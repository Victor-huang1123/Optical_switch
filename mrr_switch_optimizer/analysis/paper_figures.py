from __future__ import annotations

import argparse
import csv
import json
import math
import re
import zlib
from dataclasses import fields as dataclass_fields, replace
from pathlib import Path

from .calibration import kendall_tau
from ..core.models import DEFAULT_CELL_GEOMETRY
from ..routing.types import RoutingRules


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate paper tables and figures from a run dir")
    parser.add_argument("run_dir")
    args = parser.parse_args()
    generate_paper_artifacts(Path(args.run_dir))


def generate_paper_artifacts(run_dir: Path) -> None:
    tables_dir = run_dir / "tables"
    figures_dir = run_dir / "figures"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    summary_path = run_dir / "logical_matrix" / "logical_matrix_summary.csv"
    if not summary_path.exists():
        raise FileNotFoundError(f"missing logical matrix summary: {summary_path}")
    logical_rows = _read_dict_rows(summary_path)
    physical_summary_rows = _read_optional_dict_rows(
        run_dir / "physical_batch" / "physical_summary.csv"
    )
    calibration_rows = _read_optional_dict_rows(run_dir / "surrogate" / "calibration_report.csv")
    _write_table_t1(tables_dir / "T1_topology_scaling.csv", logical_rows)
    _write_table_t2(tables_dir / "T2_logical_comparison.csv", logical_rows, physical_summary_rows)
    _write_table_t2(
        tables_dir / "T2_logical_physical_comparison.csv",
        logical_rows,
        physical_summary_rows,
    )
    _write_table_t3(tables_dir / "T3_available_baselines.csv", logical_rows, calibration_rows)
    _write_scaling_figure(figures_dir / "F2_scaling_curves.png", logical_rows)
    _write_cdf_figure(figures_dir / "F1_il_sxr_cdf.png", run_dir)
    _write_breakeven_figure(
        figures_dir / "F3_breakeven_curves.png",
        _read_optional_dict_rows(run_dir / "breakeven_sweep.csv"),
    )
    _write_physical_scatter_figure(
        figures_dir / "F4_logical_physical_scatter.png",
        run_dir,
    )
    _write_layout_montage(
        figures_dir / "F5_routed_layouts.png",
        run_dir,
    )

    if physical_summary_rows:
        _write_physical_runtime_figure(
            figures_dir / "F6_physical_failure_runtime.png",
            physical_summary_rows,
        )
    else:
        _write_placeholder_figure(
            figures_dir / "F6_physical_failure_runtime.png",
            "F6 Physical Runtime / Routability",
            "physical_batch/physical_summary.csv not found",
        )

    physical_config = _load_physical_batch_config(run_dir)
    parameter_rows = _routing_parameter_rows(logical_rows, physical_config)
    _write_dicts(
        tables_dir / "routing_parameter_summary.csv",
        parameter_rows,
        list(parameter_rows[0]) if parameter_rows else ["topology"],
    )
    _write_crossing_bend_figure(
        figures_dir / "F7_crossing_bend_scaling.png",
        logical_rows,
        physical_summary_rows,
        _read_optional_dict_rows(
            run_dir / "physical_batch" / "physical_path_distribution.csv"
        ),
    )
    _write_layout_scaling_figure(
        figures_dir / "F8_layout_scaling.png",
        parameter_rows,
    )
    _write_physical_worst_il_artifacts(
        figures_dir / "physical_worst_il_vs_n.png",
        tables_dir / "physical_worst_il_vs_n.csv",
        physical_summary_rows,
    )


