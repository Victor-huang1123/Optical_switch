from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import replace
from heapq import heappop, heappush
from math import ceil, floor, hypot, pi
from typing import TYPE_CHECKING

from .crossing import (
    CrossingCandidate,
    CrossingRule,
    crossing_budget_exceeded,
    crossing_pair_key,
    endpoint_crossing_candidate,
    endpoint_crossing_required,
    legal_crossing_candidate,
)
from .geometry import (
    _append_points,
    _backtrack_penalty,
    _canonical_track,
    _connected_same_net_hairpin_point,
    _direction,
    _effective_spacing_threshold,
    _infer_n_physical,
    _is_axis_aligned,
    _manhattan,
    _merge_collinear_points,
    _orthogonal_crossing_point,
    _parallel_spacing_violation,
    _same_net_hairpin_penalty,
    _same_point,
    _same_segment,
    _segment_contact_point,
    _segment_crossing_count,
    _segment_hits_forbidden_point,
    _segment_intersects_obstacle,
    _segments_collinear_overlap,
    _shared_endpoint,
    _turn_timing_penalty,
    _wire_y,
)
from .grid_router import (
    HistoryCost,
    RouterState,
    neighbor_moves,
    opposite_orientation,
    orientation_axis,
)
from .port_access import (
    PortAccessLegality,
    PortAccessRegion,
    _port_junction_orientation,
    _port_route_point,
    port_access_conflict,
    port_access_conflict_detail,
)
from .route_grid import CrossingFootprint, RouteGrid, crossing_footprint_conflict
from .types import (
    EPS,
    Obstacle,
    OccupiedRouteSegment,
    OwnedSegment,
    Point,
    RoutingError,
    RoutingRules,
    RoutingWindow,
    Segment,
)

if TYPE_CHECKING:
    from ..core.models import MRRCell
    from ..core.topology import Path

__all__ = [
    '_astar_route',
    '_start_astar_call_tracking',
    '_stop_astar_call_tracking',
    '_route_segment_available',
    '_same_net_self_conflict',
    '_same_net_self_conflict_reason',
    '_same_net_reason_is_hairpin',
    '_same_net_touching_corner_reason',
    '_append_external',
    '_append_local_axis',
    '_routing_grid',
    '_tracks_between',
    '_insert_track',
    '_canonical_track',
    '_grid_neighbors',
    '_reconstruct_astar_path',
]


_ASTAR_CALL_COUNT: ContextVar[int | None] = ContextVar(
    "_ASTAR_CALL_COUNT",
    default=None,
)


def _start_astar_call_tracking() -> Token[int | None]:
    return _ASTAR_CALL_COUNT.set(0)


def _stop_astar_call_tracking(token: Token[int | None]) -> int:
    count = _ASTAR_CALL_COUNT.get()
    _ASTAR_CALL_COUNT.reset(token)
    return count or 0


def _record_astar_call() -> None:
    count = _ASTAR_CALL_COUNT.get()
    if count is not None:
        _ASTAR_CALL_COUNT.set(count + 1)


def _same_net_self_conflict(
    segment: Segment,
    own_segments: list[Segment] | tuple[Segment, ...],
    *,
    join_points: tuple[Point, ...] = (),
    rules: RoutingRules | None = None,
) -> bool:
    """True if extending the current net with ``segment`` would knot the net.

    A knot is any same-net self-overlap, interior self-crossing, self T-touch, or
    self-loop against geometry the net has already committed (earlier hops plus
    the current hop's partial path in ``own_segments``). Contact is legal only at
    a hop-join point (``join_points``, i.e. src/dst) or at the shared endpoint
    with the immediately preceding segment (``own_segments[-1]``), which is just
    the bend/continuation. This is a routing-legality reject, not a refinement
    repair: it stops the knot from ever being committed during A* search.
    """
    return _same_net_self_conflict_reason(
        segment,
        own_segments,
        join_points=join_points,
        rules=rules,
    ) is not None


def _same_net_self_conflict_reason(
    segment: Segment,
    own_segments: list[Segment] | tuple[Segment, ...],
    *,
    join_points: tuple[Point, ...] = (),
    rules: RoutingRules | None = None,
) -> str | None:
    if not own_segments:
        return None
    last_idx = len(own_segments) - 1
    for idx, prior in enumerate(own_segments):
        if _segments_collinear_overlap(segment, prior):
            return f"same_net_overlap:{_format_segment_debug(prior)}"
        crossing = _orthogonal_crossing_point(segment, prior)
        if crossing is not None:
            return (
                f"same_net_crossing at=({crossing[0]:.3f},{crossing[1]:.3f}) "
                f"prior={_format_segment_debug(prior)}"
            )
        contact = _segment_contact_point(segment, prior)
        if contact is None:
            continue
        if any(_same_point(contact, jp) for jp in join_points):
            continue
        if idx == last_idx and _same_point(contact, segment[0]):
            continue
        return (
            f"same_net_touch at=({contact[0]:.3f},{contact[1]:.3f}) "
            f"prior={_format_segment_debug(prior)}"
        )
    if rules is not None:
        hairpin = _same_net_hairpin_conflict_point(
            segment,
            tuple(own_segments),
            rules,
            join_points=join_points,
        )
        if hairpin is not None:
            point, prior = hairpin
            return (
                f"same_net_hairpin at=({point[0]:.3f},{point[1]:.3f}) "
                f"prior={_format_segment_debug(prior)}"
            )
    return None

def _same_net_reason_is_hairpin(reason: str | None) -> bool:
    return bool(reason and reason.startswith("same_net_hairpin"))


def _same_net_touching_corner_reason(
    segment: Segment,
    own_segments: tuple[Segment, ...],
) -> str | None:
    if not own_segments:
        return None
    last_idx = len(own_segments) - 1
    for idx, prior in enumerate(own_segments):
        contact = _segment_contact_point(segment, prior)
        if contact is None:
            continue
        if idx == last_idx and _same_point(contact, segment[0]):
            continue
        return (
            f"same_net_touching_corner at=({contact[0]:.3f},{contact[1]:.3f}) "
            f"prior={_format_segment_debug(prior)}"
        )
    return None


def _same_net_hairpin_conflict_point(
    segment: Segment,
    own_segments: tuple[Segment, ...],
    rules: RoutingRules,
    *,
    join_points: tuple[Point, ...],
) -> tuple[Point, Segment] | None:
    candidate_idx = len(own_segments)
    external_segments = own_segments + (segment,)
    candidate = OwnedSegment(0, segment, "external", candidate_idx)
    for idx, prior in enumerate(own_segments):
        hairpin = _connected_same_net_hairpin_point(
            OwnedSegment(0, prior, "external", idx),
            candidate,
            external_segments,
            rules,
        )
        if hairpin is None:
            continue
        return hairpin, prior
    return None


def _format_segment_debug(segment: Segment) -> str:
    return (
        f"({segment[0][0]:.3f},{segment[0][1]:.3f})"
        f"->({segment[1][0]:.3f},{segment[1][1]:.3f})"
    )

