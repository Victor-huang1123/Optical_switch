from __future__ import annotations

from dataclasses import dataclass, replace
from math import pi
import re
from typing import Literal

from ..core.fabric import FabricEndpoint, FabricGraph, FabricWaveguide
from ..core.models import MRRCell, port_for_wire, state_for_transition
from ..core.topology import Path, RNBTopology, RouteStep
from ..placement.layout import wire_y
from .drc import _validate_rules, validate_physical_routes
from .geometry import _bend_count, _polyline_length, _same_point
from .physical import route_physical_design
from .port_access import build_port_access_plan
from .types import (
    DRCViolation,
    PhysicalRoute,
    Point,
    RoutingRules,
    RoutingStats,
    Segment,
    StraightenStats,
)


@dataclass(frozen=True)
class FabricEdgeRoute:
    edge_id: str
    waypoints: tuple[Point, ...]
    length_um: float
    bend_count: int


@dataclass(frozen=True)
class FabricRoute:
    edge_id: str
    covered_edge_ids: tuple[str, ...]
    waypoints: tuple[Point, ...]
    length_um: float
    bend_count: int
    external_segments: tuple[Segment, ...]
    local_segments: tuple[Segment, ...]
    edge_routes: tuple[FabricEdgeRoute, ...] = ()


@dataclass(frozen=True)
class FailedFabricEdge:
    edge_id: str
    message: str


@dataclass(frozen=True)
class FabricDRCViolation:
    rule: str
    edge_ids: tuple[str, ...]
    message: str
    location: Point | None = None


@dataclass(frozen=True)
class FabricCrossing:
    edge_a: str
    edge_b: str
    location: Point


@dataclass(frozen=True)
class FixedFabricRoutingResult:
    graph: FabricGraph
    routes: tuple[FabricRoute, ...]
    failed_edges: tuple[FailedFabricEdge, ...]
    drc_violations: tuple[FabricDRCViolation, ...]
    crossings: tuple[FabricCrossing, ...]
    rules: RoutingRules
    stats: RoutingStats = RoutingStats()
    straighten_stats: StraightenStats | None = None

    @property
    def routed_edge_ids(self) -> tuple[str, ...]:
        return tuple(
            edge_id for route in self.routes for edge_id in route.covered_edge_ids
        )

    @property
    def failed_edge_ids(self) -> tuple[str, ...]:
        return tuple(failure.edge_id for failure in self.failed_edges)


def route_fixed_fabric(
    topology: RNBTopology,
    graph: FabricGraph,
    cells: dict[str, MRRCell],
    rules: RoutingRules = RoutingRules(),
    *,
    port_stub_um: float = 2.0,
    x_start: float = 20.0,
    x_end: float | None = None,
    wire_pitch_um: float = 36.0,
    layout_mode: Literal["auto", "astar", "template"] = "auto",
    straighten_jogs: bool = False,
    same_net_whole_net_reroute: bool = False,
    remediation_events_out: list[tuple[int, int]] | None = None,
    remediation_attempts_out: list[dict[str, object]] | None = None,
) -> FixedFabricRoutingResult:
    """Route every immutable fabric waveguide once, independent of permutation."""
    _validate_rules(rules)
    if port_stub_um <= 0.0:
        raise ValueError("port_stub_um must be positive")
    if x_end is None:
        x_end = max(cell.center[0] for cell in cells.values()) + 70.0
    if topology.name != graph.topology_name:
        raise ValueError(
            f"fabric {graph.topology_name!r} does not match topology {topology.name!r}"
        )
    _validate_graph_cells(graph, cells)
    if layout_mode not in {"auto", "astar", "template"}:
        raise ValueError("layout_mode must be 'auto', 'astar', or 'template'")

    paths = _fabric_waveguide_paths(topology, graph)
    template_eligible = _benes_template_eligible(topology, cells, wire_pitch_um)
    if layout_mode == "template" and not template_eligible:
        raise ValueError(f"{topology.name} is not eligible for the canonical template")
    template_selected = layout_mode == "template" or (
        layout_mode == "auto" and template_eligible
    )
    if template_selected:
        from .benes_template_layout import emit_benes_template

        physical = emit_benes_template(
            topology,
            paths,
            cells,
            rules,
            x_start=x_start,
            x_end=x_end,
            wire_pitch_um=wire_pitch_um,
        )
    else:
        preferred_waveguides = _preferred_waveguide_order(graph, rules)
        physical = route_physical_design(
            paths,
            cells,
            rules,
            port_stub_um=port_stub_um,
            x_start=x_start,
            x_end=x_end,
            wire_pitch_um=wire_pitch_um,
            preferred_input_order=(
                None
                if preferred_waveguides is None
                else tuple(
                    waveguide.input_wire for waveguide in preferred_waveguides
                )
            ),
            same_net_whole_net_reroute=same_net_whole_net_reroute,
            remediation_events_out=remediation_events_out,
            remediation_attempts_out=remediation_attempts_out,
        )
    waveguide_by_input = {
        waveguide.input_wire: waveguide for waveguide in graph.waveguides
    }
    routes = tuple(
        _fabric_route_from_physical(
            route,
            waveguide_by_input[route.input_port],
            graph,
            cells,
            rules,
            x_start=x_start,
            x_end=x_end,
            wire_pitch_um=wire_pitch_um,
        )
        for route in physical.routes
    )
    failed = tuple(
        FailedFabricEdge(edge_id, failure.message)
        for failure in physical.failed_nets
        for edge_id in waveguide_by_input[failure.input_port].edge_ids
    )
    owner_by_input = {
        input_wire: waveguide.owner_edge_id
        for input_wire, waveguide in waveguide_by_input.items()
    }
    fixed_result = FixedFabricRoutingResult(
        graph=graph,
        routes=routes,
        failed_edges=failed,
        drc_violations=_map_physical_drc(physical.drc_violations, owner_by_input),
        crossings=tuple(
            FabricCrossing(
                owner_by_input[crossing.net_a],
                owner_by_input[crossing.net_b],
                crossing.location,
            )
            for crossing in physical.crossings
        ),
        rules=rules,
        stats=physical.stats,
    )
    if not straighten_jogs or fixed_result.failed_edges:
        return fixed_result

    from .straighten import (
        physical_routes_from_fixed,
        straighten_fixed_fabric,
    )

    seed = replace(
        fixed_result,
        drc_violations=(),
    )
    straightened = straighten_fixed_fabric(
        seed,
        cells,
        port_access_plan=build_port_access_plan(
            paths,
            cells,
            rules,
            port_stub_um,
        ),
        max_rounds=10,
        preserve_template=template_selected,
    ).routing
    final_physical = physical_routes_from_fixed(straightened)
    final_drc = validate_physical_routes(final_physical, cells, rules)
    return replace(
        straightened,
        rules=rules,
        drc_violations=_map_physical_drc(final_drc, owner_by_input),
    )


