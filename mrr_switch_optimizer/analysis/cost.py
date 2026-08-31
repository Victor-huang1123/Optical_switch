from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations as iter_permutations
from math import factorial
from math import exp, log, log10
from random import Random

from ..core.models import DEFAULT_CELL_GEOMETRY, CellGeometry, MRRCell
from ..core.topology import Path, RNBTopology, StateAssignment
from ..placement.layout import build_cells
from .summary import LayerName, PathLossBreakdown

ALPHA_DB_PER_UM: float = 0.002
XT_CROSS_DB: float = -40.0
CROSSING_LOSS_DB_PER_CROSS: float = 0.0


@dataclass(frozen=True)
class PathMetric:
    input_port: int
    output_port: int
    insertion_loss_db: float
    sxr_db: float
    signal_power: float
    leak_power: float
    wiring_um: float
    mrr_loss_db: float


@dataclass(frozen=True)
class RoutingMetric:
    topology: str
    permutation: tuple[int, ...]
    mrr_count: int
    stages: int
    active_states: int
    total_states: int
    min_path_depth: int
    max_path_depth: int
    average_path_depth: float
    total_wiring_um: float
    crossing_count: int
    worst_insertion_loss_db: float
    average_insertion_loss_db: float
    worst_sxr_db: float
    path_metrics: tuple[PathMetric, ...]


@dataclass(frozen=True)
class PermutationPlan:
    permutation: tuple[int, ...]
    states: StateAssignment
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class EvaluationDataset:
    train_plans: tuple[PermutationPlan, ...]
    eval_plans: tuple[PermutationPlan, ...]


def build_permutation_plan(
    topology: RNBTopology,
    permutation: tuple[int, ...],
) -> PermutationPlan:
    states = topology.get_state_assignment(permutation)
    paths = tuple(topology.get_active_paths(permutation, states))
    return PermutationPlan(permutation=permutation, states=states, paths=paths)


def build_evaluation_dataset(
    topology: RNBTopology,
    train_perms: list[tuple[int, ...]],
    eval_perms: list[tuple[int, ...]],
) -> EvaluationDataset:
    plan_by_perm: dict[tuple[int, ...], PermutationPlan] = {}

    def plan_for(permutation: tuple[int, ...]) -> PermutationPlan:
        if permutation not in plan_by_perm:
            plan_by_perm[permutation] = build_permutation_plan(topology, permutation)
        return plan_by_perm[permutation]

    return EvaluationDataset(
        train_plans=tuple(plan_for(permutation) for permutation in train_perms),
        eval_plans=tuple(plan_for(permutation) for permutation in eval_perms),
    )


def evaluate_routing(
    topology: RNBTopology,
    permutation: tuple[int, ...],
    s_table: dict[tuple[str, str, int], float],
    alpha_db_per_um: float = ALPHA_DB_PER_UM,
    xt_cross_db: float = XT_CROSS_DB,
    *,
    crossing_loss_db_per_cross: float = CROSSING_LOSS_DB_PER_CROSS,
    centers: dict[str, tuple[float, float]] | None = None,
    cell_geometry: CellGeometry = DEFAULT_CELL_GEOMETRY,
) -> RoutingMetric:
    return evaluate_plan(
        topology,
        build_permutation_plan(topology, permutation),
        s_table,
        alpha_db_per_um=alpha_db_per_um,
        xt_cross_db=xt_cross_db,
        crossing_loss_db_per_cross=crossing_loss_db_per_cross,
        centers=centers,
        cell_geometry=cell_geometry,
    )