def _astar_route(
    src: Point,
    dst: Point,
    blockers: list[Segment],
    obstacles: list[Obstacle],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
    *,
    src_label: str,
    dst_label: str,
    forbidden_points: set[Point] | None = None,
    current_segments: list[Segment] | None = None,
    current_external_segments: list[Segment] | None = None,
    occupied_segments: list[OccupiedRouteSegment] | None = None,
    crossing_count_by_pair: dict[tuple[int, int], int] | None = None,
    crossing_sources_by_pair: dict[tuple[int, int], tuple[str, ...]] | None = None,
    soft_blockers: list[Segment] | None = None,
    reserved_spacing_blockers: list[Segment] | None = None,
    reserved_guard_blockers: list[Segment] | None = None,
    reserved_crossing_owners: set[int] | None = None,
    deferred_crossing_owners: set[int] | None = None,
    preferred_bend_x: float | None = None,
    reserve_space_penalty: bool = False,
    port_access: PortAccessLegality | None = None,
    routing_window: RoutingWindow | None = None,
    route_grid: RouteGrid | None = None,
    history_cost: HistoryCost | None = None,
    repeated_crossing_penalty_scale: float = 1.0,
) -> tuple[Point, ...]:
    _record_astar_call()
    if _same_point(src, dst):
        return (src,)
    if routing_window is not None and (
        not routing_window.contains_point(src) or not routing_window.contains_point(dst)
    ):
        raise RoutingError(
            f"cannot route {src_label} to {dst_label}: endpoint outside route window"
        )

    x_tracks = _insert_track(grid[0], src[0], dst[0])
    y_tracks = _insert_track(grid[1], src[1], dst[1])
    x_index = {value: idx for idx, value in enumerate(x_tracks)}
    y_index = {value: idx for idx, value in enumerate(y_tracks)}
    bend_spacing_um = (
        2.0 * rules.bend_radius_um if rules.enforce_bend_spacing else None
    )
    start_orientation = (
        _port_junction_orientation(src_label, source=True)
        if bend_spacing_um is not None
        else ""
    )
    start = RouterState(
        x_index[_canonical_track(src[0])],
        y_index[_canonical_track(src[1])],
        start_orientation,
        bend_spacing_um if start_orientation and bend_spacing_um is not None else 0.0,
    )
    target = (x_index[_canonical_track(dst[0])], y_index[_canonical_track(dst[1])])
    forbidden_points = forbidden_points or set()
    current_segments_tuple = tuple(current_segments or [])
    strict_current_external_segments_tuple = tuple(
        current_segments_tuple
        if current_external_segments is None
        else current_external_segments
    )
    current_external_segments_tuple = tuple(
        current_external_segments or current_segments_tuple
    )
    crossing_count_by_pair = crossing_count_by_pair or {}
    crossing_sources_by_pair = crossing_sources_by_pair or {}
    soft_blockers = soft_blockers or []
    reserved_spacing_blockers = reserved_spacing_blockers or []
    reserved_guard_blockers = reserved_guard_blockers or []
    reserved_crossing_owners = reserved_crossing_owners or set()
    deferred_crossing_owners = deferred_crossing_owners or set()
    route_grid = route_grid or _build_route_grid(
        x_tracks,
        y_tracks,
        blockers,
        obstacles,
        port_access,
        rules,
        occupied_segments=occupied_segments,
    )
    history_cost = history_cost or HistoryCost()
    use_turn_timing = not (src_label.startswith("I") or dst_label.startswith("O"))

    start_key = (start, _crossing_signature(crossing_count_by_pair, crossing_count_by_pair))
    heap: list[
        tuple[
            float,
            int,
            float,
            float,
            int,
            tuple[RouterState, tuple[tuple[int, int], ...]],
        ]
    ] = []
    counter = 0
    dist: dict[
        tuple[RouterState, tuple[tuple[int, int], ...]],
        tuple[float, int],
    ] = {start_key: (0.0, 0)}
    prev: dict[
        tuple[RouterState, tuple[tuple[int, int], ...]],
        tuple[tuple[RouterState, tuple[tuple[int, int], ...]], Point],
    ] = {}
    last_bend: dict[
        tuple[RouterState, tuple[tuple[int, int], ...]],
        Point | None,
    ] = {start_key: None}
    last_turns: dict[
        tuple[RouterState, tuple[tuple[int, int], ...]],
        tuple[int, ...],
    ] = {start_key: ()}
    partial_segments_by_state: dict[
        tuple[RouterState, tuple[tuple[int, int], ...]],
        tuple[Segment, ...],
    ] = {start_key: ()}
    crossing_counts_by_state: dict[
        tuple[RouterState, tuple[tuple[int, int], ...]],
        dict[tuple[int, int], int],
    ] = {
        start_key: dict(crossing_count_by_pair)
    }
    crossing_sources_by_state: dict[
        tuple[RouterState, tuple[tuple[int, int], ...]],
        dict[tuple[int, int], tuple[str, ...]],
    ] = {
        start_key: dict(crossing_sources_by_pair)
    }
    crossing_footprints_by_state: dict[
        tuple[RouterState, tuple[tuple[int, int], ...]],
        tuple[CrossingFootprint, ...],
    ] = {start_key: ()}
    port_access_blocks: dict[str, int] = {}
    port_access_block_samples: dict[str, tuple[Segment, PortAccessRegion]] = {}
    window_blocks = 0
    same_net_blocks = 0
    crossing_candidate_blocks = 0
    crossing_budget_blocks = 0
    hard_reserved_crossing_blocks = 0
    soft_reserved_crossing_hits = 0
    soft_repeated_crossing_hits = 0
    crossing_budget_block_samples: dict[tuple[int, int], CrossingCandidate] = {}
    crossing_budget_source_samples: dict[tuple[int, int], tuple[str, ...]] = {}
    hard_reserved_crossing_samples: dict[tuple[int, int], CrossingCandidate] = {}
    hard_reserved_crossing_source_samples: dict[tuple[int, int], tuple[str, ...]] = {}
    soft_reserved_crossing_samples: dict[tuple[int, int], CrossingCandidate] = {}
    soft_reserved_crossing_source_samples: dict[tuple[int, int], tuple[str, ...]] = {}
    soft_repeated_crossing_samples: dict[tuple[int, int], CrossingCandidate] = {}
    soft_repeated_crossing_source_samples: dict[tuple[int, int], tuple[str, ...]] = {}
    reserved_spacing_blocks = 0
    segment_blocks = 0
    start_heuristic = _heuristic_cost(src, dst, rules)
    heappush(
        heap,
        (
            rules.astar_heuristic_weight * start_heuristic,
            0,
            start_heuristic,
            0.0,
            counter,
            start_key,
        ),
    )
    pops = 0
    max_heap = len(heap)
    current_self_conflict_cache: dict[Segment, str | None] = {}
    segment_available_cache: dict[Segment, bool] = {}
    port_access_detail_cache: dict[Segment, tuple[str, PortAccessRegion] | None] = {}
    crossing_info_cache: dict[Segment, tuple[CrossingCandidate | None, bool, int]] = {}
    soft_penalty_cache: dict[Segment, float] = {}
    backtrack_penalty_cache: dict[Segment, float] = {}
    reserved_spacing_cache: dict[Segment, bool] = {}
    reserved_guard_cache: dict[Segment, bool] = {}
    same_net_block_samples: list[str] = []

    while heap:
        _estimate, crossing_score, _remaining, cost, _counter, state_key = heappop(heap)
        state, _signature = state_key
        pops += 1
        if rules.max_astar_pops is not None and pops > rules.max_astar_pops:
            raise RoutingError(
                f"cannot route {src_label} to {dst_label} on physical grid: "
                f"A* pop limit exceeded"
                f"{_crossing_debug_details(crossing_budget_block_samples, crossing_budget_source_samples, hard_reserved_crossing_samples, hard_reserved_crossing_source_samples, soft_repeated_crossing_samples, soft_repeated_crossing_source_samples, soft_reserved_crossing_samples, soft_reserved_crossing_source_samples)}"
                f"{_failure_counter_summary(port_access_blocks=sum(port_access_blocks.values()), window_blocks=window_blocks, same_net_blocks=same_net_blocks, crossing_candidate_blocks=crossing_candidate_blocks, crossing_budget_blocks=crossing_budget_blocks, hard_reserved_crossing_blocks=hard_reserved_crossing_blocks, soft_repeated_crossing_hits=soft_repeated_crossing_hits, soft_reserved_crossing_hits=soft_reserved_crossing_hits, reserved_spacing_blocks=reserved_spacing_blocks, segment_blocks=segment_blocks, pops=pops, max_heap=max_heap)}"
            )
        best_cost, best_crossings = dist.get(state_key, (float("inf"), 10**9))
        if cost > best_cost + EPS:
            continue
        if abs(cost - best_cost) < EPS and crossing_score > best_crossings:
            continue
        if (state.x_idx, state.y_idx) == target:
            if bend_spacing_um is None or _target_junction_is_legal(
                state,
                dst_label,
                bend_spacing_um,
            ):
                if state.crossing_arm_remaining_um > EPS:
                    continue
                return _reconstruct_astar_path_from_keys(
                    state_key,
                    prev,
                    x_tracks,
                    y_tracks,
                )

            # This net's already-committed geometry: earlier hops (current_segments_tuple)
        # plus the partial path of this hop up to `state`. Static checks against
        # earlier hops are cached per directed grid edge; partial-path checks
        # remain history-dependent.
        partial_segments = partial_segments_by_state.get(state_key, ())
        current_footprints = crossing_footprints_by_state.get(state_key, ())
        state_crossing_counts = crossing_counts_by_state.get(
            state_key,
            crossing_count_by_pair,
        )
        state_crossing_sources = crossing_sources_by_state.get(
            state_key,
            crossing_sources_by_pair,
        )

        for move in neighbor_moves(
            state,
            x_tracks,
            y_tracks,
            bend_spacing_um=bend_spacing_um,
        ):
            p0, p1 = move.segment
            segment = move.segment
            owner_input = port_access.current_net_id if port_access is not None else None
            if routing_window is not None and not routing_window.contains_segment(segment):
                window_blocks += 1
                continue
            if route_grid.crossing_footprint_conflict(segment, owner_input) is not None:
                crossing_candidate_blocks += 1
                continue
            if any(
                crossing_footprint_conflict(segment, footprint, owner_input)
                for footprint in current_footprints
            ):
                crossing_candidate_blocks += 1
                continue
            if current_segments_tuple:
                if segment not in current_self_conflict_cache:
                    current_self_conflict_cache[segment] = _same_net_self_conflict_reason(
                        segment,
                        current_segments_tuple,
                        join_points=(src, dst),
                    )
                current_conflict = current_self_conflict_cache[segment]
                if current_conflict is not None:
                    if len(same_net_block_samples) < 3:
                        same_net_block_samples.append(current_conflict)
                    same_net_blocks += 1
                    continue
            hairpin_soft_penalty = 0.0
            hairpin_segments = current_external_segments_tuple + partial_segments
            if hairpin_segments:
                if rules.uses_db_cost:
                    touching_corner_reason = _same_net_touching_corner_reason(
                        segment,
                        strict_current_external_segments_tuple + partial_segments,
                    )
                    if touching_corner_reason is not None:
                        if len(same_net_block_samples) < 3:
                            same_net_block_samples.append(touching_corner_reason)
                        same_net_blocks += 1
                        continue
                hairpin_reason = _same_net_self_conflict_reason(
                    segment,
                    hairpin_segments,
                    join_points=(src, dst),
                    rules=rules,
                )
                if hairpin_reason is not None:
                    if _same_net_reason_is_hairpin(hairpin_reason):
                        hairpin_soft_penalty = rules.hairpin_penalty_um
                    else:
                        if len(same_net_block_samples) < 3:
                            same_net_block_samples.append(hairpin_reason)
                        same_net_blocks += 1
                        continue
            reserved_spacing_conflict = reserved_spacing_cache.get(segment)
            if reserved_spacing_conflict is None:
                reserved_spacing_conflict = _reserved_spacing_conflict(
                    segment,
                    reserved_spacing_blockers,
                    rules,
                )
                reserved_spacing_cache[segment] = reserved_spacing_conflict
            if reserved_spacing_conflict:
                reserved_spacing_blocks += 1
                continue
            reserved_guard_conflict = reserved_guard_cache.get(segment)
            if reserved_guard_conflict is None:
                reserved_guard_conflict = _reserved_guard_conflict(
                    segment,
                    reserved_guard_blockers,
                    rules,
                )
                reserved_guard_cache[segment] = reserved_guard_conflict
            if reserved_guard_conflict:
                reserved_spacing_blocks += 1
                continue
            available = segment_available_cache.get(segment)
            if available is None:
                available = _route_segment_available(
                    segment,
                    blockers,
                    obstacles,
                    rules,
                    forbidden_points,
                    allowed_touch_points=(src, dst),
                    port_access=port_access,
                )
                segment_available_cache[segment] = available
                if not available and port_access is not None:
                    port_access_detail_cache[segment] = port_access_conflict_detail(
                        segment,
                        port_access,
                        rules,
                        allowed_touch_points=(src, dst),
                    )
            if not available:
                if port_access is not None:
                    detail = port_access_detail_cache.get(segment)
                    if detail is not None:
                        reason, region = detail
                        port_access_blocks[reason] = port_access_blocks.get(reason, 0) + 1
                        port_access_block_samples.setdefault(reason, (segment, region))
                    else:
                        segment_blocks += 1
                else:
                    segment_blocks += 1
                continue
            next_partial_segments = _append_partial_segment(partial_segments, segment)
            candidate_arm_segment = next_partial_segments[-1]
            use_crossing_cache = rules.min_crossing_clearance_um is None
            crossing_info = crossing_info_cache.get(segment) if use_crossing_cache else None
            if crossing_info is None:
                crossing_clearance = (
                    None
                    if rules.min_crossing_clearance_um is None
                    else _effective_spacing_threshold(
                        rules.min_crossing_clearance_um,
                        rules.waveguide_width_um,
                    )
                )
                crossing_rule = CrossingRule(
                    min_clearance_um=crossing_clearance,
                    candidate_entry_point=candidate_arm_segment[0],
                    defer_candidate_exit=crossing_clearance is not None,
                )
                crossing_candidate = legal_crossing_candidate(
                    segment,
                    route_grid,
                    rules,
                    port_access.current_net_id if port_access is not None else None,
                    crossing_rule,
                    candidate_arm_segment=candidate_arm_segment,
                )
                needs_endpoint_crossing = endpoint_crossing_required(
                    segment,
                    route_grid,
                    rules,
                    port_access.current_net_id if port_access is not None else None,
                )
                if crossing_candidate is None:
                    crossing_candidate = endpoint_crossing_candidate(
                        segment,
                        route_grid,
                        rules,
                        port_access.current_net_id if port_access is not None else None,
                        crossing_rule,
                        candidate_arm_segment=candidate_arm_segment,
                    )
                raw_crossings = _segment_crossing_count(segment, blockers)
                crossing_info = (crossing_candidate, needs_endpoint_crossing, raw_crossings)
                if use_crossing_cache:
                    crossing_info_cache[segment] = crossing_info
            crossing_candidate, needs_endpoint_crossing, raw_crossings = crossing_info
            if rules.explicit_crossings and (
                raw_crossings or needs_endpoint_crossing or crossing_candidate is not None
            ):
                repeated_crossing = False
                reserved_crossing = False
                if crossing_candidate is None:
                    crossing_candidate_blocks += 1
                    continue
                if route_grid.footprint_contains_point(crossing_candidate.location) or any(
                    _point_in_crossing_footprint(crossing_candidate.location, footprint)
                    for footprint in current_footprints
                ):
                    crossing_candidate_blocks += 1
                    continue
                if crossing_candidate.crossed_input in reserved_crossing_owners:
                    pair = crossing_pair_key(
                        crossing_candidate.owner_input,
                        crossing_candidate.crossed_input,
                    )
                    if pair is not None:
                        if rules.hard_reserved_crossing_reservation:
                            hard_reserved_crossing_blocks += 1
                            hard_reserved_crossing_samples.setdefault(pair, crossing_candidate)
                            hard_reserved_crossing_source_samples.setdefault(
                                pair,
                                state_crossing_sources.get(pair, ()),
                            )
                        else:
                            soft_reserved_crossing_hits += 1
                            soft_reserved_crossing_samples.setdefault(pair, crossing_candidate)
                            soft_reserved_crossing_source_samples.setdefault(
                                pair,
                                state_crossing_sources.get(pair, ()),
                            )
                    if rules.hard_reserved_crossing_reservation:
                        continue
                    reserved_crossing = True
                if crossing_budget_exceeded(crossing_candidate, state_crossing_counts):
                    pair = crossing_pair_key(
                        crossing_candidate.owner_input,
                        crossing_candidate.crossed_input,
                    )
                    if rules.hard_crossing_budget:
                        if pair is not None:
                            crossing_budget_blocks += 1
                            crossing_budget_block_samples.setdefault(pair, crossing_candidate)
                            crossing_budget_source_samples.setdefault(
                                pair,
                                state_crossing_sources.get(pair, ()),
                            )
                        continue
                    if pair is not None:
                        soft_repeated_crossing_hits += 1
                        soft_repeated_crossing_samples.setdefault(pair, crossing_candidate)
                        soft_repeated_crossing_source_samples.setdefault(
                            pair,
                            state_crossing_sources.get(pair, ()),
                        )
                    repeated_crossing = True
                step_crossings = 1
            else:
                repeated_crossing = False
                reserved_crossing = False
                step_crossings = raw_crossings
            step_cost = _step_length_cost(p0, p1, rules)
            step_cost += _crossing_step_cost(step_crossings, rules)
            if repeated_crossing:
                step_cost += _repeated_crossing_step_cost(
                    rules,
                    repeated_crossing_penalty_scale,
                )
            if crossing_candidate is not None and not repeated_crossing and (
                reserved_crossing
                or crossing_candidate.crossed_input in deferred_crossing_owners
            ):
                step_cost += _search_guidance_cost(8.0 * rules.hairpin_penalty_um, rules)
            backtrack_penalty = backtrack_penalty_cache.get(segment)
            if backtrack_penalty is None:
                backtrack_penalty = _backtrack_penalty(p0, p1, src, dst, rules)
                backtrack_penalty_cache[segment] = backtrack_penalty
            step_cost += _search_guidance_cost(backtrack_penalty, rules)
            step_cost += _same_net_physical_cost(
                _same_net_hairpin_penalty(segment, list(current_segments_tuple), rules),
                rules,
            )
            step_cost += _same_net_physical_cost(hairpin_soft_penalty, rules)
            soft_penalty = soft_penalty_cache.get(segment)
            if soft_penalty is None:
                soft_penalty = _soft_blocker_penalty(segment, soft_blockers, rules)
                soft_penalty_cache[segment] = soft_penalty
            step_cost += _search_guidance_cost(soft_penalty, rules)
            turning = (
                bool(state.orientation)
                and orientation_axis(state.orientation) != orientation_axis(move.next_state.orientation)
            )
            next_turns = last_turns.get(state_key, ())
            if turning:
                previous_bend = last_bend.get(state_key)
                if (
                    not rules.enforce_bend_spacing
                    and previous_bend is not None
                    and _manhattan(previous_bend, p0)
                    < 2.0 * rules.bend_radius_um - EPS
                ):
                    continue
                step_cost += _bend_step_cost(rules)
                if use_turn_timing:
                    step_cost += _turn_timing_cost(p0, src, dst, rules)
                step_cost += _bend_placement_cost(p0, obstacles, port_access, rules)
                if preferred_bend_x is not None:
                    step_cost += _search_guidance_cost(0.05 * abs(p0[0] - preferred_bend_x), rules)
                turn_sign = _turn_sign(state.orientation, move.next_state.orientation)
                if rules.uses_db_cost and _alternating_jog(next_turns, turn_sign):
                    step_cost += _jog_step_cost(rules)
                next_turns = (next_turns + (turn_sign,))[-2:]
            step_cost += _search_guidance_cost(history_cost.cost(move.next_state), rules)
            y_error = abs(p1[1] - dst[1])
            if reserve_space_penalty and abs(p1[0] - p0[0]) > EPS and y_error > EPS:
                new_remaining_x = abs(p1[0] - dst[0])
                space_needed = y_error + 2.0 * rules.bend_radius_um
                if new_remaining_x < space_needed:
                    step_cost += _search_guidance_cost(0.5 * (space_needed - new_remaining_x), rules)
            next_cost = cost + step_cost
            next_crossing_score = crossing_score + step_crossings
            next_state = move.next_state
            next_footprints = current_footprints
            if crossing_candidate is not None and rules.min_crossing_clearance_um is not None:
                clearance = _effective_spacing_threshold(
                    rules.min_crossing_clearance_um,
                    rules.waveguide_width_um,
                )
                remaining = max(
                    0.0,
                    clearance - _manhattan(crossing_candidate.location, p1),
                )
                next_state = replace(
                    next_state,
                    crossing_arm_remaining_um=round(
                        max(next_state.crossing_arm_remaining_um, remaining),
                        6,
                    ),
                )
                if owner_input is not None and crossing_candidate.crossed_input is not None:
                    candidate_horizontal = abs(
                        crossing_candidate.segment[0][1]
                        - crossing_candidate.segment[1][1]
                    ) < EPS
                    next_footprints = current_footprints + (
                        CrossingFootprint(
                            location=crossing_candidate.location,
                            side_um=rules.min_crossing_clearance_um,
                            horizontal_owner=(
                                owner_input
                                if candidate_horizontal
                                else crossing_candidate.crossed_input
                            ),
                            vertical_owner=(
                                crossing_candidate.crossed_input
                                if candidate_horizontal
                                else owner_input
                            ),
                        ),
                    )
            next_counts = dict(state_crossing_counts)
            if crossing_candidate is not None:
                pair = crossing_pair_key(
                    crossing_candidate.owner_input,
                    crossing_candidate.crossed_input,
                )
                if pair is not None:
                    next_counts[pair] = next_counts.get(pair, 0) + 1
            next_sources = dict(state_crossing_sources)
            if crossing_candidate is not None:
                pair = crossing_pair_key(
                    crossing_candidate.owner_input,
                    crossing_candidate.crossed_input,
                )
                if pair is not None:
                    next_sources[pair] = next_sources.get(pair, ()) + (
                        _format_crossing_source(crossing_candidate),
                    )
            next_key = (
                next_state,
                _crossing_signature(next_counts, crossing_count_by_pair),
            )
            old_cost, old_crossing_score = dist.get(next_key, (float("inf"), 10**9))
            if next_cost > old_cost + EPS:
                continue
            if abs(next_cost - old_cost) < EPS and next_crossing_score >= old_crossing_score:
                continue
            dist[next_key] = (next_cost, next_crossing_score)
            prev[next_key] = (state_key, p0)
            last_bend[next_key] = p0 if turning else last_bend.get(state_key)
            partial_segments_by_state[next_key] = _append_partial_segment(
                partial_segments,
                segment,
            )
            crossing_footprints_by_state[next_key] = next_footprints
            crossing_counts_by_state[next_key] = next_counts
            crossing_sources_by_state[next_key] = next_sources
            last_turns[next_key] = next_turns
            counter += 1
            heuristic = _heuristic_cost(p1, dst, rules)
            heappush(
                heap,
                (
                    next_cost + rules.astar_heuristic_weight * heuristic,
                    next_crossing_score,
                    heuristic,
                    next_cost,
                    counter,
                    next_key,
                ),
            )
            if len(heap) > max_heap:
                max_heap = len(heap)

    if port_access_blocks:
        reason = max(port_access_blocks.items(), key=lambda item: (item[1], item[0]))[0]
        detail_message = ""
        if reason in port_access_block_samples:
            segment, region = port_access_block_samples[reason]
            detail_message = (
                f"; candidate={_format_segment_debug(segment)} "
                f"region=I{region.owner_input}/{region.cell_id}.{region.port}:"
                f"{_format_segment_debug(region.segment)}"
            )
        detail_message += _crossing_debug_details(
            crossing_budget_block_samples,
            crossing_budget_source_samples,
            hard_reserved_crossing_samples,
            hard_reserved_crossing_source_samples,
            soft_repeated_crossing_samples,
            soft_repeated_crossing_source_samples,
            soft_reserved_crossing_samples,
            soft_reserved_crossing_source_samples,
        )
        raise RoutingError(
            f"cannot route {src_label} to {dst_label} on physical grid: "
            f"blocked by port access ({reason}){detail_message}"
            f"{_failure_counter_summary(port_access_blocks=sum(port_access_blocks.values()), window_blocks=window_blocks, same_net_blocks=same_net_blocks, crossing_candidate_blocks=crossing_candidate_blocks, crossing_budget_blocks=crossing_budget_blocks, hard_reserved_crossing_blocks=hard_reserved_crossing_blocks, soft_repeated_crossing_hits=soft_repeated_crossing_hits, soft_reserved_crossing_hits=soft_reserved_crossing_hits, reserved_spacing_blocks=reserved_spacing_blocks, segment_blocks=segment_blocks, pops=pops, max_heap=max_heap)}"
        )
    if crossing_budget_blocks:
        raise RoutingError(
            f"cannot route {src_label} to {dst_label} on physical grid: "
            f"blocked by crossing budget"
            f"{_crossing_budget_debug_detail(crossing_budget_block_samples, crossing_budget_source_samples)}"
            f"{_failure_counter_summary(port_access_blocks=sum(port_access_blocks.values()), window_blocks=window_blocks, same_net_blocks=same_net_blocks, crossing_candidate_blocks=crossing_candidate_blocks, crossing_budget_blocks=crossing_budget_blocks, hard_reserved_crossing_blocks=hard_reserved_crossing_blocks, soft_repeated_crossing_hits=soft_repeated_crossing_hits, soft_reserved_crossing_hits=soft_reserved_crossing_hits, reserved_spacing_blocks=reserved_spacing_blocks, segment_blocks=segment_blocks, pops=pops, max_heap=max_heap)}"
        )
    if window_blocks:
        raise RoutingError(
            f"cannot route {src_label} to {dst_label} on physical grid: blocked by route window"
            f"{_crossing_debug_details(crossing_budget_block_samples, crossing_budget_source_samples, hard_reserved_crossing_samples, hard_reserved_crossing_source_samples, soft_repeated_crossing_samples, soft_repeated_crossing_source_samples, soft_reserved_crossing_samples, soft_reserved_crossing_source_samples)}"
            f"{_failure_counter_summary(port_access_blocks=sum(port_access_blocks.values()), window_blocks=window_blocks, same_net_blocks=same_net_blocks, crossing_candidate_blocks=crossing_candidate_blocks, crossing_budget_blocks=crossing_budget_blocks, hard_reserved_crossing_blocks=hard_reserved_crossing_blocks, soft_repeated_crossing_hits=soft_repeated_crossing_hits, soft_reserved_crossing_hits=soft_reserved_crossing_hits, reserved_spacing_blocks=reserved_spacing_blocks, segment_blocks=segment_blocks, pops=pops, max_heap=max_heap)}"
        )
    raise RoutingError(
        f"cannot route {src_label} to {dst_label} on physical grid"
        f"{_crossing_debug_details(crossing_budget_block_samples, crossing_budget_source_samples, hard_reserved_crossing_samples, hard_reserved_crossing_source_samples, soft_repeated_crossing_samples, soft_repeated_crossing_source_samples, soft_reserved_crossing_samples, soft_reserved_crossing_source_samples)}"
        f"{_same_net_debug_details(same_net_block_samples)}"
        f"{_failure_counter_summary(port_access_blocks=sum(port_access_blocks.values()), window_blocks=window_blocks, same_net_blocks=same_net_blocks, crossing_candidate_blocks=crossing_candidate_blocks, crossing_budget_blocks=crossing_budget_blocks, hard_reserved_crossing_blocks=hard_reserved_crossing_blocks, soft_repeated_crossing_hits=soft_repeated_crossing_hits, soft_reserved_crossing_hits=soft_reserved_crossing_hits, reserved_spacing_blocks=reserved_spacing_blocks, segment_blocks=segment_blocks, pops=pops, max_heap=max_heap)}"
    )

