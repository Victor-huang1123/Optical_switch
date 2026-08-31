from __future__ import annotations

import csv
import re
from pathlib import Path
from statistics import median

import matplotlib.pyplot as plt
from matplotlib.axes import Axes

from mrr_switch_optimizer.core.models import MRRCell
from mrr_switch_optimizer.core.sparams import load_mrr_s_table
from mrr_switch_optimizer.core.topology import Path as RoutedPath
from mrr_switch_optimizer.core.topology import PaddedBenesTopology, StateAssignment
from mrr_switch_optimizer.output.visualize import COLORS, PIC_TEXT, _base_figure
from mrr_switch_optimizer.placement.layout import build_cells, wire_y
from mrr_switch_optimizer.routing.geometry import _routing_obstacles
from mrr_switch_optimizer.routing.grid import _routing_grid
from mrr_switch_optimizer.routing.physical import route_physical_design
from mrr_switch_optimizer.routing.port_access import _port_escape_point
from mrr_switch_optimizer.routing.types import (
    FailedNet,
    PhysicalRoutingResult,
    Point,
    RoutingRules,
    Segment,
)


ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "Physical_output" / "output" / "task11_grid_sparsified_probe"
PERMUTATION = (2, 0, 5, 1, 3, 4)
BASELINE_NODE_COUNT = 12_726
GRID_PITCH_UM = 8.0
PORT_STUB_UM = 2.0
WIRE_PITCH_UM = 36.0
X_START = 20.0
DensitySummary = dict[str, float | int]


def main() -> None:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    topology = PaddedBenesTopology()
    states = topology.get_state_assignment(PERMUTATION)
    s_table = load_mrr_s_table(ROOT / "mrr_sparam_library")
    cells = build_cells(topology, s_table)
    paths = topology.get_active_paths(PERMUTATION, states)
    x_end = max(cell.center[0] for cell in cells.values()) + 70.0

    density_rules = RoutingRules()
    grid = _routing_grid(
        paths,
        cells,
        _routing_obstacles(cells),
        density_rules,
        x_start=X_START,
        x_end=x_end,
        wire_pitch_um=WIRE_PITCH_UM,
        port_stub_um=PORT_STUB_UM,
    )
    density = _density_summary(grid)

    probes: list[tuple[str, RoutingRules, PhysicalRoutingResult]] = []
    for tag, ripup_cap in (("4500_900", 900), ("4500_2400", 2400)):
        rules = RoutingRules(max_astar_pops=4500, ripup_max_astar_pops=ripup_cap)
        result = route_physical_design(paths, cells, rules=rules)
        probes.append((tag, rules, result))
        _save_probe_png(tag, topology, states, cells, paths, rules, result)

    _save_summary_csv(density, probes)
    _save_report(density, probes)


def _density_summary(grid: tuple[tuple[float, ...], tuple[float, ...]]) -> DensitySummary:
    x_tracks, y_tracks = grid
    x_gaps = _gaps(x_tracks)
    y_gaps = _gaps(y_tracks)
    subpitch = 0.5 * GRID_PITCH_UM
    return {
        "baseline_nodes": BASELINE_NODE_COUNT,
        "x_tracks": len(x_tracks),
        "y_tracks": len(y_tracks),
        "nodes": len(x_tracks) * len(y_tracks),
        "node_reduction_pct": 100.0 * (1.0 - (len(x_tracks) * len(y_tracks)) / BASELINE_NODE_COUNT),
        "x_min_gap_um": min(x_gaps),
        "y_min_gap_um": min(y_gaps),
        "x_median_gap_um": median(x_gaps),
        "y_median_gap_um": median(y_gaps),
        "x_subpitch_gaps": sum(1 for gap in x_gaps if gap < subpitch - 1e-6),
        "y_subpitch_gaps": sum(1 for gap in y_gaps if gap < subpitch - 1e-6),
    }


def _gaps(tracks: tuple[float, ...]) -> list[float]:
    return [b - a for a, b in zip(tracks, tracks[1:])]


