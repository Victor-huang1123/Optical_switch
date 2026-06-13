from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations, product
from typing import Iterable

from .models import port_for_wire, state_for_transition

Permutation = tuple[int, ...]
StateAssignment = dict[str, int]


@dataclass(frozen=True)
class RouteStep:
    mrr_id: str
    stage: int
    pair: tuple[int, int]
    wire_in: int
    wire_out: int
    in_port: str
    out_port: str
    state: int


@dataclass(frozen=True)
class Path:
    input_port: int
    output_port: int
    steps: tuple[RouteStep, ...]


class RNBTopology:
    name: str
    N_logical: int
    N_physical: int
    n_MRR: int
    n_stages: int
    stage_pairs: tuple[tuple[tuple[int, int], ...], ...]
    stage_permutations: tuple[Permutation | None, ...] = ()

    def __init__(self) -> None:
        self._lut = self._build_lut()
        self._assert_rnb()

    def _assert_rnb(self) -> None:
        missing = [
            p for p in permutations(range(self.N_logical))
            if self._physical_target(p) not in self._lut
        ]
        if missing:
            raise AssertionError(
                f"{self.name}: not RNB — {len(missing)} permutation(s) unroutable, "
                f"e.g. {missing[:3]}"
            )

    def get_state_assignment(self, permutation: Permutation) -> StateAssignment:
        target = self._physical_target(permutation)
        if target not in self._lut:
            raise ValueError(f"{self.name} cannot route permutation {permutation}")
        return self._lut[target]

    def get_active_paths(
        self, permutation: Permutation, states: StateAssignment | None = None
    ) -> list[Path]:
        states = states or self.get_state_assignment(permutation)
        targets = self._physical_target(permutation)
        paths: list[Path] = []
        for input_wire in range(self.N_logical):
            wire = input_wire
            steps: list[RouteStep] = []
            for stage_idx, pairs in enumerate(self.stage_pairs):
                pair = _find_pair(pairs, wire)
                if pair is None:
                    wire = self._apply_fixed_permutation(stage_idx, wire)
                    continue
                mrr_id = self.mrr_id(stage_idx, pair)
                state = states[mrr_id]
                wire_out = _apply_pair(pair, wire, state)
                steps.append(
                    RouteStep(
                        mrr_id=mrr_id,
                        stage=stage_idx,
                        pair=pair,
                        wire_in=wire,
                        wire_out=wire_out,
                        in_port=port_for_wire(pair, wire, "input"),
                        out_port=port_for_wire(pair, wire_out, "output"),
                        state=state_for_transition(pair, wire, wire_out),
                    )
                )
                wire = self._apply_fixed_permutation(stage_idx, wire_out)
            paths.append(Path(input_wire, targets[input_wire], tuple(steps)))
        return paths

    def get_mrr_stage(self, mrr_id: str) -> int:
        return int(mrr_id.split("_s", 1)[1].split("_", 1)[0])

    def has_native_crossings(self) -> bool:
        raise NotImplementedError

    def mrr_id(self, stage_idx: int, pair: tuple[int, int]) -> str:
        return f"{self.name}_s{stage_idx}_w{pair[0]}_{pair[1]}"

    def iter_mrrs(self) -> Iterable[tuple[str, int, tuple[int, int]]]:
        for stage_idx, pairs in enumerate(self.stage_pairs):
            for pair in pairs:
                yield self.mrr_id(stage_idx, pair), stage_idx, pair

    def _apply_fixed_permutation(self, stage_idx: int, wire: int) -> int:
        permutation = self._fixed_permutation(stage_idx)
        return permutation[wire]

    def _fixed_permutation(self, stage_idx: int) -> Permutation:
        if not self.stage_permutations:
            return tuple(range(self.N_physical))
        permutation = self.stage_permutations[stage_idx]
        if permutation is None:
            return tuple(range(self.N_physical))
        return permutation

    def _physical_target(self, permutation: Permutation) -> Permutation:
        if len(permutation) != self.N_logical:
            raise ValueError(
                f"{self.name} expects {self.N_logical} logical outputs, got {len(permutation)}"
            )
        if sorted(permutation) != list(range(self.N_logical)):
            raise ValueError(f"not a valid S{self.N_logical} permutation: {permutation}")
        if self.N_physical == self.N_logical:
            return permutation
        blocked = tuple(range(self.N_logical, self.N_physical))
        return permutation + blocked

    def _build_lut(self) -> dict[Permutation, StateAssignment]:
        switch_count = sum(len(stage) for stage in self.stage_pairs)
        lut: dict[Permutation, StateAssignment] = {}
        flat = [
            (stage_idx, pair)
            for stage_idx, pairs in enumerate(self.stage_pairs)
            for pair in pairs
        ]
        for bits in product((0, 1), repeat=switch_count):
            wires = list(range(self.N_physical))
            states: StateAssignment = {}
            for (stage_idx, pair), state in zip(flat, bits):
                states[self.mrr_id(stage_idx, pair)] = state
                a, b = pair
                if state:
                    wires[a], wires[b] = wires[b], wires[a]
                if pair == self.stage_pairs[stage_idx][-1]:
                    permutation = self._fixed_permutation(stage_idx)
                    next_wires = [0] * self.N_physical
                    for old_wire, new_wire in enumerate(permutation):
                        next_wires[new_wire] = wires[old_wire]
                    wires = next_wires
            target: list[int] = [0] * self.N_physical
            for output_wire, input_wire in enumerate(wires):
                target[input_wire] = output_wire
            lut.setdefault(tuple(target), states)
        return lut


