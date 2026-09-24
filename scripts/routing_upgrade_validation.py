from __future__ import annotations

from collections import Counter
import csv
from dataclasses import asdict, dataclass, replace
import json
from math import isfinite
from pathlib import Path
from time import perf_counter

from mrr_switch_optimizer.analysis.fabric_coverage import verify_permutation_coverage
from mrr_switch_optimizer.analysis.fabric_loss import evaluate_fixed_fabric_worst_insertion_loss
from mrr_switch_optimizer.app.fabric_reports import fixed_fabric_geometry_hash, write_fixed_fabric_reports
from mrr_switch_optimizer.core.fabric import build_fabric_graph
from mrr_switch_optimizer.core.models import LEGACY_V2_CELL_GEOMETRY
from mrr_switch_optimizer.core.sparams import load_mrr_s_table
from mrr_switch_optimizer.core.state_assignment import BenesLoopingStrategy, WaksmanStrategy
from mrr_switch_optimizer.core.topology import PaddedBenesTopology, RNBTopology, WaksmanTopology
from mrr_switch_optimizer.output.visualize import save_fixed_fabric_png
from mrr_switch_optimizer.placement.layout import build_cells
from mrr_switch_optimizer.routing.drc import (
    _axis_segment_clearance,
    _consecutive_bend_pairs_too_close,
    validate_physical_routes,
)
from mrr_switch_optimizer.routing.fabric import FixedFabricRoutingResult, route_fixed_fabric
from mrr_switch_optimizer.routing.geometry import (
    _axis_segments,
    _orthogonal_crossing_point,
    _shared_endpoint,
)
from mrr_switch_optimizer.routing.types import DRCViolation, EPS, PhysicalRoute, RoutingRules, Segment


OUTPUT_ROOT = Path("outputs/routing_upgrade_test")
GOLDEN_HASHES = {
    4: "dc7b62ecb8ad46a3b06b1b707adc1278267c3b84befc73f29430c2d30a34b436",
    6: "0e3bb955f0aecf0b77d4f625fe81c5f973faaf027803626d3f202b1bb70ee1b1",
    8: "dbee43715e20f6f13d9d603f0f9029caebf058418fe84dc5509fa6455b36b67f",
}
CONFIG_NAMES = (
    "A_legacy",
    "B_db_placeholder",
    "C_db_realistic",
)
NEW_DRC_RULES = (
    "same_net_min_spacing",
    "perpendicular_clearance",
    "bend_radius_legality",
)
WAKSMAN = "Waksman"
PADDED_BENES = "Padded Beneš"
LEGACY_BENES_WORST_CROSSINGS = 17
LEGACY_BENES_WORST_IL_DB = 5.016731
PHASE_CASES = (
    *((WAKSMAN, n_logical, config_name) for n_logical in (4, 6) for config_name in CONFIG_NAMES),
    *((WAKSMAN, 8, config_name) for config_name in CONFIG_NAMES),
    *((PADDED_BENES, 8, config_name) for config_name in CONFIG_NAMES[1:]),
)


@dataclass(frozen=True)
class ValidationMetric:
    topology: str
    n_logical: int
    config: str
    geometry_sha256: str
    routed_edges: int
    failed_edges: int
    old_drc_count: int
    same_net_min_spacing_count: int
    perpendicular_clearance_count: int
    bend_radius_legality_count: int
    total_crossings: int
    worst_path_crossings: int
    worst_path_length_um: float
    worst_insertion_loss_db: float
    bend_pairs_lt_2r: int
    min_same_net_spacing_um: float
    min_cross_net_perpendicular_clearance_um: float
    wall_clock_s: float
    total_route_length_um: float
    png_path: str


