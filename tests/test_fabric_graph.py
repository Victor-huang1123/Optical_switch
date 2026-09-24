from __future__ import annotations

import pytest

from mrr_switch_optimizer.analysis.fabric_coverage import verify_permutation_coverage
from mrr_switch_optimizer.core.fabric import build_fabric_graph
from mrr_switch_optimizer.core.state_assignment import (
    BenesLoopingStrategy,
    WaksmanStrategy,
)
from mrr_switch_optimizer.core.topology import (
    PaddedBenesTopology,
    WaksmanTopology,
)


@pytest.mark.parametrize(
    ("n_logical", "n_physical", "n_stages", "edge_count"),
    [
        (4, 4, 3, 16),
        (6, 8, 5, 48),
        (8, 8, 5, 48),
    ],
)
def test_padded_benes_fabric_has_one_edge_per_wire_boundary(
    n_logical: int,
    n_physical: int,
    n_stages: int,
    edge_count: int,
) -> None:
    topology = PaddedBenesTopology(
        n_logical,
        strategy=BenesLoopingStrategy(),
        verify_rnb=False,
    )

    graph = build_fabric_graph(topology)

    assert graph.n_logical == n_logical
    assert graph.n_physical == n_physical
    assert len(graph.edges) == edge_count
    assert sum(edge.kind == "input" for edge in graph.edges) == n_physical
    assert sum(edge.kind == "interstage" for edge in graph.edges) == (
        n_stages - 1
    ) * n_physical
    assert sum(edge.kind == "output" for edge in graph.edges) == n_physical
    assert len(graph.waveguides) == n_physical
    assert all(len(waveguide.edge_ids) == n_stages + 1 for waveguide in graph.waveguides)


def test_logical_6x6_blocks_only_padded_boundary_terminals() -> None:
    topology = PaddedBenesTopology(
        6,
        strategy=BenesLoopingStrategy(),
        verify_rnb=False,
    )

    graph = build_fabric_graph(topology)

    assert graph.blocked_ports == (6, 7)
    assert [waveguide.input_wire for waveguide in graph.waveguides if waveguide.blocked_boundary] == [6, 7]
    for edge in graph.edges:
        for endpoint in (edge.source, edge.target):
            assert endpoint.blocked == (
                endpoint.kind in {"input", "output"} and endpoint.wire in {6, 7}
            )


def test_fabric_graph_is_deterministic() -> None:
    topology = PaddedBenesTopology(
        8,
        strategy=BenesLoopingStrategy(),
        verify_rnb=False,
    )

    first = build_fabric_graph(topology)
    second = build_fabric_graph(topology)

    assert first == second
    assert first.edge_ids == second.edge_ids


def test_mrr_internal_transitions_are_not_fabric_edges() -> None:
    topology = PaddedBenesTopology(4)

    graph = build_fabric_graph(topology)

    assert all(
        not (
            edge.source.kind == "mrr"
            and edge.target.kind == "mrr"
            and edge.source.ref == edge.target.ref
        )
        for edge in graph.edges
    )


@pytest.mark.parametrize(
    ("n_logical", "expected_edges", "expected_mrrs"),
    [(4, 14, 5), (6, 28, 11), (8, 42, 17)],
)
def test_waksman_fabric_collapses_pass_through_stages_into_edges(
    n_logical: int,
    expected_edges: int,
    expected_mrrs: int,
) -> None:
    topology = WaksmanTopology(
        n_logical,
        strategy=WaksmanStrategy(),
        verify_rnb=False,
    )

    graph = build_fabric_graph(topology)

    assert topology.n_MRR == expected_mrrs
    assert len(graph.edges) == expected_edges
    assert len(graph.waveguides) == n_logical
    assert set(graph.edge_ids) == {
        edge_id for waveguide in graph.waveguides for edge_id in waveguide.edge_ids
    }


@pytest.mark.parametrize(
    ("n_logical", "expected_permutations"),
    [(4, 24), (6, 720), (8, 40320)],
)
def test_all_padded_benes_permutations_use_one_fixed_fabric(
    n_logical: int,
    expected_permutations: int,
) -> None:
    topology = PaddedBenesTopology(
        n_logical,
        strategy=BenesLoopingStrategy(),
        verify_rnb=False,
    )
    graph = build_fabric_graph(topology)

    report = verify_permutation_coverage(topology, graph)

    assert report.passed
    assert report.permutations_checked == expected_permutations
    assert report.fabric_edge_count == len(graph.edges)


@pytest.mark.parametrize(
    ("n_logical", "expected_permutations"),
    [(4, 24), (6, 720), (8, 40320)],
)
def test_all_waksman_permutations_use_one_fixed_fabric(
    n_logical: int,
    expected_permutations: int,
) -> None:
    topology = WaksmanTopology(
        n_logical,
        strategy=WaksmanStrategy(),
        verify_rnb=False,
    )
    graph = build_fabric_graph(topology)

    report = verify_permutation_coverage(topology, graph)

    assert report.passed
    assert report.permutations_checked == expected_permutations
    assert report.fabric_edge_count == len(graph.edges)
