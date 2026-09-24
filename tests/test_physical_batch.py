from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_bounded_physical_batch_records_failures_without_aborting(tmp_path: Path) -> None:
    outdir = tmp_path / "physical_batch"
    command = [
        sys.executable,
        "main.py",
        "--physical-batch",
        "--physical-n-values",
        "4",
        "--physical-batch-samples",
        "1",
        "--topology",
        "all",
        "--max-astar-pops",
        "1",
        "--max-ripup-passes",
        "0",
        "--outdir",
        str(outdir),
    ]
    subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    summary_path = outdir / "physical_batch" / "physical_summary.csv"
    paths_path = outdir / "physical_batch" / "physical_path_distribution.csv"
    drc_path = outdir / "physical_batch" / "drc_violations.csv"
    assert summary_path.exists()
    assert paths_path.exists()
    assert drc_path.exists()

    with summary_path.open(newline="") as f:
        summary_rows = list(csv.DictReader(f))
    assert len(summary_rows) == 3
    assert {row["n_logical"] for row in summary_rows} == {"4"}
    assert {row["layer"] for row in summary_rows} == {"physical"}
    assert {row["seed"] for row in summary_rows} == {"42"}
    assert {row["ripup_passes"] for row in summary_rows} == {"0"}
    assert all("runtime_s" in row and float(row["runtime_s"]) >= 0.0 for row in summary_rows)
    assert all(row["astar_calls"] != "" for row in summary_rows)
    assert all("failure_rate" in row for row in summary_rows)

    with paths_path.open(newline="") as f:
        path_rows = list(csv.DictReader(f))
    assert path_rows
    assert {row["n_logical"] for row in path_rows} == {"4"}
    assert {row["layer"] for row in path_rows} == {"physical"}


def test_physical_batch_attempts_large_n_with_raised_limit(tmp_path: Path) -> None:
    outdir = tmp_path / "large_n"
    subprocess.run(
        [
            sys.executable,
            "main.py",
            "--physical-batch",
            "--physical-n-values",
            "9",
            "--physical-batch-samples",
            "1",
            "--topology",
            "waksman",
            "--max-physical-n",
            "16",
            "--max-astar-pops",
            "1",
            "--max-ripup-passes",
            "0",
            "--outdir",
            str(outdir),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    with (outdir / "physical_batch" / "physical_summary.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["n_logical"] == "9"
    # Large-N failures must be recorded as data rows, never crash the batch.
    assert rows[0]["route_status"] in {"routed", "failed", "error", "drc_violation"}
    assert rows[0]["failure_rate"] != ""


def test_physical_batch_full_enum_routes_all_permutations(tmp_path: Path) -> None:
    outdir = tmp_path / "full_enum"
    subprocess.run(
        [
            sys.executable,
            "main.py",
            "--physical-batch",
            "--physical-n-values",
            "3",
            "--physical-batch-samples",
            "1",
            "--physical-full-enum-max-n",
            "3",
            "--topology",
            "waksman",
            "--max-astar-pops",
            "1",
            "--max-ripup-passes",
            "0",
            "--outdir",
            str(outdir),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    with (outdir / "physical_batch" / "physical_summary.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    # 3! = 6 unique permutations, all attempted despite --physical-batch-samples 1.
    assert len(rows) == 6
    assert len({row["permutation"] for row in rows}) == 6


def test_physical_batch_topology_main_selects_benes_and_waksman(tmp_path: Path) -> None:
    outdir = tmp_path / "main_pair"
    subprocess.run(
        [
            sys.executable,
            "main.py",
            "--physical-batch",
            "--physical-n-values",
            "4",
            "--physical-batch-samples",
            "1",
            "--topology",
            "main",
            "--max-astar-pops",
            "1",
            "--max-ripup-passes",
            "0",
            "--outdir",
            str(outdir),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    with (outdir / "physical_batch" / "physical_summary.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert {row["topology"] for row in rows} == {
        "padded_benes_4x4",
        "waksman_4x4",
    }


def test_worst_il_summary_only_counts_fully_routed_attempts() -> None:
    from mrr_switch_optimizer.analysis.paper_figures import (
        _physical_summary_by_cell,
        worst_il_attempt_by_cell,
    )

    rows = [
        {
            "topology": "waksman_4x4",
            "n_logical": "4",
            "route_status": "routed",
            "permutation": "1-0-3-2",
            "physical_worst_il_db": "4.0",
            "failure_rate": "0.0",
            "runtime_s": "1.0",
        },
        {
            "topology": "waksman_4x4",
            "n_logical": "4",
            "route_status": "routed",
            "permutation": "2-3-0-1",
            "physical_worst_il_db": "5.5",
            "failure_rate": "0.0",
            "runtime_s": "1.0",
        },
        {
            "topology": "waksman_4x4",
            "n_logical": "4",
            "route_status": "failed",
            "permutation": "3-2-1-0",
            "physical_worst_il_db": "9.9",
            "failure_rate": "0.25",
            "runtime_s": "1.0",
        },
    ]
    summary = _physical_summary_by_cell(rows)
    cell = summary[("waksman_4x4", "4")]
    # The failed attempt's 9.9 dB must not leak into the worst-IL statistic.
    assert cell["worst_il_db"] == "5.500000"
    assert cell["attempts"] == "3"
    assert cell["fully_routed_attempts"] == "2"
    assert cell["fully_routed_rate"] == "0.666667"

    argmax = worst_il_attempt_by_cell(rows)
    assert argmax[("waksman_4x4", "4")]["permutation"] == "2-3-0-1"


def test_physical_batch_rejects_n_above_boundary(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "main.py",
            "--physical-batch",
            "--physical-n-values",
            "16",
            "--physical-batch-samples",
            "1",
            "--outdir",
            str(tmp_path / "bad"),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "only supports n_logical <= 8" in result.stderr