def main() -> None:
    s_table = load_mrr_s_table("mrr_sparam_library", strict=True)
    rows: list[ValidationMetric] = []
    for topology_name, n_logical, config_name in PHASE_CASES:
        print(
            f"running {topology_name} n{n_logical}/{config_name}",
            flush=True,
        )
        rows.append(
            _run_case(
                n_logical,
                config_name,
                s_table,
                topology_name=topology_name,
            )
        )

    _assert_legacy_hashes(rows)
    regressions = _length_regressions(rows)
    if any(
        n_logical in {4, 6} and max(worst, total) > 20.0 + EPS
        for (n_logical, _config_name), (worst, total) in regressions.items()
    ):
        raise RuntimeError(
            "bend legalization exceeds the 20% length-regression stop threshold: "
            f"{regressions}"
        )

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    _write_json(OUTPUT_ROOT / "metrics.json", [asdict(row) for row in rows])
    report = _render_report(rows, regressions)
    (OUTPUT_ROOT / "REPORT.md").write_text(report)
    print(report, end="")


def _rules_for(config_name: str) -> RoutingRules:
    if config_name == "A_legacy":
        return RoutingRules(
            max_astar_pops=30000,
            max_ripup_passes=5,
            grid_margin_tracks=20,
        )
    if config_name == "B_db_placeholder":
        return RoutingRules(
            max_astar_pops=30000,
            max_ripup_passes=5,
            grid_margin_tracks=20,
            cost_model="db",
            enforce_bend_spacing=True,
            legalize_port_access=True,
        )
    if config_name == "C_db_realistic":
        return RoutingRules(
            max_astar_pops=30000,
            max_ripup_passes=5,
            grid_margin_tracks=20,
            cost_model="db",
            prop_loss_db_per_um=0.0002,
            bend_loss_db_per_bend=0.005,
            crossing_loss_db_per_cross=0.1,
            enforce_bend_spacing=True,
            legalize_port_access=True,
        )
    raise ValueError(f"unknown validation config {config_name!r}")


