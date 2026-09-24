from __future__ import annotations

from dataclasses import dataclass

from .geometry import (
    _manhattan,
    _merge_collinear_points,
    _orthogonal_crossing_point,
    _point_on_segment,
    _same_point,
    _segment_contact_point,
    _segments_collinear_overlap,
)
from .route_grid import GridNodeOccupancy, RouteGrid
from .port_access import port_access_region_conflict
from .types import EPS, PhysicalRoute, Point, RoutingRules, Segment

__all__ = [
    "CrossingCandidate",
    "CrossingRule",
    "crossing_pair_key",
    "crossing_budget_exceeded",
    "endpoint_crossing_candidate",
    "endpoint_crossing_required",
    "legal_crossing_candidate",
    "merged_route_arm_clearances",
]


@dataclass(frozen=True)
class CrossingRule:
    min_clearance_um: float | None = None
    max_crossings_per_move: int = 1
    # A* knows the already-travelled part of the candidate arm but not its
    # future continuation.  It may defer only that forward arm; deterministic
    # whole-polyline candidates retain the strict four-arm check.
    candidate_entry_point: Point | None = None
    defer_candidate_exit: bool = False


@dataclass(frozen=True)
class CrossingCandidate:
    owner_input: int | None
    crossed_input: int | None
    location: Point
    segment: Segment
    crossed_segment: Segment


def merged_route_arm_clearances(
    route: PhysicalRoute,
    location: Point,
) -> tuple[float, float]:
    """Distances from a crossing to the adjacent bend/end on both route arms."""
    points = _merge_collinear_points(route.waypoints)
    matching = [
        (start, end)
        for start, end in zip(points, points[1:])
        if _point_on_segment(location, (start, end))
    ]
    if len(matching) != 1:
        return (0.0, 0.0)
    start, end = matching[0]
    return (_manhattan(location, start), _manhattan(location, end))


def legal_crossing_candidate(
    segment: Segment,
    route_grid: RouteGrid | None,
    rules: RoutingRules,
    owner_input: int | None,
    crossing_rule: CrossingRule | None = None,
    *,
    candidate_arm_segment: Segment | None = None,
) -> CrossingCandidate | None:
    if route_grid is None:
        return None
    crossing_rule = crossing_rule or CrossingRule()
    hits = route_grid.segment_query(segment, rules)
    if _has_port_access_geometric_hit(segment, hits, rules, owner_input):
        return None
    waveguide_hits = [
        hit
        for hit in hits
        if hit.kind == "waveguide"
        and hit.segment is not None
        and _orthogonal_crossing_point(segment, hit.segment) is not None
    ]
    if len(waveguide_hits) != 1:
        return None
    if crossing_rule.max_crossings_per_move != 1:
        return None
    hit = waveguide_hits[0]
    assert hit.segment is not None
    location = _orthogonal_crossing_point(segment, hit.segment)
    if location is None:
        return None
    if hit.owner_input is None or hit.owner_input == owner_input:
        return None
    if _touches_endpoint(location, segment) or _touches_endpoint(location, hit.segment):
        return None
    clearance = crossing_rule.min_clearance_um or 0.0
    candidate_arm = candidate_arm_segment or segment
    if not _candidate_arm_is_legal(location, candidate_arm, crossing_rule):
        return None
    if _clearance_to_segment_endpoints(location, hit.segment) < clearance - EPS:
        return None
    return CrossingCandidate(
        owner_input=owner_input,
        crossed_input=hit.owner_input,
        location=location,
        segment=candidate_arm,
        crossed_segment=hit.segment,
    )


