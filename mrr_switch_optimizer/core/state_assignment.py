from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from .topology import Permutation, RNBTopology, StateAssignment

_LUT_CACHE: dict[tuple[object, ...], dict[Permutation, StateAssignment]] = {}


class StateAssignmentStrategy(Protocol):
    def assign(self, topology: RNBTopology, permutation: Permutation) -> StateAssignment:
        ...


@dataclass
class BruteForceLUTStrategy:
    max_mrr: int = 20
    _lut: dict[Permutation, StateAssignment] | None = None

    def assign(self, topology: RNBTopology, permutation: Permutation) -> StateAssignment:
        target = topology._physical_target(permutation)
        lut = self.lut_for(topology)
        if target not in lut:
            raise ValueError(f"{topology.name} cannot route permutation {permutation}")
        return lut[target]

    def lut_for(self, topology: RNBTopology) -> dict[Permutation, StateAssignment]:
        key = _lut_cache_key(topology, self.max_mrr)
        if key not in _LUT_CACHE:
            _LUT_CACHE[key] = self._build_lut(topology)
        self._lut = _LUT_CACHE[key]
        return self._lut

    def _build_lut(self, topology: RNBTopology) -> dict[Permutation, StateAssignment]:
        if topology.n_MRR > self.max_mrr:
            raise NotImplementedError(
                f"{topology.name}: constructive strategy required before building "
                f"a brute-force LUT for {topology.n_MRR} MRRs"
            )
        switch_count = sum(len(stage) for stage in topology.stage_pairs)
        lut: dict[Permutation, StateAssignment] = {}
        flat = [
            (stage_idx, pair)
            for stage_idx, pairs in enumerate(topology.stage_pairs)
            for pair in pairs
        ]
        for bits in product((0, 1), repeat=switch_count):
            states: StateAssignment = {
                topology.mrr_id(stage_idx, pair): state
                for (stage_idx, pair), state in zip(flat, bits)
            }
            target = realize_state_assignment(topology, states)
            lut.setdefault(target, states)
        return lut


class BenesLoopingStrategy:
    def assign(self, topology: RNBTopology, permutation: Permutation) -> StateAssignment:
        target = topology._physical_target(permutation)
        n = topology.N_physical
        if n < 2 or n & (n - 1):
            raise ValueError(f"{topology.name}: Beneš strategy requires power-of-two physical N")
        bits = n.bit_length() - 1
        standard_perm = {
            _bit_reverse(input_wire, bits): _bit_reverse(output_wire, bits)
            for input_wire, output_wire in enumerate(target)
        }
        standard_states = _assign_standard_benes(tuple(range(n)), standard_perm, 0)
        states: StateAssignment = {}
        for (stage_idx, pair), state in standard_states.items():
            original_pair = cast(
                tuple[int, int],
                tuple(sorted(_bit_reverse(wire, bits) for wire in pair)),
            )
            states[topology.mrr_id(stage_idx, original_pair)] = state
        _require_complete_states(topology, states)
        return states


class WaksmanStrategy:
    def assign(self, topology: RNBTopology, permutation: Permutation) -> StateAssignment:
        if topology.N_physical != topology.N_logical:
            raise ValueError(f"{topology.name}: Waksman strategy does not support padding")
        target = topology._physical_target(permutation)
        perm = {input_wire: output_wire for input_wire, output_wire in enumerate(target)}
        states = _assign_waksman_recursive(tuple(range(topology.N_physical)), perm, 0, topology)
        _require_complete_states(topology, states)
        return states


class SpankeBenesStrategy:
    def assign(self, topology: RNBTopology, permutation: Permutation) -> StateAssignment:
        target = topology._physical_target(permutation)
        desired_rank = {input_wire: output_wire for input_wire, output_wire in enumerate(target)}
        wires = list(range(topology.N_physical))
        states: StateAssignment = {}
        for stage_idx, pairs in enumerate(topology.stage_pairs):
            for pair in pairs:
                a, b = pair
                state = 1 if desired_rank[wires[a]] > desired_rank[wires[b]] else 0
                states[topology.mrr_id(stage_idx, pair)] = state
                if state:
                    wires[a], wires[b] = wires[b], wires[a]
        _require_complete_states(topology, states)
        return states


class SpankeBenesRectStrategy(SpankeBenesStrategy):
    """Odd-even transposition sort routing for the rectangular Spanke-Beneš.

    Runs the bubble-sort schedule that matches the odd-even brick-wall stages
    of :class:`~mrr_switch_optimizer.core.topology.SpankeBenesRectTopology`:
    each comparator crosses (state 1) when the two upstream signals are out of
    order with respect to their target outputs and passes (state 0) otherwise.
    The compare-exchange rule and state emission are identical to
    :class:`SpankeBenesStrategy`; only the stage schedule (taken from
    ``topology.stage_pairs``) differs. Correctness follows from the odd-even
    transposition sort theorem: N alternating stages sort any permutation.
    """


