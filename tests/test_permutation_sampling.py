from __future__ import annotations

import subprocess
import sys
from math import factorial
from pathlib import Path

import pytest

from mrr_switch_optimizer.analysis.cost import make_permutation_split, sample_permutations


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_exhaustive_n6_split_keeps_full_seeded_partition() -> None:
    train, eval_perms = make_permutation_split(6, n_train=500, seed=42)
    assert len(train) == 500
    assert len(eval_perms) == factorial(6) - 500
    assert len(set(train).intersection(eval_perms)) == 0
    assert set(train).union(eval_perms) == set(sample_permutations(6, factorial(6), seed=99))


def test_sample_permutations_are_deterministic_and_unique() -> None:
    first = sample_permutations(16, 300, seed=42)
    second = sample_permutations(16, 300, seed=42)
    assert first == second
    assert len(first) == 300
    assert len(set(first)) == 300


def test_sample_split_is_disjoint() -> None:
    train, eval_perms = make_permutation_split(
        16,
        n_train=500,
        seed=42,
        mode="sample",
        n_eval=300,
    )
    assert len(train) == 500
    assert len(eval_perms) == 300
    assert len(set(train).intersection(eval_perms)) == 0


def test_exhaustive_split_rejects_large_n() -> None:
    with pytest.raises(ValueError, match="n_logical <= 8"):
        make_permutation_split(16, n_train=500, seed=42, mode="exhaustive")


def test_cli_waksman_n8_sample_milestone(tmp_path: Path) -> None:
    outdir = tmp_path / "waksman_n8"
    subprocess.run(
        [
            sys.executable,
            "main.py",
            "--topology",
            "waksman",
            "--n-logical",
            "8",
            "--permutation",
            "7,6,5,4,3,2,1,0",
            "--eval-mode",
            "sample",
            "--outdir",
            str(outdir),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (outdir / "routing_summary.json").exists()
    assert (outdir / "routing_comparison.csv").exists()
    assert (outdir / "waksman_8x8_7-6-5-4-3-2-1-0_states.csv").exists()
