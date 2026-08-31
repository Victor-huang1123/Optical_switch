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
    "GridNodeOccupancy",
    "RouteGrid",
]


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

    def mark_obstacle(self, obstacle: Obstacle) -> None:
        self.obstacles.append(obstacle)

    def mark_port_access(self, region: PortAccessRegion) -> None:
        self.port_access_regions.append(region)

    def mark_route(self, owner_input: int, segments: tuple[Segment, ...] | list[Segment]) -> None:
        for segment in segments:
            self.waveguides.append(
                GridNodeOccupancy(
                    kind="waveguide",
                    owner_input=owner_input,
                    orientation=_segment_orientation(segment),
                    segment=segment,
                )
            )

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