def _save_probe_png(
    tag: str,
    topology: PaddedBenesTopology,
    states: StateAssignment,
    cells: dict[str, MRRCell],
    paths: list[RoutedPath],
    rules: RoutingRules,
    result: PhysicalRoutingResult,
) -> None:
    fig, ax = _base_figure(
        topology,
        PERMUTATION,
        states,
        cells,
        f"task11 bounded {tag}",
        rules,
    )
    for idx, route in enumerate(sorted(result.routes, key=lambda item: item.input_port)):
        color = COLORS[idx % len(COLORS)]
        xs = [point[0] for point in route.waypoints]
        ys = [point[1] for point in route.waypoints]
        ax.plot(xs, ys, color=color, lw=2.35, alpha=0.95, zorder=6)
        ax.scatter(xs[-1], ys[-1], color=color, s=18, zorder=9)
        ax.text(xs[0] + 3.0, ys[0] + 5.0, f"I{route.input_port}", color=color, fontsize=7, zorder=12)

    for idx, failed in enumerate(result.failed_nets):
        _draw_failed_net(ax, failed, cells, rules, topology.N_physical, idx)

    info = [
        f"routed {len(result.routes)}/{len(paths)}",
        f"failed {len(result.failed_nets)}",
        f"DRC {len(result.drc_violations)}",
        f"crossings {len(result.crossings)}",
    ]
    ax.text(
        0.02,
        0.02,
        " | ".join(info),
        transform=ax.transAxes,
        color=PIC_TEXT,
        fontsize=7,
        ha="left",
        va="bottom",
        bbox={"facecolor": "#050505", "edgecolor": "#666666", "alpha": 0.75, "pad": 4},
        zorder=20,
    )
    fig.tight_layout()
    fig.savefig(OUTDIR / f"task11_bounded_{tag}_partial_failure.png", dpi=180)
    plt.close(fig)


def _draw_failed_net(
    ax: Axes,
    failed: FailedNet,
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    n_physical: int,
    idx: int,
) -> None:
    color = ("#FF4A4A", "#FF9D3D", "#D84CFF")[idx % 3]
    hop = _failed_hop_points(failed.message, cells, rules, n_physical)
    if hop is not None:
        (sx, sy), (dx, dy) = hop
        ax.plot([sx, dx], [sy, dy], color=color, lw=2.0, ls="--", zorder=10)
        ax.scatter([sx], [sy], marker="*", color="#FFD447", s=80, edgecolor="#111111", zorder=12)
        ax.scatter([dx], [dy], marker="x", color=color, s=70, linewidths=2.2, zorder=12)
        ax.text(
            0.5 * (sx + dx),
            0.5 * (sy + dy) + 8.0 + 8.0 * idx,
            f"failed I{failed.input_port}->O{failed.output_port}",
            color=color,
            fontsize=8,
            ha="center",
            zorder=13,
        )

    blocked = _blocked_segment(failed.message)
    if blocked is not None:
        (x1, y1), (x2, y2) = blocked
        ax.plot([x1, x2], [y1, y2], color="#FFFFFF", lw=5.5, alpha=0.20, zorder=11)
        ax.plot([x1, x2], [y1, y2], color=color, lw=1.6, ls=":", zorder=12)
        ax.text(0.5 * (x1 + x2) + 3.0, 0.5 * (y1 + y2) + 3.0, "blocked_by", color=color, fontsize=7, zorder=13)

    point = _blocked_point(failed.message)
    if point is not None:
        ax.scatter([point[0]], [point[1]], marker="D", color="#39B5FF", s=45, zorder=13)
        ax.text(point[0] + 4.0, point[1] + 4.0, "forbidden/history point", color="#9DDCFF", fontsize=7, zorder=13)

    for seg_idx, segment in enumerate(_source_segments(failed.message)):
        (x1, y1), (x2, y2) = segment
        ax.plot([x1, x2], [y1, y2], color="#FFD447", lw=1.4, ls="--", zorder=12)
        if seg_idx == 0:
            ax.text(x1 + 4.0, y1 + 4.0, "source candidate", color="#FFD447", fontsize=7, zorder=13)


def _failed_hop_points(
    message: str,
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    n_physical: int,
) -> tuple[Point, Point] | None:
    match = re.search(r"cannot route (.*?) to (.*?) on physical grid", message)
    if match is None:
        return None
    src = _label_point(match.group(1), cells, rules, n_physical)
    dst = _label_point(match.group(2), cells, rules, n_physical)
    if src is None or dst is None:
        return None
    return src, dst


def _label_point(
    label: str,
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    n_physical: int,
) -> Point | None:
    if label.startswith("I"):
        return (X_START, wire_y(int(label[1:]), n_physical, WIRE_PITCH_UM))
    if label.startswith("O"):
        x_end = max(cell.center[0] for cell in cells.values()) + 70.0
        return (x_end, wire_y(int(label[1:]), n_physical, WIRE_PITCH_UM))
    if "." not in label:
        return None
    mrr_id, port = label.rsplit(".", 1)
    cell = cells.get(mrr_id)
    if cell is None:
        return None
    return _port_escape_point(cell, port, rules, PORT_STUB_UM)