def evaluate_plan(
    topology: RNBTopology,
    plan: PermutationPlan,
    s_table: dict[tuple[str, str, int], float],
    alpha_db_per_um: float = ALPHA_DB_PER_UM,
    xt_cross_db: float = XT_CROSS_DB,
    *,
    crossing_loss_db_per_cross: float = CROSSING_LOSS_DB_PER_CROSS,
    centers: dict[str, tuple[float, float]] | None = None,
    cell_geometry: CellGeometry = DEFAULT_CELL_GEOMETRY,
) -> RoutingMetric:
    paths = list(plan.paths)
    cells = _build_cells_with_centers(
        topology,
        s_table,
        centers,
        cell_geometry=cell_geometry,
    )
    crossing_count = _count_crossings(topology, paths, cells)
    depths = [len(path.steps) for path in paths]
    path_metrics = tuple(
        _evaluate_path(
            path,
            paths,
            cells,
            alpha_db_per_um,
            xt_cross_db,
            topology,
            crossing_loss_db_per_cross,
        )
        for path in paths
    )
    il_values = [m.insertion_loss_db for m in path_metrics]
    sxr_values = [m.sxr_db for m in path_metrics]
    return RoutingMetric(
        topology=topology.name,
        permutation=plan.permutation,
        mrr_count=topology.n_MRR,
        stages=topology.n_stages,
        active_states=sum(plan.states.values()),
        total_states=len(plan.states),
        min_path_depth=min(depths),
        max_path_depth=max(depths),
        average_path_depth=sum(depths) / len(depths),
        total_wiring_um=sum(m.wiring_um for m in path_metrics),
        crossing_count=crossing_count,
        worst_insertion_loss_db=max(il_values),
        average_insertion_loss_db=sum(il_values) / len(il_values),
        worst_sxr_db=min(sxr_values),
        path_metrics=path_metrics,
    )


def evaluate_path_breakdowns(
    topology: RNBTopology,
    plan: PermutationPlan,
    s_table: dict[tuple[str, str, int], float],
    alpha_db_per_um: float = ALPHA_DB_PER_UM,
    xt_cross_db: float = XT_CROSS_DB,
    *,
    crossing_loss_db_per_cross: float = CROSSING_LOSS_DB_PER_CROSS,
    centers: dict[str, tuple[float, float]] | None = None,
    cell_geometry: CellGeometry = DEFAULT_CELL_GEOMETRY,
    sxr_min_db: float = 20.0,
    layer: LayerName = "logical",
) -> tuple[PathLossBreakdown, ...]:
    cells = _build_cells_with_centers(
        topology,
        s_table,
        centers,
        cell_geometry=cell_geometry,
    )
    paths = list(plan.paths)
    return tuple(
        _evaluate_path_breakdown(
            path,
            paths,
            cells,
            alpha_db_per_um,
            xt_cross_db,
            topology,
            crossing_loss_db_per_cross,
            sxr_min_db=sxr_min_db,
            layer=layer,
        )
        for path in paths
    )


def aggregate_cost(
    topology: RNBTopology,
    permutations: list[tuple[int, ...]],
    s_table: dict[tuple[str, str, int], float],
    beta_path: float = 3.0,
    beta_perm: float = 2.0,
    alpha_db_per_um: float = ALPHA_DB_PER_UM,
    xt_cross_db: float = XT_CROSS_DB,
    *,
    omega_il: float = 1.0,
    omega_xt: float = 1.0,
    sxr_min_db: float = 20.0,
    crossing_loss_db_per_cross: float = CROSSING_LOSS_DB_PER_CROSS,
    centers: dict[str, tuple[float, float]] | None = None,
    cell_geometry: CellGeometry = DEFAULT_CELL_GEOMETRY,
) -> float:
    """LSE aggregate cost C(L) over a set of permutations."""
    if not permutations:
        raise ValueError("aggregate_cost requires at least one permutation")
    if beta_path <= 0.0 or beta_perm <= 0.0:
        raise ValueError("beta_path and beta_perm must be positive")

    plans = tuple(build_permutation_plan(topology, permutation) for permutation in permutations)
    return aggregate_cost_for_plans(
        topology,
        plans,
        s_table,
        beta_path=beta_path,
        beta_perm=beta_perm,
        alpha_db_per_um=alpha_db_per_um,
        xt_cross_db=xt_cross_db,
        omega_il=omega_il,
        omega_xt=omega_xt,
        sxr_min_db=sxr_min_db,
        crossing_loss_db_per_cross=crossing_loss_db_per_cross,
        centers=centers,
        cell_geometry=cell_geometry,
    )


