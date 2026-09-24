from __future__ import annotations

from dataclasses import dataclass

from .geometry import (
    _orthogonal_crossing_point,
    _parallel_spacing_violation,
    _same_point,
    _segment_contact_point,
    _segment_intersects_obstacle,
    _segments_collinear_overlap,
)
from .port_access import PortAccessRegion, port_access_conflict, PortAccessLegality
from .types import EPS, Obstacle, Point, RoutingRules, Segment

__all__ = [
    "CrossingFootprint",
    "GridNodeOccupancy",
    "RouteGrid",
    "crossing_footprint_conflict",
]


@dataclass(frozen=True)
class CrossingFootprint:
    location: Point
    side_um: float
    horizontal_owner: int
    vertical_owner: int


@dataclass(frozen=True)
class GridNodeOccupancy:
    kind: str
    owner_input: int | None = None
    orientation: str | None = None
    segment: Segment | None = None
    region: PortAccessRegion | None = None
    obstacle: Obstacle | None = None


class RouteGrid:
    """Lightweight route-occupancy cache for A* legality and crossing checks.

    The current router still uses exact geometry as the final source of truth.
    This cache records the same objects by kind so search policies can ask
    owner/orientation-aware questions without repeatedly rediscovering that
    context from undifferentiated segment lists.
    """

    def __init__(self, x_tracks: tuple[float, ...], y_tracks: tuple[float, ...]):
        self.x_tracks = x_tracks
        self.y_tracks = y_tracks
        self.obstacles: list[Obstacle] = []
        self.port_access_regions: list[PortAccessRegion] = []
        self.waveguides: list[GridNodeOccupancy] = []
        self.crossing_footprints: list[CrossingFootprint] = []

    def mark_obstacle(self, obstacle: Obstacle) -> None:
        self.obstacles.append(obstacle)

    def mark_port_access(self, region: PortAccessRegion) -> None:
        self.port_access_regions.append(region)

    def mark_route(self, owner_input: int, segments: tuple[Segment, ...] | list[Segment]) -> None:
        for segment in _merge_connected_collinear_segments(tuple(segments)):
            self.waveguides.append(
                GridNodeOccupancy(
                    kind="waveguide",
                    owner_input=owner_input,
                    orientation=_segment_orientation(segment),
                    segment=segment,
                )
            )

    def register_crossing_footprints(self, side_um: float) -> None:
        if side_um <= EPS:
            return
        seen: set[tuple[Point, int, int]] = set()
        for index, first in enumerate(self.waveguides):
            if first.segment is None or first.owner_input is None:
                continue
            for second in self.waveguides[index + 1 :]:
                if (
                    second.segment is None
                    or second.owner_input is None
                    or second.owner_input == first.owner_input
                ):
                    continue
                location = _orthogonal_crossing_point(first.segment, second.segment)
                if location is None:
                    continue
                horizontal, vertical = (
                    (first, second) if first.orientation == "H" else (second, first)
                )
                key = (location, horizontal.owner_input, vertical.owner_input)
                if key in seen:
                    continue
                seen.add(key)
                self.crossing_footprints.append(
                    CrossingFootprint(
                        location=location,
                        side_um=side_um,
                        horizontal_owner=horizontal.owner_input,
                        vertical_owner=vertical.owner_input,
                    )
                )

    def crossing_footprint_conflict(
        self,
        segment: Segment,
        owner_input: int | None,
    ) -> CrossingFootprint | None:
        for footprint in self.crossing_footprints:
            if crossing_footprint_conflict(segment, footprint, owner_input):
                return footprint
        return None

    def footprint_contains_point(self, point: Point) -> bool:
        return any(_point_in_footprint(point, footprint) for footprint in self.crossing_footprints)

    def segment_query(self, segment: Segment, rules: RoutingRules | None = None) -> list[GridNodeOccupancy]:
        hits: list[GridNodeOccupancy] = []
        for obstacle in self.obstacles:
            if _segment_intersects_obstacle(segment, obstacle):
                hits.append(GridNodeOccupancy(kind="obstacle", obstacle=obstacle))
        for region in self.port_access_regions:
            if _segment_touches_region(segment, region.segment, rules):
                hits.append(
                    GridNodeOccupancy(
                        kind="port_access",
                        owner_input=region.owner_input,
                        orientation=_segment_orientation(region.segment),
                        segment=region.segment,
                        region=region,
                    )
                )
        for occupancy in self.waveguides:
            if occupancy.segment is not None and _segment_touches_region(
                segment,
                occupancy.segment,
                rules,
            ):
                hits.append(occupancy)
        if not hits:
            return [GridNodeOccupancy(kind="empty")]
        return hits

    def port_access_reason(
        self,
        segment: Segment,
        legality: PortAccessLegality | None,
        rules: RoutingRules,
        *,
        allowed_touch_points: tuple[Point, ...] = (),
    ) -> str | None:
        return port_access_conflict(
            segment,
            legality,
            rules,
            allowed_touch_points=allowed_touch_points,
        )

    def port_access_entry_allowed(self, segment: Segment, owner_input: int | None) -> bool:
        return any(
            region.owner_input is not None
            and region.owner_input == owner_input
            and _segments_collinear_overlap(segment, region.segment)
            for region in self.port_access_regions
        )