def _write_table_t1(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = ["topology", "n_logical", "n_physical", "n_mrr", "n_stages"]
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (row["topology"], row["n_logical"])
        unique[key] = {field: row[field] for field in fieldnames}
    _write_dicts(path, list(unique.values()), fieldnames)


def _write_table_t2(
    path: Path,
    rows: list[dict[str, str]],
    physical_rows: list[dict[str, str]],
) -> None:
    fieldnames = [
        "topology",
        "n_logical",
        "n_mrr",
        "n_stages",
        "worst_il_db",
        "avg_il_db",
        "il_p95_db",
        "worst_sxr_db",
        "sxr_violation_count",
        "physical_attempts",
        "physical_failure_rate",
        "physical_worst_il_db",
        "physical_runtime_s_avg",
    ]
    physical_by_cell = _physical_summary_by_cell(physical_rows)
    selected = []
    for row in rows:
        table_row = {field: row[field] for field in fieldnames[:9]}
        physical = physical_by_cell.get((row["topology"], row["n_logical"]), {})
        table_row.update(
            {
                "physical_attempts": physical.get("attempts", ""),
                "physical_failure_rate": physical.get("failure_rate", ""),
                "physical_worst_il_db": physical.get("worst_il_db", ""),
                "physical_runtime_s_avg": physical.get("runtime_s_avg", ""),
            }
        )
        selected.append(table_row)
    _write_dicts(path, selected, fieldnames)


def _write_table_t3(
    path: Path,
    rows: list[dict[str, str]],
    calibration_rows: list[dict[str, str]],
) -> None:
    fieldnames = [
        "topology",
        "n_logical",
        "ablation",
        "metric",
        "baseline_value",
        "comparison_value",
        "delta",
        "notes",
    ]
    table_rows: list[dict[str, str]] = []
    for row in rows:
        if row.get("n_logical") != "6":
            continue
        table_rows.append(
            {
                "topology": row["topology"],
                "n_logical": row["n_logical"],
                "ablation": "logical_default",
                "metric": "worst_il_db",
                "baseline_value": row["worst_il_db"],
                "comparison_value": "",
                "delta": "",
                "notes": "logical layer default placement",
            }
        )
        table_rows.append(
            {
                "topology": row["topology"],
                "n_logical": row["n_logical"],
                "ablation": "logical_default",
                "metric": "worst_sxr_db",
                "baseline_value": row["worst_sxr_db"],
                "comparison_value": "",
                "delta": "",
                "notes": "logical layer default placement",
            }
        )
    for row in calibration_rows:
        table_rows.append(
            {
                "topology": row["topology"],
                "n_logical": row["n_logical"],
                "ablation": "surrogate_calibration",
                "metric": "mae",
                "baseline_value": row["mae_analytic"],
                "comparison_value": row["mae_calibrated"],
                "delta": _format_delta(row["mae_calibrated"], row["mae_analytic"]),
                "notes": "negative delta means calibrated MAE improved",
            }
        )
        table_rows.append(
            {
                "topology": row["topology"],
                "n_logical": row["n_logical"],
                "ablation": "surrogate_calibration",
                "metric": "kendall_tau",
                "baseline_value": row["kendall_tau_analytic"],
                "comparison_value": row["kendall_tau_calibrated"],
                "delta": _format_delta(
                    row["kendall_tau_calibrated"],
                    row["kendall_tau_analytic"],
                ),
                "notes": "positive delta means calibrated ranking improved",
            }
        )
    _write_dicts(path, table_rows, fieldnames)


def _write_scaling_figure(path: Path, rows: list[dict[str, str]]) -> None:
    import matplotlib.pyplot as plt

    grouped = _group_by_family(rows)
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))
    metrics = [
        ("n_mrr", "MRR count"),
        ("worst_il_db", "Worst IL (dB)"),
        ("worst_sxr_db", "Worst SXR (dB)"),
    ]
    for ax, (field, ylabel) in zip(axes, metrics):
        for family, family_rows in sorted(grouped.items()):
            color, marker = _family_style(family)
            sorted_rows = sorted(family_rows, key=lambda row: int(row["n_logical"]))
            ax.plot(
                [int(row["n_logical"]) for row in sorted_rows],
                [float(row[field]) for row in sorted_rows],
                color=color,
                marker=marker,
                linewidth=1.6,
                label=family,
            )
        ax.set_xlabel("N")
        ax.set_ylabel(ylabel)
        ax.grid(True, color="#E6E6E6", linewidth=0.8)
    axes[0].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def worst_il_attempt_by_cell(
    physical_rows: list[dict[str, str]],
) -> dict[tuple[str, str], dict[str, str]]:
    """Per (topology, n_logical): the fully routed attempt with the highest
    physical worst IL. Cells with no fully routed attempt are absent."""
    argmax: dict[tuple[str, str], dict[str, str]] = {}
    for row in physical_rows:
        if row.get("route_status") != "routed":
            continue
        value = _optional_float(row.get("physical_worst_il_db"))
        if value is None:
            continue
        key = (row["topology"], row.get("n_logical", ""))
        current = argmax.get(key)
        if current is None or value > float(current["physical_worst_il_db"]):
            argmax[key] = row
    return argmax


