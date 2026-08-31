from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations

from ..core.fabric import FabricGraph
from ..core.state_assignment import verify_state_assignment
from ..core.topology import Permutation, RNBTopology


@dataclass(frozen=True)
class PermutationCoverageReport:
    topology_name: str
    fabric_edge_count: int
    permutations_checked: int
    assignment_failures: tuple[Permutation, ...]
    fabric_path_failures: tuple[Permutation, ...]
    blocked_ports: tuple[int, ...]

    @property
    def passed(self) -> bool:
        return not self.assignment_failures and not self.fabric_path_failures


def verify_permutation_coverage(
    topology: RNBTopology,
    graph: FabricGraph,
) -> PermutationCoverageReport:
    if graph.topology_name != topology.name:
        raise ValueError(
            f"fabric {graph.topology_name!r} does not match topology {topology.name!r}"
        )
    fabric_links = {
        (edge.source.endpoint_id, edge.target.endpoint_id) for edge in graph.edges
    }
    assignment_failures: list[Permutation] = []
    path_failures: list[Permutation] = []
    checked = 0

    for permutation in permutations(range(topology.N_logical)):
        checked += 1
        states = topology.get_state_assignment(permutation)
        if not verify_state_assignment(topology, permutation, states):
            assignment_failures.append(permutation)
            continue
        if not _paths_use_fabric(topology, permutation, states, fabric_links):
            path_failures.append(permutation)

    return PermutationCoverageReport(
        topology_name=topology.name,
        fabric_edge_count=len(graph.edges),
        permutations_checked=checked,
        assignment_failures=tuple(assignment_failures),
        fabric_path_failures=tuple(path_failures),
        blocked_ports=topology.blocked_ports,
    )


def _paths_use_fabric(
    topology: RNBTopology,
    permutation: Permutation,
    states: dict[str, int],
    fabric_links: set[tuple[str, str]],
) -> bool:
    for path in topology.get_active_paths(permutation, states):
        if not path.steps:
            return False
        first = path.steps[0]
        if (f"I{path.input_port}", f"{first.mrr_id}.{first.in_port}") not in fabric_links:
            return False
        for current, following in zip(path.steps, path.steps[1:]):
            link = (
                f"{current.mrr_id}.{current.out_port}",
                f"{following.mrr_id}.{following.in_port}",
            )
            if link not in fabric_links:
                return False
        last = path.steps[-1]
        if (f"{last.mrr_id}.{last.out_port}", f"O{path.output_port}") not in fabric_links:
            return False
    return True


__all__ = ["PermutationCoverageReport", "verify_permutation_coverage"]