def _same_net_debug_details(samples: list[str]) -> str:
    if not samples:
        return ""
    return "; same_net_samples=" + " | ".join(samples)

def _heuristic_cost(src: Point, dst: Point, rules: RoutingRules) -> float:
    if rules.cost_model == "db":
        return _manhattan(src, dst)
    if rules.loss_aware_cost:
        return _manhattan(src, dst) * max(0.0, rules.prop_loss_db_per_um)
    return _manhattan(src, dst)


def _target_junction_is_legal(
    state: RouterState,
    dst_label: str,
    bend_spacing_um: float,
) -> bool:
    continuation = _port_junction_orientation(dst_label, source=False)
    if not continuation:
        return True
    if not state.orientation:
        return False
    if continuation == opposite_orientation(state.orientation):
        return False
    if orientation_axis(continuation) == orientation_axis(state.orientation):
        return True
    return state.straight_run_um >= bend_spacing_um - EPS

def _step_length_cost(src: Point, dst: Point, rules: RoutingRules) -> float:
    length_um = _manhattan(src, dst)
    if rules.cost_model == "db":
        return length_um
    if rules.loss_aware_cost:
        return length_um * rules.prop_loss_db_per_um
    return length_um

def _crossing_step_cost(step_crossings: int, rules: RoutingRules) -> float:
    if rules.cost_model == "db":
        return step_crossings * _db_to_internal_um(
            rules.crossing_loss_db_per_cross,
            rules,
        )
    if rules.loss_aware_cost:
        return step_crossings * rules.crossing_loss_db_per_cross
    return step_crossings * rules.crossing_penalty_um

