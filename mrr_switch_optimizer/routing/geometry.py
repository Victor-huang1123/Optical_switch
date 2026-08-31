from __future__ import annotations

from math import ceil
from typing import TYPE_CHECKING

from .types import (
    EPS,
    Obstacle,
    OwnedSegment,
    PhysicalRoute,
    Point,
    RoutingError,
    RoutingRules,
    Segment,
)

if TYPE_CHECKING:
    from ..core.models import MRRCell
    from ..core.topology import Path

__all__ = [
    '_inflate_obstacle',
    '_parallel_spacing_violation',
    '_effective_spacing_threshold',
    '_same_net_hairpin_point',
    '_connected_same_net_hairpin_point',
    '_hairpin_bridge_point',
    '_near_route_endpoint',
    '_orthogonal_crossing_point',
    '_segment_crossing_count',
    '_backtrack_penalty',
    '_turn_timing_penalty',
    '_same_net_hairpin_penalty',
    '_segment_contact_point',
    '_segment_hits_forbidden_point',
    '_point_on_segment',
    '_segment_midpoint',
    '_merge_segment_list',
    '_merge_collinear_points',
    '_manhattan',
    '_segments_cross',
    '_candidate_routes',
    '_route_is_available',
    '_append_occupied_axis',
    '_append_points',
    '_axis_segments',
    '_segments_collinear_overlap',
    '_segment_intersects_obstacle',
    '_routing_obstacles',
    '_routing_obstacle',
    '_infer_n_physical',
    '_wire_y',
    '_intervals_overlap',
    '_is_axis_aligned',
    '_same_point',
    '_same_segment',
    '_dedupe_points',
    '_polyline_length',
    '_bend_count',
    '_direction',
    '_canonical_track',
    '_tracks_between_points',
    '_grid_points_on_segment',
    '_shared_endpoint',
    '_route_waypoint_segment_roles',
    '_external_segment_groups',
]

def _inflate_obstacle(obstacle: Obstacle, margin_um: float) -> Obstacle:
    return Obstacle(
        cell_id=obstacle.cell_id,
        left=obstacle.left - margin_um,
        right=obstacle.right + margin_um,
        bottom=obstacle.bottom - margin_um,
        top=obstacle.top + margin_um,
    )

def _effective_spacing_threshold(
    centerline_equivalent_um: float,
    waveguide_width_um: float,
) -> float:
    """Centerline threshold equivalent to an edge-to-edge measurement."""
    return max(0.0, centerline_equivalent_um - waveguide_width_um)


def _parallel_spacing_violation(
    a: Segment,
    b: Segment,
    min_spacing_um: float,
    waveguide_width_um: float = 0.0,
) -> bool:
    threshold = _effective_spacing_threshold(
        min_spacing_um,
        waveguide_width_um,
    )
    if threshold <= EPS:
        return False
    (a1, a2), (b1, b2) = a, b
    a_horizontal = abs(a1[1] - a2[1]) < EPS
    b_horizontal = abs(b1[1] - b2[1]) < EPS
    if a_horizontal != b_horizontal:
        return False
    if a_horizontal:
        if not _intervals_overlap((a1[0], a2[0]), (b1[0], b2[0])):
            return False
        distance = abs(a1[1] - b1[1])
        return EPS < distance < threshold - EPS
    if not _intervals_overlap((a1[1], a2[1]), (b1[1], b2[1])):
        return False
    distance = abs(a1[0] - b1[0])
    return EPS < distance < threshold - EPS