def _write_physical_worst_il_artifacts(
    figure_path: Path,
    table_path: Path,
    physical_rows: list[dict[str, str]],
) -> None:
    by_cell = _physical_summary_by_cell(physical_rows)
    argmax = worst_il_attempt_by_cell(physical_rows)
    fieldnames = [
        "topology",
        "topology_family",
        "n_logical",
        "attempts",
        "fully_routed_attempts",
        "fully_routed_rate",
        "drc_violation_attempts",
        "worst_il_db",
        "worst_il_permutation",
        "routed_worst_il_mean_db",
        "routed_worst_il_std_db",
        "runtime_s_avg_all_attempts",
        "runtime_s_avg_routed",
    ]
    table_rows = []
    for (topology, n_logical), cell in sorted(
        by_cell.items(), key=lambda item: (item[0][0], int(item[0][1] or 0))
    ):
        worst_row = argmax.get((topology, n_logical), {})
        table_rows.append(
            {
                "topology": topology,
                "topology_family": _topology_family(topology),
                "n_logical": n_logical,
                "attempts": cell["attempts"],
                "fully_routed_attempts": cell["fully_routed_attempts"],
                "fully_routed_rate": cell["fully_routed_rate"],
                "drc_violation_attempts": cell["drc_violation_attempts"],
                "worst_il_db": cell["worst_il_db"],
                "worst_il_permutation": worst_row.get("permutation", ""),
                "routed_worst_il_mean_db": cell["routed_worst_il_mean_db"],
                "routed_worst_il_std_db": cell["routed_worst_il_std_db"],
                "runtime_s_avg_all_attempts": cell["runtime_s_avg"],
                "runtime_s_avg_routed": cell["runtime_s_avg_routed"],
            }
        )
    _write_dicts(table_path, table_rows, fieldnames)

    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 3.8))
    grouped = _group_by_family([row for row in table_rows if row["n_logical"]])
    has_points = False
    for family, family_rows in sorted(grouped.items()):
        color, marker = _family_style(family)
        sorted_rows = sorted(family_rows, key=lambda row: int(row["n_logical"]))
        n_values = [int(row["n_logical"]) for row in sorted_rows]
        # Cells without a fully routed attempt become NaN so the line breaks
        # instead of bridging across missing data.
        il_values = [
            float(row["worst_il_db"]) if row["worst_il_db"] else math.nan
            for row in sorted_rows
        ]
        ax.plot(n_values, il_values, color=color, marker=marker, linewidth=1.6, label=family)
        for n, il, row in zip(n_values, il_values, sorted_rows):
            if math.isnan(il):
                continue
            has_points = True
            ax.annotate(
                f"{row['fully_routed_attempts']}/{row['attempts']}",
                (n, il),
                textcoords="offset points",
                xytext=(0, 6),
                fontsize=6,
                ha="center",
            )
    ax.set_xlabel("N")
    ax.set_ylabel("Physical worst IL (dB)")
    ax.set_title("Worst IL vs N (fully routed attempts; label = routed/attempts)", fontsize=9)
    ax.grid(True, color="#E6E6E6", linewidth=0.8)
    if has_points:
        ax.legend(fontsize=7)
    else:
        ax.text(
            0.5,
            0.5,
            "no fully routed attempts",
            ha="center",
            va="center",
            transform=ax.transAxes,
            fontsize=9,
        )
    fig.tight_layout()
    fig.savefig(figure_path, dpi=180)
    plt.close(fig)


