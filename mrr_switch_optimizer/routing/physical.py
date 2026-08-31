from __future__ import annotations

from dataclasses import dataclass, replace
from math import pi
import re
from typing import TYPE_CHECKING

from ..core.models import DEFAULT_CELL_GEOMETRY
from . import grid as _grid_module
from .drc import validate_physical_routes, _validate_rules
from .geometry import (
    _append_points,
    _bend_count,
    _candidate_routes,
    _dedupe_points,
    _direction,
    _effective_spacing_threshold,
    _infer_n_physical,
    _inflate_obstacle,
    _intervals_overlap,
    _is_axis_aligned,
    _manhattan,
    _merge_collinear_points,
    _orthogonal_crossing_point,
    _parallel_spacing_violation,
    _point_on_segment,
    _polyline_length,
    _routing_obstacle,
    _same_point,
    _same_segment,
    _segment_contact_point,
    _segment_crossing_count,
    _segment_hits_forbidden_point,
    _segment_intersects_obstacle,
    _segment_midpoint,
    _segments_collinear_overlap,
    _wire_y,
)
from .grid import (
    _append_external,
    _astar_route,
    _canonical_track,
    _format_segment_debug as _format_segment,
    _point_to_axis_segment_distance,
    _point_to_obstacle_distance,
    _route_segment_available,
    _routing_grid,
    _same_net_reason_is_hairpin,
    _same_net_self_conflict_reason,
    _same_net_touching_corner_reason,
    _turn_sign,
)
from .grid import _reserved_spacing_conflict
from .grid_router import HistoryCost, RouterState, opposite_orientation, orientation_axis
from .port_access import (
    PortAccessLegality,
    PortAccessPlan,
    _mrr_internal_points,
    _port_escape_point,
    _port_junction_orientation,
    _port_route_point,
    _port_stub_point,
    build_port_access_plan,
    hop_crossing_class,
    port_side,
)
from .port_access import port_access_conflict_detail
from .refinement import (
    _braid_pair_count,
    _route_crossings,
    _route_with_crossing_count,
    _routing_candidate_score,
)
from .types import (
    BEND_RADIUS_UM,
    DRCViolation,
    EPS,
    FailedNet,
    Obstacle,
    OccupiedRouteSegment,
    PhysicalRoute,
    PhysicalRoutingResult,
    Point,
    RouteCrossing,
    RoutingError,
    RoutingRules,
    RoutingStats,
    RoutingWindow,
    Segment,
)

if TYPE_CHECKING:
    from ..core.models import MRRCell
    from ..core.topology import Path, RouteStep

__all__ = [
    'route_physical_paths',
    'route_physical_design',
    '_corridor_preferred_x',
    '_corridor_key',
    '_crossing_budget_owners',
    '_crossing_sources',
    '_crossing_count_by_pair_tuple',
    '_failure_owner_components',
    '_record_failed_crossing_sources',
    '_owner_crossing_sources_for_current_net',
    '_source_reserved_crossing_owners',
    '_source_detour_candidates',
    '_port_approach_point',
    '_routing_grid_bounds',
    '_io_clamped_routing_bounds',
    '_hop_routing_window',
    '_route_physical_order',
    '_route_one_path',
    '_route_external_forbidden_points',
    '_ripup_order',
    '_path_routing_complexity',
    '_path_endpoint_span',
]

def route_physical_paths(
    paths: list[Path],
    cells: dict[str, MRRCell],
    *,
    port_stub_um: float = 2.0,
    x_start: float = 20.0,
    x_end: float | None = None,
    track_pitch_um: float = 8.0,
    wire_pitch_um: float = 36.0,
    max_detour_tracks: int = 64,
    max_stage: int | None = None,
    waveguide_width_um: float = DEFAULT_CELL_GEOMETRY.waveguide_width_um,
) -> list[PhysicalRoute]:
    """Route all active paths as waveguide-aware Manhattan polylines."""
    if not paths:
        return []
    if port_stub_um <= 0.0:
        raise ValueError("port_stub_um must be positive")
    if track_pitch_um <= 0.0:
        raise ValueError("track_pitch_um must be positive")
    if max_detour_tracks < 0:
        raise ValueError("max_detour_tracks must be non-negative")
    rules = RoutingRules(
        grid_pitch_um=track_pitch_um,
        bend_radius_um=BEND_RADIUS_UM,
        crossing_penalty_um=0.0,
        max_ripup_passes=0,
        waveguide_width_um=waveguide_width_um,
    )
    result = route_physical_design(
        paths,
        cells,
        rules=rules,
        port_stub_um=port_stub_um,
        x_start=x_start,
        x_end=x_end,
        wire_pitch_um=wire_pitch_um,
        max_stage=max_stage,
    )
    if result.failed_nets:
        failed = result.failed_nets[0]
        raise RoutingError(
            f"cannot route I{failed.input_port}->O{failed.output_port}: {failed.message}"
        )
    return list(result.routes)

def route_physical_design(
    paths: list[Path],
    cells: dict[str, MRRCell],
    rules: RoutingRules = RoutingRules(),
    *,
    port_stub_um: float = 2.0,
    x_start: float = 20.0,
    x_end: float | None = None,
    wire_pitch_um: float = 36.0,
    max_stage: int | None = None,
    preferred_input_order: tuple[int, ...] | None = None,
    same_net_whole_net_reroute: bool = False,
    remediation_events_out: list[tuple[int, int]] | None = None,
    remediation_attempts_out: list[dict[str, object]] | None = None,
) -> PhysicalRoutingResult:
    token = _grid_module._start_astar_call_tracking()
    try:
        result = _route_physical_design_impl(
            paths,
            cells,
            rules=rules,
            port_stub_um=port_stub_um,
            x_start=x_start,
            x_end=x_end,
            wire_pitch_um=wire_pitch_um,
            max_stage=max_stage,
            preferred_input_order=preferred_input_order,
            same_net_whole_net_reroute=same_net_whole_net_reroute,
            remediation_events_out=remediation_events_out,
            remediation_attempts_out=remediation_attempts_out,
        )
        astar_calls = _grid_module._stop_astar_call_tracking(token)
    except Exception:
        _grid_module._stop_astar_call_tracking(token)
        raise
    return replace(result, stats=replace(result.stats, astar_calls=astar_calls))


def _route_physical_design_impl(
    paths: list[Path],
    cells: dict[str, MRRCell],
    rules: RoutingRules = RoutingRules(),
    *,
    port_stub_um: float = 2.0,
    x_start: float = 20.0,
    x_end: float | None = None,
    wire_pitch_um: float = 36.0,
    max_stage: int | None = None,
    preferred_input_order: tuple[int, ...] | None = None,
    same_net_whole_net_reroute: bool = False,
    remediation_events_out: list[tuple[int, int]] | None = None,
    remediation_attempts_out: list[dict[str, object]] | None = None,
) -> PhysicalRoutingResult:
    """Route a design and return routed geometry, DRC, failures, and crossings.

    The physical router leaves topology and state assignment untouched. It only
    materializes the already-active paths on Manhattan grid tracks with MRR
    keepout, waveguide spacing, port escape, and rip-up retry.
    """
    _validate_rules(rules)
    if not paths:
        return PhysicalRoutingResult((), (), (), (), rules)
    if port_stub_um <= 0.0:
        raise ValueError("port_stub_um must be positive")
    if x_end is None:
        x_end = max(c.center[0] for c in cells.values()) + 70.0

    n_physical_early = _infer_n_physical(cells, paths)
    if preferred_input_order is not None:
        actual_inputs = {path.input_port for path in paths}
        if len(preferred_input_order) != len(set(preferred_input_order)):
            raise ValueError("preferred_input_order contains duplicate inputs")
        if set(preferred_input_order) != actual_inputs:
            raise ValueError(
                "preferred_input_order must contain exactly the routed inputs"
            )
        input_rank = {
            input_port: rank for rank, input_port in enumerate(preferred_input_order)
        }
        ordered_paths = sorted(paths, key=lambda path: input_rank[path.input_port])
    elif rules.max_ripup_passes == 0:
        ordered_paths = sorted(paths, key=lambda item: item.input_port)
    else:
        ordered_paths = sorted(
            paths,
            key=lambda path: _path_routing_complexity(
                path,
                n_physical_early,
                wire_pitch_um,
            ),
        )
    attempts: list[list[Path]] = [ordered_paths]
    best_routes: tuple[PhysicalRoute, ...] = ()
    best_failed: tuple[FailedNet, ...] = ()
    best_violations: tuple[DRCViolation, ...] = ()
    best_score: tuple[float, ...] | None = None
    failed_inputs: set[int] = set()
    direct_failed_inputs: set[int] = set()
    reroute_priority: tuple[int, ...] = ()
    reroute_components: tuple[frozenset[int], ...] = ()
    forbidden_points_by_input: dict[int, set[Point]] = {}
    crossing_sources_by_input: dict[int, set[_CrossingSource]] = {}
    previous_routes: tuple[PhysicalRoute, ...] = ()
    passes_executed = 0
    early_stopped = False
    stagnant_passes = 0

    for pass_idx in range(max(1, rules.max_ripup_passes + 1)):
        passes_executed = pass_idx + 1
        if pass_idx > 0:
            attempts.append(
                _ripup_order(
                    ordered_paths,
                    failed_inputs,
                    pass_idx,
                    n_physical_early,
                    wire_pitch_um,
                )
            )
        order = attempts[-1]
        reroute_inputs = (
            _bounded_reroute_inputs(
                order,
                direct_failed_inputs=direct_failed_inputs,
                related_inputs=failed_inputs,
                priority_inputs=reroute_priority,
                components=reroute_components,
                budget=rules.ripup_route_budget,
            )
            if pass_idx > 0
            else set()
        )
        if pass_idx > 0 and reroute_inputs:
            order = _prioritized_reroute_order(
                [path for path in order if path.input_port in reroute_inputs],
                reroute_priority,
            )
        fixed_routes = (
            tuple(route for route in previous_routes if route.input_port not in reroute_inputs)
            if pass_idx > 0
            else ()
        )
        pass_rules = (
            replace(rules, max_astar_pops=rules.ripup_max_astar_pops)
            if pass_idx > 0 and rules.ripup_max_astar_pops is not None
            else rules
        )
        pass_candidates = (
            _ripup_order_candidates(
                order,
                direct_failed_inputs=direct_failed_inputs,
                related_inputs=reroute_inputs,
                priority_inputs=reroute_priority,
                limit=rules.ripup_order_candidate_limit,
            )
            if pass_idx > 0 and reroute_inputs
            else (order,)
        )
        pass_result: tuple[
            tuple[PhysicalRoute, ...],
            tuple[FailedNet, ...],
            tuple[DRCViolation, ...],
        ] | None = None
        pass_score: tuple[float, ...] | None = None
        for candidate_order in pass_candidates:
            candidate_routes, candidate_failed = _route_physical_order(
                candidate_order,
                paths=paths,
                cells=cells,
                rules=pass_rules,
                port_stub_um=port_stub_um,
                x_start=x_start,
                x_end=x_end,
                wire_pitch_um=wire_pitch_um,
                max_stage=max_stage,
                forbidden_points_by_input=forbidden_points_by_input,
                crossing_sources_by_input=crossing_sources_by_input,
                ripup_pass_idx=pass_idx,
                fixed_routes=fixed_routes,
                same_net_whole_net_reroute=same_net_whole_net_reroute,
                remediation_events_out=remediation_events_out,
                remediation_attempts_out=remediation_attempts_out,
            )
            candidate_violations = validate_physical_routes(candidate_routes, cells, pass_rules)
            candidate_score = _pass_candidate_score(
                candidate_routes,
                candidate_failed,
                candidate_violations,
            )
            if pass_score is None or candidate_score < pass_score:
                pass_score = candidate_score
                pass_result = (candidate_routes, candidate_failed, candidate_violations)
            if not candidate_failed and not candidate_violations:
                break
        assert pass_result is not None
        routes, failed, pass_violations = pass_result
        previous_routes = tuple(routes)
        if not failed:
            crossings = _route_crossings(routes)
            routes = tuple(
                _route_with_crossing_count(route, crossings)
                for route in sorted(routes, key=lambda item: item.input_port)
            )
            violations = validate_physical_routes(routes, cells, rules)
            score = _routing_candidate_score(routes, violations, crossings, ())
            improved = best_score is None or score < best_score
            if improved:
                best_score = score
                best_routes = routes
                best_failed = ()
                best_violations = violations
            if not violations or pass_idx >= rules.max_ripup_passes:
                break
            stagnant_passes = 0 if improved else stagnant_passes + 1
            if 0 < rules.early_stop_stagnant_passes <= stagnant_passes:
                early_stopped = True
                break
            direct_failed_inputs = {route.input_port for route in routes}
            failed_inputs = set(direct_failed_inputs)
            reroute_priority = tuple(sorted(direct_failed_inputs))
            reroute_components = ()
            continue
        score = _pass_candidate_score(routes, failed, pass_violations)
        improved = best_score is None or score < best_score
        if improved:
            best_score = score
            best_routes = tuple(sorted(routes, key=lambda item: item.input_port))
            best_failed = tuple(failed)
            best_violations = tuple(pass_violations)
        stagnant_passes = 0 if improved else stagnant_passes + 1
        if 0 < rules.early_stop_stagnant_passes <= stagnant_passes:
            early_stopped = True
            break
        _record_failed_source_points(forbidden_points_by_input, failed)
        _record_failed_crossing_sources(crossing_sources_by_input, failed)
        violation_inputs = _violation_inputs(pass_violations)
        direct_failed_inputs = {
            failed_net.input_port for failed_net in failed
        } | violation_inputs
        failed_inputs = _failed_related_inputs(failed) | violation_inputs
        reroute_priority = _merge_priority_inputs(
            _failed_reroute_priority(failed, ordered_paths),
            violation_inputs,
        )
        reroute_components = _failure_owner_components(failed)

    if best_failed and rules.route_order_beam_width > 0:
        beam_rules = (
            replace(rules, max_astar_pops=rules.route_order_beam_max_astar_pops)
            if rules.route_order_beam_max_astar_pops is not None
            else rules
        )
        beam_routes, beam_failed = _route_order_beam(
            ordered_paths,
            paths=paths,
            cells=cells,
            rules=beam_rules,
            port_stub_um=port_stub_um,
            x_start=x_start,
            x_end=x_end,
            wire_pitch_um=wire_pitch_um,
            max_stage=max_stage,
            beam_width=rules.route_order_beam_width,
        )
        beam_violations = validate_physical_routes(beam_routes, cells, beam_rules)
        beam_score = _pass_candidate_score(beam_routes, beam_failed, beam_violations)
        if best_score is None or beam_score < best_score:
            best_score = beam_score
            best_routes = tuple(sorted(beam_routes, key=lambda item: item.input_port))
            best_failed = tuple(beam_failed)
            best_violations = tuple(beam_violations)

    violations = best_violations
    crossings = _route_crossings(best_routes)
    best_routes = tuple(
        _route_with_crossing_count(route, crossings)
        for route in sorted(best_routes, key=lambda item: item.input_port)
    )
    return PhysicalRoutingResult(
        best_routes,
        violations,
        best_failed,
        crossings,
        rules,
        _crossing_count_by_pair_tuple(crossings),
        stats=RoutingStats(
            ripup_passes_executed=passes_executed,
            early_stopped=early_stopped,
        ),
    )


def _crossing_count_by_pair_tuple(
    crossings: tuple[RouteCrossing, ...],
) -> tuple[tuple[tuple[int, int], int], ...]:
    counts: dict[tuple[int, int], int] = {}
    for crossing in crossings:
        pair = _owner_pair(crossing.net_a, crossing.net_b)
        counts[pair] = counts.get(pair, 0) + 1
    return tuple(sorted(counts.items()))


def _owner_pair(a: int, b: int) -> tuple[int, int]:
    low, high = sorted((a, b))
    return low, high

_RELATED_FAILED_INPUT_RE = re.compile(r"(?:blocked_by|region)=I(\d+)")
_CROSSING_BUDGET_OWNER_RE = re.compile(r"blocked_by=I(\d+)/crossing_budget")
_RESERVED_OWNERS_RE = re.compile(r"reserved_owners=([^:;]+)")
_CROSSING_SOURCE_POINT_RE = re.compile(r"(?:candidate_)?source=I\d+->I\d+@\((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\)")
_FLOAT_PATTERN = r"(-?\d+(?:\.\d+)?)"
_CROSSING_SOURCE_RE = re.compile(
    rf"((?:candidate_)?source)=I(\d+)->I(\d+)@"
    rf"\({_FLOAT_PATTERN},{_FLOAT_PATTERN}\):"
    rf"\({_FLOAT_PATTERN},{_FLOAT_PATTERN}\)->"
    rf"\({_FLOAT_PATTERN},{_FLOAT_PATTERN}\)x"
    rf"\({_FLOAT_PATTERN},{_FLOAT_PATTERN}\)->"
    rf"\({_FLOAT_PATTERN},{_FLOAT_PATTERN}\)"
)

@dataclass(frozen=True)
class _CrossingSource:
    owner_input: int
    crossed_input: int
    point: Point
    source_segment: Segment
    crossed_segment: Segment
    is_candidate: bool = False

def _failed_related_inputs(failed: tuple[FailedNet, ...] | list[FailedNet]) -> set[int]:
    inputs = {failed_net.input_port for failed_net in failed}
    for failed_net in failed:
        inputs.update(_reserved_owner_inputs(failed_net.message))
        for match in _RELATED_FAILED_INPUT_RE.finditer(failed_net.message):
            related_owner = int(match.group(1))
            if (
                related_owner in _crossing_budget_owners(failed_net.message)
                and "candidate_source=" in failed_net.message
            ):
                continue
            inputs.add(related_owner)
        for source in _crossing_sources(failed_net.message):
            if source.is_candidate:
                continue
            inputs.add(source.owner_input)
            inputs.add(source.crossed_input)
    return inputs

def _failure_owner_components(
    failed: tuple[FailedNet, ...] | list[FailedNet],
) -> tuple[frozenset[int], ...]:
    """Connected owner groups implied by a failed routing pass.

    These components define the smallest coherent rip-up islands. Cutting one in
    half usually preserves the exact fixed geometry that caused the failure, so
    bounded reroute treats direct-failure components as soft-budget units.
    """
    graph: dict[int, set[int]] = {}

    def connect(left: int, right: int) -> None:
        graph.setdefault(left, set()).add(right)
        graph.setdefault(right, set()).add(left)

    for failed_net in failed:
        owner = failed_net.input_port
        graph.setdefault(owner, set())
        candidate_budget_owners = (
            _crossing_budget_owners(failed_net.message)
            if "deterministic hop candidate exceeds crossing budget" in failed_net.message
            else set()
        )
        for match in _RELATED_FAILED_INPUT_RE.finditer(failed_net.message):
            related_owner = int(match.group(1))
            if related_owner in candidate_budget_owners:
                continue
            connect(owner, related_owner)
        for reserved_owner in _reserved_owner_inputs(failed_net.message):
            connect(owner, reserved_owner)
        for source in _crossing_sources(failed_net.message):
            if source.is_candidate:
                continue
            if (
                source.owner_input == owner
                and source.crossed_input in candidate_budget_owners
            ):
                continue
            connect(owner, source.owner_input)
            connect(owner, source.crossed_input)
            connect(source.owner_input, source.crossed_input)

    components: list[frozenset[int]] = []
    seen: set[int] = set()
    for owner in sorted(graph):
        if owner in seen:
            continue
        stack = [owner]
        component: set[int] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(graph.get(current, set()) - component)
        seen.update(component)
        components.append(frozenset(component))
    return tuple(sorted(components, key=lambda item: (min(item), len(item), tuple(sorted(item)))))

def _record_failed_source_points(
    forbidden_points_by_input: dict[int, set[Point]],
    failed: tuple[FailedNet, ...] | list[FailedNet],
) -> None:
    # Failed crossing provenance is kept in crossing_sources/history cost. Do
    # not promote those source points into hard forbidden points; a stale failed
    # attempt can otherwise block the only viable rip-up corridor.
    return

def _bump_failed_source_history(
    history_cost: HistoryCost,
    sources: set[_CrossingSource],
    points: set[Point],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
    *,
    attempt_idx: int,
) -> None:
    amount = rules.hairpin_penalty_um * float(attempt_idx + 1)
    x_tracks, y_tracks = grid
    for source in sources:
        history_cost.bump_segment(source.source_segment, x_tracks, y_tracks, amount)
        history_cost.bump_segment(source.crossed_segment, x_tracks, y_tracks, 0.5 * amount)
        _bump_history_point_neighborhood(history_cost, source.point, grid, amount)
    for point in points:
        _bump_history_point_neighborhood(history_cost, point, grid, amount)

def _bump_history_point_neighborhood(
    history_cost: HistoryCost,
    point: Point,
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    amount: float,
) -> None:
    x_tracks, y_tracks = grid
    if not x_tracks or not y_tracks:
        return
    x_idx = min(range(len(x_tracks)), key=lambda idx: abs(x_tracks[idx] - point[0]))
    y_idx = min(range(len(y_tracks)), key=lambda idx: abs(y_tracks[idx] - point[1]))
    for neighbor_x, neighbor_y in (
        (x_idx, y_idx),
        (x_idx - 1, y_idx),
        (x_idx + 1, y_idx),
        (x_idx, y_idx - 1),
        (x_idx, y_idx + 1),
    ):
        if neighbor_x < 0 or neighbor_x >= len(x_tracks):
            continue
        if neighbor_y < 0 or neighbor_y >= len(y_tracks):
            continue
        for orientation in ("N", "S", "E", "W"):
            history_cost.bump_state(
                RouterState(neighbor_x, neighbor_y, orientation),
                amount,
            )

def _record_failed_crossing_sources(
    crossing_sources_by_input: dict[int, set[_CrossingSource]],
    failed: tuple[FailedNet, ...] | list[FailedNet],
) -> None:
    for failed_net in failed:
        sources = set(_crossing_sources(failed_net.message))
        if not sources:
            continue
        crossing_sources_by_input.setdefault(failed_net.input_port, set()).update(sources)
        for source in sources:
            crossing_sources_by_input.setdefault(source.owner_input, set()).add(source)
            crossing_sources_by_input.setdefault(source.crossed_input, set()).add(
                _mirrored_crossing_source(source)
            )

def _mirrored_crossing_source(source: _CrossingSource) -> _CrossingSource:
    return _CrossingSource(
        owner_input=source.crossed_input,
        crossed_input=source.owner_input,
        point=source.point,
        source_segment=source.crossed_segment,
        crossed_segment=source.source_segment,
        is_candidate=source.is_candidate,
    )

def _active_crossing_sources_for_fixed_routes(
    sources: set[_CrossingSource],
    fixed_inputs: set[int],
) -> set[_CrossingSource]:
    """Keep only historical sources whose crossed route is still fixed."""
    if not sources or not fixed_inputs:
        return set()
    return {source for source in sources if source.crossed_input in fixed_inputs}

def _owner_crossing_sources_for_current_net(
    sources: set[_CrossingSource],
    owner_input: int,
) -> set[_CrossingSource]:
    """Keep pair-allocation hints even when the crossed owner is moving."""
    return {source for source in sources if source.owner_input == owner_input}

def _filtered_source_forbidden_points(
    forbidden_points: set[Point],
    all_sources: set[_CrossingSource],
    active_sources: set[_CrossingSource],
) -> set[Point]:
    if not all_sources:
        return set(forbidden_points)
    historical_source_points = {source.point for source in all_sources}
    active_source_points = {source.point for source in active_sources}
    return (set(forbidden_points) - historical_source_points) | active_source_points

def _crossing_budget_owners(message: str) -> set[int]:
    return {int(match.group(1)) for match in _CROSSING_BUDGET_OWNER_RE.finditer(message)}

def _reserved_owner_inputs(message: str) -> set[int]:
    owners: set[int] = set()
    for match in _RESERVED_OWNERS_RE.finditer(message):
        owners.update(int(owner.group(1)) for owner in re.finditer(r"I(\d+)", match.group(1)))
    return owners

def _crossing_source_points(message: str) -> set[Point]:
    points = {source.point for source in _crossing_sources(message)}
    points.update(
        (float(match.group(1)), float(match.group(2)))
        for match in _CROSSING_SOURCE_POINT_RE.finditer(message)
    )
    return points

