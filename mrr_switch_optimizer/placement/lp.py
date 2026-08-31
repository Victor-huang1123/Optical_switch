from __future__ import annotations

import numpy as np
import numpy.typing as npt
from scipy.optimize import linprog  # type: ignore[import-untyped]

from ..core.models import MRRCell
from .layout import build_cells
from ..core.topology import RNBTopology

Placement = dict[str, tuple[float, float]]


def lp_placement(
    topology: RNBTopology,
    s_table: dict[tuple[str, str, int], float],
    train_perms: list[tuple[int, ...]],
    *,
    safety_um: float = 40.0,
) -> Placement:
    """Exact L1-minimum wiring placement via LP.

    Since Y is fixed by the bus constraint, the only free variable per MRR is
    its X coordinate.  Inter-MRR wiring length decomposes as:

        |x_a + dx_a_out  -  x_b - dx_b_in|  +  |Δy|  (Δy is constant)

    so the objective is a pure L1 function of the X variables — convex and
    solvable by LP without any binary decisions.

    Each stage's X is bounded to ±safety_um of its nominal position so that
    cross-stage bbox overlaps are geometrically impossible (stage pitch 105 μm
    >> bbox width 28 μm with safety_um ≤ 52 μm).
    """
    cells = build_cells(topology, s_table)
    mrr_ids = sorted(cells)
    n = len(mrr_ids)
    mrr_idx = {mid: i for i, mid in enumerate(mrr_ids)}
    y_fixed = {mid: cells[mid].center[1] for mid in mrr_ids}

    stage_nominal_x = _stage_nominal_x(topology, cells)
    x_bounds = _per_mrr_x_bounds(topology, cells, mrr_ids, stage_nominal_x, safety_um)

    wiring_terms = _collect_wiring_terms(topology, train_perms, cells, mrr_idx)
    sep_constraints = _cross_stage_sep_constraints(topology, cells, mrr_ids, mrr_idx)

    # Never-active MRRs (not on any active path) must stay at their default x.
    # Their position affects x_end = max(centers) + 70 used by _path_wiring,
    # so letting LP move them freely corrupts wiring-length evaluation for all paths.
    active_indices = {i for i, j, _ in wiring_terms} | {j for i, j, _ in wiring_terms}
    for k, mid in enumerate(mrr_ids):
        if k not in active_indices:
            default_x = cells[mid].center[0]
            x_bounds[k] = (default_x, default_x)

    m = len(wiring_terms)

    # LP variables: [x_0 … x_{n-1},  t_0 … t_{m-1}]
    # Objective: min Σ t_k  (each t_k = |x_i - x_j + c_k|)
    c_obj = np.zeros(n + m)
    c_obj[n:] = 1.0

    # Inequality constraints encoding |x_i - x_j + c| ≤ t_k:
    #   x_i - x_j - t_k ≤ -c   (row A)
    #  -x_i + x_j - t_k ≤  c   (row B)
    A_rows: list[npt.NDArray[np.float64]] = []
    b_rhs: list[float] = []
    for k, (i, j, c) in enumerate(wiring_terms):
        rA = np.zeros(n + m); rA[i] = 1.0; rA[j] = -1.0; rA[n + k] = -1.0
        A_rows.append(rA); b_rhs.append(-c)
        rB = np.zeros(n + m); rB[i] = -1.0; rB[j] = 1.0; rB[n + k] = -1.0
        A_rows.append(rB); b_rhs.append(c)

    # Cross-stage bbox separation: x_right - x_left >= min_sep
    # encoded as x_left - x_right <= -min_sep
    for (left_i, right_i, min_sep) in sep_constraints:
        r = np.zeros(n + m); r[left_i] = 1.0; r[right_i] = -1.0
        A_rows.append(r); b_rhs.append(-min_sep)

    A_ub = np.array(A_rows) if A_rows else np.empty((0, n + m))
    b_ub = np.array(b_rhs) if b_rhs else np.empty(0)
    all_bounds = x_bounds + [(0.0, None)] * m

    result = linprog(c_obj, A_ub=A_ub, b_ub=b_ub, bounds=all_bounds, method="highs")
    if result.status != 0:
        raise ValueError(f"LP placement infeasible: {result.message}")

    x_sol = result.x[:n]
    return {mid: (float(x_sol[mrr_idx[mid]]), y_fixed[mid]) for mid in mrr_ids}


