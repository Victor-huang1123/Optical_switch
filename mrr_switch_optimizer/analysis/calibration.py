from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from random import Random

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True)
class CalibrationExample:
    topology: str
    n_logical: int
    analytic_prediction: float
    physical_target: float
    features: tuple[float, ...]


@dataclass(frozen=True)
class CalibrationReportRow:
    topology: str
    n_logical: int
    examples: int
    train_examples: int
    holdout_examples: int
    mae_analytic: float
    mae_calibrated: float
    kendall_tau_analytic: float
    kendall_tau_calibrated: float


def load_physical_path_examples(path: Path) -> list[CalibrationExample]:
    examples: list[CalibrationExample] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("route_status") != "routed":
                continue
            physical_il = _optional_float(row.get("physical_il_db", ""))
            topology_il = _optional_float(row.get("topology_il_db", ""))
            if physical_il is None or topology_il is None:
                continue
            examples.append(
                CalibrationExample(
                    topology=str(row["topology"]),
                    n_logical=int(row.get("n_logical") or 0),
                    analytic_prediction=topology_il,
                    physical_target=physical_il,
                    features=(
                        1.0,
                        _optional_float(row.get("topology_wiring_um", "")) or 0.0,
                        _optional_float(row.get("mrr_loss_db", "")) or 0.0,
                        _optional_float(row.get("bend_count", "")) or 0.0,
                        _optional_float(row.get("crossing_count", "")) or 0.0,
                    ),
                )
            )
    return examples


def calibrate_examples(
    examples: list[CalibrationExample],
    *,
    ridge_lambda: float = 1e-6,
    holdout_fraction: float = 0.4,
    seed: int = 42,
) -> list[CalibrationReportRow]:
    grouped: dict[tuple[str, int], list[CalibrationExample]] = {}
    for example in examples:
        grouped.setdefault((example.topology, example.n_logical), []).append(example)

    rows: list[CalibrationReportRow] = []
    for (topology, n_logical), group in sorted(grouped.items()):
        if len(group) < 2:
            continue
        train, holdout = _split_examples(group, holdout_fraction=holdout_fraction, seed=seed)
        coefficients = _fit_ridge_residual(train, ridge_lambda=ridge_lambda)
        analytic = np.array([example.analytic_prediction for example in holdout], dtype=float)
        physical = np.array([example.physical_target for example in holdout], dtype=float)
        residual_features = np.array([example.features for example in holdout], dtype=float)
        calibrated = analytic + residual_features @ coefficients
        rows.append(
            CalibrationReportRow(
                topology=topology,
                n_logical=n_logical,
                examples=len(group),
                train_examples=len(train),
                holdout_examples=len(holdout),
                mae_analytic=_mae(analytic, physical),
                mae_calibrated=_mae(calibrated, physical),
                kendall_tau_analytic=kendall_tau(analytic.tolist(), physical.tolist()),
                kendall_tau_calibrated=kendall_tau(calibrated.tolist(), physical.tolist()),
            )
        )
    return rows


def write_calibration_report(path: Path, rows: list[CalibrationReportRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "topology",
        "n_logical",
        "examples",
        "train_examples",
        "holdout_examples",
        "mae_analytic",
        "mae_calibrated",
        "kendall_tau_analytic",
        "kendall_tau_calibrated",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "topology": row.topology,
                    "n_logical": row.n_logical,
                    "examples": row.examples,
                    "train_examples": row.train_examples,
                    "holdout_examples": row.holdout_examples,
                    "mae_analytic": f"{row.mae_analytic:.9f}",
                    "mae_calibrated": f"{row.mae_calibrated:.9f}",
                    "kendall_tau_analytic": f"{row.kendall_tau_analytic:.9f}",
                    "kendall_tau_calibrated": f"{row.kendall_tau_calibrated:.9f}",
                }
            )


def _split_examples(
    examples: list[CalibrationExample],
    *,
    holdout_fraction: float,
    seed: int,
) -> tuple[list[CalibrationExample], list[CalibrationExample]]:
    shuffled = list(examples)
    Random(seed).shuffle(shuffled)
    holdout_count = max(1, min(len(shuffled) - 1, round(len(shuffled) * holdout_fraction)))
    holdout = shuffled[:holdout_count]
    train = shuffled[holdout_count:]
    return train, holdout


def _fit_ridge_residual(
    examples: list[CalibrationExample],
    *,
    ridge_lambda: float,
) -> npt.NDArray[np.float64]:
    x = np.array([example.features for example in examples], dtype=float)
    y = np.array(
        [example.physical_target - example.analytic_prediction for example in examples],
        dtype=float,
    )
    penalty = ridge_lambda * np.eye(x.shape[1])
    penalty[0, 0] = 0.0
    coefficients = np.linalg.pinv(x.T @ x + penalty) @ (x.T @ y)
    return np.asarray(coefficients, dtype=np.float64)


def kendall_tau(predicted: list[float], target: list[float]) -> float:
    concordant = 0
    discordant = 0
    for i in range(len(predicted)):
        for j in range(i + 1, len(predicted)):
            pred_delta = predicted[i] - predicted[j]
            target_delta = target[i] - target[j]
            product = pred_delta * target_delta
            if product > 0.0:
                concordant += 1
            elif product < 0.0:
                discordant += 1
    total = concordant + discordant
    if total == 0:
        return 0.0
    return (concordant - discordant) / total


def _mae(
    predicted: npt.NDArray[np.float64],
    target: npt.NDArray[np.float64],
) -> float:
    return float(np.mean(np.abs(predicted - target)))


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)