def _same_net_hairpin_point(
    a: Segment,
    b: Segment,
    rules: RoutingRules,
) -> Point | None:
    if _segments_collinear_overlap(a, b):
        return _segment_midpoint(a)
    (a1, a2), (b1, b2) = a, b
    a_horizontal = abs(a1[1] - a2[1]) < EPS
    b_horizontal = abs(b1[1] - b2[1]) < EPS
    if a_horizontal != b_horizontal:
        return None
    max_gap = rules.grid_pitch_um + rules.min_spacing_um + EPS
    if a_horizontal:
        if not _intervals_overlap((a1[0], a2[0]), (b1[0], b2[0])):
            return None
        gap = abs(a1[1] - b1[1])
        if EPS < gap <= max_gap:
            a_lo, a_hi = sorted((a1[0], a2[0]))
            b_lo, b_hi = sorted((b1[0], b2[0]))
            overlap_lo = max(a_lo, b_lo)
            overlap_hi = min(a_hi, b_hi)
            return ((overlap_lo + overlap_hi) * 0.5, (a1[1] + b1[1]) * 0.5)
        return None
    if not _intervals_overlap((a1[1], a2[1]), (b1[1], b2[1])):
        return None
    gap = abs(a1[0] - b1[0])
    if EPS < gap <= max_gap:
        a_lo, a_hi = sorted((a1[1], a2[1]))
        b_lo, b_hi = sorted((b1[1], b2[1]))
        overlap_lo = max(a_lo, b_lo)
        overlap_hi = min(a_hi, b_hi)
        return ((a1[0] + b1[0]) * 0.5, (overlap_lo + overlap_hi) * 0.5)
    return None

def _connected_same_net_hairpin_point(
    first: OwnedSegment,
    second: OwnedSegment,
    external_segments: tuple[Segment, ...],
    rules: RoutingRules,
) -> Point | None:
    rough = _same_net_hairpin_point(first.segment, second.segment, rules)
    if rough is None:
        return None
    start_idx = min(first.index, second.index) + 1
    end_idx = max(first.index, second.index)
    if start_idx >= end_idx:
        return None
    for bridge in external_segments[start_idx:end_idx]:
        bridge_point = _hairpin_bridge_point(first.segment, second.segment, bridge, rules)
        if bridge_point is not None:
            return bridge_point
    return None

def _hairpin_bridge_point(
    a: Segment,
    b: Segment,
    bridge: Segment,
    rules: RoutingRules,
) -> Point | None:
    if not _is_axis_aligned(bridge):
        return None
    a_horizontal = abs(a[0][1] - a[1][1]) < EPS
    bridge_horizontal = abs(bridge[0][1] - bridge[1][1]) < EPS
    if a_horizontal == bridge_horizontal:
        return None
    if _manhattan(bridge[0], bridge[1]) > 2.0 * rules.grid_pitch_um + rules.min_spacing_um + EPS:
        return None
    endpoints = bridge
    first_hits = [point for point in endpoints if _point_on_segment(point, a)]
    second_hits = [point for point in endpoints if _point_on_segment(point, b)]
    if first_hits and second_hits:
        bridge_len = _manhattan(bridge[0], bridge[1])
        min_leg_len = min(_manhattan(a[0], a[1]), _manhattan(b[0], b[1]))
        if bridge_len + EPS < rules.grid_pitch_um:
            return None
        if min_leg_len + EPS < rules.grid_pitch_um:
            return None
        return _segment_midpoint(bridge)
    return None

def _near_route_endpoint(
    point: Point,
    route: PhysicalRoute,
    rules: RoutingRules,
) -> bool:
    if not route.waypoints:
        return False
    guard = rules.port_escape_um + 2.0 * rules.turn_guard_um
    return (
        _manhattan(point, route.waypoints[0]) <= guard + EPS
        or _manhattan(point, route.waypoints[-1]) <= guard + EPS
    )

def _orthogonal_crossing_point(a: Segment, b: Segment) -> Point | None:
    (a1, a2), (b1, b2) = a, b
    a_horizontal = abs(a1[1] - a2[1]) < EPS
    b_horizontal = abs(b1[1] - b2[1]) < EPS
    if a_horizontal == b_horizontal:
        return None
    horizontal = a if a_horizontal else b
    vertical = b if a_horizontal else a
    (h1, h2), (v1, v2) = horizontal, vertical
    x = v1[0]
    y = h1[1]
    h_lo, h_hi = sorted((h1[0], h2[0]))
    v_lo, v_hi = sorted((v1[1], v2[1]))
    if h_lo + EPS < x < h_hi - EPS and v_lo + EPS < y < v_hi - EPS:
        return (x, y)
    return None