def _repeated_crossing_step_cost(
    rules: RoutingRules,
    repeated_crossing_penalty_scale: float,
) -> float:
    if rules.cost_model == "db":
        physical_loss = _db_to_internal_um(rules.repeated_crossing_loss_db, rules)
        guidance = rules.repeated_crossing_penalty_um * rules.db_tie_breaker_scale
        return (physical_loss + guidance) * repeated_crossing_penalty_scale
    if rules.loss_aware_cost:
        return rules.repeated_crossing_loss_db * repeated_crossing_penalty_scale
    return rules.repeated_crossing_penalty_um * repeated_crossing_penalty_scale

def _bend_step_cost(rules: RoutingRules) -> float:
    if rules.cost_model == "db":
        return (
            0.5 * pi * rules.bend_radius_um
            + _db_to_internal_um(rules.bend_loss_db_per_bend, rules)
        )
    if rules.loss_aware_cost:
        return rules.bend_loss_db_per_bend
    return 0.5 * pi * rules.bend_radius_um

def _jog_step_cost(rules: RoutingRules) -> float:
    if rules.cost_model == "db":
        return _db_to_internal_um(rules.jog_penalty_db, rules)
    if rules.loss_aware_cost:
        return rules.jog_penalty_db
    return 0.0

def _turn_timing_cost(
    bend_point: Point,
    src: Point,
    dst: Point,
    rules: RoutingRules,
) -> float:
    penalty_um = _turn_timing_penalty(bend_point, src, dst, rules)
    if penalty_um <= EPS:
        return 0.0
    if rules.cost_model == "db":
        if rules.physical_turn_guard:
            return penalty_um
        return penalty_um * rules.db_tie_breaker_scale
    if rules.loss_aware_cost:
        return rules.bend_placement_penalty_db
    return penalty_um

