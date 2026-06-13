from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict
from pathlib import Path

from .activity import MRRActivity, scan_mrr_activity, summarize_mrr_activity
from .cost import aggregate_cost, evaluate_routing, make_permutation_split
from .layout import build_cells
from .lp_placement import lp_placement
from .sa_placement import is_feasible_placement, sa_placement
from .sparams import load_mrr_s_table
from .surrogate import AnalyticEdge, analytic_edge_costs
from .topology import (
    PaddedBenesTopology,
    RNBTopology,
    SpankeBenesTopology,
    WaksmanTopology,
)
from .visualize import save_il_sxr_cdf, save_routing_gif, save_routing_png


def main() -> None:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[1]
    sparam_dir = Path(args.sparam_dir) if args.sparam_dir else project_root / "mrr_sparam_library"
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    permutation = _parse_permutation(args.permutation)
    s_table = load_mrr_s_table(
        sparam_dir,
        radius_um=args.radius_um,
        channel_nm=args.channel_nm,
        wavelength_nm=args.wavelength_nm,
    )

    topologies = _select_topologies(args.topology)
    metrics = []
    sa_rows = []
    activity_summary_rows = []
    eval_rows = []
    eval_summary_rows = []
    path_depth_ranges: dict[str, tuple[int, int]] = {}
    centers_by_topology: dict[str, dict[str, tuple[float, float]]] = {}
    for topology in topologies:
        states = topology.get_state_assignment(permutation)
        metric = evaluate_routing(topology, permutation, s_table)
        metrics.append(metric)
        sa_centers = None
        default_centers = {
            mrr_id: cell.center for mrr_id, cell in build_cells(topology, s_table).items()
        }
        centers_by_topology[topology.name] = default_centers
        stem = f"{topology.name}_{'-'.join(map(str, permutation))}"
        save_routing_png(topology, permutation, states, s_table, outdir / f"{stem}.png")
        if args.gif:
            save_routing_gif(topology, permutation, states, s_table, outdir / f"{stem}.gif")
        _write_states(outdir / f"{stem}_states.csv", states)
        if args.sa:
            train_perms, _ = make_permutation_split(
                topology.N_logical,
                n_train=args.sa_train,
                seed=args.split_seed,
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
            sa_metric = evaluate_routing(topology, permutation, s_table, centers=sa_centers)
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
            save_routing_png(
                topology,
                permutation,
                states,
                s_table,
                outdir / f"{stem}_sa.png",
                centers=sa_centers,
                title_suffix="SA",
            )
            if args.gif:
                save_routing_gif(
                    topology,
                    permutation,
                    states,
                    s_table,
                    outdir / f"{stem}_sa.gif",
                    centers=sa_centers,
                    title_suffix="SA",
                )
        if args.lp:
            lp_train_perms, _ = make_permutation_split(
                topology.N_logical,
                n_train=args.lp_train,
                seed=args.split_seed,
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
        activity_rows = []
        for topology in topologies:
            rows = scan_mrr_activity(topology)
            activity_rows.extend(rows)
            activity_summary_rows.append(summarize_mrr_activity(rows))
        _write_activity_rows(outdir / "mrr_activity.csv", activity_rows)
        _write_activity_summary(outdir / "mrr_activity_summary.csv", activity_summary_rows)
    if sa_rows:
        _write_sa_summary(outdir / "sa_summary.csv", sa_rows)
    if args.eval:
        for topology in topologies:
            _train, eval_perms = make_permutation_split(
                topology.N_logical,
                n_train=args.eval_train,
                seed=args.split_seed,
            )
            path_depth_ranges[topology.name] = _path_depth_range(topology, eval_perms)
            default_centers = {
                mrr_id: cell.center for mrr_id, cell in build_cells(topology, s_table).items()
            }
            default_rows = _collect_eval_rows(
                topology,
                eval_perms,
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
                    eval_perms,
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
                    eval_perms,
                    s_table,
                    lp_centers_for_eval,
                    "lp",
                    args.sxr_min_db,
                )
                eval_rows.extend(lp_eval_rows)
                eval_summary_rows.append(_summarize_eval_rows(topology.name, "lp", lp_eval_rows))
        _write_eval_distribution(outdir / "eval_path_distribution.csv", eval_rows)
        _write_eval_summary(outdir / "eval_summary.csv", eval_summary_rows)
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
        _train, analytic_perms = make_permutation_split(
            topologies[0].N_logical,
            n_train=args.eval_train,
            seed=args.split_seed,
        )
        analytic_edge_rows: list[AnalyticEdge] = []
        for topology in topologies:
            topo_centers = centers_by_topology[topology.name]
            analytic_edge_rows.extend(
                analytic_edge_costs(topology, analytic_perms, s_table, centers=topo_centers)
            )
        _write_analytic_edges(outdir / "analytic_edges.csv", analytic_edge_rows)
    if args.breakeven:
        _train, breakeven_eval_perms = make_permutation_split(
            topologies[0].N_logical,
            n_train=args.eval_train,
            seed=args.split_seed,
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add-drop MRR routing demo")
    parser.add_argument(
        "--permutation",
        default="2,0,5,1,3,4",
        help="logical output for inputs 0..5, e.g. 2,0,5,1,3,4",
    )
    parser.add_argument(
        "--topology",
        choices=("main", "all", "benes", "waksman", "sb"),
        default="main",
        help=(
            "main routes Beneš and Waksman; all also includes Spanke-Beneš; "
            "or select one topology"
        ),
    )
    parser.add_argument("--sparam-dir", default=None)
    parser.add_argument("--outdir", default="outputs")
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
    parser.add_argument("--sa-seed", type=int, default=0)
    parser.add_argument("--sa-restarts", type=int, default=2)
    parser.add_argument("--sa-t-init", type=float, default=2.0)
    parser.add_argument("--sa-t-min", type=float, default=0.02)
    parser.add_argument("--sa-cooling", type=float, default=0.8)
    parser.add_argument("--sa-moves-per-temp", type=int, default=200)
    parser.add_argument("--eval", action="store_true", help="run §11 eval-set validation")
    parser.add_argument("--eval-train", type=int, default=500, help="train split size before eval set")
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
    args = parser.parse_args()
    if args.paper_table and (not args.eval or not args.activity_scan):
        raise argparse.ArgumentError(
            None,
            "--paper-table requires both --eval and --activity-scan",
        )
    return args


def _parse_permutation(value: str) -> tuple[int, ...]:
    permutation = tuple(int(x.strip()) for x in value.split(",") if x.strip())
    if sorted(permutation) != list(range(6)):
        raise argparse.ArgumentTypeError("permutation must contain each integer 0..5 once")
    return permutation


def _select_topologies(selector: str) -> list[RNBTopology]:
    if selector == "benes":
        return [PaddedBenesTopology()]
    if selector == "waksman":
        return [WaksmanTopology()]
    if selector == "sb":
        return [SpankeBenesTopology()]
    if selector == "all":
        return [PaddedBenesTopology(), WaksmanTopology(), SpankeBenesTopology()]
    return [PaddedBenesTopology(), WaksmanTopology()]


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


def _read_placement(path: Path) -> dict[str, tuple[float, float]]:
    centers: dict[str, tuple[float, float]] = {}
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            centers[row["mrr_id"]] = (float(row["x_um"]), float(row["y_um"]))
    return centers


def _write_summary(path: Path, metrics: list[object]) -> None:
    with path.open("w") as f:
        json.dump([asdict(metric) for metric in metrics], f, indent=2)


def _write_comparison(path: Path, metrics: list[object]) -> None:
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
    eval_perms: list[tuple[int, ...]],
    s_table: dict[tuple[str, str, int], float],
    centers: dict[str, tuple[float, float]],
    layout_name: str,
    sxr_min_db: float,
) -> list[dict[str, object]]:
    rows = []
    for perm_idx, permutation in enumerate(eval_perms):
        metric = evaluate_routing(topology, permutation, s_table, centers=centers)
        for path_metric in metric.path_metrics:
            rows.append(
                {
                    "topology": topology.name,
                    "layout": layout_name,
                    "perm_idx": perm_idx,
                    "permutation": "-".join(str(x) for x in permutation),
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
    il_values = [float(row["insertion_loss_db"]) for row in rows]
    sxr_values = [float(row["sxr_db"]) for row in rows]
    return {
        "topology": topology_name,
        "layout": layout_name,
        "paths": len(rows),
        "worst_il_db": max(il_values),
        "avg_il_db": sum(il_values) / len(il_values),
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
    sorted_values = sorted(values)
    if not sorted_values:
        raise ValueError("cannot compute percentile of empty list")
    idx = min(len(sorted_values) - 1, max(0, round(q * (len(sorted_values) - 1))))
    return sorted_values[idx]


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

    sweep_path = outdir / "breakeven_sweep.csv"
    with sweep_path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "crossing_loss_db_per_cross",
                "topology",
                "worst_il_db",
                "worst_sxr_db",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    _write_breakeven_plot(outdir / "breakeven_plot.png", rows)


def _write_breakeven_plot(path: Path, rows: list[dict[str, object]]) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return

    topology_colors = {
        "padded_benes_8x8": "#0B6E69",
        "waksman_6x6": "#D55E00",
        "spanke_benes_6x6": "#0072B2",
    }
    topology_order = {
        "padded_benes_8x8": 0,
        "waksman_6x6": 1,
        "spanke_benes_6x6": 2,
    }
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        grouped.setdefault(str(row["topology"]), []).append(row)

    fig, (ax_il, ax_sxr) = plt.subplots(1, 2, figsize=(10, 4.5))
    for topology, topology_rows in sorted(
        grouped.items(),
        key=lambda item: (topology_order.get(item[0], len(topology_order)), item[0]),
    ):
        sorted_rows = sorted(
            topology_rows,
            key=lambda row: float(row["crossing_loss_db_per_cross"]),
        )
        x_values = [float(row["crossing_loss_db_per_cross"]) for row in sorted_rows]
        il_values = [float(row["worst_il_db"]) for row in sorted_rows]
        sxr_values = [float(row["worst_sxr_db"]) for row in sorted_rows]
        color = topology_colors.get(topology, "#888888")
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


def _path_depth_range(
    topology: RNBTopology,
    eval_perms: list[tuple[int, ...]],
) -> tuple[int, int]:
    depths = []
    for permutation in eval_perms:
        paths = topology.get_active_paths(permutation)
        depths.extend(len(path.steps) for path in paths)
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
        str(s["topology"]): float(s["worst_il_db"])
        for s in eval_summary_rows
        if str(s["layout"]) == "default"
    }
    paper_rows = []
    for eval_summary in eval_summary_rows:
        topology_name = str(eval_summary["topology"])
        layout_name = str(eval_summary["layout"])
        activity = activity_by_topology[topology_name]
        depth_min, depth_max = path_depth_ranges[topology_name]
        worst_il = float(eval_summary["worst_il_db"])
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
        crossing_by_perm[key] = float(row["perm_crossing_count"])

    grouped: dict[tuple[str, str], list[float]] = {}
    for (topology, layout, _perm_idx), crossing_count in crossing_by_perm.items():
        grouped.setdefault((topology, layout), []).append(crossing_count)
    return {
        key: sum(values) / len(values)
        for key, values in grouped.items()
    }


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


if __name__ == "__main__":
    main()
