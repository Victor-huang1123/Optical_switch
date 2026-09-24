from __future__ import annotations

import argparse
import csv
from dataclasses import replace
import hashlib
import json
import platform
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from ..analysis.activity import MRRActivity, scan_mrr_activity, summarize_mrr_activity
from ..analysis.calibration import (
    calibrate_examples,
    load_physical_path_examples,
    write_calibration_report,
)
from ..analysis.cost import (
    ALPHA_DB_PER_UM,
    CROSSING_LOSS_DB_PER_CROSS,
    aggregate_cost,
    build_evaluation_dataset,
    evaluate_routing,
    evaluate_plan,
    make_permutation_split,
    PermutationPlan,
)
from ..analysis.paper_figures import generate_paper_artifacts, worst_il_attempt_by_cell
from ..analysis.fabric_coverage import verify_permutation_coverage
from ..analysis.fabric_loss import (
    FabricWorstILReport,
    evaluate_fixed_fabric_worst_insertion_loss,
)
from ..analysis.surrogate import AnalyticEdge, analytic_edge_costs
from ..core.sparams import MOCK_S_TABLE, load_mrr_s_table
from ..core.fabric import build_fabric_graph
from ..core.models import DEFAULT_CELL_GEOMETRY, LEGACY_V2_CELL_GEOMETRY
from ..core.state_assignment import (
    BenesLoopingStrategy,
    SpankeBenesRectStrategy,
    SpankeBenesStrategy,
    WaksmanStrategy,
)
from ..core.topology import (
    PaddedBenesTopology,
    RNBTopology,
    SpankeBenesRectTopology,
    SpankeBenesTopology,
    WaksmanTopology,
)
from .reports import (
    _write_activity_rows,
    _write_activity_summary,
    _write_analytic_edges,
    _write_breakeven_plot,
    _write_comparison,
    _write_dict_rows,
    _write_eval_distribution,
    _write_eval_summary,
    _write_paper_table,
    _write_placement,
    _write_sa_summary,
    _write_states,
    _write_summary,
)
from .fabric_reports import write_fixed_fabric_reports
from ..output.visualize import (
    save_fixed_fabric_png,
    save_il_sxr_cdf,
    save_routing_gif,
    save_routing_png,
)
from ..placement.layout import build_cells
from ..placement.lp import lp_placement
from ..placement.sa import is_feasible_placement, sa_placement
from ..routing.physical import route_physical_design
from ..routing.fabric import route_fixed_fabric
from ..routing.types import PhysicalRoute, RoutingError, RoutingRules


def main() -> None:
    args = _parse_args()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    permutation = _parse_permutation(args.permutation, args.n_logical)
    if args.sparam_dir == "MOCK":
        # Explicit mock physics (regression tests); silent fallback still
        # warns via load_mrr_s_table.
        s_table = dict(MOCK_S_TABLE)
    else:
        s_table = load_mrr_s_table(
            _resolved_sparam_dir(args),
            radius_um=args.radius_um,
            channel_nm=args.channel_nm,
            wavelength_nm=args.wavelength_nm,
        )
    if args.fixed_fabric:
        loss_report = _run_fixed_fabric(args, permutation, s_table, outdir)
        print(f"wrote fixed fabric outputs to {outdir / 'fixed_fabric'}")
        print(
            f"{loss_report.topology_name}: "
            f"worst physical IL={loss_report.worst_insertion_loss_db:.6f} dB, "
            f"MRRs={loss_report.mrr_count}"
        )
        return
    if args.logical_matrix:
        _run_logical_matrix(args, s_table, outdir)
        print(f"wrote logical matrix outputs to {outdir / 'logical_matrix'}")
        return
    if args.paper_run:
        run_outdir = _run_paper_run(args, s_table)
        generate_paper_artifacts(run_outdir)
        print(f"wrote paper run outputs to {run_outdir}")
        return
    if args.paper_artifacts:
        artifact_dir = Path(args.paper_artifacts)
        generate_paper_artifacts(artifact_dir)
        print(f"wrote paper artifacts under {artifact_dir}")
        return
    if args.render_worst_il_layouts:
        config_path = Path(args.render_worst_il_layouts)
        config, run_outdir, matrix_args = _load_paper_run_context(args, config_path)
        physical_args = _paper_physical_args(
            matrix_args, _config_mapping(config, "physical_batch")
        )
        written = _render_worst_il_layouts(physical_args, s_table, run_outdir)
        print(
            f"wrote {len(written)} worst-IL layouts to "
            f"{run_outdir / 'physical_batch' / 'layouts_worst_il'}"
        )
        return
    if args.physical_batch:
        _run_physical_batch(args, s_table, outdir)
        print(f"wrote physical batch outputs to {outdir / 'physical_batch'}")
        return
    if args.calibrate:
        report_path = _run_calibration(args, outdir)
        print(f"wrote calibration report to {report_path}")
        return
    # The original logical CLI reports and demo canvases are legacy regression
    # artifacts. Keep their port rows and centerline-spacing semantics pinned
    # even though the library default is geometry v3.
    cli_geometry = LEGACY_V2_CELL_GEOMETRY
    debug_routing_rules = (
        replace(
            _physical_rules_from_args(args),
            waveguide_width_um=cli_geometry.waveguide_width_um,
        )
        if args.physical_debug_overlay
        else None
    )

    topologies = _select_topologies(args.topology, args.n_logical)
    metrics = []
    sa_rows = []
    activity_summary_rows = []
    eval_rows = []
    eval_summary_rows = []
    path_depth_ranges: dict[str, tuple[int, int]] = {}
    centers_by_topology: dict[str, dict[str, tuple[float, float]]] = {}
    for topology in topologies:
        states = topology.get_state_assignment(permutation)
        metric = evaluate_routing(
            topology,
            permutation,
            s_table,
            cell_geometry=cli_geometry,
        )
        metrics.append(metric)
        sa_centers = None
        default_centers = {
            mrr_id: cell.center
            for mrr_id, cell in build_cells(
                topology,
                s_table,
                cell_geometry=cli_geometry,
            ).items()
        }
        centers_by_topology[topology.name] = default_centers
        stem = f"{topology.name}_{'-'.join(map(str, permutation))}"
        _write_optional_visual(
            outdir / f"{stem}.png",
            allow_skip=args.n_logical != 6,
            write=lambda: save_routing_png(
                topology,
                permutation,
                states,
                s_table,
                outdir / f"{stem}.png",
                routing_rules=debug_routing_rules,
                debug_physical_overlay=args.physical_debug_overlay,
                cell_geometry=cli_geometry,
            ),
        )
        if args.gif:
            _write_optional_visual(
                outdir / f"{stem}.gif",
                allow_skip=args.n_logical != 6,
                write=lambda: save_routing_gif(
                    topology,
                    permutation,
                    states,
                    s_table,
                    outdir / f"{stem}.gif",
                    routing_rules=debug_routing_rules,
                    debug_physical_overlay=args.physical_debug_overlay,
                    cell_geometry=cli_geometry,
                ),
            )
        _write_states(outdir / f"{stem}_states.csv", states)
        if args.sa:
            train_perms, _ = _make_permutation_split(
                topology.N_logical,
                args.sa_train,
                args,
                n_eval=0,
            )
            default_cost = aggregate_cost(
                topology,
                train_perms,
                s_table,
                sxr_min_db=args.sxr_min_db,
                centers=default_centers,
            )
            sa_centers, sa_cost, sa_seed = _run_sa_restarts(
                topology,
                s_table,
                train_perms,
                args,
            )
            centers_by_topology[topology.name] = sa_centers
            sa_metric = evaluate_routing(
                topology,
                permutation,
                s_table,
                centers=sa_centers,
                cell_geometry=cli_geometry,
            )
            sa_rows.append(
                {
                    "topology": topology.name,
                    "train_perms": len(train_perms),
                    "sa_seed": sa_seed,
                    "default_cost": default_cost,
                    "sa_cost": sa_cost,
                    "delta_cost": sa_cost - default_cost,
                    "improved": sa_cost < default_cost,
                    "feasible": is_feasible_placement(topology, s_table, sa_centers),
                    "default_worst_il_db": metric.worst_insertion_loss_db,
                    "sa_worst_il_db": sa_metric.worst_insertion_loss_db,
                    "default_avg_il_db": metric.average_insertion_loss_db,
                    "sa_avg_il_db": sa_metric.average_insertion_loss_db,
                }
            )
            _write_placement(outdir / f"{stem}_sa_centers.csv", sa_centers)
            _write_optional_visual(
                outdir / f"{stem}_sa.png",
                allow_skip=args.n_logical != 6,
                write=lambda: save_routing_png(
                    topology,
                    permutation,
                    states,
                    s_table,
                    outdir / f"{stem}_sa.png",
                    centers=sa_centers,
                    title_suffix="SA",
                    routing_rules=debug_routing_rules,
                    debug_physical_overlay=args.physical_debug_overlay,
                    cell_geometry=cli_geometry,
                ),
            )
            if args.gif:
                _write_optional_visual(
                    outdir / f"{stem}_sa.gif",
                    allow_skip=args.n_logical != 6,
                    write=lambda: save_routing_gif(
                        topology,
                        permutation,
                        states,
                        s_table,
                        outdir / f"{stem}_sa.gif",
                        centers=sa_centers,
                        title_suffix="SA",
                        routing_rules=debug_routing_rules,
                        debug_physical_overlay=args.physical_debug_overlay,
                        cell_geometry=cli_geometry,
                    ),
                )
        if args.lp:
            lp_train_perms, _ = _make_permutation_split(
                topology.N_logical,
                args.lp_train,
                args,
                n_eval=0,
            )
            lp_centers = lp_placement(
                topology,
                s_table,
                lp_train_perms,
                safety_um=args.lp_safety_um,
            )
            centers_by_topology[topology.name] = lp_centers
            _write_placement(outdir / f"{stem}_lp_centers.csv", lp_centers)

    _write_summary(outdir / "routing_summary.json", metrics)
    _write_comparison(outdir / "routing_comparison.csv", metrics)
    if args.activity_scan:
        activity_rows: list[MRRActivity] = []
        for topology in topologies:
            activity_perms = None
            if args.eval_mode == "sample":
                _activity_train, activity_perms = _make_permutation_split(
                    topology.N_logical,
                    0,
                    args,
                    n_eval=args.eval_samples,
                )
            activity_scan_rows = scan_mrr_activity(topology, activity_perms)
            activity_rows.extend(activity_scan_rows)
            activity_summary_rows.append(summarize_mrr_activity(activity_scan_rows))
        _write_activity_rows(outdir / "mrr_activity.csv", activity_rows)
        _write_activity_summary(outdir / "mrr_activity_summary.csv", activity_summary_rows)
    if sa_rows:
        _write_sa_summary(outdir / "sa_summary.csv", sa_rows)
    if args.eval:
        for topology in topologies:
            train_perms, eval_perms = _make_permutation_split(
                topology.N_logical,
                args.train_samples,
                args,
                n_eval=args.eval_samples,
            )
            dataset = build_evaluation_dataset(topology, train_perms, eval_perms)
            path_depth_ranges[topology.name] = _path_depth_range(dataset.eval_plans)
            default_centers = {
                mrr_id: cell.center
                for mrr_id, cell in build_cells(
                    topology,
                    s_table,
                    cell_geometry=cli_geometry,
                ).items()
            }
            default_rows = _collect_eval_rows(
                topology,
                dataset.eval_plans,
                s_table,
                default_centers,
                "default",
                args.sxr_min_db,
            )
            eval_rows.extend(default_rows)
            eval_summary_rows.append(_summarize_eval_rows(topology.name, "default", default_rows))
            if args.sa:
                sa_center_path = outdir / (
                    f"{topology.name}_{'-'.join(map(str, permutation))}_sa_centers.csv"
                )
                sa_centers_for_eval = _read_placement(sa_center_path)
                sa_eval_rows = _collect_eval_rows(
                    topology,
                    dataset.eval_plans,
                    s_table,
                    sa_centers_for_eval,
                    "sa",
                    args.sxr_min_db,
                )
                eval_rows.extend(sa_eval_rows)
                eval_summary_rows.append(_summarize_eval_rows(topology.name, "sa", sa_eval_rows))
            if args.lp:
                lp_center_path = outdir / (
                    f"{topology.name}_{'-'.join(map(str, permutation))}_lp_centers.csv"
                )
                lp_centers_for_eval = _read_placement(lp_center_path)
                lp_eval_rows = _collect_eval_rows(
                    topology,
                    dataset.eval_plans,
                    s_table,
                    lp_centers_for_eval,
                    "lp",
                    args.sxr_min_db,
                )
                eval_rows.extend(lp_eval_rows)
                eval_summary_rows.append(_summarize_eval_rows(topology.name, "lp", lp_eval_rows))
        _write_eval_distribution(outdir / "eval_path_distribution.csv", eval_rows)
        _write_eval_summary(outdir / "eval_summary.csv", eval_summary_rows)
    if args.physical_eval:
        physical_rules = _physical_rules_from_args(args)
        physical_rows: list[dict[str, object]] = []
        physical_summary_rows: list[dict[str, object]] = []
        physical_drc_rows: list[dict[str, object]] = []
        layout_name = _selected_layout_name(args)
        for topology in topologies:
            physical_path_rows, summary_row, drc_rows = _collect_physical_eval_rows(
                topology,
                permutation,
                s_table,
                centers_by_topology[topology.name],
                layout_name,
                physical_rules,
                stage_pitch_um=args.stage_pitch_um,
                wire_pitch_um=args.wire_pitch_um,
                x_start_um=args.x_start_um,
                x_end_margin_um=args.x_end_margin_um,
            )
            physical_rows.extend(physical_path_rows)
            physical_summary_rows.append(summary_row)
            physical_drc_rows.extend(drc_rows)
        _write_dict_rows(outdir / "physical_path_distribution.csv", physical_rows)
        _write_dict_rows(outdir / "physical_summary.csv", physical_summary_rows)
        _write_dict_rows(
            outdir / "drc_violations.csv",
            physical_drc_rows,
            fieldnames=[
                "topology",
                "layout",
                "permutation",
                "rule",
                "net_id",
                "message",
                "x_um",
                "y_um",
            ],
        )
    if args.paper_table:
        paper_rows = _build_paper_rows(
            topologies,
            eval_rows,
            eval_summary_rows,
            activity_summary_rows,
            sa_rows,
            path_depth_ranges,
        )
        _write_paper_table(outdir / "paper_comparison_table.csv", paper_rows)
    if args.plot_distributions and args.eval:
        save_il_sxr_cdf(
            eval_rows,
            outdir / "il_sxr_cdf.png",
            sxr_min_db=args.sxr_min_db,
        )
    if args.analytic_edges:
        _train, analytic_perms = _make_permutation_split(
            topologies[0].N_logical,
            args.train_samples,
            args,
            n_eval=args.eval_samples,
        )
        analytic_edge_rows: list[AnalyticEdge] = []
        for topology in topologies:
            topo_centers = centers_by_topology[topology.name]
            analytic_edge_rows.extend(
                analytic_edge_costs(topology, analytic_perms, s_table, centers=topo_centers)
            )
        _write_analytic_edges(outdir / "analytic_edges.csv", analytic_edge_rows)
    if args.breakeven:
        _train, breakeven_eval_perms = _make_permutation_split(
            topologies[0].N_logical,
            args.train_samples,
            args,
            n_eval=args.eval_samples,
        )
        crossing_loss_sweep_db = [
            0.0,
            0.1,
            0.2,
            0.3,
            0.4,
            0.5,
            0.6,
            0.8,
            1.0,
            1.5,
            2.0,
        ]
        _run_breakeven(
            topologies,
            breakeven_eval_perms,
            s_table,
            centers_by_topology,
            outdir,
            crossing_loss_sweep_db,
        )
    for metric in metrics:
        print(
            f"{metric.topology}: worst IL={metric.worst_insertion_loss_db:.2f} dB, "
            f"avg IL={metric.average_insertion_loss_db:.2f} dB, "
            f"worst SXR={metric.worst_sxr_db:.2f} dB, "
            f"crossings={metric.crossing_count}"
        )
    for row in sa_rows:
        print(
            f"{row['topology']} SA: C={row['default_cost']:.4f}->{row['sa_cost']:.4f}, "
            f"delta={row['delta_cost']:.4f}, feasible={row['feasible']}"
        )
    print(f"wrote outputs to {outdir}")