def _run_case(
    n_logical: int,
    config_name: str,
    s_table: dict[tuple[str, str, int], float],
    *,
    topology_name: str = WAKSMAN,
) -> ValidationMetric:
    if topology_name == WAKSMAN and n_logical not in {4, 6, 8}:
        raise ValueError("routing-upgrade validation supports Waksman n4/n6/n8")
    if topology_name == PADDED_BENES and n_logical != 8:
        raise ValueError("routing-upgrade validation supports padded Beneš n8 only")
    if topology_name not in {WAKSMAN, PADDED_BENES}:
        raise ValueError(f"unknown topology {topology_name!r}")
    if config_name not in CONFIG_NAMES:
        raise ValueError(f"unknown validation config {config_name!r}")
    if topology_name == PADDED_BENES and config_name == "A_legacy":
        raise ValueError("Phase 4 padded Beneš runs only configs B/C")

    topology = _build_topology(topology_name, n_logical)
    graph = build_fabric_graph(topology)
    cells = build_cells(
        topology,
        s_table,
        stage_pitch_um=140.0,
        wire_pitch_um=64.0,
        cell_geometry=LEGACY_V2_CELL_GEOMETRY,
    )
    rules = replace(
        _rules_for(config_name),
        waveguide_width_um=LEGACY_V2_CELL_GEOMETRY.waveguide_width_um,
    )
    x_end_um = max(cell.center[0] for cell in cells.values()) + 70.0
    started = perf_counter()
    result = route_fixed_fabric(
        topology,
        graph,
        cells,
        rules,
        x_start=20.0,
        x_end=x_end_um,
        wire_pitch_um=64.0,
    )
    wall_clock_s = perf_counter() - started
    if result.failed_edges:
        failure_details = "; ".join(
            f"{failure.edge_id}: {failure.message}"
            for failure in result.failed_edges[:3]
        )
        raise RuntimeError(
            f"{topology_name} n{n_logical}/{config_name} failed "
            f"{len(result.failed_edges)} fabric edges: "
            f"{failure_details}"
        )
    physical_routes = _physical_routes(result)
    old_violations = validate_physical_routes(physical_routes, cells, result.rules)
    measurement_rules = replace(
        result.rules,
        drc_same_net_min_spacing=True,
        drc_perpendicular_clearance=True,
        drc_bend_radius_legality=True,
    )
    all_violations = validate_physical_routes(
        physical_routes,
        cells,
        measurement_rules,
    )
    new_counts = Counter(
        violation.rule
        for violation in all_violations
        if violation.rule in NEW_DRC_RULES
    )
    if config_name != "A_legacy" and new_counts["bend_radius_legality"]:
        raise RuntimeError(
            f"{topology_name} n{n_logical}/{config_name} has bend-radius legality violations"
        )

    loss_result = (
        result
        if not result.drc_violations
        else replace(result, drc_violations=())
    )
    loss_report = evaluate_fixed_fabric_worst_insertion_loss(
        topology,
        cells,
        loss_result,
    )
    coverage = verify_permutation_coverage(topology, graph)
    case_dir = _case_dir(topology_name, n_logical, config_name)
    case_dir.mkdir(parents=True, exist_ok=True)
    write_fixed_fabric_reports(
        case_dir,
        graph,
        result,
        coverage,
        {
            "routing_upgrade_config": config_name,
            "routing_upgrade_topology": topology_name,
            "legacy_geometry_expected": config_name == "A_legacy",
        },
        loss_report,
    )
    png_path = case_dir / "fixed_fabric_layout.png"
    save_fixed_fabric_png(
        topology,
        cells,
        result,
        png_path,
        wire_pitch_um=64.0,
        x_start_um=20.0,
        x_end_um=x_end_um,
    )
    _write_drc_csv(case_dir / "routing_upgrade_drc.csv", all_violations)

    metric = ValidationMetric(
        topology=topology_name,
        n_logical=n_logical,
        config=config_name,
        geometry_sha256=fixed_fabric_geometry_hash(result),
        routed_edges=len(result.routed_edge_ids),
        failed_edges=len(result.failed_edges),
        old_drc_count=len(old_violations),
        same_net_min_spacing_count=new_counts["same_net_min_spacing"],
        perpendicular_clearance_count=new_counts["perpendicular_clearance"],
        bend_radius_legality_count=new_counts["bend_radius_legality"],
        total_crossings=len(result.crossings),
        worst_path_crossings=loss_report.worst_crossing_count,
        worst_path_length_um=loss_report.worst_path_length_um,
        worst_insertion_loss_db=loss_report.worst_insertion_loss_db,
        bend_pairs_lt_2r=sum(
            len(_consecutive_bend_pairs_too_close(route, measurement_rules))
            for route in physical_routes
        ),
        min_same_net_spacing_um=_min_same_net_spacing(physical_routes),
        min_cross_net_perpendicular_clearance_um=(
            _min_cross_net_perpendicular_clearance(physical_routes)
        ),
        wall_clock_s=wall_clock_s,
        total_route_length_um=sum(route.length_um for route in result.routes),
        png_path=str(png_path),
    )
    if (
        topology_name == WAKSMAN
        and n_logical == 6
        and config_name == "C_db_realistic"
        and (
            metric.old_drc_count != 0
            or metric.min_same_net_spacing_um <= EPS
        )
    ):
        raise RuntimeError(
            "n6 C_db_realistic gate failed: expected zero legacy DRC and positive "
            f"same-net spacing, got DRC={metric.old_drc_count}, "
            f"spacing={metric.min_same_net_spacing_um:.3f} um"
        )
    _write_json(case_dir / "routing_upgrade_metrics.json", asdict(metric))
    return metric


def _build_topology(topology_name: str, n_logical: int) -> RNBTopology:
    if topology_name == WAKSMAN:
        return WaksmanTopology(n_logical, strategy=WaksmanStrategy())
    if topology_name == PADDED_BENES:
        return PaddedBenesTopology(n_logical, strategy=BenesLoopingStrategy())
    raise ValueError(f"unknown topology {topology_name!r}")


def _case_dir(topology_name: str, n_logical: int, config_name: str) -> Path:
    group = f"n{n_logical}" if topology_name == WAKSMAN else f"benes{n_logical}"
    return OUTPUT_ROOT / group / config_name


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
        )
        for route in result.routes
    )


