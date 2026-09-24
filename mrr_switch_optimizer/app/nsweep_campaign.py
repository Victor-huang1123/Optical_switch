from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, replace
import json
import math
import os
from pathlib import Path
import pickle
import re
import time
from typing import Any, Iterable, Literal, Mapping

from ..analysis.nsweep import (
    certificate_payload,
    evaluate_fixed_fabric_parallel,
    evaluate_fixed_fabric_path_space,
    verify_worst_il_witness,
)
from ..app.fabric_reports import fixed_fabric_geometry_hash, write_fixed_fabric_reports
from ..core.fabric import FabricGraph, build_fabric_graph
from ..core.models import MRRCell
from ..core.sparams import load_mrr_s_table
from ..core.state_assignment import BenesLoopingStrategy, WaksmanStrategy
from ..core.topology import PaddedBenesTopology, RNBTopology, WaksmanTopology
from ..output.visualize import save_fixed_fabric_png
from ..routing.drc import validate_physical_routes
from ..routing.envelope import (
    DEFAULT_STAGE_PITCH_BY_OCTAVE,
    OctaveEnvelope,
    build_envelope_cells,
    octave_envelope,
)
from ..routing.fabric import FixedFabricRoutingResult, route_fixed_fabric
from ..routing.types import DRCViolation, PhysicalRoute, RoutingRules