def _run_fixed_fabric(
    args: argparse.Namespace,
    comparison_permutation: tuple[int, ...],
    s_table: dict[tuple[str, str, int], float],
    outdir: Path,
) -> FabricWorstILReport:
    if args.topology in {"main", "benes"}:
        topology_selector = "benes"
    elif args.topology == "waksman":
        topology_selector = "waksman"
    else:
        raise ValueError(
            "--fixed-fabric currently supports --topology benes or waksman"
        )
    topology = _select_topologies(topology_selector, args.n_logical)[0]
    graph = build_fabric_graph(topology)
    cells = build_cells(
        topology,
        s_table,
        stage_pitch_um=args.fabric_stage_pitch_um,
        wire_pitch_um=args.fabric_wire_pitch_um,
        cell_geometry=LEGACY_V2_CELL_GEOMETRY,
    )
    rules = replace(
        _physical_rules_from_args(args),
        max_astar_pops=args.fabric_max_astar_pops,
        max_ripup_passes=args.fabric_max_ripup_passes,
        grid_margin_tracks=args.fabric_grid_margin_tracks,
        waveguide_width_um=LEGACY_V2_CELL_GEOMETRY.waveguide_width_um,
    )
    x_end_um = max(cell.center[0] for cell in cells.values()) + args.x_end_margin_um
    result = route_fixed_fabric(
        topology,
        graph,
        cells,
        rules,
        x_start=args.x_start_um,
        x_end=x_end_um,
        wire_pitch_um=args.fabric_wire_pitch_um,
    )
    coverage = verify_permutation_coverage(topology, graph)
    loss_report = evaluate_fixed_fabric_worst_insertion_loss(
        topology,
        cells,
        result,
    )

    legacy_comparison: dict[str, object] | None = None
    if args.legacy_comparison:
        legacy_paths = topology.get_active_paths(comparison_permutation)
        legacy = route_physical_design(
            legacy_paths,
            cells,
            rules,
            x_start=args.x_start_um,
            x_end=x_end_um,
            wire_pitch_um=args.fabric_wire_pitch_um,
        )
        legacy_comparison = {
            "topology": topology.name,
            "comparison_permutation": comparison_permutation,
            "fixed_fabric": {
                "physical_route_runs": 1,
                "waveguides": len(result.routes),
                "routed_edges": len(result.routed_edge_ids),
                "failed_edges": len(result.failed_edges),
                "drc_violations": len(result.drc_violations),
                "crossings": len(result.crossings),
            },
            "legacy_active_path": {
                "physical_route_runs_per_permutation": 1,
                "active_paths": len(legacy_paths),
                "routed_paths": len(legacy.routes),
                "failed_paths": len(legacy.failed_nets),
                "drc_violations": len(legacy.drc_violations),
                "crossings": len(legacy.crossings),
            },
            "models_are_not_numerically_interchangeable": True,
        }
    fabric_outdir = outdir / "fixed_fabric"
    write_fixed_fabric_reports(
        fabric_outdir,
        graph,
        result,
        coverage,
        legacy_comparison,
        loss_report,
    )
    save_fixed_fabric_png(
        topology,
        cells,
        result,
        fabric_outdir / "fixed_fabric_layout.png",
        wire_pitch_um=args.fabric_wire_pitch_um,
        x_start_um=args.x_start_um,
        x_end_um=x_end_um,
    )
    return loss_report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add-drop MRR routing demo")
    parser.add_argument(
        "--permutation",
        default=None,
        help="logical output for inputs 0..N-1, e.g. 2,0,5,1,3,4",
    )
    parser.add_argument("--n-logical", type=int, default=6, help="logical switch radix N")
    parser.add_argument(
        "--topology",
        choices=("main", "all", "paper", "benes", "waksman", "sb", "sb-rect"),
        default="main",
        help=(
            "main routes Beneš and Waksman; all also includes Spanke-Beneš; "
            "or select one topology"
        ),
    )
    parser.add_argument("--sparam-dir", default=None)
    parser.add_argument("--outdir", default="outputs")
    parser.add_argument("--paper-run", default=None, help="YAML config for reproducible paper run")
    parser.add_argument(
        "--paper-artifacts",
        default=None,
        help="existing run directory to regenerate paper tables and figures",
    )
    parser.add_argument(
        "--render-worst-il-layouts",
        default=None,
        metavar="CONFIG",
        help="paper-run YAML of an existing run: re-render the worst-IL routed "
        "layout PNG for each (topology, N) without re-running the batch",
    )
    parser.add_argument("--radius-um", type=float, default=5.0)
    parser.add_argument("--channel-nm", type=float, default=1550.0)
    parser.add_argument("--wavelength-nm", type=float, default=1550.0)
    parser.add_argument("--gif", action="store_true", help="also write animated GIFs")
    parser.add_argument("--sa", action="store_true", help="run SA placement and write *_sa outputs")
    parser.add_argument("--sa-train", type=int, default=50, help="number of train permutations for SA")
    parser.add_argument("--lp", action="store_true", help="run LP placement (exact L1-optimal wiring)")
    parser.add_argument("--lp-train", type=int, default=500, help="number of train permutations for LP")
    parser.add_argument("--lp-safety-um", type=float, default=40.0, help="LP x-bound safety margin per stage")
    parser.add_argument("--split-seed", type=int, default=42)
    parser.add_argument("--perm-seed", type=int, default=None)
    parser.add_argument("--sa-seed", type=int, default=0)
    parser.add_argument("--sa-restarts", type=int, default=2)
    parser.add_argument("--sa-t-init", type=float, default=2.0)
    parser.add_argument("--sa-t-min", type=float, default=0.02)
    parser.add_argument("--sa-cooling", type=float, default=0.8)
    parser.add_argument("--sa-moves-per-temp", type=int, default=200)
    parser.add_argument("--eval", action="store_true", help="run §11 eval-set validation")
    parser.add_argument("--eval-train", type=int, default=500, help="train split size before eval set")
    parser.add_argument("--train-samples", type=int, default=None)
    parser.add_argument("--eval-samples", type=int, default=300)
    parser.add_argument("--logical-matrix", action="store_true")
    parser.add_argument("--matrix-n-values", default="4,6,7,8,16")
    parser.add_argument("--matrix-train-samples", type=int, default=None)
    parser.add_argument("--matrix-eval-samples", type=int, default=None)
    parser.add_argument(
        "--matrix-crossing-loss-db",
        type=float,
        default=0.0,
        help=(
            "dB charged per native waveguide crossing in logical-matrix IL; "
            "set equal to physical_batch.crossing_loss_db_per_cross for a "
            "layer-consistent loss model"
        ),
    )
    parser.add_argument(
        "--eval-mode",
        choices=("exhaustive", "sample"),
        default=None,
        help="permutation protocol; defaults to exhaustive for N<=6 and sample otherwise",
    )
    parser.add_argument("--sxr-min-db", type=float, default=20.0)
    parser.add_argument("--activity-scan", action="store_true", help="scan all permutations for MRR activity")
    parser.add_argument("--paper-table", action="store_true", help="write paper_comparison_table.csv")
    parser.add_argument(
        "--plot-distributions",
        action="store_true",
        help="write il_sxr_cdf.png when --eval is enabled",
    )
    parser.add_argument(
        "--breakeven",
        action="store_true",
        help="run crossing insertion-loss breakeven sweep",
    )
    parser.add_argument(
        "--analytic-edges",
        action="store_true",
        help="write analytic_edges.csv with §8 surrogate features (B̂, Ĉ, D̂, Â, l̂) for eval permutations",
    )
    parser.add_argument(
        "--physical-eval",
        action="store_true",
        help="run waveguide-aware physical routing and write physical CSV reports",
    )
    parser.add_argument(
        "--fixed-fabric",
        action="store_true",
        help=(
            "route one immutable Padded Beneš waveguide fabric and verify all "
            "permutations against the same geometry"
        ),
    )
    parser.add_argument(
        "--legacy-comparison",
        action="store_true",
        help="also route one active permutation for legacy-model comparison",
    )
    parser.add_argument("--fabric-stage-pitch-um", type=float, default=140.0)
    parser.add_argument("--fabric-wire-pitch-um", type=float, default=64.0)
    parser.add_argument("--fabric-max-astar-pops", type=int, default=30000)
    parser.add_argument("--fabric-max-ripup-passes", type=int, default=5)
    parser.add_argument("--fabric-grid-margin-tracks", type=float, default=20.0)
    parser.add_argument("--physical-batch", action="store_true")
    parser.add_argument("--physical-batch-samples", type=int, default=20)
    parser.add_argument("--physical-n-values", default="4,6,8")
    parser.add_argument(
        "--max-physical-n",
        type=int,
        default=8,
        help="largest n_logical accepted by --physical-batch (guard, not an algorithm limit)",
    )
    parser.add_argument(
        "--physical-full-enum-max-n",
        type=int,
        default=0,
        help="route ALL n! permutations for n_logical <= this value instead of "
        "sampling --physical-batch-samples (0=off; exhaustive split requires n <= 8)",
    )
    parser.add_argument("--calibrate", action="store_true")
    parser.add_argument("--calibration-input", default=None)
    parser.add_argument("--calibration-holdout-fraction", type=float, default=0.4)
    parser.add_argument("--calibration-ridge-lambda", type=float, default=1e-6)
    parser.add_argument("--grid-pitch-um", type=float, default=8.0)
    parser.add_argument("--grid-merge-tol-um", type=float, default=3.5)
    parser.add_argument("--min-spacing-um", type=float, default=4.0)
    parser.add_argument(
        "--waveguide-width-um",
        type=float,
        default=DEFAULT_CELL_GEOMETRY.waveguide_width_um,
    )
    parser.add_argument("--mrr-keepout-um", type=float, default=6.0)
    parser.add_argument("--port-escape-um", type=float, default=10.0)
    parser.add_argument("--crossing-penalty-um", type=float, default=20.0)
    parser.add_argument("--turn-guard-um", type=float, default=16.0)
    parser.add_argument("--turn-timing-penalty-um", type=float, default=48.0)
    parser.add_argument("--backtrack-penalty-um", type=float, default=80.0)
    parser.add_argument("--hairpin-penalty-um", type=float, default=200.0)
    parser.add_argument(
        "--cost-model",
        choices=("um_penalty", "db"),
        default="um_penalty",
    )
    parser.add_argument("--db-tie-breaker-scale", type=float, default=0.05)
    parser.add_argument("--loss-aware-cost", action="store_true")
    parser.add_argument("--enforce-bend-spacing", action="store_true")
    parser.add_argument("--legalize-port-access", action="store_true")
    parser.add_argument("--port-access-runway-um", type=float, default=10.0)
    parser.add_argument("--drc-same-net-min-spacing", action="store_true")
    parser.add_argument("--drc-perpendicular-clearance", action="store_true")
    parser.add_argument("--drc-bend-radius-legality", action="store_true")
    parser.add_argument(
        "--allow-ripup-astar",
        action="store_true",
        help=(
            "let capped rip-up passes fall back to bounded A* when the "
            "deterministic candidates are exhausted (sets "
            "RoutingRules.capped_ripup_bounded_only=False)"
        ),
    )
    parser.add_argument("--prop-loss-db-per-um", type=float, default=ALPHA_DB_PER_UM)
    parser.add_argument("--bend-loss-db-per-bend", type=float, default=0.0)
    parser.add_argument(
        "--crossing-loss-db-per-cross",
        type=float,
        default=CROSSING_LOSS_DB_PER_CROSS,
    )
    parser.add_argument("--repeated-crossing-loss-db", type=float, default=0.0)
    parser.add_argument("--jog-penalty-db", type=float, default=0.0)
    parser.add_argument("--bend-placement-penalty-db", type=float, default=0.0)
    parser.add_argument("--max-astar-pops", type=int, default=None)
    parser.add_argument("--ripup-max-astar-pops", type=int, default=None)
    parser.add_argument("--local-repair-max-shift-tracks", type=int, default=8)
    parser.add_argument(
        "--route-window-max-detour-tracks",
        type=int,
        default=4,
        help="per-hop A* route-window detour room in grid tracks",
    )
    parser.add_argument(
        "--grid-margin-tracks",
        type=float,
        default=4.0,
        help="global routing-grid margin beyond outermost cells/IO, in grid tracks",
    )
    parser.add_argument("--max-ripup-passes", type=int, default=3)
    parser.add_argument(
        "--early-stop-stagnant-passes",
        type=int,
        default=0,
        help="stop rip-up after this many consecutive non-improving passes (0=off)",
    )
    parser.add_argument("--stage-pitch-um", type=float, default=105.0)
    parser.add_argument("--wire-pitch-um", type=float, default=36.0)
    parser.add_argument("--x-start-um", type=float, default=20.0)
    parser.add_argument(
        "--x-end-margin-um",
        type=float,
        default=70.0,
        help="routing x_end margin beyond the last MRR column",
    )
    parser.add_argument(
        "--physical-debug-overlay",
        action="store_true",
        help="overlay inflated keepouts and occupied physical tracks on routing PNG/GIF",
    )
    args = parser.parse_args()
    if args.n_logical < 2:
        parser.error("--n-logical must be at least 2")
    if args.perm_seed is None:
        args.perm_seed = args.split_seed
    if args.train_samples is None:
        args.train_samples = args.eval_train
    if args.eval_mode is None:
        args.eval_mode = "exhaustive" if args.n_logical <= 6 else "sample"
    if args.eval_mode == "exhaustive" and args.n_logical > 8:
        parser.error("--eval-mode exhaustive is limited to --n-logical <= 8")
    if args.paper_table and (not args.eval or not args.activity_scan):
        raise argparse.ArgumentError(
            None,
            "--paper-table requires both --eval and --activity-scan",
        )
    return args


