from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations
from math import ceil, log2
from typing import Iterable

from .models import port_for_wire, state_for_transition
from .state_assignment import BruteForceLUTStrategy, StateAssignmentStrategy

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
    blocked_ports: tuple[int, ...] = ()

    def __init__(
        self,
        *,
        strategy: StateAssignmentStrategy | None = None,
        verify_rnb: bool | None = None,
    ) -> None:
        self._state_strategy = strategy or BruteForceLUTStrategy()
        self._lut: dict[Permutation, StateAssignment] = {}
        if isinstance(self._state_strategy, BruteForceLUTStrategy):
            self._lut = self._state_strategy.lut_for(self)
        if verify_rnb is None:
            verify_rnb = self.N_logical <= 6
        if verify_rnb:
            self._assert_rnb()

    def _assert_rnb(self) -> None:
        missing = []
        for permutation in permutations(range(self.N_logical)):
            try:
                self.get_state_assignment(permutation)
            except ValueError:
                missing.append(permutation)
        if missing:
            raise AssertionError(
                f"{self.name}: not RNB — {len(missing)} permutation(s) unroutable, "
                f"e.g. {missing[:3]}"
            )

    def get_state_assignment(self, permutation: Permutation) -> StateAssignment:
        return self._state_strategy.assign(self, permutation)

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
        return BruteForceLUTStrategy().lut_for(self)


class PaddedBenesTopology(RNBTopology):
    def __init__(
        self,
        n_logical: int = 6,
        *,
        strategy: StateAssignmentStrategy | None = None,
        verify_rnb: bool | None = None,
    ) -> None:
        if n_logical < 2:
            raise ValueError(f"Padded Beneš size must be at least 2, got {n_logical}")
        n_physical = _next_power_of_two(n_logical)
        pairs = build_benes_stage_pairs(n_physical)
        self.name = f"padded_benes_{n_physical}x{n_physical}"
        self.N_logical = n_logical
        self.N_physical = n_physical
        self.stage_pairs = pairs
        self.n_stages = len(pairs)
        self.n_MRR = sum(len(stage) for stage in pairs)
        self.blocked_ports = tuple(range(n_logical, n_physical))
        super().__init__(strategy=strategy, verify_rnb=verify_rnb)

    def has_native_crossings(self) -> bool:
        return True


class SpankeBenesTopology(RNBTopology):
    def __init__(
        self,
        n_logical: int = 6,
        *,
        strategy: StateAssignmentStrategy | None = None,
        verify_rnb: bool | None = None,
    ) -> None:
        if n_logical < 2:
            raise ValueError(f"Spanke-Beneš size must be at least 2, got {n_logical}")
        pairs = build_spanke_benes_stage_pairs(n_logical)
        self.name = f"spanke_benes_{n_logical}x{n_logical}"
        self.N_logical = n_logical
        self.N_physical = n_logical
        self.stage_pairs = pairs
        self.n_stages = len(pairs)
        self.n_MRR = sum(len(stage) for stage in pairs)
        self.blocked_ports = ()
        super().__init__(strategy=strategy, verify_rnb=verify_rnb)

    def has_native_crossings(self) -> bool:
        return False


class SpankeBenesRectTopology(RNBTopology):
    """Rectangular (odd-even brick-wall) Spanke-Beneš planar network.

    Canonical Spanke & Beneš (1987) arrangement: N stages of adjacent-pair
    2x2 switches with N(N-1)/2 switches in total, same as the triangular
    :class:`SpankeBenesTopology` but with a much tighter per-path
    switch-traversal band (~N/2..N instead of 1..2N-3).
    """

    def __init__(
        self,
        n_logical: int = 6,
        *,
        strategy: StateAssignmentStrategy | None = None,
        verify_rnb: bool | None = None,
    ) -> None:
        if n_logical < 2:
            raise ValueError(
                f"Rectangular Spanke-Beneš size must be at least 2, got {n_logical}"
            )
        pairs = build_spanke_benes_rect_stage_pairs(n_logical)
        self.name = f"spanke_benes_rect_{n_logical}x{n_logical}"
        self.N_logical = n_logical
        self.N_physical = n_logical
        self.stage_pairs = pairs
        self.n_stages = len(pairs)
        self.n_MRR = sum(len(stage) for stage in pairs)
        self.blocked_ports = ()
        super().__init__(strategy=strategy, verify_rnb=verify_rnb)

    def has_native_crossings(self) -> bool:
        return False