def verify_state_assignment(
    topology: RNBTopology,
    permutation: Permutation,
    states: StateAssignment,
) -> bool:
    return realize_state_assignment(topology, states) == topology._physical_target(permutation)


def realize_state_assignment(
    topology: RNBTopology,
    states: StateAssignment,
) -> Permutation:
    wires = list(range(topology.N_physical))
    for stage_idx, pairs in enumerate(topology.stage_pairs):
        for pair in pairs:
            mrr_id = topology.mrr_id(stage_idx, pair)
            state = states.get(mrr_id)
            if state not in (0, 1):
                raise ValueError(f"state assignment missing valid state for {mrr_id}")
            a, b = pair
            if state:
                wires[a], wires[b] = wires[b], wires[a]
        permutation = topology._fixed_permutation(stage_idx)
        next_wires = [0] * topology.N_physical
        for old_wire, new_wire in enumerate(permutation):
            next_wires[new_wire] = wires[old_wire]
        wires = next_wires

    target: list[int] = [0] * topology.N_physical
    for output_wire, input_wire in enumerate(wires):
        target[input_wire] = output_wire
    return tuple(target)


def _assign_standard_benes(
    wires: tuple[int, ...],
    perm: dict[int, int],
    stage_offset: int,
) -> dict[tuple[int, tuple[int, int]], int]:
    n = len(wires)
    if n == 1:
        only = wires[0]
        if perm[only] != only:
            raise ValueError(f"invalid size-1 Beneš subpermutation: {perm}")
        return {}
    if n == 2:
        a, b = wires
        if perm[a] == a and perm[b] == b:
            return {(stage_offset, (a, b)): 0}
        if perm[a] == b and perm[b] == a:
            return {(stage_offset, (a, b)): 1}
        raise ValueError(f"invalid size-2 Beneš subpermutation: {perm}")

    half = n // 2
    inverse = {output_wire: input_wire for input_wire, output_wire in perm.items()}
    constraints: list[tuple[int, int, int]] = []
    for idx in range(half):
        constraints.append((wires[idx], wires[idx + half], 1))
        constraints.append((inverse[wires[idx]], inverse[wires[idx + half]], 1))
    colors = _solve_binary_constraints(wires, {}, constraints)

    first_stage = stage_offset
    last_stage = stage_offset + 2 * (n.bit_length() - 1) - 2
    states: dict[tuple[int, tuple[int, int]], int] = {}
    for idx in range(half):
        input_pair = (wires[idx], wires[idx + half])
        output_pair = (wires[idx], wires[idx + half])
        states[(first_stage, input_pair)] = colors[wires[idx]]
        states[(last_stage, output_pair)] = colors[inverse[wires[idx]]]

    wire_pos = {wire: idx for idx, wire in enumerate(wires)}
    subperms: list[dict[int, int]] = [{}, {}]
    for input_wire, output_wire in perm.items():
        color = colors[input_wire]
        input_idx = wire_pos[input_wire] % half
        output_idx = wire_pos[output_wire] % half
        subnet_input = wires[input_idx] if color == 0 else wires[input_idx + half]
        subnet_output = wires[output_idx] if color == 0 else wires[output_idx + half]
        subperms[color][subnet_input] = subnet_output

    states.update(_assign_standard_benes(wires[:half], subperms[0], stage_offset + 1))
    states.update(_assign_standard_benes(wires[half:], subperms[1], stage_offset + 1))
    return states


