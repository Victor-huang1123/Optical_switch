from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..analysis.activity import MRRActivity
from ..analysis.paper_figures import _family_style, _topology_family
from ..analysis.surrogate import AnalyticEdge


def _write_states(path: Path, states: dict[str, int]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["mrr_id", "state", "meaning"])
        for mrr_id, state in sorted(states.items()):
            writer.writerow([mrr_id, state, "drop/cross" if state else "through/bar"])


def _write_placement(path: Path, centers: dict[str, tuple[float, float]]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["mrr_id", "x_um", "y_um"])
        for mrr_id, (x_um, y_um) in sorted(centers.items()):
            writer.writerow([mrr_id, f"{x_um:.6f}", f"{y_um:.6f}"])


def _write_summary(path: Path, metrics: list[Any]) -> None:
    with path.open("w") as f:
        json.dump([asdict(metric) for metric in metrics], f, indent=2)


def _write_comparison(path: Path, metrics: list[Any]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "topology",
                "mrr_count",
                "stages",
                "min_path_depth",
                "max_path_depth",
                "avg_path_depth",
                "active_drop_states",
                "total_wiring_um",
                "logical_crossings",
                "worst_il_db",
                "avg_il_db",
                "worst_sxr_db",
            ]
        )
        for metric in metrics:
            writer.writerow(
                [
                    metric.topology,
                    metric.mrr_count,
                    metric.stages,
                    metric.min_path_depth,
                    metric.max_path_depth,
                    f"{metric.average_path_depth:.3f}",
                    metric.active_states,
                    f"{metric.total_wiring_um:.3f}",
                    metric.crossing_count,
                    f"{metric.worst_insertion_loss_db:.4f}",
                    f"{metric.average_insertion_loss_db:.4f}",
                    f"{metric.worst_sxr_db:.4f}",
                ]
            )


def _write_sa_summary(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "topology",
        "train_perms",
        "sa_seed",
        "default_cost",
        "sa_cost",
        "delta_cost",
        "improved",
        "feasible",
        "default_worst_il_db",
        "sa_worst_il_db",
        "default_avg_il_db",
        "sa_avg_il_db",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_activity_rows(path: Path, rows: list[MRRActivity]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "topology",
                "mrr_id",
                "stage",
                "wire_a",
                "wire_b",
                "permutations",
                "permutations_scanned",
                "state0_count",
                "state1_count",
                "active_path_visits",
                "active_state0_visits",
                "active_state1_visits",
                "classification",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.topology,
                    row.mrr_id,
                    row.stage,
                    row.pair[0],
                    row.pair[1],
                    row.permutations,
                    row.permutations_scanned,
                    row.state0_count,
                    row.state1_count,
                    row.active_path_visits,
                    row.active_state0_visits,
                    row.active_state1_visits,
                    row.classification,
                ]
            )


def _write_activity_summary(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_eval_distribution(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_eval_summary(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_dict_rows(
    path: Path,
    rows: list[dict[str, object]],
    fieldnames: list[str] | None = None,
) -> None:
    if fieldnames is None:
        fieldnames = _merged_fieldnames(rows)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        if rows:
            writer.writerows(rows)


def _merged_fieldnames(rows: list[dict[str, object]]) -> list[str]:
    fieldnames: list[str] = []
    for row in rows:
        for fieldname in row:
            if fieldname not in fieldnames:
                fieldnames.append(fieldname)
    return fieldnames


def _write_breakeven_plot(path: Path, rows: list[dict[str, object]]) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return

    family_order = {
        "padded_benes": 0,
        "waksman": 1,
        "spanke_benes": 2,
        "spanke_benes_rect": 3,
    }
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["topology"]), []).append(row)

    fig, (ax_il, ax_sxr) = plt.subplots(1, 2, figsize=(10, 4.5))
    for topology, topology_rows in sorted(
        grouped.items(),
        key=lambda item: (
            family_order.get(_topology_family(item[0]), len(family_order)),
            item[0],
        ),
    ):
        sorted_rows = sorted(
            topology_rows,
            key=lambda row: _as_float(row["crossing_loss_db_per_cross"]),
        )
        x_values = [_as_float(row["crossing_loss_db_per_cross"]) for row in sorted_rows]
        il_values = [_as_float(row["worst_il_db"]) for row in sorted_rows]
        sxr_values = [_as_float(row["worst_sxr_db"]) for row in sorted_rows]
        color = _family_style(_topology_family(topology))[0]
        ax_il.plot(x_values, il_values, color=color, marker="o", linewidth=1.8, label=topology)
        ax_sxr.plot(x_values, sxr_values, color=color, marker="o", linewidth=1.8, label=topology)

    ax_il.set_title("Worst IL sweep")
    ax_il.set_xlabel("crossing IL per crossing (dB)")
    ax_il.set_ylabel("worst_il_db")
    ax_sxr.set_title("Worst SXR sweep")
    ax_sxr.set_xlabel("crossing IL per crossing (dB)")
    ax_sxr.set_ylabel("worst_sxr_db")
    fig.suptitle("Breakeven: IL and SXR vs crossing loss penalty", fontsize=10)
    for ax in (ax_il, ax_sxr):
        ax.grid(True, color="#E6E6E6", linewidth=0.8)
        ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _write_paper_table(path: Path, paper_rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "topology",
        "n_mrr_total",
        "n_never_active",
        "n_tunable",
        "n_stages",
        "path_depth_min",
        "path_depth_max",
        "layout",
        "worst_il_db",
        "avg_il_db",
        "il_p95_db",
        "worst_sxr_db",
        "sxr_violation_count",
        "avg_crossing_count",
        "delta_worst_il_db",
        "sa_delta_cost",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(paper_rows)


def _write_analytic_edges(path: Path, rows: list[AnalyticEdge]) -> None:
    if not rows:
        return
    fieldnames = [
        "topology",
        "permutation",
        "input_port",
        "src_mrr",
        "src_port",
        "dst_mrr",
        "dst_port",
        "dx_um",
        "dy_um",
        "manhattan_um",
        "delta_phi",
        "bend_estimate",
        "crossing_estimate",
        "alignment_penalty",
        "local_density",
        "blockage_ratio",
        "analytic_cost",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "topology": row.topology,
                "permutation": "-".join(str(x) for x in row.permutation),
                "input_port": row.input_port,
                "src_mrr": row.src_mrr,
                "src_port": row.src_port,
                "dst_mrr": row.dst_mrr,
                "dst_port": row.dst_port,
                "dx_um": f"{row.dx_um:.4f}",
                "dy_um": f"{row.dy_um:.4f}",
                "manhattan_um": f"{row.manhattan_um:.4f}",
                "delta_phi": f"{row.delta_phi:.6f}",
                "bend_estimate": f"{row.bend_estimate:.4f}",
                "crossing_estimate": f"{row.crossing_estimate:.4f}",
                "alignment_penalty": f"{row.alignment_penalty:.6f}",
                "local_density": f"{row.local_density:.4f}",
                "blockage_ratio": f"{row.blockage_ratio:.4f}",
                "analytic_cost": f"{row.analytic_cost:.6f}",
            })


def _as_float(value: object) -> float:
    if isinstance(value, (int, float, str)):
        return float(value)
    raise TypeError(f"expected numeric CSV value, got {type(value).__name__}")
