from __future__ import annotations

from math import hypot
from typing import TYPE_CHECKING

from .crossing import merged_route_arm_clearances
from .geometry import (
    _axis_segments,
    _dedupe_points,
    _direction,
    _effective_spacing_threshold,
    _external_segment_groups,
    _inflate_obstacle,
    _is_axis_aligned,
    _manhattan,
    _orthogonal_crossing_point,
    _parallel_spacing_violation,
    _route_waypoint_segment_roles,
    _routing_obstacle,
    _segment_contact_point,
    _segment_intersects_obstacle,
    _segment_midpoint,
    _segments_collinear_overlap,
    _shared_endpoint,
)
from .grid import _canonical_track
from .route_grid import CrossingFootprint, crossing_footprint_conflict
from .types import (
    DRCViolation,
    EPS,
    OwnedSegment,
    PhysicalRoute,
    Point,
    RouteCrossing,
    RoutingError,
    RoutingRules,
    Segment,
)

if TYPE_CHECKING:
    from ..core.models import MRRCell

__all__ = [
    'validate_physical_routes',
    '_validate_rules',
    '_dedupe_violations',
    '_same_net_drc_violations',
    '_external_bend_pairs_too_close',
    '_consecutive_bend_pairs_too_close',
]

def validate_physical_routes(
    routes: tuple[PhysicalRoute, ...] | list[PhysicalRoute],
    cells: dict[str, MRRCell],
    rules: RoutingRules,
) -> tuple[DRCViolation, ...]:
    """Check physical routes against v1 waveguide-aware DRC rules."""
    violations: list[DRCViolation] = []
    inflated_obstacles = [
        _inflate_obstacle(_routing_obstacle(cell), rules.mrr_keepout_um)
        for cell in cells.values()
    ]
    segment_items: list[tuple[int, Segment]] = []
    for route in routes:
        for segment in (*route.external_segments, *route.local_segments):
            segment_items.append((route.input_port, segment))
            if not _is_axis_aligned(segment):
                violations.append(
                    DRCViolation(
                        "manhattan",
                        f"I{route.input_port}",
                        "segment is not horizontal or vertical",
                        segment[0],
                    )
                )
        for segment in route.external_segments:
            for obstacle in inflated_obstacles:
                if _segment_intersects_obstacle(segment, obstacle):
                    violations.append(
                        DRCViolation(
                            "mrr_keepout",
                            f"I{route.input_port}",
                            f"segment intersects inflated keepout of {obstacle.cell_id}",
                            _segment_midpoint(segment),
                        )
                    )

    for idx, (net_a, seg_a) in enumerate(segment_items):
        for net_b, seg_b in segment_items[idx + 1 :]:
            if net_a == net_b:
                continue
            if _segments_collinear_overlap(seg_a, seg_b):
                violations.append(
                    DRCViolation(
                        "same_orientation_overlap",
                        f"I{net_a}/I{net_b}",
                        "same-orientation waveguide segments overlap",
                        _segment_midpoint(seg_a),
                    )
                )
            elif _parallel_spacing_violation(
                seg_a,
                seg_b,
                rules.min_spacing_um,
                rules.waveguide_width_um,
            ):
                violations.append(
                    DRCViolation(
                        "min_spacing",
                        f"I{net_a}/I{net_b}",
                        "parallel waveguide edges are closer than the "
                        f"{rules.min_spacing_um:.3f} um centerline-equivalent "
                        "rule",
                        _segment_midpoint(seg_a),
                    )
                )
            elif net_a != net_b and (
                contact := _segment_contact_point(seg_a, seg_b)
            ) is not None:
                violations.append(
                    DRCViolation(
                        "touching_corner",
                        f"I{net_a}/I{net_b}",
                        "waveguides touch at an endpoint or T-junction; only true crossings are allowed",
                        contact,
                    )
                )
            elif not rules.allow_crossings and _orthogonal_crossing_point(seg_a, seg_b):
                violations.append(
                    DRCViolation(
                        "crossing",
                        f"I{net_a}/I{net_b}",
                        "orthogonal crossing is disabled by routing rules",
                        _orthogonal_crossing_point(seg_a, seg_b),
                    )
                )
            if rules.drc_perpendicular_clearance:
                perpendicular = _perpendicular_clearance_violation(
                    seg_a,
                    seg_b,
                    rules.min_spacing_um,
                    rules.waveguide_width_um,
                )
                if perpendicular is not None:
                    distance, location = perpendicular
                    violations.append(
                        DRCViolation(
                            "perpendicular_clearance",
                            f"I{net_a}/I{net_b}",
                            "perpendicular waveguides approach closer than "
                            f"{rules.min_spacing_um:.3f} um centerline-equivalent "
                            "edge clearance without a true crossing "
                            f"({distance:.3f} um)",
                            location,
                        )
                    )

    for route in routes:
        violations.extend(_same_net_drc_violations(route, rules))
        for bend_a, bend_b, distance in _external_bend_pairs_too_close(route, rules):
            violations.append(
                DRCViolation(
                    "min_bend_separation",
                    f"I{route.input_port}",
                    "consecutive external bends are closer than "
                    f"{2.0 * rules.bend_radius_um:.3f} um "
                    f"({distance:.3f} um)",
                    bend_a,
                )
            )
        if rules.drc_same_net_min_spacing:
            violations.extend(_same_net_min_spacing_violations(route, rules))
        if rules.drc_bend_radius_legality:
            for bend_a, bend_b, distance in _consecutive_bend_pairs_too_close(
                route,
                rules,
            ):
                violations.append(
                    DRCViolation(
                        "bend_radius_legality",
                        f"I{route.input_port}",
                        "consecutive bends are closer than "
                        f"{2.0 * rules.bend_radius_um:.3f} um "
                        f"({distance:.3f} um)",
                        bend_a,
                    )
                )

    crossing_clearance = rules.min_crossing_clearance_um
    effective_crossing_clearance = (
        None
        if crossing_clearance is None
        else _effective_spacing_threshold(
            crossing_clearance,
            rules.waveguide_width_um,
        )
    )
    if effective_crossing_clearance is not None and effective_crossing_clearance > EPS:
        route_by_input = {route.input_port: route for route in routes}
        # Crossing-arm and component-footprint rules apply to explicit crossing
        # cells inserted by the external router.  Orthogonal contacts involving
        # an MRR-local/internal segment remain part of the optical crossing-loss
        # model, but they are not standalone crossing components and never pass
        # through ``legal_crossing_candidate``.
        crossings = _external_route_crossings(routes)
        for crossing in crossings:
            arms_a = merged_route_arm_clearances(
                route_by_input[crossing.net_a], crossing.location
            )
            arms_b = merged_route_arm_clearances(
                route_by_input[crossing.net_b], crossing.location
            )
            minimum = min(*arms_a, *arms_b)
            if minimum < effective_crossing_clearance - EPS:
                violations.append(
                    DRCViolation(
                        "crossing_clearance",
                        f"I{crossing.net_a}/I{crossing.net_b}",
                        "crossing arms do not provide the required straight "
                        f"edge clearance ({crossing_clearance:.3f} um "
                        "centerline-equivalent; effective centerline threshold="
                        f"{effective_crossing_clearance:.3f} um) "
                        f"(arms={arms_a[0]:.3f},{arms_a[1]:.3f},"
                        f"{arms_b[0]:.3f},{arms_b[1]:.3f} um; "
                        f"minimum={minimum:.3f} um)",
                        crossing.location,
                    )
                )
        violations.extend(
            _crossing_footprint_violations(
                routes,
                crossings,
                crossing_clearance,
            )
        )

    return _dedupe_violations(violations)