def _search_guidance_cost(penalty_um: float, rules: RoutingRules) -> float:
    if penalty_um <= EPS:
        return 0.0
    if rules.cost_model == "db":
        return penalty_um * rules.db_tie_breaker_scale
    if rules.loss_aware_cost:
        return penalty_um * rules.prop_loss_db_per_um
    return penalty_um


def _same_net_physical_cost(penalty_um: float, rules: RoutingRules) -> float:
    if penalty_um <= EPS:
        return 0.0
    if rules.cost_model == "db" and rules.physical_same_net_hairpin:
        return penalty_um
    return _search_guidance_cost(penalty_um, rules)

def _bend_placement_cost(
    bend_point: Point,
    obstacles: list[Obstacle],
    port_access: PortAccessLegality | None,
    rules: RoutingRules,
) -> float:
    if not rules.uses_db_cost:
        return 0.0
    if rules.turn_guard_um <= EPS:
        return 0.0
    if not _bend_near_sensitive_geometry(bend_point, obstacles, port_access, rules):
        return 0.0
    if rules.cost_model == "db":
        return _db_to_internal_um(rules.bend_placement_penalty_db, rules)
    return rules.bend_placement_penalty_db


def _db_to_internal_um(loss_db: float, rules: RoutingRules) -> float:
    if rules.prop_loss_db_per_um <= 0.0:
        raise ValueError("db cost_model requires positive prop_loss_db_per_um")
    return loss_db / rules.prop_loss_db_per_um

