from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from math import pi

from ..core.models import MRRCell
from .drc import validate_physical_routes
from .fabric import (
    FabricCrossing,
    FabricRoute,
    FixedFabricRoutingResult,
    _map_physical_drc,
)
from .geometry import (
    _axis_segments,
    _bend_count,
    _dedupe_points,
    _external_segment_groups,
    _inflate_obstacle,
    _polyline_length,
    _route_waypoint_segment_roles,
    _routing_obstacle,
    _same_point,
)
from .grid import _route_segment_available
from .port_access import PortAccessLegality, PortAccessPlan
from .refinement import _route_crossings, _route_with_crossing_count
from .types import (
    EPS,
    DRCViolation,
    PhysicalRoute,
    Point,
    RoutingRules,
    Segment,
    StraightenStats,
)


@dataclass(frozen=True)
class StraightenResult:
    routing: FixedFabricRoutingResult
    stats: StraightenStats


@dataclass(frozen=True)
class _RewriteCandidate:
    route_index: int
    start_idx: int
    end_idx: int
    route: PhysicalRoute
    routes: tuple[PhysicalRoute, ...]
    crossings: tuple[object, ...]
    loss_proxy_db: float
    kind: str


@dataclass(frozen=True)
class _SlideCandidate:
    route_index: int
    first_bend_idx: int
    delta_um: float
    routes: tuple[PhysicalRoute, ...]
    crossings: tuple[object, ...]
    violations: tuple[DRCViolation, ...]
    loss_proxy_db: float


def _turn_sign(previous: Point, corner: Point, following: Point) -> int:
    first = (corner[0] - previous[0], corner[1] - previous[1])
    second = (following[0] - corner[0], following[1] - corner[1])
    cross = first[0] * second[1] - first[1] * second[0]
    return 1 if cross > EPS else -1 if cross < -EPS else 0


def _turn_signs(points: tuple[Point, ...]) -> tuple[int, ...]:
    return tuple(
        sign
        for previous, corner, following in zip(points, points[1:], points[2:])
        if (sign := _turn_sign(previous, corner, following)) != 0
    )


def _is_monotone_staircase(points: tuple[Point, ...]) -> bool:
    def monotone(values: tuple[float, ...]) -> bool:
        deltas = [second - first for first, second in zip(values, values[1:])]
        nonzero = [delta for delta in deltas if abs(delta) >= EPS]
        return not nonzero or all(delta > 0.0 for delta in nonzero) or all(
            delta < 0.0 for delta in nonzero
        )

    return monotone(tuple(point[0] for point in points)) and monotone(
        tuple(point[1] for point in points)
    )


def _rewrite_kind(points: tuple[Point, ...]) -> str | None:
    signs = _turn_signs(points)
    if len(signs) < 3:
        return None
    if any(
        first == third and first == -second
        for first, second, third in zip(signs, signs[1:], signs[2:])
    ):
        return "alternating_turn_triple"
    if len(signs) >= 4 and _is_monotone_staircase(points):
        return "monotone_staircase"
    return None


def _l_variants(start: Point, end: Point) -> tuple[tuple[Point, ...], ...]:
    if abs(start[0] - end[0]) < EPS or abs(start[1] - end[1]) < EPS:
        return ((start, end),)
    candidates = (
        (start, (end[0], start[1]), end),
        (start, (start[0], end[1]), end),
    )
    unique: list[tuple[Point, ...]] = []
    for candidate in candidates:
        cleaned = _dedupe_points(candidate)
        if cleaned not in unique:
            unique.append(cleaned)
    return tuple(unique)


def _protected_waypoints(route: FabricRoute) -> frozenset[Point]:
    return frozenset(
        point
        for edge_route in route.edge_routes
        for point in (edge_route.waypoints[0], edge_route.waypoints[-1])
    )


