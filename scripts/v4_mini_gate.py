from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from mrr_switch_optimizer.analysis.nsweep import evaluate_fixed_fabric_path_space
from mrr_switch_optimizer.app.nsweep_campaign import (
    CONFIGS,
    _audit_counts,
    _make_topology,
    _physical_routes,
    _route_case,
)
from mrr_switch_optimizer.core.fabric import build_fabric_graph
from mrr_switch_optimizer.core.models import MRRCell
from mrr_switch_optimizer.core.sparams import load_mrr_s_table
from mrr_switch_optimizer.routing.drc import validate_physical_routes
from mrr_switch_optimizer.routing.envelope import octave_envelope
from mrr_switch_optimizer.routing.fabric import FixedFabricRoutingResult


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs" / "nsweep_fixed_fabric_v4_mini_gate"
V3_MINI = ROOT / "outputs" / "nsweep_fixed_fabric_v3_mini"
TOPOLOGIES = ("waksman", "padded_benes")
SIZES = (4, 6, 8)


def _case_key(topology: str, n: int, config: str) -> str:
    return f"{topology}:n{n}:{config}"


def _baseline_metrics(topology: str, n: int, config: str) -> dict[str, object]:
    path = (
        V3_MINI
        / "cases"
        / topology
        / f"n{n:02d}"
        / config
        / "case_metrics.json"
    )
    return json.loads(path.read_text())


def _evaluation_result(
    result: FixedFabricRoutingResult,
    cells: dict[str, MRRCell],
) -> FixedFabricRoutingResult:
    audit_rules = replace(
        result.rules,
        drc_same_net_min_spacing=True,
        drc_perpendicular_clearance=True,
        drc_bend_radius_legality=True,
    )
    current_violations = validate_physical_routes(
        _physical_routes(result),
        cells,
        audit_rules,
    )
    return replace(
        result,
        drc_violations=tuple(
            violation
            for violation in current_violations
            if violation.rule
            not in {
                "same_net_min_spacing",
                "perpendicular_clearance",
                "crossing_clearance",
            }
        ),
    )


def run() -> list[dict[str, object]]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results_path = OUTPUT / "gate_results.json"
    rows: list[dict[str, object]] = []
    if results_path.is_file():
        rows = list(json.loads(results_path.read_text())["rows"])
    completed = {
        _case_key(str(row["topology"]), int(row["N"]), str(row["config"]))
        for row in rows
    }
    s_table = load_mrr_s_table("mrr_sparam_library", strict=True)
    for topology_name in TOPOLOGIES:
        for n in SIZES:
            for config in CONFIGS:
                key = _case_key(topology_name, n, config)
                if key in completed:
                    continue
                print(key, flush=True)
                topology = _make_topology(topology_name, n)
                graph = build_fabric_graph(topology)
                envelope = octave_envelope(n)
                (
                    cells,
                    result,
                    route_wall_s,
                    pops,
                    layout_mode,
                    remediation,
                    window_tracks,
                    escalation,
                    escalation_stage,
                ) = _route_case(
                    topology_name,
                    n,
                    config,
                    topology,
                    graph,
                    envelope,
                    s_table,
                    output_root=OUTPUT,
                    pops_ladder=(30000,),
                    min_crossing_clearance_um=10.0,
                    physical_turn_guard=True,
                    v4_search=True,
                    campaign_version="v4-gate",
                )
                audit = _audit_counts(result, cells)
                worst_il = None
                if (
                    not result.failed_edges
                    and audit["legacy_drc"] == 0
                    and audit["bend_radius_legality"] == 0
                ):
                    worst_il = evaluate_fixed_fabric_path_space(
                        topology,
                        cells,
                        _evaluation_result(result, cells),
                    ).worst_insertion_loss_db
                baseline = _baseline_metrics(topology_name, n, config)
                row: dict[str, object] = {
                    "topology": topology_name,
                    "N": n,
                    "config": config,
                    "layout_mode": layout_mode,
                    "failed_edges": len(result.failed_edges),
                    "crossings": len(result.crossings),
                    "bends": sum(route.bend_count for route in result.routes),
                    "worst_il_db": worst_il,
                    "v3_status": baseline["status"],
                    "v3_worst_il_db": baseline.get("worst_il_db"),
                    "wil_drift_db": (
                        None
                        if worst_il is None or baseline.get("worst_il_db") is None
                        else worst_il - float(baseline["worst_il_db"])
                    ),
                    "route_wall_s": route_wall_s,
                    "max_astar_pops": pops,
                    "remediation": remediation,
                    "window_expansion_tracks": window_tracks,
                    "reserved_region_escalation": escalation,
                    "reserved_region_escalation_stage": escalation_stage,
                    "astar_calls": result.stats.astar_calls,
                    **audit,
                    "v3_legacy_drc": int(baseline["legacy_drc"]),
                    "v3_same_net_min_spacing": int(baseline["same_net_min_spacing"]),
                    "v3_perpendicular_clearance": int(baseline["perpendicular_clearance"]),
                    "v3_crossing_clearance": int(baseline["crossing_clearance"]),
                    "v3_bend_radius_legality": int(baseline["bend_radius_legality"]),
                }
                rows.append(row)
                completed.add(key)
                results_path.write_text(
                    json.dumps({"rows": rows}, indent=2, sort_keys=True) + "\n"
                )

    baseline_audits = sum(
        int(row[f"v3_{name}"])
        for row in rows
        for name in (
            "legacy_drc",
            "same_net_min_spacing",
            "perpendicular_clearance",
            "crossing_clearance",
            "bend_radius_legality",
        )
    )
    v4_audits = sum(
        int(row[name])
        for row in rows
        for name in (
            "legacy_drc",
            "same_net_min_spacing",
            "perpendicular_clearance",
            "crossing_clearance",
            "bend_radius_legality",
        )
    )
    n4_b = next(
        row
        for row in rows
        if row["topology"] == "waksman"
        and row["N"] == 4
        and row["config"] == "B_db_placeholder"
    )
    payload = {
        "schema": "v4-mini-gate-1",
        "rows": rows,
        "all_zero_failed": all(int(row["failed_edges"]) == 0 for row in rows),
        "n4_b_zero_failed": int(n4_b["failed_edges"]) == 0,
        "n4_b_crossing_clearance": int(n4_b["crossing_clearance"]),
        "baseline_audit_total": baseline_audits,
        "v4_audit_total": v4_audits,
        "audits_strictly_reduced": v4_audits < baseline_audits,
    }
    results_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: value for key, value in payload.items() if key != "rows"}, indent=2))
    return rows


if __name__ == "__main__":
    run()