def _crossing_sources(message: str) -> tuple[_CrossingSource, ...]:
    sources: list[_CrossingSource] = []
    for match in _CROSSING_SOURCE_RE.finditer(message):
        (
            prefix,
            owner,
            crossed,
            px,
            py,
            sx0,
            sy0,
            sx1,
            sy1,
            cx0,
            cy0,
            cx1,
            cy1,
        ) = match.groups()
        sources.append(
            _CrossingSource(
                owner_input=int(owner),
                crossed_input=int(crossed),
                point=(float(px), float(py)),
                source_segment=((float(sx0), float(sy0)), (float(sx1), float(sy1))),
                crossed_segment=((float(cx0), float(cy0)), (float(cx1), float(cy1))),
                is_candidate=prefix == "candidate_source",
            )
        )
    return tuple(sources)

def _failed_reroute_priority(
    failed: tuple[FailedNet, ...] | list[FailedNet],
    order: list[Path],
) -> tuple[int, ...]:
    direct: list[int] = []
    for failed_net in failed:
        if failed_net.input_port not in direct:
            direct.append(failed_net.input_port)
    blockers_by_failed: list[list[int]] = []
    for failed_net in failed:
        blockers: list[int] = []
        for match in _RELATED_FAILED_INPUT_RE.finditer(failed_net.message):
            owner = int(match.group(1))
            if owner == failed_net.input_port or owner in blockers:
                continue
            blockers.append(owner)
        for owner in sorted(_reserved_owner_inputs(failed_net.message)):
            if owner == failed_net.input_port or owner in blockers:
                continue
            blockers.append(owner)
        for source in _crossing_sources(failed_net.message):
            if source.is_candidate:
                continue
            for owner in (source.owner_input, source.crossed_input):
                if owner == failed_net.input_port or owner in blockers:
                    continue
                blockers.append(owner)
        blockers_by_failed.append(blockers)

    priority: list[int] = []
    max_blockers = max((len(blockers) for blockers in blockers_by_failed), default=0)
    for idx in range(max_blockers + 1):
        for failed_owner, blockers in zip(direct, blockers_by_failed):
            if idx == 0 and failed_owner not in priority:
                priority.append(failed_owner)
            if idx < len(blockers):
                owner = blockers[idx]
                if owner not in priority:
                    priority.append(owner)
    return tuple(priority)

def _failed_routing_candidate_score(
    routes: tuple[PhysicalRoute, ...] | list[PhysicalRoute],
    failed: tuple[FailedNet, ...] | list[FailedNet],
) -> tuple[float, ...]:
    base = _routing_candidate_score(routes, (), (), failed)
    source_count = sum(len(_crossing_sources(failed_net.message)) for failed_net in failed)
    return (
        base[0],
        -float(len(routes)),
        float(source_count),
        *_failure_reason_counts(failed),
        *base[1:],
    )

def _pass_candidate_score(
    routes: tuple[PhysicalRoute, ...] | list[PhysicalRoute],
    failed: tuple[FailedNet, ...] | list[FailedNet],
    violations: tuple[DRCViolation, ...] | list[DRCViolation],
) -> tuple[float, ...]:
    if failed:
        return (
            1.0,
            *_failed_routing_candidate_score(routes, failed),
            float(len(violations)),
        )
    crossings = _route_crossings(routes)
    return (
        0.0,
        *_routing_candidate_score(routes, violations, crossings, ()),
    )

def _violation_inputs(
    violations: tuple[DRCViolation, ...] | list[DRCViolation],
) -> set[int]:
    inputs: set[int] = set()
    for violation in violations:
        for match in re.finditer(r"I(\d+)", violation.net_id):
            inputs.add(int(match.group(1)))
    return inputs

def _merge_priority_inputs(
    priority: tuple[int, ...],
    extra_inputs: set[int],
) -> tuple[int, ...]:
    merged = list(priority)
    for input_port in sorted(extra_inputs):
        if input_port not in merged:
            merged.append(input_port)
    return tuple(merged)

def _failure_reason_counts(
    failed: tuple[FailedNet, ...] | list[FailedNet],
) -> tuple[float, ...]:
    messages = tuple(failed_net.message for failed_net in failed)
    return (
        float(sum("A* pop limit exceeded" in message for message in messages)),
        float(sum("port_access" in message for message in messages)),
        float(sum("crossing budget" in message for message in messages)),
    )

def _bounded_reroute_inputs(
    order: list[Path],
    *,
    direct_failed_inputs: set[int],
    related_inputs: set[int],
    budget: int | None,
    priority_inputs: tuple[int, ...] = (),
    components: tuple[frozenset[int], ...] = (),
) -> set[int]:
    if not related_inputs:
        return set()
    if budget is None:
        return set(related_inputs)
    selected: list[int] = []
    order_rank = {path.input_port: idx for idx, path in enumerate(order)}
    priority_rank = {
        input_port: idx
        for idx, input_port in enumerate(priority_inputs)
    }

    def add(input_port: int) -> None:
        if input_port in related_inputs and input_port not in selected:
            selected.append(input_port)

    def add_component(component: frozenset[int]) -> None:
        for input_port in sorted(
            (item for item in component if item in related_inputs),
            key=lambda item: (
                priority_rank.get(item, len(priority_rank)),
                order_rank.get(item, len(order_rank)),
                item,
            ),
        ):
            add(input_port)

    direct_components = [
        component
        for component in components
        if component & direct_failed_inputs & related_inputs
    ]
    for component in sorted(
        direct_components,
        key=lambda item: (
            min(priority_rank.get(owner, len(priority_rank)) for owner in item),
            min(order_rank.get(owner, len(order_rank)) for owner in item),
            len(item),
            tuple(sorted(item)),
        ),
    ):
        add_component(component)
    for input_port in priority_inputs:
        if len(selected) >= max(budget, len(direct_failed_inputs)):
            break
        add(input_port)
    for path in order:
        if path.input_port not in direct_failed_inputs:
            continue
        add(path.input_port)
    for path in order:
        if len(selected) >= max(budget, len(direct_failed_inputs)):
            break
        add(path.input_port)
    return set(selected)

def _prioritized_reroute_order(
    order: list[Path],
    priority_inputs: tuple[int, ...],
) -> list[Path]:
    if not priority_inputs:
        return list(order)
    rank = {input_port: idx for idx, input_port in enumerate(priority_inputs)}
    return sorted(
        order,
        key=lambda path: (
            rank.get(path.input_port, len(rank)),
            order.index(path),
        ),
    )

def _ripup_order_candidates(
    order: list[Path],
    *,
    direct_failed_inputs: set[int],
    related_inputs: set[int],
    priority_inputs: tuple[int, ...],
    limit: int,
) -> tuple[list[Path], ...]:
    if limit <= 0:
        return (list(order),)
    path_by_input = {path.input_port: path for path in order}
    related = {input_port for input_port in related_inputs if input_port in path_by_input}
    if not related:
        return (list(order),)

    candidates: list[list[Path]] = []
    seen: set[tuple[int, ...]] = set()

    def add(input_order: list[int]) -> None:
        normalized = [
            input_port
            for input_port in input_order
            if input_port in related and input_port in path_by_input
        ]
        normalized.extend(
            input_port
            for input_port in sorted(related)
            if input_port not in normalized
        )
        key = tuple(normalized)
        if not key or key in seen:
            return
        seen.add(key)
        candidates.append([path_by_input[input_port] for input_port in normalized])

    add([path.input_port for path in order])

    direct_sorted = sorted(input_port for input_port in direct_failed_inputs if input_port in related)
    if direct_sorted:
        add(direct_sorted + [input_port for input_port in sorted(related) if input_port not in direct_sorted])
        add(direct_sorted + [input_port for input_port in priority_inputs if input_port not in direct_sorted])

    if priority_inputs:
        add(list(priority_inputs))
    add(sorted(related))

    return tuple(candidates[:limit])

def _corridor_preferred_x(
    paths: list[Path],
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    port_stub_um: float,
    x_start: float,
    x_end: float,
    wire_pitch_um: float,
    n_physical: int,
) -> dict[int, dict[tuple[float, float], float]]:
    """Assign deterministic preferred bend x-slots for each inter-stage corridor."""
    from collections import defaultdict

    corridor_paths: dict[tuple[float, float], list[tuple[int, float]]] = defaultdict(list)

    def register(src: Point, dst: Point, input_port: int) -> None:
        # A corridor only needs a bend slot when it spans horizontally. The key
        # is direction-agnostic (sorted x-range), so a right-to-left lower-bus
        # hop (src[0] > dst[0], created by the physical add-drop geometry) gets a
        # slot exactly like a left-to-right hop. Skipping right-to-left hops was
        # the bug: those segments lost all corridor management.
        if abs(src[0] - dst[0]) <= EPS:
            return
        key = _corridor_key(src, dst)
        y_mid = (src[1] + dst[1]) / 2.0
        corridor_paths[key].append((input_port, y_mid))

    for path in paths:
        if not path.steps:
            continue
        first_step = path.steps[0]
        first_cell = cells[first_step.mrr_id]
        src = (_canonical_track(x_start), _wire_y(path.input_port, n_physical, wire_pitch_um))
        dst = _port_route_point(first_cell, first_step.in_port, rules, port_stub_um)
        register(src, dst, path.input_port)

        for step_idx, step in enumerate(path.steps[:-1]):
            cell_left = cells[step.mrr_id]
            next_step = path.steps[step_idx + 1]
            cell_right = cells[next_step.mrr_id]
            src = _port_route_point(cell_left, step.out_port, rules, port_stub_um)
            dst = _port_route_point(cell_right, next_step.in_port, rules, port_stub_um)
            register(src, dst, path.input_port)

        last_step = path.steps[-1]
        last_cell = cells[last_step.mrr_id]
        src = _port_route_point(last_cell, last_step.out_port, rules, port_stub_um)
        dst = (_canonical_track(x_end), _wire_y(path.output_port, n_physical, wire_pitch_um))
        register(src, dst, path.input_port)

    result: dict[int, dict[tuple[float, float], float]] = defaultdict(dict)
    for key, entries in corridor_paths.items():
        x_left, x_right = key
        entries_sorted = sorted(entries, key=lambda item: item[1])
        n_entries = len(entries_sorted)
        for rank, (input_port, _y_mid) in enumerate(entries_sorted):
            x_slot = x_left + (rank + 1) * (x_right - x_left) / (n_entries + 1)
            result[input_port][key] = x_slot

    return dict(result)

def _corridor_key(src: Point, dst: Point) -> tuple[float, float]:
    x_left, x_right = sorted((src[0], dst[0]))
    return round(x_left, 3), round(x_right, 3)

def _routing_grid_bounds(grid: tuple[tuple[float, ...], tuple[float, ...]]) -> RoutingWindow:
    x_tracks, y_tracks = grid
    return RoutingWindow(
        left=min(x_tracks),
        right=max(x_tracks),
        bottom=min(y_tracks),
        top=max(y_tracks),
    )

def _io_clamped_routing_bounds(
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    x_start: float,
    x_end: float,
) -> RoutingWindow:
    bounds = _routing_grid_bounds(grid)
    left, right = sorted((x_start, x_end))
    return RoutingWindow(
        left=max(bounds.left, left),
        right=min(bounds.right, right),
        bottom=bounds.bottom,
        top=bounds.top,
    )

def _hop_routing_window(
    src: Point,
    dst: Point,
    obstacles: list[Obstacle],
    rules: RoutingRules,
    grid_bounds: RoutingWindow,
    *,
    detour_tracks: int,
    ripup_pass_idx: int,
) -> RoutingWindow:
    x_lo, x_hi = sorted((src[0], dst[0]))
    y_lo, y_hi = sorted((src[1], dst[1]))
    x_slack = rules.bend_radius_um + max(1, detour_tracks) * rules.grid_pitch_um
    y_slack = rules.bend_radius_um + max(1, detour_tracks) * rules.grid_pitch_um
    base_left = x_lo - x_slack
    base_right = x_hi + x_slack
    base_bottom = y_lo - y_slack
    base_top = y_hi + y_slack
    left = base_left
    right = base_right
    bottom = base_bottom
    top = base_top

    for obstacle in obstacles:
        x_relevant = _intervals_overlap(
            (base_left, base_right),
            (obstacle.left, obstacle.right),
        )
        y_relevant = _intervals_overlap(
            (base_bottom, base_top),
            (obstacle.bottom, obstacle.top),
        )
        if not (x_relevant and y_relevant):
            continue
        left = min(left, obstacle.left - x_slack)
        right = max(right, obstacle.right + x_slack)
        bottom = min(bottom, obstacle.bottom - y_slack)
        top = max(top, obstacle.top + y_slack)

    return RoutingWindow(left, right, bottom, top).expanded(
        ripup_pass_idx,
        rules.grid_pitch_um,
        bounds=grid_bounds,
    )

def _detour_tracks(base_tracks: int, rules: RoutingRules) -> int:
    if rules.explicit_crossings or rules.strict_port_access:
        return max(
            base_tracks,
            min(
                rules.local_repair_max_shift_tracks,
                rules.route_window_max_detour_tracks,
            ),
        )
    return base_tracks

def _detour_track_sequence(base_tracks: int, rules: RoutingRules) -> tuple[int, ...]:
    max_tracks = _detour_tracks(base_tracks, rules)
    candidates = (base_tracks, min(max_tracks, 4), max_tracks)
    result: list[int] = []
    for tracks in candidates:
        tracks = max(base_tracks, tracks)
        if tracks not in result:
            result.append(tracks)
    return tuple(result)

def _route_physical_order(
    order: list[Path],
    *,
    paths: list[Path],
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    port_stub_um: float,
    x_start: float,
    x_end: float,
    wire_pitch_um: float,
    max_stage: int | None,
    forbidden_points_by_input: dict[int, set[Point]] | None = None,
    crossing_sources_by_input: dict[int, set[_CrossingSource]] | None = None,
    ripup_pass_idx: int = 0,
    fixed_routes: tuple[PhysicalRoute, ...] | list[PhysicalRoute] = (),
    same_net_whole_net_reroute: bool = False,
    remediation_events_out: list[tuple[int, int]] | None = None,
    remediation_attempts_out: list[dict[str, object]] | None = None,
) -> tuple[tuple[PhysicalRoute, ...], tuple[FailedNet, ...]]:
    occupied: list[OccupiedRouteSegment] = [
        segment
        for route in fixed_routes
        for segment in _occupied_route_segments(route)
    ]
    occupied_bends: set[Point] = {
        _port_route_point(cells[step.mrr_id], port, rules, port_stub_um)
        for path in paths
        for step in path.steps
        for port in (step.in_port, step.out_port)
    }
    occupied_bends.update(
        point
        for route in fixed_routes
        for point in _route_external_forbidden_points(route)
    )
    routes: list[PhysicalRoute] = list(fixed_routes)
    fixed_inputs = {route.input_port for route in fixed_routes}
    failed: list[FailedNet] = []
    n_physical = _infer_n_physical(cells, paths)
    corridor_slots = _corridor_preferred_x(
        paths,
        cells,
        rules,
        port_stub_um,
        x_start=x_start,
        x_end=x_end,
        wire_pitch_um=wire_pitch_um,
        n_physical=n_physical,
    )
    obstacles = [
        _inflate_obstacle(_routing_obstacle(cell), rules.mrr_keepout_um)
        for cell in cells.values()
    ]
    grid = _routing_grid(
        paths,
        cells,
        obstacles,
        rules,
        x_start=x_start,
        x_end=x_end,
        wire_pitch_um=wire_pitch_um,
        port_stub_um=port_stub_um,
    )
    grid_bounds = _io_clamped_routing_bounds(grid, x_start, x_end)
    port_access_plan = build_port_access_plan(paths, cells, rules, port_stub_um)

    for path in order:
        if path.input_port in fixed_inputs:
            continue
        if max_stage is not None and not any(step.stage <= max_stage for step in path.steps):
            continue
        try:
            all_net_forbidden = (forbidden_points_by_input or {}).get(
                path.input_port,
                set(),
            )
            all_net_crossing_sources = (crossing_sources_by_input or {}).get(
                path.input_port,
                set(),
            )
            fixed_net_crossing_sources = _active_crossing_sources_for_fixed_routes(
                set(all_net_crossing_sources),
                fixed_inputs,
            )
            allocation_crossing_sources = _owner_crossing_sources_for_current_net(
                set(all_net_crossing_sources),
                path.input_port,
            )
            net_forbidden = _filtered_source_forbidden_points(
                set(all_net_forbidden),
                set(all_net_crossing_sources),
                fixed_net_crossing_sources,
            )
            visible_port_access_plan = _visible_port_access_plan(
                port_access_plan,
                routed_inputs={route.input_port for route in routes} | {path.input_port},
            )
            # Reserve this net's own access corridors during its external A*
            # hops, including future ports on the same path. Otherwise an
            # earlier external hop can run within min-spacing of a later local
            # escape and make the route fail after the fact.
            reserved_port_access = _owner_port_access_segments(
                port_access_plan,
                path.input_port,
            )
            reserved_port_access_guards = _foreign_port_access_guard_blockers(
                port_access_plan,
                path.input_port,
                rules,
                active_owner_inputs=(
                    {route.input_port for route in routes}
                    if ripup_pass_idx > 0
                    else None
                ),
            )
            visible_owner_inputs = {route.input_port for route in routes} | {path.input_port}
            soft_reserved_port_access = _future_port_access_segments(
                port_access_plan,
                visible_owner_inputs=visible_owner_inputs,
            )
            port_access = PortAccessLegality(visible_port_access_plan, path.input_port)
            route = _route_one_path(
                path,
                cells,
                occupied,
                reserved_port_access,
                reserved_port_access_guards,
                soft_reserved_port_access,
                obstacles,
                grid,
                rules,
                n_physical=n_physical,
                port_stub_um=port_stub_um,
                x_start=x_start,
                x_end=x_end,
                wire_pitch_um=wire_pitch_um,
                max_stage=max_stage,
                forbidden_points=net_forbidden | occupied_bends,
                source_crossings=set(fixed_net_crossing_sources),
                allocation_crossings=set(allocation_crossing_sources),
                corridor_slots=corridor_slots.get(path.input_port, {}),
                port_access=port_access,
                grid_bounds=grid_bounds,
                ripup_pass_idx=ripup_pass_idx,
                same_net_whole_net_reroute=same_net_whole_net_reroute,
                remediation_events_out=remediation_events_out,
                remediation_attempts_out=remediation_attempts_out,
            )
        except RoutingError as exc:
            failed.append(FailedNet(path.input_port, path.output_port, str(exc)))
            continue
        routes.append(route)
        occupied.extend(_occupied_route_segments(route))
        occupied_bends.update(_route_external_forbidden_points(route))
    return tuple(routes), tuple(failed)

def _route_external_forbidden_points(route: PhysicalRoute) -> set[Point]:
    """Return external route endpoints and true bends, excluding collinear split points."""
    incident: dict[Point, list[Segment]] = {}
    for segment in route.external_segments:
        for point in segment:
            incident.setdefault(point, []).append(segment)

    forbidden: set[Point] = set()
    for point, segments in incident.items():
        if len(segments) != 2:
            forbidden.add(point)
            continue
        first_axis = _segment_axis(segments[0])
        second_axis = _segment_axis(segments[1])
        if not first_axis or not second_axis or first_axis != second_axis:
            forbidden.add(point)
    return forbidden

@dataclass(frozen=True)
class _RouteOrderBeamState:
    routes: tuple[PhysicalRoute, ...]
    remaining_inputs: tuple[int, ...]
    order: tuple[int, ...]

def _route_order_beam(
    order: list[Path],
    *,
    paths: list[Path],
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    port_stub_um: float,
    x_start: float,
    x_end: float,
    wire_pitch_um: float,
    max_stage: int | None,
    beam_width: int,
) -> tuple[tuple[PhysicalRoute, ...], tuple[FailedNet, ...]]:
    if not order:
        return (), ()
    path_by_input = {path.input_port: path for path in order}
    initial = _RouteOrderBeamState(
        routes=(),
        remaining_inputs=tuple(path.input_port for path in order),
        order=(),
    )
    states: tuple[_RouteOrderBeamState, ...] = (initial,)
    best_routes: tuple[PhysicalRoute, ...] = ()
    best_failed: tuple[FailedNet, ...] = tuple(
        FailedNet(path.input_port, path.output_port, "not attempted by route-order beam")
        for path in order
    )
    best_score = _failed_routing_candidate_score(best_routes, best_failed)

    for _depth in range(len(order)):
        expanded: list[_RouteOrderBeamState] = []
        for state in states:
            for input_port in state.remaining_inputs:
                path = path_by_input[input_port]
                routes, failed = _route_physical_order(
                    [path],
                    paths=paths,
                    cells=cells,
                    rules=rules,
                    port_stub_um=port_stub_um,
                    x_start=x_start,
                    x_end=x_end,
                    wire_pitch_um=wire_pitch_um,
                    max_stage=max_stage,
                    ripup_pass_idx=1,
                    fixed_routes=state.routes,
                )
                if failed:
                    candidate_failed = tuple(failed) + tuple(
                        FailedNet(
                            path_by_input[remaining].input_port,
                            path_by_input[remaining].output_port,
                            "not attempted after route-order beam branch failure",
                        )
                        for remaining in state.remaining_inputs
                        if remaining != input_port
                    )
                    score = _failed_routing_candidate_score(routes, candidate_failed)
                    if score < best_score:
                        best_score = score
                        best_routes = tuple(sorted(routes, key=lambda item: item.input_port))
                        best_failed = candidate_failed
                    continue
                routed_inputs = {route.input_port for route in routes}
                remaining_inputs = tuple(
                    remaining
                    for remaining in state.remaining_inputs
                    if remaining not in routed_inputs
                )
                expanded.append(
                    _RouteOrderBeamState(
                        routes=tuple(routes),
                        remaining_inputs=remaining_inputs,
                        order=state.order + (input_port,),
                    )
                )
        if not expanded:
            break
        states = tuple(
            sorted(expanded, key=_route_order_beam_state_score)[: max(1, beam_width)]
        )
        for state in states:
            candidate_failed = tuple(
                FailedNet(
                    path_by_input[remaining].input_port,
                    path_by_input[remaining].output_port,
                    "not attempted by incomplete route-order beam",
                )
                for remaining in state.remaining_inputs
            )
            score = _failed_routing_candidate_score(state.routes, candidate_failed)
            if score < best_score:
                best_score = score
                best_routes = tuple(sorted(state.routes, key=lambda item: item.input_port))
                best_failed = candidate_failed
            if not state.remaining_inputs:
                return tuple(sorted(state.routes, key=lambda item: item.input_port)), ()

    return tuple(sorted(best_routes, key=lambda item: item.input_port)), best_failed

def _route_order_beam_state_score(
    state: _RouteOrderBeamState,
) -> tuple[float, float, float, float, float, float, tuple[float, ...]]:
    crossings = _route_crossings(state.routes)
    return (
        -float(len(state.routes)),
        float(_braid_pair_count(crossings)),
        float(len(crossings)),
        sum(route.length_um for route in state.routes),
        float(sum(route.bend_count for route in state.routes)),
        float(len(state.remaining_inputs)),
        tuple(float(item) for item in state.order),
    )

def _visible_port_access_plan(
    plan: PortAccessPlan,
    *,
    routed_inputs: set[int],
) -> PortAccessPlan:
    return PortAccessPlan(
        plan.points_by_cell_port,
        tuple(
            region
            for region in plan.reserved_regions
            if region.owner_input in routed_inputs
        ),
    )

def _owner_port_access_segments(
    plan: PortAccessPlan,
    owner_input: int,
) -> list[Segment]:
    return [
        region.segment
        for region in plan.reserved_regions
        if region.owner_input == owner_input
    ]

def _future_port_access_segments(
    plan: PortAccessPlan,
    *,
    visible_owner_inputs: set[int],
) -> list[Segment]:
    return [
        region.segment
        for region in plan.reserved_regions
        if region.owner_input not in visible_owner_inputs
    ]

def _foreign_port_access_guard_segments(
    plan: PortAccessPlan,
    owner_input: int,
    rules: RoutingRules,
) -> list[Segment]:
    return [
        item.segment
        for item in _foreign_port_access_guard_blockers(plan, owner_input, rules)
    ]

def _foreign_port_access_guard_blockers(
    plan: PortAccessPlan,
    owner_input: int,
    rules: RoutingRules,
    *,
    active_owner_inputs: set[int] | None = None,
) -> list[OccupiedRouteSegment]:
    guard_um = max(rules.grid_pitch_um, 2.0 * rules.bend_radius_um)
    guards: list[OccupiedRouteSegment] = []
    for region in plan.reserved_regions:
        if region.owner_input == owner_input:
            continue
        if active_owner_inputs is not None and region.owner_input not in active_owner_inputs:
            continue
        escape = region.segment[1]
        if region.side == "left":
            segment = (escape, (_canonical_track(escape[0] - guard_um), escape[1]))
        elif region.side == "right":
            segment = (escape, (_canonical_track(escape[0] + guard_um), escape[1]))
        else:
            continue
        guards.append(OccupiedRouteSegment(region.owner_input, segment, "port_guard"))
    return guards

