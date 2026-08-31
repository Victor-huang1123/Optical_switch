from __future__ import annotations

import csv
import subprocess
import sys
from math import isclose
from pathlib import Path

from mrr_switch_optimizer.analysis.cost import (
    aggregate_cost,
    aggregate_cost_for_plans,
    build_evaluation_dataset,
    build_permutation_plan,
    evaluate_path_breakdowns,
    evaluate_plan,
    evaluate_routing,
    make_permutation_split,
)
from mrr_switch_optimizer.core.sparams import MOCK_S_TABLE
from mrr_switch_optimizer.core.state_assignment import (
    BenesLoopingStrategy,
    SpankeBenesStrategy,
    WaksmanStrategy,
)
from mrr_switch_optimizer.core.topology import (
    PaddedBenesTopology,
    SpankeBenesTopology,
    WaksmanTopology,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_plan_evaluation_matches_uncached_evaluation() -> None:
    topology = WaksmanTopology()
    permutation = (2, 0, 5, 1, 3, 4)
    plan = build_permutation_plan(topology, permutation)
    cached = evaluate_plan(topology, plan, MOCK_S_TABLE)
    uncached = evaluate_routing(topology, permutation, MOCK_S_TABLE)
    assert cached == uncached


def test_aggregate_cost_for_plans_matches_uncached_cost() -> None:
    topology = PaddedBenesTopology()
    permutations = [(2, 0, 5, 1, 3, 4), (0, 1, 2, 3, 4, 5)]
    plans = tuple(build_permutation_plan(topology, permutation) for permutation in permutations)
    cached = aggregate_cost_for_plans(topology, plans, MOCK_S_TABLE)
    uncached = aggregate_cost(topology, permutations, MOCK_S_TABLE)
    assert isclose(cached, uncached, rel_tol=0.0, abs_tol=1e-12)


def test_evaluation_dataset_deduplicates_train_eval_plans() -> None:
    topology = WaksmanTopology()
    permutation = (2, 0, 5, 1, 3, 4)
    dataset = build_evaluation_dataset(topology, [permutation], [permutation])
    assert dataset.train_plans[0] is dataset.eval_plans[0]


def test_logical_breakdown_sum_invariant() -> None:
    topology = PaddedBenesTopology()
    plan = build_permutation_plan(topology, (2, 0, 5, 1, 3, 4))
    for breakdown in evaluate_path_breakdowns(topology, plan, MOCK_S_TABLE):
        parts = (
            breakdown.mrr_loss_db
            + breakdown.propagation_loss_db
            + breakdown.bend_loss_db
            + breakdown.crossing_loss_db
        )
        assert isclose(breakdown.total_insertion_loss_db, parts, rel_tol=0.0, abs_tol=1e-12)
        assert breakdown.mrr_count_on_path >= 1
        assert breakdown.waveguide_length_um >= 0.0


def test_spanke_logical_breakdown_has_zero_crossing_leak() -> None:
    topology = SpankeBenesTopology(strategy=SpankeBenesStrategy())
    plan = build_permutation_plan(topology, (2, 0, 5, 1, 3, 4))
    for breakdown in evaluate_path_breakdowns(topology, plan, MOCK_S_TABLE):
        assert breakdown.crossing_count == 0
        assert breakdown.crossing_loss_db == 0.0
        assert breakdown.leak_crossing_power == 0.0


def test_n16_sampled_logical_eval_smoke() -> None:
    topologies = [
        PaddedBenesTopology(16, strategy=BenesLoopingStrategy()),
        WaksmanTopology(16, strategy=WaksmanStrategy()),
        SpankeBenesTopology(16, strategy=SpankeBenesStrategy()),
    ]
    train, eval_perms = make_permutation_split(
        16,
        n_train=5,
        seed=42,
        mode="sample",
        n_eval=3,
    )
    for topology in topologies:
        dataset = build_evaluation_dataset(topology, train, eval_perms)
        metrics = [evaluate_plan(topology, plan, MOCK_S_TABLE) for plan in dataset.eval_plans]
        assert len(metrics) == 3
        assert all(metric.topology == topology.name for metric in metrics)
        assert all(metric.max_path_depth >= metric.min_path_depth for metric in metrics)


def test_cli_logical_matrix_smoke(tmp_path: Path) -> None:
    outdir = tmp_path / "matrix"
    subprocess.run(
        [
            sys.executable,
            "main.py",
            "--logical-matrix",
            "--matrix-n-values",
            "4,8",
            "--matrix-train-samples",
            "2",
            "--matrix-eval-samples",
            "2",
            "--outdir",
            str(outdir),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    summary_path = outdir / "logical_matrix" / "logical_matrix_summary.csv"
    assert summary_path.exists()
    with summary_path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 6
    assert {row["n_logical"] for row in rows} == {"4", "8"}
    assert {row["layer"] for row in rows} == {"logical"}
    assert {row["seed"] for row in rows} == {"42"}
    assert (
        outdir
        / "logical_matrix"
        / "waksman_8x8_n8"
        / "eval_path_distribution.csv"
    ).exists()
