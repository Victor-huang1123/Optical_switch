from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import yaml

from mrr_switch_optimizer.analysis.paper_figures import (
    _family_series_by_n,
    _group_by_family,
    _physical_summary_by_cell,
    _representative_n_values,
    _routed_rows,
    _topology_family,
    _write_cdf_figure,
    _write_physical_worst_il_artifacts,
    _write_table_t2,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_paper_figure_generator_smoke(tmp_path: Path) -> None:
    config_path = tmp_path / "paper_figures.yaml"
    out_root = tmp_path / "outputs"
    config_path.write_text(
        yaml.safe_dump(
            {
                "seed": 42,
                "outdir": str(out_root),
                "run_id": "fig_smoke",
                "logical_matrix": {
                    "topology": "all",
                    "n_values": [4, 8],
                    "train_samples": 2,
                    "eval_samples": 2,
                },
                "breakeven": {
                    "enabled": True,
                    "topology": "all",
                    "n_values": [4],
                    "train_samples": 2,
                    "eval_samples": 2,
                    "crossing_loss_db_per_cross": [0.0, 0.5],
                },
                "layout_gallery": {
                    "enabled": True,
                    "topology": "waksman",
                    "n_values": [6],
                },
                "physical_batch": {
                    "enabled": True,
                    "topology": "all",
                    "n_values": [4],
                    "samples": 2,
                    "max_astar_pops": 1,
                    "max_ripup_passes": 0,
                },
                "calibration": {
                    "enabled": True,
                },
            }
        )
    )
    subprocess.run(
        [sys.executable, "main.py", "--paper-run", str(config_path)],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    run_dir = out_root / "fig_smoke"
    subprocess.run(
        [sys.executable, "-m", "mrr_switch_optimizer.analysis.paper_figures", str(run_dir)],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert (run_dir / "tables" / "T1_topology_scaling.csv").exists()
    assert (run_dir / "tables" / "T2_logical_comparison.csv").exists()
    assert (run_dir / "tables" / "T2_logical_physical_comparison.csv").exists()
    assert (run_dir / "tables" / "T3_available_baselines.csv").exists()
    assert (run_dir / "breakeven_sweep.csv").exists()
    assert (run_dir / "physical_batch" / "physical_summary.csv").exists()
    assert (run_dir / "surrogate" / "calibration_report.csv").exists()
    assert list((run_dir / "layouts").glob("*.png"))
    assert (run_dir / "figures" / "F1_il_sxr_cdf.png").stat().st_size > 0
    assert (run_dir / "figures" / "F2_scaling_curves.png").stat().st_size > 0
    assert (run_dir / "figures" / "F3_breakeven_curves.png").stat().st_size > 0
    assert (run_dir / "figures" / "F4_logical_physical_scatter.png").stat().st_size > 0
    assert (run_dir / "figures" / "F5_routed_layouts.png").stat().st_size > 0
    assert (run_dir / "figures" / "F6_physical_failure_runtime.png").stat().st_size > 0
    assert (run_dir / "figures" / "F7_crossing_bend_scaling.png").stat().st_size > 0
    assert (run_dir / "figures" / "F8_layout_scaling.png").stat().st_size > 0
    assert (run_dir / "figures" / "physical_worst_il_vs_n.png").stat().st_size > 0
    with (run_dir / "tables" / "physical_worst_il_vs_n.csv").open(newline="") as f:
        worst_il_reader = csv.DictReader(f)
        worst_il_fields = worst_il_reader.fieldnames or []
        worst_il_rows = list(worst_il_reader)
    for column in (
        "topology",
        "topology_family",
        "n_logical",
        "drc_violation_attempts",
        "routed_worst_il_mean_db",
        "routed_worst_il_std_db",
        "runtime_s_avg_all_attempts",
        "runtime_s_avg_routed",
    ):
        assert column in worst_il_fields
    assert all(
        row["topology_family"] == _topology_family(row["topology"]) for row in worst_il_rows
    )
    with (run_dir / "tables" / "T2_logical_comparison.csv").open(newline="") as f:
        t2_rows = list(csv.DictReader(f))
    with (run_dir / "logical_matrix" / "logical_matrix_summary.csv").open(newline="") as f:
        logical_ns = {row["n_logical"] for row in csv.DictReader(f)}
    assert {row["n_logical"] for row in t2_rows} == logical_ns


def test_topology_family_strips_only_trailing_size_suffix() -> None:
    assert _topology_family("padded_benes_8x8") == "padded_benes"
    assert _topology_family("waksman_7x7") == "waksman"
    assert _topology_family("spanke_benes_16x16") == "spanke_benes"
    assert _topology_family("spanke_benes_rect_12x12") == "spanke_benes_rect"
    assert _topology_family("crossbar") == "crossbar"
    assert _topology_family("weird_4x4_core") == "weird_4x4_core"


def test_group_by_family_merges_sizes_across_n() -> None:
    rows = [
        {"topology": "padded_benes_4x4", "n_logical": "4"},
        {"topology": "padded_benes_8x8", "n_logical": "8"},
        {"topology": "waksman_6x6", "n_logical": "6"},
    ]
    grouped = _group_by_family(rows)
    assert set(grouped) == {"padded_benes", "waksman"}
    assert [row["n_logical"] for row in grouped["padded_benes"]] == ["4", "8"]


def test_family_series_by_n_with_routed_filter_excludes_failed_attempts() -> None:
    rows = [
        {
            "topology": "waksman_4x4",
            "n_logical": "4",
            "route_status": "routed",
            "crossing_count": "2.0",
        },
        {
            "topology": "waksman_4x4",
            "n_logical": "4",
            "route_status": "routed",
            "crossing_count": "4.0",
        },
        {
            "topology": "waksman_4x4",
            "n_logical": "4",
            "route_status": "failed",
            "crossing_count": "100.0",
        },
        {
            "topology": "waksman_8x8",
            "n_logical": "8",
            "route_status": "routed",
            "crossing_count": "6.0",
        },
    ]
    series = _family_series_by_n(_routed_rows(rows), "crossing_count")
    assert series == {"waksman": [(4, 3.0), (8, 6.0)]}


def test_physical_summary_by_cell_reports_drc_and_conditioned_stats() -> None:
    rows = [
        {
            "topology": "waksman_4x4",
            "n_logical": "4",
            "route_status": "routed",
            "permutation": "1-0-3-2",
            "physical_worst_il_db": "4.0",
            "runtime_s": "1.0",
        },
        {
            "topology": "waksman_4x4",
            "n_logical": "4",
            "route_status": "routed",
            "permutation": "2-3-0-1",
            "physical_worst_il_db": "6.0",
            "runtime_s": "3.0",
        },
        {
            "topology": "waksman_4x4",
            "n_logical": "4",
            "route_status": "failed",
            "permutation": "3-2-1-0",
            "physical_worst_il_db": "9.9",
            "runtime_s": "5.0",
        },
        {
            "topology": "waksman_4x4",
            "n_logical": "4",
            "route_status": "drc_violation",
            "permutation": "0-1-2-3",
            "physical_worst_il_db": "8.8",
            "runtime_s": "7.0",
        },
    ]
    cell = _physical_summary_by_cell(rows)[("waksman_4x4", "4")]
    assert cell["attempts"] == "4"
    assert cell["fully_routed_attempts"] == "2"
    assert cell["drc_violation_attempts"] == "1"
    # DRC-violating and failed attempts must not leak into routed-only stats.
    assert cell["worst_il_db"] == "6.000000"
    assert cell["routed_worst_il_mean_db"] == "5.000000"
    assert cell["routed_worst_il_std_db"] == "1.000000"
    assert cell["runtime_s_avg"] == "4.000000"
    assert cell["runtime_s_avg_routed"] == "2.000000"


def test_worst_il_artifacts_columns_and_gap_cell(tmp_path: Path) -> None:
    physical_rows = [
        {
            "topology": "waksman_4x4",
            "n_logical": "4",
            "route_status": "routed",
            "permutation": "1-0-3-2",
            "physical_worst_il_db": "4.0",
            "runtime_s": "1.0",
        },
        {
            "topology": "waksman_4x4",
            "n_logical": "4",
            "route_status": "failed",
            "permutation": "2-3-0-1",
            "physical_worst_il_db": "9.0",
            "runtime_s": "3.0",
        },
        # N=6 has no routed attempt: must stay a gap in the figure and an
        # empty worst_il_db in the table, with the DRC censoring counted.
        {
            "topology": "waksman_6x6",
            "n_logical": "6",
            "route_status": "drc_violation",
            "permutation": "0-1-2-3-4-5",
            "physical_worst_il_db": "5.0",
            "runtime_s": "2.0",
        },
        {
            "topology": "waksman_8x8",
            "n_logical": "8",
            "route_status": "routed",
            "permutation": "1-0-3-2-5-4-7-6",
            "physical_worst_il_db": "6.5",
            "runtime_s": "4.0",
        },
        {
            "topology": "spanke_benes_rect_4x4",
            "n_logical": "4",
            "route_status": "routed",
            "permutation": "0-1-2-3",
            "physical_worst_il_db": "3.0",
            "runtime_s": "1.5",
        },
    ]
    figure_path = tmp_path / "physical_worst_il_vs_n.png"
    table_path = tmp_path / "physical_worst_il_vs_n.csv"
    _write_physical_worst_il_artifacts(figure_path, table_path, physical_rows)

    assert figure_path.stat().st_size > 0
    with table_path.open(newline="") as f:
        by_key = {(row["topology"], row["n_logical"]): row for row in csv.DictReader(f)}
    n4 = by_key[("waksman_4x4", "4")]
    assert n4["topology_family"] == "waksman"
    assert n4["worst_il_db"] == "4.000000"
    assert n4["worst_il_permutation"] == "1-0-3-2"
    assert n4["drc_violation_attempts"] == "0"
    assert n4["routed_worst_il_mean_db"] == "4.000000"
    assert n4["routed_worst_il_std_db"] == "0.000000"
    assert n4["runtime_s_avg_all_attempts"] == "2.000000"
    assert n4["runtime_s_avg_routed"] == "1.000000"
    n6 = by_key[("waksman_6x6", "6")]
    assert n6["worst_il_db"] == ""
    assert n6["drc_violation_attempts"] == "1"
    assert n6["fully_routed_attempts"] == "0"
    assert by_key[("spanke_benes_rect_4x4", "4")]["topology_family"] == "spanke_benes_rect"


def test_table_t2_includes_every_n_present(tmp_path: Path) -> None:
    logical_rows = [
        {
            "topology": f"waksman_{n}x{n}",
            "n_logical": str(n),
            "n_mrr": "12",
            "n_stages": "5",
            "worst_il_db": "1.0",
            "avg_il_db": "0.5",
            "il_p95_db": "0.9",
            "worst_sxr_db": "20.0",
            "sxr_violation_count": "0",
        }
        for n in (3, 5, 11, 16)
    ]
    table_path = tmp_path / "T2.csv"
    _write_table_t2(table_path, logical_rows, [])
    with table_path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert [row["n_logical"] for row in rows] == ["3", "5", "11", "16"]


def test_representative_n_values_selects_min_median_max() -> None:
    assert _representative_n_values(list(range(3, 17))) == [3, 10, 16]
    assert _representative_n_values([4, 8]) == [4, 8]
    assert _representative_n_values([8]) == [8]
    assert _representative_n_values([]) == []


def test_cdf_figure_uses_representative_n(tmp_path: Path) -> None:
    matrix_dir = tmp_path / "logical_matrix"
    for n in (4, 8, 12):
        cell_dir = matrix_dir / f"waksman_n{n}"
        cell_dir.mkdir(parents=True)
        with (cell_dir / "eval_path_distribution.csv").open("w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["topology", "n_logical", "insertion_loss_db", "sxr_db"]
            )
            writer.writeheader()
            writer.writerow(
                {
                    "topology": f"waksman_{n}x{n}",
                    "n_logical": str(n),
                    "insertion_loss_db": "1.0",
                    "sxr_db": "20.0",
                }
            )
            writer.writerow(
                {
                    "topology": f"waksman_{n}x{n}",
                    "n_logical": str(n),
                    "insertion_loss_db": "2.0",
                    "sxr_db": "22.0",
                }
            )
    figure_path = tmp_path / "F1_il_sxr_cdf.png"
    _write_cdf_figure(figure_path, tmp_path)
    assert figure_path.stat().st_size > 0