def _occupied_route_segments(route: PhysicalRoute) -> list[OccupiedRouteSegment]:
    return [
        OccupiedRouteSegment(route.input_port, segment, "external")
        for segment in route.external_segments
    ] + [
        OccupiedRouteSegment(route.input_port, segment, "local")
        for segment in route.local_segments
    ]

def _occupied_blocker_segments(occupied: list[OccupiedRouteSegment]) -> list[Segment]:
    return [item.segment for item in occupied]

def _guard_segments(
    guards: list[Segment] | list[OccupiedRouteSegment],
) -> list[Segment]:
    return [
        item.segment if isinstance(item, OccupiedRouteSegment) else item
        for item in guards
    ]

def _reserved_guard_conflict_reason(
    segment: Segment,
    guards: list[Segment] | list[OccupiedRouteSegment],
    rules: RoutingRules,
) -> str | None:
    for item in guards:
        blocker = item.segment if isinstance(item, OccupiedRouteSegment) else item
        if not (
            _segments_collinear_overlap(segment, blocker)
            or _parallel_spacing_violation(
                segment, blocker, rules.min_spacing_um, rules.waveguide_width_um
            )
            or _orthogonal_crossing_point(segment, blocker) is not None
        ):
            continue
        if isinstance(item, OccupiedRouteSegment):
            return f"blocked_by=I{item.owner_input}/port_guard:{_format_segment(blocker)}"
        return f"blocked_by=unknown/port_guard:{_format_segment(blocker)}"
    return None

def _crossing_count_by_pair_for_current_net(
    occupied: list[OccupiedRouteSegment],
    current_external_segments: list[Segment],
    owner_input: int,
) -> dict[tuple[int, int], int]:
    counts: dict[tuple[int, int], int] = {}
    _add_crossing_counts_by_pair(
        counts,
        occupied,
        current_external_segments,
        owner_input,
    )
    return counts

def _crossing_sources_by_pair_for_current_net(
    occupied: list[OccupiedRouteSegment],
    current_external_segments: list[Segment],
    owner_input: int,
) -> dict[tuple[int, int], tuple[str, ...]]:
    sources: dict[tuple[int, int], tuple[str, ...]] = {}
    occupied_external = [item for item in occupied if item.kind == "external"]
    for segment in current_external_segments:
        for other in occupied_external:
            location = _orthogonal_crossing_point(segment, other.segment)
            if location is None or other.owner_input is None:
                continue
            pair = _owner_pair(owner_input, other.owner_input)
            source = (
                f"I{owner_input}->I{other.owner_input}"
                f"@({location[0]:.3f},{location[1]:.3f})"
                f":{_format_segment(segment)}"
                f"x{_format_segment(other.segment)}"
            )
            sources[pair] = sources.get(pair, ()) + (source,)
    return sources

def _add_crossing_counts_by_pair(
    counts: dict[tuple[int, int], int],
    occupied: list[OccupiedRouteSegment],
    new_external_segments: list[Segment] | tuple[Segment, ...],
    owner_input: int | None,
) -> None:
    if owner_input is None:
        return
    occupied_external = [item for item in occupied if item.kind == "external"]
    for segment in new_external_segments:
        for other in occupied_external:
            if (
                other.owner_input is None
                or _orthogonal_crossing_point(segment, other.segment) is None
            ):
                continue
            pair = _owner_pair(owner_input, other.owner_input)
            counts[pair] = counts.get(pair, 0) + 1

def _append_owned_local_axis(
    points: list[Point],
    start: Point,
    end: Point,
    occupied: list[OccupiedRouteSegment],
    current_owner: int,
    external_segments: list[Segment],
    local_segments: list[Segment],
    rules: RoutingRules,
) -> None:
    if _same_point(start, end):
        return
    segment = (start, end)
    if not _is_axis_aligned(segment):
        raise RoutingError(f"non-Manhattan port escape {start} -> {end}")
    for blocker, label in _local_axis_blockers(
        occupied,
        current_owner,
        external_segments,
        local_segments,
    ):
        if _segments_collinear_overlap(segment, blocker):
            raise RoutingError(
                f"port escape overlaps existing route: {start} -> {end}; "
                f"blocked_by={label}:{_format_segment(blocker)}"
            )
        if _parallel_spacing_violation(
            segment, blocker, rules.min_spacing_um, rules.waveguide_width_um
        ):
            raise RoutingError(
                f"port escape violates spacing: {start} -> {end}; "
                f"blocked_by={label}:{_format_segment(blocker)}"
            )
        if (
            label.startswith(f"I{current_owner}/")
            and _orthogonal_crossing_point(segment, blocker) is not None
        ):
            raise RoutingError(
                f"port escape crosses same-net route: {start} -> {end}; "
                f"blocked_by={label}:{_format_segment(blocker)}"
            )
        contact = _segment_contact_point(segment, blocker)
        if contact is not None and not (_same_point(contact, start) or _same_point(contact, end)):
            raise RoutingError(
                f"port escape touches existing route: {start} -> {end}; "
                f"blocked_by={label}:{_format_segment(blocker)}"
            )
    local_segments.append(segment)
    _append_points(points, (start, end))

def _local_axis_blockers(
    occupied: list[OccupiedRouteSegment],
    current_owner: int,
    external_segments: list[Segment],
    local_segments: list[Segment],
) -> list[tuple[Segment, str]]:
    blockers = [
        (item.segment, f"I{item.owner_input}/{item.kind}")
        for item in occupied
    ]
    blockers.extend((segment, f"I{current_owner}/external") for segment in external_segments)
    blockers.extend((segment, f"I{current_owner}/local") for segment in local_segments)
    return blockers

def _route_one_path(
    path: Path,
    cells: dict[str, MRRCell],
    occupied: list[OccupiedRouteSegment],
    reserved_port_access: list[Segment],
    reserved_port_access_guards: list[Segment] | list[OccupiedRouteSegment],
    soft_reserved_port_access: list[Segment],
    obstacles: list[Obstacle],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
    *,
    n_physical: int,
    port_stub_um: float,
    x_start: float,
    x_end: float,
    wire_pitch_um: float,
    max_stage: int | None,
    forbidden_points: set[Point],
    source_crossings: set[_CrossingSource] | None = None,
    allocation_crossings: set[_CrossingSource] | None = None,
    corridor_slots: dict[tuple[float, float], float] | None = None,
    port_access: PortAccessLegality | None = None,
    grid_bounds: RoutingWindow | None = None,
    ripup_pass_idx: int = 0,
    same_net_whole_net_reroute: bool = False,
    remediation_events_out: list[tuple[int, int]] | None = None,
    remediation_attempts_out: list[dict[str, object]] | None = None,
) -> PhysicalRoute:
    history_cost = HistoryCost(step_penalty_um=max(24.0, 0.5 * rules.hairpin_penalty_um))
    last_error: RoutingError | None = None
    penalized_crossing_owners: set[int] = set()
    retry_source_crossings: set[_CrossingSource] = set(source_crossings or set())
    retry_allocation_crossings: set[_CrossingSource] = set(allocation_crossings or set())
    retry_forbidden_points = set(forbidden_points)
    max_attempts = 1 if rules.max_ripup_passes == 0 else 1 + int(ripup_pass_idx > 0)
    for attempt_idx in range(max_attempts):
        attempted_external: list[Segment] = []
        try:
            return _route_one_path_attempt(
                path,
                cells,
                occupied,
                reserved_port_access,
                reserved_port_access_guards,
                soft_reserved_port_access,
                obstacles,
                grid,
                rules,
                n_physical=n_physical,
                port_stub_um=port_stub_um,
                x_start=x_start,
                x_end=x_end,
                wire_pitch_um=wire_pitch_um,
                max_stage=max_stage,
                forbidden_points=retry_forbidden_points,
                corridor_slots=corridor_slots,
                port_access=port_access,
                grid_bounds=grid_bounds,
                ripup_pass_idx=ripup_pass_idx + attempt_idx,
                history_cost=history_cost,
                external_segments_out=attempted_external,
                penalized_crossing_owners=penalized_crossing_owners,
                source_crossings=retry_source_crossings,
                allocation_crossings=retry_allocation_crossings,
            )
        except RoutingError as exc:
            last_error = exc
            new_penalty_owners = _crossing_budget_owners(str(exc)) - penalized_crossing_owners
            new_source_crossings = set(_crossing_sources(str(exc))) - retry_source_crossings
            new_forbidden_points = _crossing_source_points(str(exc)) - retry_forbidden_points
            if (
                not attempted_external
                and not new_penalty_owners
                and not new_source_crossings
                and not new_forbidden_points
            ):
                break
            penalized_crossing_owners.update(new_penalty_owners)
            retry_source_crossings.update(new_source_crossings)
            _bump_failed_source_history(
                history_cost,
                new_source_crossings,
                new_forbidden_points,
                grid,
                rules,
                attempt_idx=attempt_idx,
            )
            history_cost.bump_route(
                attempted_external,
                grid[0],
                grid[1],
                amount=rules.hairpin_penalty_um * (attempt_idx + 1),
            )
    assert last_error is not None
    fallback_beam_width = _effective_fallback_beam_width(rules, last_error)
    if fallback_beam_width > 0 and rules.fallback_alternatives_per_hop > 0:
        effective_grid_bounds = grid_bounds or _io_clamped_routing_bounds(
            grid,
            x_start,
            x_end,
        )
        try:
            return _route_one_path_beam(
                path,
                cells,
                occupied,
                reserved_port_access,
                reserved_port_access_guards,
                soft_reserved_port_access,
                obstacles,
                grid,
                rules,
                n_physical=n_physical,
                port_stub_um=port_stub_um,
                x_start=x_start,
                x_end=x_end,
                wire_pitch_um=wire_pitch_um,
                max_stage=max_stage,
                forbidden_points=retry_forbidden_points,
                corridor_slots=corridor_slots,
                port_access=port_access,
                grid_bounds=effective_grid_bounds,
                ripup_pass_idx=ripup_pass_idx + 1,
                history_cost=history_cost,
                penalized_crossing_owners=penalized_crossing_owners,
                source_crossings=retry_source_crossings,
                allocation_crossings=retry_allocation_crossings,
                beam_width=fallback_beam_width,
                alternatives_per_hop=(
                    max(fallback_beam_width, rules.fallback_alternatives_per_hop)
                    if rules.fallback_beam_width == 0
                    else rules.fallback_alternatives_per_hop
                ),
            )
        except RoutingError as exc:
            last_error = exc
        if (
            same_net_whole_net_reroute
            and isinstance(last_error, _SameNetCommittedHopError)
        ):
            triggering_error = last_error
            displacement_hop_index = max(0, last_error.failed_hop_index - 1)
            for displacement_tracks in (1, -1, 2, -2):
                window_expansion_tracks = _remediation_window_expansion_tracks(
                    rules,
                    displacement_tracks,
                )
                try:
                    remediated = _route_one_path_beam(
                        path,
                        cells,
                        occupied,
                        reserved_port_access,
                        reserved_port_access_guards,
                        soft_reserved_port_access,
                        obstacles,
                        grid,
                        rules,
                        n_physical=n_physical,
                        port_stub_um=port_stub_um,
                        x_start=x_start,
                        x_end=x_end,
                        wire_pitch_um=wire_pitch_um,
                        max_stage=max_stage,
                        forbidden_points=retry_forbidden_points,
                        corridor_slots=corridor_slots,
                        port_access=port_access,
                        grid_bounds=effective_grid_bounds,
                        ripup_pass_idx=ripup_pass_idx + 1,
                        history_cost=history_cost,
                        penalized_crossing_owners=penalized_crossing_owners,
                        source_crossings=retry_source_crossings,
                        allocation_crossings=retry_allocation_crossings,
                        beam_width=fallback_beam_width,
                        alternatives_per_hop=max(
                            fallback_beam_width,
                            rules.fallback_alternatives_per_hop,
                        ),
                        riser_displacement_tracks=displacement_tracks,
                        riser_displacement_hop_index=displacement_hop_index,
                    )
                except RoutingError as exc:
                    last_error = exc
                    if remediation_attempts_out is not None:
                        remediation_attempts_out.append(
                            {
                                "input_port": path.input_port,
                                "trigger_hop_index": triggering_error.failed_hop_index,
                                "displaced_hop_index": displacement_hop_index,
                                "displacement_tracks": displacement_tracks,
                                "window_expansion_tracks": window_expansion_tracks,
                                "status": "failed",
                                "error": str(exc),
                            }
                        )
                    continue
                if remediation_attempts_out is not None:
                    remediation_attempts_out.append(
                        {
                            "input_port": path.input_port,
                            "trigger_hop_index": triggering_error.failed_hop_index,
                            "displaced_hop_index": displacement_hop_index,
                            "displacement_tracks": displacement_tracks,
                            "window_expansion_tracks": window_expansion_tracks,
                            "status": "succeeded",
                        }
                    )
                if remediation_events_out is not None:
                    remediation_events_out.append(
                        (path.input_port, displacement_tracks)
                    )
                return remediated
            last_error = RoutingError(
                f"{triggering_error}; whole-net deterministic riser displacement "
                f"ladder exhausted (+1,-1,+2,-2 tracks) on predecessor hop "
                f"{displacement_hop_index}; last_error={last_error}"
            )
    raise last_error


def _effective_fallback_beam_width(
    rules: RoutingRules,
    error: RoutingError,
) -> int:
    if rules.fallback_beam_width > 0:
        return rules.fallback_beam_width
    message = str(error)
    if rules.uses_db_cost and "waksman_" in message and any(
        marker in message
        for marker in (
            "same-net self conflict",
            "same_net_touching_corner",
            "same_net:",
        )
    ):
        return 4
    if (
        rules.uses_db_cost
        and "padded_benes_" in message
        and "A* pop limit exceeded" in message
    ):
        return 2
    return 0

@dataclass(frozen=True)
class _ExternalHopSpec:
    src: Point
    dst: Point
    src_label: str
    dst_label: str
    repeated_crossing_penalty_scale: float
    preferred_bend_x: float | None
    reserve_space_penalty: bool
    base_detour_tracks: int
    future_hops: tuple[tuple[Point, Point], ...]
    after_step_idx: int | None
    post_local_segments: tuple[Segment, ...]
    riser_displacement_tracks: int = 0


def _repeated_crossing_penalty_scale_for_hop(
    src_port: str | None,
    dst_port: str | None,
    rules: RoutingRules,
) -> float:
    if hop_crossing_class(src_port, dst_port) == "outward":
        return rules.outward_repeated_crossing_scale
    return 1.0


@dataclass
class _PathBranch:
    points: list[Point]
    external_segments: list[Segment]
    local_segments: list[Segment]
    history_cost: HistoryCost


class _SameNetCommittedHopError(RoutingError):
    """A fallback hop was boxed in by this branch's earlier committed hops."""

    def __init__(self, message: str, *, failed_hop_index: int) -> None:
        super().__init__(message)
        self.failed_hop_index = failed_hop_index


_SAME_NET_TOUCH_PRIOR_RE = re.compile(
    rf"same_net_touch(?:ing_corner)? at=\({_FLOAT_PATTERN},{_FLOAT_PATTERN}\) "
    rf"prior=\({_FLOAT_PATTERN},{_FLOAT_PATTERN}\)->"
    rf"\({_FLOAT_PATTERN},{_FLOAT_PATTERN}\)"
)


def _same_net_touch_hits_committed_branch(
    error: RoutingError,
    branch: _PathBranch,
) -> bool:
    committed = tuple(branch.external_segments) + tuple(branch.local_segments)
    if not committed:
        return False
    for match in _SAME_NET_TOUCH_PRIOR_RE.finditer(str(error)):
        prior = (
            (float(match.group(3)), float(match.group(4))),
            (float(match.group(5)), float(match.group(6))),
        )
        if any(_same_segment(prior, segment) for segment in committed):
            return True
    return False


def _displace_candidate_risers(
    candidate: tuple[Point, ...],
    rules: RoutingRules,
    displacement_tracks: int,
    *,
    src_label: str | None = None,
    dst_label: str | None = None,
) -> tuple[Point, ...] | None:
    """Translate internal vertical risers without changing either endpoint.

    The candidate already satisfies the source/destination port directions.
    Moving its risers therefore preserves its overall shape, unlike
    synthesizing a new single-riser L at a corridor midpoint.  Endpoint-tied
    risers grow a short connector in the source/destination port direction;
    internal risers use the signed campaign ladder.
    """
    if not displacement_tracks:
        return candidate
    signed_delta_x = displacement_tracks * rules.grid_pitch_um
    magnitude_x = abs(displacement_tracks) * rules.grid_pitch_um
    points = list(candidate)
    vertical_indices = [
        idx
        for idx, (start, end) in enumerate(_segments_from_points(candidate))
        if abs(start[0] - end[0]) <= EPS and abs(start[1] - end[1]) > EPS
    ]
    if not vertical_indices:
        return candidate

    shifted_x: dict[int, float] = {}
    for idx in vertical_indices:
        delta_x = signed_delta_x
        if idx == 0:
            orientation = _port_junction_orientation(src_label or "", source=True)
            if orientation == "W":
                delta_x = -magnitude_x
            elif orientation == "E":
                delta_x = magnitude_x
        elif idx == len(points) - 2:
            orientation = _port_junction_orientation(dst_label or "", source=False)
            if orientation == "W":
                delta_x = magnitude_x
            elif orientation == "E":
                delta_x = -magnitude_x
        shifted_x[idx] = _canonical_track(points[idx][0] + delta_x)

    displaced: list[Point] = [points[0]]
    if 0 in shifted_x:
        displaced.append((shifted_x[0], points[0][1]))
    for point_idx in range(1, len(points) - 1):
        previous_segment = point_idx - 1
        next_segment = point_idx
        x = points[point_idx][0]
        if previous_segment in shifted_x:
            x = shifted_x[previous_segment]
        elif next_segment in shifted_x:
            x = shifted_x[next_segment]
        displaced.append((x, points[point_idx][1]))
    last_segment = len(points) - 2
    if last_segment in shifted_x:
        displaced.append((shifted_x[last_segment], points[-1][1]))
    displaced.append(points[-1])
    return _dedupe_points(_merge_collinear_points(tuple(displaced)))

def _route_one_path_beam(
    path: Path,
    cells: dict[str, MRRCell],
    occupied: list[OccupiedRouteSegment],
    reserved_port_access: list[Segment],
    reserved_port_access_guards: list[Segment] | list[OccupiedRouteSegment],
    soft_reserved_port_access: list[Segment],
    obstacles: list[Obstacle],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
    *,
    n_physical: int,
    port_stub_um: float,
    x_start: float,
    x_end: float,
    wire_pitch_um: float,
    max_stage: int | None,
    forbidden_points: set[Point],
    corridor_slots: dict[tuple[float, float], float] | None,
    port_access: PortAccessLegality | None,
    grid_bounds: RoutingWindow,
    ripup_pass_idx: int,
    history_cost: HistoryCost,
    penalized_crossing_owners: set[int] | None = None,
    source_crossings: set[_CrossingSource] | None = None,
    allocation_crossings: set[_CrossingSource] | None = None,
    beam_width: int | None = None,
    alternatives_per_hop: int | None = None,
    riser_displacement_tracks: int = 0,
    riser_displacement_hop_index: int | None = None,
) -> PhysicalRoute:
    steps = [step for step in path.steps if max_stage is None or step.stage <= max_stage]
    if not steps:
        raise RoutingError(f"I{path.input_port} has no active MRR steps to route")
    corridor_slots = corridor_slots or {}
    specs = _external_hop_specs(
        path,
        steps,
        cells,
        rules,
        n_physical=n_physical,
        port_stub_um=port_stub_um,
        x_start=x_start,
        x_end=x_end,
        wire_pitch_um=wire_pitch_um,
        corridor_slots=corridor_slots,
    )
    if riser_displacement_tracks:
        specs = [
            replace(
                spec,
                riser_displacement_tracks=(
                    riser_displacement_tracks
                    if riser_displacement_hop_index is None
                    or spec_idx == riser_displacement_hop_index
                    else 0
                ),
            )
            for spec_idx, spec in enumerate(specs)
        ]
    beam_width = max(1, rules.fallback_beam_width if beam_width is None else beam_width)
    alternatives_per_hop = max(
        1,
        rules.fallback_alternatives_per_hop
        if alternatives_per_hop is None
        else alternatives_per_hop,
    )
    penalized_crossing_owners = penalized_crossing_owners or set()
    source_crossings = source_crossings or set()
    allocation_crossings = allocation_crossings or set()
    branches = [
        _PathBranch(
            points=[],
            external_segments=[],
            local_segments=[],
            history_cost=_clone_history_cost(history_cost),
        )
    ]
    last_error: RoutingError | None = None

    for spec_idx, spec in enumerate(specs):
        next_branches: list[_PathBranch] = []
        committed_same_net_failure = False
        for branch in branches:
            try:
                alternatives = _external_hop_alternatives(
                    spec,
                    branch,
                    path,
                    occupied,
                    reserved_port_access,
                    reserved_port_access_guards,
                    soft_reserved_port_access,
                    obstacles,
                    grid,
                    rules,
                    forbidden_points=forbidden_points,
                    port_access=port_access,
                    grid_bounds=grid_bounds,
                    ripup_pass_idx=ripup_pass_idx,
                    alternatives_per_hop=alternatives_per_hop,
                    penalized_crossing_owners=penalized_crossing_owners,
                    source_crossings=source_crossings,
                    allocation_crossings=allocation_crossings,
                )
            except RoutingError as exc:
                last_error = exc
                committed_same_net_failure = (
                    committed_same_net_failure
                    or _same_net_touch_hits_committed_branch(exc, branch)
                )
                continue

            for external_path in alternatives:
                try:
                    candidate = _extend_branch_with_hop(
                        branch,
                        external_path,
                        spec,
                        steps,
                        cells,
                        occupied,
                        path.input_port,
                        rules,
                        port_stub_um,
                    )
                except RoutingError as exc:
                    last_error = exc
                    continue
                next_branches.append(candidate)

        if not next_branches:
            detail = f": {last_error}" if last_error is not None else ""
            message = (
                f"cannot route I{path.input_port}->O{path.output_port} with bounded "
                f"multi-hop fallback at {spec.src_label}->{spec.dst_label}{detail}"
            )
            if committed_same_net_failure:
                raise _SameNetCommittedHopError(
                    message,
                    failed_hop_index=spec_idx,
                )
            raise RoutingError(message)
        branches = sorted(
            next_branches,
            key=lambda branch: _branch_score(branch, occupied, path.input_port, rules),
        )[:beam_width]

    best = min(
        branches,
        key=lambda branch: _branch_score(branch, occupied, path.input_port, rules),
    )
    route_points = _dedupe_points(tuple(best.points))
    bend_count = _bend_count(route_points)
    bend_arc_um = 0.5 * pi * rules.bend_radius_um
    return PhysicalRoute(
        input_port=path.input_port,
        output_port=path.output_port,
        waypoints=route_points,
        length_um=_polyline_length(route_points) + bend_count * bend_arc_um,
        bend_count=bend_count,
        external_segments=tuple(best.external_segments),
        local_segments=tuple(best.local_segments),
    )

