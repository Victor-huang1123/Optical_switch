"""Crossing-insertion ablation for one (topology, N, config, explicit) case.

Both arms differ only in `RoutingRules.explicit_crossings`; both use the A* lane
so the comparison is method-symmetric.  Crossings are re-detected geometrically
by the DRC pass, so crossing loss is charged identically in both arms.

Usage: crossing_insert_ablation.py --topology waksman --n 10 \
           --config C_db_realistic --explicit true --pops 100000
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import json
import time
from pathlib import Path

from mrr_switch_optimizer.analysis.nsweep import evaluate_fixed_fabric_path_space
from mrr_switch_optimizer.app.nsweep_campaign import (
    _audit_counts,
    _campaign_rules,
    _make_topology,
    _physical_routes,
)
from mrr_switch_optimizer.core.fabric import build_fabric_graph
from mrr_switch_optimizer.core.sparams import load_mrr_s_table
from mrr_switch_optimizer.routing.drc import validate_physical_routes
from mrr_switch_optimizer.routing.envelope import build_envelope_cells, octave_envelope
from mrr_switch_optimizer.routing.fabric import route_fixed_fabric

ROOT = Path(__file__).resolve().parents[1]


def _eval_ready(result, cells):
    audit_rules = replace(
        result.rules,
        drc_same_net_min_spacing=True,
        drc_perpendicular_clearance=True,
        drc_bend_radius_legality=True,
    )
    violations = validate_physical_routes(_physical_routes(result), cells, audit_rules)
    return replace(
        result,
        drc_violations=tuple(
            v
            for v in violations
            if v.rule
            not in {"same_net_min_spacing", "perpendicular_clearance", "crossing_clearance"}
        ),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topology", required=True)
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--explicit", required=True, choices=("true", "false"))
    ap.add_argument("--pops", type=int, default=100000)
    ap.add_argument("--outdir", default=str(ROOT / "outputs" / "crossing_insert_ablation"))
    args = ap.parse_args()

    explicit = args.explicit == "true"
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = f"{args.topology}_n{args.n:02d}_{args.config[0]}_explicit-{args.explicit}_pops{args.pops}"
    out_path = outdir / f"{stem}.json"
    if out_path.is_file():
        print(f"skip (exists) {stem}", flush=True)
        return

    s_table = load_mrr_s_table(str(ROOT / "mrr_sparam_library"), strict=True)
    topology = _make_topology(args.topology, args.n)
    graph = build_fabric_graph(topology)
    envelope = octave_envelope(args.n)
    cells = build_envelope_cells(topology, s_table, envelope)
    rules = replace(
        _campaign_rules(args.config, pops=args.pops, topology=args.topology),
        explicit_crossings=explicit,
    )
    started = time.perf_counter()
    result = route_fixed_fabric(
        topology,
        graph,
        cells,
        rules,
        x_start=envelope.x_start_um,
        x_end=envelope.x_end_um,
        wire_pitch_um=envelope.wire_pitch_um,
        layout_mode="astar",
    )
    wall = time.perf_counter() - started
    audit = _audit_counts(result, cells)
    pairs = Counter(tuple(sorted((c.edge_a, c.edge_b))) for c in result.crossings)
    worst_il = None
    eval_error = None
    if not result.failed_edges:
        try:
            worst_il = evaluate_fixed_fabric_path_space(
                topology, cells, _eval_ready(result, cells)
            ).worst_insertion_loss_db
        except Exception as exc:  # report, never mask
            eval_error = f"{type(exc).__name__}: {exc}"

    row = {
        "topology": args.topology,
        "N": args.n,
        "config": args.config,
        "explicit_crossings": explicit,
        "pops": args.pops,
        "failed_edges": len(result.failed_edges),
        "crossings": len(result.crossings),
        "distinct_crossing_pairs": len(pairs),
        "max_pair_multiplicity": max(pairs.values()) if pairs else 0,
        "bends": sum(r.bend_count for r in result.routes),
        "worst_il_db": worst_il,
        "eval_error": eval_error,
        "route_wall_s": wall,
        "astar_calls": result.stats.astar_calls,
        **audit,
    }
    out_path.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n")
    print(
        f"{args.topology:13} n{args.n} {args.config[0]} explicit={args.explicit:5} "
        f"failed={row['failed_edges']} cross={row['crossings']} bends={row['bends']} "
        f"wil={worst_il} {wall:.0f}s",
        flush=True,
    )


if __name__ == "__main__":
    main()