def _write_cdf_figure(path: Path, run_dir: Path) -> None:
    import matplotlib.pyplot as plt

    path_rows: list[dict[str, str]] = []
    for csv_path in sorted((run_dir / "logical_matrix").glob("*_n*/eval_path_distribution.csv")):
        path_rows.extend(_read_dict_rows(csv_path))
    if not path_rows:
        _write_placeholder_figure(
            path,
            "F1 IL / SXR CDF",
            "logical_matrix/*/eval_path_distribution.csv not found",
        )
        return
    available_ns = sorted({int(row["n_logical"]) for row in path_rows if row.get("n_logical")})
    selected_ns = _representative_n_values(available_ns)
    linestyles = dict(zip(selected_ns, ("-", "--", ":")))
    fig, (ax_il, ax_sxr) = plt.subplots(1, 2, figsize=(9, 3.8))
    for family, family_rows in sorted(_group_by_family(path_rows).items()):
        color, _ = _family_style(family)
        for n in selected_ns:
            selected = [row for row in family_rows if row.get("n_logical") == str(n)]
            if not selected:
                continue
            il_values = sorted(float(row["insertion_loss_db"]) for row in selected)
            sxr_values = sorted(float(row["sxr_db"]) for row in selected)
            y_il = [(idx + 1) / len(il_values) for idx in range(len(il_values))]
            y_sxr = [(idx + 1) / len(sxr_values) for idx in range(len(sxr_values))]
            label = f"{family} N={n}"
            ax_il.plot(
                il_values, y_il, color=color, linestyle=linestyles[n], linewidth=1.4, label=label
            )
            ax_sxr.plot(
                sxr_values, y_sxr, color=color, linestyle=linestyles[n], linewidth=1.4, label=label
            )
    ax_il.set_xlabel("IL (dB)")
    ax_il.set_ylabel("CDF")
    ax_sxr.set_xlabel("SXR (dB)")
    ax_sxr.set_ylabel("CDF")
    ax_il.set_title(
        f"N shown: {', '.join(str(n) for n in selected_ns)} "
        f"(min/median/max of {len(available_ns)} available)",
        fontsize=9,
    )
    for ax in (ax_il, ax_sxr):
        ax.grid(True, color="#E6E6E6", linewidth=0.8)
    if ax_il.get_lines():
        ax_il.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _write_breakeven_figure(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        _write_placeholder_figure(
            path,
            "F3 Breakeven Curves",
            "breakeven_sweep.csv not found",
        )
        return

    import matplotlib.pyplot as plt

    grouped = _group_breakeven_rows(rows)
    ordered_keys = sorted(grouped, key=lambda key: (key[0], int(key[1] or 0)))
    linestyle_cycle = ("-", "--", ":", "-.")
    linestyle_by_key: dict[tuple[str, str], str] = {}
    for family, n_logical in ordered_keys:
        family_index = sum(1 for key in linestyle_by_key if key[0] == family)
        linestyle_by_key[(family, n_logical)] = linestyle_cycle[
            family_index % len(linestyle_cycle)
        ]
    fig, (ax_il, ax_sxr) = plt.subplots(1, 2, figsize=(9, 3.8))
    for family, n_logical in ordered_keys:
        topology_rows = grouped[(family, n_logical)]
        color, marker = _family_style(family)
        linestyle = linestyle_by_key[(family, n_logical)]
        label = family if not n_logical else f"{family} N={n_logical}"
        sorted_rows = sorted(
            topology_rows,
            key=lambda row: float(row["crossing_loss_db_per_cross"]),
        )
        xs = [float(row["crossing_loss_db_per_cross"]) for row in sorted_rows]
        ax_il.plot(
            xs,
            [float(row["worst_il_db"]) for row in sorted_rows],
            color=color,
            marker=marker,
            linestyle=linestyle,
            linewidth=1.4,
            label=label,
        )
        ax_sxr.plot(
            xs,
            [float(row["worst_sxr_db"]) for row in sorted_rows],
            color=color,
            marker=marker,
            linestyle=linestyle,
            linewidth=1.4,
            label=label,
        )
    ax_il.set_xlabel("Crossing loss (dB/crossing)")
    ax_il.set_ylabel("Worst IL (dB)")
    ax_sxr.set_xlabel("Crossing loss (dB/crossing)")
    ax_sxr.set_ylabel("Worst SXR (dB)")
    for ax in (ax_il, ax_sxr):
        ax.grid(True, color="#E6E6E6", linewidth=0.8)
    ax_il.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _write_physical_scatter_figure(path: Path, run_dir: Path) -> None:
    physical_rows = [
        row
        for row in _read_optional_dict_rows(
            run_dir / "physical_batch" / "physical_path_distribution.csv"
        )
        if row.get("route_status") == "routed"
        and row.get("topology_il_db")
        and row.get("physical_il_db")
    ]
    if not physical_rows:
        _write_placeholder_figure(
            path,
            "F4 Logical vs Physical",
            "physical_batch/physical_path_distribution.csv has no routed rows",
        )
        return

    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(13, 3.8))
    topological_il = [float(row["topology_il_db"]) for row in physical_rows]
    physical_il = [float(row["physical_il_db"]) for row in physical_rows]
    topological_wiring = [float(row["topology_wiring_um"]) for row in physical_rows]
    physical_wiring = [float(row["physical_wiring_um"]) for row in physical_rows]

    axes[0].scatter(topological_il, physical_il, s=12, alpha=0.65)
    axes[0].set_xlabel("Logical IL (dB)")
    axes[0].set_ylabel("Physical IL (dB)")
    axes[0].set_title(
        f"MAE={_mae(topological_il, physical_il):.3f}, "
        f"tau={kendall_tau(topological_il, physical_il):.3f}",
        fontsize=9,
    )

    axes[1].scatter(topological_wiring, physical_wiring, s=12, alpha=0.65)
    axes[1].set_xlabel("Logical wiring (um)")
    axes[1].set_ylabel("Physical wiring (um)")
    axes[1].set_title(
        f"MAE={_mae(topological_wiring, physical_wiring):.1f}, "
        f"tau={kendall_tau(topological_wiring, physical_wiring):.3f}",
        fontsize=9,
    )

    crossing_pairs = _matched_crossing_pairs(run_dir, physical_rows)
    if crossing_pairs:
        logical_crossings = [pair[0] for pair in crossing_pairs]
        physical_crossings = [pair[1] for pair in crossing_pairs]
        axes[2].scatter(logical_crossings, physical_crossings, s=12, alpha=0.65)
        axes[2].set_xlabel("Logical crossing proxy")
        axes[2].set_ylabel("Physical crossings")
        axes[2].set_title(
            f"tau={kendall_tau(logical_crossings, physical_crossings):.3f}",
            fontsize=9,
        )
    else:
        axes[2].text(
            0.5,
            0.5,
            "No matching logical rows\nfor crossing proxy",
            ha="center",
            va="center",
            transform=axes[2].transAxes,
        )
        axes[2].set_axis_off()

    for ax in axes[:2]:
        ax.grid(True, color="#E6E6E6", linewidth=0.8)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _write_layout_montage(path: Path, run_dir: Path) -> None:
    image_paths = [
        candidate
        for candidate in sorted(run_dir.rglob("*.png"))
        if "figures" not in candidate.relative_to(run_dir).parts
    ]
    if not image_paths:
        _write_placeholder_figure(
            path,
            "F5 Routed Layout Renders",
            "No routed-layout PNG files found in run directory",
        )
        return

    import matplotlib.image as mpimg
    import matplotlib.pyplot as plt

    selected = image_paths[:6]
    cols = min(3, len(selected))
    rows = (len(selected) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(4.2 * cols, 3.2 * rows))
    flat_axes = [axes] if len(selected) == 1 else list(axes.flat)
    for ax, image_path in zip(flat_axes, selected):
        ax.imshow(mpimg.imread(image_path))
        ax.set_title(image_path.stem[:48], fontsize=8)
        ax.set_axis_off()
    for ax in flat_axes[len(selected):]:
        ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _write_physical_runtime_figure(path: Path, rows: list[dict[str, str]]) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 4, figsize=(15, 3.6))
    metrics = [
        ("failure_rate", "Routing success rate"),
        ("runtime_s", "Runtime (s)"),
        ("astar_calls", "A* calls"),
        ("ripup_passes_executed", "Rip-up passes executed"),
    ]
    for ax, (field, ylabel) in zip(axes, metrics):
        for family, points in _family_series_by_n(rows, field).items():
            color, marker = _family_style(family)
            xs = [n for n, _ in points]
            ys = [value for _, value in points]
            if field == "failure_rate":
                ys = [1.0 - value for value in ys]
            ax.plot(xs, ys, color=color, marker=marker, linewidth=1.6, label=family)
        ax.set_xlabel("N")
        ax.set_ylabel(ylabel)
        ax.grid(True, color="#E6E6E6", linewidth=0.8)
    axes[0].set_ylim(-0.05, 1.05)
    axes[0].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _load_physical_batch_config(run_dir: Path) -> dict[str, object]:
    config_path = run_dir / "run_config.json"
    if not config_path.exists():
        return {}
    with config_path.open() as f:
        payload = json.load(f)
    config = payload.get("config", {}) if isinstance(payload, dict) else {}
    if not isinstance(config, dict):
        return {}
    physical = config.get("physical_batch", {})
    return dict(physical) if isinstance(physical, dict) else {}


