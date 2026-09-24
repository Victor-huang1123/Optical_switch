"""N=4 ablation: explicit crossing insertion on vs off.

Both topologies are routed with the A* lane so the two arms differ only in
`RoutingRules.explicit_crossings`.  Crossings are re-detected geometrically by
the DRC pass, so crossing loss is charged identically in both arms.
"""
from __future__ import annotations

from dataclasses import replace
import json
import time
from pathlib import Path

from mrr_switch_optimizer.analysis.nsweep import evaluate_fixed_fabric_path_space
from mrr_switch_optimizer.app.nsweep_campaign import (
    CONFIGS,
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
OUTPUT = ROOT / "outputs" / "crossing_insert_n4"
N = 4
POPS = 30000


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


def run() -> list[dict[str, object]]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    s_table = load_mrr_s_table("mrr_sparam_library", strict=True)
    rows: list[dict[str, object]] = []
    for topology_name in ("waksman", "padded_benes"):
        for config in CONFIGS:
            for explicit in (True, False):
                topology = _make_topology(topology_name, N)
                graph = build_fabric_graph(topology)
                envelope = octave_envelope(N)
                cells = build_envelope_cells(topology, s_table, envelope)
                rules = replace(
                    _campaign_rules(config, pops=POPS, topology=topology_name),
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
                worst_il = None
                eval_error = None
                if not result.failed_edges:
                    try:
                        worst_il = evaluate_fixed_fabric_path_space(
                            topology, cells, _eval_ready(result, cells)
                        ).worst_insertion_loss_db
                    except Exception as exc:  # report, do not mask
                        eval_error = f"{type(exc).__name__}: {exc}"
                row = {
                    "topology": topology_name,
                    "N": N,
                    "config": config,
                    "explicit_crossings": explicit,
                    "failed_edges": len(result.failed_edges),
                    "crossings": len(result.crossings),
                    "bends": sum(r.bend_count for r in result.routes),
                    "worst_il_db": worst_il,
                    "eval_error": eval_error,
                    "route_wall_s": wall,
                    "astar_calls": result.stats.astar_calls,
                    **audit,
                }
                rows.append(row)
                print(
                    f"{topology_name:13} {config[0]} explicit={explicit!s:5} "
                    f"failed={row['failed_edges']} cross={row['crossings']} "
                    f"bends={row['bends']} wil={worst_il} {wall:.0f}s",
                    flush=True,
                )
                (OUTPUT / "results.json").write_text(
                    json.dumps({"rows": rows}, indent=2, sort_keys=True) + "\n"
                )
    return rows


if __name__ == "__main__":
    run()