def _external_route_crossings(
    routes: tuple[PhysicalRoute, ...] | list[PhysicalRoute],
) -> tuple[RouteCrossing, ...]:
    crossings: list[RouteCrossing] = []
    seen: set[tuple[int, int, Point]] = set()
    for index, first in enumerate(routes):
        for second in routes[index + 1 :]:
            low, high = sorted((first.input_port, second.input_port))
            for first_segment in first.external_segments:
                for second_segment in second.external_segments:
                    location = _orthogonal_crossing_point(
                        first_segment,
                        second_segment,
                    )
                    if location is None:
                        continue
                    key = (low, high, location)
                    if key in seen:
                        continue
                    seen.add(key)
                    crossings.append(RouteCrossing(low, high, location))
    return tuple(crossings)


def _crossing_footprint_violations(
    routes: tuple[PhysicalRoute, ...] | list[PhysicalRoute],
    crossings: tuple[RouteCrossing, ...],
    side_um: float,
) -> list[DRCViolation]:
    route_by_input = {route.input_port: route for route in routes}
    violations: list[DRCViolation] = []
    for crossing in crossings:
        net_a = crossing.net_a
        net_b = crossing.net_b
        location = crossing.location
        horizontal_owner = _crossing_horizontal_owner(
            route_by_input[net_a],
            route_by_input[net_b],
            location,
        )
        vertical_owner = net_b if horizontal_owner == net_a else net_a
        footprint = CrossingFootprint(
            location=location,
            side_um=side_um,
            horizontal_owner=horizontal_owner,
            vertical_owner=vertical_owner,
        )
        for route in routes:
            for segment in (*route.external_segments, *route.local_segments):
                if not crossing_footprint_conflict(
                    segment,
                    footprint,
                    route.input_port,
                ):
                    continue
                violations.append(
                    DRCViolation(
                        "crossing_footprint",
                        f"I{route.input_port}",
                        "waveguide segment or bend enters the ownerless crossing "
                        f"footprint (side={side_um:.3f} um; crossing="
                        f"I{net_a}/I{net_b})",
                        location,
                    )
                )
    return violations


