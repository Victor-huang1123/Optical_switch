from __future__ import annotations

from .geometry import _orthogonal_crossing_point
from .types import DRCViolation, FailedNet, PhysicalRoute, RouteCrossing, Segment

__all__ = [
    "_route_crossings",
    "_braid_pair_count",
    "_routing_candidate_score",
    "_route_with_crossing_count",
]


def _net_segments(route: PhysicalRoute) -> tuple[Segment, ...]:
    """Every physical waveguide segment of a net. Inter-net crossings through
    local (stub/escape) segments are as lossy as external ones, so counting
    must cover both kinds or reported IL undercounts real crossings."""
    return route.external_segments + route.local_segments


def _route_crossings(
    routes: tuple[PhysicalRoute, ...] | list[PhysicalRoute],
) -> tuple[RouteCrossing, ...]:
    crossings: list[RouteCrossing] = []
    for idx, route_a in enumerate(routes):
        segments_a = _net_segments(route_a)
        for route_b in routes[idx + 1 :]:
            segments_b = _net_segments(route_b)
            for seg_a in segments_a:
                for seg_b in segments_b:
                    location = _orthogonal_crossing_point(seg_a, seg_b)
                    if location is not None:
                        crossings.append(
                            RouteCrossing(route_a.input_port, route_b.input_port, location)
                        )
    return tuple(crossings)


def _braid_pair_count(
    crossings: tuple[RouteCrossing, ...],
    threshold: int = 2,
) -> int:
    pair_counts: dict[frozenset[int], int] = {}
    for crossing in crossings:
        key = frozenset((crossing.net_a, crossing.net_b))
        pair_counts[key] = pair_counts.get(key, 0) + 1
    return sum(1 for count in pair_counts.values() if count >= threshold)


def _routing_candidate_score(
    routes: tuple[PhysicalRoute, ...] | list[PhysicalRoute],
    violations: tuple[DRCViolation, ...] | list[DRCViolation],
    crossings: tuple[RouteCrossing, ...] | list[RouteCrossing],
    failed: tuple[FailedNet, ...] | list[FailedNet],
) -> tuple[float, ...]:
    return (
        float(len(failed)),
        float(len(violations)),
        float(_braid_pair_count(tuple(crossings))),
        float(len(crossings)),
        sum(route.length_um for route in routes),
        float(sum(route.bend_count for route in routes)),
    )


def _route_with_crossing_count(
    route: PhysicalRoute,
    crossings: tuple[RouteCrossing, ...],
) -> PhysicalRoute:
    crossing_count = sum(
        1
        for crossing in crossings
        if crossing.net_a == route.input_port or crossing.net_b == route.input_port
    )
    return PhysicalRoute(
        input_port=route.input_port,
        output_port=route.output_port,
        waypoints=route.waypoints,
        length_um=route.length_um,
        bend_count=route.bend_count,
        external_segments=route.external_segments,
        local_segments=route.local_segments,
        crossing_count=crossing_count,
    )