def _parse_permutation(value: str | None, n_logical: int) -> tuple[int, ...]:
    if value is None:
        if n_logical == 6:
            return (2, 0, 5, 1, 3, 4)
        return tuple(range(n_logical))
    permutation = tuple(int(x.strip()) for x in value.split(",") if x.strip())
    if len(permutation) != n_logical:
        raise argparse.ArgumentTypeError(
            f"permutation length {len(permutation)} does not match --n-logical {n_logical}"
        )
    if sorted(permutation) != list(range(n_logical)):
        raise argparse.ArgumentTypeError(
            f"permutation must contain each integer 0..{n_logical - 1} once"
        )
    return permutation


def _select_topologies(selector: str, n_logical: int) -> list[RNBTopology]:
    def padded() -> PaddedBenesTopology:
        if n_logical == 6:
            return PaddedBenesTopology()
        return PaddedBenesTopology(n_logical, strategy=BenesLoopingStrategy())

    def waksman() -> WaksmanTopology:
        if n_logical == 6:
            return WaksmanTopology()
        return WaksmanTopology(n_logical, strategy=WaksmanStrategy())

    def spanke() -> SpankeBenesTopology:
        if n_logical == 6:
            return SpankeBenesTopology()
        return SpankeBenesTopology(n_logical, strategy=SpankeBenesStrategy())

    def spanke_rect() -> SpankeBenesRectTopology:
        return SpankeBenesRectTopology(n_logical, strategy=SpankeBenesRectStrategy())

    if selector == "benes":
        return [padded()]
    if selector == "waksman":
        return [waksman()]
    if selector == "sb":
        return [spanke()]
    if selector == "sb-rect":
        return [spanke_rect()]
    if selector == "all":
        return [padded(), waksman(), spanke()]
    if selector == "paper":
        # The paper comparison set: both Spanke-Benes arrangements, so the
        # canonical rectangular variant cannot be called a strawman omission.
        return [padded(), waksman(), spanke(), spanke_rect()]
    if selector == "main":
        return [padded(), waksman()]
    raise ValueError(
        f"unknown topology selector {selector!r}; "
        "expected one of main, all, paper, benes, waksman, sb, sb-rect"
    )