def endpoint_crossing_candidate(
    segment: Segment,
    route_grid: RouteGrid | None,
    rules: RoutingRules,
    owner_input: int | None,
    crossing_rule: CrossingRule | None = None,
    *,
    candidate_arm_segment: Segment | None = None,
) -> CrossingCandidate | None:
    """Crossing candidate whose crossing lies at this move's start point.

    A* advances one grid edge at a time, while final route assembly merges
    collinear points. If a net approaches an occupied waveguide and then leaves
    from that exact grid point, neither individual edge has an interior
    orthogonal crossing, but the merged segment does. Count that on the leaving
    edge only, so one physical crossing consumes one pair-ledger entry.
    """
    if route_grid is None:
        return None
    crossing_rule = crossing_rule or CrossingRule()
    if crossing_rule.max_crossings_per_move != 1:
        return None
    hits = route_grid.segment_query(segment, rules)
    if _has_port_access_geometric_hit(segment, hits, rules, owner_input):
        return None

    candidates: list[CrossingCandidate] = []
    for hit in hits:
        if hit.kind != "waveguide" or hit.segment is None:
            continue
        if hit.owner_input is None or hit.owner_input == owner_input:
            continue
        contact = _segment_contact_point(segment, hit.segment)
        if contact is None:
            continue
        # Count only the edge leaving the crossing point. The approach edge is
        # still only a pending T-contact; charging both would make one legal
        # crossing look like two crossings against the pair ledger.
        if not _same_point(contact, segment[0]):
            continue
        if _same_point(contact, segment[1]):
            continue
        if _touches_endpoint(contact, hit.segment):
            continue
        clearance = crossing_rule.min_clearance_um or 0.0
        if _clearance_to_segment_endpoints(contact, hit.segment) < clearance - EPS:
            continue
        candidate_arm = candidate_arm_segment or segment
        if not _candidate_arm_is_legal(contact, candidate_arm, crossing_rule):
            continue
        candidates.append(
            CrossingCandidate(
                owner_input=owner_input,
                crossed_input=hit.owner_input,
                location=contact,
                segment=candidate_arm,
                crossed_segment=hit.segment,
            )
        )
    if len(candidates) != 1:
        return None
    return candidates[0]


def endpoint_crossing_required(
    segment: Segment,
    route_grid: RouteGrid | None,
    rules: RoutingRules,
    owner_input: int | None,
) -> bool:
    """True when this move starts on a foreign waveguide interior.

    Such a move is the second half of a grid-node crossing after a pending
    endpoint contact. If it cannot be classified as a legal crossing candidate,
    A* must reject it rather than letting final collinear merge turn it into an
    untracked crossing.
    """
    if route_grid is None:
        return False
    for hit in route_grid.segment_query(segment, rules):
        if hit.kind != "waveguide" or hit.segment is None:
            continue
        if hit.owner_input is None or hit.owner_input == owner_input:
            continue
        contact = _segment_contact_point(segment, hit.segment)
        if contact is None:
            continue
        if not _same_point(contact, segment[0]):
            continue
        if _touches_endpoint(contact, hit.segment):
            continue
        return True
    return False


def crossing_pair_key(a: int | None, b: int | None) -> tuple[int, int] | None:
    if a is None or b is None or a == b:
        return None
    low, high = sorted((a, b))
    return low, high


def crossing_budget_exceeded(
    candidate: CrossingCandidate,
    crossing_count_by_pair: dict[tuple[int, int], int],
    *,
    max_crossings_per_pair: int = 1,
) -> bool:
    pair = crossing_pair_key(candidate.owner_input, candidate.crossed_input)
    if pair is None:
        return True
    return crossing_count_by_pair.get(pair, 0) >= max_crossings_per_pair


def _has_port_access_geometric_hit(
    segment: Segment,
    hits: list[GridNodeOccupancy],
    rules: RoutingRules,
    owner_input: int | None,
) -> bool:
    for hit in hits:
        if hit.kind != "port_access" or hit.segment is None:
            continue
        if rules.allow_foreign_outer_runway_transit and hit.region is not None:
            if port_access_region_conflict(
                segment,
                hit.region,
                owner_input,
                rules,
            ) is None:
                continue
        if _segments_collinear_overlap(segment, hit.segment):
            return True
        if _orthogonal_crossing_point(segment, hit.segment) is not None:
            return True
        if _segment_contact_point(segment, hit.segment) is not None:
            return True
    return False


def _touches_endpoint(point: Point, segment: Segment) -> bool:
    return _same_point(point, segment[0]) or _same_point(point, segment[1])


def _clearance_to_segment_endpoints(point: Point, segment: Segment) -> float:
    return min(_manhattan(point, segment[0]), _manhattan(point, segment[1]))


def _candidate_arm_is_legal(
    location: Point,
    segment: Segment,
    crossing_rule: CrossingRule,
) -> bool:
    clearance = crossing_rule.min_clearance_um or 0.0
    if clearance <= EPS:
        return True
    if crossing_rule.candidate_entry_point is None:
        return _clearance_to_segment_endpoints(location, segment) >= clearance - EPS
    entry = crossing_rule.candidate_entry_point
    if not _point_on_segment(entry, segment):
        return False
    if _manhattan(location, entry) < clearance - EPS:
        return False
    if crossing_rule.defer_candidate_exit:
        return True
    exit_point = segment[1] if _same_point(entry, segment[0]) else segment[0]
    return _manhattan(location, exit_point) >= clearance - EPS