def _external_hop_specs(
    path: Path,
    steps: list[RouteStep],
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    *,
    n_physical: int,
    port_stub_um: float,
    x_start: float,
    x_end: float,
    wire_pitch_um: float,
    corridor_slots: dict[tuple[float, float], float],
) -> list[_ExternalHopSpec]:
    terminal = (x_end, _wire_y(path.output_port, n_physical, wire_pitch_um))
    specs: list[_ExternalHopSpec] = []
    current = (x_start, _wire_y(path.input_port, n_physical, wire_pitch_um))
    first_step = steps[0]
    first_cell = cells[first_step.mrr_id]
    first_escape = _port_route_point(first_cell, first_step.in_port, rules, port_stub_um)
    specs.append(
        _ExternalHopSpec(
            src=current,
            dst=first_escape,
            src_label=f"I{path.input_port}",
            dst_label=f"{first_step.mrr_id}.{first_step.in_port}",
            repeated_crossing_penalty_scale=_repeated_crossing_penalty_scale_for_hop(
                None,
                first_step.in_port,
                rules,
            ),
            preferred_bend_x=corridor_slots.get(_corridor_key(current, first_escape)),
            reserve_space_penalty=True,
            base_detour_tracks=1,
            future_hops=tuple(
                _external_hops_after(
                    steps,
                    cells,
                    rules,
                    port_stub_um,
                    start_index=-1,
                    terminal=terminal,
                )
            ),
            after_step_idx=0,
            post_local_segments=_local_step_segments(
                first_step,
                cells,
                rules,
                port_stub_um,
            ),
        )
    )
    for step_idx, step in enumerate(steps[:-1]):
        next_step = steps[step_idx + 1]
        cell = cells[step.mrr_id]
        next_cell = cells[next_step.mrr_id]
        src = _port_route_point(cell, step.out_port, rules, port_stub_um)
        dst = _port_route_point(next_cell, next_step.in_port, rules, port_stub_um)
        specs.append(
            _ExternalHopSpec(
                src=src,
                dst=dst,
                src_label=f"{step.mrr_id}.{step.out_port}",
                dst_label=f"{next_step.mrr_id}.{next_step.in_port}",
                repeated_crossing_penalty_scale=_repeated_crossing_penalty_scale_for_hop(
                    step.out_port,
                    next_step.in_port,
                    rules,
                ),
                preferred_bend_x=corridor_slots.get(_corridor_key(src, dst)),
                reserve_space_penalty=False,
                base_detour_tracks=2,
                future_hops=tuple(
                    _external_hops_after(
                        steps,
                        cells,
                        rules,
                        port_stub_um,
                        start_index=step_idx,
                        terminal=terminal,
                    )
                ),
                after_step_idx=step_idx + 1,
                post_local_segments=_local_step_segments(
                    next_step,
                    cells,
                    rules,
                    port_stub_um,
                ),
            )
        )
    last_step = steps[-1]
    last_cell = cells[last_step.mrr_id]
    src = _port_route_point(last_cell, last_step.out_port, rules, port_stub_um)
    specs.append(
        _ExternalHopSpec(
            src=src,
            dst=terminal,
            src_label=f"{last_step.mrr_id}.{last_step.out_port}",
            dst_label=f"O{path.output_port}",
            repeated_crossing_penalty_scale=_repeated_crossing_penalty_scale_for_hop(
                last_step.out_port,
                None,
                rules,
            ),
            preferred_bend_x=corridor_slots.get(_corridor_key(src, terminal)),
            reserve_space_penalty=True,
            base_detour_tracks=1,
            future_hops=(),
            after_step_idx=None,
            post_local_segments=(),
        )
    )
    return specs

def _local_step_segments(
    step: RouteStep,
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    port_stub_um: float,
) -> tuple[Segment, ...]:
    cell = cells[step.mrr_id]
    in_route = _port_route_point(cell, step.in_port, rules, port_stub_um)
    in_escape = _port_escape_point(cell, step.in_port, rules, port_stub_um)
    in_stub = _port_stub_point(cell, step.in_port, port_stub_um)
    in_port = cell.port_xy(step.in_port)
    out_port = cell.port_xy(step.out_port)
    out_stub = _port_stub_point(cell, step.out_port, port_stub_um)
    out_escape = _port_escape_point(cell, step.out_port, rules, port_stub_um)
    out_route = _port_route_point(cell, step.out_port, rules, port_stub_um)
    segments = (
        (in_route, in_escape),
        (in_escape, in_stub),
        (in_stub, in_port),
        (out_port, out_stub),
        (out_stub, out_escape),
        (out_escape, out_route),
    )
    return tuple(segment for segment in segments if not _same_point(segment[0], segment[1]))

def _external_hop_alternatives(
    spec: _ExternalHopSpec,
    branch: _PathBranch,
    path: Path,
    occupied: list[OccupiedRouteSegment],
    reserved_port_access: list[Segment],
    reserved_port_access_guards: list[Segment] | list[OccupiedRouteSegment],
    soft_reserved_port_access: list[Segment],
    obstacles: list[Obstacle],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
    *,
    forbidden_points: set[Point],
    port_access: PortAccessLegality | None,
    grid_bounds: RoutingWindow,
    ripup_pass_idx: int,
    alternatives_per_hop: int,
    penalized_crossing_owners: set[int],
    source_crossings: set[_CrossingSource],
    allocation_crossings: set[_CrossingSource] | None = None,
) -> list[tuple[Point, ...]]:
    alternatives: list[tuple[Point, ...]] = []
    seen: set[tuple[Point, ...]] = set()
    local_history = _clone_history_cost(branch.history_cost)
    last_error: RoutingError | None = None
    blockers = _occupied_blocker_segments(occupied)
    allocation_crossings = allocation_crossings or set()
    reserved_crossing_owners = _reserved_future_crossing_owners(
        list(spec.future_hops),
        occupied,
        path.input_port,
        rules,
    ) | _source_reserved_crossing_owners(
        set(source_crossings) | set(allocation_crossings),
        spec.src,
        spec.dst,
        rules,
        owner_input=path.input_port,
    )
    candidate_blockers = blockers + branch.external_segments + branch.local_segments + reserved_port_access
    current_self_segments = (
        branch.external_segments
        + branch.local_segments
        + list(spec.post_local_segments)
    )
    self_spacing_blockers = list(spec.post_local_segments)

    use_deterministic_candidates = (
        rules.uses_db_cost
        or ripup_pass_idx > 0
        or rules.max_astar_pops is not None
    )
    if use_deterministic_candidates:
        deterministic_candidates = _deterministic_hop_candidates(
            spec.src,
            spec.dst,
            obstacles,
            rules,
            base_detour_tracks=spec.base_detour_tracks,
            grid=grid,
            port_access=port_access,
            blockers=candidate_blockers,
        )
        if spec.riser_displacement_tracks:
            deterministic_candidates = tuple(
                displaced
                for candidate in deterministic_candidates
                if (
                    displaced := _displace_candidate_risers(
                        candidate,
                        rules,
                        spec.riser_displacement_tracks,
                        src_label=spec.src_label,
                        dst_label=spec.dst_label,
                    )
                )
                is not None
            )
            deterministic_candidates = tuple(
                _dedupe_candidate_routes(deterministic_candidates)
            )
        ordered_candidates = (
            _sort_candidates_for_hop(
                deterministic_candidates,
                local_history,
                grid,
                obstacles,
                port_access,
                rules,
                candidate_blockers,
            )
            if rules.uses_db_cost
            else _sort_candidates_by_history(deterministic_candidates, local_history, grid)
        )
        for candidate in ordered_candidates:
            try:
                candidate_label = (
                    "riser-displaced deterministic hop candidate"
                    if spec.riser_displacement_tracks
                    else "deterministic hop candidate"
                )
                waypoint_external = _route_external_hop_via_waypoints(
                    candidate,
                    candidate_blockers,
                    obstacles,
                    grid,
                    rules,
                    src_label=spec.src_label,
                    dst_label=spec.dst_label,
                    forbidden_points=forbidden_points,
                    current_segments=current_self_segments,
                    current_external_segments=branch.external_segments,
                    occupied_segments=occupied,
                    crossing_count_by_pair=_crossing_count_by_pair_for_current_net(
                        occupied,
                        branch.external_segments,
                        path.input_port,
                    ),
                    crossing_sources_by_pair=_crossing_sources_by_pair_for_current_net(
                        occupied,
                        branch.external_segments,
                        path.input_port,
                    ),
                    soft_blockers=soft_reserved_port_access,
                    reserved_spacing_blockers=self_spacing_blockers,
                    reserved_guard_blockers=_guard_segments(reserved_port_access_guards),
                    reserved_crossing_owners=reserved_crossing_owners,
                    deferred_crossing_owners=penalized_crossing_owners | _deferred_route_owners(
                        list(spec.future_hops),
                        port_access,
                        occupied,
                        path.input_port,
                    ),
                    preferred_bend_x=spec.preferred_bend_x,
                    reserve_space_penalty=spec.reserve_space_penalty,
                    port_access=port_access,
                    grid_bounds=grid_bounds,
                    base_detour_tracks=spec.base_detour_tracks,
                    ripup_pass_idx=ripup_pass_idx,
                    history_cost=local_history,
                    candidate_label=candidate_label,
                    repeated_crossing_penalty_scale=spec.repeated_crossing_penalty_scale,
                    candidate_window_expansion_tracks=(
                        _remediation_window_expansion_tracks(
                            rules,
                            spec.riser_displacement_tracks,
                        )
                    ),
                )
            except RoutingError as exc:
                last_error = exc
                continue
            if waypoint_external not in seen:
                alternatives.append(waypoint_external)
                seen.add(waypoint_external)
            local_history.bump_route(
                _segments_from_points(waypoint_external),
                grid[0],
                grid[1],
                amount=rules.hairpin_penalty_um * float(len(alternatives)),
            )
            if len(alternatives) >= alternatives_per_hop:
                return alternatives

        if spec.riser_displacement_tracks:
            if alternatives:
                return alternatives
            if last_error is None:
                raise RoutingError(
                    "whole-net riser displacement produced no port-legal candidate"
                )
            raise last_error

    for alt_idx in range(alternatives_per_hop):
        external: tuple[Point, ...] | None = None
        for detour_tracks in _detour_track_sequence(spec.base_detour_tracks, rules):
            try:
                external = _astar_route(
                    spec.src,
                    spec.dst,
                    blockers + branch.external_segments + branch.local_segments + reserved_port_access,
                    obstacles,
                    grid,
                    rules,
                    src_label=spec.src_label,
                    dst_label=spec.dst_label,
                    forbidden_points=forbidden_points,
                    current_segments=current_self_segments,
                    current_external_segments=branch.external_segments,
                    occupied_segments=occupied,
                    crossing_count_by_pair=_crossing_count_by_pair_for_current_net(
                        occupied,
                        branch.external_segments,
                        path.input_port,
                    ),
                    crossing_sources_by_pair=_crossing_sources_by_pair_for_current_net(
                        occupied,
                        branch.external_segments,
                        path.input_port,
                    ),
                    soft_blockers=soft_reserved_port_access,
                    reserved_spacing_blockers=self_spacing_blockers,
                    reserved_guard_blockers=_guard_segments(reserved_port_access_guards),
                    reserved_crossing_owners=reserved_crossing_owners,
                    deferred_crossing_owners=penalized_crossing_owners | _deferred_route_owners(
                        list(spec.future_hops),
                        port_access,
                        occupied,
                        path.input_port,
                    ),
                    preferred_bend_x=spec.preferred_bend_x,
                    reserve_space_penalty=spec.reserve_space_penalty,
                    port_access=port_access,
                    routing_window=_hop_routing_window(
                        spec.src,
                        spec.dst,
                        obstacles,
                        rules,
                        grid_bounds,
                        detour_tracks=detour_tracks,
                        ripup_pass_idx=ripup_pass_idx,
                    ),
                    history_cost=local_history,
                    repeated_crossing_penalty_scale=spec.repeated_crossing_penalty_scale,
                )
                break
            except RoutingError as exc:
                last_error = exc
        if external is None:
            break
        if external not in seen:
            alternatives.append(external)
            seen.add(external)
        local_history.bump_route(
            _segments_from_points(external),
            grid[0],
            grid[1],
            amount=rules.hairpin_penalty_um * float(alt_idx + 1),
        )
    if not alternatives:
        assert last_error is not None
        raise last_error
    return alternatives

def _route_external_hop(
    src: Point,
    dst: Point,
    blockers: list[Segment],
    obstacles: list[Obstacle],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
    *,
    src_label: str,
    dst_label: str,
    forbidden_points: set[Point],
    current_segments: list[Segment],
    current_external_segments: list[Segment] | None = None,
    occupied_segments: list[OccupiedRouteSegment],
    crossing_count_by_pair: dict[tuple[int, int], int],
    crossing_sources_by_pair: dict[tuple[int, int], tuple[str, ...]] | None = None,
    soft_blockers: list[Segment],
    reserved_spacing_blockers: list[Segment],
    reserved_guard_blockers: list[Segment] | list[OccupiedRouteSegment],
    reserved_crossing_owners: set[int] | None = None,
    deferred_crossing_owners: set[int] | None = None,
    preferred_bend_x: float | None = None,
    reserve_space_penalty: bool = False,
    port_access: PortAccessLegality | None = None,
    grid_bounds: RoutingWindow | None = None,
    base_detour_tracks: int = 1,
    ripup_pass_idx: int = 0,
    history_cost: HistoryCost | None = None,
    future_local_segments: tuple[Segment, ...] = (),
    source_crossings: set[_CrossingSource] | None = None,
    allocation_crossings: set[_CrossingSource] | None = None,
    repeated_crossing_penalty_scale: float = 1.0,
) -> tuple[Point, ...]:
    grid_bounds = grid_bounds or _routing_grid_bounds(grid)
    last_error: RoutingError | None = None
    source_crossings = source_crossings or set()
    allocation_crossings = allocation_crossings or set()
    reserved_owner_sources = set(source_crossings) | set(allocation_crossings)
    current_external_segments = current_external_segments or current_segments
    current_self_segments = current_segments + list(future_local_segments)
    self_spacing_blockers = reserved_spacing_blockers + list(future_local_segments)
    source_candidates = _sort_candidates_by_history(
        _source_detour_candidates(
            src,
            dst,
            source_crossings,
            grid,
            rules,
            owner_input=port_access.current_net_id if port_access is not None else None,
        ),
        history_cost,
        grid,
    )
    source_reserved_crossing_owners = _source_reserved_crossing_owners(
        reserved_owner_sources,
        src,
        dst,
        rules,
        owner_input=port_access.current_net_id if port_access is not None else None,
    )
    hard_source_reserved_crossing_owners = (
        source_reserved_crossing_owners
        if rules.hard_source_crossing_reservation
        else set()
    )
    soft_source_reserved_crossing_owners = (
        set()
        if rules.hard_source_crossing_reservation
        else source_reserved_crossing_owners
    )
    combined_reserved_crossing_owners = (
        set(reserved_crossing_owners or set()) | hard_source_reserved_crossing_owners
    )
    combined_deferred_crossing_owners = (
        set(deferred_crossing_owners or set()) | soft_source_reserved_crossing_owners
    )
    source_detour_errors: list[str] = []
    for candidate in source_candidates:
        try:
            return _route_external_hop_via_waypoints(
                candidate,
                blockers,
                obstacles,
                grid,
                rules,
                src_label=src_label,
                dst_label=dst_label,
                forbidden_points=forbidden_points,
                current_segments=current_self_segments,
                current_external_segments=current_external_segments,
                occupied_segments=occupied_segments,
                crossing_count_by_pair=crossing_count_by_pair,
                crossing_sources_by_pair=crossing_sources_by_pair,
                soft_blockers=soft_blockers,
                reserved_spacing_blockers=self_spacing_blockers,
                reserved_guard_blockers=reserved_guard_blockers,
                reserved_crossing_owners=combined_reserved_crossing_owners,
                deferred_crossing_owners=combined_deferred_crossing_owners,
                preferred_bend_x=preferred_bend_x,
                reserve_space_penalty=reserve_space_penalty,
                port_access=port_access,
                grid_bounds=grid_bounds,
                base_detour_tracks=base_detour_tracks,
                ripup_pass_idx=ripup_pass_idx,
                history_cost=history_cost,
                candidate_label="source detour",
                repeated_crossing_penalty_scale=repeated_crossing_penalty_scale,
            )
        except RoutingError as exc:
            last_error = exc
            source_detour_errors.append(str(exc))
    use_deterministic_candidates = (
        rules.uses_db_cost
        or ripup_pass_idx > 0
        or rules.max_astar_pops is not None
    )
    if use_deterministic_candidates:
        deterministic_candidates = _deterministic_hop_candidates(
            src,
            dst,
            obstacles,
            rules,
            base_detour_tracks=base_detour_tracks,
            grid=grid,
            port_access=port_access,
            blockers=blockers,
        )
        ordered_candidates = (
            _sort_candidates_for_hop(
                deterministic_candidates,
                history_cost,
                grid,
                obstacles,
                port_access,
                rules,
                blockers,
            )
            if rules.uses_db_cost
            else _sort_candidates_by_history(deterministic_candidates, history_cost, grid)
        )
        for candidate in ordered_candidates:
            if candidate in source_candidates:
                continue
            try:
                return _route_external_hop_via_waypoints(
                    candidate,
                    blockers,
                    obstacles,
                    grid,
                    rules,
                    src_label=src_label,
                    dst_label=dst_label,
                    forbidden_points=forbidden_points,
                    current_segments=current_self_segments,
                    current_external_segments=current_external_segments,
                    occupied_segments=occupied_segments,
                    crossing_count_by_pair=crossing_count_by_pair,
                    crossing_sources_by_pair=crossing_sources_by_pair,
                    soft_blockers=soft_blockers,
                    reserved_spacing_blockers=self_spacing_blockers,
                    reserved_guard_blockers=reserved_guard_blockers,
                    reserved_crossing_owners=combined_reserved_crossing_owners,
                    deferred_crossing_owners=combined_deferred_crossing_owners,
                    preferred_bend_x=preferred_bend_x,
                    reserve_space_penalty=reserve_space_penalty,
                    port_access=port_access,
                    grid_bounds=grid_bounds,
                    base_detour_tracks=base_detour_tracks,
                    ripup_pass_idx=ripup_pass_idx,
                    history_cost=history_cost,
                    candidate_label="deterministic hop candidate",
                    repeated_crossing_penalty_scale=repeated_crossing_penalty_scale,
                )
            except RoutingError as exc:
                last_error = exc
    if ripup_pass_idx > 0 and source_reserved_crossing_owners:
        for candidate in _sort_candidates_by_history(
            _reserved_owner_avoidance_hop_candidates(
                src,
                dst,
                occupied_segments,
                source_reserved_crossing_owners,
                forbidden_points,
                obstacles,
                grid,
                rules,
                base_detour_tracks=base_detour_tracks,
            ),
            history_cost,
            grid,
        ):
            try:
                return _route_external_hop_via_waypoints(
                    candidate,
                    blockers,
                    obstacles,
                    grid,
                    rules,
                    src_label=src_label,
                    dst_label=dst_label,
                    forbidden_points=forbidden_points,
                    current_segments=current_self_segments,
                    current_external_segments=current_external_segments,
                    occupied_segments=occupied_segments,
                    crossing_count_by_pair=crossing_count_by_pair,
                    crossing_sources_by_pair=crossing_sources_by_pair,
                    soft_blockers=soft_blockers,
                    reserved_spacing_blockers=self_spacing_blockers,
                    reserved_guard_blockers=reserved_guard_blockers,
                    reserved_crossing_owners=combined_reserved_crossing_owners,
                    deferred_crossing_owners=combined_deferred_crossing_owners,
                    preferred_bend_x=preferred_bend_x,
                    reserve_space_penalty=reserve_space_penalty,
                    port_access=port_access,
                    grid_bounds=grid_bounds,
                    base_detour_tracks=base_detour_tracks,
                    ripup_pass_idx=ripup_pass_idx,
                    history_cost=history_cost,
                    candidate_label="reserved-owner avoidance candidate",
                    repeated_crossing_penalty_scale=repeated_crossing_penalty_scale,
                )
            except RoutingError as exc:
                last_error = exc
    if (
        ripup_pass_idx > 0
        and last_error is not None
        and "same-net self conflict" in str(last_error)
    ):
        for candidate in _sort_candidates_by_history(
            _same_net_avoidance_hop_candidates(
                src,
                dst,
                current_self_segments,
                obstacles,
                grid,
                rules,
                base_detour_tracks=base_detour_tracks,
                avoid_segments=tuple(_guard_segments(reserved_guard_blockers)),
            ),
            history_cost,
            grid,
        ):
            try:
                return _route_external_hop_via_waypoints(
                    candidate,
                    blockers,
                    obstacles,
                    grid,
                    rules,
                    src_label=src_label,
                    dst_label=dst_label,
                    forbidden_points=forbidden_points,
                    current_segments=current_self_segments,
                    current_external_segments=current_external_segments,
                    occupied_segments=occupied_segments,
                    crossing_count_by_pair=crossing_count_by_pair,
                    crossing_sources_by_pair=crossing_sources_by_pair,
                    soft_blockers=soft_blockers,
                    reserved_spacing_blockers=self_spacing_blockers,
                    reserved_guard_blockers=reserved_guard_blockers,
                    reserved_crossing_owners=combined_reserved_crossing_owners,
                    deferred_crossing_owners=combined_deferred_crossing_owners,
                    preferred_bend_x=preferred_bend_x,
                    reserve_space_penalty=reserve_space_penalty,
                    port_access=port_access,
                    grid_bounds=grid_bounds,
                    base_detour_tracks=base_detour_tracks,
                    ripup_pass_idx=ripup_pass_idx,
                    history_cost=history_cost,
                    candidate_label="same-net avoidance candidate",
                    repeated_crossing_penalty_scale=repeated_crossing_penalty_scale,
                )
            except RoutingError as exc:
                last_error = exc
    if (
        rules.source_hint_bounded_only
        and ripup_pass_idx > 0
        and hard_source_reserved_crossing_owners
    ):
        detail = f": {last_error}" if last_error is not None else ""
        owners = ",".join(f"I{owner}" for owner in sorted(source_reserved_crossing_owners))
        detail += _source_hint_debug_detail(
            reserved_owner_sources,
            src,
            dst,
            rules,
            owner_input=port_access.current_net_id if port_access is not None else None,
        )
        if source_detour_errors:
            samples = "; ".join(source_detour_errors[:3])
            detail = f"{detail}; source_detour_failed={len(source_detour_errors)} samples={samples}"
        raise RoutingError(
            f"source-reserved hop exhausted bounded candidates before A*: "
            f"reserved_owners={owners}{detail}"
        )
    if (
        rules.capped_ripup_bounded_only
        and not rules.uses_db_cost
        and ripup_pass_idx > 0
        and rules.max_astar_pops is not None
        and (not source_reserved_crossing_owners or rules.hard_source_crossing_reservation)
    ):
        detail = f": {last_error}" if last_error is not None else ""
        detail += _source_hint_debug_detail(
            reserved_owner_sources,
            src,
            dst,
            rules,
            owner_input=port_access.current_net_id if port_access is not None else None,
        )
        raise RoutingError(
            "capped rip-up hop exhausted bounded candidates before A*"
            f"{detail}"
        )
    for detour_tracks in _detour_track_sequence(base_detour_tracks, rules):
        try:
            external = _astar_route(
                src,
                dst,
                blockers,
                obstacles,
                grid,
                rules,
                src_label=src_label,
                dst_label=dst_label,
                forbidden_points=forbidden_points,
                current_segments=current_self_segments,
                current_external_segments=current_external_segments,
                occupied_segments=occupied_segments,
                crossing_count_by_pair=crossing_count_by_pair,
                crossing_sources_by_pair=crossing_sources_by_pair,
                soft_blockers=soft_blockers,
                reserved_spacing_blockers=self_spacing_blockers,
                reserved_guard_blockers=_guard_segments(reserved_guard_blockers),
                reserved_crossing_owners=combined_reserved_crossing_owners,
                deferred_crossing_owners=combined_deferred_crossing_owners,
                preferred_bend_x=preferred_bend_x,
                reserve_space_penalty=reserve_space_penalty,
                port_access=port_access,
                routing_window=_hop_routing_window(
                    src,
                    dst,
                    obstacles,
                    rules,
                    grid_bounds,
                    detour_tracks=detour_tracks,
                    ripup_pass_idx=ripup_pass_idx,
                ),
                history_cost=history_cost,
                repeated_crossing_penalty_scale=repeated_crossing_penalty_scale,
            )
            same_net_reason = _polyline_same_net_conflict_reason(
                external,
                current_external_segments,
                rules,
                join_points=(src, dst),
            )
            if same_net_reason is not None and not _same_net_reason_is_hairpin(same_net_reason):
                last_error = RoutingError(
                    f"A* route creates same-net self conflict; {same_net_reason}"
                )
                if history_cost is not None:
                    history_cost.bump_route(
                        _segments_from_points(external),
                        grid[0],
                        grid[1],
                        amount=rules.hairpin_penalty_um,
                    )
                continue
            return external
        except RoutingError as exc:
            last_error = exc
    assert last_error is not None
    approach = _port_approach_point(dst_label, dst, rules)
    if (
        rules.port_approach_fallback
        and approach is not None
        and not _same_point(approach, src)
        and "port_access" in str(last_error)
    ):
        try:
            return _route_external_hop_via_approach(
                src,
                approach,
                dst,
                blockers,
                obstacles,
                grid,
                rules,
                src_label=src_label,
                dst_label=dst_label,
                forbidden_points=forbidden_points,
                current_segments=current_self_segments,
                current_external_segments=current_external_segments,
                occupied_segments=occupied_segments,
                crossing_count_by_pair=crossing_count_by_pair,
                crossing_sources_by_pair=crossing_sources_by_pair,
                soft_blockers=soft_blockers,
                reserved_spacing_blockers=self_spacing_blockers,
                reserved_guard_blockers=reserved_guard_blockers,
                reserved_crossing_owners=combined_reserved_crossing_owners,
                deferred_crossing_owners=combined_deferred_crossing_owners,
                preferred_bend_x=preferred_bend_x,
                reserve_space_penalty=reserve_space_penalty,
                port_access=port_access,
                grid_bounds=grid_bounds,
                base_detour_tracks=base_detour_tracks,
                ripup_pass_idx=ripup_pass_idx,
                history_cost=history_cost,
                repeated_crossing_penalty_scale=repeated_crossing_penalty_scale,
            )
        except RoutingError as exc:
            last_error = exc
    if source_detour_errors:
        sample_errors = "; ".join(source_detour_errors[:3])
        raise RoutingError(
            f"{last_error}; source_detour_failed={len(source_detour_errors)} "
            f"samples={sample_errors}"
        )
    raise last_error

