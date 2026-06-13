from __future__ import annotations

from math import exp
from random import Random

# incremental SA — intentional private access
from .cost import _build_cells_with_centers, _evaluate_path
from .layout import build_cells
from .models import MRRCell
from .topology import Path, RNBTopology

Placement = dict[str, tuple[float, float]]


def sa_placement(
    topology: RNBTopology,
    s_table: dict[tuple[str, str, int], float],
    train_perms: list[tuple[int, ...]],
    *,
    T_init: float = 1.0,
    T_min: float = 1e-4,
    cooling: float = 0.995,
    moves_per_temp: int = 50,
    seed: int = 0,
    omega_il: float = 1.0,
    omega_xt: float = 1.0,
    sxr_min_db: float = 20.0,
) -> Placement:
    rng = Random(seed)
    default_cells = build_cells(topology, s_table)
    stage_by_mrr = {mrr_id: stage for mrr_id, stage, _ in topology.iter_mrrs()}
    mrr_ids = list(default_cells)
    centers: Placement = {mrr_id: cell.center for mrr_id, cell in default_cells.items()}
    best_centers = dict(centers)
    if not is_feasible_placement(topology, s_table, centers):
        raise ValueError("initial placement violates hard constraints")
    if not 0.0 < cooling < 1.0:
        raise ValueError("cooling must be in (0, 1)")
    if T_init <= T_min or T_min <= 0.0:
        raise ValueError("temperature schedule requires T_init > T_min > 0")

    beta_path = 3.0
    beta_perm = 2.0
    alpha_db_per_um = 0.002
    xt_cross_db = -40.0
    mrr_to_permpaths, paths_by_perm, path_c, perm_z = _build_sa_state(
        topology,
        train_perms,
        s_table,
        centers,
        beta_path,
        beta_perm,
        alpha_db_per_um,
        xt_cross_db,
        omega_il,
        omega_xt,
        sxr_min_db,
    )
    current_cost = _cost_from_perm_z(perm_z, beta_path, beta_perm)
    best_cost = current_cost

    T = T_init
    while T > T_min:
        for _ in range(moves_per_temp):
            candidate = _propose_move(rng, centers, mrr_ids, stage_by_mrr)
            if not is_feasible_placement(topology, s_table, candidate):
                continue
            moved = {mrr_id for mrr_id in mrr_ids if centers[mrr_id] != candidate[mrr_id]}
            cand_path_c = dict(path_c)
            cand_perm_z = dict(perm_z)
            _incremental_update(
                candidate,
                moved,
                mrr_to_permpaths,
                paths_by_perm,
                cand_path_c,
                cand_perm_z,
                topology,
                s_table,
                train_perms,
                beta_path,
                alpha_db_per_um,
                xt_cross_db,
                omega_il,
                omega_xt,
                sxr_min_db,
            )
            candidate_cost = _cost_from_perm_z(cand_perm_z, beta_path, beta_perm)
            delta = candidate_cost - current_cost
            if delta < 0.0 or rng.random() < exp(-delta / T):
                centers = candidate
                path_c = cand_path_c
                perm_z = cand_perm_z
                current_cost = candidate_cost
                if candidate_cost < best_cost:
                    best_centers = dict(candidate)
                    best_cost = candidate_cost
        T *= cooling
    return best_centers


def _build_sa_state(
    topology: RNBTopology,
    train_perms: list[tuple[int, ...]],
    s_table: dict[tuple[str, str, int], float],
    centers: Placement,
    beta_path: float,
    beta_perm: float,
    alpha_db_per_um: float,
    xt_cross_db: float,
    omega_il: float,
    omega_xt: float,
    sxr_min_db: float,
) -> tuple[
    dict[str, list[tuple[int, int]]],
    dict[int, list[Path]],
    dict[tuple[int, int], float],
    dict[int, float],
]:
    _ = beta_perm
    cells = _build_cells_with_centers(topology, s_table, centers)
    mrr_to_permpaths: dict[str, list[tuple[int, int]]] = {}
    paths_by_perm: dict[int, list[Path]] = {}
    path_c: dict[tuple[int, int], float] = {}
    perm_z: dict[int, float] = {}
    for perm_idx, permutation in enumerate(train_perms):
        states = topology.get_state_assignment(permutation)
        paths = topology.get_active_paths(permutation, states)
        paths_by_perm[perm_idx] = paths
        z = 0.0
        for path in paths:
            metric = _evaluate_path(path, paths, cells, alpha_db_per_um, xt_cross_db, topology)
            c_path = (
                omega_il * metric.insertion_loss_db
                + omega_xt * max(0.0, sxr_min_db - metric.sxr_db)
            )
            key = (perm_idx, path.input_port)
            path_c[key] = c_path
            z += exp(beta_path * c_path)
            for step in path.steps:
                mrr_to_permpaths.setdefault(step.mrr_id, []).append(key)
        perm_z[perm_idx] = z
    return mrr_to_permpaths, paths_by_perm, path_c, perm_z