def aggregate_cost_for_plans(
    topology: RNBTopology,
    plans: tuple[PermutationPlan, ...],
    s_table: dict[tuple[str, str, int], float],
    beta_path: float = 3.0,
    beta_perm: float = 2.0,
    alpha_db_per_um: float = ALPHA_DB_PER_UM,
    xt_cross_db: float = XT_CROSS_DB,
    *,
    omega_il: float = 1.0,
    omega_xt: float = 1.0,
    sxr_min_db: float = 20.0,
    crossing_loss_db_per_cross: float = CROSSING_LOSS_DB_PER_CROSS,
    centers: dict[str, tuple[float, float]] | None = None,
    cell_geometry: CellGeometry = DEFAULT_CELL_GEOMETRY,
) -> float:
    """LSE aggregate cost C(L) over cached permutation plans."""
    if not plans:
        raise ValueError("aggregate_cost_for_plans requires at least one permutation plan")
    if beta_path <= 0.0 or beta_perm <= 0.0:
        raise ValueError("beta_path and beta_perm must be positive")

    cells = _build_cells_with_centers(
        topology,
        s_table,
        centers,
        cell_geometry=cell_geometry,
    )
    perm_costs: list[float] = []
    for plan in plans:
        paths = list(plan.paths)
        path_costs = []
        for path in paths:
            metric = _evaluate_path(
                path,
                paths,
                cells,
                alpha_db_per_um,
                xt_cross_db,
                topology,
                crossing_loss_db_per_cross,
            )
            path_cost = (
                omega_il * metric.insertion_loss_db
                + omega_xt * max(0.0, sxr_min_db - metric.sxr_db)
            )
            path_costs.append(path_cost)
        perm_costs.append(_logsumexp_scaled(path_costs, beta_path))
    return _logsumexp_scaled(perm_costs, beta_perm)


def make_permutation_split(
    n_logical: int,
    n_train: int = 500,
    seed: int = 42,
    *,
    mode: str = "exhaustive",
    n_eval: int | None = None,
) -> tuple[list[tuple[int, ...]], list[tuple[int, ...]]]:
    """Returns (train_perms, eval_perms) from S_n, fixed seed."""
    if mode not in {"exhaustive", "sample"}:
        raise ValueError(f"unknown permutation split mode: {mode}")
    if mode == "sample":
        if n_eval is None:
            n_eval = 300
        rng = Random(seed)
        seen: set[tuple[int, ...]] = set()
        train = _draw_unique_permutations(n_logical, n_train, rng, seen)
        eval_perms = _draw_unique_permutations(n_logical, n_eval, rng, seen)
        return train, eval_perms

    if n_logical > 8:
        raise ValueError("exhaustive permutation split is limited to n_logical <= 8")
    all_perms = list(iter_permutations(range(n_logical)))
    if not 0 <= n_train <= len(all_perms):
        raise ValueError(f"n_train must be in [0, {len(all_perms)}], got {n_train}")
    rng = Random(seed)
    rng.shuffle(all_perms)
    if n_eval is not None:
        if not 0 <= n_eval <= len(all_perms) - n_train:
            raise ValueError(
                f"n_eval must be in [0, {len(all_perms) - n_train}], got {n_eval}"
            )
        return all_perms[:n_train], all_perms[n_train : n_train + n_eval]
    return all_perms[:n_train], all_perms[n_train:]


def sample_permutations(
    n_logical: int,
    k: int,
    seed: int,
) -> list[tuple[int, ...]]:
    rng = Random(seed)
    return _draw_unique_permutations(n_logical, k, rng, set())


def _draw_unique_permutations(
    n_logical: int,
    k: int,
    rng: Random,
    seen: set[tuple[int, ...]],
) -> list[tuple[int, ...]]:
    if k < 0:
        raise ValueError(f"k must be non-negative, got {k}")
    if n_logical <= 8 and len(seen) + k > factorial(n_logical):
        raise ValueError(
            f"requested {k} new permutations with {len(seen)} excluded, "
            f"but S_{n_logical} has only {factorial(n_logical)} permutations"
        )
    samples: list[tuple[int, ...]] = []
    while len(samples) < k:
        values = list(range(n_logical))
        rng.shuffle(values)
        permutation = tuple(values)
        if permutation in seen:
            continue
        seen.add(permutation)
        samples.append(permutation)
    return samples


