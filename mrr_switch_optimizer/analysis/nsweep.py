from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import permutations
from math import factorial
import multiprocessing as mp
import random
import time
from typing import Any

from ..core.fabric import FabricEdge, FabricGraph
from ..core.models import MRRCell, port_for_wire, state_for_transition
from ..core.state_assignment import BenesLoopingStrategy, verify_state_assignment
from ..core.topology import Path, Permutation, RNBTopology, RouteStep
from ..routing.fabric import FabricEdgeRoute, FixedFabricRoutingResult
from .fabric_coverage import PermutationCoverageReport
from .fabric_loss import (
    FabricWorstILReport,
    _PathLoss,
    _crossing_indices_by_edge,
    _evaluate_path_loss,
)


@dataclass(frozen=True)
class ParallelExhaustiveResult:
    loss_report: FabricWorstILReport
    coverage_report: PermutationCoverageReport
    wall_clock_s: float
    workers: int
    chunk_count: int


@dataclass(frozen=True)
class PathRankEntry:
    rank: int
    insertion_loss_db: float
    input_port: int
    output_port: int
    realizable: bool
    witness_permutation: Permutation | None
    proof: str


@dataclass(frozen=True)
class PathSpaceResult:
    worst_insertion_loss_db: float
    worst_path: Path
    worst_loss: _PathLoss
    witness_permutation: Permutation
    enumerated_paths: int
    realizable_paths: int
    unrealizable_paths: int
    path_rank_table: tuple[PathRankEntry, ...]
    wall_clock_s: float


_WORKER: dict[str, Any] = {}


def _loss_setup(
    result: FixedFabricRoutingResult,
) -> tuple[
    dict[tuple[str, str], str],
    dict[str, FabricEdgeRoute],
    dict[str, frozenset[int]],
]:
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
    return edge_id_by_link, edge_route_by_id, crossing_indices_by_edge


def _init_exhaustive_worker(
    topology: RNBTopology,
    cells: dict[str, MRRCell],
    result: FixedFabricRoutingResult,
) -> None:
    edge_id_by_link, edge_route_by_id, crossing_indices_by_edge = _loss_setup(result)
    _WORKER.clear()
    _WORKER.update(
        topology=topology,
        cells=cells,
        result=result,
        edge_id_by_link=edge_id_by_link,
        edge_route_by_id=edge_route_by_id,
        crossing_indices_by_edge=crossing_indices_by_edge,
        fabric_links={
            (edge.source.endpoint_id, edge.target.endpoint_id)
            for edge in result.graph.edges
        },
    )


def _evaluate_prefix_chunk(prefix: tuple[int, int]) -> dict[str, Any]:
    topology: RNBTopology = _WORKER["topology"]
    cells: dict[str, MRRCell] = _WORKER["cells"]
    result: FixedFabricRoutingResult = _WORKER["result"]
    remaining = tuple(value for value in range(topology.N_logical) if value not in prefix)
    worst: tuple[float, Permutation, Path, _PathLoss] | None = None
    assignment_failures: list[Permutation] = []
    fabric_path_failures: list[Permutation] = []
    checked = 0
    paths_checked = 0
    for suffix in permutations(remaining):
        permutation = prefix + suffix
        checked += 1
        states = topology.get_state_assignment(permutation)
        if not verify_state_assignment(topology, permutation, states):
            assignment_failures.append(permutation)
            continue
        active_paths = topology.get_active_paths(permutation, states)
        if not all(_path_uses_fabric(path, _WORKER["fabric_links"]) for path in active_paths):
            fabric_path_failures.append(permutation)
            continue
        for path in active_paths:
            paths_checked += 1
            loss = _evaluate_path_loss(
                path,
                cells,
                result,
                _WORKER["edge_id_by_link"],
                _WORKER["edge_route_by_id"],
                _WORKER["crossing_indices_by_edge"],
            )
            key = (loss.insertion_loss_db, tuple(-v for v in permutation), -path.input_port)
            if worst is None:
                worst = (loss.insertion_loss_db, permutation, path, loss)
            else:
                old_key = (
                    worst[0],
                    tuple(-v for v in worst[1]),
                    -worst[2].input_port,
                )
                if key > old_key:
                    worst = (loss.insertion_loss_db, permutation, path, loss)
    if worst is None:
        raise ValueError(f"chunk {prefix} produced no logical paths")
    return {
        "worst": worst,
        "checked": checked,
        "paths_checked": paths_checked,
        "assignment_failures": assignment_failures,
        "fabric_path_failures": fabric_path_failures,
    }


