from __future__ import annotations

from itertools import combinations
from math import pi

from ..core.models import MRRCell
from ..core.topology import Path, RNBTopology, RouteStep
from ..placement.layout import wire_y
from .drc import validate_physical_routes
from .geometry import (
    _axis_segments,
    _bend_count,
    _dedupe_points,
    _orthogonal_crossing_point,
    _polyline_length,
    _segment_contact_point,
    _segments_collinear_overlap,
)
from .port_access import _mrr_internal_points
from .refinement import _route_crossings, _route_with_crossing_count
from .types import PhysicalRoute, PhysicalRoutingResult, Point, RoutingRules, RoutingStats, Segment
from .types import RouteCrossing


_PORT_RUN_X_UM = 24.0
# The inflated MRR keepout ends 20 um from the cell center (14 um half-width
# plus 6 um keepout).  Putting the channel anchor on that boundary opens six
# spare riser tracks without changing the pinned octave canvas.
_CHANNEL_ANCHOR_X_UM = 20.0
_RISER_END_RUN_UM = 12.0
_RISER_TRACK_PITCH_UM = 8.0
_RISER_ORDER_BEAM_WIDTH = 512
_RISER_ORDER_MAX_DEPTH = 8
# Re-residued for the v3 port rows.  Both cell-side wrap risers remain safely
# above 2R after port dy moves from +/-4.0 to +/-5.5 um.
_SOUTH_WRAP_OFFSET_Y_UM = 21.5
_NORTH_WRAP_OFFSET_Y_UM = 25.5


def predicted_template_crossings(n_physical: int) -> int:
    try:
        return {4: 14, 8: 60, 16: 232}[n_physical]
    except KeyError:
        raise ValueError(f"no canonical Beneš template for P={n_physical}") from None


def emit_benes_template(
    topology: RNBTopology,
    paths: list[Path],
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    *,
    x_start: float,
    x_end: float,
    wire_pitch_um: float,
) -> PhysicalRoutingResult:
    """Emit the canonical convention-A fixed Beneš geometry without A*."""
    if not topology.name.startswith("padded_benes_"):
        raise ValueError("the canonical template is only defined for padded Beneš")
    if topology.N_physical not in {4, 8, 16}:
        raise ValueError(f"no canonical template for P={topology.N_physical}")
    if topology.stage_permutations:
        raise ValueError("the canonical template requires permutation-free stage wiring")

    by_wire = {path.input_port: path for path in paths}
    if set(by_wire) != set(range(topology.N_physical)):
        raise ValueError("template requires one static waveguide for every physical wire")
    if any(path.output_port != path.input_port for path in paths):
        raise ValueError("template waveguides must retain their physical wire index")

    channel_paths = _channel_paths(
        topology,
        by_wire,
        cells,
        x_start=x_start,
        x_end=x_end,
        wire_pitch_um=wire_pitch_um,
    )
    routes = tuple(
        _emit_wire_route(
            by_wire[wire],
            channel_paths,
            cells,
            rules,
        )
        for wire in range(topology.N_physical)
    )
    crossings = _route_crossings(routes)
    routes = tuple(_route_with_crossing_count(route, crossings) for route in routes)
    violations = validate_physical_routes(routes, cells, rules)
    return PhysicalRoutingResult(
        routes=routes,
        drc_violations=violations,
        failed_nets=(),
        crossings=crossings,
        rules=rules,
        crossing_count_by_pair=_crossing_count_by_pair(crossings),
        stats=RoutingStats(astar_calls=0, ripup_passes_executed=0),
    )


