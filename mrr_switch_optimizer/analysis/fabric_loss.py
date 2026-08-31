from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from math import log10, pi

from ..core.models import MRRCell
from ..core.topology import Path, Permutation, RNBTopology
from ..routing.fabric import FabricEdgeRoute, FixedFabricRoutingResult
from ..routing.geometry import _bend_count, _polyline_length
from ..routing.port_access import _mrr_internal_points
from ..routing.types import EPS, Point


@dataclass(frozen=True)
class FabricWorstILReport:
    topology_name: str
    n_logical: int
    n_physical: int
    mrr_count: int
    fabric_edge_count: int
    permutations_checked: int
    paths_checked: int
    worst_insertion_loss_db: float
    worst_permutation: Permutation
    worst_input_port: int
    worst_output_port: int
    worst_path_length_um: float
    worst_bend_count: int
    worst_crossing_count: int
    worst_mrr_loss_db: float
    worst_propagation_loss_db: float
    worst_bend_loss_db: float
    worst_crossing_loss_db: float
    prop_loss_db_per_um: float
    bend_loss_db_per_bend: float
    crossing_loss_db_per_cross: float


@dataclass(frozen=True)
class _PathLoss:
    insertion_loss_db: float
    path_length_um: float
    bend_count: int
    crossing_count: int
    mrr_loss_db: float
    propagation_loss_db: float
    bend_loss_db: float
    crossing_loss_db: float


def evaluate_fixed_fabric_worst_insertion_loss(
    topology: RNBTopology,
    cells: dict[str, MRRCell],
    result: FixedFabricRoutingResult,
) -> FabricWorstILReport:
    """Evaluate every logical path on one already-routed physical fabric."""
    if result.graph.topology_name != topology.name:
        raise ValueError(
            f"fabric {result.graph.topology_name!r} does not match "
            f"topology {topology.name!r}"
        )
    if result.failed_edges:
        raise ValueError("cannot evaluate insertion loss with failed fabric edges")
    if result.drc_violations:
        raise ValueError("cannot evaluate insertion loss with fabric DRC violations")

    edge_route_by_id = {
        edge_route.edge_id: edge_route
        for route in result.routes
        for edge_route in route.edge_routes
    }
    if set(edge_route_by_id) != set(result.graph.edge_ids):
        raise ValueError("fixed fabric result has incomplete edge-level geometry")
    edge_id_by_link = {
        (edge.source.endpoint_id, edge.target.endpoint_id): edge.edge_id
        for edge in result.graph.edges
    }
    crossing_indices_by_edge = _crossing_indices_by_edge(
        edge_route_by_id,
        tuple(crossing.location for crossing in result.crossings),
    )

    worst: tuple[Permutation, Path, _PathLoss] | None = None
    permutations_checked = 0
    paths_checked = 0
    for permutation in permutations(range(topology.N_logical)):
        permutations_checked += 1
        states = topology.get_state_assignment(permutation)
        for path in topology.get_active_paths(permutation, states):
            paths_checked += 1
            loss = _evaluate_path_loss(
                path,
                cells,
                result,
                edge_id_by_link,
                edge_route_by_id,
                crossing_indices_by_edge,
            )
            if worst is None or loss.insertion_loss_db > worst[2].insertion_loss_db:
                worst = (permutation, path, loss)

    if worst is None:
        raise ValueError(f"{topology.name} produced no logical paths")
    worst_permutation, worst_path, worst_loss = worst
    return FabricWorstILReport(
        topology_name=topology.name,
        n_logical=topology.N_logical,
        n_physical=topology.N_physical,
        mrr_count=topology.n_MRR,
        fabric_edge_count=len(result.graph.edges),
        permutations_checked=permutations_checked,
        paths_checked=paths_checked,
        worst_insertion_loss_db=worst_loss.insertion_loss_db,
        worst_permutation=worst_permutation,
        worst_input_port=worst_path.input_port,
        worst_output_port=worst_path.output_port,
        worst_path_length_um=worst_loss.path_length_um,
        worst_bend_count=worst_loss.bend_count,
        worst_crossing_count=worst_loss.crossing_count,
        worst_mrr_loss_db=worst_loss.mrr_loss_db,
        worst_propagation_loss_db=worst_loss.propagation_loss_db,
        worst_bend_loss_db=worst_loss.bend_loss_db,
        worst_crossing_loss_db=worst_loss.crossing_loss_db,
        prop_loss_db_per_um=result.rules.prop_loss_db_per_um,
        bend_loss_db_per_bend=result.rules.bend_loss_db_per_bend,
        crossing_loss_db_per_cross=result.rules.crossing_loss_db_per_cross,
    )


