from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.artist import Artist
from matplotlib.axes import Axes
from matplotlib.figure import Figure
from matplotlib.patches import Circle, Rectangle

from ..core.models import DEFAULT_CELL_GEOMETRY, CellGeometry, MRRCell
from ..core.topology import Path as RoutedPath
from ..core.topology import RNBTopology, StateAssignment
from ..placement.layout import build_cells, wire_y
from ..routing.geometry import _inflate_obstacle, _routing_obstacle
from ..routing.fabric import FixedFabricRoutingResult
from ..routing.physical import route_physical_design, route_physical_paths
from ..routing.port_access import _port_stub_point
from ..routing.types import RoutingError, RoutingRules

COLORS = ("#0B6E69", "#D55E00", "#0072B2", "#CC79A7", "#8C6D31", "#4D4D4D")
PIC_BG = "#050505"
PIC_GRID = "#24343F"
PIC_GREEN = "#46D08A"
PIC_GREEN_DARK = "#173C2D"
PIC_YELLOW = "#F1C40F"
PIC_ORANGE = "#E76F2E"
PIC_TEXT = "#EDEDED"
TOPOLOGY_CDF_COLORS = {
    "padded_benes_8x8": "#0B6E69",
    "waksman_6x6": "#D55E00",
    "spanke_benes_6x6": "#0072B2",
}
TOPOLOGY_CDF_ORDER = {
    "padded_benes_8x8": 0,
    "waksman_6x6": 1,
    "spanke_benes_6x6": 2,
}
LAYOUT_CDF_STYLES = {
    "default": "--",
    "sa": "-",
}
LAYOUT_CDF_ORDER = {
    "default": 0,
    "sa": 1,
}


def save_il_sxr_cdf(
    eval_rows: list[dict[str, object]],
    out_path: str | Path,
    sxr_min_db: float = 20.0,
) -> None:
    """Save a 1x2 figure with IL CDF and SXR CDF."""
    grouped: dict[tuple[str, str], dict[str, list[float]]] = {}
    for row in eval_rows:
        topology = str(row["topology"])
        layout = str(row["layout"])
        values = grouped.setdefault((topology, layout), {"il": [], "sxr": []})
        values["il"].append(_float_value(row["insertion_loss_db"]))
        values["sxr"].append(_float_value(row["sxr_db"]))

    fig, (ax_il, ax_sxr) = plt.subplots(1, 2, figsize=(10, 4.5))
    for (topology, layout), values in sorted(grouped.items(), key=_cdf_group_sort_key):
        color = TOPOLOGY_CDF_COLORS.get(topology, "#888888")
        linestyle = LAYOUT_CDF_STYLES.get(layout, "-")
        label = f"{topology} ({layout})"
        il_x, il_y = _cdf_points(values["il"])
        sxr_x, sxr_y = _cdf_points(values["sxr"])
        ax_il.plot(il_x, il_y, color=color, linestyle=linestyle, linewidth=1.8, label=label)
        ax_sxr.plot(sxr_x, sxr_y, color=color, linestyle=linestyle, linewidth=1.8, label=label)

    ax_sxr.axvline(sxr_min_db, color="#4D4D4D", linestyle=":", linewidth=1.2)
    ax_il.set_title("Insertion loss CDF")
    ax_il.set_xlabel("insertion_loss_db")
    ax_il.set_ylabel("CDF")
    ax_sxr.set_title("SXR CDF")
    ax_sxr.set_xlabel("sxr_db")
    ax_sxr.set_ylabel("CDF")
    for ax in (ax_il, ax_sxr):
        ax.set_ylim(0.0, 1.02)
        ax.grid(True, color="#E6E6E6", linewidth=0.8)
    ax_il.legend(fontsize=8, loc="lower right")
    ax_sxr.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def _cdf_points(values: list[float]) -> tuple[list[float], list[float]]:
    sorted_values = sorted(values)
    n_values = len(sorted_values)
    if n_values == 0:
        return [], []
    return sorted_values, [(idx + 1) / n_values for idx in range(n_values)]


def _cdf_group_sort_key(item: tuple[tuple[str, str], dict[str, list[float]]]) -> tuple[int, int, str, str]:
    topology, layout = item[0]
    return (
        TOPOLOGY_CDF_ORDER.get(topology, len(TOPOLOGY_CDF_ORDER)),
        LAYOUT_CDF_ORDER.get(layout, len(LAYOUT_CDF_ORDER)),
        topology,
        layout,
    )