def _segment_crossing_count(segment: Segment, blockers: list[Segment]) -> int:
    return sum(
        1
        for blocker in blockers
        if _orthogonal_crossing_point(segment, blocker) is not None
    )

def _backtrack_penalty(
    start: Point,
    end: Point,
    src: Point,
    dst: Point,
    rules: RoutingRules,
) -> float:
    if abs(start[0] - end[0]) >= EPS:
        route_dx = dst[0] - src[0]
        step_dx = end[0] - start[0]
        if abs(route_dx) >= EPS and route_dx * step_dx < -EPS:
            return rules.backtrack_penalty_um
    return 0.0

def _turn_timing_penalty(
    bend_point: Point,
    src: Point,
    dst: Point,
    rules: RoutingRules,
) -> float:
    if rules.turn_guard_um <= EPS:
        return 0.0
    if _manhattan(src, bend_point) < rules.turn_guard_um - EPS:
        return rules.turn_timing_penalty_um
    if _manhattan(bend_point, dst) < rules.turn_guard_um - EPS:
        return rules.turn_timing_penalty_um
    return 0.0

def _same_net_hairpin_penalty(
    segment: Segment,
    current_segments: list[Segment],
    rules: RoutingRules,
) -> float:
    if not current_segments:
        return 0.0
    return rules.hairpin_penalty_um * sum(
        1
        for current in current_segments
        if _same_net_hairpin_point(segment, current, rules) is not None
    )

def _segment_contact_point(a: Segment, b: Segment) -> Point | None:
    if _segments_collinear_overlap(a, b):
        return None
    if _orthogonal_crossing_point(a, b) is not None:
        return None
    contacts: list[Point] = []
    for point in a:
        if _point_on_segment(point, b):
            contacts.append(point)
    for point in b:
        if _point_on_segment(point, a):
            contacts.append(point)
    for contact in contacts:
        if not any(_same_point(contact, seen) for seen in contacts[:contacts.index(contact)]):
            return contact
    return None

def _segment_hits_forbidden_point(
    segment: Segment,
    forbidden_points: set[Point],
    allowed_touch_points: tuple[Point, ...],
) -> bool:
    for point in forbidden_points:
        if any(_same_point(point, allowed) for allowed in allowed_touch_points):
            continue
        if _point_on_segment(point, segment):
            return True
    return False

def _point_on_segment(point: Point, segment: Segment) -> bool:
    start, end = segment
    if not _is_axis_aligned(segment):
        return False
    if abs(start[0] - end[0]) < EPS:
        if abs(point[0] - start[0]) >= EPS:
            return False
        y_lo, y_hi = sorted((start[1], end[1]))
        return y_lo - EPS <= point[1] <= y_hi + EPS
    if abs(point[1] - start[1]) >= EPS:
        return False
    x_lo, x_hi = sorted((start[0], end[0]))
    return x_lo - EPS <= point[0] <= x_hi + EPS

def _segment_midpoint(segment: Segment) -> Point:
    start, end = segment
    return ((start[0] + end[0]) * 0.5, (start[1] + end[1]) * 0.5)

def _merge_segment_list(segments: list[Segment]) -> list[Segment]:
    if not segments:
        return []
    points: list[Point] = [segments[0][0], segments[0][1]]
    for start, end in segments[1:]:
        if _same_point(points[-1], start):
            points.append(end)
        else:
            points.extend((start, end))
    merged_points = _merge_collinear_points(tuple(points))
    return _axis_segments(merged_points)

