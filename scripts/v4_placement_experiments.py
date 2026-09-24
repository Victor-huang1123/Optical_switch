from __future__ import annotations

import argparse
from dataclasses import replace
import json
from math import ceil
from pathlib import Path
import re
import time
from typing import Any, Literal

from mrr_switch_optimizer.analysis.nsweep import evaluate_fixed_fabric_path_space
from mrr_switch_optimizer.app.nsweep_campaign import (
    _audit_counts,
    _campaign_rules,
    _foreign_port_access_frontier_exhausted,
    _make_topology,
    _physical_routes,
)
from mrr_switch_optimizer.core.fabric import build_fabric_graph
from mrr_switch_optimizer.core.models import MRRCell
from mrr_switch_optimizer.core.sparams import load_mrr_s_table
from mrr_switch_optimizer.routing.crossing import merged_route_arm_clearances
from mrr_switch_optimizer.routing.envelope import build_envelope_cells, octave_envelope
from mrr_switch_optimizer.routing.fabric import (
    FixedFabricRoutingResult,
    _fabric_waveguide_paths,
    route_fixed_fabric,
)
from mrr_switch_optimizer.routing.geometry import (
    _orthogonal_crossing_point,
    _segment_contact_point,
    _segments_collinear_overlap,
)
from mrr_switch_optimizer.routing.port_access import build_port_access_plan


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "outputs" / "v4_placement_experiments"
Y_BIASES_UM = (0.0, 4.0, 8.0, 12.0)
X_STAGGERS_UM = (0.0, 16.0, 32.0)
TOPOLOGIES = ("waksman", "padded_benes")
SIZES = (6, 8)
CONFIG_NAMES = ("B_db_placeholder", "C_db_realistic")


def _shifted_cells(
    cells: dict[str, MRRCell],
    topology: Any,
    *,
    y_bias_um: float = 0.0,
    x_stagger_um: float = 0.0,
) -> dict[str, MRRCell]:
    rank_by_id: dict[str, int] = {}
    by_stage: dict[int, list[MRRCell]] = {}
    for cell_id, cell in cells.items():
        by_stage.setdefault(topology.get_mrr_stage(cell_id), []).append(cell)
    for stage_cells in by_stage.values():
        for rank, cell in enumerate(
            sorted(stage_cells, key=lambda item: (-item.center[1], item.id))
        ):
            rank_by_id[cell.id] = rank
    return {
        cell_id: replace(
            cell,
            center=(
                cell.center[0]
                + (x_stagger_um if rank_by_id[cell_id] % 2 else 0.0),
                cell.center[1] - y_bias_um,
            ),
        )
        for cell_id, cell in cells.items()
    }


