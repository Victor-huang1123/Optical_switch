from __future__ import annotations

from dataclasses import dataclass
from itertools import permutations

from .topology import RNBTopology


@dataclass(frozen=True)
class MRRActivity:
    topology: str
    mrr_id: str
    stage: int
    pair: tuple[int, int]
    permutations: int
    state0_count: int
    state1_count: int
    active_path_visits: int
    active_state0_visits: int
    active_state1_visits: int
    classification: str


def scan_mrr_activity(topology: RNBTopology) -> list[MRRActivity]:
    """Scan all S_N permutations and classify MRR control/activity roles."""
    mrr_meta = {
        mrr_id: (stage, pair)
        for mrr_id, stage, pair in topology.iter_mrrs()
    }
    state_counts = {mrr_id: {0: 0, 1: 0} for mrr_id in mrr_meta}
    active_visits = {mrr_id: 0 for mrr_id in mrr_meta}
    active_state_counts = {mrr_id: {0: 0, 1: 0} for mrr_id in mrr_meta}
    all_perms = list(permutations(range(topology.N_logical)))

    for permutation in all_perms:
        states = topology.get_state_assignment(permutation)
        for mrr_id, state in states.items():
            state_counts[mrr_id][state] += 1
        for path in topology.get_active_paths(permutation, states):
            for step in path.steps:
                active_visits[step.mrr_id] += 1
                active_state_counts[step.mrr_id][step.state] += 1

    rows = []
    for mrr_id in sorted(mrr_meta):
        stage, pair = mrr_meta[mrr_id]
        rows.append(
            MRRActivity(
                topology=topology.name,
                mrr_id=mrr_id,
                stage=stage,
                pair=pair,
                permutations=len(all_perms),
                state0_count=state_counts[mrr_id][0],
                state1_count=state_counts[mrr_id][1],
                active_path_visits=active_visits[mrr_id],
                active_state0_visits=active_state_counts[mrr_id][0],
                active_state1_visits=active_state_counts[mrr_id][1],
                classification=_classify_activity(
                    state_counts[mrr_id][0],
                    state_counts[mrr_id][1],
                    active_visits[mrr_id],
                ),
            )
        )
    return rows


def summarize_mrr_activity(rows: list[MRRActivity]) -> dict[str, object]:
    if not rows:
        raise ValueError("cannot summarize empty MRR activity rows")
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.classification] = counts.get(row.classification, 0) + 1
    total_mrr = len(rows)
    never_active = counts.get("never_active", 0)
    passive_through = counts.get("passive_through_candidate", 0)
    always_on = counts.get("always_on_candidate", 0)
    tunable = counts.get("tunable", 0)
    return {
        "topology": rows[0].topology,
        "total_mrr": total_mrr,
        "never_active": never_active,
        "passive_through_candidate": passive_through,
        "always_on_candidate": always_on,
        "tunable": tunable,
        "active_physical_after_removing_never_active": total_mrr - never_active,
        "tunable_after_passive_replacement": tunable,
        "passive_replacement_candidates": passive_through + always_on,
    }


def _classify_activity(
    state0_count: int,
    state1_count: int,
    active_path_visits: int,
) -> str:
    if active_path_visits == 0:
        return "never_active"
    if state1_count == 0:
        return "passive_through_candidate"
    if state0_count == 0:
        return "always_on_candidate"
    return "tunable"