def _merge_collinear_points(points: tuple[Point, ...]) -> tuple[Point, ...]:
    deduped = _dedupe_points(points)
    if len(deduped) <= 2:
        return deduped
    merged: list[Point] = [deduped[0], deduped[1]]
    for point in deduped[2:]:
        prev = merged[-2]
        current = merged[-1]
        if _is_axis_aligned((prev, current)) and _is_axis_aligned((current, point)):
            if _direction(prev, current) == _direction(current, point):
                merged[-1] = point
                continue
        merged.append(point)
    return tuple(merged)

def _manhattan(a: Point, b: Point) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])

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

def _candidate_routes(
    src: Point,
    dst: Point,
    track_pitch_um: float,
    max_detour_tracks: int,
    obstacles: list[Obstacle],
) -> list[tuple[Point, ...]]:
    candidates: list[tuple[Point, ...]] = []
    if abs(src[0] - dst[0]) < EPS or abs(src[1] - dst[1]) < EPS:
        candidates.append((src, dst))
    else:
        candidates.append((src, (dst[0], src[1]), dst))
        candidates.append((src, (src[0], dst[1]), dst))

    y_bases = [src[1], dst[1], 0.5 * (src[1] + dst[1])]
    x_bases = [src[0], dst[0], 0.5 * (src[0] + dst[0])]
    for obstacle in obstacles:
        y_bases.extend((obstacle.bottom, obstacle.top))
        x_bases.extend((obstacle.left, obstacle.right))
    for track_idx in range(1, max_detour_tracks + 1):
        for sign in (-1.0, 1.0):
            offset = sign * track_idx * track_pitch_um
            for y_track in y_bases:
                detour_y = y_track + offset
                candidates.append((src, (src[0], detour_y), (dst[0], detour_y), dst))
                for src_sign in (-1.0, 1.0):
                    depart_x = src[0] + src_sign * abs(offset)
                    for dst_sign in (-1.0, 1.0):
                        arrive_x = dst[0] + dst_sign * abs(offset)
                        candidates.append(
                            (
                                src,
                                (depart_x, src[1]),
                                (depart_x, detour_y),
                                (arrive_x, detour_y),
                                (arrive_x, dst[1]),
                                dst,
                            )
                        )
            for x_track in x_bases:
                detour_x = x_track + offset
                candidates.append((src, (detour_x, src[1]), (detour_x, dst[1]), dst))

    unique: list[tuple[Point, ...]] = []
    seen: set[tuple[Point, ...]] = set()
    for candidate in candidates:
        cleaned = _dedupe_points(candidate)
        if len(cleaned) < 2 or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    unique.sort(key=lambda pts: (_polyline_length(pts), _bend_count(pts), len(pts), pts))
    return unique

def _route_is_available(
    segments: list[Segment],
    blockers: list[Segment],
    obstacles: list[Obstacle] | None = None,
) -> bool:
    obstacles = obstacles or []
    for idx, segment in enumerate(segments):
        for other in segments[idx + 1 :]:
            if _segments_collinear_overlap(segment, other):
                return False
        for blocker in blockers:
            if _segments_collinear_overlap(segment, blocker):
                return False
        for obstacle in obstacles:
            if _segment_intersects_obstacle(segment, obstacle):
                return False
    return True

def _append_occupied_axis(
    points: list[Point],
    start: Point,
    end: Point,
    occupied: list[Segment],
) -> None:
    if _same_point(start, end):
        return
    segment = (start, end)
    if not _is_axis_aligned(segment):
        raise RoutingError(f"non-Manhattan segment {start} -> {end}")
    if not _route_is_available([segment], occupied):
        raise RoutingError(f"port stub overlaps existing route: {start} -> {end}")
    occupied.append(segment)
    _append_points(points, (start, end))

def _append_points(points: list[Point], new_points: tuple[Point, ...]) -> None:
    for point in new_points:
        if points and _same_point(points[-1], point):
            continue
        points.append(point)