def _bend_near_sensitive_geometry(
    bend_point: Point,
    obstacles: list[Obstacle],
    port_access: PortAccessLegality | None,
    rules: RoutingRules,
) -> bool:
    guard = rules.turn_guard_um
    if any(_point_to_obstacle_distance(bend_point, obstacle) < guard - EPS for obstacle in obstacles):
        return True
    if port_access is None:
        return False
    for point in port_access.plan.points_by_cell_port.values():
        if (
            _manhattan(bend_point, point.port_xy) < guard - EPS
            or _manhattan(bend_point, point.stub_xy) < guard - EPS
            or _manhattan(bend_point, point.escape_xy) < guard - EPS
            or _manhattan(bend_point, point.route_xy) < guard - EPS
        ):
            return True
    return any(
        _point_to_axis_segment_distance(bend_point, region.segment) < guard - EPS
        for region in port_access.plan.reserved_regions
    )

def _point_to_obstacle_distance(point: Point, obstacle: Obstacle) -> float:
    x, y = point
    dx = max(obstacle.left - x, 0.0, x - obstacle.right)
    dy = max(obstacle.bottom - y, 0.0, y - obstacle.top)
    return max(dx, dy)

def _point_to_axis_segment_distance(point: Point, segment: Segment) -> float:
    (x0, y0), (x1, y1) = segment
    x, y = point
    if abs(x0 - x1) < EPS:
        y_lo, y_hi = sorted((y0, y1))
        if y_lo - EPS <= y <= y_hi + EPS:
            return abs(x - x0)
        return abs(x - x0) + min(abs(y - y0), abs(y - y1))
    if abs(y0 - y1) < EPS:
        x_lo, x_hi = sorted((x0, x1))
        if x_lo - EPS <= x <= x_hi + EPS:
            return abs(y - y0)
        return abs(y - y0) + min(abs(x - x0), abs(x - x1))
    return min(_manhattan(point, segment[0]), _manhattan(point, segment[1]))

def _turn_sign(previous_orientation: str, next_orientation: str) -> int:
    vectors = {
        "E": (1, 0),
        "W": (-1, 0),
        "N": (0, 1),
        "S": (0, -1),
    }
    prev = vectors.get(previous_orientation)
    nxt = vectors.get(next_orientation)
    if prev is None or nxt is None:
        return 0
    cross = prev[0] * nxt[1] - prev[1] * nxt[0]
    if cross > 0:
        return 1
    if cross < 0:
        return -1
    return 0

def _alternating_jog(previous_turns: tuple[int, ...], next_turn: int) -> bool:
    if next_turn == 0 or len(previous_turns) < 2:
        return False
    return previous_turns[-2] == next_turn and previous_turns[-1] == -next_turn

def _reserved_spacing_conflict(
    segment: Segment,
    blockers: list[Segment],
    rules: RoutingRules,
) -> bool:
    for blocker in blockers:
        if _segments_collinear_overlap(segment, blocker):
            return True
        if _parallel_spacing_violation(
            segment, blocker, rules.min_spacing_um, rules.waveguide_width_um
        ):
            return True
        if rules.uses_db_cost and _parallel_endpoint_spacing_violation(
            segment,
            blocker,
            _effective_spacing_threshold(
                rules.min_spacing_um, rules.waveguide_width_um
            ),
        ):
            return True
    return False

def _parallel_endpoint_spacing_violation(
    first: Segment,
    second: Segment,
    min_spacing_um: float,
) -> bool:
    if min_spacing_um <= EPS or _shared_endpoint(first, second) is not None:
        return False
    first_horizontal = abs(first[0][1] - first[1][1]) < EPS
    second_horizontal = abs(second[0][1] - second[1][1]) < EPS
    if first_horizontal != second_horizontal:
        return False
    return min(
        hypot(first_point[0] - second_point[0], first_point[1] - second_point[1])
        for first_point in first
        for second_point in second
    ) < min_spacing_um - EPS

def _reserved_guard_conflict(
    segment: Segment,
    blockers: list[Segment],
    rules: RoutingRules,
) -> bool:
    for blocker in blockers:
        if _segments_collinear_overlap(segment, blocker):
            return True
        if _parallel_spacing_violation(
            segment, blocker, rules.min_spacing_um, rules.waveguide_width_um
        ):
            return True
    return False

def _append_partial_segment(
    partial_segments: tuple[Segment, ...],
    segment: Segment,
) -> tuple[Segment, ...]:
    if not partial_segments:
        return (segment,)
    previous = partial_segments[-1]
    if (
        _same_point(previous[1], segment[0])
        and _direction(previous[0], previous[1]) == _direction(segment[0], segment[1])
        and _is_axis_aligned((previous[0], segment[1]))
    ):
        return partial_segments[:-1] + ((previous[0], segment[1]),)
    return partial_segments + (segment,)