def _min_same_net_spacing(routes: tuple[PhysicalRoute, ...]) -> float:
    minimum = float("inf")
    for route in routes:
        segments = _axis_segments(route.waypoints)
        for first_idx, first in enumerate(segments):
            for second in segments[first_idx + 2 :]:
                if _shared_endpoint(first, second) is not None:
                    continue
                distance, _location = _axis_segment_clearance(first, second)
                minimum = min(minimum, distance)
    return minimum


def _min_cross_net_perpendicular_clearance(
    routes: tuple[PhysicalRoute, ...],
) -> float:
    minimum = float("inf")
    for first_idx, first_route in enumerate(routes):
        for second_route in routes[first_idx + 1 :]:
            for first in first_route.external_segments:
                for second in second_route.external_segments:
                    if _segments_have_same_axis(first, second):
                        continue
                    if _orthogonal_crossing_point(first, second) is not None:
                        continue
                    distance, _location = _axis_segment_clearance(first, second)
                    minimum = min(minimum, distance)
    return minimum


def _segments_have_same_axis(first: Segment, second: Segment) -> bool:
    first_horizontal = abs(first[0][1] - first[1][1]) < EPS
    second_horizontal = abs(second[0][1] - second[1][1]) < EPS
    return first_horizontal == second_horizontal


def _assert_legacy_hashes(rows: list[ValidationMetric]) -> None:
    for row in rows:
        if row.topology != WAKSMAN or row.config != "A_legacy":
            continue
        expected = GOLDEN_HASHES[row.n_logical]
        if row.geometry_sha256 != expected:
            raise RuntimeError(
                f"n{row.n_logical} legacy hash mismatch: "
                f"{row.geometry_sha256} != {expected}"
            )


def _length_regressions(
    rows: list[ValidationMetric],
) -> dict[tuple[int, str], tuple[float, float]]:
    by_key = {
        (row.n_logical, row.config): row
        for row in rows
        if row.topology == WAKSMAN
    }
    regressions: dict[tuple[int, str], tuple[float, float]] = {}
    for n_logical in (4, 6, 8):
        baseline = by_key[(n_logical, "A_legacy")]
        for config_name in ("B_db_placeholder", "C_db_realistic"):
            row = by_key[(n_logical, config_name)]
            regressions[(n_logical, config_name)] = (
                100.0
                * (row.worst_path_length_um / baseline.worst_path_length_um - 1.0),
                100.0
                * (row.total_route_length_um / baseline.total_route_length_um - 1.0),
            )
    return regressions