def _route_with_escalation(
    topology: Any,
    cells: dict[str, MRRCell],
    config: str,
    *,
    layout_mode: Literal["astar", "template"],
    max_astar_pops: int,
) -> tuple[FixedFabricRoutingResult, int, float]:
    graph = build_fabric_graph(topology)
    envelope = octave_envelope(topology.N_logical)
    started = time.perf_counter()
    stage = 0
    while True:
        rules = _campaign_rules(
            config,
            pops=max_astar_pops,
            topology="waksman" if topology.name.startswith("waksman") else "padded_benes",
            full_drc=True,
            min_crossing_clearance_um=10.0,
            physical_turn_guard=True,
            physical_same_net_hairpin=True,
            reserved_region_stage=stage,
        )
        result = route_fixed_fabric(
            topology,
            graph,
            cells,
            rules,
            x_start=envelope.x_start_um,
            x_end=envelope.x_end_um,
            wire_pitch_um=envelope.wire_pitch_um,
            layout_mode=layout_mode,
        )
        if (
            layout_mode == "template"
            or stage >= 2
            or not _foreign_port_access_frontier_exhausted(result)
        ):
            return result, stage, time.perf_counter() - started
        stage += 1


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = ceil(percentile * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


def _external_crossing_clearances(result: FixedFabricRoutingResult) -> list[float]:
    routes = _physical_routes(result)
    values: list[float] = []
    seen: set[tuple[int, int, tuple[float, float]]] = set()
    for index, first in enumerate(routes):
        for second in routes[index + 1 :]:
            for first_segment in first.external_segments:
                for second_segment in second.external_segments:
                    location = _orthogonal_crossing_point(first_segment, second_segment)
                    if location is None:
                        continue
                    key = (first.input_port, second.input_port, location)
                    if key in seen:
                        continue
                    seen.add(key)
                    values.append(
                        min(
                            *merged_route_arm_clearances(first, location),
                            *merged_route_arm_clearances(second, location),
                        )
                    )
    return values


def _port_access_congestion(
    topology: Any,
    cells: dict[str, MRRCell],
    result: FixedFabricRoutingResult,
) -> tuple[int, int]:
    paths = _fabric_waveguide_paths(topology, result.graph)
    plan = build_port_access_plan(paths, cells, result.rules, 2.0)
    physical = _physical_routes(result)
    contacts = 0
    for route in physical:
        for segment in route.external_segments:
            for region in plan.reserved_regions:
                if region.owner_input == route.input_port:
                    continue
                if (
                    _segments_collinear_overlap(segment, region.segment)
                    or _orthogonal_crossing_point(segment, region.segment) is not None
                    or _segment_contact_point(segment, region.segment) is not None
                ):
                    contacts += 1
    failed_prunes = sum(
        int(match.group(1))
        for failure in result.failed_edges
        for match in [re.search(r"\bport_access:(\d+)\b", failure.message)]
        if match is not None
    )
    return contacts, failed_prunes


def _row(
    family: str,
    lane: str,
    topology_name: str,
    n: int,
    config: str,
    y_bias_um: float,
    x_stagger_um: float,
    cells: dict[str, MRRCell],
    topology: Any,
    result: FixedFabricRoutingResult,
    escalation_stage: int,
    wall_s: float,
    max_astar_pops: int,
) -> dict[str, Any]:
    audit = _audit_counts(result, cells)
    clearances = _external_crossing_clearances(result)
    contacts, failed_prunes = _port_access_congestion(topology, cells, result)
    worst_il: float | None = None
    if not result.failed_edges and audit["legacy_drc"] == 0 and audit["bend_radius_legality"] == 0:
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
        worst_il = evaluate_fixed_fabric_path_space(
            topology,
            cells,
            evaluation_result,
        ).worst_insertion_loss_db
    return {
        "family": family,
        "lane": lane,
        "topology": topology_name,
        "N": n,
        "config": config,
        "y_bias_um": y_bias_um,
        "x_stagger_um": x_stagger_um,
        "status": "route_failed" if result.failed_edges else "routed",
        "failed_edges": len(result.failed_edges),
        "total_route_length_um": sum(route.length_um for route in result.routes),
        "total_bends": sum(route.bend_count for route in result.routes),
        "total_crossings_loss_model": len(result.crossings),
        "worst_il_db": worst_il,
        **audit,
        "explicit_crossings": len(clearances),
        "arm_clearance_min_um": min(clearances) if clearances else None,
        "arm_clearance_p10_um": _percentile(clearances, 0.10),
        "arm_clearance_median_um": _percentile(clearances, 0.50),
        "arm_clearance_p90_um": _percentile(clearances, 0.90),
        "arm_clearance_under_9p55": sum(value < 9.55 - 1e-9 for value in clearances),
        "foreign_port_access_contacts": contacts,
        "failed_port_access_prunes": failed_prunes,
        "reserved_region_escalation_stage": escalation_stage,
        "max_astar_pops": max_astar_pops,
        "route_wall_s": wall_s,
        "astar_calls": result.stats.astar_calls,
    }


def _key(row: dict[str, Any]) -> str:
    return ":".join(
        str(row[field])
        for field in (
            "family",
            "lane",
            "topology",
            "N",
            "config",
            "y_bias_um",
            "x_stagger_um",
        )
    )


def _write_json(path: Path, rows: list[dict[str, Any]], max_astar_pops: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "v4-placement-experiments-1",
                "max_astar_pops": max_astar_pops,
                "rows": rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _write_report(path: Path, rows: list[dict[str, Any]], max_astar_pops: int) -> None:
    y_rows = sorted(
        (row for row in rows if row["family"] == "y_bias"),
        key=lambda row: (
            row["lane"] != "astar",
            row["topology"],
            row["N"],
            row["config"],
            row["y_bias_um"],
        ),
    )
    x_rows = sorted(
        (row for row in rows if row["family"] == "x_stagger"),
        key=lambda row: (
            row["topology"],
            row["N"],
            row["config"],
            row["x_stagger_um"],
        ),
    )
    lines = [
        "# V4 placement experiments (report only)",
        "",
        "No placement rule is adopted by this report. The A* runs use v4 search "
        f"discipline with a {max_astar_pops:,}-pop per-hop cap and the recorded "
        "reserved-region escalation. `total_route_length_um` is the fabric-wide "
        "waveguide/wrap-length proxy. A y-bias moves every cell center toward its "
        "lower add/drop bus by the stated delta. An x-stagger leaves even-ranked "
        "cells in each stage at nominal x and shifts odd-ranked cells right by the "
        "stated delta.",
        "",
        "## Uniform y-bias",
        "",
        "| Lane | Topology | N | Config | y bias (um) | Status/failed | Length (um) | Bends | WIL (dB) | Same-net audit | Arm min (um) | Explicit crossings |",
        "|---|---|---:|:---:|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in y_rows:
        lines.append(
            "| {lane} | {topology} | {N} | {config} | {bias} | {status}/{failed} | "
            "{length} | {bends} | {wil} | {same} | {arm} | {crossings} |".format(
                lane=row["lane"],
                topology=row["topology"],
                N=row["N"],
                config=row["config"],
                bias=_fmt(row["y_bias_um"], 0),
                status=row["status"],
                failed=row["failed_edges"],
                length=_fmt(row["total_route_length_um"]),
                bends=row["total_bends"],
                wil=_fmt(row["worst_il_db"], 6),
                same=row["same_net_min_spacing"],
                arm=_fmt(row["arm_clearance_min_um"]),
                crossings=row["explicit_crossings"],
            )
        )
    lines.extend(
        [
            "",
            "The padded-Beneš n8 template rows are the required re-derived-anchor "
            "check. Compare `total_crossings_loss_model` in the raw JSON with "
            "T(8)=60; any delta is called out below.",
            "",
            "## In-column x-stagger",
            "",
            "| Topology | N | Config | x stagger (um) | Status/failed | Arm min / p10 / median / p90 (um) | Arms <9.55 | Foreign PA contacts | Failed PA prunes | Escalation |",
            "|---|---:|:---:|---:|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in x_rows:
        distribution = " / ".join(
            _fmt(row[field])
            for field in (
                "arm_clearance_min_um",
                "arm_clearance_p10_um",
                "arm_clearance_median_um",
                "arm_clearance_p90_um",
            )
        )
        lines.append(
            "| {topology} | {N} | {config} | {stagger} | {status}/{failed} | "
            "{distribution} | {under} | {contacts} | {prunes} | {stage} |".format(
                topology=row["topology"],
                N=row["N"],
                config=row["config"],
                stagger=_fmt(row["x_stagger_um"], 0),
                status=row["status"],
                failed=row["failed_edges"],
                distribution=distribution,
                under=row["arm_clearance_under_9p55"],
                contacts=row["foreign_port_access_contacts"],
                prunes=row["failed_port_access_prunes"],
                stage=row["reserved_region_escalation_stage"],
            )
        )
    template_deltas = [
        row
        for row in y_rows
        if row["lane"] == "template" and row["total_crossings_loss_model"] != 60
    ]
    lines.extend(
        [
            "",
            "## Template crossing invariant",
            "",
            (
                "All padded-Beneš n8 template rows retain T(8)=60."
                if not template_deltas
                else "; ".join(
                    f"delta={row['y_bias_um']:g} um produced "
                    f"{row['total_crossings_loss_model']} crossings"
                    for row in template_deltas
                )
            ),
            "",
            "## Decision",
            "",
            "Adoption is deferred to human review, as required. Production placement "
            "and the v3 geometry constants are unchanged.",
            "",
        ]
    )
    path.write_text("\n".join(lines))


def run(output: Path, max_astar_pops: int) -> list[dict[str, Any]]:
    results_path = output / "results.json"
    rows: list[dict[str, Any]] = []
    if results_path.is_file():
        payload = json.loads(results_path.read_text())
        if int(payload["max_astar_pops"]) != max_astar_pops:
            raise ValueError("resume pop budget differs from existing results.json")
        rows = list(payload["rows"])
    completed = {_key(row) for row in rows}
    s_table = load_mrr_s_table("mrr_sparam_library", strict=True)

    specifications: list[tuple[str, str, str, int, str, float, float]] = []
    # The template probe is deterministic and inexpensive, so record its T(8)
    # invariant before starting the longer A* matrix.
    for config in CONFIG_NAMES:
        for delta in Y_BIASES_UM:
            specifications.append(
                ("y_bias", "template", "padded_benes", 8, config, delta, 0.0)
            )
    for topology_name in TOPOLOGIES:
        for n in SIZES:
            for config in CONFIG_NAMES:
                for delta in Y_BIASES_UM:
                    specifications.append(
                        ("y_bias", "astar", topology_name, n, config, delta, 0.0)
                    )
                for delta in X_STAGGERS_UM:
                    specifications.append(
                        ("x_stagger", "astar", topology_name, n, config, 0.0, delta)
                    )
    for family, lane, topology_name, n, config, y_bias, x_stagger in specifications:
        probe = {
            "family": family,
            "lane": lane,
            "topology": topology_name,
            "N": n,
            "config": config,
            "y_bias_um": y_bias,
            "x_stagger_um": x_stagger,
        }
        if _key(probe) in completed:
            continue
        if family == "x_stagger" and x_stagger == 0.0:
            baseline = next(
                (
                    row
                    for row in rows
                    if row["family"] == "y_bias"
                    and row["lane"] == "astar"
                    and row["topology"] == topology_name
                    and row["N"] == n
                    and row["config"] == config
                    and row["y_bias_um"] == 0.0
                ),
                None,
            )
            if baseline is not None:
                rows.append(
                    {
                        **baseline,
                        "family": family,
                        "y_bias_um": 0.0,
                        "x_stagger_um": 0.0,
                    }
                )
                completed.add(_key(probe))
                _write_json(results_path, rows, max_astar_pops)
                continue
        topology = _make_topology(topology_name, n)
        envelope = octave_envelope(n)
        base_cells = build_envelope_cells(topology, s_table, envelope)
        cells = _shifted_cells(
            base_cells,
            topology,
            y_bias_um=y_bias,
            x_stagger_um=x_stagger,
        )
        print(
            f"{family} {lane} {topology_name} n={n} {config} "
            f"y={y_bias:g} x={x_stagger:g}",
            flush=True,
        )
        result, escalation_stage, wall_s = _route_with_escalation(
            topology,
            cells,
            config,
            layout_mode=lane,
            max_astar_pops=max_astar_pops,
        )
        rows.append(
            _row(
                family,
                lane,
                topology_name,
                n,
                config,
                y_bias,
                x_stagger,
                cells,
                topology,
                result,
                escalation_stage,
                wall_s,
                max_astar_pops,
            )
        )
        _write_json(results_path, rows, max_astar_pops)
    _write_report(ROOT / "placement_experiments.md", rows, max_astar_pops)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-astar-pops", type=int, default=30000)
    args = parser.parse_args()
    if args.max_astar_pops <= 0:
        parser.error("--max-astar-pops must be positive")
    rows = run(args.output, args.max_astar_pops)
    print(f"completed {len(rows)} placement rows", flush=True)


if __name__ == "__main__":
    main()