def save_routing_png(
    topology: RNBTopology,
    permutation: tuple[int, ...],
    states: StateAssignment,
    s_table: dict[tuple[str, str, int], float],
    out_path: str | Path,
    *,
    centers: dict[str, tuple[float, float]] | None = None,
    title_suffix: str = "",
    routing_rules: RoutingRules | None = None,
    debug_physical_overlay: bool = False,
    cell_geometry: CellGeometry = DEFAULT_CELL_GEOMETRY,
) -> None:
    paths = topology.get_active_paths(permutation, states)
    cells = build_cells(
        topology,
        s_table,
        centers=centers,
        cell_geometry=cell_geometry,
    )
    fig, ax = _base_figure(
        topology,
        permutation,
        states,
        cells,
        title_suffix,
        routing_rules if debug_physical_overlay else None,
    )
    _draw_paths(
        ax,
        paths,
        cells,
        routing_rules=routing_rules,
        debug_physical_overlay=debug_physical_overlay,
        waveguide_width_um=cell_geometry.waveguide_width_um,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def save_routing_gif(
    topology: RNBTopology,
    permutation: tuple[int, ...],
    states: StateAssignment,
    s_table: dict[tuple[str, str, int], float],
    out_path: str | Path,
    fps: int = 2,
    *,
    centers: dict[str, tuple[float, float]] | None = None,
    title_suffix: str = "",
    routing_rules: RoutingRules | None = None,
    debug_physical_overlay: bool = False,
    cell_geometry: CellGeometry = DEFAULT_CELL_GEOMETRY,
) -> None:
    paths = topology.get_active_paths(permutation, states)
    cells = build_cells(
        topology,
        s_table,
        centers=centers,
        cell_geometry=cell_geometry,
    )
    fig, ax = _base_figure(
        topology,
        permutation,
        states,
        cells,
        title_suffix,
        routing_rules if debug_physical_overlay else None,
    )
    dynamic_artists: list[Artist] = []

    def update(frame: int) -> list[Artist]:
        for artist in dynamic_artists:
            artist.remove()
        dynamic_artists.clear()
        dynamic_artists.extend(
            _draw_paths(
                ax,
                paths,
                cells,
                max_stage=frame - 1,
                routing_rules=routing_rules,
                debug_physical_overlay=debug_physical_overlay,
                waveguide_width_um=cell_geometry.waveguide_width_um,
            )
        )
        suffix = f" {title_suffix}" if title_suffix else ""
        ax.set_title(
            f"{topology.name} add-drop routing{suffix}, stage {frame}/{topology.n_stages}"
        )
        return dynamic_artists

    anim = FuncAnimation(fig, update, frames=topology.n_stages + 1, interval=700, repeat=True)
    anim.save(out_path, writer=PillowWriter(fps=fps))
    plt.close(fig)


def save_fixed_fabric_png(
    topology: RNBTopology,
    cells: dict[str, MRRCell],
    result: FixedFabricRoutingResult,
    out_path: str | Path,
    *,
    wire_pitch_um: float = 64.0,
    x_start_um: float = 20.0,
    x_end_um: float | None = None,
) -> None:
    if x_end_um is None:
        x_end_um = max(cell.center[0] for cell in cells.values()) + 70.0
    fig, ax = _base_figure(
        topology,
        tuple(range(topology.N_logical)),
        {},
        cells,
        "fixed fabric",
        result.rules,
        wire_pitch_um=wire_pitch_um,
        x_start_um=x_start_um,
        x_end_um=x_end_um,
        title=(
            f"{topology.name} immutable fabric "
            f"(logical {topology.N_logical}x{topology.N_logical}, "
            f"physical {topology.N_physical}x{topology.N_physical})"
        ),
        mark_blocked_inputs=True,
    )
    for idx, route in enumerate(result.routes):
        if len(route.waypoints) < 2:
            continue
        color = COLORS[idx % len(COLORS)]
        xs = [point[0] for point in route.waypoints]
        ys = [point[1] for point in route.waypoints]
        ax.plot(xs, ys, color=color, lw=2.3, alpha=0.92, zorder=4)
        ax.scatter(xs[-1], ys[-1], color=color, s=18, zorder=9)
    if result.failed_edges:
        ax.text(
            0.01,
            0.01,
            f"failed fabric edges: {len(result.failed_edges)}",
            transform=ax.transAxes,
            color="#FF6B6B",
            fontsize=8,
            ha="left",
            va="bottom",
        )
    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def _base_figure(
    topology: RNBTopology,
    permutation: tuple[int, ...],
    states: StateAssignment,
    cells: dict[str, MRRCell],
    title_suffix: str,
    debug_rules: RoutingRules | None = None,
    *,
    wire_pitch_um: float = 36.0,
    x_start_um: float = 15.0,
    x_end_um: float | None = None,
    title: str | None = None,
    mark_blocked_inputs: bool = False,
) -> tuple[Figure, Axes]:
    width = max(8.5, 1.45 * topology.n_stages)
    fig, ax = plt.subplots(figsize=(width, 4.8))
    fig.patch.set_facecolor(PIC_BG)
    ax.set_facecolor(PIC_BG)
    y_values = [
        wire_y(w, topology.N_physical, wire_pitch_um)
        for w in range(topology.N_physical)
    ]
    x_min = x_start_um
    x_max = (
        max(cell.center[0] for cell in cells.values()) + 75.0
        if x_end_um is None
        else x_end_um
    )

    for grid_x in range(int(x_min - 40), int(x_max + 70), 12):
        for grid_y in range(int(min(y_values) - 35), int(max(y_values) + 45), 12):
            ax.plot(grid_x, grid_y, marker=".", color=PIC_GRID, markersize=0.8, zorder=0)

    # Port labels only — waveguides are drawn as coloured signal paths below
    for wire, y in enumerate(y_values):
        input_label = (
            f"I{wire} blocked"
            if mark_blocked_inputs and wire >= topology.N_logical
            else f"I{wire}"
        )
        ax.text(x_min - 8, y, input_label, ha="right", va="center", fontsize=8, color=PIC_TEXT)
        label = f"O{wire}" if wire < topology.N_logical else f"O{wire} blocked"
        ax.text(x_max + 8, y, label, ha="left", va="center", fontsize=8, color=PIC_TEXT)

    labeled_stages: set[int] = set()
    for mrr_id, cell in cells.items():
        stage = topology.get_mrr_stage(mrr_id)
        if debug_rules is not None:
            keepout = _inflate_obstacle(_routing_obstacle(cell), debug_rules.mrr_keepout_um)
            ax.add_patch(
                Rectangle(
                    (keepout.left, keepout.bottom),
                    keepout.right - keepout.left,
                    keepout.top - keepout.bottom,
                    facecolor="none",
                    edgecolor="#BDBDBD",
                    linewidth=0.7,
                    linestyle="--",
                    alpha=0.45,
                    zorder=1,
                )
            )
        _draw_basic_mrr_cell(ax, cell)
        if stage not in labeled_stages:
            labeled_stages.add(stage)
            ax.text(cell.center[0], max(y_values) + 20, f"S{stage}", ha="center", fontsize=8, color=PIC_TEXT)

    suffix = f" {title_suffix}" if title_suffix else ""
    ax.set_title(
        title or f"{topology.name} add-drop routing{suffix}: {permutation}",
        color=PIC_TEXT,
    )
    ax.set_xlabel("x (um)", color=PIC_TEXT)
    ax.set_ylabel("y (um)", color=PIC_TEXT)
    left_margin = 90 if mark_blocked_inputs and topology.blocked_ports else 25
    ax.set_xlim(x_min - left_margin, x_max + 55)
    ax.set_ylim(min(y_values) - 30, max(y_values) + 38)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(False)
    ax.tick_params(colors=PIC_TEXT)
    for spine in ax.spines.values():
        spine.set_color(PIC_TEXT)
    return fig, ax


def _draw_basic_mrr_cell(ax: Axes, cell: MRRCell, port_stub_um: float = 2.0) -> None:
    obstacle = _routing_obstacle(cell)
    cx, cy = cell.center
    width = obstacle.right - obstacle.left
    height = obstacle.top - obstacle.bottom

    ax.add_patch(
        Rectangle(
            (obstacle.left, obstacle.bottom),
            width,
            height,
            facecolor="#020403",
            edgecolor=PIC_GREEN_DARK,
            linewidth=1.6,
            zorder=2,
        )
    )

    in_xy = cell.port_xy("in")
    drop_xy = cell.port_xy("drop")
    upper_y = in_xy[1]
    lower_y = drop_xy[1]

    ax.plot([obstacle.left, obstacle.right], [upper_y, upper_y], color=PIC_GREEN, lw=1.8, zorder=4)
    ax.plot([obstacle.left, obstacle.right], [lower_y, lower_y], color=PIC_GREEN, lw=1.8, zorder=4)

    ring_r = min(7.2, max(4.5, 0.20 * abs(upper_y - lower_y)))
    ax.add_patch(Circle((cx, cy), ring_r, facecolor="none", edgecolor=PIC_GREEN, lw=2.0, zorder=5))
    ax.add_patch(Circle((cx, cy), ring_r * 0.78, facecolor="none", edgecolor="#82F0B6", lw=1.0, zorder=5))

    coupling_gap = max(2.0, ring_r * 0.45)
    if upper_y > cy + ring_r + coupling_gap:
        ax.plot([cx, cx], [cy + ring_r, upper_y - coupling_gap],
                color="#9D9D9D", lw=0.8, ls=":", zorder=3)
    if lower_y < cy - ring_r - coupling_gap:
        ax.plot([cx, cx], [lower_y + coupling_gap, cy - ring_r],
                color="#9D9D9D", lw=0.8, ls=":", zorder=3)

    port_size = 5.2
    for port_name in ("in", "drop", "th", "add"):
        stub_x, stub_y = _port_stub_point(cell, port_name, port_stub_um)
        px, py = cell.port_xy(port_name)
        ax.plot([stub_x, px], [stub_y, py], color=PIC_GREEN, lw=1.3, zorder=4)
        ax.add_patch(
            Rectangle(
                (stub_x - port_size / 2.0, stub_y - port_size / 2.0),
                port_size,
                port_size,
                facecolor=PIC_YELLOW,
                edgecolor=PIC_YELLOW,
                linewidth=0.8,
                zorder=8,
            )
        )

    if height <= 45.0:
        ax.text(
            obstacle.left + 2.0,
            obstacle.bottom + 5.0,
            "TunableFilter",
            color=PIC_ORANGE,
            fontsize=4.8,
            ha="left",
            va="center",
            zorder=6,
        )


def _draw_paths(
    ax: Axes,
    paths: list[RoutedPath],
    cells: dict[str, MRRCell],
    max_stage: int | None = None,
    routing_rules: RoutingRules | None = None,
    debug_physical_overlay: bool = False,
    waveguide_width_um: float = DEFAULT_CELL_GEOMETRY.waveguide_width_um,
) -> list[Artist]:
    artists: list[Artist] = []
    if routing_rules is None and not debug_physical_overlay:
        routes = route_physical_paths(
            paths,
            cells,
            max_stage=max_stage,
            waveguide_width_um=waveguide_width_um,
        )
    else:
        rules = routing_rules or RoutingRules()
        result = route_physical_design(paths, cells, rules=rules, max_stage=max_stage)
        if result.failed_nets:
            failed = result.failed_nets[0]
            raise RoutingError(
                f"cannot route I{failed.input_port}->O{failed.output_port}: {failed.message}"
            )
        routes = list(result.routes)
        if debug_physical_overlay:
            for route in routes:
                for segment in route.external_segments:
                    (x1, y1), (x2, y2) = segment
                    overlay = ax.plot(
                        [x1, x2],
                        [y1, y2],
                        color="#FFFFFF",
                        lw=6.0,
                        alpha=0.10,
                        solid_capstyle="butt",
                        zorder=1,
                    )[0]
                    artists.append(overlay)
    for idx, route in enumerate(routes):
        points = route.waypoints
        if len(points) < 2:
            continue
        color = COLORS[idx % len(COLORS)]
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        line = ax.plot(xs, ys, color=color, lw=2.3, alpha=0.92, zorder=4)[0]
        marker = ax.scatter(xs[-1], ys[-1], color=color, s=18, zorder=9)
        artists.extend([line, marker])
    return artists


def _float_value(value: object) -> float:
    if isinstance(value, (int, float, str, bytes, bytearray)):
        return float(value)
    raise TypeError(f"expected numeric value, got {type(value).__name__}")


def _path_points(
    path: RoutedPath,
    cells: dict[str, MRRCell],
    max_stage: int | None,
) -> list[tuple[float, float]]:
    if not path.steps:
        return []
    points = [(20.0, wire_y(path.input_port, _infer_n_physical(cells)))]
    for step in path.steps:
        if max_stage is not None and step.stage > max_stage:
            break
        cell = cells[step.mrr_id]
        points.append(cell.port_xy(step.in_port))
        points.append(cell.port_xy(step.out_port))
    if max_stage is None or max_stage >= path.steps[-1].stage:
        last_x = max(cell.center[0] for cell in cells.values()) + 70.0
        points.append((last_x, wire_y(path.output_port, _infer_n_physical(cells))))
    return points


def _infer_n_physical(cells: dict[str, MRRCell]) -> int:
    max_wire = 0
    for mrr_id in cells:
        tail = mrr_id.rsplit("_w", 1)[1]
        a, b = tail.split("_")
        max_wire = max(max_wire, int(a), int(b))
    return max_wire + 1