def _crossing_horizontal_owner(
    first: PhysicalRoute,
    second: PhysicalRoute,
    location: Point,
) -> int:
    for route in (first, second):
        for segment in (*route.external_segments, *route.local_segments):
            if (
                abs(segment[0][1] - segment[1][1]) < EPS
                and min(segment[0][0], segment[1][0]) - EPS <= location[0]
                <= max(segment[0][0], segment[1][0]) + EPS
                and abs(segment[0][1] - location[1]) < EPS
            ):
                return route.input_port
    return first.input_port

def _validate_rules(rules: RoutingRules) -> None:
    if rules.grid_pitch_um <= 0.0:
        raise ValueError("grid_pitch_um must be positive")
    if rules.grid_merge_tol_um < 0.0:
        raise ValueError("grid_merge_tol_um must be non-negative")
    if (
        rules.grid_merge_tol_um > 0.0
        and rules.min_spacing_um > 0.0
        and _effective_spacing_threshold(
            rules.min_spacing_um,
            rules.waveguide_width_um,
        )
        > 0.0
        and rules.grid_merge_tol_um
        >= _effective_spacing_threshold(
            rules.min_spacing_um,
            rules.waveguide_width_um,
        )
    ):
        raise ValueError(
            "grid_merge_tol_um must be smaller than the effective "
            "edge-to-edge spacing threshold"
        )
    if rules.min_spacing_um < 0.0:
        raise ValueError("min_spacing_um must be non-negative")
    if rules.waveguide_width_um < 0.0:
        raise ValueError("waveguide_width_um must be non-negative")
    if rules.mrr_keepout_um < 0.0:
        raise ValueError("mrr_keepout_um must be non-negative")
    if rules.port_escape_um < 0.0:
        raise ValueError("port_escape_um must be non-negative")
    if rules.port_access_runway_um < 0.0:
        raise ValueError("port_access_runway_um must be non-negative")
    if rules.port_access_stagger_tracks < 0:
        raise ValueError("port_access_stagger_tracks must be non-negative")
    if rules.bend_radius_um < 0.0:
        raise ValueError("bend_radius_um must be non-negative")
    if rules.crossing_penalty_um < 0.0:
        raise ValueError("crossing_penalty_um must be non-negative")
    if rules.repeated_crossing_penalty_um < 0.0:
        raise ValueError("repeated_crossing_penalty_um must be non-negative")
    if rules.outward_repeated_crossing_scale < 0.0:
        raise ValueError("outward_repeated_crossing_scale must be non-negative")
    if rules.min_crossing_clearance_um is not None and rules.min_crossing_clearance_um < 0.0:
        raise ValueError("min_crossing_clearance_um must be non-negative")
    if rules.turn_guard_um < 0.0:
        raise ValueError("turn_guard_um must be non-negative")
    if rules.turn_timing_penalty_um < 0.0:
        raise ValueError("turn_timing_penalty_um must be non-negative")
    if rules.backtrack_penalty_um < 0.0:
        raise ValueError("backtrack_penalty_um must be non-negative")
    if rules.hairpin_penalty_um < 0.0:
        raise ValueError("hairpin_penalty_um must be non-negative")
    if rules.local_repair_max_shift_tracks < 0:
        raise ValueError("local_repair_max_shift_tracks must be non-negative")
    if rules.route_window_max_detour_tracks < 0:
        raise ValueError("route_window_max_detour_tracks must be non-negative")
    if rules.grid_margin_tracks < 0.0:
        raise ValueError("grid_margin_tracks must be non-negative")
    if rules.max_ripup_passes < 0:
        raise ValueError("max_ripup_passes must be non-negative")
    if rules.early_stop_stagnant_passes < 0:
        raise ValueError("early_stop_stagnant_passes must be non-negative")
    if rules.ripup_route_budget is not None and rules.ripup_route_budget <= 0:
        raise ValueError("ripup_route_budget must be positive")
    if rules.ripup_order_candidate_limit <= 0:
        raise ValueError("ripup_order_candidate_limit must be positive")
    if rules.fallback_beam_width < 0:
        raise ValueError("fallback_beam_width must be non-negative")
    if rules.fallback_alternatives_per_hop < 0:
        raise ValueError("fallback_alternatives_per_hop must be non-negative")
    if rules.route_order_beam_width < 0:
        raise ValueError("route_order_beam_width must be non-negative")
    if rules.route_order_beam_max_astar_pops is not None and rules.route_order_beam_max_astar_pops <= 0:
        raise ValueError("route_order_beam_max_astar_pops must be positive")
    if rules.astar_heuristic_weight < 1.0:
        raise ValueError("astar_heuristic_weight must be at least 1.0")
    if rules.cost_model not in {"um_penalty", "db"}:
        raise ValueError("cost_model must be 'um_penalty' or 'db'")
    if rules.db_tie_breaker_scale < 0.0:
        raise ValueError("db_tie_breaker_scale must be non-negative")
    if rules.corridor_guide_mode not in {"off", "soft", "hard"}:
        raise ValueError("corridor_guide_mode must be 'off', 'soft', or 'hard'")
    if rules.prop_loss_db_per_um < 0.0:
        raise ValueError("prop_loss_db_per_um must be non-negative")
    if rules.cost_model == "db" and rules.prop_loss_db_per_um <= 0.0:
        raise ValueError("db cost_model requires positive prop_loss_db_per_um")
    if rules.bend_loss_db_per_bend < 0.0:
        raise ValueError("bend_loss_db_per_bend must be non-negative")
    if rules.crossing_loss_db_per_cross < 0.0:
        raise ValueError("crossing_loss_db_per_cross must be non-negative")
    if rules.repeated_crossing_loss_db < 0.0:
        raise ValueError("repeated_crossing_loss_db must be non-negative")
    if rules.jog_penalty_db < 0.0:
        raise ValueError("jog_penalty_db must be non-negative")
    if rules.bend_placement_penalty_db < 0.0:
        raise ValueError("bend_placement_penalty_db must be non-negative")
    if rules.max_astar_pops is not None and rules.max_astar_pops <= 0:
        raise ValueError("max_astar_pops must be positive")
    if rules.ripup_max_astar_pops is not None and rules.ripup_max_astar_pops <= 0:
        raise ValueError("ripup_max_astar_pops must be positive")