def _deterministic_hop_candidates(
    src: Point,
    dst: Point,
    obstacles: list[Obstacle],
    rules: RoutingRules,
    *,
    base_detour_tracks: int,
    grid: tuple[tuple[float, ...], tuple[float, ...]] | None = None,
    port_access: PortAccessLegality | None = None,
    blockers: list[Segment] | tuple[Segment, ...] = (),
) -> tuple[tuple[Point, ...], ...]:
    max_tracks = _detour_tracks(base_detour_tracks, rules)
    if not rules.uses_db_cost:
        return tuple(
            _bend_spaced_candidates(
                _candidate_routes(
                    src,
                    dst,
                    rules.grid_pitch_um,
                    max_tracks,
                    obstacles,
                ),
                rules,
            )[:64]
        )
    raw_candidates = (
        list(_l_shape_route_options(src, dst))
        + list(
            _delayed_l_route_options(
                src,
                dst,
                obstacles,
                rules,
                max_tracks,
                grid=grid,
                port_access=port_access,
            )
        )
        + _candidate_routes(
            src,
            dst,
            rules.grid_pitch_um,
            max_tracks,
            obstacles,
        )
    )
    candidates = _dedupe_candidate_routes(raw_candidates)
    return tuple(
        _bend_spaced_candidates(
            sorted(
                candidates,
                key=lambda points: _candidate_route_sort_key(
                    points,
                    obstacles,
                    port_access,
                    rules,
                    blockers,
                ),
            ),
            rules,
        )[:64]
    )

def _delayed_l_route_options(
    src: Point,
    dst: Point,
    obstacles: list[Obstacle],
    rules: RoutingRules,
    max_tracks: int,
    *,
    grid: tuple[tuple[float, ...], tuple[float, ...]] | None = None,
    port_access: PortAccessLegality | None = None,
) -> tuple[tuple[Point, ...], ...]:
    if _same_point(src, dst):
        return ()
    if abs(src[0] - dst[0]) < EPS or abs(src[1] - dst[1]) < EPS:
        return ()
    span_um = max(1, max_tracks) * rules.grid_pitch_um + rules.turn_guard_um
    x_lo, x_hi = sorted((src[0], dst[0]))
    y_lo, y_hi = sorted((src[1], dst[1]))
    x_values = {src[0], dst[0]}
    y_values = {src[1], dst[1]}
    for obstacle in obstacles:
        x_values.update((obstacle.left - rules.turn_guard_um, obstacle.right + rules.turn_guard_um))
        y_values.update((obstacle.bottom - rules.turn_guard_um, obstacle.top + rules.turn_guard_um))
    if grid is not None:
        x_values.update(_bounded_candidate_tracks(grid[0], x_lo, x_hi, span_um))
        y_values.update(_bounded_candidate_tracks(grid[1], y_lo, y_hi, span_um))
    if port_access is not None:
        for access_point in port_access.plan.points_by_cell_port.values():
            for point in (
                access_point.port_xy,
                access_point.stub_xy,
                access_point.escape_xy,
                access_point.route_xy,
            ):
                x_values.update((point[0] - rules.turn_guard_um, point[0] + rules.turn_guard_um))
                y_values.update((point[1] - rules.turn_guard_um, point[1] + rules.turn_guard_um))
        for region in port_access.plan.reserved_regions:
            for point in region.segment:
                x_values.update((point[0] - rules.turn_guard_um, point[0] + rules.turn_guard_um))
                y_values.update((point[1] - rules.turn_guard_um, point[1] + rules.turn_guard_um))
    candidates: list[tuple[Point, ...]] = []
    for x in sorted(_canonical_track(value) for value in x_values):
        if x_lo - span_um - EPS <= x <= x_hi + span_um + EPS and not (
            abs(x - src[0]) < EPS or abs(x - dst[0]) < EPS
        ):
            candidates.append(_merge_collinear_points((src, (x, src[1]), (x, dst[1]), dst)))
    for y in sorted(_canonical_track(value) for value in y_values):
        if y_lo - span_um - EPS <= y <= y_hi + span_um + EPS and not (
            abs(y - src[1]) < EPS or abs(y - dst[1]) < EPS
        ):
            candidates.append(_merge_collinear_points((src, (src[0], y), (dst[0], y), dst)))
    return tuple(_dedupe_candidate_routes(candidates))

def _bounded_candidate_tracks(
    tracks: tuple[float, ...],
    lower: float,
    upper: float,
    margin: float,
) -> tuple[float, ...]:
    values = [
        track
        for track in tracks
        if lower - margin - EPS <= track <= upper + margin + EPS
    ]
    center = 0.5 * (lower + upper)
    return tuple(
        sorted(
            values,
            key=lambda track: (abs(track - center), abs(track - lower), track),
        )[:16]
    )

def _dedupe_candidate_routes(
    candidates: list[tuple[Point, ...]] | tuple[tuple[Point, ...], ...],
) -> list[tuple[Point, ...]]:
    unique: list[tuple[Point, ...]] = []
    seen: set[tuple[Point, ...]] = set()
    for candidate in candidates:
        cleaned = _dedupe_points(_merge_collinear_points(candidate))
        if len(cleaned) < 2 or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return unique

def _same_net_avoidance_hop_candidates(
    src: Point,
    dst: Point,
    current_segments: list[Segment],
    obstacles: list[Obstacle],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
    *,
    base_detour_tracks: int,
    avoid_segments: tuple[Segment, ...] = (),
) -> tuple[tuple[Point, ...], ...]:
    if not current_segments:
        return ()
    candidates: list[tuple[Point, ...]] = []
    seen: set[tuple[Point, ...]] = set()
    for base in _deterministic_hop_candidates(
        src,
        dst,
        obstacles,
        rules,
        base_detour_tracks=base_detour_tracks,
        grid=grid,
    ):
        routed_segments: list[Segment] = []
        for idx, segment in enumerate(_segments_from_points(base)):
            if rules.uses_db_cost:
                conflict = _same_net_candidate_conflict(
                    segment,
                    tuple(current_segments) + tuple(routed_segments),
                    join_points=(src, dst),
                )
            else:
                conflict = _same_net_orthogonal_conflict(
                    segment,
                    tuple(current_segments) + tuple(routed_segments),
                    join_points=(src, dst),
                )
            if conflict is None:
                routed_segments.append(segment)
                continue
            point, prior = conflict
            source = _CrossingSource(
                owner_input=-1,
                crossed_input=-1,
                point=point,
                source_segment=segment,
                crossed_segment=prior,
            )
            replacements = list(_source_detour_replacements(segment, source, grid, rules))
            replacements.extend(
                _same_net_local_conflict_replacements(segment, prior, grid, rules)
            )
            for replacement in replacements:
                candidate = _dedupe_points(
                    _merge_collinear_points(
                        tuple(base[:idx] + replacement + base[idx + 2 :])
                    )
                )
                if candidate == base or candidate in seen:
                    continue
                if _polyline_has_same_net_conflict(
                    candidate,
                    current_segments,
                    join_points=(src, dst),
                ):
                    continue
                if _polyline_has_guard_conflict(candidate, avoid_segments, rules):
                    continue
                seen.add(candidate)
                candidates.append(candidate)
            break
    for candidate in _same_net_envelope_hop_candidates(
        src,
        dst,
        current_segments,
        avoid_segments,
        grid,
        rules,
    ):
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return tuple(_bend_spaced_candidates(candidates, rules)[:64])

def _reserved_owner_avoidance_hop_candidates(
    src: Point,
    dst: Point,
    occupied_segments: list[OccupiedRouteSegment],
    reserved_owners: set[int],
    forbidden_points: set[Point],
    obstacles: list[Obstacle],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
    *,
    base_detour_tracks: int,
) -> tuple[tuple[Point, ...], ...]:
    if not reserved_owners:
        return ()
    reserved_external = [
        item
        for item in occupied_segments
        if item.kind == "external" and item.owner_input in reserved_owners
    ]
    if not reserved_external:
        return ()

    candidates: list[tuple[Point, ...]] = []
    seen: set[tuple[Point, ...]] = set()
    for base in _deterministic_hop_candidates(
        src,
        dst,
        obstacles,
        rules,
        base_detour_tracks=base_detour_tracks,
        grid=grid,
    ):
        for idx, segment in enumerate(_segments_from_points(base)):
            replacement_added = False
            for occupied in reserved_external:
                point = _orthogonal_crossing_point(segment, occupied.segment)
                if point is None or occupied.owner_input is None:
                    continue
                source = _CrossingSource(
                    owner_input=-1,
                    crossed_input=occupied.owner_input,
                    point=point,
                    source_segment=segment,
                    crossed_segment=occupied.segment,
                )
                for replacement in _source_detour_replacements(segment, source, grid, rules):
                    candidate = _dedupe_points(
                        _merge_collinear_points(
                            tuple(base[:idx] + replacement + base[idx + 2 :])
                        )
                    )
                    if candidate == base or candidate in seen:
                        continue
                    if _polyline_crosses_reserved_owner(
                        candidate,
                        reserved_external,
                        rules,
                    ):
                        continue
                    if _polyline_intersects_obstacle(candidate, obstacles):
                        continue
                    if _polyline_hits_forbidden_point(
                        candidate,
                        forbidden_points,
                        allowed_touch_points=(src, dst),
                    ):
                        continue
                    seen.add(candidate)
                    candidates.append(candidate)
                    replacement_added = True
                for replacement in _reserved_owner_local_envelope_replacements(
                    segment,
                    occupied,
                    reserved_external,
                    grid,
                    rules,
                ):
                    candidate = _dedupe_points(
                        _merge_collinear_points(
                            tuple(base[:idx] + replacement + base[idx + 2 :])
                        )
                    )
                    if candidate == base or candidate in seen:
                        continue
                    if _polyline_crosses_reserved_owner(
                        candidate,
                        reserved_external,
                        rules,
                    ):
                        continue
                    if _polyline_intersects_obstacle(candidate, obstacles):
                        continue
                    if _polyline_hits_forbidden_point(
                        candidate,
                        forbidden_points,
                        allowed_touch_points=(src, dst),
                    ):
                        continue
                    seen.add(candidate)
                    candidates.append(candidate)
                    replacement_added = True
            if replacement_added:
                break
    for candidate in _reserved_owner_envelope_hop_candidates(
        src,
        dst,
        occupied_segments,
        reserved_external,
        forbidden_points,
        obstacles,
        grid,
        rules,
    ):
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return tuple(_bend_spaced_candidates(candidates, rules)[:64])

def _reserved_owner_local_envelope_replacements(
    segment: Segment,
    crossed: OccupiedRouteSegment,
    reserved_external: list[OccupiedRouteSegment],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
) -> tuple[tuple[Point, ...], ...]:
    axis = _segment_axis(segment)
    if axis not in {"h", "v"}:
        return ()
    cluster = _connected_reserved_external_cluster(crossed, reserved_external)
    if not cluster:
        return ()
    xs = [point[0] for item in cluster for point in item.segment]
    ys = [point[1] for item in cluster for point in item.segment]
    guard = max(
        rules.grid_pitch_um,
        2.0 * rules.bend_radius_um,
        _effective_spacing_threshold(
            rules.min_spacing_um, rules.waveguide_width_um
        ),
        _effective_spacing_threshold(
            rules.min_crossing_clearance_um or 0.0,
            rules.waveguide_width_um,
        ),
    )
    x_tracks, y_tracks = grid
    x_escape = _local_escape_tracks(x_tracks, min(xs), max(xs), guard)
    y_escape = _local_escape_tracks(y_tracks, min(ys), max(ys), guard)
    if not x_escape or not y_escape:
        return ()

    start, end = segment
    raw: list[tuple[Point, ...]] = []
    if axis == "h":
        for y in y_escape:
            raw.append((start, (start[0], y), (end[0], y), end))
            for x in x_escape:
                raw.append((start, (x, start[1]), (x, y), (end[0], y), end))
                raw.append((start, (start[0], y), (x, y), (x, end[1]), end))
    else:
        for x in x_escape:
            raw.append((start, (x, start[1]), (x, end[1]), end))
            for y in y_escape:
                raw.append((start, (start[0], y), (x, y), (x, end[1]), end))
                raw.append((start, (x, start[1]), (x, y), (end[0], y), end))

    candidates: list[tuple[Point, ...]] = []
    seen: set[tuple[Point, ...]] = set()
    for candidate in sorted(
        (_dedupe_points(_merge_collinear_points(points)) for points in raw),
        key=lambda points: (_polyline_length(points), _bend_count(points), len(points), points),
    ):
        if candidate in seen or len(candidate) < 2:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return tuple(_bend_spaced_candidates(candidates, rules)[:16])

def _connected_reserved_external_cluster(
    seed: OccupiedRouteSegment,
    reserved_external: list[OccupiedRouteSegment],
) -> tuple[OccupiedRouteSegment, ...]:
    cluster: list[OccupiedRouteSegment] = [seed]
    changed = True
    while changed:
        changed = False
        for item in reserved_external:
            if item in cluster or item.owner_input != seed.owner_input:
                continue
            if any(
                _segments_connected(item.segment, existing.segment)
                for existing in cluster
            ):
                cluster.append(item)
                changed = True
    return tuple(cluster)

def _segments_connected(left: Segment, right: Segment) -> bool:
    return (
        _segments_collinear_overlap(left, right)
        or _orthogonal_crossing_point(left, right) is not None
        or _segment_contact_point(left, right) is not None
    )

def _local_escape_tracks(
    tracks: tuple[float, ...],
    lo: float,
    hi: float,
    guard: float,
) -> tuple[float, ...]:
    if not tracks:
        return ()
    min_track, max_track = min(tracks), max(tracks)
    raw = set(_escape_tracks(tracks, lo, hi, guard))
    for multiplier in (1.0, 2.0, 3.0):
        raw.add(_canonical_track(lo - multiplier * guard))
        raw.add(_canonical_track(hi + multiplier * guard))

    below = sorted(
        [
            value
            for value in raw
            if min_track - EPS <= value <= lo - guard + EPS
        ],
        key=lambda value: (lo - value, -value),
    )
    above = sorted(
        [
            value
            for value in raw
            if hi + guard - EPS <= value <= max_track + EPS
        ],
        key=lambda value: (value - hi, value),
    )
    values: list[float] = []
    side_idx = 0
    sides = [above[:4], below[:4]]
    while len(values) < 8 and any(sides):
        side = sides[side_idx % 2]
        if side:
            value = side.pop(0)
            if value not in values:
                values.append(value)
        side_idx += 1
    return tuple(values)

def _reserved_owner_envelope_hop_candidates(
    src: Point,
    dst: Point,
    occupied_segments: list[OccupiedRouteSegment],
    reserved_external: list[OccupiedRouteSegment],
    forbidden_points: set[Point],
    obstacles: list[Obstacle],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
) -> tuple[tuple[Point, ...], ...]:
    if not reserved_external:
        return ()
    xs = [point[0] for item in reserved_external for point in item.segment]
    ys = [point[1] for item in reserved_external for point in item.segment]
    guard = max(
        rules.grid_pitch_um,
        2.0 * rules.bend_radius_um,
        _effective_spacing_threshold(
            rules.min_spacing_um, rules.waveguide_width_um
        ),
        _effective_spacing_threshold(
            rules.min_crossing_clearance_um or 0.0,
            rules.waveguide_width_um,
        ),
    )
    x_tracks, y_tracks = grid
    y_escape = _escape_tracks(y_tracks, min(ys), max(ys), guard)
    x_escape = _escape_tracks(x_tracks, min(xs), max(xs), guard)
    raw: list[tuple[Point, ...]] = []
    for y in y_escape:
        raw.append((src, (src[0], y), (dst[0], y), dst))
    for x in x_escape:
        raw.append((src, (x, src[1]), (x, dst[1]), dst))
    for y in y_escape:
        for x in x_escape:
            raw.append((src, (src[0], y), (x, y), (x, dst[1]), dst))
            raw.append((src, (x, src[1]), (x, y), (dst[0], y), dst))

    candidates: list[tuple[Point, ...]] = []
    seen: set[tuple[Point, ...]] = set()
    for candidate in sorted(
        (_dedupe_points(_merge_collinear_points(points)) for points in raw),
        key=lambda points: (_polyline_length(points), _bend_count(points), len(points), points),
    ):
        if candidate in seen or len(candidate) < 2:
            continue
        if _polyline_crosses_reserved_owner(candidate, reserved_external, rules):
            continue
        if _polyline_intersects_obstacle(candidate, obstacles):
            continue
        if _polyline_hits_forbidden_point(
            candidate,
            forbidden_points,
            allowed_touch_points=(src, dst),
        ):
            continue
        if _polyline_has_occupied_non_crossing_conflict(
            candidate,
            occupied_segments,
            rules,
            allowed_touch_points=(src, dst),
        ):
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return tuple(candidates[:32])

def _polyline_crosses_reserved_owner(
    points: tuple[Point, ...],
    reserved_external: list[OccupiedRouteSegment],
    rules: RoutingRules,
) -> bool:
    for segment in _segments_from_points(points):
        for occupied in reserved_external:
            if _orthogonal_crossing_point(segment, occupied.segment) is not None:
                return True
            if _segments_collinear_overlap(segment, occupied.segment):
                return True
            if _parallel_spacing_violation(
                segment,
                occupied.segment,
                rules.min_spacing_um,
                rules.waveguide_width_um,
            ):
                return True
            contact = _segment_contact_point(segment, occupied.segment)
            if contact is not None and not any(_same_point(contact, point) for point in segment):
                return True
    return False

def _polyline_intersects_obstacle(
    points: tuple[Point, ...],
    obstacles: list[Obstacle],
) -> bool:
    return any(
        _segment_intersects_obstacle(segment, obstacle)
        for segment in _segments_from_points(points)
        for obstacle in obstacles
    )

def _polyline_hits_forbidden_point(
    points: tuple[Point, ...],
    forbidden_points: set[Point],
    *,
    allowed_touch_points: tuple[Point, ...],
) -> bool:
    if not forbidden_points:
        return False
    return any(
        _segment_hits_forbidden_point(
            segment,
            forbidden_points,
            allowed_touch_points,
        )
        for segment in _segments_from_points(points)
    )

def _polyline_has_occupied_non_crossing_conflict(
    points: tuple[Point, ...],
    occupied_segments: list[OccupiedRouteSegment],
    rules: RoutingRules,
    *,
    allowed_touch_points: tuple[Point, ...],
) -> bool:
    for segment in _segments_from_points(points):
        for occupied in occupied_segments:
            blocker = occupied.segment
            if _segments_collinear_overlap(segment, blocker):
                return True
            if _parallel_spacing_violation(
                segment, blocker, rules.min_spacing_um, rules.waveguide_width_um
            ):
                return True
            contact = _segment_contact_point(segment, blocker)
            if contact is not None and not any(
                _same_point(contact, point) for point in allowed_touch_points
            ):
                return True
    return False

def _same_net_envelope_hop_candidates(
    src: Point,
    dst: Point,
    current_segments: list[Segment],
    avoid_segments: tuple[Segment, ...],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
) -> tuple[tuple[Point, ...], ...]:
    if not current_segments:
        return ()
    bbox_segments = tuple(current_segments) + tuple(avoid_segments)
    xs = [point[0] for segment in bbox_segments for point in segment]
    ys = [point[1] for segment in bbox_segments for point in segment]
    guard = max(
        rules.grid_pitch_um,
        2.0 * rules.bend_radius_um,
        _effective_spacing_threshold(
            rules.min_spacing_um, rules.waveguide_width_um
        ),
        _effective_spacing_threshold(
            rules.min_crossing_clearance_um or 0.0,
            rules.waveguide_width_um,
        ),
    )
    x_tracks, y_tracks = grid
    x_escape = _escape_tracks(x_tracks, min(xs), max(xs), guard)
    y_escape = _escape_tracks(y_tracks, min(ys), max(ys), guard)
    raw: list[tuple[Point, ...]] = []
    for y in y_escape:
        raw.append((src, (src[0], y), (dst[0], y), dst))
    for x in x_escape:
        raw.append((src, (x, src[1]), (x, dst[1]), dst))
    for y in y_escape:
        for x in x_escape:
            raw.append((src, (src[0], y), (x, y), (x, dst[1]), dst))
            raw.append((src, (x, src[1]), (x, y), (dst[0], y), dst))

    candidates: list[tuple[Point, ...]] = []
    seen: set[tuple[Point, ...]] = set()
    for candidate in sorted(
        (_dedupe_points(_merge_collinear_points(points)) for points in raw),
        key=lambda points: (_polyline_length(points), _bend_count(points), len(points), points),
    ):
        if candidate in seen or len(candidate) < 2:
            continue
        if _polyline_has_same_net_conflict(
            candidate,
            current_segments,
            join_points=(src, dst),
        ):
            continue
        if _polyline_has_guard_conflict(candidate, avoid_segments, rules):
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return tuple(candidates[:32])

def _polyline_has_guard_conflict(
    points: tuple[Point, ...],
    guards: tuple[Segment, ...],
    rules: RoutingRules,
) -> bool:
    if not guards:
        return False
    return any(
        _reserved_guard_conflict_reason(segment, list(guards), rules) is not None
        for segment in _segments_from_points(points)
    )

def _escape_tracks(
    tracks: tuple[float, ...],
    lo: float,
    hi: float,
    guard: float,
) -> tuple[float, ...]:
    below = [track for track in tracks if track <= lo - guard + EPS]
    above = [track for track in tracks if track >= hi + guard - EPS]
    values: list[float] = []
    for track in sorted(above, key=lambda item: (abs(item - hi), item))[:4]:
        if track not in values:
            values.append(track)
    for track in sorted(below, key=lambda item: (abs(item - lo), -item))[:4]:
        if track not in values:
            values.append(track)
    return tuple(values)

def _polyline_has_same_net_conflict(
    points: tuple[Point, ...],
    current_segments: list[Segment],
    *,
    join_points: tuple[Point, ...],
) -> bool:
    routed_segments: list[Segment] = []
    for segment in _segments_from_points(points):
        if _same_net_self_conflict_reason(
            segment,
            tuple(current_segments) + tuple(routed_segments),
            join_points=join_points,
        ) is not None:
            return True
        routed_segments.append(segment)
    return False

def _polyline_same_net_conflict_reason(
    points: tuple[Point, ...],
    current_external_segments: list[Segment],
    rules: RoutingRules,
    *,
    join_points: tuple[Point, ...],
) -> str | None:
    routed_segments: list[Segment] = []
    for segment in _segments_from_points(points):
        reason = _same_net_self_conflict_reason(
            segment,
            current_external_segments + routed_segments,
            join_points=join_points,
            rules=rules,
        )
        if reason is not None:
            return reason
        routed_segments.append(segment)
    return None

def _same_net_orthogonal_conflict(
    segment: Segment,
    own_segments: tuple[Segment, ...],
    *,
    join_points: tuple[Point, ...],
) -> tuple[Point, Segment] | None:
    for prior in own_segments:
        point = _orthogonal_crossing_point(segment, prior)
        if point is None:
            continue
        if any(_same_point(point, join_point) for join_point in join_points):
            continue
        return point, prior
    return None

def _same_net_candidate_conflict(
    segment: Segment,
    own_segments: tuple[Segment, ...],
    *,
    join_points: tuple[Point, ...],
) -> tuple[Point, Segment] | None:
    if not own_segments:
        return None
    last_idx = len(own_segments) - 1
    for idx, prior in enumerate(own_segments):
        point = _orthogonal_crossing_point(segment, prior)
        if point is None:
            point = _segment_contact_point(segment, prior)
        if point is None and _segments_collinear_overlap(segment, prior):
            point = _segment_midpoint(segment)
        if point is None:
            continue
        if any(_same_point(point, join_point) for join_point in join_points):
            continue
        if idx == last_idx and _same_point(point, segment[0]):
            continue
        return point, prior
    return None