def _cost_from_perm_z(
    perm_z: dict[int, float],
    beta_path: float,
    beta_perm: float,
) -> float:
    from math import log

    from .cost import _logsumexp_scaled

    perm_costs = [(1.0 / beta_path) * log(max(z, 1e-300)) for z in perm_z.values()]
    return _logsumexp_scaled(perm_costs, beta_perm)


def _incremental_update(
    candidate: Placement,
    moved_mrrs: set[str],
    mrr_to_permpaths: dict[str, list[tuple[int, int]]],
    paths_by_perm: dict[int, list[Path]],
    path_c: dict[tuple[int, int], float],
    perm_z: dict[int, float],
    topology: RNBTopology,
    s_table: dict[tuple[str, str, int], float],
    train_perms: list[tuple[int, ...]],
    beta_path: float,
    alpha_db_per_um: float,
    xt_cross_db: float,
    omega_il: float,
    omega_xt: float,
    sxr_min_db: float,
) -> None:
    _ = train_perms
    affected_perms: set[int] = set()
    for mrr_id in moved_mrrs:
        for perm_idx, _input_port in mrr_to_permpaths.get(mrr_id, []):
            affected_perms.add(perm_idx)

    cells = _build_cells_with_centers(topology, s_table, candidate)
    for perm_idx in affected_perms:
        paths = paths_by_perm[perm_idx]
        for path in paths:
            key = (perm_idx, path.input_port)
            old_c = path_c[key]
            metric = _evaluate_path(path, paths, cells, alpha_db_per_um, xt_cross_db, topology)
            new_c = (
                omega_il * metric.insertion_loss_db
                + omega_xt * max(0.0, sxr_min_db - metric.sxr_db)
            )
            perm_z[perm_idx] -= exp(beta_path * old_c)
            perm_z[perm_idx] += exp(beta_path * new_c)
            path_c[key] = new_c