def _candidate_windows(
    route: PhysicalRoute,
    protected: frozenset[Point],
) -> tuple[tuple[int, int, str], ...]:
    roles = _route_waypoint_segment_roles(route)
    windows: list[tuple[int, int, str]] = []
    for group_start, group_end, _external_indices in _external_segment_groups(roles):
        for start_idx in range(group_start, group_end - 2):
            for end_idx in range(start_idx + 4, group_end + 1):
                if any(
                    route.waypoints[idx] in protected
                    for idx in range(start_idx + 1, end_idx)
                ):
                    continue
                points = route.waypoints[start_idx : end_idx + 1]
                kind = _rewrite_kind(points)
                if kind is not None:
                    windows.append((start_idx, end_idx, kind))
    return tuple(windows)


def _splice_external_route(
    route: PhysicalRoute,
    start_idx: int,
    end_idx: int,
    replacement: tuple[Point, ...],
    rules: RoutingRules,
) -> PhysicalRoute:
    roles = _route_waypoint_segment_roles(route)
    if any(role is None for role in roles[start_idx:end_idx]):
        raise ValueError("straightening span includes a local segment")
    waypoints = (
        route.waypoints[: start_idx + 1]
        + replacement[1:-1]
        + route.waypoints[end_idx:]
    )
    waypoints = _dedupe_points(waypoints)
    prefix_external = tuple(
        segment
        for segment, role in zip(_axis_segments(route.waypoints), roles)
        if role is not None and role < roles[start_idx]  # type: ignore[operator]
    )
    last_role = roles[end_idx - 1]
    suffix_external = tuple(
        segment
        for segment, role in zip(_axis_segments(route.waypoints), roles)
        if role is not None and role > last_role  # type: ignore[operator]
    )
    replacement_segments = tuple(_axis_segments(replacement))
    bends = _bend_count(waypoints)
    return PhysicalRoute(
        input_port=route.input_port,
        output_port=route.output_port,
        waypoints=waypoints,
        length_um=(
            _polyline_length(waypoints)
            + bends * 0.5 * pi * rules.bend_radius_um
        ),
        bend_count=bends,
        external_segments=prefix_external + replacement_segments + suffix_external,
        local_segments=route.local_segments,
        crossing_count=route.crossing_count,
    )


def _slide_bend_pair_route(
    route: PhysicalRoute,
    first_bend_idx: int,
    delta_um: float,
    rules: RoutingRules,
) -> PhysicalRoute:
    points = list(route.waypoints)
    first = points[first_bend_idx]
    second = points[first_bend_idx + 1]
    before = points[first_bend_idx - 1]
    after = points[first_bend_idx + 2]
    outer_horizontal = abs(before[1] - first[1]) < EPS
    if outer_horizontal:
        points[first_bend_idx] = (first[0] + delta_um, first[1])
        points[first_bend_idx + 1] = (second[0] + delta_um, second[1])
    else:
        points[first_bend_idx] = (first[0], first[1] + delta_um)
        points[first_bend_idx + 1] = (second[0], second[1] + delta_um)
    waypoints = tuple(points)
    if any(_same_point(start, end) for start, end in zip(waypoints, waypoints[1:])):
        raise ValueError("bend slide collapses a segment")
    roles = _route_waypoint_segment_roles(route)
    segments = tuple(_axis_segments(waypoints))
    external_segments = tuple(
        segment for segment, role in zip(segments, roles) if role is not None
    )
    bends = _bend_count(waypoints)
    return PhysicalRoute(
        input_port=route.input_port,
        output_port=route.output_port,
        waypoints=waypoints,
        length_um=_polyline_length(waypoints) + bends * 0.5 * pi * rules.bend_radius_um,
        bend_count=bends,
        external_segments=external_segments,
        local_segments=route.local_segments,
        crossing_count=route.crossing_count,
    )


def _drc_rule_counts(violations: tuple[DRCViolation, ...]) -> Counter[str]:
    return Counter(violation.rule for violation in violations)