def _same_net_local_conflict_replacements(
    segment: Segment,
    prior: Segment,
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
) -> tuple[tuple[Point, ...], ...]:
    axis = _segment_axis(segment)
    if axis not in {"h", "v"}:
        return ()
    start, end = segment
    x_tracks, y_tracks = grid
    guard = max(
        rules.grid_pitch_um,
        2.0 * rules.bend_radius_um,
        _effective_spacing_threshold(
            rules.min_spacing_um, rules.waveguide_width_um
        ),
        _effective_spacing_threshold(
            rules.min_crossing_clearance_um or 0.0,
            rules.waveguide_width_um,
        ),
    )
    raw: list[tuple[Point, ...]] = []
    if axis == "h":
        prior_ys = [prior[0][1], prior[1][1]]
        y_escape = _local_escape_tracks(y_tracks, min(prior_ys), max(prior_ys), guard)
        for y in y_escape:
            raw.append((start, (start[0], y), (end[0], y), end))
    else:
        prior_xs = [prior[0][0], prior[1][0]]
        x_escape = _local_escape_tracks(x_tracks, min(prior_xs), max(prior_xs), guard)
        for x in x_escape:
            raw.append((start, (x, start[1]), (x, end[1]), end))

    candidates: list[tuple[Point, ...]] = []
    seen: set[tuple[Point, ...]] = set()
    for candidate in sorted(
        (_dedupe_points(_merge_collinear_points(points)) for points in raw),
        key=lambda points: (_polyline_length(points), _bend_count(points), len(points), points),
    ):
        if len(candidate) < 2 or candidate == (start, end) or candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return tuple(_bend_spaced_candidates(candidates, rules)[:16])

def _source_detour_candidates(
    src: Point,
    dst: Point,
    sources: set[_CrossingSource] | tuple[_CrossingSource, ...],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
    *,
    owner_input: int | None = None,
) -> tuple[tuple[Point, ...], ...]:
    if not sources:
        return ()
    candidates: list[tuple[Point, ...]] = []
    seen: set[tuple[Point, ...]] = set()
    ordered_sources = sorted(
        sources,
        key=lambda source: (
            source.owner_input,
            source.crossed_input,
            source.point,
            source.source_segment,
            source.crossed_segment,
        ),
    )
    for base in _l_shape_route_options(src, dst):
        base_segments = _segments_from_points(base)
        for idx, segment in enumerate(base_segments):
            for source in ordered_sources:
                if owner_input is not None and source.owner_input != owner_input:
                    continue
                if not _source_crossing_matches_segment(segment, source):
                    continue
                for replacement in _source_detour_replacements(segment, source, grid, rules):
                    candidate = _merge_collinear_points(
                        tuple(base[:idx] + replacement + base[idx + 2 :])
                    )
                    if candidate == base or candidate in seen:
                        continue
                    seen.add(candidate)
                    candidates.append(candidate)
    for source in ordered_sources:
        if owner_input is not None and source.owner_input != owner_input:
            continue
        for candidate in _source_segment_detour_candidates(src, dst, source, grid, rules):
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)
    return tuple(
        _bend_spaced_candidates(
            sorted(
                candidates,
                key=lambda points: (
                    _polyline_length(points),
                    _bend_count(points),
                    len(points),
                    points,
                ),
            ),
            rules,
        )
    )

def _bend_spaced_candidates(
    candidates: list[tuple[Point, ...]] | tuple[tuple[Point, ...], ...],
    rules: RoutingRules,
) -> list[tuple[Point, ...]]:
    valid: list[tuple[Point, ...]] = []
    for candidate in candidates:
        try:
            _validate_polyline_bend_spacing(candidate, rules)
        except RoutingError:
            continue
        valid.append(candidate)
    return valid

def _source_reserved_crossing_owners(
    sources: set[_CrossingSource] | tuple[_CrossingSource, ...],
    src: Point,
    dst: Point,
    rules: RoutingRules,
    *,
    owner_input: int | None,
) -> set[int]:
    return {
        source.crossed_input
        for source in sources
        if (
            (owner_input is None or source.owner_input == owner_input)
            and _source_crossing_relevant_to_hop(source, src, dst, rules)
        )
    }

def _source_hint_debug_detail(
    sources: set[_CrossingSource] | tuple[_CrossingSource, ...],
    src: Point,
    dst: Point,
    rules: RoutingRules,
    *,
    owner_input: int | None,
) -> str:
    if not sources:
        return ""
    owner_sources = [
        source
        for source in sources
        if owner_input is None or source.owner_input == owner_input
    ]
    relevant = [
        source
        for source in owner_sources
        if _source_crossing_relevant_to_hop(source, src, dst, rules)
    ] or owner_sources
    if not relevant:
        return ""
    formatted = "; ".join(
        f"{'candidate_source' if source.is_candidate else 'source'}="
        f"{_format_crossing_source_hint(source)}"
        for source in sorted(
            relevant,
            key=lambda item: (
                item.owner_input,
                item.crossed_input,
                item.point,
                item.source_segment,
                item.crossed_segment,
            ),
        )[:2]
    )
    return f"; {formatted}"

def _format_crossing_source_hint(source: _CrossingSource) -> str:
    return (
        f"I{source.owner_input}->I{source.crossed_input}"
        f"@({source.point[0]:.3f},{source.point[1]:.3f})"
        f":{_format_segment(source.source_segment)}"
        f"x{_format_segment(source.crossed_segment)}"
    )

def _source_segment_detour_candidates(
    src: Point,
    dst: Point,
    source: _CrossingSource,
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
) -> tuple[tuple[Point, ...], ...]:
    if not _source_crossing_relevant_to_hop(source, src, dst, rules):
        return ()
    replacements = _source_detour_replacements(source.source_segment, source, grid, rules)
    if not replacements:
        return ()
    candidates: list[tuple[Point, ...]] = []
    for replacement in replacements:
        for prefix in _connection_options(src, replacement[0]):
            for suffix in _connection_options(replacement[-1], dst):
                candidate = _merge_collinear_points(
                    prefix + replacement[1:] + suffix[1:]
                )
                if _same_point(candidate[0], src) and _same_point(candidate[-1], dst):
                    candidates.append(candidate)
    return tuple(candidates)

def _source_crossing_relevant_to_hop(
    source: _CrossingSource,
    src: Point,
    dst: Point,
    rules: RoutingRules,
) -> bool:
    slack = max(
        2.0 * rules.bend_radius_um,
        rules.grid_pitch_um * max(1, rules.route_window_max_detour_tracks),
    )
    if _segment_near_hop_bbox(source.source_segment, (src, dst), slack_um=slack):
        return True
    return any(
        _source_crossing_matches_segment(segment, source)
        for route in _l_shape_route_options(src, dst)
        for segment in _segments_from_points(route)
    )

def _connection_options(src: Point, dst: Point) -> tuple[tuple[Point, ...], ...]:
    if _same_point(src, dst):
        return ((src,),)
    return _l_shape_route_options(src, dst)

def _source_crossing_matches_segment(
    segment: Segment,
    source: _CrossingSource,
) -> bool:
    if not _point_on_segment(source.point, segment):
        return False
    if not _point_on_segment(source.point, source.source_segment):
        return False
    if _segment_axis(segment) != _segment_axis(source.source_segment):
        return False
    if not _segments_collinear_overlap(segment, source.source_segment):
        return False
    crossing = _orthogonal_crossing_point(source.source_segment, source.crossed_segment)
    return crossing is not None and _same_point(crossing, source.point)

def _source_detour_replacements(
    segment: Segment,
    source: _CrossingSource,
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
) -> tuple[tuple[Point, ...], ...]:
    axis = _segment_axis(segment)
    crossed_axis = _segment_axis(source.crossed_segment)
    if axis == "" or crossed_axis == "" or axis == crossed_axis:
        return ()

    start, end = segment
    guard = max(
        rules.grid_pitch_um,
        2.0 * rules.bend_radius_um,
        _effective_spacing_threshold(
            rules.min_spacing_um, rules.waveguide_width_um
        ),
        _effective_spacing_threshold(
            rules.min_crossing_clearance_um or 0.0,
            rules.waveguide_width_um,
        ),
    )
    x_tracks, y_tracks = grid
    replacements: list[tuple[Point, ...]] = []
    if axis == "h":
        before_x, after_x = _bracket_values_around_point(
            x_tracks,
            start[0],
            end[0],
            source.point[0],
            guard,
        )
        if before_x is None or after_x is None:
            return ()
        crossed_y0, crossed_y1 = source.crossed_segment[0][1], source.crossed_segment[1][1]
        for alt_y in _source_detour_alt_values(
            y_tracks,
            crossed_y0,
            crossed_y1,
            source.point[1],
            guard,
        ):
            replacements.append(
                _merge_collinear_points(
                    (
                        start,
                        (_canonical_track(before_x), start[1]),
                        (_canonical_track(before_x), alt_y),
                        (_canonical_track(after_x), alt_y),
                        (_canonical_track(after_x), end[1]),
                        end,
                    )
                )
            )
    else:
        before_y, after_y = _bracket_values_around_point(
            y_tracks,
            start[1],
            end[1],
            source.point[1],
            guard,
        )
        if before_y is None or after_y is None:
            return ()
        crossed_x0, crossed_x1 = source.crossed_segment[0][0], source.crossed_segment[1][0]
        for alt_x in _source_detour_alt_values(
            x_tracks,
            crossed_x0,
            crossed_x1,
            source.point[0],
            guard,
        ):
            replacements.append(
                _merge_collinear_points(
                    (
                        start,
                        (start[0], _canonical_track(before_y)),
                        (alt_x, _canonical_track(before_y)),
                        (alt_x, _canonical_track(after_y)),
                        (end[0], _canonical_track(after_y)),
                        end,
                    )
                )
            )
    return tuple(replacement for replacement in replacements if len(replacement) >= 2)

def _segment_axis(segment: Segment) -> str:
    if abs(segment[0][1] - segment[1][1]) < EPS:
        return "h"
    if abs(segment[0][0] - segment[1][0]) < EPS:
        return "v"
    return ""

def _bracket_values_around_point(
    tracks: tuple[float, ...],
    start: float,
    end: float,
    point: float,
    min_distance: float,
) -> tuple[float | None, float | None]:
    lo, hi = sorted((start, end))
    values = sorted(
        {
            _canonical_track(value)
            for value in (*tracks, start, end)
            if lo - EPS <= value <= hi + EPS and abs(value - point) > EPS
        }
    )
    below = [value for value in values if value < point - EPS]
    above = [value for value in values if value > point + EPS]
    if not below or not above:
        return None, None
    below_far = [value for value in below if value <= point - min_distance + EPS]
    above_far = [value for value in above if value >= point + min_distance - EPS]
    lower = max(below_far) if below_far else max(below)
    upper = min(above_far) if above_far else min(above)
    if start <= end:
        if lower - start < min_distance - EPS:
            lower = start
        if end - upper < min_distance - EPS:
            upper = end
        return lower, upper
    if start - upper < min_distance - EPS:
        upper = start
    if lower - end < min_distance - EPS:
        lower = end
    return upper, lower

def _source_detour_alt_values(
    tracks: tuple[float, ...],
    crossed_start: float,
    crossed_end: float,
    source_coord: float,
    guard: float,
) -> tuple[float, ...]:
    lo, hi = sorted((crossed_start, crossed_end))
    min_track, max_track = min(tracks), max(tracks)
    raw_values = [lo - guard, hi + guard, lo - 2.0 * guard, hi + 2.0 * guard]
    raw_values.extend(value for value in tracks if value <= lo - guard + EPS)
    raw_values.extend(value for value in tracks if value >= hi + guard - EPS)

    legal_values: list[float] = []
    for value in sorted({_canonical_track(value) for value in raw_values}):
        if value < min_track - EPS or value > max_track + EPS:
            continue
        if lo - guard + EPS < value < hi + guard - EPS:
            continue
        legal_values.append(value)

    below = sorted(
        [value for value in legal_values if value <= lo - guard + EPS],
        key=lambda item: (abs(item - source_coord), -item),
    )
    above = sorted(
        [value for value in legal_values if value >= hi + guard - EPS],
        key=lambda item: (abs(item - source_coord), item),
    )
    sides = [above, below] if (
        above
        and (not below or abs(above[0] - source_coord) <= abs(below[0] - source_coord))
    ) else [below, above]

    result: list[float] = []
    side_idx = 0
    while len(result) < 8 and any(sides):
        side = sides[side_idx % 2]
        if side:
            value = side.pop(0)
            if value not in result:
                result.append(value)
        side_idx += 1
    return tuple(result)

def _route_external_hop_via_waypoints(
    waypoints: tuple[Point, ...],
    blockers: list[Segment],
    obstacles: list[Obstacle],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
    *,
    src_label: str,
    dst_label: str,
    forbidden_points: set[Point],
    current_segments: list[Segment],
    current_external_segments: list[Segment] | None = None,
    occupied_segments: list[OccupiedRouteSegment],
    crossing_count_by_pair: dict[tuple[int, int], int],
    crossing_sources_by_pair: dict[tuple[int, int], tuple[str, ...]] | None,
    soft_blockers: list[Segment],
    reserved_spacing_blockers: list[Segment],
    reserved_guard_blockers: list[Segment] | list[OccupiedRouteSegment],
    reserved_crossing_owners: set[int] | None,
    deferred_crossing_owners: set[int] | None,
    preferred_bend_x: float | None,
    reserve_space_penalty: bool,
    port_access: PortAccessLegality | None,
    grid_bounds: RoutingWindow,
    base_detour_tracks: int,
    ripup_pass_idx: int,
    history_cost: HistoryCost | None,
    candidate_label: str = "source detour",
    repeated_crossing_penalty_scale: float = 1.0,
    candidate_window_expansion_tracks: int = 0,
) -> tuple[Point, ...]:
    route_points = _merge_collinear_points(waypoints)
    route_points = _validate_external_hop_route_points(
        route_points,
        blockers,
        obstacles,
        rules,
        src_label=src_label,
        dst_label=dst_label,
        forbidden_points=forbidden_points,
        current_segments=current_segments,
        current_external_segments=current_external_segments,
        occupied_segments=occupied_segments,
        crossing_count_by_pair=crossing_count_by_pair,
        crossing_sources_by_pair=crossing_sources_by_pair,
        reserved_spacing_blockers=reserved_spacing_blockers,
        reserved_guard_blockers=reserved_guard_blockers,
        reserved_crossing_owners=reserved_crossing_owners,
        port_access=port_access,
        grid_bounds=grid_bounds,
        base_detour_tracks=base_detour_tracks,
        ripup_pass_idx=ripup_pass_idx,
        candidate_label=candidate_label,
        repeated_crossing_penalty_scale=repeated_crossing_penalty_scale,
        candidate_window_expansion_tracks=candidate_window_expansion_tracks,
    )
    if _port_to_port_hop_labels(src_label, dst_label):
        route_points = _simplify_port_to_port_alternating_jogs(
            route_points,
            blockers,
            obstacles,
            grid,
            rules,
            src_label=src_label,
            dst_label=dst_label,
            forbidden_points=forbidden_points,
            current_segments=current_segments,
            current_external_segments=current_external_segments,
            occupied_segments=occupied_segments,
            crossing_count_by_pair=crossing_count_by_pair,
            crossing_sources_by_pair=crossing_sources_by_pair,
            soft_blockers=soft_blockers,
            reserved_spacing_blockers=reserved_spacing_blockers,
            reserved_guard_blockers=reserved_guard_blockers,
            reserved_crossing_owners=reserved_crossing_owners,
            port_access=port_access,
            grid_bounds=grid_bounds,
            base_detour_tracks=base_detour_tracks,
            ripup_pass_idx=ripup_pass_idx,
            candidate_label=candidate_label,
            repeated_crossing_penalty_scale=repeated_crossing_penalty_scale,
            candidate_window_expansion_tracks=candidate_window_expansion_tracks,
        )
    return route_points

def _validate_external_hop_route_points(
    route_points: tuple[Point, ...],
    blockers: list[Segment],
    obstacles: list[Obstacle],
    rules: RoutingRules,
    *,
    src_label: str,
    dst_label: str,
    forbidden_points: set[Point],
    current_segments: list[Segment],
    current_external_segments: list[Segment] | None,
    occupied_segments: list[OccupiedRouteSegment],
    crossing_count_by_pair: dict[tuple[int, int], int],
    crossing_sources_by_pair: dict[tuple[int, int], tuple[str, ...]] | None,
    reserved_spacing_blockers: list[Segment],
    reserved_guard_blockers: list[Segment] | list[OccupiedRouteSegment],
    reserved_crossing_owners: set[int] | None,
    port_access: PortAccessLegality | None,
    grid_bounds: RoutingWindow,
    base_detour_tracks: int,
    ripup_pass_idx: int,
    candidate_label: str,
    repeated_crossing_penalty_scale: float,
    candidate_window_expansion_tracks: int = 0,
) -> tuple[Point, ...]:
    if len(route_points) < 2:
        raise RoutingError(f"{candidate_label} has no routable segments")
    _validate_polyline_bend_spacing(
        route_points,
        rules,
        src_label=src_label,
        dst_label=dst_label,
    )
    candidate_window = _hop_routing_window(
        route_points[0],
        route_points[-1],
        obstacles,
        rules,
        grid_bounds,
        detour_tracks=_candidate_validation_detour_tracks(
            candidate_label,
            base_detour_tracks,
            rules,
        ),
        ripup_pass_idx=ripup_pass_idx,
    )
    if candidate_window_expansion_tracks:
        candidate_window = candidate_window.expanded(
            candidate_window_expansion_tracks,
            rules.grid_pitch_um,
            bounds=grid_bounds,
        )
    routed_segments: list[Segment] = []
    owner_input = port_access.current_net_id if port_access is not None else None
    current_counts = dict(crossing_count_by_pair)
    current_sources = dict(crossing_sources_by_pair or {})
    strict_current_external_segments = (
        current_segments
        if current_external_segments is None
        else current_external_segments
    )
    current_external_segments = current_external_segments or current_segments
    for segment in _segments_from_points(route_points):
        if not candidate_window.contains_segment(segment):
            raise RoutingError(
                f"{candidate_label} outside route window: "
                f"segment={_format_segment(segment)} "
                f"window=({candidate_window.left:.3f},{candidate_window.right:.3f},"
                f"{candidate_window.bottom:.3f},{candidate_window.top:.3f})"
            )
        if rules.uses_db_cost:
            touching_corner_reason = _same_net_touching_corner_reason(
                segment,
                tuple(strict_current_external_segments) + tuple(routed_segments),
            )
            if touching_corner_reason is not None:
                raise RoutingError(
                    f"{candidate_label} creates same-net self conflict; "
                    f"{touching_corner_reason}"
                )
        same_net_reason = _same_net_self_conflict_reason(
            segment,
            current_segments + routed_segments,
            join_points=(route_points[0], route_points[-1]),
        )
        if same_net_reason is not None:
            raise RoutingError(
                f"{candidate_label} creates same-net self conflict; {same_net_reason}"
            )
        hairpin_reason = _same_net_self_conflict_reason(
            segment,
            current_external_segments + routed_segments,
            join_points=(route_points[0], route_points[-1]),
            rules=rules,
        )
        if hairpin_reason is not None and not _same_net_reason_is_hairpin(hairpin_reason):
            raise RoutingError(
                f"{candidate_label} creates same-net self conflict; {hairpin_reason}"
            )
        if _reserved_spacing_conflict(segment, reserved_spacing_blockers, rules):
            raise RoutingError(f"{candidate_label} violates reserved spacing")
        guard_reason = _reserved_guard_conflict_reason(segment, reserved_guard_blockers, rules)
        if guard_reason is not None:
            raise RoutingError(
                f"{candidate_label} violates reserved port guard; {guard_reason}"
            )
        if not _route_segment_available(
            segment,
            blockers + routed_segments,
            obstacles,
            rules,
            forbidden_points,
            allowed_touch_points=(route_points[0], route_points[-1]),
            port_access=port_access,
        ):
            reason = _route_segment_blocked_reason(
                segment,
                blockers + routed_segments,
                obstacles,
                rules,
                forbidden_points,
                allowed_touch_points=(route_points[0], route_points[-1]),
                port_access=port_access,
                occupied_segments=occupied_segments,
                current_owner=owner_input,
                current_segments=current_segments + routed_segments,
            )
            raise RoutingError(f"{candidate_label} segment is blocked; {reason}")
        _validate_source_detour_crossings(
            segment,
            blockers + routed_segments,
            occupied_segments,
            current_counts,
            current_sources,
            owner_input,
            rules,
            reserved_crossing_owners or set(),
            port_access,
            candidate_label,
            repeated_crossing_penalty_scale,
        )
        routed_segments.append(segment)
    return route_points

def _port_to_port_hop_labels(src_label: str, dst_label: str) -> bool:
    return "." in src_label and "." in dst_label

def _simplify_port_to_port_alternating_jogs(
    route_points: tuple[Point, ...],
    blockers: list[Segment],
    obstacles: list[Obstacle],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
    *,
    src_label: str,
    dst_label: str,
    forbidden_points: set[Point],
    current_segments: list[Segment],
    current_external_segments: list[Segment] | None,
    occupied_segments: list[OccupiedRouteSegment],
    crossing_count_by_pair: dict[tuple[int, int], int],
    crossing_sources_by_pair: dict[tuple[int, int], tuple[str, ...]] | None,
    soft_blockers: list[Segment],
    reserved_spacing_blockers: list[Segment],
    reserved_guard_blockers: list[Segment] | list[OccupiedRouteSegment],
    reserved_crossing_owners: set[int] | None,
    port_access: PortAccessLegality | None,
    grid_bounds: RoutingWindow,
    base_detour_tracks: int,
    ripup_pass_idx: int,
    candidate_label: str,
    repeated_crossing_penalty_scale: float,
    candidate_window_expansion_tracks: int = 0,
) -> tuple[Point, ...]:
    best = route_points
    improved = True
    while improved:
        improved = False
        for start_idx, end_idx in _alternating_jog_windows(best):
            replacements = _alternating_jog_shortcut_options(
                best[start_idx],
                best[end_idx],
                obstacles,
                rules,
                _detour_tracks(base_detour_tracks, rules),
                grid=grid,
                port_access=port_access,
            )
            for replacement in replacements:
                candidate = _dedupe_points(
                    _merge_collinear_points(
                        tuple(best[:start_idx] + replacement + best[end_idx + 1 :])
                    )
                )
                if candidate == best or _bend_count(candidate) >= _bend_count(best):
                    continue
                try:
                    valid = _validate_external_hop_route_points(
                        candidate,
                        blockers,
                        obstacles,
                        rules,
                        src_label=src_label,
                        dst_label=dst_label,
                        forbidden_points=forbidden_points,
                        current_segments=current_segments,
                        current_external_segments=current_external_segments,
                        occupied_segments=occupied_segments,
                        crossing_count_by_pair=crossing_count_by_pair,
                        crossing_sources_by_pair=crossing_sources_by_pair,
                        reserved_spacing_blockers=reserved_spacing_blockers,
                        reserved_guard_blockers=reserved_guard_blockers,
                        reserved_crossing_owners=reserved_crossing_owners,
                        port_access=port_access,
                        grid_bounds=grid_bounds,
                        base_detour_tracks=base_detour_tracks,
                        ripup_pass_idx=ripup_pass_idx,
                        candidate_label=f"{candidate_label} W-shortcut",
                        repeated_crossing_penalty_scale=repeated_crossing_penalty_scale,
                        candidate_window_expansion_tracks=candidate_window_expansion_tracks,
                    )
                except RoutingError:
                    continue
                if _candidate_route_cost(
                    valid,
                    obstacles,
                    port_access,
                    rules,
                    blockers,
                ) > _candidate_route_cost(
                    best,
                    obstacles,
                    port_access,
                    rules,
                    blockers,
                ) + EPS:
                    continue
                best = valid
                improved = True
                break
            if improved:
                break
    return best