def _assign_waksman_recursive(
    wires: tuple[int, ...],
    perm: dict[int, int],
    stage_offset: int,
    topology: RNBTopology,
) -> StateAssignment:
    n = len(wires)
    if n == 1:
        only = wires[0]
        if perm[only] != only:
            raise ValueError(f"invalid size-1 Waksman subpermutation: {perm}")
        return {}
    if n == 2:
        a, b = wires
        if perm[a] == a and perm[b] == b:
            return {topology.mrr_id(stage_offset, (a, b)): 0}
        if perm[a] == b and perm[b] == a:
            return {topology.mrr_id(stage_offset, (a, b)): 1}
        raise ValueError(f"invalid size-2 Waksman subpermutation: {perm}")

    pair_count = n // 2
    inverse = {output_wire: input_wire for input_wire, output_wire in perm.items()}
    fixed: dict[int, int] = {}
    constraints: list[tuple[int, int, int]] = []

    for idx in range(pair_count):
        constraints.append((wires[2 * idx], wires[2 * idx + 1], 1))
    if n % 2:
        fixed[wires[-1]] = 0

    output_pair_count = pair_count if n % 2 else pair_count - 1
    for idx in range(pair_count):
        output_a = wires[2 * idx]
        output_b = wires[2 * idx + 1]
        input_a = inverse[output_a]
        input_b = inverse[output_b]
        if idx < output_pair_count:
            constraints.append((input_a, input_b, 1))
        else:
            fixed[input_a] = 0
            fixed[input_b] = 1
    if n % 2:
        fixed[inverse[wires[-1]]] = 0

    colors = _solve_binary_constraints(wires, fixed, constraints)
    states: StateAssignment = {}
    for idx in range(pair_count):
        pair = (wires[2 * idx], wires[2 * idx + 1])
        states[topology.mrr_id(stage_offset, pair)] = colors[pair[0]]

    middle_stage_count = max(
        _waksman_stage_count(len(wires[0::2])),
        _waksman_stage_count(len(wires[1::2])),
    )
    output_stage = stage_offset + 1 + middle_stage_count
    for idx in range(output_pair_count):
        pair = (wires[2 * idx], wires[2 * idx + 1])
        states[topology.mrr_id(output_stage, pair)] = colors[inverse[pair[0]]]

    wire_pos = {wire: idx for idx, wire in enumerate(wires)}
    upper_wires = wires[0::2]
    lower_wires = wires[1::2]
    subperms: list[dict[int, int]] = [{}, {}]
    for input_wire, output_wire in perm.items():
        color = colors[input_wire]
        input_pos = wire_pos[input_wire]
        input_idx = input_pos // 2
        if input_pos == n - 1 and n % 2:
            input_idx = len(upper_wires) - 1
        output_pos = wire_pos[output_wire]
        output_idx = output_pos // 2
        if output_pos == n - 1 and n % 2:
            output_idx = len(upper_wires) - 1
        subnet_input = upper_wires[input_idx] if color == 0 else lower_wires[input_idx]
        subnet_output = upper_wires[output_idx] if color == 0 else lower_wires[output_idx]
        subperms[color][subnet_input] = subnet_output

    states.update(_assign_waksman_recursive(upper_wires, subperms[0], stage_offset + 1, topology))
    if lower_wires:
        states.update(_assign_waksman_recursive(lower_wires, subperms[1], stage_offset + 1, topology))
    return states


def _waksman_stage_count(n: int) -> int:
    if n <= 1:
        return 0
    if n == 2:
        return 1
    output_pair_count = n // 2 if n % 2 else n // 2 - 1
    return (
        1
        + max(_waksman_stage_count((n + 1) // 2), _waksman_stage_count(n // 2))
        + (1 if output_pair_count else 0)
    )


def _solve_binary_constraints(
    variables: tuple[int, ...],
    fixed: dict[int, int],
    constraints: list[tuple[int, int, int]],
) -> dict[int, int]:
    adjacency: dict[int, list[tuple[int, int]]] = {variable: [] for variable in variables}
    for left, right, parity in constraints:
        adjacency[left].append((right, parity))
        adjacency[right].append((left, parity))

    colors: dict[int, int] = {}
    for variable, color in fixed.items():
        _set_color(colors, variable, color)
        _propagate_color(variable, adjacency, colors)
    for variable in variables:
        if variable in colors:
            continue
        _set_color(colors, variable, 0)
        _propagate_color(variable, adjacency, colors)
    return colors


def _propagate_color(
    seed: int,
    adjacency: dict[int, list[tuple[int, int]]],
    colors: dict[int, int],
) -> None:
    stack = [seed]
    while stack:
        current = stack.pop()
        for other, parity in adjacency[current]:
            expected = colors[current] ^ parity
            if other in colors:
                if colors[other] != expected:
                    raise ValueError("inconsistent permutation-network coloring constraints")
                continue
            colors[other] = expected
            stack.append(other)


def _set_color(colors: dict[int, int], variable: int, color: int) -> None:
    if variable in colors and colors[variable] != color:
        raise ValueError("inconsistent fixed permutation-network coloring constraints")
    colors[variable] = color


def _bit_reverse(value: int, bits: int) -> int:
    result = 0
    for _ in range(bits):
        result = (result << 1) | (value & 1)
        value >>= 1
    return result


def _require_complete_states(topology: RNBTopology, states: StateAssignment) -> None:
    expected = {mrr_id for mrr_id, _stage, _pair in topology.iter_mrrs()}
    missing = expected - set(states)
    extra = set(states) - expected
    if missing or extra:
        raise ValueError(
            f"{topology.name}: incomplete state assignment "
            f"(missing={len(missing)}, extra={len(extra)})"
        )


def _lut_cache_key(
    topology: RNBTopology,
    max_mrr: int,
) -> tuple[object, ...]:
    fixed_permutations = tuple(
        topology._fixed_permutation(stage_idx)
        for stage_idx in range(len(topology.stage_pairs))
    )
    return (
        max_mrr,
        type(topology).__name__,
        topology.name,
        topology.N_logical,
        topology.N_physical,
        topology.stage_pairs,
        fixed_permutations,
    )