def _soft_blocker_penalty(
    segment: Segment,
    soft_blockers: list[Segment],
    rules: RoutingRules,
) -> float:
    if not soft_blockers:
        return 0.0
    penalty = 0.0
    for blocker in soft_blockers:
        if _segments_collinear_overlap(segment, blocker):
            penalty += 8.0 * rules.hairpin_penalty_um
        elif _parallel_spacing_violation(
            segment, blocker, rules.min_spacing_um, rules.waveguide_width_um
        ):
            penalty += 4.0 * rules.hairpin_penalty_um
        elif _orthogonal_crossing_point(segment, blocker) is not None:
            penalty += 2.0 * rules.hairpin_penalty_um
        elif _segment_contact_point(segment, blocker) is not None:
            penalty += 2.0 * rules.hairpin_penalty_um
    return penalty

def _failure_counter_summary(
    *,
    port_access_blocks: int,
    window_blocks: int,
    same_net_blocks: int,
    crossing_candidate_blocks: int,
    crossing_budget_blocks: int,
    hard_reserved_crossing_blocks: int,
    soft_repeated_crossing_hits: int,
    soft_reserved_crossing_hits: int,
    reserved_spacing_blocks: int,
    segment_blocks: int,
    pops: int,
    max_heap: int,
) -> str:
    return (
        " counters="
        f"port_access:{port_access_blocks},"
        f"window:{window_blocks},"
        f"same_net:{same_net_blocks},"
        f"crossing_candidate:{crossing_candidate_blocks},"
        f"crossing_budget:{crossing_budget_blocks},"
        f"hard_reserved_crossing:{hard_reserved_crossing_blocks},"
        f"soft_repeated_crossing:{soft_repeated_crossing_hits},"
        f"soft_reserved_crossing:{soft_reserved_crossing_hits},"
        f"reserved_spacing:{reserved_spacing_blocks},"
        f"segment:{segment_blocks},"
        f"pops:{pops},"
        f"max_heap:{max_heap}"
    )

def _crossing_budget_debug_detail(
    samples: dict[tuple[int, int], CrossingCandidate],
    source_samples: dict[tuple[int, int], tuple[str, ...]] | None = None,
) -> str:
    return _crossing_owner_debug_detail(
        "blocked_by",
        "crossing_budget",
        samples,
        source_samples,
    )


def _crossing_debug_details(
    crossing_budget_samples: dict[tuple[int, int], CrossingCandidate],
    crossing_budget_sources: dict[tuple[int, int], tuple[str, ...]],
    hard_reserved_samples: dict[tuple[int, int], CrossingCandidate],
    hard_reserved_sources: dict[tuple[int, int], tuple[str, ...]],
    soft_repeated_samples: dict[tuple[int, int], CrossingCandidate],
    soft_repeated_sources: dict[tuple[int, int], tuple[str, ...]],
    soft_reserved_samples: dict[tuple[int, int], CrossingCandidate],
    soft_reserved_sources: dict[tuple[int, int], tuple[str, ...]],
) -> str:
    return (
        _crossing_budget_debug_detail(crossing_budget_samples, crossing_budget_sources)
        + _crossing_owner_debug_detail(
            "blocked_by",
            "reserved_crossing",
            hard_reserved_samples,
            hard_reserved_sources,
        )
        + _crossing_owner_debug_detail(
            "soft_repeat",
            "repeated_crossing",
            soft_repeated_samples,
            soft_repeated_sources,
        )
        + _crossing_owner_debug_detail(
            "soft_reserved",
            "reserved_crossing",
            soft_reserved_samples,
            soft_reserved_sources,
        )
    )


def _crossing_owner_debug_detail(
    label: str,
    reason: str,
    samples: dict[tuple[int, int], CrossingCandidate],
    source_samples: dict[tuple[int, int], tuple[str, ...]] | None = None,
) -> str:
    if not samples:
        return ""
    pair = sorted(samples)[0]
    candidate = samples[pair]
    sources = (source_samples or {}).get(pair, ())
    source = f" source={sources[0]}" if sources else ""
    return (
        f"; {label}=I{candidate.crossed_input}/{reason} "
        f"pair=I{pair[0]}-I{pair[1]} "
        f"at=({candidate.location[0]:.3f},{candidate.location[1]:.3f})"
        f"{source}"
    )

def _format_crossing_source(candidate: CrossingCandidate) -> str:
    return (
        f"I{candidate.owner_input}->I{candidate.crossed_input}"
        f"@({candidate.location[0]:.3f},{candidate.location[1]:.3f})"
        f":{_format_segment_debug(candidate.segment)}"
        f"x{_format_segment_debug(candidate.crossed_segment)}"
    )

def _crossing_signature(
    counts: dict[tuple[int, int], int],
    base_counts: dict[tuple[int, int], int],
) -> tuple[tuple[int, int], ...]:
    """Pairs whose budget this A* hop has consumed beyond committed history."""
    return tuple(
        sorted(
            pair
            for pair, count in counts.items()
            if count > base_counts.get(pair, 0)
        )
    )

def _build_route_grid(
    x_tracks: tuple[float, ...],
    y_tracks: tuple[float, ...],
    blockers: list[Segment],
    obstacles: list[Obstacle],
    port_access: PortAccessLegality | None,
    rules: RoutingRules,
    *,
    occupied_segments: list[OccupiedRouteSegment] | None = None,
) -> RouteGrid:
    grid = RouteGrid(x_tracks, y_tracks)
    for obstacle in obstacles:
        grid.mark_obstacle(obstacle)
    port_segments: list[Segment] = []
    if port_access is not None:
        for region in port_access.plan.reserved_regions:
            grid.mark_port_access(region)
            port_segments.append(region.segment)
    if occupied_segments is not None:
        waveguides = [
            occupied
            for occupied in occupied_segments
            if occupied.kind == "external" and not any(
                _same_segment(occupied.segment, port_segment)
                for port_segment in port_segments
            )
        ]
        for owner_input, segments in _merged_owned_segments(waveguides).items():
            grid.mark_route(owner_input, segments)
    else:
        waveguide_blockers = [
            segment
            for segment in blockers
            if not any(_same_segment(segment, port_segment) for port_segment in port_segments)
        ]
        grid.mark_route(owner_input=-1, segments=waveguide_blockers)
    if rules.min_crossing_clearance_um is not None:
        grid.register_crossing_footprints(rules.min_crossing_clearance_um)
    return grid


def _merged_owned_segments(
    occupied_segments: list[OccupiedRouteSegment],
) -> dict[int, tuple[Segment, ...]]:
    by_owner: dict[int, list[Segment]] = {}
    for occupied in occupied_segments:
        if occupied.owner_input is None:
            continue
        by_owner.setdefault(occupied.owner_input, []).append(occupied.segment)
    return {
        owner: _merge_connected_collinear_segments(segments)
        for owner, segments in by_owner.items()
    }


def _merge_connected_collinear_segments(segments: list[Segment]) -> tuple[Segment, ...]:
    merged: list[Segment] = []
    for segment in segments:
        if not merged:
            merged.append(segment)
            continue
        previous = merged[-1]
        if (
            _same_point(previous[1], segment[0])
            and _direction(previous[0], previous[1]) == _direction(segment[0], segment[1])
        ):
            merged[-1] = (previous[0], segment[1])
        else:
            merged.append(segment)
    return tuple(merged)


def _point_in_crossing_footprint(point: Point, footprint: CrossingFootprint) -> bool:
    half = 0.5 * footprint.side_um
    return (
        abs(point[0] - footprint.location[0]) <= half + EPS
        and abs(point[1] - footprint.location[1]) <= half + EPS
    )

