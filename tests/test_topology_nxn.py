from __future__ import annotations

from math import ceil, log2

import pytest

from mrr_switch_optimizer.core.topology import (
    PaddedBenesTopology,
    RNBTopology,
    SpankeBenesRectTopology,
    SpankeBenesTopology,
    StateAssignment,
    WaksmanTopology,
    build_benes_stage_pairs,
    build_spanke_benes_rect_stage_pairs,
    build_spanke_benes_stage_pairs,
    build_waksman_stage_pairs,
)


PADDED_BENES_8_LITERAL = (
    ((0, 1), (2, 3), (4, 5), (6, 7)),
    ((0, 2), (1, 3), (4, 6), (5, 7)),
    ((0, 4), (1, 5), (2, 6), (3, 7)),
    ((0, 2), (1, 3), (4, 6), (5, 7)),
    ((0, 1), (2, 3), (4, 5), (6, 7)),
)

SPANKE_BENES_6_LITERAL = (
    ((0, 1),),
    ((1, 2),),
    ((0, 1), (2, 3)),
    ((1, 2), (3, 4)),
    ((0, 1), (2, 3), (4, 5)),
    ((1, 2), (3, 4)),
    ((0, 1), (2, 3)),
    ((1, 2),),
    ((0, 1),),
)

SPANKE_BENES_RECT_6_LITERAL = (
    ((0, 1), (2, 3), (4, 5)),
    ((1, 2), (3, 4)),
    ((0, 1), (2, 3), (4, 5)),
    ((1, 2), (3, 4)),
    ((0, 1), (2, 3), (4, 5)),
    ((1, 2), (3, 4)),
)


def test_default_stage_pairs_and_names_match_literals() -> None:
    padded = PaddedBenesTopology()
    spanke = SpankeBenesTopology()
    waksman = WaksmanTopology()

    assert padded.name == "padded_benes_8x8"
    assert padded.N_logical == 6
    assert padded.N_physical == 8
    assert padded.n_MRR == 20
    assert padded.n_stages == 5
    assert padded.stage_pairs == PADDED_BENES_8_LITERAL

    assert spanke.name == "spanke_benes_6x6"
    assert spanke.N_logical == 6
    assert spanke.N_physical == 6
    assert spanke.n_MRR == 15
    assert spanke.n_stages == 9
    assert spanke.stage_pairs == SPANKE_BENES_6_LITERAL

    assert waksman.name == "waksman_6x6"
    assert waksman.N_logical == 6
    assert waksman.N_physical == 6
    assert waksman.n_MRR == 11
    assert waksman.n_stages == 5


def test_generators_match_default_literals() -> None:
    assert build_benes_stage_pairs(8) == PADDED_BENES_8_LITERAL
    assert build_spanke_benes_stage_pairs(6) == SPANKE_BENES_6_LITERAL


@pytest.mark.parametrize("n_logical", [2, 3, 4, 5, 6, 7, 8, 16])
def test_padded_benes_count_formulas(n_logical: int) -> None:
    n_physical = 1 << ceil(log2(n_logical))
    stages = build_benes_stage_pairs(n_physical)
    expected_stage_count = 2 * int(log2(n_physical)) - 1
    assert len(stages) == expected_stage_count
    assert sum(len(stage) for stage in stages) == (n_physical // 2) * expected_stage_count
    assert n_physical - n_logical >= 0


@pytest.mark.parametrize("n_logical", [2, 3, 4, 5, 6, 7, 8, 16])
def test_spanke_benes_count_formulas(n_logical: int) -> None:
    stages = build_spanke_benes_stage_pairs(n_logical)
    assert len(stages) == 2 * n_logical - 3
    assert sum(len(stage) for stage in stages) == n_logical * (n_logical - 1) // 2


def test_spanke_benes_rect_default_matches_literal() -> None:
    rect = SpankeBenesRectTopology()

    assert rect.name == "spanke_benes_rect_6x6"
    assert rect.N_logical == 6
    assert rect.N_physical == 6
    assert rect.n_MRR == 15
    assert rect.n_stages == 6
    assert rect.stage_pairs == SPANKE_BENES_RECT_6_LITERAL
    assert not rect.has_native_crossings()
    assert build_spanke_benes_rect_stage_pairs(6) == SPANKE_BENES_RECT_6_LITERAL


@pytest.mark.parametrize("n_logical", list(range(2, 17)))
def test_spanke_benes_rect_count_formulas(n_logical: int) -> None:
    stages = build_spanke_benes_rect_stage_pairs(n_logical)
    expected_stage_count = 1 if n_logical == 2 else n_logical
    assert len(stages) == expected_stage_count
    assert sum(len(stage) for stage in stages) == n_logical * (n_logical - 1) // 2
    for stage_idx, pairs in enumerate(stages):
        wires = [wire for pair in pairs for wire in pair]
        assert len(wires) == len(set(wires))
        for low, high in pairs:
            assert high == low + 1
        assert all(low % 2 == stage_idx % 2 for low, _high in pairs)


@pytest.mark.parametrize(
    ("n_logical", "expected_stages", "expected_mrr"),
    [
        (2, 1, 1),
        (3, 3, 3),
        (4, 3, 5),
        (5, 5, 8),
        (6, 5, 11),
        (7, 5, 14),
        (8, 5, 17),
        (16, 7, 49),
    ],
)
def test_waksman_count_formulas(
    n_logical: int,
    expected_stages: int,
    expected_mrr: int,
) -> None:
    stages = build_waksman_stage_pairs(n_logical)
    assert len(stages) == expected_stages
    assert sum(len(stage) for stage in stages) == expected_mrr


def test_large_lut_guard_fails_loudly() -> None:
    with pytest.raises(NotImplementedError, match="constructive strategy required"):
        PaddedBenesTopology(16)
    with pytest.raises(NotImplementedError, match="constructive strategy required"):
        SpankeBenesTopology(7)
    with pytest.raises(NotImplementedError, match="constructive strategy required"):
        SpankeBenesRectTopology(7)
    with pytest.raises(NotImplementedError, match="constructive strategy required"):
        WaksmanTopology(16)


def test_small_n_topologies_construct_and_route_samples() -> None:
    padded = PaddedBenesTopology(4)
    padded_perm = (2, 0, 3, 1)
    padded_states = padded.get_state_assignment(padded_perm)
    assert _realized_permutation(padded, padded_states)[: padded.N_logical] == padded_perm

    waksman = WaksmanTopology(8)
    waksman_perm = (7, 6, 5, 4, 3, 2, 1, 0)
    waksman_states = waksman.get_state_assignment(waksman_perm)
    assert _realized_permutation(waksman, waksman_states) == waksman_perm


def _realized_permutation(
    topology: RNBTopology,
    states: StateAssignment,
) -> tuple[int, ...]:
    target = [0] * topology.N_physical
    for input_wire in range(topology.N_physical):
        wire = input_wire
        for stage_idx, pairs in enumerate(topology.stage_pairs):
            for pair in pairs:
                if wire not in pair:
                    continue
                state = states[topology.mrr_id(stage_idx, pair)]
                if state:
                    wire = pair[1] if wire == pair[0] else pair[0]
                break
            wire = topology._apply_fixed_permutation(stage_idx, wire)
        target[input_wire] = wire
    return tuple(target)
