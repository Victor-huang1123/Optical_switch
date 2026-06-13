from __future__ import annotations

from dataclasses import dataclass
from math import pi

from .layout import build_cells
from .models import MRRCell
from .topology import Path, RNBTopology


@dataclass(frozen=True)
class AnalyticEdge:
    topology: str
    permutation: tuple[int, ...]
    input_port: int
    src_mrr: str
    src_port: str
    dst_mrr: str
    dst_port: str
    dx_um: float
    dy_um: float
    manhattan_um: float
    delta_phi: float
    bend_estimate: float
    crossing_estimate: float
    alignment_penalty: float
    local_density: float
    blockage_ratio: float
    analytic_cost: float


def analytic_edge_costs(
    topology: RNBTopology,
    permutations: list[tuple[int, ...]],
    s_table: dict[tuple[str, str, int], float],
    *,
    centers: dict[str, tuple[float, float]] | None = None,
    alpha: float = 0.002,
    beta: float = 0.02,
    gamma: float = 0.02,
    eta: float = 0.01,
    mu: float = 0.0,
) -> list[AnalyticEdge]:
    """Compute §8 analytic-prior edge costs without physical routing."""
    cells = build_cells(topology, s_table, centers=centers)
    edges: list[AnalyticEdge] = []
    for permutation in permutations:
        states = topology.get_state_assignment(permutation)
        paths = topology.get_active_paths(permutation, states)
        path_segments = _path_segments(paths, cells)
        for path in paths:
            for src, dst in zip(path.steps, path.steps[1:]):
                src_cell = cells[src.mrr_id]
                dst_cell = cells[dst.mrr_id]
                src_xy = src_cell.port_xy(src.out_port)
                dst_xy = dst_cell.port_xy(dst.in_port)
                dx_um = dst_xy[0] - src_xy[0]
                dy_um = dst_xy[1] - src_xy[1]
                manhattan_um = abs(dx_um) + abs(dy_um)
                delta_phi = _delta_phi(
                    src_cell.ports[src.out_port].phi,
                    dst_cell.ports[dst.in_port].phi,
                )
                bend_estimate = 1.0 if abs(dx_um) > 0.0 and abs(dy_um) > 0.0 else 0.0
                crossing_estimate = _segment_crossing_count(
                    (src_xy, dst_xy),
                    path.input_port,
                    path_segments,
                )
                alignment_penalty = delta_phi / pi
                local_density = _local_density(src_cell, cells) + _local_density(dst_cell, cells)
                blockage_ratio = 0.0
                analytic_cost = (
                    alpha * manhattan_um
                    + beta * bend_estimate
                    + gamma * crossing_estimate
                    + eta * alignment_penalty
                    + mu * blockage_ratio
                )
                edges.append(
                    AnalyticEdge(
                        topology=topology.name,
                        permutation=permutation,
                        input_port=path.input_port,
                        src_mrr=src.mrr_id,
                        src_port=src.out_port,
                        dst_mrr=dst.mrr_id,
                        dst_port=dst.in_port,
                        dx_um=dx_um,
                        dy_um=dy_um,
                        manhattan_um=manhattan_um,
                        delta_phi=delta_phi,
                        bend_estimate=bend_estimate,
                        crossing_estimate=crossing_estimate,
                        alignment_penalty=alignment_penalty,
                        local_density=local_density,
                        blockage_ratio=blockage_ratio,
                        analytic_cost=analytic_cost,
                    )
                )
    return edges


def analytic_layout_cost(
    topology: RNBTopology,
    permutations: list[tuple[int, ...]],
    s_table: dict[tuple[str, str, int], float],
    *,
    centers: dict[str, tuple[float, float]] | None = None,
) -> float:
    return sum(
        edge.analytic_cost
        for edge in analytic_edge_costs(topology, permutations, s_table, centers=centers)
    )


def _path_segments(
    paths: list[Path],
    cells: dict[str, MRRCell],
) -> list[tuple[int, tuple[tuple[float, float], tuple[float, float]]]]:
    segments = []
    for path in paths:
        for src, dst in zip(path.steps, path.steps[1:]):
            segments.append(
                (
                    path.input_port,
                    (cells[src.mrr_id].port_xy(src.out_port), cells[dst.mrr_id].port_xy(dst.in_port)),
                )
            )
    return segments


def _segment_crossing_count(
    segment: tuple[tuple[float, float], tuple[float, float]],
    input_port: int,
    segments: list[tuple[int, tuple[tuple[float, float], tuple[float, float]]]],
) -> float:
    count = 0.0
    for other_input, other_segment in segments:
        if other_input == input_port:
            continue
        if _segments_cross(segment, other_segment):
            count += 1.0
    return count


def _segments_cross(
    segment_a: tuple[tuple[float, float], tuple[float, float]],
    segment_b: tuple[tuple[float, float], tuple[float, float]],
) -> bool:
    (x1, y1), (x2, y2) = segment_a
    (x3, y3), (x4, y4) = segment_b
    if max(x1, x2) <= min(x3, x4) or max(x3, x4) <= min(x1, x2):
        return False

    def orient(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> float:
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)

    o1 = orient(x1, y1, x2, y2, x3, y3)
    o2 = orient(x1, y1, x2, y2, x4, y4)
    o3 = orient(x3, y3, x4, y4, x1, y1)
    o4 = orient(x3, y3, x4, y4, x2, y2)
    return o1 * o2 < 0.0 and o3 * o4 < 0.0


def _delta_phi(phi_a: float, phi_b: float) -> float:
    delta = abs(phi_a - phi_b) % (2.0 * pi)
    return min(delta, 2.0 * pi - delta)


def _local_density(cell: MRRCell, cells: dict[str, MRRCell], radius_um: float = 120.0) -> float:
    density = 0.0
    for other in cells.values():
        if other.id == cell.id:
            continue
        distance = abs(other.center[0] - cell.center[0]) + abs(other.center[1] - cell.center[1])
        if distance < radius_um:
            density += 1.0 - distance / radius_um
    return density