def _dedupe_violations(violations: list[DRCViolation]) -> tuple[DRCViolation, ...]:
    deduped: list[DRCViolation] = []
    seen: set[tuple[str, str, str, Point | None]] = set()
    for violation in violations:
        location = (
            (round(violation.location[0], 6), round(violation.location[1], 6))
            if violation.location is not None
            else None
        )
        key = (violation.rule, violation.net_id, violation.message, location)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(violation)
    return tuple(deduped)

def _same_net_drc_violations(
    route: PhysicalRoute,
    rules: RoutingRules,
) -> list[DRCViolation]:
    violations: list[DRCViolation] = []
    owned = [
        OwnedSegment(route.input_port, segment, "external", idx)
        for idx, segment in enumerate(route.external_segments)
    ]
    for idx, first in enumerate(owned):
        for second in owned[idx + 1 :]:
            if abs(first.index - second.index) <= 1:
                continue
            contact = _segment_contact_point(first.segment, second.segment)
            if contact is not None:
                violations.append(
                    DRCViolation(
                        "same_net_touching_corner",
                        f"I{route.input_port}",
                        "same net touches itself at a bend/end point",
                        contact,
                    )
                )
    return violations


def _same_net_min_spacing_violations(
    route: PhysicalRoute,
    rules: RoutingRules,
) -> list[DRCViolation]:
    threshold = _effective_spacing_threshold(
        rules.min_spacing_um,
        rules.waveguide_width_um,
    )
    if threshold <= EPS:
        return []
    segments = _axis_segments(route.waypoints)
    violations: list[DRCViolation] = []
    for first_idx, first in enumerate(segments):
        for second_idx in range(first_idx + 2, len(segments)):
            second = segments[second_idx]
            if _shared_endpoint(first, second) is not None:
                continue
            distance, location = _axis_segment_clearance(first, second)
            if distance > threshold + EPS:
                continue
            violations.append(
                DRCViolation(
                    "same_net_min_spacing",
                    f"I{route.input_port}",
                    "non-adjacent same-net segments do not exceed the required "
                    f"{rules.min_spacing_um:.3f} um centerline-equivalent "
                    f"edge spacing (effective threshold={threshold:.3f} um; "
                    f"measured={distance:.3f} um; "
                    f"segments {first_idx}/{second_idx})",
                    location,
                )
            )
    return violations