def _fabric_waveguide_paths(
    topology: RNBTopology,
    graph: FabricGraph,
) -> list[Path]:
    paths: list[Path] = []
    for waveguide in graph.waveguides:
        wire = waveguide.input_wire
        steps: list[RouteStep] = []
        for stage_idx, pairs in enumerate(topology.stage_pairs):
            pair = next((pair for pair in pairs if wire in pair), None)
            if pair is not None:
                mrr_id = topology.mrr_id(stage_idx, pair)
                steps.append(
                    RouteStep(
                        mrr_id=mrr_id,
                        stage=stage_idx,
                        pair=pair,
                        wire_in=wire,
                        wire_out=wire,
                        in_port=port_for_wire(pair, wire, "input"),
                        out_port=port_for_wire(pair, wire, "output"),
                        state=state_for_transition(pair, wire, wire),
                    )
                )
            wire = topology._apply_fixed_permutation(stage_idx, wire)
        paths.append(
            Path(
                input_port=waveguide.input_wire,
                output_port=waveguide.output_wire,
                steps=tuple(steps),
            )
        )
    return paths


def _fabric_route_from_physical(
    route: PhysicalRoute,
    waveguide: FabricWaveguide,
    graph: FabricGraph,
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    *,
    x_start: float,
    x_end: float,
    wire_pitch_um: float,
) -> FabricRoute:
    return FabricRoute(
        edge_id=waveguide.owner_edge_id,
        covered_edge_ids=waveguide.edge_ids,
        waypoints=route.waypoints,
        length_um=route.length_um,
        bend_count=route.bend_count,
        external_segments=route.external_segments,
        local_segments=route.local_segments,
        edge_routes=_split_edge_routes(
            route.waypoints,
            waveguide,
            graph,
            cells,
            rules,
            x_start=x_start,
            x_end=x_end,
            wire_pitch_um=wire_pitch_um,
        ),
    )


