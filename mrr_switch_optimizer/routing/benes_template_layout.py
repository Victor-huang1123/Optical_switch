from __future__ import annotations

from math import pi

from ..core.models import MRRCell
from ..core.topology import Path, RNBTopology, RouteStep
from ..placement.layout import wire_y
from .drc import validate_physical_routes
from .geometry import _axis_segments, _bend_count, _dedupe_points, _polyline_length
from .port_access import _mrr_internal_points
from .refinement import _route_crossings, _route_with_crossing_count
from .types import PhysicalRoute, PhysicalRoutingResult, Point, RoutingRules, RoutingStats, Segment
from .types import RouteCrossing


_PORT_RUN_X_UM = 24.0
_CHANNEL_ANCHOR_X_UM = 40.0
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
            order = _canonical_riser_order(connections)
            usable_left = left + 16.0
            usable_right = right - 16.0
            positions: tuple[float, ...]
            if len(order) == 1:
                positions = ((usable_left + usable_right) / 2.0,)
            else:
                step = (usable_right - usable_left) / (len(order) - 1)
                positions = tuple(usable_left + rank * step for rank in range(len(order)))
            riser_by_wire = dict(zip(order, positions))

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
