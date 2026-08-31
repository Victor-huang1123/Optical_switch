from __future__ import annotations

from pathlib import Path
import sys

import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def draw_basic_mrr(
    ax: plt.Axes,
    *,
    center: tuple[float, float] = (0.0, 0.0),
    width: float = 100.0,
    height: float = 80.0,
    port_gap: float = 24.0,
    port_stub: float = 18.0,
    ring_radius: float = 18.0,
    label: str = "TunableFilter",
    wavelength_label: str = "resonanceWavelength=155um",
) -> dict[str, tuple[float, float]]:
    """Draw a reusable four-port MRR symbol.

    Returns the four port anchor coordinates so later placement/routing code can
    connect wires to them.
    """
    cx, cy = center
    left_x = cx - width / 2.0
    right_x = cx + width / 2.0
    bottom_y = cy - height / 2.0
    upper_y = cy + port_gap / 2.0
    lower_y = cy - port_gap / 2.0

    green = "#46D08A"
    yellow = "#F1C40F"
    orange = "#E76F2E"
    dark_green = "#183B2D"

    ax.add_patch(
        Rectangle(
            (left_x, bottom_y),
            width,
            height,
            facecolor="#020403",
            edgecolor=dark_green,
            linewidth=2.2,
            zorder=1,
        )
    )

    # Two horizontal coupling buses.
    bus_left = cx - ring_radius * 1.8
    bus_right = cx + ring_radius * 1.8
    ax.plot([bus_left, bus_right], [upper_y, upper_y], color=green, lw=2.0, zorder=4)
    ax.plot([bus_left, bus_right], [lower_y, lower_y], color=green, lw=2.0, zorder=4)

    # Ring resonator, drawn as a double circle to match the reference style.
    ax.add_patch(Circle(center, ring_radius, facecolor="none", edgecolor=green, lw=2.2, zorder=5))
    ax.add_patch(
        Circle(center, ring_radius * 0.78, facecolor="none", edgecolor="#82F0B6", lw=1.2, zorder=5)
    )

    # Vertical dashed coupling hints.
    ax.plot([cx, cx], [upper_y - 3.0, cy + ring_radius], color="#9D9D9D", lw=0.9, ls=":", zorder=3)
    ax.plot([cx, cx], [cy - ring_radius, lower_y + 3.0], color="#9D9D9D", lw=0.9, ls=":", zorder=3)

    # Four yellow optical ports as compact square anchors.
    ports = {
        "in": (left_x - port_stub, upper_y),
        "drop": (left_x - port_stub, lower_y),
        "th": (right_x + port_stub, upper_y),
        "add": (right_x + port_stub, lower_y),
    }
    port_size = 9.0
    for _name, (px, py) in ports.items():
        ax.add_patch(
            Rectangle(
                (px - port_size / 2.0, py - port_size / 2.0),
                port_size,
                port_size,
                facecolor=yellow,
                edgecolor=yellow,
                linewidth=1.0,
                zorder=7,
            )
        )
        if px < cx:
            ax.plot([px + port_size / 2.0, left_x], [py, py], color=green, lw=1.5, zorder=4)
            ax.plot([left_x, bus_left], [py, py], color=green, lw=1.5, zorder=4)
        else:
            ax.plot([bus_right, right_x], [py, py], color=green, lw=1.5, zorder=4)
            ax.plot([right_x, px - port_size / 2.0], [py, py], color=green, lw=1.5, zorder=4)

    ax.text(left_x + 4.0, bottom_y + 11.0, label, color=orange, fontsize=8, ha="left", va="center")
    ax.text(
        left_x + 4.0,
        bottom_y + 1.0,
        wavelength_label,
        color=orange,
        fontsize=6.5,
        ha="left",
        va="center",
    )
    return ports


def save_basic_mrr(out_path: str | Path) -> None:
    fig, ax = plt.subplots(figsize=(4.0, 3.0), dpi=180)
    ax.set_facecolor("#050505")
    fig.patch.set_facecolor("#050505")

    # Dotted PIC-layout style background.
    for x in range(-90, 91, 12):
        for y in range(-70, 86, 12):
            ax.plot(x, y, marker=".", color="#24343F", markersize=1.0, zorder=0)

    draw_basic_mrr(ax)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(-90, 90)
    ax.set_ylim(-70, 85)
    ax.axis("off")
    fig.tight_layout(pad=0)
    fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.04)
    plt.close(fig)


def save_placement_only(out_path: str | Path) -> None:
    from mrr_switch_optimizer.core.sparams import MOCK_S_TABLE
    from mrr_switch_optimizer.core.topology import WaksmanTopology
    from mrr_switch_optimizer.placement.layout import build_cells

    topology = WaksmanTopology()
    cells = build_cells(topology, MOCK_S_TABLE)

    fig, ax = plt.subplots(figsize=(13, 5.5), dpi=180)
    ax.set_facecolor("#050505")
    fig.patch.set_facecolor("#050505")

    x_min = min(cell.center[0] for cell in cells.values()) - 95.0
    x_max = max(cell.center[0] for cell in cells.values()) + 95.0
    y_min = min(cell.center[1] for cell in cells.values()) - 70.0
    y_max = max(cell.center[1] for cell in cells.values()) + 85.0
    for x in range(int(x_min), int(x_max) + 1, 12):
        for y in range(int(y_min), int(y_max) + 1, 12):
            ax.plot(x, y, marker=".", color="#24343F", markersize=0.9, zorder=0)

    labeled_stages: set[int] = set()
    for mrr_id, cell in sorted(cells.items()):
        stage = topology.get_mrr_stage(mrr_id)
        draw_basic_mrr(
            ax,
            center=cell.center,
            width=42.0,
            height=34.0,
            port_gap=16.0,
            port_stub=8.0,
            ring_radius=7.2,
            label="",
            wavelength_label="",
        )
        if stage not in labeled_stages:
            labeled_stages.add(stage)
            ax.text(
                cell.center[0],
                y_max - 12.0,
                f"S{stage}",
                color="#EDEDED",
                fontsize=10,
                ha="center",
                va="center",
            )

    ax.set_title("waksman_6x6 MRR placement only", color="#F2F2F2", fontsize=15)
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.axis("off")
    fig.tight_layout(pad=0.2)
    fig.savefig(out_path, facecolor=fig.get_facecolor(), bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)


if __name__ == "__main__":
    outdir = Path(__file__).resolve().parent
    save_basic_mrr(outdir / "basic_mrr.png")
    save_placement_only(outdir / "basic_mrr_placement_only_waksman.png")