def _route_segment_available(
    segment: Segment,
    blockers: list[Segment],
    obstacles: list[Obstacle],
    rules: RoutingRules,
    forbidden_points: set[Point] | None = None,
    allowed_touch_points: tuple[Point, ...] = (),
    port_access: PortAccessLegality | None = None,
) -> bool:
    if not _is_axis_aligned(segment):
        return False
    if forbidden_points and _segment_hits_forbidden_point(
        segment,
        forbidden_points,
        allowed_touch_points,
    ):
        return False
    for obstacle in obstacles:
        if _segment_intersects_obstacle(segment, obstacle):
            return False
    for blocker in blockers:
        if _segments_collinear_overlap(segment, blocker):
            return False
        if _parallel_spacing_violation(
            segment, blocker, rules.min_spacing_um, rules.waveguide_width_um
        ):
            return False
        if not rules.allow_crossings and _orthogonal_crossing_point(segment, blocker):
            return False
    # Owner-aware port access: reject overlap/spacing/crossing of any reserved
    # corridor and a non-owner net's T-touch/end-block of another net's corridor.
    # Returns a structured reason so the caller can name a port-access violation
    # instead of a generic blocker failure.
    if port_access_conflict(
        segment,
        port_access,
        rules,
        allowed_touch_points=allowed_touch_points,
    ) is not None:
        return False
    return True

def _append_external(
    points: list[Point],
    external_segments: list[Segment],
    new_points: tuple[Point, ...],
) -> None:
    before = len(points)
    _append_points(points, new_points)
    start_idx = max(0, before - 1)
    for start, end in zip(points[start_idx:], points[start_idx + 1 :]):
        if not _same_point(start, end):
            external_segments.append((start, end))

def _append_local_axis(
    points: list[Point],
    start: Point,
    end: Point,
    blockers: list[Segment],
    local_segments: list[Segment],
    rules: RoutingRules,
) -> None:
    if _same_point(start, end):
        return
    segment = (start, end)
    if not _is_axis_aligned(segment):
        raise RoutingError(f"non-Manhattan port escape {start} -> {end}")
    for blocker in blockers:
        if _segments_collinear_overlap(segment, blocker):
            raise RoutingError(f"port escape overlaps existing route: {start} -> {end}")
        if _parallel_spacing_violation(
            segment, blocker, rules.min_spacing_um, rules.waveguide_width_um
        ):
            raise RoutingError(f"port escape violates spacing: {start} -> {end}")
        contact = _segment_contact_point(segment, blocker)
        if contact is not None and not (_same_point(contact, start) or _same_point(contact, end)):
            raise RoutingError(f"port escape touches existing route: {start} -> {end}")
    local_segments.append(segment)
    _append_points(points, (start, end))

def _routing_grid(
    paths: list[Path],
    cells: dict[str, MRRCell],
    obstacles: list[Obstacle],
    rules: RoutingRules,
    *,
    x_start: float,
    x_end: float,
    wire_pitch_um: float,
    port_stub_um: float,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    n_physical = _infer_n_physical(cells, paths)
    x_values = [x_start, x_end]
    y_values = [_wire_y(wire, n_physical, wire_pitch_um) for wire in range(n_physical)]
    for cell in cells.values():
        for port in cell.ports:
            route_point = _port_route_point(cell, port, rules, port_stub_um)
            x_values.append(route_point[0])
            y_values.append(route_point[1])
    for obstacle in obstacles:
        x_values.extend((
            obstacle.left - rules.grid_pitch_um,
            obstacle.left,
            obstacle.right,
            obstacle.right + rules.grid_pitch_um,
        ))
        y_values.extend((
            obstacle.bottom - rules.grid_pitch_um,
            obstacle.bottom,
            obstacle.top,
            obstacle.top + rules.grid_pitch_um,
        ))
    margin = max(
        rules.grid_margin_tracks * rules.grid_pitch_um,
        rules.port_escape_um + rules.mrr_keepout_um + 12.0,
    )
    x_min = min(x_values) - margin
    x_max = max(x_values) + margin
    y_min = min(y_values) - margin
    y_max = max(y_values) + margin
    x_tracks = _tracks_between(
        x_min,
        x_max,
        rules.grid_pitch_um,
        x_values,
        merge_tol_um=rules.grid_merge_tol_um,
    )
    y_tracks = _tracks_between(
        y_min,
        y_max,
        rules.grid_pitch_um,
        y_values,
        merge_tol_um=rules.grid_merge_tol_um,
    )
    return x_tracks, y_tracks

def _tracks_between(
    lower: float,
    upper: float,
    pitch: float,
    exact_values: list[float],
    *,
    merge_tol_um: float = 0.0,
) -> tuple[float, ...]:
    start = floor(lower / pitch) * pitch
    stop = ceil(upper / pitch) * pitch
    exact = {_canonical_track(value) for value in exact_values}
    values = set(exact)
    count = int(round((stop - start) / pitch))
    for idx in range(count + 1):
        values.add(_canonical_track(start + idx * pitch))
    return _merge_nearby_tracks(tuple(sorted(values)), exact, merge_tol_um)

def _merge_nearby_tracks(
    tracks: tuple[float, ...],
    exact_values: set[float],
    merge_tol_um: float,
) -> tuple[float, ...]:
    if merge_tol_um <= EPS or len(tracks) <= 1:
        return tracks
    clusters: list[list[float]] = [[tracks[0]]]
    for track in tracks[1:]:
        if track - clusters[-1][-1] < merge_tol_um - EPS:
            clusters[-1].append(track)
        else:
            clusters.append([track])
    merged: list[float] = []
    for cluster in clusters:
        exact_in_cluster = [value for value in cluster if value in exact_values]
        representative = exact_in_cluster[0] if exact_in_cluster else cluster[0]
        merged.append(representative)
    return tuple(merged)

def _insert_track(
    tracks: tuple[float, ...],
    *values: float,
) -> tuple[float, ...]:
    merged = set(tracks)
    for value in values:
        merged.add(_canonical_track(value))
    return tuple(sorted(merged))

def _grid_neighbors(
    node: tuple[int, int],
    x_tracks: tuple[float, ...],
    y_tracks: tuple[float, ...],
) -> tuple[tuple[tuple[int, int], str], ...]:
    x_idx, y_idx = node
    neighbors: list[tuple[tuple[int, int], str]] = []
    if x_idx > 0:
        neighbors.append(((x_idx - 1, y_idx), "h"))
    if x_idx + 1 < len(x_tracks):
        neighbors.append(((x_idx + 1, y_idx), "h"))
    if y_idx > 0:
        neighbors.append(((x_idx, y_idx - 1), "v"))
    if y_idx + 1 < len(y_tracks):
        neighbors.append(((x_idx, y_idx + 1), "v"))
    return tuple(neighbors)

def _reconstruct_astar_path(
    state: RouterState,
    prev: dict[RouterState, tuple[RouterState, Point]],
    x_tracks: tuple[float, ...],
    y_tracks: tuple[float, ...],
) -> tuple[Point, ...]:
    points = [(x_tracks[state.x_idx], y_tracks[state.y_idx])]
    while state in prev:
        prev_state, point = prev[state]
        points.append(point)
        state = prev_state
    points.reverse()
    return _merge_collinear_points(tuple(points))

def _reconstruct_astar_path_from_keys(
    state_key: tuple[RouterState, tuple[tuple[int, int], ...]],
    prev: dict[
        tuple[RouterState, tuple[tuple[int, int], ...]],
        tuple[tuple[RouterState, tuple[tuple[int, int], ...]], Point],
    ],
    x_tracks: tuple[float, ...],
    y_tracks: tuple[float, ...],
) -> tuple[Point, ...]:
    state, _signature = state_key
    points = [(x_tracks[state.x_idx], y_tracks[state.y_idx])]
    while state_key in prev:
        prev_key, point = prev[state_key]
        points.append(point)
        state_key = prev_key
    points.reverse()
    return _merge_collinear_points(tuple(points))