def _channel_paths(
    topology: RNBTopology,
    by_wire: dict[int, Path],
    cells: dict[str, MRRCell],
    *,
    x_start: float,
    x_end: float,
    wire_pitch_um: float,
) -> dict[tuple[int, int], tuple[Point, ...]]:
    channels: dict[tuple[int, int], tuple[Point, ...]] = {}
    n_stages = topology.n_stages
    for channel in range(n_stages + 1):
        connections: list[tuple[int, Point, Point]] = []
        for wire, path in by_wire.items():
            if channel == 0:
                source = (x_start, wire_y(wire, topology.N_physical, wire_pitch_um))
            else:
                source = _egress_anchor(path.steps[channel - 1], cells)
            if channel == n_stages:
                target = (x_end, wire_y(wire, topology.N_physical, wire_pitch_um))
            else:
                target = _ingress_anchor(path.steps[channel], cells)
            connections.append((wire, source, target))

        if channel == 0:
            # Stage 0 pairs are adjacent. Their vertical spans are disjoint, so
            # one shared riser is deterministic and creates zero crossings.
            riser_by_wire = {wire: x_start + 12.0 for wire, _s, _t in connections}
        elif channel == n_stages:
            # Convention-A output wraps invert the two members of each adjacent
            # pair. Two shared risers produce exactly one crossing per pair.
            left = min(source[0] for _wire, source, _target in connections)
            riser_by_wire = {
                wire: left + (10.0 if wire % 2 else 20.0)
                for wire, _source, _target in connections
            }
        else:
            left = max(source[0] for _wire, source, _target in connections)
            right = min(target[0] for _wire, _source, target in connections)
            riser_by_wire = _guarded_riser_assignment(
                connections,
                left=left,
                right=right,
            )

        for wire, source, target in connections:
            riser_x = riser_by_wire[wire]
            channels[(wire, channel)] = _dedupe_points(
                (source, (riser_x, source[1]), (riser_x, target[1]), target)
            )
    return channels


def _canonical_riser_order(
    connections: list[tuple[int, Point, Point]],
) -> tuple[int, ...]:
    rising = sorted(
        (item for item in connections if item[2][1] > item[1][1]),
        key=lambda item: (-item[1][1], item[0]),
    )
    flat = sorted(
        (item for item in connections if item[2][1] == item[1][1]),
        key=lambda item: (-item[1][1], item[0]),
    )
    falling = sorted(
        (item for item in connections if item[2][1] < item[1][1]),
        key=lambda item: (item[1][1], item[0]),
    )
    return tuple(item[0] for item in (*rising, *flat, *falling))


def _guarded_riser_assignment(
    connections: list[tuple[int, Point, Point]],
    *,
    left: float,
    right: float,
) -> dict[int, float]:
    """Assign two-track guards to every adjacent-riser crossing class.

    A crossing between neighboring risers otherwise has only one 8 um track
    from the crossing to the adjacent bend.  Non-crossing neighbors retain one
    track, while crossing neighbors receive two.  One unused track is also
    reserved beside the target cell's fixed wrap riser.

    P16's two middle channels have more adjacent crossings than the six spare
    tracks can guard in their canonical order.  In that case a small,
    deterministic beam finds the nearest count-preserving, contact-free order;
    P4, P8, and all other P16 channels keep the canonical order unchanged.
    """
    crossing_counts, contact_free = _riser_pair_tables(connections)
    canonical = _canonical_riser_order(connections)
    track_span = (right - left - 2.0 * _RISER_END_RUN_UM) / _RISER_TRACK_PITCH_UM
    rounded_track_span = round(track_span)
    if abs(track_span - rounded_track_span) > 1e-9:
        raise ValueError(
            "canonical Beneš riser corridor does not land on the 8 um track grid"
        )
    spare_tracks = int(rounded_track_span) - (len(canonical) - 1)
    max_adjacent_crossings = spare_tracks - 1
    if max_adjacent_crossings < 0:
        raise ValueError("canonical Beneš riser corridor has no wrap-guard track")
    order = _guarded_riser_order(
        canonical,
        crossing_counts,
        contact_free,
        max_adjacent_crossings=max_adjacent_crossings,
    )

    positions = [left + _RISER_END_RUN_UM]
    for first, second in zip(order, order[1:]):
        guarded_tracks = 2 if _ordered_pair_value(
            crossing_counts,
            first,
            second,
            True,
        ) else 1
        positions.append(positions[-1] + guarded_tracks * _RISER_TRACK_PITCH_UM)
    maximum = right - _RISER_END_RUN_UM - _RISER_TRACK_PITCH_UM
    if positions[-1] > maximum + 1e-9:
        raise ValueError(
            "canonical Beneš riser corridor cannot provide two-track "
            "crossing arms and the target wrap guard"
        )
    return dict(zip(order, positions))