def _alternating_jog_windows(points: tuple[Point, ...]) -> tuple[tuple[int, int], ...]:
    bend_indices: list[int] = []
    signs: list[int] = []
    for idx, (previous, current, next_point) in enumerate(
        zip(points, points[1:], points[2:]),
        start=1,
    ):
        sign = _turn_sign(
            _orientation_between(previous, current),
            _orientation_between(current, next_point),
        )
        if sign == 0:
            continue
        bend_indices.append(idx)
        signs.append(sign)
    windows: list[tuple[int, int]] = []
    idx = 0
    while idx < len(signs) - 2:
        run_end = idx
        while run_end + 1 < len(signs) and signs[run_end + 1] == -signs[run_end]:
            run_end += 1
        if run_end - idx + 1 >= 3:
            start_idx = bend_indices[idx] - 1
            end_idx = bend_indices[run_end] + 1
            if start_idx >= 0 and end_idx < len(points):
                windows.append((start_idx, end_idx))
        idx = max(idx + 1, run_end)
    return tuple(windows)

def _alternating_jog_shortcut_options(
    start: Point,
    end: Point,
    obstacles: list[Obstacle],
    rules: RoutingRules,
    max_tracks: int,
    *,
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    port_access: PortAccessLegality | None,
) -> tuple[tuple[Point, ...], ...]:
    options: list[tuple[Point, ...]] = []
    if abs(start[0] - end[0]) < EPS or abs(start[1] - end[1]) < EPS:
        options.append((start, end))
    else:
        options.extend(_l_shape_route_options(start, end))
    options.extend(
        _delayed_l_route_options(
            start,
            end,
            obstacles,
            rules,
            max_tracks,
            grid=grid,
            port_access=port_access,
        )
    )
    return tuple(_dedupe_candidate_routes(options))

def _candidate_validation_detour_tracks(
    candidate_label: str,
    base_detour_tracks: int,
    rules: RoutingRules,
) -> int:
    detour_tracks = _detour_tracks(base_detour_tracks, rules)
    if candidate_label in {
        "reserved-owner avoidance candidate",
        "same-net avoidance candidate",
        "source detour",
    }:
        return max(detour_tracks, rules.local_repair_max_shift_tracks)
    return detour_tracks

def _remediation_window_expansion_tracks(
    rules: RoutingRules,
    displacement_tracks: int,
) -> int:
    """Return the remediation-only per-hop search-window margin in tracks."""
    if not rules.remediation_window_expansion or not displacement_tracks:
        return 0
    return abs(displacement_tracks) + 2

def _validate_polyline_bend_spacing(
    points: tuple[Point, ...],
    rules: RoutingRules,
    *,
    src_label: str | None = None,
    dst_label: str | None = None,
) -> None:
    bends: list[Point] = []
    for previous, current, next_point in zip(points, points[1:], points[2:]):
        if _direction(previous, current) != _direction(current, next_point):
            bends.append(current)
    for first, second in zip(bends, bends[1:]):
        if _manhattan(first, second) < 2.0 * rules.bend_radius_um - EPS:
            raise RoutingError("source detour violates bend spacing")
    if rules.enforce_bend_spacing:
        _validate_junction_bend_spacing(
            points,
            rules,
            src_label=src_label,
            dst_label=dst_label,
        )


def _validate_junction_bend_spacing(
    points: tuple[Point, ...],
    rules: RoutingRules,
    *,
    src_label: str | None,
    dst_label: str | None,
) -> None:
    min_run_um = 2.0 * rules.bend_radius_um
    previous_orientation = (
        _port_junction_orientation(src_label, source=True)
        if src_label is not None
        else ""
    )
    straight_run_um = min_run_um if previous_orientation else 0.0
    for start, end in _segments_from_points(points):
        orientation = _orientation_between(start, end)
        segment_length_um = _manhattan(start, end)
        if previous_orientation:
            if orientation == opposite_orientation(previous_orientation):
                raise RoutingError("port junction reverses direction")
            if orientation_axis(orientation) != orientation_axis(previous_orientation):
                if straight_run_um < min_run_um - EPS:
                    raise RoutingError("port junction violates bend spacing")
                straight_run_um = segment_length_um
            else:
                straight_run_um += segment_length_um
        else:
            straight_run_um = segment_length_um
        straight_run_um = min(min_run_um, straight_run_um)
        previous_orientation = orientation

    continuation = (
        _port_junction_orientation(dst_label, source=False)
        if dst_label is not None
        else ""
    )
    if not continuation:
        return
    if continuation == opposite_orientation(previous_orientation):
        raise RoutingError("port junction reverses direction")
    if (
        orientation_axis(continuation) != orientation_axis(previous_orientation)
        and straight_run_um < min_run_um - EPS
    ):
        raise RoutingError("port junction violates bend spacing")

def _route_segment_blocked_reason(
    segment: Segment,
    blockers: list[Segment],
    obstacles: list[Obstacle],
    rules: RoutingRules,
    forbidden_points: set[Point],
    *,
    allowed_touch_points: tuple[Point, ...],
    port_access: PortAccessLegality | None,
    occupied_segments: list[OccupiedRouteSegment],
    current_owner: int | None,
    current_segments: list[Segment],
) -> str:
    if not _is_axis_aligned(segment):
        return "blocked_by=unknown/non_axis_segment"
    forbidden_point = _first_forbidden_point_on_segment(
        segment,
        forbidden_points,
        allowed_touch_points,
    )
    if forbidden_point is not None:
        return (
            "blocked_by=unknown/forbidden_point"
            f" at=({forbidden_point[0]:.3f},{forbidden_point[1]:.3f})"
            f":{_format_segment(segment)}"
        )
    for obstacle in obstacles:
        if _segment_intersects_obstacle(segment, obstacle):
            return f"blocked_by={obstacle.cell_id}/obstacle:{_format_segment(segment)}"
    for blocker in blockers:
        if _segments_collinear_overlap(segment, blocker):
            return (
                f"blocked_by={_blocker_owner_label(blocker, occupied_segments, current_owner, current_segments)}"
                f"/overlap:{_format_segment(blocker)}"
            )
        if _parallel_spacing_violation(
            segment, blocker, rules.min_spacing_um, rules.waveguide_width_um
        ):
            return (
                f"blocked_by={_blocker_owner_label(blocker, occupied_segments, current_owner, current_segments)}"
                f"/spacing:{_format_segment(blocker)}"
            )
        if not rules.allow_crossings and _orthogonal_crossing_point(segment, blocker):
            return (
                f"blocked_by={_blocker_owner_label(blocker, occupied_segments, current_owner, current_segments)}"
                f"/crossing:{_format_segment(blocker)}"
            )
    access_detail = port_access_conflict_detail(
        segment,
        port_access,
        rules,
        allowed_touch_points=allowed_touch_points,
    )
    if access_detail is not None:
        reason, region = access_detail
        return (
            f"region=I{region.owner_input}/{region.cell_id}.{region.port}/"
            f"{reason}:{_format_segment(region.segment)}"
        )
    return f"blocked_by=unknown/segment:{_format_segment(segment)}"

def _first_forbidden_point_on_segment(
    segment: Segment,
    forbidden_points: set[Point],
    allowed_touch_points: tuple[Point, ...],
) -> Point | None:
    for point in sorted(forbidden_points):
        if any(_same_point(point, allowed) for allowed in allowed_touch_points):
            continue
        if _point_on_segment(point, segment):
            return point
    return None

def _blocker_owner_label(
    blocker: Segment,
    occupied_segments: list[OccupiedRouteSegment],
    current_owner: int | None,
    current_segments: list[Segment],
) -> str:
    for occupied in occupied_segments:
        if _same_segment(blocker, occupied.segment):
            return f"I{occupied.owner_input}/{occupied.kind}"
    if current_owner is not None and any(
        _same_segment(blocker, segment) for segment in current_segments
    ):
        return f"I{current_owner}/current"
    return "unknown"

def _validate_source_detour_crossings(
    segment: Segment,
    blockers: list[Segment],
    occupied_segments: list[OccupiedRouteSegment],
    crossing_count_by_pair: dict[tuple[int, int], int],
    crossing_sources_by_pair: dict[tuple[int, int], tuple[str, ...]],
    owner_input: int | None,
    rules: RoutingRules,
    reserved_crossing_owners: set[int],
    port_access: PortAccessLegality | None,
    candidate_label: str,
    repeated_crossing_penalty_scale: float = 1.0,
) -> None:
    allowed_touch_points = segment
    if port_access is not None:
        for region in port_access.plan.reserved_regions:
            if _orthogonal_crossing_point(segment, region.segment) is not None:
                raise RoutingError(f"{candidate_label} crosses port access")
            contact = _segment_contact_point(segment, region.segment)
            if contact is not None and not any(
                _same_point(contact, point) for point in allowed_touch_points
            ):
                raise RoutingError(f"{candidate_label} touches port access")

    crossings: list[tuple[Point, OccupiedRouteSegment]] = []
    seen_crossings: set[tuple[int, Point]] = set()
    for blocker in blockers:
        location = _orthogonal_crossing_point(segment, blocker)
        if location is None:
            contact = _segment_contact_point(segment, blocker)
            if contact is not None and not any(
                _same_point(contact, point) for point in allowed_touch_points
            ):
                raise RoutingError(f"{candidate_label} touches blocker")
            continue
        occupied = _occupied_external_for_segment(blocker, occupied_segments)
        if occupied is None:
            raise RoutingError(f"{candidate_label} crosses non-waveguide blocker")
        if occupied.owner_input is None:
            raise RoutingError(f"{candidate_label} crosses ownerless blocker")
        key = (occupied.owner_input, location)
        if key in seen_crossings:
            continue
        seen_crossings.add(key)
        crossings.append((location, occupied))

    if len(crossings) > 1:
        raise RoutingError(f"{candidate_label} has multiple crossings on one segment")
    if not crossings:
        return
    if owner_input is None:
        raise RoutingError(f"{candidate_label} crossing has unknown owner")
    location, crossed = crossings[0]
    if crossed.owner_input == owner_input:
        raise RoutingError(f"{candidate_label} crosses same net")
    if crossed.owner_input in reserved_crossing_owners:
        raise RoutingError(
            f"{candidate_label} crosses reserved future owner I{crossed.owner_input} "
            f"at=({location[0]:.3f},{location[1]:.3f}) "
            f"segment={_format_segment(segment)} crossed={_format_segment(crossed.segment)}"
        )
    clearance = _effective_spacing_threshold(
        rules.min_crossing_clearance_um or 0.0,
        rules.waveguide_width_um,
    )
    for point in (*segment, *crossed.segment):
        if _manhattan(location, point) < clearance - EPS:
            raise RoutingError(f"{candidate_label} crossing violates clearance")
    if crossed.owner_input is None:
        raise RoutingError(f"{candidate_label} crosses ownerless blocker")
    pair = _owner_pair(owner_input, crossed.owner_input)
    if crossing_count_by_pair.get(pair, 0) >= 1:
        if rules.hard_crossing_budget:
            raise RoutingError(
                f"{candidate_label} exceeds crossing budget; "
                f"blocked_by=I{crossed.owner_input}/crossing_budget "
                f"pair=I{pair[0]}-I{pair[1]} "
                f"at=({location[0]:.3f},{location[1]:.3f}) "
                f"candidate_source=I{owner_input}->I{crossed.owner_input}"
                f"@({location[0]:.3f},{location[1]:.3f})"
                f":{_format_segment(segment)}x{_format_segment(crossed.segment)} "
                f"segment={_format_segment(segment)} "
                f"crossed={_format_segment(crossed.segment)} "
                f"repeat_penalty={rules.repeated_crossing_penalty_um * repeated_crossing_penalty_scale:.3f}"
            )
    crossing_count_by_pair[pair] = crossing_count_by_pair.get(pair, 0) + 1
    source = (
        f"I{owner_input}->I{crossed.owner_input}"
        f"@({location[0]:.3f},{location[1]:.3f})"
        f":{_format_segment(segment)}"
        f"x{_format_segment(crossed.segment)}"
    )
    crossing_sources_by_pair[pair] = crossing_sources_by_pair.get(pair, ()) + (source,)

def _occupied_external_for_segment(
    segment: Segment,
    occupied_segments: list[OccupiedRouteSegment],
) -> OccupiedRouteSegment | None:
    for occupied in occupied_segments:
        if occupied.kind == "external" and _same_segment(occupied.segment, segment):
            return occupied
    return None

def _add_crossing_sources_by_pair(
    sources: dict[tuple[int, int], tuple[str, ...]],
    occupied: list[OccupiedRouteSegment],
    new_external_segments: list[Segment] | tuple[Segment, ...],
    owner_input: int,
) -> None:
    occupied_external = [item for item in occupied if item.kind == "external"]
    for segment in new_external_segments:
        for other in occupied_external:
            location = _orthogonal_crossing_point(segment, other.segment)
            if location is None or other.owner_input is None:
                continue
            pair = _owner_pair(owner_input, other.owner_input)
            source = (
                f"I{owner_input}->I{other.owner_input}"
                f"@({location[0]:.3f},{location[1]:.3f})"
                f":{_format_segment(segment)}"
                f"x{_format_segment(other.segment)}"
            )
            sources[pair] = sources.get(pair, ()) + (source,)

def _route_external_hop_via_approach(
    src: Point,
    approach: Point,
    dst: Point,
    blockers: list[Segment],
    obstacles: list[Obstacle],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
    *,
    src_label: str,
    dst_label: str,
    forbidden_points: set[Point],
    current_segments: list[Segment],
    current_external_segments: list[Segment] | None = None,
    occupied_segments: list[OccupiedRouteSegment],
    crossing_count_by_pair: dict[tuple[int, int], int],
    crossing_sources_by_pair: dict[tuple[int, int], tuple[str, ...]] | None,
    soft_blockers: list[Segment],
    reserved_spacing_blockers: list[Segment],
    reserved_guard_blockers: list[Segment] | list[OccupiedRouteSegment],
    reserved_crossing_owners: set[int] | None,
    deferred_crossing_owners: set[int] | None,
    preferred_bend_x: float | None,
    reserve_space_penalty: bool,
    port_access: PortAccessLegality | None,
    grid_bounds: RoutingWindow,
    base_detour_tracks: int,
    ripup_pass_idx: int,
    history_cost: HistoryCost | None,
    repeated_crossing_penalty_scale: float = 1.0,
) -> tuple[Point, ...]:
    last_error: RoutingError | None = None
    current_external_segments = current_external_segments or current_segments
    final_segment = (approach, dst)
    reserved_final_crossing_owners = _direct_port_approach_crossing_owners(
        final_segment,
        occupied_segments,
        port_access.current_net_id if port_access is not None else None,
    )
    for detour_tracks in (base_detour_tracks,):
        try:
            first = _astar_route(
                src,
                approach,
                blockers,
                obstacles,
                grid,
                rules,
                src_label=src_label,
                dst_label=f"{dst_label}.approach",
                forbidden_points=forbidden_points,
                current_segments=current_segments,
                occupied_segments=occupied_segments,
                crossing_count_by_pair=crossing_count_by_pair,
                crossing_sources_by_pair=crossing_sources_by_pair,
                soft_blockers=soft_blockers,
                reserved_spacing_blockers=reserved_spacing_blockers,
                reserved_guard_blockers=_guard_segments(reserved_guard_blockers),
                reserved_crossing_owners=reserved_crossing_owners,
                deferred_crossing_owners=(
                    (deferred_crossing_owners or set()) | reserved_final_crossing_owners
                ),
                preferred_bend_x=preferred_bend_x,
                reserve_space_penalty=reserve_space_penalty,
                port_access=port_access,
                current_external_segments=current_external_segments,
                routing_window=_hop_routing_window(
                    src,
                    approach,
                    obstacles,
                    rules,
                    grid_bounds,
                    detour_tracks=detour_tracks,
                    ripup_pass_idx=ripup_pass_idx,
                ),
                history_cost=history_cost,
                repeated_crossing_penalty_scale=repeated_crossing_penalty_scale,
            )
        except RoutingError as exc:
            last_error = exc
            continue

        first_segments = list(_segments_from_points(first))
        try:
            _validate_direct_port_approach_segment(
                approach,
                dst,
                blockers + first_segments,
                obstacles,
                rules,
                forbidden_points=forbidden_points,
                current_segments=current_segments + first_segments,
                occupied_segments=occupied_segments,
                crossing_count_by_pair=crossing_count_by_pair,
                reserved_spacing_blockers=reserved_spacing_blockers,
                reserved_guard_blockers=reserved_guard_blockers,
                port_access=port_access,
                routing_window=_hop_routing_window(
                    approach,
                    dst,
                    obstacles,
                    rules,
                    grid_bounds,
                    detour_tracks=1,
                    ripup_pass_idx=ripup_pass_idx,
                ),
            )
        except RoutingError as exc:
            last_error = exc
            continue
        return _dedupe_points(first + (dst,))

    assert last_error is not None
    raise last_error

def _validate_direct_port_approach_segment(
    approach: Point,
    dst: Point,
    blockers: list[Segment],
    obstacles: list[Obstacle],
    rules: RoutingRules,
    *,
    forbidden_points: set[Point],
    current_segments: list[Segment],
    occupied_segments: list[OccupiedRouteSegment],
    crossing_count_by_pair: dict[tuple[int, int], int],
    reserved_spacing_blockers: list[Segment],
    reserved_guard_blockers: list[Segment] | list[OccupiedRouteSegment],
    port_access: PortAccessLegality | None,
    routing_window: RoutingWindow,
) -> None:
    segment = (approach, dst)
    if not routing_window.contains_segment(segment):
        raise RoutingError("direct port approach outside route window")
    same_net_reason = _same_net_self_conflict_reason(
        segment,
        current_segments,
        join_points=(approach, dst),
    )
    if same_net_reason is not None:
        raise RoutingError(
            f"direct port approach creates same-net self conflict; {same_net_reason}"
        )
    if _reserved_spacing_conflict(segment, reserved_spacing_blockers, rules):
        raise RoutingError("direct port approach violates reserved spacing")
    guard_reason = _reserved_guard_conflict_reason(segment, reserved_guard_blockers, rules)
    if guard_reason is not None:
        raise RoutingError(f"direct port approach violates reserved port guard; {guard_reason}")
    if not _route_segment_available(
        segment,
        blockers,
        obstacles,
        rules,
        forbidden_points,
        allowed_touch_points=(approach, dst),
        port_access=port_access,
    ):
        raise RoutingError("direct port approach is blocked")
    crossing_reason = _direct_port_approach_crossing_reason(
        segment,
        blockers,
        occupied_segments,
        crossing_count_by_pair,
        port_access.current_net_id if port_access is not None else None,
        rules,
    )
    if crossing_reason is not None:
        raise RoutingError(
            f"direct port approach has no legal crossing; {crossing_reason}"
        )

def _direct_port_approach_crossing_reason(
    segment: Segment,
    blockers: list[Segment],
    occupied_segments: list[OccupiedRouteSegment],
    crossing_count_by_pair: dict[tuple[int, int], int],
    owner_input: int | None,
    rules: RoutingRules,
) -> str | None:
    occupied_external = [
        item
        for item in occupied_segments
        if item.kind == "external"
        and _orthogonal_crossing_point(segment, item.segment) is not None
    ]
    occupied_external_segments = [item.segment for item in occupied_external]
    for blocker in blockers:
        if any(_same_segment(blocker, occupied) for occupied in occupied_external_segments):
            continue
        if _orthogonal_crossing_point(segment, blocker) is not None:
            return f"blocked_by=unknown/direct_port_approach:{_format_segment(blocker)}"
    if not occupied_external:
        return None
    if len(occupied_external) != 1 or owner_input is None:
        owners = sorted(
            item.owner_input for item in occupied_external if item.owner_input is not None
        )
        return f"multiple_crossings owners={','.join(f'I{owner}' for owner in owners)}"
    crossed = occupied_external[0]
    if crossed.owner_input is None:
        return "unclassified_direct_port_approach_crossing"
    if crossed.owner_input == owner_input:
        return f"blocked_by=I{crossed.owner_input}/same_net_direct_port_approach"
    location = _orthogonal_crossing_point(segment, crossed.segment)
    if location is None:
        return "unclassified_direct_port_approach_crossing"
    clearance = _effective_spacing_threshold(
        rules.min_crossing_clearance_um or 0.0,
        rules.waveguide_width_um,
    )
    if _manhattan(location, segment[0]) < clearance - EPS:
        return (
            f"blocked_by=I{crossed.owner_input}/direct_port_approach_clearance "
            f"at=({location[0]:.3f},{location[1]:.3f})"
        )
    if _manhattan(location, segment[1]) < clearance - EPS:
        return (
            f"blocked_by=I{crossed.owner_input}/direct_port_approach_clearance "
            f"at=({location[0]:.3f},{location[1]:.3f})"
        )
    if _manhattan(location, crossed.segment[0]) < clearance - EPS:
        return (
            f"blocked_by=I{crossed.owner_input}/direct_port_approach_clearance "
            f"at=({location[0]:.3f},{location[1]:.3f})"
        )
    if _manhattan(location, crossed.segment[1]) < clearance - EPS:
        return (
            f"blocked_by=I{crossed.owner_input}/direct_port_approach_clearance "
            f"at=({location[0]:.3f},{location[1]:.3f})"
        )
    pair = _owner_pair(owner_input, crossed.owner_input)
    if rules.hard_crossing_budget and crossing_count_by_pair.get(pair, 0) >= 1:
        return (
            f"blocked_by=I{crossed.owner_input}/direct_port_approach_budget "
            f"pair=I{pair[0]}-I{pair[1]} "
            f"at=({location[0]:.3f},{location[1]:.3f})"
        )
    return None

def _direct_port_approach_crossing_owners(
    segment: Segment,
    occupied_segments: list[OccupiedRouteSegment],
    owner_input: int | None,
) -> set[int]:
    if owner_input is None:
        return set()
    return {
        item.owner_input
        for item in occupied_segments
        if item.kind == "external"
        and item.owner_input is not None
        and item.owner_input != owner_input
        and _orthogonal_crossing_point(segment, item.segment) is not None
    }

def _port_approach_point(
    dst_label: str,
    dst: Point,
    rules: RoutingRules,
) -> Point | None:
    if "." not in dst_label:
        return None
    _cell_id, port = dst_label.rsplit(".", 1)
    try:
        side = port_side(port)
    except ValueError:
        return None
    offset = max(rules.grid_pitch_um, 2.0 * rules.bend_radius_um)
    if side == "left":
        return (_canonical_track(dst[0] - offset), dst[1])
    if side == "right":
        return (_canonical_track(dst[0] + offset), dst[1])
    return None

def _extend_branch_with_hop(
    branch: _PathBranch,
    external_path: tuple[Point, ...],
    spec: _ExternalHopSpec,
    steps: list[RouteStep],
    cells: dict[str, MRRCell],
    occupied: list[OccupiedRouteSegment],
    owner_input: int,
    rules: RoutingRules,
    port_stub_um: float,
) -> _PathBranch:
    points = list(branch.points)
    external_segments = list(branch.external_segments)
    local_segments = list(branch.local_segments)
    if points and not _same_point(points[-1], external_path[0]):
        raise RoutingError(
            f"bounded fallback branch endpoint mismatch at {spec.src_label}->{spec.dst_label}"
        )
    route_points = external_path if not points else external_path[1:]
    _append_external(points, external_segments, route_points)
    if spec.after_step_idx is not None:
        _append_local_step(
            points,
            steps[spec.after_step_idx],
            cells,
            occupied,
            owner_input,
            external_segments,
            local_segments,
            rules,
            port_stub_um,
        )
    return _PathBranch(
        points=points,
        external_segments=external_segments,
        local_segments=local_segments,
        history_cost=_clone_history_cost(branch.history_cost),
    )

def _append_local_step(
    points: list[Point],
    step: RouteStep,
    cells: dict[str, MRRCell],
    occupied: list[OccupiedRouteSegment],
    owner_input: int,
    external_segments: list[Segment],
    local_segments: list[Segment],
    rules: RoutingRules,
    port_stub_um: float,
) -> None:
    cell = cells[step.mrr_id]
    in_route = _port_route_point(cell, step.in_port, rules, port_stub_um)
    in_escape = _port_escape_point(cell, step.in_port, rules, port_stub_um)
    in_stub = _port_stub_point(cell, step.in_port, port_stub_um)
    in_port = cell.port_xy(step.in_port)
    out_port = cell.port_xy(step.out_port)
    out_stub = _port_stub_point(cell, step.out_port, port_stub_um)
    out_escape = _port_escape_point(cell, step.out_port, rules, port_stub_um)
    out_route = _port_route_point(cell, step.out_port, rules, port_stub_um)

    _append_owned_local_axis(
        points,
        in_route,
        in_escape,
        occupied,
        owner_input,
        external_segments,
        local_segments,
        rules,
    )
    _append_owned_local_axis(
        points,
        in_escape,
        in_stub,
        occupied,
        owner_input,
        external_segments,
        local_segments,
        rules,
    )
    _append_owned_local_axis(
        points,
        in_stub,
        in_port,
        occupied,
        owner_input,
        external_segments,
        local_segments,
        rules,
    )
    _append_points(points, _mrr_internal_points(cell, step.in_port, step.out_port)[1:])
    _append_owned_local_axis(
        points,
        out_port,
        out_stub,
        occupied,
        owner_input,
        external_segments,
        local_segments,
        rules,
    )
    _append_owned_local_axis(
        points,
        out_stub,
        out_escape,
        occupied,
        owner_input,
        external_segments,
        local_segments,
        rules,
    )
    _append_owned_local_axis(
        points,
        out_escape,
        out_route,
        occupied,
        owner_input,
        external_segments,
        local_segments,
        rules,
    )