def _path_uses_fabric(path: Path, fabric_links: set[tuple[str, str]]) -> bool:
    if not path.steps:
        return False
    links = [(f"I{path.input_port}", f"{path.steps[0].mrr_id}.{path.steps[0].in_port}")]
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
    return all(link in fabric_links for link in links)


def evaluate_fixed_fabric_parallel(
    topology: RNBTopology,
    cells: dict[str, MRRCell],
    result: FixedFabricRoutingResult,
    *,
    workers: int = 64,
) -> ParallelExhaustiveResult:
    """One fork-parallel N! pass for exact WIL and permutation coverage."""
    if result.failed_edges:
        raise ValueError("cannot evaluate insertion loss with failed fabric edges")
    if result.drc_violations:
        raise ValueError("cannot evaluate insertion loss with fabric DRC violations")
    workers = max(1, min(64, workers))
    prefixes = tuple(
        (first, second)
        for first in range(topology.N_logical)
        for second in range(topology.N_logical)
        if first != second
    )
    started = time.perf_counter()
    if workers == 1:
        _init_exhaustive_worker(topology, cells, result)
        chunks = [_evaluate_prefix_chunk(prefix) for prefix in prefixes]
    else:
        context = mp.get_context("fork")
        with context.Pool(
            processes=min(workers, len(prefixes)),
            initializer=_init_exhaustive_worker,
            initargs=(topology, cells, result),
        ) as pool:
            chunks = pool.map(_evaluate_prefix_chunk, prefixes, chunksize=1)
    wall_clock_s = time.perf_counter() - started

    best = min(
        (chunk["worst"] for chunk in chunks),
        key=lambda item: (-item[0], item[1], item[2].input_port, item[2].output_port),
    )
    _value, worst_permutation, worst_path, worst_loss = best
    coverage = PermutationCoverageReport(
        topology_name=topology.name,
        fabric_edge_count=len(result.graph.edges),
        permutations_checked=sum(chunk["checked"] for chunk in chunks),
        assignment_failures=tuple(
            failure
            for chunk in chunks
            for failure in chunk["assignment_failures"]
        ),
        fabric_path_failures=tuple(
            failure
            for chunk in chunks
            for failure in chunk["fabric_path_failures"]
        ),
        blocked_ports=topology.blocked_ports,
    )
    loss_report = FabricWorstILReport(
        topology_name=topology.name,
        n_logical=topology.N_logical,
        n_physical=topology.N_physical,
        mrr_count=topology.n_MRR,
        fabric_edge_count=len(result.graph.edges),
        permutations_checked=coverage.permutations_checked,
        paths_checked=sum(chunk["paths_checked"] for chunk in chunks),
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
    return ParallelExhaustiveResult(
        loss_report=loss_report,
        coverage_report=coverage,
        wall_clock_s=wall_clock_s,
        workers=workers,
        chunk_count=len(prefixes),
    )


def enumerate_fabric_paths(
    topology: RNBTopology,
    graph: FabricGraph,
) -> tuple[Path, ...]:
    """Enumerate every directed logical I-to-O path by bar/drop branching."""
    outgoing = {edge.source.endpoint_id: edge for edge in graph.edges}
    info = {mrr_id: (stage, pair) for mrr_id, stage, pair in topology.iter_mrrs()}
    blocked = set(topology.blocked_ports)
    paths: list[Path] = []
    for input_port in range(topology.N_logical):
        stack: list[tuple[FabricEdge, tuple[RouteStep, ...]]] = [
            (outgoing[f"I{input_port}"], ())
        ]
        while stack:
            edge, steps = stack.pop()
            if edge.target.kind == "output":
                if edge.target.wire not in blocked:
                    paths.append(Path(input_port, edge.target.wire, steps))
                continue
            mrr_id = edge.target.ref
            stage, pair = info[mrr_id]
            wire_in = edge.target.wire
            other = pair[1] if wire_in == pair[0] else pair[0]
            for wire_out in (other, wire_in):
                step = RouteStep(
                    mrr_id=mrr_id,
                    stage=stage,
                    pair=pair,
                    wire_in=wire_in,
                    wire_out=wire_out,
                    in_port=port_for_wire(pair, wire_in, "input"),
                    out_port=port_for_wire(pair, wire_out, "output"),
                    state=state_for_transition(pair, wire_in, wire_out),
                )
                stack.append(
                    (outgoing[f"{mrr_id}.{step.out_port}"], steps + (step,))
                )
    unique = tuple(dict.fromkeys(paths))
    if len(unique) != len(paths):
        raise AssertionError("directed path enumeration produced duplicates")
    return unique


def _benes_forced_bar_switches(topology: RNBTopology) -> frozenset[str]:
    if not isinstance(topology._state_strategy, BenesLoopingStrategy):
        return frozenset()
    n = topology.N_physical
    bits = n.bit_length() - 1

    def reverse(wire: int) -> int:
        return int(f"{wire:0{bits}b}"[::-1], 2)

    forced: set[str] = set()

    def walk(wires: tuple[int, ...], stage: int) -> None:
        if len(wires) < 4:
            return
        half = len(wires) // 2
        first = reverse(wires[0])
        second = reverse(wires[half])
        original_pair = (min(first, second), max(first, second))
        forced.add(topology.mrr_id(stage, original_pair))
        walk(wires[:half], stage + 1)
        walk(wires[half:], stage + 1)

    walk(tuple(range(n)), 0)
    return frozenset(forced)


def _path_for_permutation(
    topology: RNBTopology,
    permutation: Permutation,
    input_port: int,
) -> Path:
    states = topology.get_state_assignment(permutation)
    return topology.get_active_paths(permutation, states)[input_port]


def _witness_for_path(
    topology: RNBTopology,
    path: Path,
    *,
    random_tries: int,
) -> tuple[Permutation | None, str]:
    forced = _benes_forced_bar_switches(topology)
    forbidden = tuple(
        step.mrr_id
        for step in path.steps
        if step.state == 1 and step.mrr_id in forced
    )
    if forbidden:
        return None, "strategy-recursion prune: forced-bar " + ",".join(forbidden)

    input_port, output_port = path.input_port, path.output_port
    sources = [value for value in range(topology.N_logical) if value != input_port]
    targets = [value for value in range(topology.N_logical) if value != output_port]
    seed = sum(
        (index + 1) * sum(ord(ch) for ch in step.mrr_id) * (step.state + 1)
        for index, step in enumerate(path.steps)
    )
    rng = random.Random(seed)
    for attempt in range(1, random_tries + 1):
        shuffled = rng.sample(targets, len(targets))
        candidate = [0] * topology.N_logical
        candidate[input_port] = output_port
        for source, target in zip(sources, shuffled):
            candidate[source] = target
        permutation = tuple(candidate)
        if _path_for_permutation(topology, permutation, input_port) == path:
            return permutation, f"random witness ({attempt} tries)"

    # Complete deterministic backtracking. Fixing pi(i)=o turns the remaining
    # search into a lexicographically ordered (N-1)! completion tree. Structural
    # Beneš forced-state contradictions were pruned above; every leaf is checked
    # against the actual strategy, so termination is a proof in either direction.
    for target_order in permutations(targets):
        candidate = [0] * topology.N_logical
        candidate[input_port] = output_port
        for source, target in zip(sources, target_order):
            candidate[source] = target
        permutation = tuple(candidate)
        if _path_for_permutation(topology, permutation, input_port) == path:
            return permutation, "deterministic completion witness"
    return None, f"deterministic completion exhausted {factorial(topology.N_logical - 1)} leaves"


def evaluate_fixed_fabric_path_space(
    topology: RNBTopology,
    cells: dict[str, MRRCell],
    result: FixedFabricRoutingResult,
    *,
    random_tries: int = 4096,
    classify_all: bool | None = None,
) -> PathSpaceResult:
    if result.failed_edges:
        raise ValueError("cannot evaluate insertion loss with failed fabric edges")
    if result.drc_violations:
        raise ValueError("cannot evaluate insertion loss with fabric DRC violations")
    started = time.perf_counter()
    if classify_all is None:
        classify_all = topology.N_logical <= 8
    setup = _loss_setup(result)
    paths = enumerate_fabric_paths(topology, result.graph)
    losses = {
        path: _evaluate_path_loss(path, cells, result, *setup)
        for path in paths
    }
    ranked = sorted(
        paths,
        key=lambda path: (
            -losses[path].insertion_loss_db,
            path.input_port,
            path.output_port,
            tuple((step.mrr_id, step.state) for step in path.steps),
        ),
    )
    entries: list[PathRankEntry] = []
    worst: tuple[Path, _PathLoss, Permutation] | None = None
    for rank, path in enumerate(ranked, 1):
        witness, proof = _witness_for_path(
            topology,
            path,
            random_tries=random_tries,
        )
        entries.append(
            PathRankEntry(
                rank=rank,
                insertion_loss_db=losses[path].insertion_loss_db,
                input_port=path.input_port,
                output_port=path.output_port,
                realizable=witness is not None,
                witness_permutation=witness,
                proof=proof,
            )
        )
        if witness is not None and worst is None:
            worst = path, losses[path], witness
            if not classify_all:
                break
    if worst is None:
        raise ValueError(f"{topology.name} has no strategy-realizable logical path")
    worst_path, worst_loss, witness = worst
    return PathSpaceResult(
        worst_insertion_loss_db=worst_loss.insertion_loss_db,
        worst_path=worst_path,
        worst_loss=worst_loss,
        witness_permutation=witness,
        enumerated_paths=len(paths),
        realizable_paths=sum(entry.realizable for entry in entries),
        unrealizable_paths=sum(not entry.realizable for entry in entries),
        path_rank_table=tuple(entries),
        wall_clock_s=time.perf_counter() - started,
    )


def verify_worst_il_witness(
    topology: RNBTopology,
    cells: dict[str, MRRCell],
    result: FixedFabricRoutingResult,
    *,
    witness_permutation: Permutation,
    input_port: int,
    expected_il_db: float,
) -> None:
    path = _path_for_permutation(topology, witness_permutation, input_port)
    loss = _evaluate_path_loss(path, cells, result, *_loss_setup(result))
    if loss.insertion_loss_db != expected_il_db:
        raise AssertionError(
            f"certificate mismatch: {loss.insertion_loss_db!r} != {expected_il_db!r}"
        )


def certificate_payload(path_result: PathSpaceResult) -> dict[str, Any]:
    return {
        "method": "strategy-relative directed-path enumeration + witness proof",
        "worst_il_db": path_result.worst_insertion_loss_db,
        "worst_io_pair": [
            path_result.worst_path.input_port,
            path_result.worst_path.output_port,
        ],
        "witness_permutation": list(path_result.witness_permutation),
        "path_rank_table": [asdict(entry) for entry in path_result.path_rank_table],
        "unrealizable_proofs_count": path_result.unrealizable_paths,
    }


__all__ = [
    "ParallelExhaustiveResult",
    "PathRankEntry",
    "PathSpaceResult",
    "certificate_payload",
    "enumerate_fabric_paths",
    "evaluate_fixed_fabric_parallel",
    "evaluate_fixed_fabric_path_space",
    "verify_worst_il_witness",
]
