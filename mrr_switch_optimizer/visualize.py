from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.patches import Circle, Rectangle

from .layout import build_cells, wire_y
from .models import MRRCell
from .topology import Path as RoutedPath
from .topology import RNBTopology, StateAssignment

COLORS = ("#0B6E69", "#D55E00", "#0072B2", "#CC79A7", "#8C6D31", "#4D4D4D")
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
        values["il"].append(float(row["insertion_loss_db"]))
        values["sxr"].append(float(row["sxr_db"]))

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
) -> None:
    paths = topology.get_active_paths(permutation, states)
    cells = build_cells(topology, s_table, centers=centers)
    fig, ax = _base_figure(topology, permutation, states, cells, title_suffix)
    _draw_paths(ax, paths, cells)
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
) -> None:
    paths = topology.get_active_paths(permutation, states)
    cells = build_cells(topology, s_table, centers=centers)
    fig, ax = _base_figure(topology, permutation, states, cells, title_suffix)
    dynamic_artists: list[object] = []

    def update(frame: int) -> list[object]:
        for artist in dynamic_artists:
            artist.remove()
        dynamic_artists.clear()
        dynamic_artists.extend(_draw_paths(ax, paths, cells, max_stage=frame - 1))
        suffix = f" {title_suffix}" if title_suffix else ""
        ax.set_title(
            f"{topology.name} add-drop routing{suffix}, stage {frame}/{topology.n_stages}"
        )
        return dynamic_artists

    anim = FuncAnimation(fig, update, frames=topology.n_stages + 1, interval=700, repeat=True)
    anim.save(out_path, writer=PillowWriter(fps=fps))
    plt.close(fig)


def _base_figure(
    topology: RNBTopology,
    permutation: tuple[int, ...],
    states: StateAssignment,
    cells: dict[str, MRRCell],
    title_suffix: str,
) -> tuple[plt.Figure, plt.Axes]:
    width = max(8.5, 1.45 * topology.n_stages)
    fig, ax = plt.subplots(figsize=(width, 4.8))
    y_values = [wire_y(w, topology.N_physical) for w in range(topology.N_physical)]
    x_min = 15.0
    x_max = max(cell.center[0] for cell in cells.values()) + 75.0
    for wire, y in enumerate(y_values):
        color = "#D8D8D8" if wire >= topology.N_logical else "#B7B7B7"
        ax.plot([x_min, x_max], [y, y], color=color, lw=0.8, zorder=0)
        ax.text(x_min - 8, y, f"I{wire}", ha="right", va="center", fontsize=8)
        label = f"O{wire}" if wire < topology.N_logical else "blocked"
        ax.text(x_max + 8, y, label, ha="left", va="center", fontsize=8)

    labeled_stages: set[int] = set()
    for mrr_id, cell in cells.items():
        stage = topology.get_mrr_stage(mrr_id)
        state = states[mrr_id]
        edge = "#C1440E" if state else "#1F6F8B"
        rect = Rectangle(
            (cell.center[0] - cell.bbox.width / 2, cell.center[1] - cell.bbox.height / 2),
            cell.bbox.width,
            cell.bbox.height,
            facecolor="#FFFFFF",
            edgecolor=edge,
            linewidth=1.3,
            zorder=3,
        )
        ax.add_patch(rect)
        ax.add_patch(Circle(cell.center, radius=6.0, fill=False, ec=edge, lw=1.3, zorder=4))
        ax.text(
            cell.center[0],
            cell.center[1],
            "D" if state else "T",
            ha="center",
            va="center",
            fontsize=7,
            color=edge,
            zorder=5,
        )
        if stage not in labeled_stages:
            labeled_stages.add(stage)
            ax.text(cell.center[0], max(y_values) + 20, f"S{stage}", ha="center", fontsize=8)

    suffix = f" {title_suffix}" if title_suffix else ""
    ax.set_title(f"{topology.name} add-drop routing{suffix}: {permutation}")
    ax.set_xlabel("x (um)")
    ax.set_ylabel("wire index, top to bottom")
    ax.set_xlim(x_min - 25, x_max + 55)
    ax.set_ylim(min(y_values) - 30, max(y_values) + 38)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(False)
    return fig, ax


def _draw_paths(
    ax: plt.Axes,
    paths: list[RoutedPath],
    cells: dict[str, MRRCell],
    max_stage: int | None = None,
) -> list[object]:
    artists: list[object] = []
    for idx, path in enumerate(paths):
        points = _path_points(path, cells, max_stage)
        if len(points) < 2:
            continue
        color = COLORS[idx % len(COLORS)]
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        line = ax.plot(xs, ys, color=color, lw=2.3, alpha=0.92, zorder=2)[0]
        marker = ax.scatter(xs[-1], ys[-1], color=color, s=18, zorder=6)
        artists.extend([line, marker])
    return artists


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