def is_feasible_placement(
    topology: RNBTopology,
    s_table: dict[tuple[str, str, int], float],
    centers: Placement,
) -> bool:
    cells = build_cells(topology, s_table)
    if set(cells) != set(centers):
        return False
    if _has_y_bus_violation(topology, centers):
        return False
    placed = {
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
    if _has_x_floorplan_violation(topology, placed):
        return False
    if _has_stage_thermal_violation(topology, placed):
        return False
    return not _has_bbox_overlap(placed)


def _has_y_bus_violation(
    topology: RNBTopology,
    centers: Placement,
    wire_pitch_um: float = 36.0,
    tol_um: float = 1e-6,
) -> bool:
    n_phys = topology.N_physical
    for mrr_id, _stage, pair in topology.iter_mrrs():
        expected_y = 0.5 * (
            (n_phys - 1 - pair[0]) + (n_phys - 1 - pair[1])
        ) * wire_pitch_um
        if abs(centers[mrr_id][1] - expected_y) > tol_um:
            return True
    return False


def _has_x_floorplan_violation(
    topology: RNBTopology,
    cells: dict[str, MRRCell],
    x_min_um: float = 35.0,
    right_margin_um: float = 85.0,
) -> bool:
    x_max_um = 85.0 + (topology.n_stages - 1) * 105.0 + right_margin_um
    for cell in cells.values():
        half_width = 0.5 * cell.bbox.width
        if cell.center[0] - half_width < x_min_um:
            return True
        if cell.center[0] + half_width > x_max_um:
            return True
    return False


def _propose_move(
    rng: Random,
    centers: Placement,
    mrr_ids: list[str],
    stage_by_mrr: dict[str, int],
) -> Placement:
    move = rng.choice(("translate", "swap_x", "swap_stage", "local_spreading", "stage_shift"))
    candidate = dict(centers)
    stages = _mrrs_by_stage(mrr_ids, stage_by_mrr)

    if move == "translate":
        mrr_id = rng.choice(mrr_ids)
        x, y = candidate[mrr_id]
        dx = rng.choice((-24.0, -12.0, 12.0, 24.0))
        candidate[mrr_id] = (x + dx, y)
    elif move == "swap_x":
        valid_stages = [ids for ids in stages.values() if len(ids) >= 2]
        if not valid_stages:
            return candidate
        a, b = rng.sample(rng.choice(valid_stages), 2)
        ax, ay = candidate[a]
        bx, by = candidate[b]
        candidate[a] = (bx, ay)
        candidate[b] = (ax, by)
    elif move == "swap_stage":
        if len(stages) < 2:
            return candidate
        stage_a, stage_b = rng.sample(list(stages), 2)
        _swap_stage_x(candidate, stages[stage_a], stages[stage_b])
    elif move == "local_spreading":
        mrr_id = rng.choice(mrr_ids)
        cx, cy = candidate[mrr_id]
        spread_radius_um = 72.0
        for other_id, (ox, oy) in centers.items():
            if other_id == mrr_id:
                continue
            if abs(ox - cx) < spread_radius_um and abs(ox - cx) > 0.0:
                dx_sign = 1.0 if ox > cx else -1.0
                candidate[other_id] = (ox + dx_sign * 12.0, oy)
    elif move == "stage_shift":
        ids = stages[rng.choice(list(stages))]
        dx = rng.choice((-24.0, -12.0, 12.0, 24.0))
        for mrr_id in ids:
            x, y = candidate[mrr_id]
            candidate[mrr_id] = (x + dx, y)
    return candidate


def _mrrs_by_stage(
    mrr_ids: list[str],
    stage_by_mrr: dict[str, int],
) -> dict[int, list[str]]:
    stages: dict[int, list[str]] = {}
    for mrr_id in mrr_ids:
        stages.setdefault(stage_by_mrr[mrr_id], []).append(mrr_id)
    return stages


def _swap_stage_x(candidate: Placement, stage_a: list[str], stage_b: list[str]) -> None:
    center_a = sum(candidate[mrr_id][0] for mrr_id in stage_a) / len(stage_a)
    center_b = sum(candidate[mrr_id][0] for mrr_id in stage_b) / len(stage_b)
    for mrr_id in stage_a:
        x, y = candidate[mrr_id]
        candidate[mrr_id] = (center_b + (x - center_a), y)
    for mrr_id in stage_b:
        x, y = candidate[mrr_id]
        candidate[mrr_id] = (center_a + (x - center_b), y)


def _has_stage_thermal_violation(
    topology: RNBTopology,
    cells: dict[str, MRRCell],
) -> bool:
    by_stage: dict[int, list[MRRCell]] = {}
    for mrr_id, stage_idx, _ in topology.iter_mrrs():
        by_stage.setdefault(stage_idx, []).append(cells[mrr_id])
    for stage_cells in by_stage.values():
        for idx, cell_a in enumerate(stage_cells):
            for cell_b in stage_cells[idx + 1 :]:
                d_min = max(cell_a.d_min_th, cell_b.d_min_th)
                if abs(cell_a.center[1] - cell_b.center[1]) < d_min:
                    return True
    return False


def _has_bbox_overlap(cells: dict[str, MRRCell]) -> bool:
    cell_list = list(cells.values())
    for idx, cell_a in enumerate(cell_list):
        for cell_b in cell_list[idx + 1 :]:
            if _bbox_intersects(cell_a, cell_b):
                return True
    return False


def _bbox_intersects(cell_a: MRRCell, cell_b: MRRCell) -> bool:
    half_width = 0.5 * (cell_a.bbox.width + cell_b.bbox.width)
    half_height = 0.5 * (cell_a.bbox.height + cell_b.bbox.height)
    return (
        abs(cell_a.center[0] - cell_b.center[0]) < half_width
        and abs(cell_a.center[1] - cell_b.center[1]) < half_height
    )