def _blocked_segment(message: str) -> Segment | None:
    match = re.search(
        r"blocked_by=[^:]+:\(([-0-9.]+),([-0-9.]+)\)->\(([-0-9.]+),([-0-9.]+)\)",
        message,
    )
    if match is None:
        match = re.search(
            r"blocked_by=[^:]+ at=\([-0-9.]+,[-0-9.]+\):\(([-0-9.]+),([-0-9.]+)\)->\(([-0-9.]+),([-0-9.]+)\)",
            message,
        )
    if match is None:
        return None
    x1, y1, x2, y2 = (float(value) for value in match.groups())
    return (x1, y1), (x2, y2)


def _blocked_point(message: str) -> Point | None:
    match = re.search(r"blocked_by=[^:]+ at=\(([-0-9.]+),([-0-9.]+)\)", message)
    if match is None:
        return None
    return float(match.group(1)), float(match.group(2))


def _source_segments(message: str) -> list[Segment]:
    source_match = re.search(r"source=[^:]+:(.*)$", message)
    if source_match is None:
        return []
    return [
        ((float(x1), float(y1)), (float(x2), float(y2)))
        for x1, y1, x2, y2 in re.findall(
            r"\(([-0-9.]+),([-0-9.]+)\)->\(([-0-9.]+),([-0-9.]+)\)",
            source_match.group(1),
        )
    ]


def _save_summary_csv(
    density: DensitySummary,
    probes: list[tuple[str, RoutingRules, PhysicalRoutingResult]],
) -> None:
    path = OUTDIR / "task11_probe_summary.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "probe",
                "max_astar_pops",
                "ripup_max_astar_pops",
                "grid_merge_tol_um",
                "nodes",
                "x_tracks",
                "y_tracks",
                "routed",
                "failed",
                "drc",
                "crossings",
                "max_crossings_per_pair",
                "failed_nets",
            ],
        )
        writer.writeheader()
        for tag, rules, result in probes:
            writer.writerow(
                {
                    "probe": tag,
                    "max_astar_pops": rules.max_astar_pops,
                    "ripup_max_astar_pops": rules.ripup_max_astar_pops,
                    "grid_merge_tol_um": rules.grid_merge_tol_um,
                    "nodes": density["nodes"],
                    "x_tracks": density["x_tracks"],
                    "y_tracks": density["y_tracks"],
                    "routed": len(result.routes),
                    "failed": len(result.failed_nets),
                    "drc": len(result.drc_violations),
                    "crossings": len(result.crossings),
                    "max_crossings_per_pair": _max_crossings_per_pair(result),
                    "failed_nets": " ".join(f"I{item.input_port}->O{item.output_port}" for item in result.failed_nets),
                }
            )


def _save_report(
    density: DensitySummary,
    probes: list[tuple[str, RoutingRules, PhysicalRoutingResult]],
) -> None:
    lines = [
        "Task 11 grid sparsification probe",
        f"permutation={PERMUTATION}",
        f"baseline_nodes={density['baseline_nodes']}",
        f"current_nodes={density['nodes']} ({density['node_reduction_pct']:.1f}% reduction)",
        (
            f"x_tracks={density['x_tracks']} y_tracks={density['y_tracks']} "
            f"x_min_gap={density['x_min_gap_um']:.3f} y_min_gap={density['y_min_gap_um']:.3f} "
            f"x_median_gap={density['x_median_gap_um']:.3f} y_median_gap={density['y_median_gap_um']:.3f}"
        ),
        f"subpitch_gaps(<4um): x={density['x_subpitch_gaps']} y={density['y_subpitch_gaps']}",
        "",
    ]
    for tag, rules, result in probes:
        lines.extend(
            [
                f"Probe {tag}",
                f"rules=max_astar_pops={rules.max_astar_pops}, ripup_max_astar_pops={rules.ripup_max_astar_pops}, grid_merge_tol_um={rules.grid_merge_tol_um}",
                (
                    f"routed={len(result.routes)} failed={len(result.failed_nets)} "
                    f"drc={len(result.drc_violations)} crossings={len(result.crossings)} "
                    f"max_crossings_per_pair={_max_crossings_per_pair(result)}"
                ),
            ]
        )
        if result.failed_nets:
            lines.append("failed_nets:")
            for item in result.failed_nets:
                lines.append(f"- I{item.input_port}->O{item.output_port}: {item.message}")
        if result.drc_violations:
            lines.append("drc_violations:")
            for violation in result.drc_violations:
                lines.append(f"- {violation.rule} {violation.net_id}: {violation.message} location={violation.location}")
        lines.append("")
    (OUTDIR / "task11_grid_sparsified_report.txt").write_text("\n".join(lines), encoding="utf-8")


def _max_crossings_per_pair(result: PhysicalRoutingResult) -> int:
    if result.crossing_count_by_pair:
        return max(count for _pair, count in result.crossing_count_by_pair)
    return 0


if __name__ == "__main__":
    main()