def _guarded_riser_order(
    canonical: tuple[int, ...],
    crossing_counts: dict[tuple[int, int, bool], int],
    contact_free: dict[tuple[int, int, bool], bool],
    *,
    max_adjacent_crossings: int,
) -> tuple[int, ...]:
    target_crossings, adjacent_crossings = _riser_order_metrics(
        canonical,
        crossing_counts,
    )
    if adjacent_crossings <= max_adjacent_crossings:
        return canonical

    beam: tuple[tuple[int, ...], ...] = (canonical,)
    seen = {canonical}
    for _depth in range(1, _RISER_ORDER_MAX_DEPTH + 1):
        candidates: set[tuple[int, ...]] = set()
        for order in beam:
            for first, second in combinations(range(len(order)), 2):
                candidate_list = list(order)
                candidate_list[first], candidate_list[second] = (
                    candidate_list[second],
                    candidate_list[first],
                )
                candidate = tuple(candidate_list)
                if candidate in seen:
                    continue
                seen.add(candidate)
                metrics = _riser_order_metrics(candidate, crossing_counts)
                if metrics[0] != target_crossings:
                    continue
                if not _riser_order_is_contact_free(candidate, contact_free):
                    continue
                candidates.add(candidate)
        ranked = sorted(
            candidates,
            key=lambda candidate: (
                _riser_order_metrics(candidate, crossing_counts)[1],
                candidate,
            ),
        )
        if ranked and (
            _riser_order_metrics(ranked[0], crossing_counts)[1]
            <= max_adjacent_crossings
        ):
            return ranked[0]
        beam = tuple(ranked[:_RISER_ORDER_BEAM_WIDTH])
    raise ValueError(
        "canonical Beneš riser ordering cannot preserve its crossing count "
        "with two-track crossing arms"
    )


def _riser_pair_tables(
    connections: list[tuple[int, Point, Point]],
) -> tuple[
    dict[tuple[int, int, bool], int],
    dict[tuple[int, int, bool], bool],
]:
    endpoints = {
        wire: (source[1], target[1])
        for wire, source, target in connections
    }
    crossing_counts: dict[tuple[int, int, bool], int] = {}
    contact_free: dict[tuple[int, int, bool], bool] = {}
    for first, second in combinations(sorted(endpoints), 2):
        for first_before_second in (False, True):
            first_path, second_path = _normalized_riser_pair(
                endpoints[first],
                endpoints[second],
                first_before_second=first_before_second,
            )
            segment_pairs = tuple(
                (segment_a, segment_b)
                for segment_a in _axis_segments(first_path)
                for segment_b in _axis_segments(second_path)
            )
            key = (first, second, first_before_second)
            crossing_counts[key] = sum(
                _orthogonal_crossing_point(segment_a, segment_b) is not None
                for segment_a, segment_b in segment_pairs
            )
            contact_free[key] = not any(
                _segments_collinear_overlap(segment_a, segment_b)
                or _segment_contact_point(segment_a, segment_b) is not None
                for segment_a, segment_b in segment_pairs
            )
    return crossing_counts, contact_free


def _normalized_riser_pair(
    first: tuple[float, float],
    second: tuple[float, float],
    *,
    first_before_second: bool,
) -> tuple[tuple[Point, ...], tuple[Point, ...]]:
    first_x, second_x = (
        (1.0, 2.0) if first_before_second else (2.0, 1.0)
    )
    return (
        ((0.0, first[0]), (first_x, first[0]), (first_x, first[1]), (3.0, first[1])),
        (
            (0.0, second[0]),
            (second_x, second[0]),
            (second_x, second[1]),
            (3.0, second[1]),
        ),
    )


def _riser_order_metrics(
    order: tuple[int, ...],
    crossing_counts: dict[tuple[int, int, bool], int],
) -> tuple[int, int]:
    rank = {wire: index for index, wire in enumerate(order)}
    total = sum(
        _ordered_pair_value(
            crossing_counts,
            first,
            second,
            rank[first] < rank[second],
        )
        for first, second in combinations(sorted(order), 2)
    )
    adjacent = sum(
        bool(_ordered_pair_value(crossing_counts, first, second, True))
        for first, second in zip(order, order[1:])
    )
    return total, adjacent


def _riser_order_is_contact_free(
    order: tuple[int, ...],
    contact_free: dict[tuple[int, int, bool], bool],
) -> bool:
    rank = {wire: index for index, wire in enumerate(order)}
    return all(
        _ordered_pair_value(
            contact_free,
            first,
            second,
            rank[first] < rank[second],
        )
        for first, second in combinations(sorted(order), 2)
    )