def _best_bend_slide(
    routes: tuple[PhysicalRoute, ...],
    protected_by_input: dict[int, frozenset[Point]],
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    loss_ceiling: float,
    current_violations: tuple[DRCViolation, ...],
    port_access_plan: PortAccessPlan | None,
) -> tuple[_SlideCandidate | None, int, int]:
    current_counts = _drc_rule_counts(current_violations)
    current_arm_count = current_counts["crossing_clearance"]
    if current_arm_count == 0:
        return None, 0, 0
    best: _SlideCandidate | None = None
    candidates = 0
    legal_candidates = 0
    deltas = tuple(
        sign * rung * rules.grid_pitch_um
        for rung in range(1, rules.local_repair_max_shift_tracks + 1)
        for sign in (1.0, -1.0)
    )
    for route_index in sorted(
        range(len(routes)), key=lambda index: routes[index].input_port
    ):
        route = routes[route_index]
        points = route.waypoints
        roles = _route_waypoint_segment_roles(route)
        protected = protected_by_input[route.input_port]
        blockers = [
            segment
            for other_index, other in enumerate(routes)
            for segment in (*other.external_segments, *other.local_segments)
            if other_index != route_index
        ]
        obstacles = [
            _inflate_obstacle(_routing_obstacle(cell), rules.mrr_keepout_um)
            for cell in cells.values()
        ]
        for first_bend_idx in range(1, len(points) - 2):
            local_roles = roles[first_bend_idx - 1 : first_bend_idx + 2]
            if len(local_roles) != 3 or any(role is None for role in local_roles):
                continue
            if (
                points[first_bend_idx] in protected
                or points[first_bend_idx + 1] in protected
            ):
                continue
            before, first, second, after = points[
                first_bend_idx - 1 : first_bend_idx + 3
            ]
            outer_horizontal = abs(before[1] - first[1]) < EPS
            middle_horizontal = abs(first[1] - second[1]) < EPS
            after_horizontal = abs(second[1] - after[1]) < EPS
            if outer_horizontal != after_horizontal or outer_horizontal == middle_horizontal:
                continue
            unchanged_own = [
                segment
                for segment_index, segment in enumerate(_axis_segments(points))
                if segment_index not in {
                    first_bend_idx - 1,
                    first_bend_idx,
                    first_bend_idx + 1,
                }
            ]
            for delta_um in deltas:
                candidates += 1
                try:
                    candidate_route = _slide_bend_pair_route(
                        route,
                        first_bend_idx,
                        delta_um,
                        rules,
                    )
                except ValueError:
                    continue
                changed = candidate_route.waypoints[
                    first_bend_idx - 1 : first_bend_idx + 3
                ]
                port_access = (
                    None
                    if port_access_plan is None
                    else PortAccessLegality.for_net(port_access_plan, route.input_port)
                )
                if not _candidate_is_available(
                    changed,
                    blockers + unchanged_own,
                    obstacles,
                    rules,
                    port_access,
                ):
                    continue
                candidate_routes = list(routes)
                candidate_routes[route_index] = candidate_route
                crossings = _route_crossings(candidate_routes)
                candidate_routes = [
                    _route_with_crossing_count(item, crossings)
                    for item in candidate_routes
                ]
                candidate_routes_tuple = tuple(candidate_routes)
                violations = tuple(
                    validate_physical_routes(candidate_routes_tuple, cells, rules)
                )
                counts = _drc_rule_counts(violations)
                if any(
                    count > current_counts.get(rule, 0)
                    for rule, count in counts.items()
                ):
                    continue
                arm_count = counts["crossing_clearance"]
                if arm_count >= current_arm_count:
                    continue
                loss = _loss_proxy_db(candidate_routes_tuple, len(crossings), rules)
                # The +0.01 dB allowance applies to the complete offline
                # legalization pass, not independently to every committed
                # slide.  Holding one ceiling prevents several individually
                # legal repairs from accumulating an unbounded loss increase.
                if loss > loss_ceiling + EPS:
                    continue
                legal_candidates += 1
                candidate = _SlideCandidate(
                    route_index=route_index,
                    first_bend_idx=first_bend_idx,
                    delta_um=delta_um,
                    routes=candidate_routes_tuple,
                    crossings=tuple(crossings),
                    violations=violations,
                    loss_proxy_db=loss,
                )
                if best is None or (
                    arm_count,
                    len(violations),
                    loss,
                    abs(delta_um),
                    delta_um < 0.0,
                    route.input_port,
                    first_bend_idx,
                    candidate_route.waypoints,
                ) < (
                    _drc_rule_counts(best.violations)["crossing_clearance"],
                    len(best.violations),
                    best.loss_proxy_db,
                    abs(best.delta_um),
                    best.delta_um < 0.0,
                    routes[best.route_index].input_port,
                    best.first_bend_idx,
                    best.routes[best.route_index].waypoints,
                ):
                    best = candidate
    return best, candidates, legal_candidates