def _evaluate_path(
    path: Path,
    all_paths: list[Path],
    cells: dict[str, MRRCell],
    alpha_db_per_um: float,
    xt_cross_db: float,
    topology: RNBTopology,
    crossing_loss_db_per_cross: float = 0.0,
) -> PathMetric:
    wiring_um = _path_wiring(path, cells)
    mrr_power = 1.0
    for step in path.steps:
        cell = cells[step.mrr_id]
        mrr_power *= cell.s_table[(step.in_port, step.out_port, step.state)]
    wiring_loss_db = alpha_db_per_um * wiring_um
    mrr_loss_db = -10.0 * log10(max(mrr_power, 1e-15))
    leak_power = _mrr_leak_power(path, all_paths, cells)
    if topology.has_native_crossings():
        leak_power += _path_crossing_leak(path, all_paths, cells, xt_cross_db)
    crossing_il_db = 0.0
    if topology.has_native_crossings() and crossing_loss_db_per_cross > 0.0:
        crossing_il_db = crossing_loss_db_per_cross * _count_path_crossings(
            path, all_paths, cells
        )
    signal_power = (10.0 ** (-(wiring_loss_db + crossing_il_db) / 10.0)) * mrr_power
    sxr_db = 10.0 * log10(signal_power / max(leak_power, 1e-15))
    return PathMetric(
        input_port=path.input_port,
        output_port=path.output_port,
        insertion_loss_db=wiring_loss_db + mrr_loss_db + crossing_il_db,
        sxr_db=sxr_db,
        signal_power=signal_power,
        leak_power=leak_power,
        wiring_um=wiring_um,
        mrr_loss_db=mrr_loss_db,
    )


def _evaluate_path_breakdown(
    path: Path,
    all_paths: list[Path],
    cells: dict[str, MRRCell],
    alpha_db_per_um: float,
    xt_cross_db: float,
    topology: RNBTopology,
    crossing_loss_db_per_cross: float = 0.0,
    *,
    sxr_min_db: float,
    layer: LayerName,
) -> PathLossBreakdown:
    wiring_um = _path_wiring(path, cells)
    mrr_power = 1.0
    for step in path.steps:
        cell = cells[step.mrr_id]
        mrr_power *= cell.s_table[(step.in_port, step.out_port, step.state)]

    propagation_loss_db = alpha_db_per_um * wiring_um
    mrr_loss_db = -10.0 * log10(max(mrr_power, 1e-15))
    bend_count = 0
    bend_loss_db = 0.0
    crossing_count = 0
    crossing_loss_db = 0.0
    leak_crossing_power = 0.0
    if topology.has_native_crossings():
        crossing_count = _count_path_crossings(path, all_paths, cells)
        crossing_loss_db = crossing_loss_db_per_cross * crossing_count
        leak_crossing_power = _path_crossing_leak(path, all_paths, cells, xt_cross_db)
    leak_mrr_power = _mrr_leak_power(path, all_paths, cells)
    leak_total_power = leak_mrr_power + leak_crossing_power
    total_insertion_loss_db = (
        mrr_loss_db + propagation_loss_db + bend_loss_db + crossing_loss_db
    )
    signal_power = (10.0 ** (-(propagation_loss_db + crossing_loss_db) / 10.0)) * mrr_power
    sxr_db = 10.0 * log10(signal_power / max(leak_total_power, 1e-15))
    return PathLossBreakdown(
        path_id=f"I{path.input_port}->O{path.output_port}",
        layer=layer,
        input_port=path.input_port,
        output_port=path.output_port,
        mrr_count_on_path=len(path.steps),
        mrr_loss_db=mrr_loss_db,
        waveguide_length_um=wiring_um,
        propagation_loss_db=propagation_loss_db,
        bend_count=bend_count,
        bend_loss_db=bend_loss_db,
        crossing_count=crossing_count,
        crossing_loss_db=crossing_loss_db,
        total_insertion_loss_db=total_insertion_loss_db,
        leak_mrr_power=leak_mrr_power,
        leak_crossing_power=leak_crossing_power,
        leak_total_power=leak_total_power,
        signal_power=signal_power,
        sxr_db=sxr_db,
        sxr_violation=sxr_db < sxr_min_db,
    )


def _path_wiring(
    path: Path,
    cells: dict[str, MRRCell],
    x_start: float = 20.0,
    x_end: float | None = None,
) -> float:
    if not path.steps:
        return 0.0
    if x_end is None:
        x_end = max(c.center[0] for c in cells.values()) + 70.0
    # first segment: input-port x → first MRR in_port
    # y-difference is zero because ports land on waveguide buses
    p_first = cells[path.steps[0].mrr_id].port_xy(path.steps[0].in_port)
    total = abs(p_first[0] - x_start)
    # inter-MRR segments
    for a, b in zip(path.steps, path.steps[1:]):
        p0 = cells[a.mrr_id].port_xy(a.out_port)
        p1 = cells[b.mrr_id].port_xy(b.in_port)
        total += abs(p0[0] - p1[0]) + abs(p0[1] - p1[1])
    # last segment: last MRR out_port → output-port x
    p_last = cells[path.steps[-1].mrr_id].port_xy(path.steps[-1].out_port)
    total += abs(x_end - p_last[0])
    return total