# ─── helpers ─────────────────────────────────────────────────────────────────


def _stage_nominal_x(
    topology: RNBTopology,
    cells: dict[str, MRRCell],
) -> dict[int, float]:
    nominal: dict[int, float] = {}
    for mid, stage, _ in topology.iter_mrrs():
        nominal.setdefault(stage, cells[mid].center[0])
    return nominal


def _per_mrr_x_bounds(
    topology: RNBTopology,
    cells: dict[str, MRRCell],
    mrr_ids: list[str],
    stage_nominal_x: dict[int, float],
    safety_um: float,
) -> list[tuple[float, float]]:
    stage_by_mrr = {mid: stage for mid, stage, _ in topology.iter_mrrs()}
    bounds = []
    for mid in mrr_ids:
        nom = stage_nominal_x[stage_by_mrr[mid]]
        half_w = 0.5 * cells[mid].bbox.width
        lo = max(35.0 + half_w, nom - safety_um)
        hi = min(85.0 + (topology.n_stages - 1) * 105.0 + 85.0 - half_w, nom + safety_um)
        bounds.append((lo, hi))
    return bounds


def _cross_stage_sep_constraints(
    topology: RNBTopology,
    cells: dict[str, MRRCell],
    mrr_ids: list[str],
    mrr_idx: dict[str, int],
) -> list[tuple[int, int, float]]:
    """Return (left_idx, right_idx, min_sep) for cross-stage y-overlapping pairs.

    For MRRs from adjacent stages that overlap in y, the LP must ensure the
    left-stage MRR stays to the left with at least min_sep separation.
    """
    stage_by_mrr = {mid: stage for mid, stage, _ in topology.iter_mrrs()}
    constraints = []
    for ia, mid_a in enumerate(mrr_ids):
        for ib, mid_b in enumerate(mrr_ids):
            if ib <= ia:
                continue
            s_a, s_b = stage_by_mrr[mid_a], stage_by_mrr[mid_b]
            if s_a == s_b:
                continue  # within-stage: already handled by x_bounds + no y-overlap
            ca, cb = cells[mid_a], cells[mid_b]
            half_h = 0.5 * (ca.bbox.height + cb.bbox.height)
            if abs(ca.center[1] - cb.center[1]) >= half_h:
                continue  # no y-overlap, no x constraint needed
            min_sep = 0.5 * (ca.bbox.width + cb.bbox.width)
            # left = smaller nominal x (lower stage index)
            if s_a < s_b:
                constraints.append((ia, ib, min_sep))
            else:
                constraints.append((ib, ia, min_sep))
    return constraints


def _collect_wiring_terms(
    topology: RNBTopology,
    train_perms: list[tuple[int, ...]],
    cells: dict[str, MRRCell],
    mrr_idx: dict[str, int],
) -> list[tuple[int, int, float]]:
    """Return (i, j, c) for each |x_i - x_j + c| term in the wiring objective."""
    terms: list[tuple[int, int, float]] = []
    for permutation in train_perms:
        states = topology.get_state_assignment(permutation)
        paths = topology.get_active_paths(permutation, states)
        for path in paths:
            for a, b in zip(path.steps, path.steps[1:]):
                dx_a = cells[a.mrr_id].ports[a.out_port].dx
                dx_b = cells[b.mrr_id].ports[b.in_port].dx
                terms.append((mrr_idx[a.mrr_id], mrr_idx[b.mrr_id], dx_a - dx_b))
    return terms