def _loss_proxy_db(
    routes: tuple[PhysicalRoute, ...],
    crossing_count: int,
    rules: RoutingRules,
) -> float:
    return (
        rules.prop_loss_db_per_um * sum(route.length_um for route in routes)
        + rules.bend_loss_db_per_bend * sum(route.bend_count for route in routes)
        + rules.crossing_loss_db_per_cross * crossing_count
    )


def _candidate_is_available(
    replacement: tuple[Point, ...],
    blockers: list[Segment],
    obstacles: list[object],
    rules: RoutingRules,
    port_access: PortAccessLegality | None,
) -> bool:
    if len(replacement) == 3:
        min_leg = 2.0 * rules.bend_radius_um
        if min(
            _polyline_length(replacement[:2]),
            _polyline_length(replacement[1:]),
        ) < min_leg - EPS:
            return False
    return all(
        _route_segment_available(
            segment,
            blockers,
            obstacles,  # type: ignore[arg-type]
            rules,
            allowed_touch_points=(replacement[0], replacement[-1]),
            port_access=port_access,
        )
        for segment in _axis_segments(replacement)
    )


def physical_routes_from_fixed(
    result: FixedFabricRoutingResult,
) -> tuple[PhysicalRoute, ...]:
    waveguide_by_owner = {
        waveguide.owner_edge_id: waveguide for waveguide in result.graph.waveguides
    }
    return tuple(
        PhysicalRoute(
            input_port=waveguide_by_owner[route.edge_id].input_wire,
            output_port=waveguide_by_owner[route.edge_id].output_wire,
            waypoints=route.waypoints,
            length_um=route.length_um,
            bend_count=route.bend_count,
            external_segments=route.external_segments,
            local_segments=route.local_segments,
            crossing_count=sum(
                crossing.edge_a == route.edge_id or crossing.edge_b == route.edge_id
                for crossing in result.crossings
            ),
        )
        for route in result.routes
    )


def _best_rewrite_for_route(
    route_index: int,
    routes: tuple[PhysicalRoute, ...],
    protected: frozenset[Point],
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    baseline_loss: float,
    port_access_plan: PortAccessPlan | None,
) -> tuple[_RewriteCandidate | None, int, int]:
    route = routes[route_index]
    windows = _candidate_windows(route, protected)
    blockers = [
        segment
        for other_index, other in enumerate(routes)
        for segment in (*other.external_segments, *other.local_segments)
        if other_index != route_index
    ]
    obstacles = [
        _inflate_obstacle(_routing_obstacle(cell), rules.mrr_keepout_um)
        for cell in cells.values()
    ]
    best: _RewriteCandidate | None = None
    legal_candidates = 0
    for start_idx, end_idx, kind in windows:
        outside_segments = _axis_segments(route.waypoints[: start_idx + 1])
        outside_segments.extend(_axis_segments(route.waypoints[end_idx:]))
        candidate_blockers = blockers + outside_segments
        for replacement in _l_variants(
            route.waypoints[start_idx], route.waypoints[end_idx]
        ):
            if _bend_count(replacement) >= _bend_count(
                route.waypoints[start_idx : end_idx + 1]
            ):
                continue
            port_access = (
                None
                if port_access_plan is None
                else PortAccessLegality.for_net(port_access_plan, route.input_port)
            )
            if not _candidate_is_available(
                replacement,
                candidate_blockers,
                obstacles,
                rules,
                port_access,
            ):
                continue
            candidate_route = _splice_external_route(
                route,
                start_idx,
                end_idx,
                replacement,
                rules,
            )
            candidate_routes = list(routes)
            candidate_routes[route_index] = candidate_route
            crossings = _route_crossings(candidate_routes)
            candidate_routes = [
                _route_with_crossing_count(item, crossings) for item in candidate_routes
            ]
            if validate_physical_routes(candidate_routes, cells, rules):
                continue
            legal_candidates += 1
            candidate_routes_tuple = tuple(candidate_routes)
            loss = _loss_proxy_db(candidate_routes_tuple, len(crossings), rules)
            if loss > baseline_loss + EPS:
                continue
            candidate = _RewriteCandidate(
                route_index=route_index,
                start_idx=start_idx,
                end_idx=end_idx,
                route=candidate_routes_tuple[route_index],
                routes=candidate_routes_tuple,
                crossings=tuple(crossings),
                loss_proxy_db=loss,
                kind=kind,
            )
            if best is None or (
                candidate.loss_proxy_db,
                len(candidate.crossings),
                sum(item.length_um for item in candidate.routes),
                sum(item.bend_count for item in candidate.routes),
                candidate.start_idx,
                candidate.end_idx,
                candidate.route.waypoints,
            ) < (
                best.loss_proxy_db,
                len(best.crossings),
                sum(item.length_um for item in best.routes),
                sum(item.bend_count for item in best.routes),
                best.start_idx,
                best.end_idx,
                best.route.waypoints,
            ):
                best = candidate
    return best, len(windows), legal_candidates