def _rules_from_physical_config(physical_config: dict[str, object]) -> RoutingRules:
    rule_names = {field.name for field in dataclass_fields(RoutingRules)}
    kwargs = {key: value for key, value in physical_config.items() if key in rule_names}
    return replace(RoutingRules(), **kwargs)  # type: ignore[arg-type]


def _routing_parameter_rows(
    logical_rows: list[dict[str, str]],
    physical_config: dict[str, object],
) -> list[dict[str, str]]:
    rules = _rules_from_physical_config(physical_config)
    stage_pitch_um = float(str(physical_config.get("stage_pitch_um", 105.0)))
    wire_pitch_um = float(str(physical_config.get("wire_pitch_um", 36.0)))
    x_start_um = float(str(physical_config.get("x_start_um", 20.0)))
    x_end_margin_um = float(str(physical_config.get("x_end_margin_um", 70.0)))
    slack_tracks = max(
        1,
        min(rules.local_repair_max_shift_tracks, rules.route_window_max_detour_tracks),
    )
    slack_um = rules.bend_radius_um + slack_tracks * rules.grid_pitch_um
    grid_margin_um = max(
        rules.grid_margin_tracks * rules.grid_pitch_um,
        rules.port_escape_um + rules.mrr_keepout_um + 12.0,
    )

    unique: dict[tuple[str, int], dict[str, str]] = {}
    for row in logical_rows:
        key = (row["topology"], int(row["n_logical"]))
        unique.setdefault(key, row)

    parameter_rows: list[dict[str, str]] = []
    for (topology, n_logical), row in sorted(unique.items()):
        n_physical = int(row["n_physical"])
        n_stages = int(row["n_stages"])
        width_um = (
            (n_stages - 1) * stage_pitch_um
            + DEFAULT_CELL_GEOMETRY.bbox_width_um
        )
        height_um = (
            (n_physical - 1) * wire_pitch_um
            + DEFAULT_CELL_GEOMETRY.bbox_height_um
        )
        parameter_rows.append(
            {
                "topology": topology,
                "n_logical": str(n_logical),
                "n_physical": str(n_physical),
                "n_stages": str(n_stages),
                "n_mrr": row["n_mrr"],
                "stage_pitch_um": f"{stage_pitch_um:.3f}",
                "wire_pitch_um": f"{wire_pitch_um:.3f}",
                "estimated_layout_width_um": f"{width_um:.3f}",
                "estimated_layout_height_um": f"{height_um:.3f}",
                "estimated_layout_area_um2": f"{width_um * height_um:.3f}",
                "grid_pitch_um": f"{rules.grid_pitch_um:.3f}",
                "grid_margin_tracks": f"{rules.grid_margin_tracks:.3f}",
                "grid_margin_um": f"{grid_margin_um:.3f}",
                "route_window_max_detour_tracks": str(rules.route_window_max_detour_tracks),
                "effective_route_window_slack_um": f"{slack_um:.3f}",
                "local_repair_max_shift_tracks": str(rules.local_repair_max_shift_tracks),
                "max_astar_pops": str(rules.max_astar_pops or ""),
                "ripup_max_astar_pops": str(rules.ripup_max_astar_pops or ""),
                "max_ripup_passes": str(rules.max_ripup_passes),
                "early_stop_enabled": str(rules.early_stop_stagnant_passes > 0),
                "early_stop_stagnant_passes": str(rules.early_stop_stagnant_passes),
                "loss_aware_cost": str(rules.loss_aware_cost),
                "prop_loss_db_per_um": f"{rules.prop_loss_db_per_um:.6f}",
                "bend_loss_db_per_bend": f"{rules.bend_loss_db_per_bend:.6f}",
                "crossing_loss_db_per_cross": f"{rules.crossing_loss_db_per_cross:.6f}",
                "repeated_crossing_loss_db": f"{rules.repeated_crossing_loss_db:.6f}",
                "port_escape_um": f"{rules.port_escape_um:.3f}",
                "mrr_keepout_um": f"{rules.mrr_keepout_um:.3f}",
                "min_spacing_um": f"{rules.min_spacing_um:.3f}",
                "x_start_um": f"{x_start_um:.3f}",
                "x_end_margin_um": f"{x_end_margin_um:.3f}",
            }
        )
    return parameter_rows