def _axis_segments(points: tuple[Point, ...] | list[Point]) -> list[Segment]:
    segments: list[Segment] = []
    for start, end in zip(points, points[1:]):
        if _same_point(start, end):
            continue
        segment = (start, end)
        if not _is_axis_aligned(segment):
            raise RoutingError(f"non-Manhattan segment {start} -> {end}")
        segments.append(segment)
    return segments

def _segments_collinear_overlap(a: Segment, b: Segment) -> bool:
    (a1, a2), (b1, b2) = a, b
    a_horizontal = abs(a1[1] - a2[1]) < EPS
    b_horizontal = abs(b1[1] - b2[1]) < EPS
    if a_horizontal != b_horizontal:
        return False
    if a_horizontal:
        if abs(a1[1] - b1[1]) >= EPS:
            return False
        return _intervals_overlap((a1[0], a2[0]), (b1[0], b2[0]))
    if abs(a1[0] - b1[0]) >= EPS:
        return False
    return _intervals_overlap((a1[1], a2[1]), (b1[1], b2[1]))

def _segment_intersects_obstacle(segment: Segment, obstacle: Obstacle) -> bool:
    (x1, y1), (x2, y2) = segment
    if abs(y1 - y2) < EPS:
        if not obstacle.bottom + EPS < y1 < obstacle.top - EPS:
            return False
        return _intervals_overlap((x1, x2), (obstacle.left, obstacle.right))
    if abs(x1 - x2) < EPS:
        if not obstacle.left + EPS < x1 < obstacle.right - EPS:
            return False
        return _intervals_overlap((y1, y2), (obstacle.bottom, obstacle.top))
    raise RoutingError(f"non-Manhattan segment {segment[0]} -> {segment[1]}")

def _routing_obstacles(cells: dict[str, MRRCell]) -> list[Obstacle]:
    return [_routing_obstacle(cell) for cell in cells.values()]

def _routing_obstacle(cell: MRRCell) -> Obstacle:
    cx, cy = cell.center
    port_points = [cell.port_xy(port) for port in cell.ports]
    half_width = max(
        0.5 * cell.bbox.width,
        *(abs(px - cx) for px, _py in port_points),
    )
    half_height = max(
        0.5 * cell.bbox.height,
        *(abs(py - cy) for _px, py in port_points),
    )
    return Obstacle(
        cell_id=cell.id,
        left=cx - half_width,
        right=cx + half_width,
        bottom=cy - half_height,
        top=cy + half_height,
    )

def _infer_n_physical(cells: dict[str, MRRCell], paths: list[Path]) -> int:
    max_wire = 0
    for mrr_id in cells:
        tail = mrr_id.rsplit("_w", 1)[1]
        a, b = tail.split("_")
        max_wire = max(max_wire, int(a), int(b))
    for path in paths:
        max_wire = max(max_wire, path.input_port, path.output_port)
    return max_wire + 1

def _wire_y(wire: int, n_physical: int, pitch_um: float) -> float:
    return (n_physical - 1 - wire) * pitch_um

def _intervals_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    a_lo, a_hi = sorted(a)
    b_lo, b_hi = sorted(b)
    return min(a_hi, b_hi) - max(a_lo, b_lo) > EPS

def _is_axis_aligned(segment: Segment) -> bool:
    (x1, y1), (x2, y2) = segment
    return abs(x1 - x2) < EPS or abs(y1 - y2) < EPS

def _same_point(a: Point, b: Point) -> bool:
    return abs(a[0] - b[0]) < EPS and abs(a[1] - b[1]) < EPS

def _same_segment(a: Segment, b: Segment) -> bool:
    return (
        (_same_point(a[0], b[0]) and _same_point(a[1], b[1]))
        or (_same_point(a[0], b[1]) and _same_point(a[1], b[0]))
    )