def _segment_orientation(segment: Segment) -> str:
    start, end = segment
    if abs(start[0] - end[0]) < EPS:
        return "V"
    if abs(start[1] - end[1]) < EPS:
        return "H"
    return "D"


def _merge_connected_collinear_segments(
    segments: tuple[Segment, ...],
) -> tuple[Segment, ...]:
    merged: list[Segment] = []
    for segment in segments:
        if not merged:
            merged.append(segment)
            continue
        previous = merged[-1]
        if (
            _same_point(previous[1], segment[0])
            and _segment_orientation(previous) == _segment_orientation(segment)
            and _segment_orientation(segment) in {"H", "V"}
        ):
            merged[-1] = (previous[0], segment[1])
        else:
            merged.append(segment)
    return tuple(merged)


def _segment_touches_region(
    segment: Segment,
    other: Segment,
    rules: RoutingRules | None,
) -> bool:
    if _segments_collinear_overlap(segment, other):
        return True
    if _orthogonal_crossing_point(segment, other) is not None:
        return True
    contact = _segment_contact_point(segment, other)
    if contact is not None:
        return True
    if rules is not None and _parallel_spacing_violation(
        segment,
        other,
        rules.min_spacing_um,
        rules.waveguide_width_um,
    ):
        return True
    return any(_same_point(point, other_point) for point in segment for other_point in other)


def crossing_footprint_conflict(
    segment: Segment,
    footprint: CrossingFootprint,
    owner_input: int | None,
) -> bool:
    """Whether a segment illegally enters an inserted crossing component.

    Only the two registered through arms may occupy the square.  In particular,
    a bend, a third net, or the wrong arm of either crossing net is rejected.
    """
    if not _segment_intersects_footprint(segment, footprint):
        return False
    (x0, y0), (x1, y1) = segment
    cx, cy = footprint.location
    horizontal = abs(y0 - y1) < EPS
    if (
        horizontal
        and owner_input == footprint.horizontal_owner
        and abs(y0 - cy) < EPS
    ):
        return False
    if (
        not horizontal
        and abs(x0 - x1) < EPS
        and owner_input == footprint.vertical_owner
        and abs(x0 - cx) < EPS
    ):
        return False
    return True


def _segment_intersects_footprint(
    segment: Segment,
    footprint: CrossingFootprint,
) -> bool:
    half = 0.5 * footprint.side_um
    cx, cy = footprint.location
    left, right = cx - half, cx + half
    bottom, top = cy - half, cy + half
    (x0, y0), (x1, y1) = segment
    if abs(y0 - y1) < EPS:
        lo, hi = sorted((x0, x1))
        return bottom - EPS <= y0 <= top + EPS and hi >= left - EPS and lo <= right + EPS
    if abs(x0 - x1) < EPS:
        lo, hi = sorted((y0, y1))
        return left - EPS <= x0 <= right + EPS and hi >= bottom - EPS and lo <= top + EPS
    return False


def _point_in_footprint(point: Point, footprint: CrossingFootprint) -> bool:
    half = 0.5 * footprint.side_um
    return (
        abs(point[0] - footprint.location[0]) <= half + EPS
        and abs(point[1] - footprint.location[1]) <= half + EPS
    )