def _perpendicular_clearance_violation(
    first: Segment,
    second: Segment,
    min_spacing_um: float,
    waveguide_width_um: float = 0.0,
) -> tuple[float, Point] | None:
    threshold = _effective_spacing_threshold(
        min_spacing_um,
        waveguide_width_um,
    )
    if threshold <= EPS:
        return None
    first_horizontal = abs(first[0][1] - first[1][1]) < EPS
    second_horizontal = abs(second[0][1] - second[1][1]) < EPS
    if first_horizontal == second_horizontal:
        return None
    if _orthogonal_crossing_point(first, second) is not None:
        return None
    distance, location = _axis_segment_clearance(first, second)
    if distance >= threshold - EPS:
        return None
    return distance, location


def _axis_segment_clearance(first: Segment, second: Segment) -> tuple[float, Point]:
    first_horizontal = abs(first[0][1] - first[1][1]) < EPS
    second_horizontal = abs(second[0][1] - second[1][1]) < EPS
    if first_horizontal and not second_horizontal:
        horizontal, vertical = first, second
    elif second_horizontal and not first_horizontal:
        horizontal, vertical = second, first
    elif first_horizontal:
        first_lo, first_hi = sorted((first[0][0], first[1][0]))
        second_lo, second_hi = sorted((second[0][0], second[1][0]))
        first_x = min(max(second_lo, first_lo), first_hi)
        second_x = min(max(first_x, second_lo), second_hi)
        first_point = (first_x, first[0][1])
        second_point = (second_x, second[0][1])
        return (
            hypot(first_point[0] - second_point[0], first_point[1] - second_point[1]),
            _midpoint(first_point, second_point),
        )
    else:
        first_lo, first_hi = sorted((first[0][1], first[1][1]))
        second_lo, second_hi = sorted((second[0][1], second[1][1]))
        first_y = min(max(second_lo, first_lo), first_hi)
        second_y = min(max(first_y, second_lo), second_hi)
        first_point = (first[0][0], first_y)
        second_point = (second[0][0], second_y)
        return (
            hypot(first_point[0] - second_point[0], first_point[1] - second_point[1]),
            _midpoint(first_point, second_point),
        )

    horizontal_lo, horizontal_hi = sorted((horizontal[0][0], horizontal[1][0]))
    vertical_lo, vertical_hi = sorted((vertical[0][1], vertical[1][1]))
    horizontal_point = (
        min(max(vertical[0][0], horizontal_lo), horizontal_hi),
        horizontal[0][1],
    )
    vertical_point = (
        vertical[0][0],
        min(max(horizontal[0][1], vertical_lo), vertical_hi),
    )
    return (
        hypot(
            horizontal_point[0] - vertical_point[0],
            horizontal_point[1] - vertical_point[1],
        ),
        _midpoint(horizontal_point, vertical_point),
    )