def _clone_history_cost(history_cost: HistoryCost) -> HistoryCost:
    return HistoryCost(
        penalties=dict(history_cost.penalties),
        step_penalty_um=history_cost.step_penalty_um,
    )

def _segments_from_points(points: tuple[Point, ...]) -> tuple[Segment, ...]:
    return tuple(
        (start, end)
        for start, end in zip(points, points[1:])
        if not _same_point(start, end)
    )

def _sort_candidates_by_history(
    candidates: tuple[tuple[Point, ...], ...] | list[tuple[Point, ...]],
    history_cost: HistoryCost | None,
    grid: tuple[tuple[float, ...], tuple[float, ...]],
) -> tuple[tuple[Point, ...], ...]:
    if history_cost is None or not history_cost.penalties:
        return tuple(candidates)
    return tuple(
        sorted(
            candidates,
            key=lambda points: (
                _history_polyline_cost(points, history_cost, grid),
                _polyline_length(points),
                _bend_count(points),
                len(points),
                points,
            ),
        )
    )

def _history_polyline_cost(
    points: tuple[Point, ...],
    history_cost: HistoryCost,
    grid: tuple[tuple[float, ...], tuple[float, ...]],
) -> float:
    x_tracks, y_tracks = grid
    total = 0.0
    for segment in _segments_from_points(points):
        orientation = _history_segment_orientation(segment)
        if not orientation:
            continue
        for x_idx, x in enumerate(x_tracks):
            for y_idx, y in enumerate(y_tracks):
                if _point_on_segment((x, y), segment):
                    total += history_cost.cost(RouterState(x_idx, y_idx, orientation))
    return total

def _history_segment_orientation(segment: Segment) -> str:
    direction = _direction(segment[0], segment[1])
    if direction == "h":
        return "E" if segment[1][0] >= segment[0][0] else "W"
    if direction == "v":
        return "N" if segment[1][1] >= segment[0][1] else "S"
    return ""

def _sort_candidates_for_hop(
    candidates: tuple[tuple[Point, ...], ...] | list[tuple[Point, ...]],
    history_cost: HistoryCost | None,
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    obstacles: list[Obstacle],
    port_access: PortAccessLegality | None,
    rules: RoutingRules,
    blockers: list[Segment] | tuple[Segment, ...] = (),
) -> tuple[tuple[Point, ...], ...]:
    return tuple(
        sorted(
            candidates,
            key=lambda points: (
                *_candidate_route_sort_key(points, obstacles, port_access, rules, blockers),
                _history_polyline_cost(points, history_cost, grid)
                if history_cost is not None
                else 0.0,
            ),
        )
    )

def _candidate_route_sort_key(
    points: tuple[Point, ...],
    obstacles: list[Obstacle],
    port_access: PortAccessLegality | None,
    rules: RoutingRules,
    blockers: list[Segment] | tuple[Segment, ...] = (),
) -> tuple[float, float, int, tuple[Point, ...]]:
    return (
        _candidate_route_cost(points, obstacles, port_access, rules, blockers),
        float(_bend_count(points)),
        len(points),
        points,
    )

def _candidate_route_cost(
    points: tuple[Point, ...],
    obstacles: list[Obstacle],
    port_access: PortAccessLegality | None,
    rules: RoutingRules,
    blockers: list[Segment] | tuple[Segment, ...] = (),
) -> float:
    length = _polyline_length(points)
    bend_count = _bend_count(points)
    crossing_count = sum(
        _segment_crossing_count(segment, list(blockers))
        for segment in _segments_from_points(points)
    )
    if rules.cost_model == "db":
        if rules.prop_loss_db_per_um <= 0.0:
            raise ValueError("db cost_model requires positive prop_loss_db_per_um")
        cost = length + bend_count * 0.5 * pi * rules.bend_radius_um
        cost += bend_count * (
            rules.bend_loss_db_per_bend / rules.prop_loss_db_per_um
        )
        cost += crossing_count * (
            rules.crossing_loss_db_per_cross / rules.prop_loss_db_per_um
        )
        cost += _candidate_jog_count(points) * (
            rules.jog_penalty_db / rules.prop_loss_db_per_um
        )
        cost += _candidate_sensitive_bend_count(
            points,
            obstacles,
            port_access,
            rules,
        ) * (rules.bend_placement_penalty_db / rules.prop_loss_db_per_um)
        return cost
    if rules.loss_aware_cost:
        cost = length * rules.prop_loss_db_per_um + bend_count * rules.bend_loss_db_per_bend
        cost += crossing_count * rules.crossing_loss_db_per_cross
        cost += _candidate_jog_count(points) * rules.jog_penalty_db
        cost += (
            _candidate_sensitive_bend_count(points, obstacles, port_access, rules)
            * rules.bend_placement_penalty_db
        )
        return cost
    return (
        length
        + bend_count * 0.5 * pi * rules.bend_radius_um
        + crossing_count * rules.crossing_penalty_um
    )

def _candidate_jog_count(points: tuple[Point, ...]) -> int:
    signs: list[int] = []
    jogs = 0
    for previous, current, next_point in zip(points, points[1:], points[2:]):
        prev_dir = _orientation_between(previous, current)
        next_dir = _orientation_between(current, next_point)
        sign = _turn_sign(prev_dir, next_dir)
        if sign == 0:
            continue
        if len(signs) >= 2 and signs[-2] == sign and signs[-1] == -sign:
            jogs += 1
        signs = (signs + [sign])[-2:]
    return jogs

def _candidate_sensitive_bend_count(
    points: tuple[Point, ...],
    obstacles: list[Obstacle],
    port_access: PortAccessLegality | None,
    rules: RoutingRules,
) -> int:
    if rules.turn_guard_um <= EPS:
        return 0
    return sum(
        1
        for previous, current, next_point in zip(points, points[1:], points[2:])
        if _direction(previous, current) != _direction(current, next_point)
        and _point_near_sensitive_geometry(current, obstacles, port_access, rules)
    )

def _point_near_sensitive_geometry(
    point: Point,
    obstacles: list[Obstacle],
    port_access: PortAccessLegality | None,
    rules: RoutingRules,
) -> bool:
    guard = rules.turn_guard_um
    if any(_point_to_obstacle_distance(point, obstacle) < guard - EPS for obstacle in obstacles):
        return True
    if port_access is None:
        return False
    for access_point in port_access.plan.points_by_cell_port.values():
        if (
            _manhattan(point, access_point.port_xy) < guard - EPS
            or _manhattan(point, access_point.stub_xy) < guard - EPS
            or _manhattan(point, access_point.escape_xy) < guard - EPS
            or _manhattan(point, access_point.route_xy) < guard - EPS
        ):
            return True
    return any(
        _point_to_axis_segment_distance(point, region.segment) < guard - EPS
        for region in port_access.plan.reserved_regions
    )

def _orientation_between(src: Point, dst: Point) -> str:
    if abs(src[0] - dst[0]) >= EPS:
        return "E" if dst[0] > src[0] else "W"
    if abs(src[1] - dst[1]) >= EPS:
        return "N" if dst[1] > src[1] else "S"
    return ""

def _branch_score(
    branch: _PathBranch,
    occupied: list[OccupiedRouteSegment],
    owner_input: int,
    rules: RoutingRules,
) -> tuple[float, ...]:
    route_points = _dedupe_points(tuple(branch.points))
    pair_counts = _crossing_count_by_pair_for_current_net(
        occupied,
        branch.external_segments,
        owner_input,
    )
    return (
        float(sum(pair_counts.values())),
        float(len(branch.external_segments)),
        float(len(branch.local_segments)),
        _polyline_length(route_points),
        float(_bend_count(route_points)),
    )

def _route_one_path_attempt(
    path: Path,
    cells: dict[str, MRRCell],
    occupied: list[OccupiedRouteSegment],
    reserved_port_access: list[Segment],
    reserved_port_access_guards: list[Segment] | list[OccupiedRouteSegment],
    soft_reserved_port_access: list[Segment],
    obstacles: list[Obstacle],
    grid: tuple[tuple[float, ...], tuple[float, ...]],
    rules: RoutingRules,
    *,
    n_physical: int,
    port_stub_um: float,
    x_start: float,
    x_end: float,
    wire_pitch_um: float,
    max_stage: int | None,
    forbidden_points: set[Point],
    corridor_slots: dict[tuple[float, float], float] | None = None,
    port_access: PortAccessLegality | None = None,
    grid_bounds: RoutingWindow | None = None,
    ripup_pass_idx: int = 0,
    history_cost: HistoryCost | None = None,
    external_segments_out: list[Segment] | None = None,
    penalized_crossing_owners: set[int] | None = None,
    source_crossings: set[_CrossingSource] | None = None,
    allocation_crossings: set[_CrossingSource] | None = None,
) -> PhysicalRoute:
    steps = [step for step in path.steps if max_stage is None or step.stage <= max_stage]
    if not steps:
        raise RoutingError(f"I{path.input_port} has no active MRR steps to route")
    corridor_slots = corridor_slots or {}
    grid_bounds = grid_bounds or _io_clamped_routing_bounds(grid, x_start, x_end)
    history_cost = history_cost or HistoryCost()
    penalized_crossing_owners = penalized_crossing_owners or set()
    source_crossings = source_crossings or set()
    allocation_crossings = allocation_crossings or set()

    points: list[Point] = []
    external_segments: list[Segment] = (
        external_segments_out if external_segments_out is not None else []
    )
    local_segments: list[Segment] = []

    current = (x_start, _wire_y(path.input_port, n_physical, wire_pitch_um))
    first_step = steps[0]
    first_cell = cells[first_step.mrr_id]
    first_escape = _port_route_point(first_cell, first_step.in_port, rules, port_stub_um)
    future_hops = _external_hops_after(
        steps,
        cells,
        rules,
        port_stub_um,
        start_index=-1,
        terminal=(x_end, _wire_y(path.output_port, n_physical, wire_pitch_um)),
    )
    occupied_blockers = _occupied_blocker_segments(occupied)
    first_external = _route_external_hop(
        current,
        first_escape,
        occupied_blockers + external_segments + local_segments + reserved_port_access,
        obstacles,
        grid,
        rules,
        src_label=f"I{path.input_port}",
        dst_label=f"{first_step.mrr_id}.{first_step.in_port}",
        forbidden_points=forbidden_points,
        current_segments=external_segments + local_segments,
        current_external_segments=external_segments,
        occupied_segments=occupied,
        crossing_count_by_pair=_crossing_count_by_pair_for_current_net(
            occupied,
            external_segments,
            path.input_port,
        ),
        crossing_sources_by_pair=_crossing_sources_by_pair_for_current_net(
            occupied,
            external_segments,
            path.input_port,
        ),
        soft_blockers=soft_reserved_port_access,
        reserved_spacing_blockers=[],
        reserved_guard_blockers=reserved_port_access_guards,
        reserved_crossing_owners=_reserved_future_crossing_owners(
            future_hops,
            occupied,
            path.input_port,
            rules,
        ),
        deferred_crossing_owners=penalized_crossing_owners | _deferred_route_owners(
            future_hops,
            port_access,
            occupied,
            path.input_port,
        ),
        preferred_bend_x=corridor_slots.get(_corridor_key(current, first_escape)),
        reserve_space_penalty=True,
        port_access=port_access,
        grid_bounds=grid_bounds,
        base_detour_tracks=1,
        ripup_pass_idx=ripup_pass_idx,
        history_cost=history_cost,
        future_local_segments=_local_step_segments(
            first_step,
            cells,
            rules,
            port_stub_um,
        ),
        source_crossings=source_crossings,
        allocation_crossings=allocation_crossings,
        repeated_crossing_penalty_scale=_repeated_crossing_penalty_scale_for_hop(
            None,
            first_step.in_port,
            rules,
        ),
    )
    _append_external(points, external_segments, first_external)

    for step_idx, step in enumerate(steps):
        cell = cells[step.mrr_id]
        in_route = _port_route_point(cell, step.in_port, rules, port_stub_um)
        in_escape = _port_escape_point(cell, step.in_port, rules, port_stub_um)
        in_stub = _port_stub_point(cell, step.in_port, port_stub_um)
        in_port = cell.port_xy(step.in_port)
        out_port = cell.port_xy(step.out_port)
        out_stub = _port_stub_point(cell, step.out_port, port_stub_um)
        out_escape = _port_escape_point(cell, step.out_port, rules, port_stub_um)
        out_route = _port_route_point(cell, step.out_port, rules, port_stub_um)

        occupied_blockers = _occupied_blocker_segments(occupied)
        _append_owned_local_axis(points, in_route, in_escape, occupied, path.input_port, external_segments, local_segments, rules)
        _append_owned_local_axis(points, in_escape, in_stub, occupied, path.input_port, external_segments, local_segments, rules)
        _append_owned_local_axis(points, in_stub, in_port, occupied, path.input_port, external_segments, local_segments, rules)
        _append_points(points, _mrr_internal_points(cell, step.in_port, step.out_port)[1:])
        _append_owned_local_axis(points, out_port, out_stub, occupied, path.input_port, external_segments, local_segments, rules)
        _append_owned_local_axis(points, out_stub, out_escape, occupied, path.input_port, external_segments, local_segments, rules)
        _append_owned_local_axis(points, out_escape, out_route, occupied, path.input_port, external_segments, local_segments, rules)

        if step_idx + 1 < len(steps):
            next_step = steps[step_idx + 1]
            next_cell = cells[next_step.mrr_id]
            next_escape = _port_route_point(next_cell, next_step.in_port, rules, port_stub_um)
            future_hops = _external_hops_after(
                steps,
                cells,
                rules,
                port_stub_um,
                start_index=step_idx,
                terminal=(x_end, _wire_y(path.output_port, n_physical, wire_pitch_um)),
            )
            external = _route_external_hop(
                out_route,
                next_escape,
                occupied_blockers + external_segments + local_segments + reserved_port_access,
                obstacles,
                grid,
                rules,
                src_label=f"{step.mrr_id}.{step.out_port}",
                dst_label=f"{next_step.mrr_id}.{next_step.in_port}",
                forbidden_points=forbidden_points,
                current_segments=external_segments + local_segments,
                current_external_segments=external_segments,
                occupied_segments=occupied,
                crossing_count_by_pair=_crossing_count_by_pair_for_current_net(
                    occupied,
                    external_segments,
                    path.input_port,
                ),
                crossing_sources_by_pair=_crossing_sources_by_pair_for_current_net(
                    occupied,
                    external_segments,
                    path.input_port,
                ),
                soft_blockers=soft_reserved_port_access,
                reserved_spacing_blockers=[],
                reserved_guard_blockers=reserved_port_access_guards,
                reserved_crossing_owners=_reserved_future_crossing_owners(
                    future_hops,
                    occupied,
                    path.input_port,
                    rules,
                ),
                deferred_crossing_owners=penalized_crossing_owners | _deferred_route_owners(
                    future_hops,
                    port_access,
                    occupied,
                    path.input_port,
                ),
                preferred_bend_x=corridor_slots.get(_corridor_key(out_route, next_escape)),
                port_access=port_access,
                grid_bounds=grid_bounds,
                base_detour_tracks=2,
                ripup_pass_idx=ripup_pass_idx,
                history_cost=history_cost,
                future_local_segments=_local_step_segments(
                    next_step,
                    cells,
                    rules,
                    port_stub_um,
                ),
                source_crossings=source_crossings,
                allocation_crossings=allocation_crossings,
                repeated_crossing_penalty_scale=_repeated_crossing_penalty_scale_for_hop(
                    step.out_port,
                    next_step.in_port,
                    rules,
                ),
            )
            _append_external(points, external_segments, external[1:])

    if len(steps) == len(path.steps):
        terminal = (x_end, _wire_y(path.output_port, n_physical, wire_pitch_um))
        last_step = steps[-1]
        external = _route_external_hop(
            points[-1],
            terminal,
            _occupied_blocker_segments(occupied) + external_segments + local_segments + reserved_port_access,
            obstacles,
            grid,
            rules,
            src_label=f"{last_step.mrr_id}.{last_step.out_port}",
            dst_label=f"O{path.output_port}",
            forbidden_points=forbidden_points,
            current_segments=external_segments + local_segments,
            current_external_segments=external_segments,
            occupied_segments=occupied,
            crossing_count_by_pair=_crossing_count_by_pair_for_current_net(
                occupied,
                external_segments,
                path.input_port,
            ),
            crossing_sources_by_pair=_crossing_sources_by_pair_for_current_net(
                occupied,
                external_segments,
                path.input_port,
            ),
            soft_blockers=soft_reserved_port_access,
            reserved_spacing_blockers=[],
            reserved_guard_blockers=reserved_port_access_guards,
            reserved_crossing_owners=set(),
            deferred_crossing_owners=penalized_crossing_owners,
            preferred_bend_x=corridor_slots.get(_corridor_key(points[-1], terminal)),
            reserve_space_penalty=True,
            port_access=port_access,
            grid_bounds=grid_bounds,
            base_detour_tracks=1,
            ripup_pass_idx=ripup_pass_idx,
            history_cost=history_cost,
            source_crossings=source_crossings,
            allocation_crossings=allocation_crossings,
            repeated_crossing_penalty_scale=_repeated_crossing_penalty_scale_for_hop(
                last_step.out_port,
                None,
                rules,
            ),
        )
        _append_external(points, external_segments, external[1:])

    route_points = _dedupe_points(tuple(points))
    bend_count = _bend_count(route_points)
    bend_arc_um = 0.5 * pi * rules.bend_radius_um
    return PhysicalRoute(
        input_port=path.input_port,
        output_port=path.output_port,
        waypoints=route_points,
        length_um=_polyline_length(route_points) + bend_count * bend_arc_um,
        bend_count=bend_count,
        external_segments=tuple(external_segments),
        local_segments=tuple(local_segments),
    )

def _external_hops_after(
    steps: list[RouteStep],
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    port_stub_um: float,
    *,
    start_index: int,
    terminal: Point,
) -> list[tuple[Point, Point]]:
    hops: list[tuple[Point, Point]] = []
    for idx in range(start_index + 1, len(steps) - 1):
        left = steps[idx]
        right = steps[idx + 1]
        src = _port_route_point(cells[left.mrr_id], left.out_port, rules, port_stub_um)
        dst = _port_route_point(cells[right.mrr_id], right.in_port, rules, port_stub_um)
        hops.append((src, dst))
    if start_index < len(steps) - 1 and steps:
        last = steps[-1]
        src = _port_route_point(cells[last.mrr_id], last.out_port, rules, port_stub_um)
        hops.append((src, terminal))
    return hops

def _deferred_crossing_owners(
    future_hops: list[tuple[Point, Point]],
    port_access: PortAccessLegality | None,
    current_owner: int,
) -> set[int]:
    if port_access is None or not future_hops:
        return set()
    owners: set[int] = set()
    for region in port_access.plan.reserved_regions:
        if region.owner_input is None or region.owner_input == current_owner:
            continue
        if any(_segment_near_hop_bbox(region.segment, hop) for hop in future_hops):
            owners.add(region.owner_input)
    return owners

def _deferred_route_owners(
    future_hops: list[tuple[Point, Point]],
    port_access: PortAccessLegality | None,
    occupied: list[OccupiedRouteSegment],
    current_owner: int,
) -> set[int]:
    return _deferred_crossing_owners(
        future_hops,
        port_access,
        current_owner,
    ) | _future_waveguide_crossing_owners(
        future_hops,
        occupied,
        current_owner,
    )

def _reserved_future_crossing_owners(
    future_hops: list[tuple[Point, Point]],
    occupied: list[OccupiedRouteSegment],
    current_owner: int,
    rules: RoutingRules,
) -> set[int]:
    if not rules.future_crossing_reservation:
        return set()
    return _future_waveguide_crossing_owners(
        future_hops,
        occupied,
        current_owner,
    )

def _future_waveguide_crossing_owners(
    future_hops: list[tuple[Point, Point]],
    occupied: list[OccupiedRouteSegment],
    current_owner: int,
) -> set[int]:
    if not future_hops:
        return set()
    owners: set[int] = set()
    occupied_external = [
        item
        for item in occupied
        if item.kind == "external" and item.owner_input != current_owner
    ]
    for hop in future_hops:
        for owner in _forced_l_shape_crossing_owners(hop, occupied_external):
            owners.add(owner)
    return owners

def _forced_l_shape_crossing_owners(
    hop: tuple[Point, Point],
    occupied_external: list[OccupiedRouteSegment],
) -> set[int]:
    src, dst = hop
    route_options = _l_shape_route_options(src, dst)
    if not route_options:
        return set()
    option_owners: list[set[int]] = []
    for option in route_options:
        owners: set[int] = set()
        for segment in _segments_from_points(option):
            for occupied in occupied_external:
                if (
                    occupied.owner_input is not None
                    and _orthogonal_crossing_point(segment, occupied.segment) is not None
                ):
                    owners.add(occupied.owner_input)
        option_owners.append(owners)
    if not option_owners:
        return set()
    forced = set(option_owners[0])
    for owners in option_owners[1:]:
        forced &= owners
    return forced

def _l_shape_route_options(src: Point, dst: Point) -> tuple[tuple[Point, ...], ...]:
    if _same_point(src, dst):
        return ()
    if abs(src[0] - dst[0]) < EPS or abs(src[1] - dst[1]) < EPS:
        return ((src, dst),)
    via_x_first = (dst[0], src[1])
    via_y_first = (src[0], dst[1])
    if _same_point(via_x_first, via_y_first):
        return ((src, via_x_first, dst),)
    return (
        (src, via_x_first, dst),
        (src, via_y_first, dst),
    )

def _segment_near_hop_bbox(
    segment: Segment,
    hop: tuple[Point, Point],
    *,
    slack_um: float = 16.0,
) -> bool:
    src, dst = hop
    x_lo, x_hi = sorted((src[0], dst[0]))
    y_lo, y_hi = sorted((src[1], dst[1]))
    sx_lo, sx_hi = sorted((segment[0][0], segment[1][0]))
    sy_lo, sy_hi = sorted((segment[0][1], segment[1][1]))
    return (
        sx_hi >= x_lo - slack_um
        and sx_lo <= x_hi + slack_um
        and sy_hi >= y_lo - slack_um
        and sy_lo <= y_hi + slack_um
    )

def _ripup_order(
    paths: list[Path],
    failed_inputs: set[int],
    pass_idx: int,
    n_physical: int,
    wire_pitch_um: float,
) -> list[Path]:
    def complexity(path: Path) -> float:
        return _path_routing_complexity(path, n_physical, wire_pitch_um)

    if pass_idx == 1:
        return sorted(
            paths,
            key=lambda path: (
                path.input_port not in failed_inputs,
                complexity(path),
                path.input_port,
            ),
        )
    if pass_idx == 2:
        return sorted(
            paths,
            key=lambda path: (
                path.input_port not in failed_inputs,
                -complexity(path),
                path.input_port,
            ),
        )
    if pass_idx == 3:
        return sorted(
            paths,
            key=lambda path: (
                path.input_port not in failed_inputs,
                path.input_port,
            ),
        )
    if pass_idx % 2 == 1:
        return sorted(
            paths,
            key=lambda path: (
                path.input_port not in failed_inputs,
                complexity(path),
                path.input_port,
            ),
        )
    return sorted(
        paths,
        key=lambda path: (-complexity(path), path.input_port),
    )

def _path_routing_complexity(
    path: Path,
    n_physical: int,
    wire_pitch_um: float,
) -> float:
    """Estimate routing difficulty so simple nets claim straight corridors first."""
    y_start = _wire_y(path.input_port, n_physical, wire_pitch_um)
    y_end = _wire_y(path.output_port, n_physical, wire_pitch_um)
    y_span = abs(y_end - y_start)
    n_cross = sum(1 for step in path.steps if step.state == 1)
    n_steps = len(path.steps)
    return y_span + 18.0 * n_cross + 6.0 * n_steps

def _path_endpoint_span(path: Path) -> float:
    return abs(path.output_port - path.input_port) + 0.25 * len(path.steps)
