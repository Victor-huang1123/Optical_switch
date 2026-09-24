from __future__ import annotations

from itertools import permutations
from random import Random

import pytest

from mrr_switch_optimizer.core.state_assignment import (
    BenesLoopingStrategy,
    BruteForceLUTStrategy,
    SpankeBenesRectStrategy,
    SpankeBenesStrategy,
    StateAssignmentStrategy,
    WaksmanStrategy,
    realize_state_assignment,
    verify_state_assignment,
)
from mrr_switch_optimizer.core.topology import (
    PaddedBenesTopology,
    RNBTopology,
    SpankeBenesRectTopology,
    SpankeBenesTopology,
    WaksmanTopology,
)


@pytest.mark.parametrize(
    "topology_factory",
    [
        PaddedBenesTopology,
        WaksmanTopology,
        SpankeBenesTopology,
        SpankeBenesRectTopology,
    ],
)
def test_default_topology_state_assignments_verify_exhaustively(
    topology_factory: type[RNBTopology],
) -> None:
    topology = topology_factory()
    for permutation in permutations(range(topology.N_logical)):
        states = topology.get_state_assignment(permutation)
        assert verify_state_assignment(topology, permutation, states)


def test_bruteforce_strategy_matches_topology_lut_for_spot_checks() -> None:
    strategy = BruteForceLUTStrategy()
    topology = WaksmanTopology(strategy=strategy)
    for permutation in [
        (0, 1, 2, 3, 4, 5),
        (2, 0, 5, 1, 3, 4),
        (5, 4, 3, 2, 1, 0),
    ]:
        states = strategy.assign(topology, permutation)
        assert states == topology._lut[topology._physical_target(permutation)]
        assert states == topology.get_state_assignment(permutation)


def test_bruteforce_strategy_refuses_large_lut() -> None:
    with pytest.raises(NotImplementedError, match="constructive strategy required"):
        WaksmanTopology(16)


def test_bruteforce_lut_cache_is_topology_specific_when_strategy_is_reused() -> None:
    strategy = BruteForceLUTStrategy()
    permutation = (2, 0, 3, 1)
    padded = PaddedBenesTopology(4, strategy=strategy)
    waksman = WaksmanTopology(4, strategy=strategy)

    padded_states = padded.get_state_assignment(permutation)
    waksman_states = waksman.get_state_assignment(permutation)

    assert verify_state_assignment(padded, permutation, padded_states)
    assert verify_state_assignment(waksman, permutation, waksman_states)
    assert all(mrr_id.startswith(padded.name) for mrr_id in padded_states)
    assert all(mrr_id.startswith(waksman.name) for mrr_id in waksman_states)


@pytest.mark.parametrize(
    ("topology_factory", "strategy"),
    [
        (PaddedBenesTopology, BenesLoopingStrategy()),
        (WaksmanTopology, WaksmanStrategy()),
        (SpankeBenesTopology, SpankeBenesStrategy()),
        (SpankeBenesRectTopology, SpankeBenesRectStrategy()),
    ],
)
def test_constructive_strategies_verify_default_n6_exhaustively(
    topology_factory: type[RNBTopology],
    strategy: StateAssignmentStrategy,
) -> None:
    topology = topology_factory(strategy=strategy)
    for permutation in permutations(range(topology.N_logical)):
        states = topology.get_state_assignment(permutation)
        assert verify_state_assignment(topology, permutation, states)


@pytest.mark.parametrize(
    ("topology_factory", "strategy", "n_logical"),
    [
        (PaddedBenesTopology, BenesLoopingStrategy(), 8),
        (PaddedBenesTopology, BenesLoopingStrategy(), 16),
        (WaksmanTopology, WaksmanStrategy(), 8),
        (WaksmanTopology, WaksmanStrategy(), 16),
        (SpankeBenesTopology, SpankeBenesStrategy(), 8),
        (SpankeBenesTopology, SpankeBenesStrategy(), 16),
        (SpankeBenesRectTopology, SpankeBenesRectStrategy(), 8),
        (SpankeBenesRectTopology, SpankeBenesRectStrategy(), 12),
        (SpankeBenesRectTopology, SpankeBenesRectStrategy(), 16),
    ],
)
def test_constructive_strategies_verify_sampled_large_n(
    topology_factory: type[RNBTopology],
    strategy: StateAssignmentStrategy,
    n_logical: int,
) -> None:
    topology = topology_factory(n_logical, strategy=strategy)
    for permutation in _sample_permutations(n_logical, 1000, seed=123):
        states = topology.get_state_assignment(permutation)
        assert verify_state_assignment(topology, permutation, states)


@pytest.mark.parametrize("n_logical", [2, 3, 4, 5, 6, 7])
def test_spanke_benes_rect_constructive_exhaustive_small_n(n_logical: int) -> None:
    topology = SpankeBenesRectTopology(n_logical, strategy=SpankeBenesRectStrategy())
    for permutation in permutations(range(n_logical)):
        states = topology.get_state_assignment(permutation)
        assert verify_state_assignment(topology, permutation, states)
        for path in topology.get_active_paths(permutation, states):
            achieved = path.steps[-1].wire_out if path.steps else path.input_port
            assert achieved == path.output_port


@pytest.mark.parametrize("n_logical", [2, 3, 4, 5, 6])
def test_spanke_benes_rect_constructive_matches_bruteforce_oracle(n_logical: int) -> None:
    oracle = SpankeBenesRectTopology(n_logical)
    constructive = SpankeBenesRectTopology(n_logical, strategy=SpankeBenesRectStrategy())
    for permutation in permutations(range(n_logical)):
        target = oracle._physical_target(permutation)
        oracle_states = oracle.get_state_assignment(permutation)
        constructive_states = constructive.get_state_assignment(permutation)
        assert realize_state_assignment(oracle, oracle_states) == target
        assert realize_state_assignment(constructive, constructive_states) == target


def _sample_permutations(
    n_logical: int,
    count: int,
    seed: int,
) -> list[tuple[int, ...]]:
    rng = Random(seed)
    seen: set[tuple[int, ...]] = set()
    samples: list[tuple[int, ...]] = []
    while len(samples) < count:
        values = list(range(n_logical))
        rng.shuffle(values)
        permutation = tuple(values)
        if permutation in seen:
            continue
        seen.add(permutation)
        samples.append(permutation)
    return samples