def _evaluate_path_loss(
    path: Path,
    cells: dict[str, MRRCell],
    result: FixedFabricRoutingResult,
    edge_id_by_link: dict[tuple[str, str], str],
    edge_route_by_id: dict[str, FabricEdgeRoute],
    crossing_indices_by_edge: dict[str, frozenset[int]],
) -> _PathLoss:
    edge_ids = _active_edge_ids(path, edge_id_by_link)
    edge_routes = tuple(edge_route_by_id[edge_id] for edge_id in edge_ids)
    bend_arc_um = 0.5 * pi * result.rules.bend_radius_um
    path_length_um = sum(edge_route.length_um for edge_route in edge_routes)
    bend_count = sum(edge_route.bend_count for edge_route in edge_routes)
    mrr_power = 1.0
    for step in path.steps:
        cell = cells[step.mrr_id]
        internal_points = _mrr_internal_points(cell, step.in_port, step.out_port)
        internal_bends = _bend_count(internal_points)
        path_length_um += (
            _polyline_length(internal_points) + internal_bends * bend_arc_um
        )
        bend_count += internal_bends
        mrr_power *= cell.s_table[(step.in_port, step.out_port, step.state)]

    active_crossings: set[int] = set()
    for edge_id in edge_ids:
        active_crossings.update(crossing_indices_by_edge[edge_id])
    crossing_count = len(active_crossings)
    mrr_loss_db = -10.0 * log10(max(mrr_power, 1e-15))
    propagation_loss_db = result.rules.prop_loss_db_per_um * path_length_um
    bend_loss_db = result.rules.bend_loss_db_per_bend * bend_count
    crossing_loss_db = (
        result.rules.crossing_loss_db_per_cross * crossing_count
    )
    return _PathLoss(
        insertion_loss_db=(
            mrr_loss_db
            + propagation_loss_db
            + bend_loss_db
            + crossing_loss_db
        ),
        path_length_um=path_length_um,
        bend_count=bend_count,
        crossing_count=crossing_count,
        mrr_loss_db=mrr_loss_db,
        propagation_loss_db=propagation_loss_db,
        bend_loss_db=bend_loss_db,
        crossing_loss_db=crossing_loss_db,
    )


def _active_edge_ids(
    path: Path,
    edge_id_by_link: dict[tuple[str, str], str],
) -> tuple[str, ...]:
    if not path.steps:
        raise ValueError(f"I{path.input_port}->O{path.output_port} has no MRR steps")
    links = [
        (f"I{path.input_port}", f"{path.steps[0].mrr_id}.{path.steps[0].in_port}")
    ]
    links.extend(
        (
            f"{current.mrr_id}.{current.out_port}",
            f"{following.mrr_id}.{following.in_port}",
        )
        for current, following in zip(path.steps, path.steps[1:])
    )
    links.append(
        (
            f"{path.steps[-1].mrr_id}.{path.steps[-1].out_port}",
            f"O{path.output_port}",
        )
    )
    try:
        return tuple(edge_id_by_link[link] for link in links)
    except KeyError as exc:
        raise ValueError(f"active path uses missing fixed fabric link {exc.args[0]}") from exc


def _crossing_indices_by_edge(
    edge_route_by_id: dict[str, FabricEdgeRoute],
    crossing_locations: tuple[Point, ...],
) -> dict[str, frozenset[int]]:
    return {
        edge_id: frozenset(
            idx
            for idx, location in enumerate(crossing_locations)
            if _point_on_polyline(location, edge_route.waypoints)
        )
        for edge_id, edge_route in edge_route_by_id.items()
    }


def _point_on_polyline(point: Point, waypoints: tuple[Point, ...]) -> bool:
    x, y = point
    for start, end in zip(waypoints, waypoints[1:]):
        if abs(start[0] - end[0]) < EPS:
            if abs(x - start[0]) < EPS and min(start[1], end[1]) - EPS <= y <= max(
                start[1], end[1]
            ) + EPS:
                return True
        elif abs(start[1] - end[1]) < EPS:
            if abs(y - start[1]) < EPS and min(start[0], end[0]) - EPS <= x <= max(
                start[0], end[0]
            ) + EPS:
                return True
    return False


__all__ = [
    "FabricWorstILReport",
    "evaluate_fixed_fabric_worst_insertion_loss",
]