def _build_cells_with_centers(
    topology: RNBTopology,
    s_table: dict[tuple[str, str, int], float],
    centers: dict[str, tuple[float, float]] | None,
    *,
    cell_geometry: CellGeometry = DEFAULT_CELL_GEOMETRY,
) -> dict[str, MRRCell]:
    cells = build_cells(topology, s_table, cell_geometry=cell_geometry)
    if centers is None:
        return cells
    missing = set(cells) - set(centers)
    if missing:
        raise ValueError(f"centers missing {len(missing)} MRR(s), e.g. {sorted(missing)[:3]}")
    return {
        mrr_id: MRRCell(
            id=cell.id,
            center=centers[mrr_id],
            ports=cell.ports,
            s_table=cell.s_table,
            bbox=cell.bbox,
            d_min_th=cell.d_min_th,
        )
        for mrr_id, cell in cells.items()
    }


def _logsumexp_scaled(values: list[float], beta: float) -> float:
    shifted = [beta * value for value in values]
    max_shifted = max(shifted)
    return (max_shifted + log(sum(exp(value - max_shifted) for value in shifted))) / beta


def _mrr_leak_power(
    path: Path, all_paths: list[Path], cells: dict[str, MRRCell]
) -> float:
    leak = 0.0
    steps_by_mrr = {step.mrr_id: step for step in path.steps}
    for other in all_paths:
        if other.input_port == path.input_port:
            continue
        # track accumulated power of the interfering path as it traverses MRRs
        power_j = 1.0
        for other_step in other.steps:
            step = steps_by_mrr.get(other_step.mrr_id)
            if step is not None:
                cell = cells[step.mrr_id]
                leak += power_j * cell.s_table.get(
                    (other_step.in_port, step.out_port, step.state), 1e-12
                )
            power_j *= cells[other_step.mrr_id].s_table.get(
                (other_step.in_port, other_step.out_port, other_step.state), 1.0
            )
    return leak


def _count_path_crossings(
    path: Path,
    all_paths: list[Path],
    cells: dict[str, MRRCell],
) -> int:
    count = 0
    for other in all_paths:
        if other.input_port == path.input_port:
            continue
        for seg_a in _edge_segments(path, cells):
            for seg_b in _edge_segments(other, cells):
                if _segments_cross(seg_a, seg_b):
                    count += 1
    # The current schematic layout does not expand non-adjacent switch pairs
    # into physical access waveguides. Count the intermediate buses as a
    # conservative native-crossing proxy until full routed geometry exists.
    for step in path.steps:
        count += max(0, abs(step.pair[1] - step.pair[0]) - 1)
    return count


def _path_crossing_leak(
    path: Path,
    all_paths: list[Path],
    cells: dict[str, MRRCell],
    xt_cross_db: float,
) -> float:
    chi = 10.0 ** (xt_cross_db / 10.0)
    leak = 0.0
    for other in all_paths:
        if other.input_port == path.input_port:
            continue
        for seg_a in _edge_segments(path, cells):
            for seg_b in _edge_segments(other, cells):
                if _segments_cross(seg_a, seg_b):
                    leak += chi
    return leak


def _count_crossings(
    topology: RNBTopology, paths: list[Path], cells: dict[str, MRRCell]
) -> int:
    if not topology.has_native_crossings():
        return 0
    count = 0
    segments = [
        (path.input_port, segment)
        for path in paths
        for segment in _edge_segments(path, cells)
    ]
    for idx, (path_a, seg_a) in enumerate(segments):
        for path_b, seg_b in segments[idx + 1 :]:
            if path_a != path_b and _segments_cross(seg_a, seg_b):
                count += 1
    return count


def _edge_segments(
    path: Path, cells: dict[str, MRRCell]
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    return [
        (cells[a.mrr_id].port_xy(a.out_port), cells[b.mrr_id].port_xy(b.in_port))
        for a, b in zip(path.steps, path.steps[1:])
    ]


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