class PaddedBenesTopology(RNBTopology):
    name = "padded_benes_8x8"
    N_logical = 6
    N_physical = 8
    n_MRR = 20
    n_stages = 5
    stage_pairs = (
        ((0, 1), (2, 3), (4, 5), (6, 7)),
        ((0, 2), (1, 3), (4, 6), (5, 7)),
        ((0, 4), (1, 5), (2, 6), (3, 7)),
        ((0, 2), (1, 3), (4, 6), (5, 7)),
        ((0, 1), (2, 3), (4, 5), (6, 7)),
    )

    def has_native_crossings(self) -> bool:
        return True


class SpankeBenesTopology(RNBTopology):
    name = "spanke_benes_6x6"
    N_logical = 6
    N_physical = 6
    n_MRR = 15
    n_stages = 9
    stage_pairs = (
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

    def has_native_crossings(self) -> bool:
        return False


class WaksmanTopology(RNBTopology):
    name = "waksman_6x6"
    N_logical = 6
    N_physical = 6

    def __init__(self) -> None:
        pairs = build_waksman_stage_pairs(self.N_logical)
        self.stage_pairs = pairs
        self.n_stages = len(pairs)
        self.n_MRR = sum(len(stage) for stage in pairs)
        super().__init__()

    def has_native_crossings(self) -> bool:
        return True


def build_waksman_stage_pairs(n: int) -> tuple[tuple[tuple[int, int], ...], ...]:
    """Recursively build Waksman permutation-network switch stages.

    The returned representation is a sequence of optional 2x2 switches. Pairs
    may be non-adjacent; that encodes the Waksman interconnect at the topology
    level and is why this topology has native crossings.
    """
    if n < 1:
        raise ValueError(f"Waksman network size must be positive, got {n}")
    return _build_waksman_for_wires(tuple(range(n)))


def _build_waksman_for_wires(
    wires: tuple[int, ...]
) -> tuple[tuple[tuple[int, int], ...], ...]:
    n = len(wires)
    if n == 1:
        return tuple()
    if n == 2:
        return (((wires[0], wires[1]),),)

    outer_pairs = tuple((wires[i], wires[i + 1]) for i in range(0, n - 1, 2))
    upper_stages = _build_waksman_for_wires(wires[0::2])
    lower_stages = _build_waksman_for_wires(wires[1::2])
    middle_stages = _merge_disjoint_stages(upper_stages, lower_stages)

    # For even n, one switch in the outer output column is redundant.
    output_pairs = outer_pairs if n % 2 else outer_pairs[:-1]
    output_stage = (output_pairs,) if output_pairs else tuple()
    return (outer_pairs,) + middle_stages + output_stage


def _merge_disjoint_stages(
    left: tuple[tuple[tuple[int, int], ...], ...],
    right: tuple[tuple[tuple[int, int], ...], ...],
) -> tuple[tuple[tuple[int, int], ...], ...]:
    stages: list[tuple[tuple[int, int], ...]] = []
    for idx in range(max(len(left), len(right))):
        pairs: list[tuple[int, int]] = []
        if idx < len(left):
            pairs.extend(left[idx])
        if idx < len(right):
            pairs.extend(right[idx])
        if pairs:
            stages.append(tuple(pairs))
    return tuple(stages)


def _find_pair(
    pairs: tuple[tuple[int, int], ...], wire: int
) -> tuple[int, int] | None:
    for pair in pairs:
        if wire in pair:
            return pair
    return None


def _apply_pair(pair: tuple[int, int], wire: int, state: int) -> int:
    if state == 0:
        return wire
    a, b = pair
    return b if wire == a else a
