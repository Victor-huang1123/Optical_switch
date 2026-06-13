from __future__ import annotations

from dataclasses import dataclass
from math import pi
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import MRRCell
    from .topology import Path

# Quarter-circle arc length for a 90° bend with minimum bend radius.
# Si photonics single-mode waveguides typically use R_min = 5 μm.
BEND_RADIUS_UM: float = 5.0
BEND_ARC_UM: float = 0.5 * pi * BEND_RADIUS_UM  # ≈ 7.85 μm per 90° bend


@dataclass(frozen=True)
class RoutedSegment:
    waypoints: tuple[tuple[float, float], ...]
    length_um: float
    bend_count: int


def route_l_shape(
    src_xy: tuple[float, float],
    dst_xy: tuple[float, float],
    *,
    horizontal_first: bool = True,
    bend_radius_um: float = BEND_RADIUS_UM,
) -> RoutedSegment:
    """Route src→dst with at most one 90° bend.

    Returns a straight segment when dx=0 or dy=0.
    The extra arc length per bend = π/2 × bend_radius_um.
    """
    x1, y1 = src_xy
    x2, y2 = dst_xy
    dx = abs(x2 - x1)
    dy = abs(y2 - y1)

    if dx < 1e-6 or dy < 1e-6:
        return RoutedSegment(
            waypoints=(src_xy, dst_xy),
            length_um=dx + dy,
            bend_count=0,
        )

    corner: tuple[float, float] = (x2, y1) if horizontal_first else (x1, y2)
    arc = 0.5 * pi * bend_radius_um
    return RoutedSegment(
        waypoints=(src_xy, corner, dst_xy),
        length_um=dx + dy + arc,
        bend_count=1,
    )


def route_path_wiring(
    path: Path,
    cells: dict[str, MRRCell],
    x_start: float = 20.0,
    x_end: float | None = None,
    *,
    bend_radius_um: float = BEND_RADIUS_UM,
) -> tuple[float, int]:
    """Compute routed wiring length and bend count for one path.

    Drop-in replacement for cost._path_wiring() that adds bend penalties.
    Returns (total_length_um, total_bends).
    """
    if not path.steps:
        return 0.0, 0
    if x_end is None:
        x_end = max(c.center[0] for c in cells.values()) + 70.0

    total = 0.0
    bends = 0

    # Input coupler → first MRR in_port (stays on same y bus, no bend)
    p_first = cells[path.steps[0].mrr_id].port_xy(path.steps[0].in_port)
    seg = route_l_shape((x_start, p_first[1]), p_first, bend_radius_um=bend_radius_um)
    total += seg.length_um
    bends += seg.bend_count

    # Inter-MRR segments
    for a, b in zip(path.steps, path.steps[1:]):
        p0 = cells[a.mrr_id].port_xy(a.out_port)
        p1 = cells[b.mrr_id].port_xy(b.in_port)
        seg = route_l_shape(p0, p1, bend_radius_um=bend_radius_um)
        total += seg.length_um
        bends += seg.bend_count

    # Last MRR out_port → output coupler (stays on same y bus, no bend)
    p_last = cells[path.steps[-1].mrr_id].port_xy(path.steps[-1].out_port)
    seg = route_l_shape(p_last, (x_end, p_last[1]), bend_radius_um=bend_radius_um)
    total += seg.length_um
    bends += seg.bend_count

    return total, bends


def count_routed_crossings(
    paths: list[Path],
    cells: dict[str, MRRCell],
) -> int:
    """Count actual geometry crossings between waveguide segments of different paths.

    Uses L-shape routing for each inter-MRR edge, then tests all sub-segment pairs
    from different input ports for intersection.
    """
    indexed: list[tuple[int, tuple[float, float], tuple[float, float]]] = []
    for path in paths:
        for a, b in zip(path.steps, path.steps[1:]):
            p0 = cells[a.mrr_id].port_xy(a.out_port)
            p1 = cells[b.mrr_id].port_xy(b.in_port)
            seg = route_l_shape(p0, p1)
            pts = seg.waypoints
            for k in range(len(pts) - 1):
                indexed.append((path.input_port, pts[k], pts[k + 1]))

    count = 0
    for i, (pa, s1, e1) in enumerate(indexed):
        for pb, s2, e2 in indexed[i + 1 :]:
            if pa != pb and _segments_cross(s1, e1, s2, e2):
                count += 1
    return count


# ─── geometry helpers ─────────────────────────────────────────────────────────


def _segments_cross(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    (x1, y1), (x2, y2) = a1, a2
    (x3, y3), (x4, y4) = b1, b2
    if max(x1, x2) <= min(x3, x4) or max(x3, x4) <= min(x1, x2):
        return False

    def orient(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

    o1 = orient(x1, y1, x2, y2, x3, y3)
    o2 = orient(x1, y1, x2, y2, x4, y4)
    o3 = orient(x3, y3, x4, y4, x1, y1)
    o4 = orient(x3, y3, x4, y4, x2, y2)
    return o1 * o2 < 0.0 and o3 * o4 < 0.0