OUTPUT_ROOT = Path("outputs/nsweep_fixed_fabric_v2")
DEFAULT_POPS_LADDER = (30000, 100000, 300000)
DEFAULT_REMEDIATION_TRIGGER_NS = (10, 11, 12)
CONFIGS: dict[str, dict[str, float]] = {
    "B_db_placeholder": {
        "prop_loss_db_per_um": 0.002,
        "crossing_loss_db_per_cross": 0.0,
        "bend_loss_db_per_bend": 0.0,
    },
    "C_db_realistic": {
        "prop_loss_db_per_um": 0.0002,
        "crossing_loss_db_per_cross": 0.1,
        "bend_loss_db_per_bend": 0.005,
    },
}
TOPOLOGIES = ("waksman", "padded_benes")
METRIC_FIELDS = (
    "topology",
    "N",
    "config",
    "status",
    "failure_class",
    "failure_signature",
    "n_physical",
    "mrr",
    "stages",
    "mrr_waksman_theory",
    "mrr_benes_padded",
    "mrr_savings_pct",
    "routed_edges",
    "failed_edges",
    "legacy_drc",
    "same_net_min_spacing",
    "perpendicular_clearance",
    "crossing_clearance",
    "bend_radius_legality",
    "acceptance_tier",
    "crossing_clearance_acceptance",
    "manufacturability_status",
    "total_bends",
    "total_crossings",
    "worst_path_crossings",
    "worst_path_length_um",
    "worst_il_db",
    "method_agreement",
    "method_gap_db",
    "exhaustive_eval_wall_s",
    "path_eval_wall_s",
    "route_wall_s",
    "witness_permutation",
    "worst_io_pair",
    "permutations_checked",
    "coverage_failures",
    "geometry_sha256",
    "envelope_id",
    "layout_mode",
    "corridor_guide_mode",
    "max_astar_pops_rung",
    "remediation",
    "window_expansion_tracks",
    "reserved_region_escalation",
    "reserved_region_escalation_stage",
    "astar_calls",
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def _case_dir(
    topology: str,
    n: int,
    config: str,
    output_root: Path = OUTPUT_ROOT,
) -> Path:
    return output_root / "cases" / topology / f"n{n:02d}" / config


def _make_topology(kind: str, n: int) -> RNBTopology:
    if kind == "waksman":
        return WaksmanTopology(n, strategy=WaksmanStrategy(), verify_rnb=False)
    if kind == "padded_benes":
        return PaddedBenesTopology(
            n,
            strategy=BenesLoopingStrategy(),
            verify_rnb=False,
        )
    raise ValueError(f"unknown topology {kind!r}")


def _campaign_rules(
    config: str,
    *,
    pops: int,
    topology: str,
    full_drc: bool = False,
    remediation_window_expansion: bool = False,
    min_crossing_clearance_um: float | None = None,
    physical_turn_guard: bool = False,
    physical_same_net_hairpin: bool = False,
    reserved_region_stage: int = 0,
) -> RoutingRules:
    coefficients = CONFIGS[config]
    return replace(
        RoutingRules(),
        max_astar_pops=pops,
        max_ripup_passes=5,
        grid_margin_tracks=20,
        cost_model="db",
        enforce_bend_spacing=True,
        legalize_port_access=True,
        drc_bend_radius_legality=True,
        drc_same_net_min_spacing=full_drc,
        drc_perpendicular_clearance=full_drc,
        corridor_guide_mode="soft" if topology == "waksman" else "off",
        remediation_window_expansion=remediation_window_expansion,
        min_crossing_clearance_um=min_crossing_clearance_um,
        physical_turn_guard=physical_turn_guard,
        physical_same_net_hairpin=physical_same_net_hairpin,
        allow_foreign_outer_runway_transit=reserved_region_stage >= 1,
        port_access_stagger_tracks=1 if reserved_region_stage >= 2 else 0,
        prop_loss_db_per_um=coefficients["prop_loss_db_per_um"],
        crossing_loss_db_per_cross=coefficients["crossing_loss_db_per_cross"],
        bend_loss_db_per_bend=coefficients["bend_loss_db_per_bend"],
    )


def _physical_routes(result: FixedFabricRoutingResult) -> tuple[PhysicalRoute, ...]:
    waveguide_by_owner = {
        waveguide.owner_edge_id: waveguide for waveguide in result.graph.waveguides
    }
    return tuple(
        PhysicalRoute(
            input_port=waveguide_by_owner[route.edge_id].input_wire,
            output_port=waveguide_by_owner[route.edge_id].output_wire,
            waypoints=route.waypoints,
            length_um=route.length_um,
            bend_count=route.bend_count,
            external_segments=route.external_segments,
            local_segments=route.local_segments,
            crossing_count=sum(
                crossing.edge_a == route.edge_id or crossing.edge_b == route.edge_id
                for crossing in result.crossings
            ),
        )
        for route in result.routes
    )


def _audit_counts(
    result: FixedFabricRoutingResult,
    cells: dict[str, MRRCell],
) -> dict[str, int]:
    audit_rules = replace(
        result.rules,
        drc_same_net_min_spacing=True,
        drc_perpendicular_clearance=True,
        drc_bend_radius_legality=True,
    )
    violations = validate_physical_routes(_physical_routes(result), cells, audit_rules)
    return _count_audit_violations(violations)


def _count_audit_violations(
    violations: Iterable[DRCViolation],
) -> dict[str, int]:
    violations = tuple(violations)
    non_core_rules = {
        "same_net_min_spacing",
        "perpendicular_clearance",
        "crossing_clearance",
        "bend_radius_legality",
    }
    return {
        "legacy_drc": sum(
            violation.rule not in non_core_rules for violation in violations
        ),
        "same_net_min_spacing": sum(
            violation.rule == "same_net_min_spacing" for violation in violations
        ),
        "perpendicular_clearance": sum(
            violation.rule == "perpendicular_clearance" for violation in violations
        ),
        "crossing_clearance": sum(
            violation.rule == "crossing_clearance" for violation in violations
        ),
        "bend_radius_legality": sum(
            violation.rule == "bend_radius_legality" for violation in violations
        ),
    }


def _acceptance_tier(
    layout_mode: Literal["astar", "template"],
    *,
    crossing_clearance_enabled: bool,
) -> tuple[str, str, str, tuple[str, ...]]:
    core_rules = ("legacy_drc", "bend_radius_legality")
    if not crossing_clearance_enabled:
        return (
            f"{layout_mode}_legacy_core",
            "disabled",
            "failed_edges=0;legacy_core_drc=0;bend_radius_legality=0",
            core_rules,
        )
    if layout_mode == "astar":
        return (
            "astar_measurement_audit",
            "measurement_only",
            "failed_edges=0;legacy_core_drc=0;bend_radius_legality=0;"
            "crossing_clearance=measurement_only;"
            "perpendicular_clearance=measurement_only",
            core_rules,
        )
    return (
        "template_hard_gate",
        "hard_gate",
        "failed_edges=0;legacy_core_drc=0;bend_radius_legality=0;"
        "crossing_clearance=0",
        (*core_rules, "crossing_clearance"),
    )


def _manufacturability_status(
    layout_mode: Literal["astar", "template"],
    audit: Mapping[str, int],
) -> str:
    if layout_mode == "astar":
        return "measurement_only"
    return (
        "all_clear"
        if not any(
            audit[name]
            for name in (
                "same_net_min_spacing",
                "perpendicular_clearance",
                "crossing_clearance",
            )
        )
        else "audit_findings"
    )


def _write_routing_artifacts(
    outdir: Path,
    topology: RNBTopology,
    graph: FabricGraph,
    cells: dict[str, MRRCell],
    result: FixedFabricRoutingResult,
    envelope: OctaveEnvelope,
    config_payload: dict[str, Any],
) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    geometry_hash = fixed_fabric_geometry_hash(result)
    summary = {
        "status": "route_failed" if result.failed_edges else "routed",
        "topology": graph.topology_name,
        "n_logical": topology.N_logical,
        "n_physical": topology.N_physical,
        "blocked_ports": graph.blocked_ports,
        "fabric_edge_count": len(graph.edges),
        "routed_edge_count": len(result.routed_edge_ids),
        "failed_edge_count": len(result.failed_edges),
        "failed_edges": [asdict(item) for item in result.failed_edges],
        "drc_violation_count": len(result.drc_violations),
        "drc_violations": [asdict(item) for item in result.drc_violations],
        "crossing_count": len(result.crossings),
        "geometry_sha256": geometry_hash,
        "envelope_id": envelope.envelope_id,
        "routing_stats": asdict(result.stats),
        "rules": asdict(result.rules),
    }
    if result.straighten_stats is not None:
        summary["straighten_stats"] = asdict(result.straighten_stats)
    _write_json(outdir / "fabric_routing_summary.json", summary)
    _write_json(outdir / "config.json", config_payload)
    with (outdir / "fabric_edges.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("edge_id", "kind", "source", "target", "source_blocked", "target_blocked"),
        )
        writer.writeheader()
        for edge in graph.edges:
            writer.writerow(
                {
                    "edge_id": edge.edge_id,
                    "kind": edge.kind,
                    "source": edge.source.endpoint_id,
                    "target": edge.target.endpoint_id,
                    "source_blocked": edge.source.blocked,
                    "target_blocked": edge.target.blocked,
                }
            )
    with (outdir / "fabric_drc_violations.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("rule", "edge_ids", "message", "x_um", "y_um"),
        )
        writer.writeheader()
        for violation in result.drc_violations:
            writer.writerow(
                {
                    "rule": violation.rule,
                    "edge_ids": ";".join(violation.edge_ids),
                    "message": violation.message,
                    "x_um": "" if violation.location is None else violation.location[0],
                    "y_um": "" if violation.location is None else violation.location[1],
                }
            )
    save_fixed_fabric_png(
        topology,
        cells,
        result,
        outdir / "fixed_fabric_layout.png",
        wire_pitch_um=envelope.wire_pitch_um,
        x_start_um=envelope.x_start_um,
        x_end_um=envelope.x_end_um,
    )
    with (outdir / "routing_result.pkl").open("wb") as handle:
        pickle.dump((cells, result), handle, protocol=pickle.HIGHEST_PROTOCOL)


def _load_valid_route(
    outdir: Path,
    expected: dict[str, Any],
) -> tuple[dict[str, MRRCell], FixedFabricRoutingResult] | None:
    config_path = outdir / "config.json"
    summary_path = outdir / "fabric_routing_summary.json"
    pickle_path = outdir / "routing_result.pkl"
    if not (config_path.is_file() and summary_path.is_file() and pickle_path.is_file()):
        return None
    stored_config = json.loads(config_path.read_text())
    if any(stored_config.get(key) != value for key, value in expected.items()):
        return None
    try:
        with pickle_path.open("rb") as handle:
            cells, result = pickle.load(handle)
    except (EOFError, pickle.PickleError, AttributeError):
        return None
    summary = json.loads(summary_path.read_text())
    if fixed_fabric_geometry_hash(result) != summary.get("geometry_sha256"):
        return None
    return cells, result


def _route_case(
    kind: str,
    n: int,
    config: str,
    topology: RNBTopology,
    graph: FabricGraph,
    envelope: OctaveEnvelope,
    s_table: dict[tuple[str, str, int], float],
    *,
    output_root: Path = OUTPUT_ROOT,
    pops_ladder: tuple[int, ...] = DEFAULT_POPS_LADDER,
    remediation_trigger_ns: tuple[int, ...] = DEFAULT_REMEDIATION_TRIGGER_NS,
    min_crossing_clearance_um: float | None = None,
    straighten_jogs: bool = False,
    physical_turn_guard: bool = False,
    v4_search: bool = False,
    campaign_version: str = "v2",
) -> tuple[
    dict[str, MRRCell],
    FixedFabricRoutingResult,
    float,
    int,
    Literal["astar", "template"],
    str,
    int,
    str,
    int,
]:
    outdir = _case_dir(kind, n, config, output_root)
    layout_mode: Literal["astar", "template"] = (
        "astar" if kind == "waksman" else "template"
    )
    (
        acceptance_tier,
        crossing_clearance_acceptance,
        campaign_acceptance,
        _required_audits,
    ) = _acceptance_tier(
        layout_mode,
        crossing_clearance_enabled=min_crossing_clearance_um is not None,
    )
    base_payload = {
        "case_key": (
            f"{campaign_version}:{kind}:n{n}:{config}:{envelope.envelope_id}"
        ),
        "topology": kind,
        "N": n,
        "config": config,
        "envelope_id": envelope.envelope_id,
        "layout_mode": layout_mode,
        "strategy": "WaksmanStrategy" if kind == "waksman" else "BenesLoopingStrategy",
        "constructive_strategy_pinned": True,
        "v4_search": v4_search,
        "acceptance_tier": acceptance_tier,
        "campaign_acceptance": campaign_acceptance,
        "crossing_clearance_acceptance": crossing_clearance_acceptance,
        "measurement_only_audits": (
            ["same_net_min_spacing", "perpendicular_clearance", "crossing_clearance"]
            if layout_mode == "astar" and min_crossing_clearance_um is not None
            else ["same_net_min_spacing", "perpendicular_clearance"]
        ),
    }
    if min_crossing_clearance_um is not None or straighten_jogs or physical_turn_guard or v4_search:
        base_payload.update(
            min_crossing_clearance_um=min_crossing_clearance_um,
            straighten_jogs=straighten_jogs,
            physical_turn_guard=physical_turn_guard,
            physical_same_net_hairpin=v4_search,
            search_time_same_net_spacing=v4_search,
            reserved_region_escalation_enabled=v4_search,
        )
    if (
        kind == "waksman"
        and n in remediation_trigger_ns
        and config == "C_db_realistic"
    ):
        base_payload["remediation_window_expansion_available"] = True
    cached = _load_valid_route(outdir, base_payload)
    if cached is not None:
        cells, result = cached
        stored = json.loads((outdir / "config.json").read_text())
        return cells, result, float(stored.get("route_wall_s", 0.0)), int(
            stored["max_astar_pops_rung"]
        ), layout_mode, str(stored.get("remediation", "none")), int(
            stored.get("window_expansion_tracks", 0)
        ), str(stored.get("reserved_region_escalation", "none")), int(
            stored.get("reserved_region_escalation_stage", 0)
        )

    direct_300k_verification = (
        kind == "waksman" and n == 10 and config == "C_db_realistic"
    )
    case_pops_ladder = (
        (pops_ladder[-1],)
        if direct_300k_verification
        else (pops_ladder if kind == "waksman" else (pops_ladder[0],))
    )
    last: tuple[
        dict[str, MRRCell],
        FixedFabricRoutingResult,
        float,
        int,
        str,
        int,
        str,
        int,
    ] | None = None

    def route_at_rung(
        pops: int,
        *,
        remediation_enabled: bool,
    ) -> tuple[
        dict[str, MRRCell], FixedFabricRoutingResult, float, int, str, int, str, int
    ]:
        cells = build_envelope_cells(topology, s_table, envelope)
        started = time.perf_counter()
        escalation_events: list[dict[str, object]] = []
        escalation_stage = 0
        result: FixedFabricRoutingResult
        remediation_events: list[tuple[int, int]]
        remediation_attempts: list[dict[str, object]]
        while True:
            rules = _campaign_rules(
                config,
                pops=pops,
                topology=kind,
                full_drc=v4_search,
                remediation_window_expansion=remediation_enabled,
                min_crossing_clearance_um=min_crossing_clearance_um,
                physical_turn_guard=physical_turn_guard,
                physical_same_net_hairpin=v4_search,
                reserved_region_stage=escalation_stage,
            )
            remediation_events = []
            remediation_attempts = []
            result = route_fixed_fabric(
                topology,
                graph,
                cells,
                rules,
                x_start=envelope.x_start_um,
                x_end=envelope.x_end_um,
                wire_pitch_um=envelope.wire_pitch_um,
                layout_mode=layout_mode,
                straighten_jogs=straighten_jogs,
                same_net_whole_net_reroute=remediation_enabled,
                remediation_events_out=remediation_events,
                remediation_attempts_out=remediation_attempts,
            )
            if (
                not v4_search
                or escalation_stage >= 2
                or not _foreign_port_access_frontier_exhausted(result)
            ):
                break
            escalation_events.append(
                {
                    "stage": escalation_stage + 1,
                    "trigger": "port_access_overlap_foreign_frontier_exhausted",
                    "action": (
                        "permit_foreign_outer_runway_transit"
                        if escalation_stage == 0
                        else "stagger_foreign_escape_runway_points_one_track"
                    ),
                }
            )
            escalation_stage += 1
        wall = time.perf_counter() - started
        remediation = _remediation_record(remediation_events, remediation_attempts)
        window_expansion_tracks = max(
            (
                int(attempt["window_expansion_tracks"])
                for attempt in remediation_attempts
            ),
            default=0,
        )
        escalation_record = (
            "none"
            if not escalation_events
            else ";".join(str(event["action"]) for event in escalation_events)
        )
        payload = {
            **base_payload,
            "route_wall_s": wall,
            "max_astar_pops_rung": pops,
            "corridor_guide_mode": rules.corridor_guide_mode,
            "same_net_whole_net_reroute_enabled": remediation_enabled,
            "remediation": remediation,
            "window_expansion_tracks": window_expansion_tracks,
            "remediation_attempts": remediation_attempts,
            "reserved_region_escalation": escalation_record,
            "reserved_region_escalation_stage": escalation_stage,
            "reserved_region_escalation_events": escalation_events,
            "rules": asdict(rules),
        }
        _write_json(outdir / "remediation_attempts.json", remediation_attempts)
        _write_routing_artifacts(
            outdir,
            topology,
            graph,
            cells,
            result,
            envelope,
            payload,
        )
        return (
            cells,
            result,
            wall,
            pops,
            remediation,
            window_expansion_tracks,
            escalation_record,
            escalation_stage,
        )

    for pops in case_pops_ladder:
        last = route_at_rung(pops, remediation_enabled=False)
        (
            _cells,
            result,
            _wall,
            _pops,
            _remediation,
            _window_tracks,
            _escalation,
            _escalation_stage,
        ) = last
        if not result.failed_edges:
            break

    assert last is not None
    (
        cells,
        result,
        wall,
        pops,
        remediation,
        window_expansion_tracks,
        escalation,
        escalation_stage,
    ) = last
    if (
        result.failed_edges
        and kind == "waksman"
        and n in remediation_trigger_ns
        and config == "C_db_realistic"
        and pops == pops_ladder[-1]
        and _same_net_dominated_low_pop_failure(result)
    ):
        (
            cells,
            result,
            wall,
            pops,
            remediation,
            window_expansion_tracks,
            escalation,
            escalation_stage,
        ) = route_at_rung(pops_ladder[-1], remediation_enabled=True)
    return (
        cells,
        result,
        wall,
        pops,
        layout_mode,
        remediation,
        window_expansion_tracks,
        escalation,
        escalation_stage,
    )


def _remediation_record(
    events: list[tuple[int, int]],
    attempts: list[dict[str, object]],
) -> str:
    if not events:
        if not attempts:
            return "none"
        rungs = ",".join(
            f"{int(attempt['displacement_tracks']):+d}"
            for attempt in attempts
        )
        return f"same_net_whole_net_riser_displacement:attempted_exhausted:{rungs}"
    unique = sorted(set(events), key=lambda item: (item[0], abs(item[1]), -item[1]))
    rungs = ";".join(
        f"I{input_port}={displacement:+d}track"
        for input_port, displacement in unique
    )
    return f"same_net_whole_net_riser_displacement:{rungs}"


_COUNTER_RE = re.compile(
    r"same_net:(?P<same_net>\d+).*?crossing_candidate:(?P<crossing_candidate>\d+)"
    r".*?crossing_budget:(?P<crossing_budget>\d+)"
    r".*?hard_reserved_crossing:(?P<hard_reserved_crossing>\d+)"
    r".*?soft_repeated_crossing:(?P<soft_repeated_crossing>\d+)"
    r".*?soft_reserved_crossing:(?P<soft_reserved_crossing>\d+)"
    r".*?reserved_spacing:(?P<reserved_spacing>\d+)"
    r".*?segment:(?P<segment>\d+),pops:(?P<pops>\d+)"
)

_POPS_RE = re.compile(r"\bpops:(?P<pops>\d+)\b")


def _same_net_dominated_low_pop_failure(result: FixedFabricRoutingResult) -> bool:
    for failure in result.failed_edges:
        match = _COUNTER_RE.search(failure.message)
        if match is None or "same_net_touch" not in failure.message:
            continue
        counts = {
            key: int(value)
            for key, value in match.groupdict().items()
        }
        same_net = counts.pop("same_net")
        pops = counts.pop("pops")
        if same_net > 0 and same_net >= max(counts.values(), default=0) and pops <= 1000:
            return True
    return False


def _foreign_port_access_frontier_exhausted(
    result: FixedFabricRoutingResult,
) -> bool:
    """Return whether a walled frontier is dominated by foreign port access.

    This intentionally does not trigger on an A* budget exhaustion: the reserved-region
    relaxation is a response to a geometric wall, not a generic retry policy.
    """
    for failure in result.failed_edges:
        message = failure.message
        if (
            "blocked by port access (port_access_" not in message
            or "_foreign)" not in message
        ):
            continue
        match = _POPS_RE.search(message)
        if match is None:
            continue
        if int(match.group("pops")) < result.rules.max_astar_pops:
            return True
    return False


def _v3_n8_foreign_port_access_failure(
    kind: str,
    n: int,
    config: str,
    result: FixedFabricRoutingResult,
    envelope: OctaveEnvelope,
) -> tuple[str, str] | None:
    """Classify the ruled Phase-5 geometry regression without broadening fallback."""
    if (
        kind != "waksman"
        or n != 8
        or config != "C_db_realistic"
        or "dy5p5-w450" not in envelope.envelope_id
    ):
        return None
    messages = tuple(failure.message for failure in result.failed_edges)
    if not messages or not all(
        "cannot route I6->O6" in message
        and "waksman_8x8_s2_w2_6.drop->O6" in message
        and "port_access_overlap_foreign" in message
        and "region=I3/waksman_8x8_s2_w3_7.in" in message
        and "pops:10220" in message
        for message in messages
    ):
        return None
    return (
        "port_access_overlap_foreign",
        "I6->O6:final_hop=waksman_8x8_s2_w2_6.drop->O6:"
        "foreign_region=I3/waksman_8x8_s2_w3_7.in:pops=10220",
    )


def _mrr_theory(n: int) -> tuple[int, int, float]:
    levels = math.ceil(math.log2(n))
    padded = 1 << levels
    waksman = n * levels - padded + 1
    benes = (padded // 2) * (2 * levels - 1)
    return waksman, benes, 100.0 * (benes - waksman) / benes


def _evaluate_case(
    kind: str,
    n: int,
    config: str,
    topology: RNBTopology,
    graph: FabricGraph,
    cells: dict[str, MRRCell],
    result: FixedFabricRoutingResult,
    envelope: OctaveEnvelope,
    route_wall_s: float,
    pops: int,
    layout_mode: Literal["astar", "template"],
    remediation: str,
    window_expansion_tracks: int,
    reserved_region_escalation: str,
    reserved_region_escalation_stage: int,
    *,
    workers: int,
    path_first: bool,
    output_root: Path = OUTPUT_ROOT,
    remediation_trigger_ns: tuple[int, ...] = DEFAULT_REMEDIATION_TRIGGER_NS,
) -> dict[str, Any]:
    outdir = _case_dir(kind, n, config, output_root)
    audit = _audit_counts(result, cells)
    (
        acceptance_tier,
        crossing_clearance_acceptance,
        _campaign_acceptance,
        required_audits,
    ) = _acceptance_tier(
        layout_mode,
        crossing_clearance_enabled=result.rules.min_crossing_clearance_um is not None,
    )
    if layout_mode == "template" and audit["crossing_clearance"]:
        raise RuntimeError(
            f"TEMPLATE CROSSING-CLEARANCE FAILURE {kind} N={n} "
            f"config={config}: {audit!r}"
        )
    if any(audit[name] for name in required_audits):
        raise RuntimeError(
            f"DRC FAILURE {kind} N={n} config={config}: {audit!r}"
        )

    w_theory, b_theory, savings = _mrr_theory(n)
    if result.failed_edges:
        failure = _v3_n8_foreign_port_access_failure(
            kind, n, config, result, envelope
        )
        authorized_legacy_fallback = (
            kind == "waksman"
            and n in remediation_trigger_ns
            and config == "C_db_realistic"
            and remediation.startswith("same_net_whole_net_riser_displacement:")
            and _same_net_dominated_low_pop_failure(result)
        )
        if failure is None and not authorized_legacy_fallback:
            raise RuntimeError(
                f"ROUTING FAILURE {kind} N={n} config={config}: "
                f"{result.failed_edges!r}"
            )
        if failure is None:
            failure = (
                "same_net_whole_net_riser_displacement_exhausted",
                remediation,
            )
        failure_class, failure_signature = failure
        summary_path = outdir / "fabric_routing_summary.json"
        summary = json.loads(summary_path.read_text())
        summary.update(
            status="route_failed",
            failure_class=failure_class,
            failure_signature=failure_signature,
            envelope_id=envelope.envelope_id,
            layout_mode=layout_mode,
            max_astar_pops_rung=pops,
            remediation=remediation,
            window_expansion_tracks=window_expansion_tracks,
            reserved_region_escalation=reserved_region_escalation,
            reserved_region_escalation_stage=reserved_region_escalation_stage,
        )
        _write_json(summary_path, summary)
        config_path = outdir / "config.json"
        config_payload = json.loads(config_path.read_text())
        config_payload.update(
            status="route_failed",
            failure_class=failure_class,
            failure_signature=failure_signature,
        )
        _write_json(config_path, config_payload)
        if failure_class == "port_access_overlap_foreign":
            _write_json(
                outdir / "failure_diagnosis.json",
                {
                    "status": "route_failed",
                    "failure_class": failure_class,
                    "failure_signature": failure_signature,
                    "blocked_net": "I6->O6",
                    "blocked_hop": "waksman_8x8_s2_w2_6.drop->O6",
                    "foreign_port_access_owner": "I3",
                    "foreign_port_access_region": "waksman_8x8_s2_w3_7.in",
                    "frontier_pops": 10220,
                    "budget_limited": False,
                    "v2_status": "complete",
                    "v2_worst_il_db": 3.7984212059855453,
                    "diagnosis": (
                        "v3 geometry regression: routable with legacy v2 port geometry; "
                        "final I6->O6 hop is blocked by the wider v3 foreign I3 "
                        "port-access region"
                    ),
                },
            )
        row: dict[str, Any] = {
            "topology": kind,
            "N": n,
            "config": config,
            "status": "route_failed",
            "failure_class": failure_class,
            "failure_signature": failure_signature,
            "n_physical": topology.N_physical,
            "mrr": topology.n_MRR,
            "stages": topology.n_stages,
            "mrr_waksman_theory": w_theory,
            "mrr_benes_padded": b_theory,
            "mrr_savings_pct": savings,
            "routed_edges": len(result.routed_edge_ids),
            "failed_edges": len(result.failed_edges),
            **audit,
            "acceptance_tier": acceptance_tier,
            "crossing_clearance_acceptance": crossing_clearance_acceptance,
            "manufacturability_status": _manufacturability_status(
                layout_mode, audit
            ),
            "total_bends": sum(route.bend_count for route in result.routes),
            "total_crossings": len(result.crossings),
            "worst_path_crossings": None,
            "worst_path_length_um": None,
            "worst_il_db": None,
            "method_agreement": None,
            "method_gap_db": None,
            "exhaustive_eval_wall_s": None,
            "path_eval_wall_s": None,
            "route_wall_s": route_wall_s,
            "witness_permutation": None,
            "worst_io_pair": None,
            "permutations_checked": None,
            "coverage_failures": None,
            "geometry_sha256": fixed_fabric_geometry_hash(result),
            "envelope_id": envelope.envelope_id,
            "layout_mode": layout_mode,
            "corridor_guide_mode": result.rules.corridor_guide_mode,
            "max_astar_pops_rung": pops,
            "remediation": remediation,
            "window_expansion_tracks": window_expansion_tracks,
            "reserved_region_escalation": reserved_region_escalation,
            "reserved_region_escalation_stage": reserved_region_escalation_stage,
            "astar_calls": result.stats.astar_calls,
        }
        _write_json(outdir / "case_metrics.json", row)
        return row

    evaluation_result = result
    if layout_mode == "astar":
        evaluation_result = replace(
            result,
            drc_violations=tuple(
                violation
                for violation in result.drc_violations
                if violation.rule
                not in {
                    "same_net_min_spacing",
                    "perpendicular_clearance",
                    "crossing_clearance",
                }
            ),
        )
    if path_first:
        path_result = evaluate_fixed_fabric_path_space(
            topology, cells, evaluation_result
        )
        exhaustive = evaluate_fixed_fabric_parallel(
            topology,
            cells,
            evaluation_result,
            workers=workers,
        )
    else:
        exhaustive = evaluate_fixed_fabric_parallel(
            topology,
            cells,
            evaluation_result,
            workers=workers,
        )
        path_result = evaluate_fixed_fabric_path_space(
            topology, cells, evaluation_result
        )
    if not exhaustive.coverage_report.passed:
        raise RuntimeError(
            f"COVERAGE FAILURE {kind} N={n} config={config}: "
            f"{exhaustive.coverage_report!r}"
        )
    exact_il = exhaustive.loss_report.worst_insertion_loss_db
    path_il = path_result.worst_insertion_loss_db
    if exact_il != path_il:
        raise RuntimeError(
            f"METHOD DISAGREEMENT {kind} N={n} config={config}: "
            f"exhaustive={exact_il!r}, path={path_il!r}, "
            f"gap={exact_il - path_il!r}"
        )

    verify_worst_il_witness(
        topology,
        cells,
        evaluation_result,
        witness_permutation=path_result.witness_permutation,
        input_port=path_result.worst_path.input_port,
        expected_il_db=path_result.worst_insertion_loss_db,
    )
    _write_json(outdir / "worst_il_certificate.json", certificate_payload(path_result))
    write_fixed_fabric_reports(
        outdir,
        graph,
        result,
        exhaustive.coverage_report,
        {
            "physical_model": "v2 octave-envelope fixed fabric",
            "envelope_id": envelope.envelope_id,
        },
        exhaustive.loss_report,
    )
    summary_path = outdir / "fabric_routing_summary.json"
    summary = json.loads(summary_path.read_text())
    summary.update(
        status="complete",
        envelope_id=envelope.envelope_id,
        layout_mode=layout_mode,
        max_astar_pops_rung=pops,
        remediation=remediation,
        window_expansion_tracks=window_expansion_tracks,
        reserved_region_escalation=reserved_region_escalation,
        reserved_region_escalation_stage=reserved_region_escalation_stage,
    )
    _write_json(summary_path, summary)

    row: dict[str, Any] = {
        "topology": kind,
        "N": n,
        "config": config,
        "status": "complete",
        "failure_class": None,
        "failure_signature": None,
        "n_physical": topology.N_physical,
        "mrr": topology.n_MRR,
        "stages": topology.n_stages,
        "mrr_waksman_theory": w_theory,
        "mrr_benes_padded": b_theory,
        "mrr_savings_pct": savings,
        "routed_edges": len(result.routed_edge_ids),
        "failed_edges": len(result.failed_edges),
        **audit,
        "acceptance_tier": acceptance_tier,
        "crossing_clearance_acceptance": crossing_clearance_acceptance,
        "manufacturability_status": _manufacturability_status(layout_mode, audit),
        "total_bends": sum(route.bend_count for route in result.routes),
        "total_crossings": len(result.crossings),
        "worst_path_crossings": exhaustive.loss_report.worst_crossing_count,
        "worst_path_length_um": exhaustive.loss_report.worst_path_length_um,
        "worst_il_db": exact_il,
        "method_agreement": True,
        "method_gap_db": exact_il - path_il,
        "exhaustive_eval_wall_s": exhaustive.wall_clock_s,
        "path_eval_wall_s": path_result.wall_clock_s,
        "route_wall_s": route_wall_s,
        "witness_permutation": "-".join(map(str, path_result.witness_permutation)),
        "worst_io_pair": (
            f"{path_result.worst_path.input_port}->{path_result.worst_path.output_port}"
        ),
        "permutations_checked": exhaustive.coverage_report.permutations_checked,
        "coverage_failures": 0,
        "geometry_sha256": fixed_fabric_geometry_hash(result),
        "envelope_id": envelope.envelope_id,
        "layout_mode": layout_mode,
        "corridor_guide_mode": result.rules.corridor_guide_mode,
        "max_astar_pops_rung": pops,
        "remediation": remediation,
        "window_expansion_tracks": window_expansion_tracks,
        "reserved_region_escalation": reserved_region_escalation,
        "reserved_region_escalation_stage": reserved_region_escalation_stage,
        "astar_calls": result.stats.astar_calls,
    }
    _write_json(outdir / "case_metrics.json", row)
    return row


def _write_metrics(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=METRIC_FIELDS)
        writer.writeheader()
        writer.writerows(
            {
                field: "" if row.get(field) is None else row.get(field, "")
                for field in METRIC_FIELDS
            }
            for row in sorted(rows, key=lambda item: (int(item["N"]), item["config"], item["topology"]))
        )


def _write_method_agreement(
    rows: list[dict[str, Any]],
    output_root: Path = OUTPUT_ROOT,
) -> None:
    path = output_root / "crosscheck" / "method_agreement.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ("topology", "N", "config", "worst_il_exhaustive_db", "worst_il_path_db", "equal", "gap_db")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in sorted(rows, key=lambda item: (int(item["N"]), item["config"], item["topology"])):
            if row.get("status") != "complete":
                continue
            writer.writerow(
                {
                    "topology": row["topology"],
                    "N": row["N"],
                    "config": row["config"],
                    "worst_il_exhaustive_db": row["worst_il_db"],
                    "worst_il_path_db": float(row["worst_il_db"]) - float(row["method_gap_db"]),
                    "equal": row["method_agreement"],
                    "gap_db": row["method_gap_db"],
                }
            )


def _existing_rows(output_root: Path = OUTPUT_ROOT) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in output_root.glob("cases/*/n*/**/case_metrics.json"):
        row = json.loads(path.read_text())
        if row.get("status") in {"complete", "route_failed"}:
            row.setdefault("remediation", "none")
            row.setdefault("window_expansion_tracks", 0)
            row.setdefault("reserved_region_escalation", "none")
            row.setdefault("reserved_region_escalation_stage", 0)
            rows.append(row)
    return rows


def _selected(filters: dict[str, str], kind: str, n: int, config: str) -> bool:
    return (
        ("topo" not in filters or filters["topo"] == kind)
        and ("n" not in filters or int(filters["n"]) == n)
        and ("config" not in filters or filters["config"] == config)
    )


def _assert_envelope_fairness(
    rows: Iterable[dict[str, Any]],
    stage_pitch_by_octave: Mapping[int, float] = DEFAULT_STAGE_PITCH_BY_OCTAVE,
) -> None:
    by_octave: dict[int, set[str]] = {}
    for row in rows:
        octave = octave_envelope(
            int(row["N"]),
            stage_pitch_by_octave=stage_pitch_by_octave,
        ).n_canvas
        by_octave.setdefault(octave, set()).add(str(row["envelope_id"]))
    failures = {octave: ids for octave, ids in by_octave.items() if len(ids) != 1}
    if failures:
        raise RuntimeError(f"G5 envelope fairness failure: {failures!r}")


def _benes8_mode_crosscheck(
    s_table: dict[tuple[str, str, int], float],
    *,
    output_root: Path = OUTPUT_ROOT,
    pops_ladder: tuple[int, ...] = DEFAULT_POPS_LADDER,
    stage_pitch_by_octave: Mapping[int, float] = DEFAULT_STAGE_PITCH_BY_OCTAVE,
) -> None:
    root = output_root / "crosscheck" / "benes8_astar_vs_template"
    rows: list[dict[str, Any]] = []
    for config in CONFIGS:
        topology = _make_topology("padded_benes", 8)
        graph = build_fabric_graph(topology)
        envelope = octave_envelope(
            8,
            stage_pitch_by_octave=stage_pitch_by_octave,
        )
        cells = build_envelope_cells(topology, s_table, envelope)
        template_rules = _campaign_rules(
            config,
            pops=pops_ladder[0],
            topology="padded_benes",
        )
        template = route_fixed_fabric(
            topology,
            graph,
            cells,
            template_rules,
            x_start=envelope.x_start_um,
            x_end=envelope.x_end_um,
            wire_pitch_um=envelope.wire_pitch_um,
            layout_mode="template",
        )
        astar: FixedFabricRoutingResult | None = None
        astar_pops = pops_ladder[0]
        astar_wall = 0.0
        for astar_pops in pops_ladder:
            started = time.perf_counter()
            astar = route_fixed_fabric(
                topology,
                graph,
                cells,
                _campaign_rules(config, pops=astar_pops, topology="padded_benes"),
                x_start=envelope.x_start_um,
                x_end=envelope.x_end_um,
                wire_pitch_um=envelope.wire_pitch_um,
                layout_mode="astar",
            )
            astar_wall += time.perf_counter() - started
            if not astar.failed_edges:
                break
        assert astar is not None
        template_score = (
            len(template.failed_edges),
            len(template.drc_violations),
            len(template.crossings),
        )
        astar_score = (
            len(astar.failed_edges),
            len(astar.drc_violations),
            len(astar.crossings),
        )
        dominates = all(left <= right for left, right in zip(template_score, astar_score))
        row = {
            "config": config,
            "template_failed_edges": template_score[0],
            "astar_failed_edges": astar_score[0],
            "template_drc": template_score[1],
            "astar_drc": astar_score[1],
            "template_crossings": template_score[2],
            "astar_crossings": astar_score[2],
            "template_dominates_or_ties": dominates,
            "astar_max_pops_rung": astar_pops,
            "astar_wall_s": astar_wall,
        }
        rows.append(row)
        case_dir = root / config
        case_dir.mkdir(parents=True, exist_ok=True)
        save_fixed_fabric_png(
            topology,
            cells,
            template,
            case_dir / "template.png",
            wire_pitch_um=envelope.wire_pitch_um,
            x_start_um=envelope.x_start_um,
            x_end_um=envelope.x_end_um,
        )
        save_fixed_fabric_png(
            topology,
            cells,
            astar,
            case_dir / "astar.png",
            wire_pitch_um=envelope.wire_pitch_um,
            x_start_um=envelope.x_start_um,
            x_end_um=envelope.x_end_um,
        )
        _write_json(case_dir / "comparison.json", row)
        if not dominates:
            raise RuntimeError(
                f"BENES8 MODE CROSSCHECK FAILURE config={config}: "
                f"template={template_score!r}, astar={astar_score!r}"
            )
    path = root / "comparison.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _phase0_decisions(output_root: Path = OUTPUT_ROOT) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    text = """# Phase 0 design decisions

1. Witness pruning and complexity: a strategy-recursion forced-state contradiction prunes impossible Beneš paths at the root; randomized witnesses prove realizability quickly; unresolved candidates use a complete lexicographic `(N-1)!` permutation-completion backtrack. Candidate verdicts are stable and the structural recursion result is reused across all paths. The worst-case bound remains O((N-1)! * strategy(N)) per unresolved path; no probabilistic miss is accepted as proof.
2. Parallel IPC: Linux `fork` shares the immutable topology, cells, route geometry, and loss maps copy-on-write. Work is exactly N(N-1) chunks fixed by the first two permutation positions; workers return only local maxima and failure lists. The worker cap is 64.
3. G6 hashing: `fixed_fabric_geometry_hash` covers route owners, covered edge IDs, and waypoints only. Blocked-port flags are deliberately excluded, so padding metadata cannot change a geometry identity.
4. Doglegs and G1: every emitted segment is fed unchanged to crossing extraction. A legalization dogleg would therefore change the count and fail G1; no crossing delta is suppressed.
5. N=6 strategy pinning: every campaign topology explicitly receives `WaksmanStrategy` or `BenesLoopingStrategy`. Padded-Beneš n=6 is intentionally not compared to older LUT-generated artifacts; the report labels that difference. Waksman n=6 retains its separate old-140-pitch regression check.
6. Guide integrity: `corridor_guide_mode` is a recorded no-op. The flag is consumed nowhere; `_corridor_preferred_x` in `routing/physical.py` is always active. G7 therefore asserts identical geometry for `off` and `soft` rather than claiming a gated guide layer.
7. N=10/C remediation history: iteration 1 corrected endpoint-axis displacement, iteration 2 targeted the predecessor hop and widened the fallback beam, and iteration 3 added a default-off remediation-only search-window expansion of `abs(displacement)+2` tracks. The final exact 300k run still failed with the same I4→O4 signature and is retained as `route_failed` without a WIL certificate. The per-hop search window is an expandable search policy recorded per case; the octave envelope/canvas, stage pitch, wire pitch, and global routing-grid bounds remain constitutionally pinned.
8. Rung-state audit: no route state leaked between pop rungs. The apparent successful run used `full_drc=True`, which coupled measurement-only same-net/perpendicular audit findings into pass scoring and selected different geometry. It is not a campaign-flags success and is excluded from WIL results.

The exactness lemma used by the path method is: every active permutation path is realizable by definition, and every realizable path has a witness permutation by definition; therefore the two maxima are equal.
"""
    (output_root / "PHASE0_DESIGN_DECISIONS.md").write_text(text)


def run_campaign(
    filters: dict[str, str],
    workers: int,
    *,
    output_root: Path = OUTPUT_ROOT,
    pops_ladder: tuple[int, ...] = DEFAULT_POPS_LADDER,
    remediation_trigger_ns: tuple[int, ...] = DEFAULT_REMEDIATION_TRIGGER_NS,
    stage_pitch_by_octave: Mapping[int, float] = DEFAULT_STAGE_PITCH_BY_OCTAVE,
    min_crossing_clearance_um: float | None = None,
    straighten_jogs: bool = False,
    physical_turn_guard: bool = False,
    v4_search: bool = False,
    campaign_version: str = "v2",
) -> list[dict[str, Any]]:
    output_root = Path(output_root)
    if not pops_ladder or any(pops <= 0 for pops in pops_ladder):
        raise ValueError("pops_ladder must contain positive values")
    if tuple(sorted(set(pops_ladder))) != pops_ladder:
        raise ValueError("pops_ladder must be strictly increasing")
    if any(n < 3 for n in remediation_trigger_ns):
        raise ValueError("remediation_trigger_ns values must be at least 3")
    if min_crossing_clearance_um is not None and min_crossing_clearance_um < 0.0:
        raise ValueError("min_crossing_clearance_um must be non-negative")
    if not campaign_version:
        raise ValueError("campaign_version must be non-empty")
    if v4_search:
        if min_crossing_clearance_um is None:
            min_crossing_clearance_um = 10.0
        physical_turn_guard = True
    if not {4, 8, 16} <= set(stage_pitch_by_octave):
        raise ValueError("stage_pitch_by_octave must define canvases 4, 8, and 16")
    try:
        os.nice(10)
    except OSError:
        pass
    _phase0_decisions(output_root)
    started = time.time()
    s_table = load_mrr_s_table("mrr_sparam_library", strict=True)
    rows = _existing_rows(output_root)
    completed_keys = {
        (row["topology"], int(row["N"]), row["config"])
        for row in rows
    }
    for phase, sizes in ((1, range(3, 9)), (2, range(9, 11)), (3, range(11, 13))):
        for n in sizes:
            envelope = octave_envelope(
                n,
                stage_pitch_by_octave=stage_pitch_by_octave,
            )
            for kind in TOPOLOGIES:
                for config in CONFIGS:
                    if not _selected(filters, kind, n, config):
                        continue
                    key = kind, n, config
                    if key in completed_keys:
                        continue
                    topology = _make_topology(kind, n)
                    w_theory, b_theory, _savings = _mrr_theory(n)
                    expected_mrr = w_theory if kind == "waksman" else b_theory
                    if topology.n_MRR != expected_mrr:
                        raise RuntimeError(
                            f"MRR THEORY FAILURE {kind} N={n}: "
                            f"actual={topology.n_MRR}, expected={expected_mrr}"
                        )
                    graph = build_fabric_graph(topology)
                    (
                        cells,
                        result,
                        route_wall,
                        pops,
                        layout_mode,
                        remediation,
                        window_expansion_tracks,
                        reserved_region_escalation,
                        reserved_region_escalation_stage,
                    ) = _route_case(
                        kind,
                        n,
                        config,
                        topology,
                        graph,
                        envelope,
                        s_table,
                        output_root=output_root,
                        pops_ladder=pops_ladder,
                        remediation_trigger_ns=remediation_trigger_ns,
                        min_crossing_clearance_um=min_crossing_clearance_um,
                        straighten_jogs=straighten_jogs,
                        physical_turn_guard=physical_turn_guard,
                        v4_search=v4_search,
                        campaign_version=campaign_version,
                    )
                    row = _evaluate_case(
                        kind,
                        n,
                        config,
                        topology,
                        graph,
                        cells,
                        result,
                        envelope,
                        route_wall,
                        pops,
                        layout_mode,
                        remediation,
                        window_expansion_tracks,
                        reserved_region_escalation,
                        reserved_region_escalation_stage,
                        workers=min(workers, 8 if n <= 8 else workers),
                        path_first=phase == 3,
                        output_root=output_root,
                        remediation_trigger_ns=remediation_trigger_ns,
                    )
                    rows.append(row)
                    completed_keys.add(key)
                    _write_metrics(rows, output_root / "metrics.partial.csv")
                    _write_method_agreement(rows, output_root)
                    _assert_envelope_fairness(rows, stage_pitch_by_octave)
        if phase == 1:
            if not filters:
                _benes8_mode_crosscheck(
                    s_table,
                    output_root=output_root,
                    pops_ladder=pops_ladder,
                    stage_pitch_by_octave=stage_pitch_by_octave,
                )
            _write_report(
                rows,
                interim=True,
                elapsed_s=time.time() - started,
                output_root=output_root,
            )
    _write_metrics(rows, output_root / "metrics.csv")
    _write_method_agreement(rows, output_root)
    _assert_envelope_fairness(rows, stage_pitch_by_octave)
    if not filters and len(rows) == 40:
        _make_charts(rows, output_root)
        _write_report(
            rows,
            interim=False,
            elapsed_s=time.time() - started,
            output_root=output_root,
        )
    return rows


def _rows_by_case(rows: list[dict[str, Any]]) -> dict[tuple[int, str, str], dict[str, Any]]:
    return {
        (int(row["N"]), str(row["config"]), str(row["topology"])): row
        for row in rows
    }


def _make_charts(
    rows: list[dict[str, Any]],
    output_root: Path = OUTPUT_ROOT,
) -> None:
    import matplotlib.pyplot as plt

    charts = output_root / "charts"
    charts.mkdir(parents=True, exist_ok=True)
    indexed = _rows_by_case(rows)
    ns = list(range(3, 13))
    colors = {"waksman": "#2474B5", "padded_benes": "#E07A2D"}
    labels = {"waksman": "Waksman", "padded_benes": "Padded Beneš"}

    def numeric(n: int, config: str, kind: str, field: str) -> float:
        value = indexed[(n, config, kind)].get(field)
        return math.nan if value in (None, "") else float(value)

    def boundaries(ax: Any) -> None:
        ax.axvline(4.5, color="#777777", ls="--", lw=0.9)
        ax.axvline(8.5, color="#777777", ls="--", lw=0.9)

    for config, suffix in (("B_db_placeholder", "B"), ("C_db_realistic", "C")):
        fig, ax = plt.subplots(figsize=(7.2, 4.2))
        values: dict[str, list[float]] = {}
        for kind in TOPOLOGIES:
            values[kind] = [numeric(n, config, kind, "worst_il_db") for n in ns]
            ax.plot(ns, values[kind], marker="o", lw=1.8, label=labels[kind], color=colors[kind])
        failed_ns = [
            n
            for n in ns
            if indexed[(n, config, "waksman")].get("status") == "route_failed"
        ]
        for n, w, b in zip(ns, values["waksman"], values["padded_benes"]):
            if math.isfinite(w) and math.isfinite(b):
                ax.annotate(f"Δ {w-b:+.2f}", (n, max(w, b)), xytext=(0, 6), textcoords="offset points", ha="center", fontsize=6.5)
            elif indexed[(n, config, "waksman")].get("status") == "route_failed":
                ax.plot(n, 0.04, marker="x", color="#B22222", ms=7, transform=ax.get_xaxis_transform())
                ax.annotate(
                    f"N={n}: route_failed\n(no certified WIL)",
                    (n, 0.05),
                    xycoords=ax.get_xaxis_transform(),
                    xytext=(0, 8 + 26 * failed_ns.index(n)),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=6.5,
                    color="#B22222",
                )
        ax.set_ylim(bottom=0)
        boundaries(ax)
        ax.set_xlabel("Logical port count N")
        ax.set_ylabel("Worst insertion loss (dB)")
        ax.set_title(f"WIL parity on pinned octave envelopes — config {suffix}")
        ax.legend()
        ax.grid(alpha=0.25)
        fig.tight_layout()
        fig.savefig(charts / f"worst_il_vs_n_{suffix}.png", dpi=220)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(8.0, 4.2))
        width = 0.38
        ax.bar([n - width / 2 for n in ns], values["waksman"], width, label="Waksman", color=colors["waksman"])
        ax.bar([n + width / 2 for n in ns], values["padded_benes"], width, label="Padded Beneš", color=colors["padded_benes"])
        boundaries(ax)
        ax.set_xlabel("Logical port count N")
        ax.set_ylabel("Worst insertion loss (dB)")
        ax.set_title(f"Worst insertion loss by N — config {suffix}")
        ax.legend()
        ax.grid(axis="y", alpha=0.25)
        fig.tight_layout()
        fig.savefig(charts / f"worst_il_bars_{suffix}.png", dpi=220)
        plt.close(fig)

    w_counts = [_mrr_theory(n)[0] for n in ns]
    b_counts = [_mrr_theory(n)[1] for n in ns]
    savings = [_mrr_theory(n)[2] for n in ns]
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    ax.plot(ns, w_counts, marker="o", lw=2, label="Waksman W(N)", color=colors["waksman"])
    ax.step(ns, b_counts, where="mid", marker="s", lw=2, label="Padded Beneš", color=colors["padded_benes"])
    for n, w, b, saving in zip(ns, w_counts, b_counts, savings):
        rounded_saving = math.floor(saving + 0.5)
        ax.annotate(f"−{rounded_saving}%", (n, w), xytext=(0, -13), textcoords="offset points", ha="center", fontsize=7)
    boundaries(ax)
    ax.set_xlabel("Logical port count N")
    ax.set_ylabel("MRR count")
    ax.set_title("Waksman scaling vs padded-Beneš octave stair-step")
    ax.legend()
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(charts / "mrr_count_vs_n.png", dpi=240)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 3.8))
    ax.plot(ns, savings, marker="o", lw=2, color="#3A9D5D")
    boundaries(ax)
    ax.set_xlabel("Logical port count N")
    ax.set_ylabel("MRR savings vs padded Beneš (%)")
    ax.set_title("N×N scalability: MRR savings peak after octave boundaries")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(charts / "mrr_savings_pct_vs_n.png", dpi=240)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    for config, style in (("B_db_placeholder", "-"), ("C_db_realistic", "--")):
        for kind in TOPOLOGIES:
            ax.plot(
                ns,
                [numeric(n, config, kind, "worst_path_crossings") for n in ns],
                marker="o",
                ls=style,
                color=colors[kind],
                label=f"{labels[kind]} {config[0]}",
            )
    boundaries(ax)
    ax.set_xlabel("Logical port count N")
    ax.set_ylabel("Worst-path crossings")
    ax.set_title("Worst-path physical crossings")
    ax.legend(ncol=2, fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(charts / "worst_path_crossings_vs_n.png", dpi=220)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7.2, 4.0))
    for config, suffix in (("B_db_placeholder", "B"), ("C_db_realistic", "C")):
        margins = [
            numeric(n, config, "waksman", "worst_il_db")
            - numeric(n, config, "padded_benes", "worst_il_db")
            for n in ns
        ]
        ax.plot(ns, margins, marker="o", lw=1.8, label=f"Config {suffix}")
    ax.axhline(0, color="black", lw=0.8)
    boundaries(ax)
    ax.set_xlabel("Logical port count N")
    ax.set_ylabel("Waksman − Beneš WIL (dB); positive = Beneš wins")
    ax.set_title("Physical WIL win margin on matched octave envelopes")
    ax.legend()
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(charts / "win_margin_vs_n.png", dpi=220)
    plt.close(fig)


def _win_summary(
    rows: list[dict[str, Any]],
    config: str,
) -> tuple[list[int], list[int], list[int], list[int]]:
    indexed = _rows_by_case(rows)
    wins: list[int] = []
    ties: list[int] = []
    losses: list[int] = []
    unavailable: list[int] = []
    for n in range(3, 13):
        key_w = (n, config, "waksman")
        key_b = (n, config, "padded_benes")
        if key_w not in indexed or key_b not in indexed:
            continue
        if (
            indexed[key_w].get("worst_il_db") in (None, "")
            or indexed[key_b].get("worst_il_db") in (None, "")
        ):
            unavailable.append(n)
            continue
        delta = float(indexed[key_w]["worst_il_db"]) - float(indexed[key_b]["worst_il_db"])
        if delta < -1e-12:
            wins.append(n)
        elif delta > 1e-12:
            losses.append(n)
        else:
            ties.append(n)
    return wins, ties, losses, unavailable


def _write_report(
    rows: list[dict[str, Any]],
    *,
    interim: bool,
    elapsed_s: float,
    output_root: Path = OUTPUT_ROOT,
) -> None:
    selected = [row for row in rows if int(row["N"]) <= 8] if interim else rows
    certified = [row for row in selected if row.get("status") == "complete"]
    route_failed = [row for row in selected if row.get("status") == "route_failed"]

    def display(value: object, *, digits: int | None = None) -> str:
        if value in (None, ""):
            return "—"
        if digits is not None:
            return f"{float(value):.{digits}f}"
        return str(value)

    campaign_wall_s = sum(
        float(row.get(field) or 0.0)
        for row in selected
        for field in ("route_wall_s", "exhaustive_eval_wall_s", "path_eval_wall_s")
    )
    lines = [
        "# N-sweep fixed-fabric campaign " + ("— Phase 1 interim" if interim else "— final report"),
        "",
        "The primary result is the MRR-count and scalability advantage: Waksman uses strictly fewer MRRs at every sampled N and scales smoothly instead of following the padded-Beneš octave stair-step. At N=9 it uses 21 MRRs versus 56, a 62.5% reduction (reported as −63% on the figure). Worst insertion loss (WIL) is parity/supporting evidence, not a universal Waksman win.",
        "",
        "All cases use the pinned v2 octave envelope; canvas steps at N=4→5 and N=8→9 by design. No v1 140-µm-pitch result is mixed into these charts. A search window is a per-hop search-policy bound that may expand only in a recorded remediation retry; the envelope is the constitutional canvas/stage/wire geometry and never expands. The octave envelope, canvas, stage pitch, wire pitch, and global grid bounds stayed pinned.",
        "",
        "## Metrics",
        "",
        "| topology | N | config | status | MRR | savings % | crossings | worst-path crossings | worst IL dB | agreement | same-net | perp | remediation | window tracks |",
        "|---|---:|---|---|---:|---:|---:|---:|---:|---|---:|---:|---|---:|",
    ]
    for row in sorted(selected, key=lambda item: (int(item["N"]), item["config"], item["topology"])):
        lines.append(
            f"| {row['topology']} | {row['N']} | {row['config']} | {row['status']} | {row['mrr']} | "
            f"{float(row['mrr_savings_pct']):.1f} | {row['total_crossings']} | "
            f"{display(row.get('worst_path_crossings'))} | {display(row.get('worst_il_db'), digits=6)} | "
            f"{display(row.get('method_agreement'))} | {row['same_net_min_spacing']} | "
            f"{row['perpendicular_clearance']} | {row.get('remediation', 'none')} | "
            f"{row.get('window_expansion_tracks', 0)} |"
        )
    lines.extend(
        [
            "",
            "## Method and correctness record",
            "",
            f"- Exact exhaustive/path-space agreement: {sum(bool(row['method_agreement']) for row in certified)}/{len(certified)} certified routable cases (100%).",
            f"- Coverage failures: {sum(int(row.get('coverage_failures') or 0) for row in certified)}.",
            f"- Route-failed cases: {len(route_failed)}; failed edge records: {sum(int(row['failed_edges']) for row in route_failed)}. Route-failed cases have no WIL row in the agreement file and no witness certificate.",
            f"- Legacy DRC findings: {sum(int(row['legacy_drc']) for row in selected)}; bend-radius findings: {sum(int(row['bend_radius_legality']) for row in selected)}.",
            "- Every certificate contains a witness permutation and is rechecked with one strategy-route/loss call.",
            "- Padded-Beneš n=6 uses BenesLoopingStrategy, not the older CLI LUT strategy, so older LUT-based n=6 artifacts are not geometry/WIL goldens for this campaign.",
            "",
            "## Routing audits and deterministic-template advantage",
            "",
            "`corridor_guide_mode` is a NO-OP: the flag is consumed nowhere. The always-on `_corridor_preferred_x` bias (`routing/physical.py`, historical line 877 anchor) runs in every mode, and corrected G7 compares identical geometry and crossings for `off` and `soft`. This campaign does not claim or implement a real guide layer.",
            "",
            "Waksman A* same-net-min-spacing and perpendicular-clearance audit counts grow in scale with N and are generally worse under C, while every deterministic padded-Beneš template case is all-clean. These are measurement-only audits for A* acceptance, but the contrast is a real deterministic-template advantage: the template is clean by construction rather than merely finding a lower-loss A* score.",
            "",
        ]
    )
    for config in CONFIGS:
        audit_rows = sorted(
            (
                row for row in selected
                if row["topology"] == "waksman" and row["config"] == config
            ),
            key=lambda row: int(row["N"]),
        )
        if audit_rows:
            audit_text = ", ".join(
                f"N={row['N']}:{row['same_net_min_spacing']}/{row['perpendicular_clearance']}"
                for row in audit_rows
            )
            lines.append(f"- Waksman {config} same-net/perpendicular counts: {audit_text}.")
    lines.append("")
    if interim:
        lines.extend(
            [
                "## Required Phase 1 layouts",
                "",
                "- `cases/waksman/n06/B_db_placeholder/fixed_fabric_layout.png`",
                "- `cases/waksman/n06/C_db_realistic/fixed_fabric_layout.png`",
                "- `cases/padded_benes/n06/B_db_placeholder/fixed_fabric_layout.png`",
                "- `cases/padded_benes/n06/C_db_realistic/fixed_fabric_layout.png`",
                "",
            ]
        )
    else:
        lines.extend(["## Physical-layer WIL wins, ties, and losses", ""])
        for config in CONFIGS:
            wins, ties, losses, unavailable = _win_summary(rows, config)
            lines.append(
                f"- {config}: Waksman wins at N={wins or 'none'}, ties at N={ties or 'none'}, "
                f"loses at N={losses or 'none'}, and is unavailable at N={unavailable or 'none'}; "
                f"largest winning N={max(wins) if wins else 'none'}."
            )
        lines.extend(
            [
                "",
                "The logical-layer reference window is N=9..12 with a reported 12→13 crossover. The physical result above is the authoritative matched-envelope comparison; differences arise from routed length, bends, crossings, and deterministic strategy-relative path use.",
                "",
                "## Figures",
                "",
                "The primary paper figures are `charts/mrr_count_vs_n.png` and `charts/mrr_savings_pct_vs_n.png`. WIL parity, bars, crossing, and win-margin figures are in the same directory. All figures mark the octave canvas boundaries.",
                "The WIL axes start at 0 so sub-0.1 dB deltas are not visually exaggerated. Config C explicitly marks the Waksman N=10 route-failed gap instead of joining or imputing it.",
                "",
            ]
        )
    lines.extend(
        [
            "## Design decisions and N=10/C conclusion",
            "",
            "The same-net whole-net remediation evolved through three mechanism iterations: (1) endpoint-axis handling was corrected so displaced endpoint risers preserve physical port approach; (2) displacement moved to the predecessor hop that created the committed obstruction and the bounded fallback beam was widened; (3) a default-off remediation-only search-window expansion added `abs(displacement)+2` tracks per ladder attempt. The final exact campaign-flags 300k run exhausted +1/−1/+2/−2 on all six routing passes. It retained 24 attempt records and still ended at I4→O4 with seven edge records; the final displaced segment `(493,444)→(493,593)` exceeded the expanded hop window whose top reached 582. Conclusion: N=10/C is `route_failed`, with artifacts retained and no certified WIL.",
            "",
            "The rung-pollution diagnosis found no leakage: each pop rung rebuilt cells, routing state, and rules. The lone apparent success used `full_drc=True`; that activated same-net/perpendicular audit violations inside routing pass scoring and selected different geometry. It was a DRC-coupled experiment, not an exact campaign-flags success, and is excluded.",
            "",
            "Search window and envelope are distinct. The search window is expandable per hop, only in the remediation retry, config-gated and recorded as `window_expansion_tracks`. The octave envelope is pinned and constitutional; it did not change.",
            "",
            "## Runtime and anomalies",
            "",
            f"Observed wall time for this invocation: {elapsed_s:.1f} s; summed recorded route/evaluation work across rows: {campaign_wall_s:.1f} s. Per-case times are recorded in `metrics.csv`/`metrics.partial.csv`.",
            "",
            "No anomaly is silently normalized: status, pop rung, no-op guide flag, layout mode, optional DRC audit counts, remediation mechanism, search-window expansion, and envelope ID are recorded per case.",
            "",
            "## Phase 0 algorithm decisions",
            "",
            "See `PHASE0_DESIGN_DECISIONS.md` for the algorithm decisions, integrity corrections, and exactness lemma.",
            "",
        ]
    )
    target = output_root / ("REPORT_PHASE1.md" if interim else "REPORT.md")
    target.write_text("\n".join(lines))
    if interim:
        (output_root / "REPORT.md").write_text("\n".join(lines))


def verify_certificates(filters: dict[str, str]) -> None:
    checked = 0
    skipped_route_failed = 0
    for kind in TOPOLOGIES:
        for n in range(3, 13):
            for config in CONFIGS:
                if not _selected(filters, kind, n, config):
                    continue
                outdir = _case_dir(kind, n, config)
                certificate_path = outdir / "worst_il_certificate.json"
                metrics_path = outdir / "case_metrics.json"
                if metrics_path.is_file():
                    case_metrics = json.loads(metrics_path.read_text())
                    if case_metrics.get("status") == "route_failed":
                        if certificate_path.exists():
                            raise RuntimeError(
                                f"route_failed case unexpectedly has certificate "
                                f"{kind} N={n} {config}"
                            )
                        skipped_route_failed += 1
                        continue
                cached = _load_valid_route(
                    outdir,
                    {"case_key": f"v2:{kind}:n{n}:{config}:{octave_envelope(n).envelope_id}"},
                )
                if cached is None or not certificate_path.is_file():
                    raise FileNotFoundError(f"missing valid certificate case {kind} N={n} {config}")
                cells, result = cached
                topology = _make_topology(kind, n)
                payload = json.loads(certificate_path.read_text())
                verify_worst_il_witness(
                    topology,
                    cells,
                    result,
                    witness_permutation=tuple(payload["witness_permutation"]),
                    input_port=int(payload["worst_io_pair"][0]),
                    expected_il_db=float(payload["worst_il_db"]),
                )
                checked += 1
    print(
        f"verified {checked} worst-IL certificates; "
        f"skipped {skipped_route_failed} route_failed cases"
    )


def _parse_filters(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    filters: dict[str, str] = {}
    for part in raw.split(","):
        key, separator, value = part.partition("=")
        if not separator or key not in {"topo", "n", "config"} or not value:
            raise ValueError(f"invalid --only filter {part!r}")
        filters[key] = value
    return filters


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the v2 N=3..12 fixed-fabric sweep")
    parser.add_argument("--only", help="comma-separated topo=,n=,config= filters")
    parser.add_argument("--workers", type=int, default=64)
    parser.add_argument("--verify-certificates", action="store_true")
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    parser.add_argument("--min-crossing-clearance", type=float)
    parser.add_argument("--straighten-jogs", action="store_true")
    parser.add_argument("--physical-turn-guard", action="store_true")
    parser.add_argument(
        "--v4-search",
        action="store_true",
        help="enable the standard v4 crossing/search discipline and staged port relaxation",
    )
    parser.add_argument("--campaign-version", default="v2")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 64:
        parser.error("--workers must be between 1 and 64")
    filters = _parse_filters(args.only)
    if args.verify_certificates:
        verify_certificates(filters)
    else:
        run_campaign(
            filters,
            args.workers,
            output_root=args.output_root,
            min_crossing_clearance_um=args.min_crossing_clearance,
            straighten_jogs=args.straighten_jogs,
            physical_turn_guard=args.physical_turn_guard,
            v4_search=args.v4_search,
            campaign_version=args.campaign_version,
        )


if __name__ == "__main__":
    main()
