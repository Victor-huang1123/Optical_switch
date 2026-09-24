from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

from mrr_switch_optimizer.analysis.calibration import (
    CalibrationExample,
    calibrate_examples,
    kendall_tau,
    write_calibration_report,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_kendall_tau_orders_rankings() -> None:
    assert kendall_tau([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == 1.0
    assert kendall_tau([3.0, 2.0, 1.0], [1.0, 2.0, 3.0]) == -1.0


def test_ridge_residual_calibration_improves_synthetic_mae() -> None:
    examples = [
        CalibrationExample(
            topology="synthetic",
            n_logical=4,
            analytic_prediction=float(x),
            physical_target=float(x) + 2.0 + 0.5 * float(x),
            features=(1.0, float(x)),
        )
        for x in range(12)
    ]
    rows = calibrate_examples(examples, ridge_lambda=1e-9, holdout_fraction=0.25, seed=1)
    assert len(rows) == 1
    row = rows[0]
    assert row.mae_calibrated < row.mae_analytic


def test_write_calibration_report(tmp_path: Path) -> None:
    examples = [
        CalibrationExample(
            topology="synthetic",
            n_logical=4,
            analytic_prediction=float(x),
            physical_target=float(x) + 1.0,
            features=(1.0, float(x)),
        )
        for x in range(8)
    ]
    rows = calibrate_examples(examples, ridge_lambda=1e-9, seed=2)
    report_path = tmp_path / "calibration_report.csv"
    write_calibration_report(report_path, rows)
    with report_path.open(newline="") as f:
        report_rows = list(csv.DictReader(f))
    assert report_rows[0]["topology"] == "synthetic"
    assert report_rows[0]["n_logical"] == "4"


def test_cli_calibration_from_physical_batch(tmp_path: Path) -> None:
    outdir = tmp_path / "calibration"
    subprocess.run(
        [
            sys.executable,
            "main.py",
            "--physical-batch",
            "--physical-n-values",
            "4",
            "--physical-batch-samples",
            "2",
            "--topology",
            "all",
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
    subprocess.run(
        [
            sys.executable,
            "main.py",
            "--calibrate",
            "--outdir",
            str(outdir),
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report_path = outdir / "surrogate" / "calibration_report.csv"
    assert report_path.exists()
    with report_path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows
    assert {"mae_analytic", "mae_calibrated", "kendall_tau_analytic", "kendall_tau_calibrated"} <= set(rows[0])