def _dedupe_points(points: tuple[Point, ...]) -> tuple[Point, ...]:
    cleaned: list[Point] = []
    for point in points:
        if cleaned and _same_point(cleaned[-1], point):
            continue
        cleaned.append(point)
    return tuple(cleaned)

def _polyline_length(points: tuple[Point, ...] | list[Point]) -> float:
    return sum(
        abs(end[0] - start[0]) + abs(end[1] - start[1])
        for start, end in zip(points, points[1:])
    )

def _bend_count(points: tuple[Point, ...] | list[Point]) -> int:
    directions = [
        _direction(start, end)
        for start, end in zip(points, points[1:])
        if not _same_point(start, end)
    ]
    return sum(
        1
        for prev, current in zip(directions, directions[1:])
        if prev != current
    )

def _direction(start: Point, end: Point) -> str:
    if abs(start[0] - end[0]) < EPS:
        return "v"
    if abs(start[1] - end[1]) < EPS:
        return "h"
    raise RoutingError(f"non-Manhattan segment {start} -> {end}")


def _canonical_track(value: float) -> float:
    return round(value, 6)

def _tracks_between_points(
    lower: float,
    upper: float,
    pitch_um: float,
) -> set[float]:
    lo, hi = sorted((lower, upper))
    values = {_canonical_track(lo), _canonical_track(hi)}
    if pitch_um <= EPS:
        return values
    start = ceil(lo / pitch_um) * pitch_um
    count = int((hi - start) // pitch_um) + 1
    for idx in range(max(0, count)):
        value = start + idx * pitch_um
        if lo - EPS <= value <= hi + EPS:
            values.add(_canonical_track(value))
    return values

def _grid_points_on_segment(segment: Segment, pitch_um: float) -> set[Point]:
    start, end = segment
    points = {
        (_canonical_track(start[0]), _canonical_track(start[1])),
        (_canonical_track(end[0]), _canonical_track(end[1])),
    }
    if pitch_um <= EPS:
        return points
    if abs(start[0] - end[0]) < EPS:
        y_lo, y_hi = sorted((start[1], end[1]))
        count = int((y_hi - y_lo) // pitch_um)
        for idx in range(count + 1):
            y = y_lo + idx * pitch_um
            if y_lo - EPS <= y <= y_hi + EPS:
                points.add((_canonical_track(start[0]), _canonical_track(y)))
    elif abs(start[1] - end[1]) < EPS:
        x_lo, x_hi = sorted((start[0], end[0]))
        count = int((x_hi - x_lo) // pitch_um)
        for idx in range(count + 1):
            x = x_lo + idx * pitch_um
            if x_lo - EPS <= x <= x_hi + EPS:
                points.add((_canonical_track(x), _canonical_track(start[1])))
    return points

def _shared_endpoint(a: Segment, b: Segment) -> Point | None:
    for point_a in a:
        for point_b in b:
            if _same_point(point_a, point_b):
                return point_a
    return None

def _route_waypoint_segment_roles(
    route: PhysicalRoute,
) -> list[int | None]:
    roles: list[int | None] = []
    external_idx = 0
    for segment in _axis_segments(route.waypoints):
        if (
            external_idx < len(route.external_segments)
            and _same_segment(segment, route.external_segments[external_idx])
        ):
            roles.append(external_idx)
            external_idx += 1
        else:
            roles.append(None)
    return roles

def _external_segment_groups(
    segment_roles: list[int | None],
) -> list[tuple[int, int, set[int]]]:
    groups: list[tuple[int, int, set[int]]] = []
    idx = 0
    while idx < len(segment_roles):
        role = segment_roles[idx]
        if role is None:
            idx += 1
            continue
        start = idx
        external_indices = {role}
        idx += 1
        while idx < len(segment_roles) and segment_roles[idx] is not None:
            external_indices.add(segment_roles[idx])  # type: ignore[arg-type]
            idx += 1
        groups.append((start, idx, external_indices))
    return groups