def _write_crossing_bend_figure(
    path: Path,
    logical_rows: list[dict[str, str]],
    physical_summary_rows: list[dict[str, str]],
    physical_path_rows: list[dict[str, str]],
) -> None:
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))

    for family, points in _family_series_by_n(logical_rows, "avg_crossing_count").items():
        color, marker = _family_style(family)
        axes[0].plot(
            [n for n, _ in points],
            [value for _, value in points],
            color=color,
            marker=marker,
            linewidth=1.6,
            label=family,
        )
    axes[0].set_ylabel("Logical crossing proxy (avg/perm)")

    # Average crossings over fully routed attempts only; failed attempts carry
    # partial routes whose crossing counts would skew the average.
    routed_summary_rows = _routed_rows(physical_summary_rows)
    for family, points in _family_series_by_n(routed_summary_rows, "crossing_count").items():
        color, marker = _family_style(family)
        axes[1].plot(
            [n for n, _ in points],
            [value for _, value in points],
            color=color,
            marker=marker,
            linewidth=1.6,
            label=family,
        )
    axes[1].set_ylabel("Physical crossings (avg/routed attempt)")

    routed_path_rows = _routed_rows(physical_path_rows)
    for family, points in _family_series_by_n(routed_path_rows, "bend_count").items():
        color, marker = _family_style(family)
        axes[2].plot(
            [n for n, _ in points],
            [value for _, value in points],
            color=color,
            marker=marker,
            linewidth=1.6,
            label=family,
        )
    axes[2].set_ylabel("Physical bends (avg/path)")

    has_any_line = False
    for ax in axes:
        ax.set_xlabel("N")
        ax.grid(True, color="#E6E6E6", linewidth=0.8)
        if ax.get_lines():
            ax.legend(fontsize=7)
            has_any_line = True
    if not has_any_line:
        plt.close(fig)
        _write_placeholder_figure(
            path,
            "F7 Crossing / Bend Scaling",
            "no crossing or bend data available",
        )
        return
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _write_layout_scaling_figure(path: Path, parameter_rows: list[dict[str, str]]) -> None:
    if not parameter_rows:
        _write_placeholder_figure(
            path,
            "F8 Layout Scaling",
            "routing_parameter_summary.csv is empty",
        )
        return

    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))
    metrics = [
        ("estimated_layout_width_um", "Estimated width (um)"),
        ("estimated_layout_height_um", "Estimated height (um)"),
        ("estimated_layout_area_um2", "Estimated area (um^2)"),
    ]
    for ax, (field, ylabel) in zip(axes, metrics):
        for family, rows in sorted(_group_by_family(parameter_rows).items()):
            color, marker = _family_style(family)
            sorted_rows = sorted(rows, key=lambda row: int(row["n_logical"]))
            ax.plot(
                [int(row["n_logical"]) for row in sorted_rows],
                [float(row[field]) for row in sorted_rows],
                color=color,
                marker=marker,
                linewidth=1.6,
                label=family,
            )
        ax.set_xlabel("N")
        ax.set_ylabel(ylabel)
        ax.grid(True, color="#E6E6E6", linewidth=0.8)
    axes[0].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