def _midpoint(first: Point, second: Point) -> Point:
    return ((first[0] + second[0]) * 0.5, (first[1] + second[1]) * 0.5)

def _external_bend_pairs_too_close(
    route: PhysicalRoute,
    rules: RoutingRules,
) -> list[tuple[Point, Point, float]]:
    min_separation = 2.0 * rules.bend_radius_um
    if min_separation <= EPS or len(route.waypoints) < 3:
        return []

    local_endpoints = {
        (_canonical_track(point[0]), _canonical_track(point[1]))
        for segment in route.local_segments
        for point in segment
    }
    try:
        external_groups = _external_segment_groups(_route_waypoint_segment_roles(route))
    except RoutingError:
        return []

    close_pairs: list[tuple[Point, Point, float]] = []
    for start_idx, end_idx, _external_indices in external_groups:
        group_points = route.waypoints[start_idx : end_idx + 1]
        bends: list[Point] = []
        for prev, point, nxt in zip(group_points, group_points[1:], group_points[2:]):
            if not _is_axis_aligned((prev, point)) or not _is_axis_aligned((point, nxt)):
                continue
            prev_horizontal = abs(prev[1] - point[1]) < EPS
            next_horizontal = abs(point[1] - nxt[1]) < EPS
            if prev_horizontal == next_horizontal:
                continue
            bend = (_canonical_track(point[0]), _canonical_track(point[1]))
            if bend in local_endpoints:
                continue
            bends.append(bend)

        for bend_a, bend_b in zip(bends, bends[1:]):
            distance = _manhattan(bend_a, bend_b)
            if distance < min_separation - EPS:
                close_pairs.append((bend_a, bend_b, distance))

    return close_pairs


def _consecutive_bend_pairs_too_close(
    route: PhysicalRoute,
    rules: RoutingRules,
) -> list[tuple[Point, Point, float]]:
    min_separation = 2.0 * rules.bend_radius_um
    if min_separation <= EPS:
        return []

    points = _dedupe_points(route.waypoints)
    bends = [
        point
        for previous, point, following in zip(points, points[1:], points[2:])
        if _direction(previous, point) != _direction(point, following)
    ]
    return [
        (first, second, distance)
        for first, second in zip(bends, bends[1:])
        if (distance := _manhattan(first, second)) < min_separation - EPS
    ]