def _make_permutation_split(
    n_logical: int,
    n_train: int,
    args: argparse.Namespace,
    *,
    n_eval: int | None = None,
) -> tuple[list[tuple[int, ...]], list[tuple[int, ...]]]:
    return make_permutation_split(
        n_logical,
        n_train=n_train,
        seed=args.perm_seed,
        mode=args.eval_mode,
        n_eval=n_eval,
    )


def _write_optional_visual(
    path: Path,
    *,
    allow_skip: bool,
    write: Callable[[], None],
) -> None:
    if allow_skip:
        print(f"skipping {path.name}: plotting is disabled for non-default N")
        return
    try:
        write()
    except RoutingError as exc:
        raise RoutingError(f"failed to write {path.name}: {exc}") from exc


def _load_paper_run_context(
    args: argparse.Namespace,
    config_path: Path,
) -> tuple[dict[object, object], Path, argparse.Namespace]:
    import yaml  # type: ignore[import-untyped]

    with config_path.open() as f:
        config = yaml.safe_load(f) or {}
    if not isinstance(config, dict):
        raise ValueError("--paper-run config must be a YAML mapping")

    seed = int(config.get("seed", args.perm_seed))
    logical_config = _config_mapping(config, "logical_matrix")

    base_outdir = (
        Path(args.outdir)
        if args.outdir != "outputs"
        else Path(str(config.get("outdir", args.outdir)))
    )
    run_id = str(
        config.get(
            "run_id",
            f"{config_path.stem}_{datetime.now().strftime('%Y%m%d')}_seed{seed}",
        )
    )
    run_outdir = base_outdir / run_id

    matrix_args = argparse.Namespace(**vars(args))
    matrix_args.perm_seed = seed
    matrix_args.split_seed = seed
    matrix_args.topology = str(logical_config.get("topology", "all"))
    n_values = logical_config.get("n_values", [4, 6, 8, 16])
    if isinstance(n_values, list):
        matrix_args.matrix_n_values = ",".join(str(value) for value in n_values)
    else:
        matrix_args.matrix_n_values = str(n_values)
    matrix_args.matrix_train_samples = logical_config.get("train_samples")
    matrix_args.matrix_eval_samples = logical_config.get("eval_samples")
    if "crossing_loss_db_per_cross" in logical_config:
        matrix_args.matrix_crossing_loss_db = _as_float(
            logical_config["crossing_loss_db_per_cross"]
        )
    return config, run_outdir, matrix_args


def _run_paper_run(
    args: argparse.Namespace,
    s_table: dict[tuple[str, str, int], float],
) -> Path:
    config_path = Path(args.paper_run)
    config, run_outdir, matrix_args = _load_paper_run_context(args, config_path)
    breakeven_config = _config_mapping(config, "breakeven")
    gallery_config = _config_mapping(config, "layout_gallery")
    physical_config = _config_mapping(config, "physical_batch")
    calibration_config = _config_mapping(config, "calibration")
    run_outdir.mkdir(parents=True, exist_ok=True)

    physical_enabled = _config_enabled(physical_config)
    physical_args = _paper_physical_args(matrix_args, physical_config)
    _write_run_config(
        run_outdir / "run_config.json",
        config_path,
        config,
        matrix_args,
        physical_args=physical_args if physical_enabled else None,
    )
    _run_logical_matrix(matrix_args, s_table, run_outdir)
    if _config_enabled(breakeven_config):
        _run_breakeven_matrix(
            _paper_breakeven_args(matrix_args, breakeven_config),
            s_table,
            run_outdir,
            breakeven_config,
        )
    if physical_enabled:
        _run_physical_batch(
            physical_args,
            s_table,
            run_outdir,
        )
        _render_worst_il_layouts(physical_args, s_table, run_outdir)
    if _config_enabled(gallery_config):
        _run_layout_gallery(
            _paper_gallery_args(matrix_args, gallery_config),
            s_table,
            run_outdir,
            routing_rules=_physical_rules_from_args(physical_args),
        )
    if _config_enabled(calibration_config, default=physical_enabled):
        _run_calibration(_paper_calibration_args(matrix_args, calibration_config), run_outdir)
    return run_outdir


def _config_mapping(config: dict[object, object], key: str) -> dict[object, object]:
    value = config.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} config must be a YAML mapping")
    return value


def _config_enabled(config: dict[object, object], *, default: bool = False) -> bool:
    return bool(config.get("enabled", default))


def _csv_n_values(value: object, default: str) -> str:
    if value is None:
        return default
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def _paper_breakeven_args(
    args: argparse.Namespace,
    config: dict[object, object],
) -> argparse.Namespace:
    breakeven_args = argparse.Namespace(**vars(args))
    breakeven_args.topology = str(config.get("topology", args.topology))
    breakeven_args.matrix_n_values = _csv_n_values(config.get("n_values"), "6,8")
    breakeven_args.matrix_train_samples = config.get("train_samples", args.matrix_train_samples)
    breakeven_args.matrix_eval_samples = config.get("eval_samples", args.matrix_eval_samples)
    return breakeven_args


def _paper_gallery_args(
    args: argparse.Namespace,
    config: dict[object, object],
) -> argparse.Namespace:
    gallery_args = argparse.Namespace(**vars(args))
    gallery_args.topology = str(config.get("topology", args.topology))
    gallery_args.matrix_n_values = _csv_n_values(config.get("n_values"), "6,8")
    return gallery_args


def _paper_physical_args(
    args: argparse.Namespace,
    config: dict[object, object],
) -> argparse.Namespace:
    physical_args = argparse.Namespace(**vars(args))
    physical_args.topology = str(config.get("topology", args.topology))
    physical_args.physical_n_values = _csv_n_values(config.get("n_values"), args.physical_n_values)
    physical_args.physical_batch_samples = _as_int(
        config.get("samples", args.physical_batch_samples)
    )
    physical_args.physical_full_enum_max_n = _as_int(
        config.get("full_enum_max_n", args.physical_full_enum_max_n)
    )
    for key in (
        "grid_pitch_um",
        "grid_merge_tol_um",
        "min_spacing_um",
        "waveguide_width_um",
        "mrr_keepout_um",
        "port_escape_um",
        "crossing_penalty_um",
        "turn_guard_um",
        "turn_timing_penalty_um",
        "backtrack_penalty_um",
        "hairpin_penalty_um",
        "loss_aware_cost",
        "allow_ripup_astar",
        "prop_loss_db_per_um",
        "bend_loss_db_per_bend",
        "crossing_loss_db_per_cross",
        "repeated_crossing_loss_db",
        "jog_penalty_db",
        "bend_placement_penalty_db",
        "max_astar_pops",
        "ripup_max_astar_pops",
        "local_repair_max_shift_tracks",
        "route_window_max_detour_tracks",
        "grid_margin_tracks",
        "max_ripup_passes",
        "early_stop_stagnant_passes",
        "stage_pitch_um",
        "wire_pitch_um",
        "x_start_um",
        "x_end_margin_um",
        "max_physical_n",
    ):
        if key in config:
            setattr(physical_args, key, config[key])
    return physical_args


def _paper_calibration_args(
    args: argparse.Namespace,
    config: dict[object, object],
) -> argparse.Namespace:
    calibration_args = argparse.Namespace(**vars(args))
    if "input" in config:
        calibration_args.calibration_input = str(config["input"])
    if "holdout_fraction" in config:
        calibration_args.calibration_holdout_fraction = _as_float(config["holdout_fraction"])
    if "ridge_lambda" in config:
        calibration_args.calibration_ridge_lambda = _as_float(config["ridge_lambda"])
    return calibration_args


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str, bytes, bytearray)):
        return int(value)
    raise TypeError(f"cannot convert {value!r} to int")


def _as_float(value: object) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, str, bytes, bytearray)):
        return float(value)
    raise TypeError(f"cannot convert {value!r} to float")


def _resolved_sparam_dir(args: argparse.Namespace) -> Path:
    project_root = Path(__file__).resolve().parents[2]
    return Path(args.sparam_dir) if args.sparam_dir else project_root / "mrr_sparam_library"


def _sparam_provenance(args: argparse.Namespace) -> dict[str, object]:
    if args.sparam_dir == "MOCK":
        return {"source": "mock_explicit", "library_dir": None}
    library_dir = _resolved_sparam_dir(args)
    on_file = library_dir / f"mrr_r{args.radius_um:.2f}_ch{args.channel_nm:.1f}_on.csv"
    off_file = library_dir / f"mrr_r{args.radius_um:.2f}_ch{args.channel_nm:.1f}_off.csv"
    loaded = on_file.exists() and off_file.exists()
    return {
        "source": "library" if loaded else "mock_fallback",
        "library_dir": str(library_dir),
        "radius_um": args.radius_um,
        "channel_nm": args.channel_nm,
        "wavelength_nm": args.wavelength_nm,
    }