class WaksmanTopology(RNBTopology):
    def __init__(
        self,
        n_logical: int = 6,
        *,
        strategy: StateAssignmentStrategy | None = None,
        verify_rnb: bool | None = None,
    ) -> None:
        if n_logical < 2:
            raise ValueError(f"Waksman network size must be at least 2, got {n_logical}")
        pairs = build_waksman_stage_pairs(n_logical)
        self.name = f"waksman_{n_logical}x{n_logical}"
        self.N_logical = n_logical
        self.N_physical = n_logical
        self.stage_pairs = pairs
        self.n_stages = len(pairs)
        self.n_MRR = sum(len(stage) for stage in pairs)
        self.blocked_ports = ()
        super().__init__(strategy=strategy, verify_rnb=verify_rnb)

    def has_native_crossings(self) -> bool:
        return True


def build_benes_stage_pairs(n_physical: int) -> tuple[tuple[tuple[int, int], ...], ...]:
    if n_physical < 2 or not _is_power_of_two(n_physical):
        raise ValueError(f"Beneš physical size must be a power of two >= 2, got {n_physical}")
    levels = int(log2(n_physical))
    distances = [2**level for level in range(levels)]
    distances.extend(2**level for level in range(levels - 2, -1, -1))
    return tuple(_butterfly_stage_pairs(n_physical, distance) for distance in distances)


def build_spanke_benes_stage_pairs(n: int) -> tuple[tuple[tuple[int, int], ...], ...]:
    if n < 2:
        raise ValueError(f"Spanke-Beneš size must be at least 2, got {n}")
    stages: list[tuple[tuple[int, int], ...]] = []
    for stage_idx in range(2 * n - 3):
        span = min(stage_idx, 2 * n - 4 - stage_idx)
        start = stage_idx % 2
        stages.append(tuple((wire, wire + 1) for wire in range(start, span + 1, 2)))
    return tuple(stages)


def build_spanke_benes_rect_stage_pairs(n: int) -> tuple[tuple[tuple[int, int], ...], ...]:
    """Odd-even brick-wall (rectangular) Spanke-Beneš switch stages.

    Convention: even-indexed stages pair wires (0, 1), (2, 3), ...; odd-indexed
    stages pair wires (1, 2), (3, 4), .... This is the odd-even transposition
    sort schedule with N stages and N(N-1)/2 switches in total. Empty stages
    are dropped, which only affects N == 2 (its single odd stage has no
    comparator, so N == 2 has one stage, coinciding with the triangular form).
    """
    if n < 2:
        raise ValueError(f"Rectangular Spanke-Beneš size must be at least 2, got {n}")
    stages: list[tuple[tuple[int, int], ...]] = []
    for stage_idx in range(n):
        start = stage_idx % 2
        pairs = tuple((wire, wire + 1) for wire in range(start, n - 1, 2))
        if pairs:
            stages.append(pairs)
    return tuple(stages)


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


def _next_power_of_two(value: int) -> int:
    return 1 << ceil(log2(value))


def _is_power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


def _butterfly_stage_pairs(
    n_physical: int,
    distance: int,
) -> tuple[tuple[int, int], ...]:
    pairs: list[tuple[int, int]] = []
    block_size = 2 * distance
    for block_start in range(0, n_physical, block_size):
        for offset in range(distance):
            pairs.append((block_start + offset, block_start + offset + distance))
    return tuple(pairs)


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