def _split_edge_routes(
    waypoints: tuple[Point, ...],
    waveguide: FabricWaveguide,
    graph: FabricGraph,
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    *,
    x_start: float,
    x_end: float,
    wire_pitch_um: float,
) -> tuple[FabricEdgeRoute, ...]:
    edge_by_id = {edge.edge_id: edge for edge in graph.edges}
    edge_routes: list[FabricEdgeRoute] = []
    cursor = 0
    bend_arc_um = 0.5 * pi * rules.bend_radius_um
    for edge_id in waveguide.edge_ids:
        edge = edge_by_id[edge_id]
        source = _endpoint_point(
            edge.source,
            cells,
            graph.n_physical,
            x_start=x_start,
            x_end=x_end,
            wire_pitch_um=wire_pitch_um,
        )
        target = _endpoint_point(
            edge.target,
            cells,
            graph.n_physical,
            x_start=x_start,
            x_end=x_end,
            wire_pitch_um=wire_pitch_um,
        )
        source_idx = _find_waypoint(waypoints, source, cursor)
        target_idx = _find_waypoint(waypoints, target, source_idx)
        edge_waypoints = waypoints[source_idx : target_idx + 1]
        bends = _bend_count(edge_waypoints)
        edge_routes.append(
            FabricEdgeRoute(
                edge_id=edge_id,
                waypoints=edge_waypoints,
                length_um=(
                    _polyline_length(edge_waypoints) + bends * bend_arc_um
                ),
                bend_count=bends,
            )
        )
        cursor = target_idx
    return tuple(edge_routes)


def _endpoint_point(
    endpoint: FabricEndpoint,
    cells: dict[str, MRRCell],
    n_physical: int,
    *,
    x_start: float,
    x_end: float,
    wire_pitch_um: float,
) -> Point:
    if endpoint.kind == "input":
        return (x_start, wire_y(endpoint.wire, n_physical, wire_pitch_um))
    if endpoint.kind == "output":
        return (x_end, wire_y(endpoint.wire, n_physical, wire_pitch_um))
    if endpoint.port is None:
        raise ValueError(f"MRR endpoint {endpoint.endpoint_id} has no port")
    return cells[endpoint.ref].port_xy(endpoint.port)


def _find_waypoint(
    waypoints: tuple[Point, ...],
    target: Point,
    start_idx: int,
) -> int:
    for idx in range(start_idx, len(waypoints)):
        if _same_point(waypoints[idx], target):
            return idx
    raise ValueError(f"fixed route does not contain endpoint waypoint {target}")


def _preferred_waveguide_order(
    graph: FabricGraph,
    rules: RoutingRules | None = None,
) -> tuple[FabricWaveguide, ...] | None:
    if len({len(waveguide.edge_ids) for waveguide in graph.waveguides}) > 1:
        if rules is not None and rules.cost_model == "db":
            return tuple(
                sorted(
                    graph.waveguides,
                    key=lambda item: (-len(item.edge_ids), item.input_wire),
                )
            )
        return None
    constrained = {
        waveguide.input_wire
        for waveguide in graph.waveguides
        if waveguide.blocked_boundary
    }
    if not constrained and graph.n_physical >= 8:
        constrained = {graph.n_physical - 2, graph.n_physical - 1}
    reverse_unconstrained = (
        rules is not None
        and rules.cost_model == "db"
        and graph.topology_name.startswith("padded_benes_")
    )
    return tuple(
        sorted(
            graph.waveguides,
            key=lambda item: (
                item.input_wire not in constrained,
                (
                    -item.input_wire
                    if reverse_unconstrained and item.input_wire not in constrained
                    else item.input_wire
                ),
            ),
        )
    )


def _benes_template_eligible(
    topology: RNBTopology,
    cells: dict[str, MRRCell],
    wire_pitch_um: float,
) -> bool:
    if not topology.name.startswith("padded_benes_"):
        return False
    expected_pitch = {4: 136.0, 8: 168.0, 16: 232.0}.get(topology.N_physical)
    if expected_pitch is None or abs(wire_pitch_um - 64.0) > 1e-6:
        return False
    stage_x: dict[int, float] = {}
    for mrr_id, cell in cells.items():
        stage_x.setdefault(topology.get_mrr_stage(mrr_id), cell.center[0])
    return all(
        abs(stage_x[stage + 1] - stage_x[stage] - expected_pitch) <= 1e-6
        for stage in range(topology.n_stages - 1)
    )


def _map_physical_drc(
    violations: tuple[DRCViolation, ...],
    owner_by_input: dict[int, str],
) -> tuple[FabricDRCViolation, ...]:
    return tuple(
        FabricDRCViolation(
            rule=violation.rule,
            edge_ids=tuple(
                owner_by_input[int(match)]
                for match in re.findall(r"I(\d+)", violation.net_id)
            ),
            message=violation.message,
            location=violation.location,
        )
        for violation in violations
    )


def _validate_graph_cells(graph: FabricGraph, cells: dict[str, MRRCell]) -> None:
    missing = sorted(
        {
            endpoint.ref
            for edge in graph.edges
            for endpoint in (edge.source, edge.target)
            if endpoint.kind == "mrr" and endpoint.ref not in cells
        }
    )
    if missing:
        raise ValueError(f"fabric references missing MRR cells: {missing}")


__all__ = [
    "FabricCrossing",
    "FabricDRCViolation",
    "FabricEdgeRoute",
    "FabricRoute",
    "FailedFabricEdge",
    "FixedFabricRoutingResult",
    "route_fixed_fabric",
]