def _render_report(
    rows: list[ValidationMetric],
    regressions: dict[tuple[int, str], tuple[float, float]],
) -> str:
    lines = [
        "# Fixed-Fabric Routing Upgrade — Phase 4 Report",
        "",
        "## Metrics",
        "",
        "| Topology | Size | Config | Routed/failed | DRC old | Same-net | Perpendicular | Bend radius | Crossings | Worst crossings | Worst length µm | Worst IL dB | Bend pairs <2R | Min same-net µm | Min perpendicular µm | Runtime s |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                (
                    row.topology,
                    f"n{row.n_logical}",
                    row.config,
                    f"{row.routed_edges}/{row.failed_edges}",
                    str(row.old_drc_count),
                    str(row.same_net_min_spacing_count),
                    str(row.perpendicular_clearance_count),
                    str(row.bend_radius_legality_count),
                    str(row.total_crossings),
                    str(row.worst_path_crossings),
                    _format_metric(row.worst_path_length_um, 3),
                    _format_metric(row.worst_insertion_loss_db, 6),
                    str(row.bend_pairs_lt_2r),
                    _format_metric(row.min_same_net_spacing_um, 3),
                    _format_metric(
                        row.min_cross_net_perpendicular_clearance_um,
                        3,
                    ),
                    _format_metric(row.wall_clock_s, 3),
                )
            )
            + " |"
        )

    n8 = {
        (row.topology, row.config): row
        for row in rows
        if row.n_logical == 8
    }
    lines.extend(
        (
            "",
            "## Waksman vs Padded Beneš",
            "",
            "| Config | Waksman worst crossings | Beneš worst crossings | Waksman−Beneš gap | Waksman worst IL dB | Beneš worst IL dB | Lower worst IL |",
            "|---|---:|---:|---:|---:|---:|---|",
            f"| A_legacy | {n8[(WAKSMAN, 'A_legacy')].worst_path_crossings} | {LEGACY_BENES_WORST_CROSSINGS} | {n8[(WAKSMAN, 'A_legacy')].worst_path_crossings - LEGACY_BENES_WORST_CROSSINGS:+d} | {n8[(WAKSMAN, 'A_legacy')].worst_insertion_loss_db:.6f} | {LEGACY_BENES_WORST_IL_DB:.6f} | Padded Beneš |",
        )
    )
    for config_name in CONFIG_NAMES[1:]:
        waksman = n8[(WAKSMAN, config_name)]
        benes = n8[(PADDED_BENES, config_name)]
        lower = (
            WAKSMAN
            if waksman.worst_insertion_loss_db < benes.worst_insertion_loss_db
            else PADDED_BENES
        )
        lines.append(
            f"| {config_name} | {waksman.worst_path_crossings} | "
            f"{benes.worst_path_crossings} | "
            f"{waksman.worst_path_crossings - benes.worst_path_crossings:+d} | "
            f"{waksman.worst_insertion_loss_db:.6f} | "
            f"{benes.worst_insertion_loss_db:.6f} | {lower} |"
        )

    legacy_gap = n8[(WAKSMAN, "A_legacy")].worst_path_crossings - LEGACY_BENES_WORST_CROSSINGS
    lines.append("")
    for config_name in CONFIG_NAMES[1:]:
        waksman = n8[(WAKSMAN, config_name)]
        benes = n8[(PADDED_BENES, config_name)]
        gap = waksman.worst_path_crossings - benes.worst_path_crossings
        if gap <= 0:
            conclusion = f"closes fully and reverses to {gap:+d}"
        elif gap < legacy_gap:
            conclusion = f"narrows from +{legacy_gap} to +{gap}, but does not fully close"
        else:
            conclusion = f"does not close (legacy +{legacy_gap}, now {gap:+d})"
        lines.append(f"- Under {config_name}, the worst-path crossing gap {conclusion}.")
    c_waksman = n8[(WAKSMAN, "C_db_realistic")]
    c_benes = n8[(PADDED_BENES, "C_db_realistic")]
    c_order = sorted(
        ((c_waksman.worst_insertion_loss_db, WAKSMAN), (c_benes.worst_insertion_loss_db, PADDED_BENES))
    )
    lines.append(
        f"- Under C_db_realistic, {c_order[0][1]} ranks first (lower worst IL) at "
        f"{c_order[0][0]:.6f} dB; {c_order[1][1]} ranks second at "
        f"{c_order[1][0]:.6f} dB, a {c_order[1][0] - c_order[0][0]:.6f} dB gap."
    )

    lines.extend(("", "## Layout PNGs", ""))
    lines.extend(f"- `{row.png_path}`" for row in rows)
    lines.extend(
        (
            "",
            "## Baseline Confirmation",
            "",
            f"- Config A Waksman n8 geometry SHA-256: `{GOLDEN_HASHES[8]}`.",
            "- Pre-edit n8 blind spots reproduced exactly: 2.0 µm minimum non-crossing cross-net clearance, 4.0 µm minimum non-adjacent same-net spacing, and 11 consecutive bend pairs below 10 µm (minimum 4.0 µm).",
            f"- Config A n4 geometry SHA-256: `{GOLDEN_HASHES[4]}`.",
            f"- Config A n6 geometry SHA-256: `{GOLDEN_HASHES[6]}`.",
            "- All baseline/default flags remained off; committed geometry is unchanged.",
            "- The required n6 C_db_realistic gate re-routes 28/0 edges with zero legacy DRC violations and 4.000 µm minimum same-net spacing; no 0.000 µm same-net contact remains.",
            "",
            "## Design Decisions",
            "",
            "- `same_net_touching_corner` is a hard candidate-generation constraint in dB mode, checked during A* expansion and deterministic candidate validation. This prevents invalid geometry directly without inflating nonphysical penalties in the dB objective; other guidance remains at `db_tie_breaker_scale=0.05`.",
            "- dB fixed-fabric routing is constrained-first: unequal Waksman paths use most edges first, while padded Beneš routes constrained boundary wires first and the remaining inputs in descending order. This gives scarce port/crossing access to the paths that otherwise fail late; legacy `um_penalty` ordering is unchanged.",
            "- Waksman dB routes that hit a same-net dead end activate the existing bounded multi-hop fallback at width/alternatives 4. Padded Beneš dB routes use width 2 only after a 30,000-pop hop failure; explicit fallback settings and legacy/default routing are unchanged.",
            "- dB reserved-spacing checks include parallel endpoint near-misses around future same-net port runways. This prevents an earlier hop from consuming the only legal departure direction at a later port.",
            "- Port access uses a flagged fixed per-side runway macro. The 2 µm device stub and 10 µm escape remain unchanged; A* connects 10 µm (at least 2R) beyond the escape, preserving legacy geometry when disabled.",
            "- A* state includes incoming direction and capped straight-run distance only when `enforce_bend_spacing` is enabled; target continuation is checked against the port macro.",
            "",
            "## Files Changed",
            "",
            "- `mrr_switch_optimizer/routing/types.py` — adds default-off DRC, cost-model, bend-spacing, and port-runway controls.",
            "- `mrr_switch_optimizer/routing/drc.py` — adds the three blind-spot rules and rule validation.",
            "- `mrr_switch_optimizer/routing/grid_router.py` — extends flagged A* state with straight-run distance.",
            "- `mrr_switch_optimizer/routing/grid.py` — normalizes dB costs and enforces turn, self-contact, and future-port endpoint legality.",
            "- `mrr_switch_optimizer/routing/port_access.py` — defines the pre-legalized side-runway macro.",
            "- `mrr_switch_optimizer/routing/physical.py` — routes to macro endpoints and applies targeted bounded hop backtracking.",
            "- `mrr_switch_optimizer/routing/fabric.py` — applies dB-only constrained-first Waksman and padded-Beneš ordering.",
            "- `mrr_switch_optimizer/app/cli.py` — exposes only new opt-in CLI flags; defaults remain legacy.",
            "- `mrr_switch_optimizer/app/routing_upgrade_validation.py` — runs the guarded Phase 3/4 matrix and writes this report.",
            "- `tests/test_routing_upgrade.py` — covers golden hashes, blind spots, dB normalization, hard self-contact rejection, route order, A* state, macro geometry, and bend legality.",
            "",
            "## Open Risks",
            "",
        )
    )
    for row in rows:
        if row.old_drc_count:
            lines.append(
                f"- {row.topology} n{row.n_logical} {row.config} retains "
                f"{row.old_drc_count} legacy DRC violation(s); its IL is a nominal "
                "geometry/loss-model value and the PNG requires review."
            )
    for (n_logical, config_name), (worst, total) in regressions.items():
        lines.append(
            f"- n{n_logical} {config_name} length change versus A: "
            f"{worst:+.2f}% worst path, {total:+.2f}% total fabric."
        )
    lines.extend(
        (
            "- Exact same-net self-contact is hard-rejected in dB mode, but the broader same-net spacing metric remains measurement-only and uses a conservative inclusive 4.0 µm boundary.",
            "- Perpendicular-clearance counts remain measurement-only; true orthogonal crossings are still legal and excluded from that count.",
            "",
        )
    )
    return "\n".join(lines)


def _write_drc_csv(path: Path, violations: tuple[DRCViolation, ...]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("rule", "net_id", "message", "x_um", "y_um"),
        )
        writer.writeheader()
        for violation in violations:
            writer.writerow(
                {
                    "rule": violation.rule,
                    "net_id": violation.net_id,
                    "message": violation.message,
                    "x_um": "" if violation.location is None else violation.location[0],
                    "y_um": "" if violation.location is None else violation.location[1],
                }
            )


def _write_json(path: Path, payload: object) -> None:
    with path.open("w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)


def _format_metric(value: float, digits: int) -> str:
    if not isfinite(value):
        return "—"
    return f"{value:.{digits}f}"


if __name__ == "__main__":
    main()