_TOPOLOGY_SIZE_SUFFIX = re.compile(r"_\d+x\d+$")

# Stable family -> (color, marker) so every figure renders a family identically.
_FAMILY_STYLES: dict[str, tuple[str, str]] = {
    "padded_benes": ("#0B6E69", "o"),
    "waksman": ("#D55E00", "s"),
    "spanke_benes": ("#0072B2", "^"),
    "spanke_benes_rect": ("#CC79A7", "D"),
}
_FALLBACK_COLORS = ("#009E73", "#E69F00", "#56B4E9", "#661100", "#000000")
_FALLBACK_MARKERS = ("v", "P", "X", "*", "d")


def _topology_family(name: str) -> str:
    """Strip the trailing _<digits>x<digits> size suffix from a topology name."""
    return _TOPOLOGY_SIZE_SUFFIX.sub("", name)


def _family_style(family: str) -> tuple[str, str]:
    style = _FAMILY_STYLES.get(family)
    if style is not None:
        return style
    digest = zlib.crc32(family.encode())
    return (
        _FALLBACK_COLORS[digest % len(_FALLBACK_COLORS)],
        _FALLBACK_MARKERS[digest % len(_FALLBACK_MARKERS)],
    )


def _group_by_family(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(_topology_family(row["topology"]), []).append(row)
    return grouped


def _routed_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if row.get("route_status") == "routed"]


def _family_series_by_n(
    rows: list[dict[str, str]],
    field: str,
) -> dict[str, list[tuple[int, float]]]:
    """Per family: (n_logical, mean of field) points sorted by n_logical."""
    by_family: dict[str, dict[int, list[float]]] = {}
    for row in rows:
        value = _optional_float(row.get(field))
        if value is None:
            continue
        by_family.setdefault(_topology_family(row["topology"]), {}).setdefault(
            int(row["n_logical"]), []
        ).append(value)
    return {
        family: [(n, sum(values) / len(values)) for n, values in sorted(by_n.items())]
        for family, by_n in sorted(by_family.items())
    }