def _resplit_edge_routes(
    new_waypoints: tuple[Point, ...],
    old_route: FabricRoute,
    rules: RoutingRules,
) -> tuple[object, ...]:
    from .fabric import FabricEdgeRoute

    edge_routes: list[FabricEdgeRoute] = []
    cursor = 0
    bend_arc_um = 0.5 * pi * rules.bend_radius_um
    for old_edge_route in old_route.edge_routes:
        source = old_edge_route.waypoints[0]
        target = old_edge_route.waypoints[-1]
        source_idx = next(
            idx
            for idx in range(cursor, len(new_waypoints))
            if _same_point(new_waypoints[idx], source)
        )
        target_idx = next(
            idx
            for idx in range(source_idx, len(new_waypoints))
            if _same_point(new_waypoints[idx], target)
        )
        points = new_waypoints[source_idx : target_idx + 1]
        bends = _bend_count(points)
        edge_routes.append(
            FabricEdgeRoute(
                edge_id=old_edge_route.edge_id,
                waypoints=points,
                length_um=_polyline_length(points) + bends * bend_arc_um,
                bend_count=bends,
            )
        )
        cursor = target_idx
    return tuple(edge_routes)


def straighten_fixed_fabric(
    result: FixedFabricRoutingResult,
    cells: dict[str, MRRCell],
    *,
    port_access_plan: PortAccessPlan | None = None,
    max_rounds: int = 10,
    preserve_template: bool = False,
) -> StraightenResult:
    """Conservatively remove external jogs from an already-routed fabric.

    This function never invokes a router.  It first slides adjacent bend pairs
    to repair crossing arms, accepting at most +0.01 dB only when an arm
    violation is removed.  Ordinary jog rewrites remain non-increasing-loss.
    Every commit passes blocker/keepout/port-access checks and may not add a DRC
    finding relative to its input geometry.
    """
    if max_rounds < 1:
        raise ValueError("max_rounds must be positive")
    if preserve_template:
        stats = StraightenStats()
        return StraightenResult(replace(result, straighten_stats=stats), stats)
    if result.failed_edges:
        raise ValueError("cannot straighten an incomplete fabric")

    routes = physical_routes_from_fixed(result)
    initial_routes = routes
    initial_crossings = _route_crossings(routes)
    initial_loss = _loss_proxy_db(routes, len(initial_crossings), result.rules)
    current_loss = initial_loss
    protected_by_input = {
        physical.input_port: _protected_waypoints(fabric)
        for physical, fabric in zip(routes, result.routes)
    }
    candidate_windows = 0
    legal_candidates = 0
    rewrites = 0
    rounds_executed = 0
    crossings = initial_crossings
    initial_drc = tuple(validate_physical_routes(routes, cells, result.rules))
    current_drc = initial_drc
    bend_slides = 0

    for _round_index in range(max_rounds):
        slide, slide_candidates, slide_legal = _best_bend_slide(
            routes,
            protected_by_input,
            cells,
            result.rules,
            initial_loss + 0.01,
            current_drc,
            port_access_plan,
        )
        candidate_windows += slide_candidates
        legal_candidates += slide_legal
        if slide is None:
            break
        routes = slide.routes
        crossings = slide.crossings  # type: ignore[assignment]
        current_loss = slide.loss_proxy_db
        current_drc = slide.violations
        bend_slides += 1
        rounds_executed += 1

    for round_index in range(max_rounds - rounds_executed):
        if current_drc:
            break
        changed = False
        rounds_executed += 1
        for route_index in sorted(
            range(len(routes)), key=lambda index: routes[index].input_port
        ):
            candidate, window_count, legal_count = _best_rewrite_for_route(
                route_index,
                routes,
                protected_by_input[routes[route_index].input_port],
                cells,
                result.rules,
                current_loss,
                port_access_plan,
            )
            candidate_windows += window_count
            legal_candidates += legal_count
            if candidate is None:
                continue
            routes = candidate.routes
            crossings = candidate.crossings  # type: ignore[assignment]
            current_loss = candidate.loss_proxy_db
            rewrites += 1
            changed = True
        if not changed:
            break

    final_drc = validate_physical_routes(routes, cells, result.rules)
    initial_counts = _drc_rule_counts(initial_drc)
    final_counts = _drc_rule_counts(tuple(final_drc))
    regression = next(
        (
            violation
            for violation in final_drc
            if final_counts[violation.rule] > initial_counts.get(violation.rule, 0)
        ),
        None,
    )
    if regression is not None:
        raise RuntimeError(
            "straightening legality regression: "
            f"{regression.rule}: {regression.net_id}: {regression.message}"
        )

    fabric_by_input = {
        waveguide.input_wire: next(
            route for route in result.routes if route.edge_id == waveguide.owner_edge_id
        )
        for waveguide in result.graph.waveguides
    }
    waveguide_by_input = {
        waveguide.input_wire: waveguide for waveguide in result.graph.waveguides
    }
    new_fabric_routes = tuple(
        replace(
            fabric_by_input[physical.input_port],
            waypoints=physical.waypoints,
            length_um=physical.length_um,
            bend_count=physical.bend_count,
            external_segments=physical.external_segments,
            local_segments=physical.local_segments,
            edge_routes=_resplit_edge_routes(
                physical.waypoints,
                fabric_by_input[physical.input_port],
                result.rules,
            ),
        )
        for physical in routes
    )
    owner_by_input = {
        input_wire: waveguide.owner_edge_id
        for input_wire, waveguide in waveguide_by_input.items()
    }
    stats = StraightenStats(
        rounds=rounds_executed,
        rewrites=rewrites + bend_slides,
        candidate_windows=candidate_windows,
        legal_candidates=legal_candidates,
        bends_removed=sum(route.bend_count for route in initial_routes)
        - sum(route.bend_count for route in routes),
        length_saved_um=sum(route.length_um for route in initial_routes)
        - sum(route.length_um for route in routes),
        crossings_delta=len(crossings) - len(initial_crossings),
        loss_proxy_delta_db=current_loss - initial_loss,
        bend_slides=bend_slides,
        crossing_arm_violations_removed=(
            initial_counts["crossing_clearance"]
            - final_counts["crossing_clearance"]
        ),
    )
    final_result = replace(
        result,
        routes=new_fabric_routes,
        drc_violations=_map_physical_drc(final_drc, owner_by_input),
        crossings=tuple(
            FabricCrossing(
                owner_by_input[crossing.net_a],
                owner_by_input[crossing.net_b],
                crossing.location,
            )
            for crossing in crossings
        ),
        straighten_stats=stats,
    )
    return StraightenResult(
        final_result,
        stats,
    )


__all__ = [
    "StraightenResult",
    "StraightenStats",
    "physical_routes_from_fixed",
    "straighten_fixed_fabric",
]