def _write_run_config(
    path: Path,
    config_path: Path,
    config: dict[object, object],
    args: argparse.Namespace,
    physical_args: argparse.Namespace | None = None,
) -> None:
    resolved = {
        "argv": sys.argv,
        "config_path": str(config_path),
        "config": config,
        "resolved": {
            "topology": args.topology,
            "matrix_n_values": _parse_n_values(args.matrix_n_values),
            "matrix_train_samples": args.matrix_train_samples,
            "matrix_eval_samples": args.matrix_eval_samples,
            "breakeven": config.get("breakeven", {}),
            "layout_gallery": config.get("layout_gallery", {}),
            "physical_batch": config.get("physical_batch", {}),
            "calibration": config.get("calibration", {}),
            "perm_seed": args.perm_seed,
            "split_seed": args.split_seed,
            "sa_seed": args.sa_seed,
        },
        "versions": {
            "git_sha": _git_sha(),
            "package_version": _package_version(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "sparams": _sparam_provenance(args),
        "permutation_lists": _permutation_hashes_for_matrix(args),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    if physical_args is not None:
        resolved["physical_permutation_lists"] = _permutation_hashes_for_physical(
            physical_args
        )
    with path.open("w") as f:
        json.dump(resolved, f, indent=2, sort_keys=True)


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _package_version() -> str:
    try:
        return version("mrr-switch-optimizer")
    except PackageNotFoundError:
        return "unknown"


def _permutation_hashes_for_physical(
    args: argparse.Namespace,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for n_logical in _parse_n_values(args.physical_n_values):
        if n_logical <= args.physical_full_enum_max_n:
            _train, permutations = make_permutation_split(
                n_logical, n_train=0, seed=args.perm_seed, mode="exhaustive"
            )
            mode = "exhaustive"
        else:
            _train, permutations = make_permutation_split(
                n_logical,
                n_train=0,
                seed=args.perm_seed,
                mode="sample",
                n_eval=args.physical_batch_samples,
            )
            mode = "sample"
        rows.append(
            {
                "n_logical": n_logical,
                "mode": mode,
                "attempt_count": len(permutations),
                "perm_sha256": _hash_permutations(permutations),
            }
        )
    return rows


def _permutation_hashes_for_matrix(args: argparse.Namespace) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for n_logical in _parse_n_values(args.matrix_n_values):
        mode, n_train, n_eval = _matrix_protocol(n_logical, args)
        train, eval_perms = make_permutation_split(
            n_logical,
            n_train=n_train,
            seed=args.perm_seed,
            mode=mode,
            n_eval=n_eval,
        )
        rows.append(
            {
                "n_logical": n_logical,
                "mode": mode,
                "train_count": len(train),
                "eval_count": len(eval_perms),
                "train_sha256": _hash_permutations(train),
                "eval_sha256": _hash_permutations(eval_perms),
            }
        )
    return rows


def _hash_permutations(permutations: list[tuple[int, ...]]) -> str:
    payload = json.dumps([list(permutation) for permutation in permutations], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _run_logical_matrix(
    args: argparse.Namespace,
    s_table: dict[tuple[str, str, int], float],
    outdir: Path,
) -> None:
    matrix_root = outdir / "logical_matrix"
    matrix_root.mkdir(parents=True, exist_ok=True)
    selector = "all" if args.topology == "main" else args.topology
    summary_rows: list[dict[str, object]] = []
    for n_logical in _parse_n_values(args.matrix_n_values):
        mode, n_train, n_eval = _matrix_protocol(n_logical, args)
        train_perms, eval_perms = make_permutation_split(
            n_logical,
            n_train=n_train,
            seed=args.perm_seed,
            mode=mode,
            n_eval=n_eval,
        )
        for topology in _select_topologies(selector, n_logical):
            topology_dir = matrix_root / f"{topology.name}_n{n_logical}"
            completed_summary = _load_completed_matrix_cell(
                topology_dir,
                n_logical=n_logical,
                seed=args.perm_seed,
                mode=mode,
                train_count=len(train_perms),
                eval_count=len(eval_perms),
                crossing_loss_db=args.matrix_crossing_loss_db,
            )
            if completed_summary is not None:
                summary_rows.append(completed_summary)
                continue
            dataset = build_evaluation_dataset(topology, train_perms, eval_perms)
            centers = {
                mrr_id: cell.center for mrr_id, cell in build_cells(topology, s_table).items()
            }
            rows = _collect_eval_rows(
                topology,
                dataset.eval_plans,
                s_table,
                centers,
                "default",
                args.sxr_min_db,
                crossing_loss_db_per_cross=args.matrix_crossing_loss_db,
            )
            for row in rows:
                row["n_logical"] = n_logical
                row["layer"] = "logical"
                row["seed"] = args.perm_seed
                row["eval_mode"] = mode
            summary = _summarize_eval_rows(topology.name, "default", rows)
            summary.update(
                {
                    "n_logical": n_logical,
                    "n_physical": topology.N_physical,
                    "layer": "logical",
                    "seed": args.perm_seed,
                    "eval_mode": mode,
                    "train_perms": len(train_perms),
                    "eval_perms": len(eval_perms),
                    "n_mrr": topology.n_MRR,
                    "n_stages": topology.n_stages,
                    "avg_crossing_count": _avg_perm_crossing_count(rows),
                    "crossing_loss_db_per_cross": args.matrix_crossing_loss_db,
                }
            )
            summary_rows.append(summary)
            topology_dir.mkdir(parents=True, exist_ok=True)
            _write_eval_distribution(topology_dir / "eval_path_distribution.csv", rows)
            _write_eval_summary(topology_dir / "eval_summary.csv", [summary])
    _write_dict_rows(matrix_root / "logical_matrix_summary.csv", summary_rows)


def _avg_perm_crossing_count(rows: list[dict[str, object]]) -> float:
    crossing_by_perm: dict[object, float] = {
        row["perm_idx"]: _as_float(row["perm_crossing_count"]) for row in rows
    }
    if not crossing_by_perm:
        return 0.0
    return sum(crossing_by_perm.values()) / len(crossing_by_perm)


def _load_completed_matrix_cell(
    topology_dir: Path,
    *,
    n_logical: int,
    seed: int,
    mode: str,
    train_count: int,
    eval_count: int,
    crossing_loss_db: float,
) -> dict[str, object] | None:
    """Reuse a finished logical-matrix cell so interrupted runs can resume."""
    summary_path = topology_dir / "eval_summary.csv"
    distribution_path = topology_dir / "eval_path_distribution.csv"
    if not summary_path.exists() or not distribution_path.exists():
        return None
    rows = _read_optional_csv_rows(summary_path)
    if len(rows) != 1:
        return None
    row = rows[0]
    expected = {
        "n_logical": str(n_logical),
        "seed": str(seed),
        "eval_mode": mode,
        "train_perms": str(train_count),
        "eval_perms": str(eval_count),
        "crossing_loss_db_per_cross": str(crossing_loss_db),
    }
    for key, value in expected.items():
        if str(row.get(key, "")) != value:
            return None
    return dict(row)


def _run_breakeven_matrix(
    args: argparse.Namespace,
    s_table: dict[tuple[str, str, int], float],
    outdir: Path,
    config: dict[object, object],
) -> None:
    selector = "all" if args.topology == "main" else args.topology
    crossing_loss_sweep_db = _crossing_loss_sweep(config)
    rows: list[dict[str, object]] = []
    for n_logical in _parse_n_values(args.matrix_n_values):
        mode, n_train, n_eval = _matrix_protocol(n_logical, args)
        _train_perms, eval_perms = make_permutation_split(
            n_logical,
            n_train=n_train,
            seed=args.perm_seed,
            mode=mode,
            n_eval=n_eval,
        )
        for topology in _select_topologies(selector, n_logical):
            centers = {
                mrr_id: cell.center for mrr_id, cell in build_cells(topology, s_table).items()
            }
            for crossing_loss_db in crossing_loss_sweep_db:
                metrics = [
                    evaluate_routing(
                        topology,
                        permutation,
                        s_table,
                        crossing_loss_db_per_cross=crossing_loss_db,
                        centers=centers,
                    )
                    for permutation in eval_perms
                ]
                rows.append(
                    {
                        "topology": topology.name,
                        "n_logical": n_logical,
                        "layer": "logical",
                        "seed": args.perm_seed,
                        "eval_mode": mode,
                        "eval_perms": len(eval_perms),
                        "crossing_loss_db_per_cross": crossing_loss_db,
                        "worst_il_db": max(
                            metric.worst_insertion_loss_db for metric in metrics
                        ),
                        "worst_sxr_db": min(metric.worst_sxr_db for metric in metrics),
                    }
                )
    _write_dict_rows(outdir / "breakeven_sweep.csv", rows)


def _run_layout_gallery(
    args: argparse.Namespace,
    s_table: dict[tuple[str, str, int], float],
    outdir: Path,
    *,
    routing_rules: RoutingRules | None = None,
) -> None:
    gallery_root = outdir / "layouts"
    gallery_root.mkdir(parents=True, exist_ok=True)
    routed_permutations = _load_routed_gallery_permutations(
        outdir / "physical_batch" / "physical_summary.csv"
    )
    selector = "all" if args.topology == "main" else args.topology
    for n_logical in _parse_n_values(args.matrix_n_values):
        for topology in _select_topologies(selector, n_logical):
            key = (topology.name, n_logical)
            permutation = routed_permutations.get(key)
            render_rules = routing_rules
            if permutation is None:
                permutation = _parse_permutation(None, n_logical)
                render_rules = None
            states = topology.get_state_assignment(permutation)
            stem = f"{topology.name}_n{n_logical}_{'-'.join(map(str, permutation))}"
            png_path = gallery_root / f"{stem}.png"
            error_path = gallery_root / f"{stem}_render_error.csv"
            try:
                save_routing_png(
                    topology,
                    permutation,
                    states,
                    s_table,
                    png_path,
                    title_suffix="paper gallery",
                    routing_rules=render_rules,
                )
                if error_path.exists():
                    error_path.unlink()
            except RoutingError as exc:
                _write_dict_rows(
                    error_path,
                    [
                        {
                            "topology": topology.name,
                            "n_logical": n_logical,
                            "permutation": "-".join(str(x) for x in permutation),
                            "error": str(exc),
                        }
                    ],
                )


def _load_routed_gallery_permutations(
    summary_path: Path,
) -> dict[tuple[str, int], tuple[int, ...]]:
    if not summary_path.exists():
        return {}
    selections: dict[tuple[str, int], tuple[int, ...]] = {}
    with summary_path.open(newline="") as f:
        for row in csv.DictReader(f):
            if row.get("route_status") != "routed":
                continue
            topology = row.get("topology", "")
            try:
                n_logical = int(row.get("n_logical", ""))
                permutation = _parse_hyphen_permutation(row.get("permutation", ""), n_logical)
            except ValueError:
                continue
            selections.setdefault((topology, n_logical), permutation)
    return selections


def _parse_hyphen_permutation(text: str, n_logical: int) -> tuple[int, ...]:
    try:
        permutation = tuple(int(part) for part in text.split("-") if part != "")
    except ValueError as exc:
        raise ValueError(f"invalid permutation {text!r}") from exc
    if len(permutation) != n_logical or set(permutation) != set(range(n_logical)):
        raise ValueError(f"invalid permutation {text!r} for N={n_logical}")
    return permutation


def _crossing_loss_sweep(config: dict[object, object]) -> list[float]:
    value = config.get("crossing_loss_db_per_cross")
    if value is None:
        return [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.5, 2.0]
    if isinstance(value, list):
        return [_as_float(item) for item in value]
    return [_as_float(value)]


def _run_physical_batch(
    args: argparse.Namespace,
    s_table: dict[tuple[str, str, int], float],
    outdir: Path,
) -> None:
    batch_root = outdir / "physical_batch"
    batch_root.mkdir(parents=True, exist_ok=True)
    selector = args.topology
    rules = _physical_rules_from_args(args)
    all_path_rows = _read_optional_csv_rows(
        batch_root / "physical_path_distribution.csv"
    )
    all_summary_rows = _read_optional_csv_rows(batch_root / "physical_summary.csv")
    all_drc_rows = _read_optional_csv_rows(batch_root / "drc_violations.csv")
    completed_attempts = _completed_physical_attempts(all_summary_rows)
    for n_logical in _parse_n_values(args.physical_n_values):
        if n_logical > args.max_physical_n:
            raise ValueError(
                f"--physical-batch only supports n_logical <= {args.max_physical_n}; "
                "raise --max-physical-n (or physical_batch.max_physical_n in the "
                "run config) to attempt larger N"
            )
        if n_logical <= args.physical_full_enum_max_n:
            _train, permutations = make_permutation_split(
                n_logical,
                n_train=0,
                seed=args.perm_seed,
                mode="exhaustive",
            )
        else:
            _train, permutations = make_permutation_split(
                n_logical,
                n_train=0,
                seed=args.perm_seed,
                mode="sample",
                n_eval=args.physical_batch_samples,
            )
        for topology in _select_topologies(selector, n_logical):
            centers = {
                mrr_id: cell.center
                for mrr_id, cell in build_cells(
                    topology,
                    s_table,
                    stage_pitch_um=args.stage_pitch_um,
                    wire_pitch_um=args.wire_pitch_um,
                ).items()
            }
            for perm_idx, permutation in enumerate(permutations):
                attempt_key = _physical_attempt_key(
                    topology.name,
                    n_logical,
                    args.perm_seed,
                    perm_idx,
                    permutation,
                )
                if attempt_key in completed_attempts:
                    continue
                started = time.perf_counter()
                try:
                    path_rows, summary_row, drc_rows = _collect_physical_eval_rows(
                        topology,
                        permutation,
                        s_table,
                        centers,
                        "default",
                        rules,
                        stage_pitch_um=args.stage_pitch_um,
                        wire_pitch_um=args.wire_pitch_um,
                        x_start_um=args.x_start_um,
                        x_end_margin_um=args.x_end_margin_um,
                    )
                except Exception as exc:  # Physical failures are reported as data.
                    runtime_s = time.perf_counter() - started
                    summary_row = {
                        "topology": topology.name,
                        "layout": "default",
                        "permutation": "-".join(str(x) for x in permutation),
                        "route_status": "error",
                        "failure_reason": str(exc),
                        "paths": topology.N_logical,
                        "routed_paths": 0,
                        "failed_paths": topology.N_logical,
                        "drc_violation_count": "",
                        "crossing_count": "",
                    }
                    path_rows = []
                    drc_rows = []
                else:
                    runtime_s = time.perf_counter() - started
                _enrich_physical_batch_rows(
                    path_rows,
                    summary_row,
                    drc_rows,
                    n_logical=n_logical,
                    perm_idx=perm_idx,
                    seed=args.perm_seed,
                    runtime_s=runtime_s,
                    rules=rules,
                )
                all_path_rows.extend(path_rows)
                all_summary_rows.append(summary_row)
                all_drc_rows.extend(drc_rows)
                completed_attempts.add(attempt_key)
                _write_physical_batch_outputs(
                    batch_root,
                    all_path_rows,
                    all_summary_rows,
                    all_drc_rows,
                )


def _render_worst_il_layouts(
    args: argparse.Namespace,
    s_table: dict[tuple[str, str, int], float],
    outdir: Path,
) -> list[Path]:
    """Re-route and draw, per (topology, N), the fully routed attempt whose
    physical worst IL is highest. The router is deterministic, so re-routing
    with the batch's rules/geometry reproduces the layout that produced the
    reported WIL (x_start/x_end/wire_pitch must match the batch run; the
    route_physical_design defaults equal the batch defaults of 20/max+70/36)."""
    batch_root = outdir / "physical_batch"
    summary_rows = _read_optional_csv_rows(batch_root / "physical_summary.csv")
    if not summary_rows:
        return []
    argmax = worst_il_attempt_by_cell(
        [{key: str(value) for key, value in row.items()} for row in summary_rows]
    )
    if not argmax:
        return []
    rules = _physical_rules_from_args(args)
    layouts_dir = batch_root / "layouts_worst_il"
    layouts_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for (topology_name, n_value), row in sorted(
        argmax.items(), key=lambda item: (item[0][0], int(item[0][1] or 0))
    ):
        if not n_value:
            continue
        n_logical = int(n_value)
        permutation = tuple(int(x) for x in str(row["permutation"]).split("-"))
        topology = next(
            (
                candidate
                for candidate in _select_topologies("paper", n_logical)
                if candidate.name == topology_name
            ),
            None,
        )
        if topology is None:
            print(f"skipping worst-IL layout for unknown topology {topology_name}")
            continue
        centers = {
            mrr_id: cell.center
            for mrr_id, cell in build_cells(
                topology,
                s_table,
                stage_pitch_um=args.stage_pitch_um,
                wire_pitch_um=args.wire_pitch_um,
            ).items()
        }
        states = topology.get_state_assignment(permutation)
        out_path = layouts_dir / (
            f"{topology_name}_perm{row['perm_idx']}_{row['permutation']}.png"
        )
        try:
            save_routing_png(
                topology,
                permutation,
                states,
                s_table,
                out_path,
                centers=centers,
                title_suffix=f" | worst IL {float(row['physical_worst_il_db']):.3f} dB",
                routing_rules=rules,
            )
        except Exception as exc:  # Rendering must not fail the whole run.
            print(f"failed to render worst-IL layout {out_path.name}: {exc}")
            continue
        written.append(out_path)
    return written


def _read_optional_csv_rows(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def _completed_physical_attempts(
    summary_rows: list[dict[str, object]],
) -> set[tuple[str, int, int, int, str]]:
    completed: set[tuple[str, int, int, int, str]] = set()
    for row in summary_rows:
        try:
            completed.add(
                (
                    str(row["topology"]),
                    _as_int(row["n_logical"]),
                    _as_int(row["seed"]),
                    _as_int(row["perm_idx"]),
                    str(row["permutation"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return completed


def _physical_attempt_key(
    topology_name: str,
    n_logical: int,
    seed: int,
    perm_idx: int,
    permutation: tuple[int, ...],
) -> tuple[str, int, int, int, str]:
    return (
        topology_name,
        n_logical,
        seed,
        perm_idx,
        "-".join(str(x) for x in permutation),
    )


def _write_physical_batch_outputs(
    batch_root: Path,
    path_rows: list[dict[str, object]],
    summary_rows: list[dict[str, object]],
    drc_rows: list[dict[str, object]],
) -> None:
    _write_dict_rows(batch_root / "physical_path_distribution.csv", path_rows)
    _write_dict_rows(batch_root / "physical_summary.csv", summary_rows)
    _write_dict_rows(
        batch_root / "drc_violations.csv",
        drc_rows,
        fieldnames=[
            "topology",
            "n_logical",
            "layout",
            "perm_idx",
            "seed",
            "permutation",
            "rule",
            "net_id",
            "message",
            "x_um",
            "y_um",
        ],
    )


def _run_calibration(args: argparse.Namespace, outdir: Path) -> Path:
    input_path = (
        Path(args.calibration_input)
        if args.calibration_input
        else outdir / "physical_batch" / "physical_path_distribution.csv"
    )
    examples = load_physical_path_examples(input_path)
    rows = calibrate_examples(
        examples,
        ridge_lambda=args.calibration_ridge_lambda,
        holdout_fraction=args.calibration_holdout_fraction,
        seed=args.perm_seed,
    )
    report_path = outdir / "surrogate" / "calibration_report.csv"
    write_calibration_report(report_path, rows)
    return report_path


def _enrich_physical_batch_rows(
    path_rows: list[dict[str, object]],
    summary_row: dict[str, object],
    drc_rows: list[dict[str, object]],
    *,
    n_logical: int,
    perm_idx: int,
    seed: int,
    runtime_s: float,
    rules: RoutingRules,
) -> None:
    common = {
        "n_logical": n_logical,
        "layer": "physical",
        "perm_idx": perm_idx,
        "seed": seed,
    }
    for row in path_rows:
        row.update(common)
    for row in drc_rows:
        row.update({key: value for key, value in common.items() if key != "layer"})
    paths = _as_int(summary_row.get("paths") or 0)
    failed_paths = _as_int(summary_row.get("failed_paths") or 0)
    summary_row.update(common)
    summary_row["runtime_s"] = f"{runtime_s:.6f}"
    summary_row["failure_rate"] = failed_paths / paths if paths else 0.0
    summary_row["astar_calls"] = summary_row.get("astar_calls", "")
    summary_row["ripup_passes"] = rules.max_ripup_passes


def _parse_n_values(value: str) -> list[int]:
    n_values = [int(part.strip()) for part in value.split(",") if part.strip()]
    if not n_values:
        raise argparse.ArgumentTypeError("--matrix-n-values must contain at least one N")
    for n_logical in n_values:
        if n_logical < 2:
            raise argparse.ArgumentTypeError("matrix N values must be at least 2")
    return n_values


def _matrix_protocol(
    n_logical: int,
    args: argparse.Namespace,
) -> tuple[str, int, int]:
    if n_logical <= 6:
        mode = "exhaustive"
        # S_4 has 24 and S_5 has 120 permutations, so the N=6 default of 500
        # training permutations cannot apply below N=6.
        default_train = {2: 1, 3: 4, 4: 14, 5: 70}.get(n_logical, 500)
        n_train = (
            args.matrix_train_samples
            if args.matrix_train_samples is not None
            else default_train
        )
        total = 1
        for value in range(2, n_logical + 1):
            total *= value
        # The eval default covers every permutation not reserved for training,
        # so train_samples: 0 makes the eval split truly exhaustive.
        default_eval = total - n_train
    else:
        mode = "sample"
        default_train = 500
        default_eval = 300
        n_train = (
            args.matrix_train_samples
            if args.matrix_train_samples is not None
            else default_train
        )
    n_eval = args.matrix_eval_samples if args.matrix_eval_samples is not None else default_eval
    return mode, n_train, n_eval


def _read_placement(path: Path) -> dict[str, tuple[float, float]]:
    centers: dict[str, tuple[float, float]] = {}
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            centers[row["mrr_id"]] = (float(row["x_um"]), float(row["y_um"]))
    return centers


def _run_sa_restarts(
    topology: RNBTopology,
    s_table: dict[tuple[str, str, int], float],
    train_perms: list[tuple[int, ...]],
    args: argparse.Namespace,
) -> tuple[dict[str, tuple[float, float]], float, int]:
    if args.sa_restarts < 1:
        raise ValueError("--sa-restarts must be at least 1")
    best_centers: dict[str, tuple[float, float]] | None = None
    best_cost = float("inf")
    best_seed = args.sa_seed
    for offset in range(args.sa_restarts):
        seed = args.sa_seed + offset
        centers = sa_placement(
            topology,
            s_table,
            train_perms,
            T_init=args.sa_t_init,
            T_min=args.sa_t_min,
            cooling=args.sa_cooling,
            moves_per_temp=args.sa_moves_per_temp,
            seed=seed,
            sxr_min_db=args.sxr_min_db,
        )
        cost = aggregate_cost(
            topology,
            train_perms,
            s_table,
            sxr_min_db=args.sxr_min_db,
            centers=centers,
        )
        if cost < best_cost:
            best_centers = centers
            best_cost = cost
            best_seed = seed
    if best_centers is None:
        raise RuntimeError("SA restarts produced no placement")
    return best_centers, best_cost, best_seed


def _collect_eval_rows(
    topology: RNBTopology,
    eval_plans: tuple[PermutationPlan, ...],
    s_table: dict[tuple[str, str, int], float],
    centers: dict[str, tuple[float, float]],
    layout_name: str,
    sxr_min_db: float,
    *,
    crossing_loss_db_per_cross: float = 0.0,
) -> list[dict[str, object]]:
    rows = []
    for perm_idx, plan in enumerate(eval_plans):
        metric = evaluate_plan(
            topology,
            plan,
            s_table,
            crossing_loss_db_per_cross=crossing_loss_db_per_cross,
            centers=centers,
        )
        for path_metric in metric.path_metrics:
            rows.append(
                {
                    "topology": topology.name,
                    "layout": layout_name,
                    "perm_idx": perm_idx,
                    "permutation": "-".join(str(x) for x in plan.permutation),
                    "input_port": path_metric.input_port,
                    "output_port": path_metric.output_port,
                    "insertion_loss_db": path_metric.insertion_loss_db,
                    "sxr_db": path_metric.sxr_db,
                    "sxr_violation": path_metric.sxr_db < sxr_min_db,
                    "wiring_um": path_metric.wiring_um,
                    "mrr_loss_db": path_metric.mrr_loss_db,
                    "signal_power": path_metric.signal_power,
                    "leak_power": path_metric.leak_power,
                    "perm_worst_il_db": metric.worst_insertion_loss_db,
                    "perm_worst_sxr_db": metric.worst_sxr_db,
                    "perm_crossing_count": metric.crossing_count,
                }
            )
    return rows


def _summarize_eval_rows(
    topology_name: str,
    layout_name: str,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    il_values = [_as_float(row["insertion_loss_db"]) for row in rows]
    sxr_values = [_as_float(row["sxr_db"]) for row in rows]
    il_mean = sum(il_values) / len(il_values)
    il_std = (sum((v - il_mean) ** 2 for v in il_values) / len(il_values)) ** 0.5
    return {
        "topology": topology_name,
        "layout": layout_name,
        "paths": len(rows),
        "worst_il_db": max(il_values),
        "avg_il_db": il_mean,
        "il_std_db": il_std,
        "il_iqr_db": _percentile(il_values, 0.75) - _percentile(il_values, 0.25),
        "il_p50_db": _percentile(il_values, 0.50),
        "il_p90_db": _percentile(il_values, 0.90),
        "il_p95_db": _percentile(il_values, 0.95),
        "il_p99_db": _percentile(il_values, 0.99),
        "worst_sxr_db": min(sxr_values),
        "avg_sxr_db": sum(sxr_values) / len(sxr_values),
        "sxr_p01_db": _percentile(sxr_values, 0.01),
        "sxr_p05_db": _percentile(sxr_values, 0.05),
        "sxr_violation_count": sum(1 for row in rows if row["sxr_violation"]),
    }


def _percentile(values: list[float], q: float) -> float:
    """Linearly interpolated percentile (numpy 'linear' convention)."""
    sorted_values = sorted(values)
    if not sorted_values:
        raise ValueError("cannot compute percentile of empty list")
    position = q * (len(sorted_values) - 1)
    lower = max(0, min(len(sorted_values) - 1, int(position)))
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def _physical_rules_from_args(args: argparse.Namespace) -> RoutingRules:
    return RoutingRules(
        grid_pitch_um=args.grid_pitch_um,
        grid_merge_tol_um=args.grid_merge_tol_um,
        min_spacing_um=args.min_spacing_um,
        waveguide_width_um=args.waveguide_width_um,
        mrr_keepout_um=args.mrr_keepout_um,
        port_escape_um=args.port_escape_um,
        crossing_penalty_um=args.crossing_penalty_um,
        turn_guard_um=args.turn_guard_um,
        turn_timing_penalty_um=args.turn_timing_penalty_um,
        backtrack_penalty_um=args.backtrack_penalty_um,
        hairpin_penalty_um=args.hairpin_penalty_um,
        cost_model=args.cost_model,
        db_tie_breaker_scale=args.db_tie_breaker_scale,
        loss_aware_cost=args.loss_aware_cost,
        enforce_bend_spacing=args.enforce_bend_spacing,
        legalize_port_access=args.legalize_port_access,
        port_access_runway_um=args.port_access_runway_um,
        drc_same_net_min_spacing=args.drc_same_net_min_spacing,
        drc_perpendicular_clearance=args.drc_perpendicular_clearance,
        drc_bend_radius_legality=args.drc_bend_radius_legality,
        capped_ripup_bounded_only=not args.allow_ripup_astar,
        prop_loss_db_per_um=args.prop_loss_db_per_um,
        bend_loss_db_per_bend=args.bend_loss_db_per_bend,
        crossing_loss_db_per_cross=args.crossing_loss_db_per_cross,
        repeated_crossing_loss_db=args.repeated_crossing_loss_db,
        jog_penalty_db=args.jog_penalty_db,
        bend_placement_penalty_db=args.bend_placement_penalty_db,
        max_astar_pops=args.max_astar_pops,
        ripup_max_astar_pops=args.ripup_max_astar_pops,
        local_repair_max_shift_tracks=args.local_repair_max_shift_tracks,
        route_window_max_detour_tracks=args.route_window_max_detour_tracks,
        grid_margin_tracks=args.grid_margin_tracks,
        max_ripup_passes=args.max_ripup_passes,
        early_stop_stagnant_passes=args.early_stop_stagnant_passes,
    )


def _effective_route_window_slack_um(rules: RoutingRules) -> float:
    """Maximum pass-0 hop-window slack (see routing.physical._hop_routing_window)."""
    tracks = max(
        1,
        min(rules.local_repair_max_shift_tracks, rules.route_window_max_detour_tracks),
    )
    return rules.bend_radius_um + tracks * rules.grid_pitch_um


def _selected_layout_name(args: argparse.Namespace) -> str:
    if args.lp:
        return "lp"
    if args.sa:
        return "sa"
    return "default"


def _collect_physical_eval_rows(
    topology: RNBTopology,
    permutation: tuple[int, ...],
    s_table: dict[tuple[str, str, int], float],
    centers: dict[str, tuple[float, float]],
    layout_name: str,
    rules: RoutingRules,
    *,
    stage_pitch_um: float = 105.0,
    wire_pitch_um: float = 36.0,
    x_start_um: float = 20.0,
    x_end_margin_um: float = 70.0,
) -> tuple[list[dict[str, object]], dict[str, object], list[dict[str, object]]]:
    states = topology.get_state_assignment(permutation)
    paths = topology.get_active_paths(permutation, states)
    cells = build_cells(topology, s_table, centers=centers)
    # The logical reference uses the batch's loss model so il_delta_db
    # measures routing overhead, not a loss-model mismatch between layers.
    topology_metric = evaluate_routing(
        topology,
        permutation,
        s_table,
        alpha_db_per_um=rules.prop_loss_db_per_um,
        crossing_loss_db_per_cross=rules.crossing_loss_db_per_cross,
        centers=centers,
    )
    topology_by_input = {
        path_metric.input_port: path_metric
        for path_metric in topology_metric.path_metrics
    }
    x_end_um = max(cell.center[0] for cell in cells.values()) + x_end_margin_um
    result = route_physical_design(
        paths,
        cells,
        rules=rules,
        x_start=x_start_um,
        x_end=x_end_um,
        wire_pitch_um=wire_pitch_um,
    )
    # Reported physical IL always follows the configured loss model (defaults
    # equal the legacy ALPHA_DB_PER_UM / CROSSING_LOSS_DB_PER_CROSS constants),
    # independent of whether the A* search cost is loss-aware.
    alpha_db_per_um = rules.prop_loss_db_per_um
    crossing_loss_db_per_cross = rules.crossing_loss_db_per_cross
    routes_by_input = {route.input_port: route for route in result.routes}
    failed_by_input = {failed.input_port: failed for failed in result.failed_nets}
    permutation_label = "-".join(str(x) for x in permutation)

    rows: list[dict[str, object]] = []
    physical_il_values: list[float] = []
    il_delta_values: list[float] = []
    physical_wiring_total = 0.0
    for path in sorted(paths, key=lambda item: item.input_port):
        path_metric = topology_by_input[path.input_port]
        route = routes_by_input.get(path.input_port)
        failed = failed_by_input.get(path.input_port)
        if route is None:
            rows.append(
                {
                    "topology": topology.name,
                    "layout": layout_name,
                    "permutation": permutation_label,
                    "input_port": path.input_port,
                    "output_port": path.output_port,
                    "route_status": "failed",
                    "failure_reason": failed.message if failed else "not routed",
                    "physical_wiring_um": "",
                    "bend_count": "",
                    "crossing_count": "",
                    "physical_il_db": "",
                    "topology_il_db": f"{path_metric.insertion_loss_db:.6f}",
                    "il_delta_db": "",
                    "topology_wiring_um": f"{path_metric.wiring_um:.6f}",
                    "wiring_delta_um": "",
                    "mrr_loss_db": f"{path_metric.mrr_loss_db:.6f}",
                    "sxr_db": f"{path_metric.sxr_db:.6f}",
                }
            )
            continue

        physical_wiring_total += route.length_um
        physical_il_db = _physical_route_il_db(
            path_metric.mrr_loss_db,
            route,
            rules,
            alpha_db_per_um=alpha_db_per_um,
            crossing_loss_db_per_cross=crossing_loss_db_per_cross,
        )
        il_delta_db = physical_il_db - path_metric.insertion_loss_db
        wiring_delta_um = route.length_um - path_metric.wiring_um
        physical_il_values.append(physical_il_db)
        il_delta_values.append(il_delta_db)
        rows.append(
            {
                "topology": topology.name,
                "layout": layout_name,
                "permutation": permutation_label,
                "input_port": path.input_port,
                "output_port": path.output_port,
                "route_status": "routed",
                "failure_reason": "",
                "physical_wiring_um": f"{route.length_um:.6f}",
                "bend_count": route.bend_count,
                "crossing_count": route.crossing_count,
                "physical_il_db": f"{physical_il_db:.6f}",
                "topology_il_db": f"{path_metric.insertion_loss_db:.6f}",
                "il_delta_db": f"{il_delta_db:.6f}",
                "topology_wiring_um": f"{path_metric.wiring_um:.6f}",
                "wiring_delta_um": f"{wiring_delta_um:.6f}",
                "mrr_loss_db": f"{path_metric.mrr_loss_db:.6f}",
                "sxr_db": f"{path_metric.sxr_db:.6f}",
            }
        )

    route_status = "routed"
    if result.failed_nets:
        route_status = "failed"
    elif result.drc_violations:
        route_status = "drc_violation"

    summary_row: dict[str, object] = {
        "topology": topology.name,
        "layout": layout_name,
        "permutation": permutation_label,
        "route_status": route_status,
        "paths": len(paths),
        "routed_paths": len(result.routes),
        "failed_paths": len(result.failed_nets),
        "drc_violation_count": len(result.drc_violations),
        "crossing_count": len(result.crossings),
        "braid_pair_count": _braid_pair_count(result.crossings),
        "max_pair_crossing_count": max(
            (count for _pair, count in result.crossing_count_by_pair),
            default=0,
        ),
        "repeated_crossing_pairs": ";".join(
            f"I{pair[0]}-I{pair[1]}:{count}"
            for pair, count in result.crossing_count_by_pair
            if count >= 2
        ),
        "topology_wiring_um": f"{topology_metric.total_wiring_um:.6f}",
        "physical_wiring_um": f"{physical_wiring_total:.6f}",
        "wiring_delta_um": f"{physical_wiring_total - topology_metric.total_wiring_um:.6f}",
        "topology_worst_il_db": f"{topology_metric.worst_insertion_loss_db:.6f}",
        "physical_worst_il_db": f"{max(physical_il_values):.6f}" if physical_il_values else "",
        "worst_il_delta_db": (
            f"{max(physical_il_values) - topology_metric.worst_insertion_loss_db:.6f}"
            if physical_il_values
            else ""
        ),
        "avg_il_delta_db": (
            f"{sum(il_delta_values) / len(il_delta_values):.6f}"
            if il_delta_values
            else ""
        ),
        "grid_pitch_um": rules.grid_pitch_um,
        "grid_merge_tol_um": rules.grid_merge_tol_um,
        "min_spacing_um": rules.min_spacing_um,
        "waveguide_width_um": rules.waveguide_width_um,
        "mrr_keepout_um": rules.mrr_keepout_um,
        "port_escape_um": rules.port_escape_um,
        "crossing_penalty_um": rules.crossing_penalty_um,
        "turn_guard_um": rules.turn_guard_um,
        "turn_timing_penalty_um": rules.turn_timing_penalty_um,
        "backtrack_penalty_um": rules.backtrack_penalty_um,
        "hairpin_penalty_um": rules.hairpin_penalty_um,
        "loss_aware_cost": rules.loss_aware_cost,
        "capped_ripup_bounded_only": rules.capped_ripup_bounded_only,
        "prop_loss_db_per_um": rules.prop_loss_db_per_um,
        "bend_loss_db_per_bend": rules.bend_loss_db_per_bend,
        "crossing_loss_db_per_cross": rules.crossing_loss_db_per_cross,
        "repeated_crossing_loss_db": rules.repeated_crossing_loss_db,
        "jog_penalty_db": rules.jog_penalty_db,
        "bend_placement_penalty_db": rules.bend_placement_penalty_db,
        "max_astar_pops": rules.max_astar_pops,
        "ripup_max_astar_pops": rules.ripup_max_astar_pops,
        "local_repair_max_shift_tracks": rules.local_repair_max_shift_tracks,
        "route_window_max_detour_tracks": rules.route_window_max_detour_tracks,
        "effective_route_window_slack_um": _effective_route_window_slack_um(rules),
        "grid_margin_tracks": rules.grid_margin_tracks,
        "stage_pitch_um": stage_pitch_um,
        "wire_pitch_um": wire_pitch_um,
        "x_start_um": x_start_um,
        "x_end_um": f"{x_end_um:.6f}",
        "astar_calls": result.stats.astar_calls,
        "ripup_passes": rules.max_ripup_passes,
        "ripup_passes_executed": result.stats.ripup_passes_executed,
        "early_stop_enabled": rules.early_stop_stagnant_passes > 0,
        "early_stopped": result.stats.early_stopped,
    }

    drc_rows: list[dict[str, object]] = [
        {
            "topology": topology.name,
            "layout": layout_name,
            "permutation": permutation_label,
            "rule": violation.rule,
            "net_id": violation.net_id,
            "message": violation.message,
            "x_um": f"{violation.location[0]:.6f}" if violation.location else "",
            "y_um": f"{violation.location[1]:.6f}" if violation.location else "",
        }
        for violation in result.drc_violations
    ]
    return rows, summary_row, drc_rows


def _physical_route_il_db(
    mrr_loss_db: float,
    route: PhysicalRoute,
    rules: RoutingRules,
    *,
    alpha_db_per_um: float,
    crossing_loss_db_per_cross: float,
) -> float:
    return (
        mrr_loss_db
        + alpha_db_per_um * route.length_um
        + rules.bend_loss_db_per_bend * route.bend_count
        + crossing_loss_db_per_cross * route.crossing_count
    )


def _braid_pair_count(
    crossings: tuple[object, ...],
    threshold: int = 2,
) -> int:
    pair_counts: dict[frozenset[int], int] = {}
    for crossing in crossings:
        net_a = int(getattr(crossing, "net_a"))
        net_b = int(getattr(crossing, "net_b"))
        key = frozenset((net_a, net_b))
        pair_counts[key] = pair_counts.get(key, 0) + 1
    return sum(1 for count in pair_counts.values() if count >= threshold)


def _run_breakeven(
    topologies: list[RNBTopology],
    eval_perms: list[tuple[int, ...]],
    s_table: dict[tuple[str, str, int], float],
    centers_by_topology: dict[str, dict[str, tuple[float, float]]],
    outdir: Path,
    crossing_loss_sweep_db: list[float],
) -> None:
    rows = []
    for crossing_loss_db in crossing_loss_sweep_db:
        for topology in topologies:
            metrics = [
                evaluate_routing(
                    topology,
                    permutation,
                    s_table,
                    crossing_loss_db_per_cross=crossing_loss_db,
                    centers=centers_by_topology[topology.name],
                )
                for permutation in eval_perms
            ]
            rows.append(
                {
                    "crossing_loss_db_per_cross": crossing_loss_db,
                    "topology": topology.name,
                    "worst_il_db": max(metric.worst_insertion_loss_db for metric in metrics),
                    "worst_sxr_db": min(metric.worst_sxr_db for metric in metrics),
                }
            )

    _write_dict_rows(
        outdir / "breakeven_sweep.csv",
        rows,
        fieldnames=[
            "crossing_loss_db_per_cross",
            "topology",
            "worst_il_db",
            "worst_sxr_db",
        ],
    )
    _write_breakeven_plot(outdir / "breakeven_plot.png", rows)


def _path_depth_range(
    eval_plans: tuple[PermutationPlan, ...],
) -> tuple[int, int]:
    depths: list[int] = []
    for plan in eval_plans:
        depths.extend(len(path.steps) for path in plan.paths)
    return min(depths), max(depths)


def _build_paper_rows(
    topologies: list[RNBTopology],
    eval_rows: list[dict[str, object]],
    eval_summary_rows: list[dict[str, object]],
    activity_summary_rows: list[dict[str, object]],
    sa_rows: list[dict[str, object]],
    path_depth_ranges: dict[str, tuple[int, int]],
) -> list[dict[str, object]]:
    topology_by_name = {topology.name: topology for topology in topologies}
    activity_by_topology = {row["topology"]: row for row in activity_summary_rows}
    sa_by_topology = {row["topology"]: row for row in sa_rows}
    crossing_by_layout = _avg_crossing_by_layout(eval_rows)
    # pre-index default worst IL for delta computation
    default_worst_il: dict[str, float] = {
        str(s["topology"]): _as_float(s["worst_il_db"])
        for s in eval_summary_rows
        if str(s["layout"]) == "default"
    }
    paper_rows = []
    for eval_summary in eval_summary_rows:
        topology_name = str(eval_summary["topology"])
        layout_name = str(eval_summary["layout"])
        activity = activity_by_topology[topology_name]
        depth_min, depth_max = path_depth_ranges[topology_name]
        worst_il = _as_float(eval_summary["worst_il_db"])
        delta_il = (
            worst_il - default_worst_il[topology_name]
            if topology_name in default_worst_il and layout_name != "default"
            else ""
        )
        paper_rows.append(
            {
                "topology": topology_name,
                "n_mrr_total": activity["total_mrr"],
                "n_never_active": activity["never_active"],
                "n_tunable": activity["tunable"],
                "n_stages": topology_by_name[topology_name].n_stages,
                "path_depth_min": depth_min,
                "path_depth_max": depth_max,
                "layout": layout_name,
                "worst_il_db": worst_il,
                "avg_il_db": eval_summary["avg_il_db"],
                "il_p95_db": eval_summary["il_p95_db"],
                "worst_sxr_db": eval_summary["worst_sxr_db"],
                "sxr_violation_count": eval_summary["sxr_violation_count"],
                "avg_crossing_count": crossing_by_layout[(topology_name, layout_name)],
                "delta_worst_il_db": delta_il,
                "sa_delta_cost": (
                    sa_by_topology[topology_name]["delta_cost"]
                    if layout_name == "sa" and topology_name in sa_by_topology
                    else ""
                ),
            }
        )
    return paper_rows


def _avg_crossing_by_layout(
    eval_rows: list[dict[str, object]],
) -> dict[tuple[str, str], float]:
    crossing_by_perm: dict[tuple[str, str, str], float] = {}
    for row in eval_rows:
        key = (str(row["topology"]), str(row["layout"]), str(row["perm_idx"]))
        crossing_by_perm[key] = _as_float(row["perm_crossing_count"])

    grouped: dict[tuple[str, str], list[float]] = {}
    for (topology, layout, _perm_idx), crossing_count in crossing_by_perm.items():
        grouped.setdefault((topology, layout), []).append(crossing_count)
    return {
        key: sum(values) / len(values)
        for key, values in grouped.items()
    }


if __name__ == "__main__":
    main()