def _representative_n_values(values: list[int]) -> list[int]:
    """Up to 3 representative N values: min, median, max of the available set."""
    if not values:
        return []
    ordered = sorted(set(values))
    return sorted({ordered[0], ordered[len(ordered) // 2], ordered[-1]})


def _group_breakeven_rows(
    rows: list[dict[str, str]],
) -> dict[tuple[str, str], list[dict[str, str]]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        key = (_topology_family(row["topology"]), row.get("n_logical", ""))
        grouped.setdefault(key, []).append(row)
    return grouped


def _physical_summary_by_cell(
    rows: list[dict[str, str]],
) -> dict[tuple[str, str], dict[str, str]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault((row["topology"], row.get("n_logical", "")), []).append(row)

    summary: dict[tuple[str, str], dict[str, str]] = {}
    for key, cell_rows in grouped.items():
        failure_rates = [
            value
            for row in cell_rows
            if (value := _optional_float(row.get("failure_rate"))) is not None
        ]
        runtimes = [
            value
            for row in cell_rows
            if (value := _optional_float(row.get("runtime_s"))) is not None
        ]
        # Worst IL is only meaningful when every net routed; partially routed
        # attempts would understate the true worst-case path. DRC-violating
        # attempts are likewise excluded from IL stats but counted so the
        # censoring stays visible.
        fully_routed_rows = _routed_rows(cell_rows)
        drc_violation_rows = [
            row for row in cell_rows if row.get("route_status") == "drc_violation"
        ]
        physical_worst = [
            value
            for row in fully_routed_rows
            if (value := _optional_float(row.get("physical_worst_il_db"))) is not None
        ]
        routed_runtimes = [
            value
            for row in fully_routed_rows
            if (value := _optional_float(row.get("runtime_s"))) is not None
        ]
        if physical_worst:
            worst_il_mean = sum(physical_worst) / len(physical_worst)
            worst_il_var = sum(
                (value - worst_il_mean) ** 2 for value in physical_worst
            ) / len(physical_worst)
            worst_il_mean_text = f"{worst_il_mean:.6f}"
            worst_il_std_text = f"{math.sqrt(worst_il_var):.6f}"
        else:
            worst_il_mean_text = ""
            worst_il_std_text = ""
        summary[key] = {
            "attempts": str(len(cell_rows)),
            "fully_routed_attempts": str(len(fully_routed_rows)),
            "fully_routed_rate": f"{len(fully_routed_rows) / len(cell_rows):.6f}",
            "drc_violation_attempts": str(len(drc_violation_rows)),
            "failure_rate": (
                f"{sum(failure_rates) / len(failure_rates):.6f}" if failure_rates else ""
            ),
            "worst_il_db": f"{max(physical_worst):.6f}" if physical_worst else "",
            "routed_worst_il_mean_db": worst_il_mean_text,
            "routed_worst_il_std_db": worst_il_std_text,
            "runtime_s_avg": f"{sum(runtimes) / len(runtimes):.6f}" if runtimes else "",
            "runtime_s_avg_routed": (
                f"{sum(routed_runtimes) / len(routed_runtimes):.6f}"
                if routed_runtimes
                else ""
            ),
        }
    return summary


def _read_dict_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _read_optional_dict_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return _read_dict_rows(path)


def _write_dicts(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_placeholder_figure(path: Path, title: str, message: str) -> None:
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(6, 3.8))
    ax.text(0.5, 0.58, title, ha="center", va="center", fontsize=12, weight="bold")
    ax.text(0.5, 0.42, message, ha="center", va="center", fontsize=9)
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def _matched_crossing_pairs(
    run_dir: Path,
    physical_rows: list[dict[str, str]],
) -> list[tuple[float, float]]:
    logical_lookup: dict[tuple[str, str, str, str], float] = {}
    for csv_path in sorted((run_dir / "logical_matrix").glob("*_n*/eval_path_distribution.csv")):
        for row in _read_dict_rows(csv_path):
            key = (
                row["topology"],
                row.get("n_logical", ""),
                row["permutation"],
                row["input_port"],
            )
            logical_lookup[key] = float(row["perm_crossing_count"])

    pairs: list[tuple[float, float]] = []
    for row in physical_rows:
        physical_crossing = _optional_float(row.get("crossing_count"))
        if physical_crossing is None:
            continue
        key = (
            row["topology"],
            row.get("n_logical", ""),
            row["permutation"],
            row["input_port"],
        )
        logical_crossing = logical_lookup.get(key)
        if logical_crossing is not None:
            pairs.append((logical_crossing, physical_crossing))
    return pairs


def _mae(predicted: list[float], target: list[float]) -> float:
    return sum(abs(pred - actual) for pred, actual in zip(predicted, target)) / len(predicted)


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _format_delta(comparison: str, baseline: str) -> str:
    return f"{float(comparison) - float(baseline):.9f}"


if __name__ == "__main__":
    main()