def _ordered_pair_value(
    values: dict[tuple[int, int, bool], int]
    | dict[tuple[int, int, bool], bool],
    first: int,
    second: int,
    first_before_second: bool,
) -> int | bool:
    if first < second:
        return values[(first, second, first_before_second)]
    return values[(second, first, not first_before_second)]


def _ingress_anchor(step: RouteStep, cells: dict[str, MRRCell]) -> Point:
    cell = cells[step.mrr_id]
    cx, cy = cell.center
    if step.in_port == "in":
        return cx - _CHANNEL_ANCHOR_X_UM, cell.port_xy("in")[1]
    if step.in_port == "add":
        return cx - _CHANNEL_ANCHOR_X_UM, cy - _SOUTH_WRAP_OFFSET_Y_UM
    raise ValueError(f"unexpected template ingress port {step.in_port!r}")


def _egress_anchor(step: RouteStep, cells: dict[str, MRRCell]) -> Point:
    cell = cells[step.mrr_id]
    cx, cy = cell.center
    if step.out_port == "th":
        return cx + _CHANNEL_ANCHOR_X_UM, cell.port_xy("th")[1]
    if step.out_port == "drop":
        return cx + _CHANNEL_ANCHOR_X_UM, cy + _NORTH_WRAP_OFFSET_Y_UM
    raise ValueError(f"unexpected template egress port {step.out_port!r}")


def _emit_wire_route(
    path: Path,
    channel_paths: dict[tuple[int, int], tuple[Point, ...]],
    cells: dict[str, MRRCell],
    rules: RoutingRules,
) -> PhysicalRoute:
    points: list[Point] = []
    external: list[Segment] = []
    local: list[Segment] = []

    def append_polyline(polyline: tuple[Point, ...], owner: list[Segment] | None) -> None:
        nonlocal points
        if points and points[-1] == polyline[0]:
            additions = polyline[1:]
        else:
            additions = polyline
        points.extend(additions)
        if owner is not None:
            owner.extend(_axis_segments(polyline))

    append_polyline(channel_paths[(path.input_port, 0)], external)
    for stage, step in enumerate(path.steps):
        cell = cells[step.mrr_id]
        cx, cy = cell.center
        in_port = cell.port_xy(step.in_port)
        out_port = cell.port_xy(step.out_port)
        if step.in_port == "in":
            append_polyline((points[-1], in_port), local)
        else:
            ingress = (
                points[-1],
                (cx + _PORT_RUN_X_UM, cy - _SOUTH_WRAP_OFFSET_Y_UM),
                (cx + _PORT_RUN_X_UM, in_port[1]),
            )
            append_polyline(ingress, external)
            append_polyline((points[-1], in_port), local)

        append_polyline(_mrr_internal_points(cell, step.in_port, step.out_port), None)

        if step.out_port == "th":
            append_polyline((out_port, _egress_anchor(step, cells)), local)
        else:
            run = (cx - _PORT_RUN_X_UM, out_port[1])
            append_polyline((out_port, run), local)
            anchor = _egress_anchor(step, cells)
            append_polyline((run, (run[0], anchor[1]), anchor), external)

        append_polyline(channel_paths[(path.input_port, stage + 1)], external)

    route_points = _dedupe_points(tuple(points))
    bend_count = _bend_count(route_points)
    return PhysicalRoute(
        input_port=path.input_port,
        output_port=path.output_port,
        waypoints=route_points,
        length_um=(
            _polyline_length(route_points)
            + bend_count * 0.5 * pi * rules.bend_radius_um
        ),
        bend_count=bend_count,
        external_segments=tuple(external),
        local_segments=tuple(local),
    )


def _crossing_count_by_pair(
    crossings: tuple[RouteCrossing, ...],
) -> tuple[tuple[tuple[int, int], int], ...]:
    counts: dict[tuple[int, int], int] = {}
    for crossing in crossings:
        pair = (
            min(crossing.net_a, crossing.net_b),
            max(crossing.net_a, crossing.net_b),
        )
        counts[pair] = counts.get(pair, 0) + 1
    return tuple(sorted(counts.items()))


__all__ = ["emit_benes_template", "predicted_template_crossings"]
