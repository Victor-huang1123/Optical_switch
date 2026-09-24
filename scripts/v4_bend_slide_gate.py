from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
import json
from pathlib import Path
import pickle

from mrr_switch_optimizer.app.nsweep_campaign import _make_topology
from mrr_switch_optimizer.routing.drc import validate_physical_routes
from mrr_switch_optimizer.routing.fabric import _fabric_waveguide_paths
from mrr_switch_optimizer.routing.port_access import build_port_access_plan
from mrr_switch_optimizer.routing.straighten import (
    physical_routes_from_fixed,
    straighten_fixed_fabric,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "outputs" / "nsweep_fixed_fabric_v4_mini_gate"
DEFAULT_OUTPUT = ROOT / "outputs" / "v4_bend_slide_gate"


def _counts(routes: tuple[object, ...], cells: dict[str, object], rules: object) -> Counter[str]:
    return Counter(
        violation.rule
        for violation in validate_physical_routes(routes, cells, rules)  # type: ignore[arg-type]
    )


def run(source: Path = DEFAULT_SOURCE, output: Path = DEFAULT_OUTPUT) -> list[dict[str, object]]:
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for pickle_path in sorted(source.glob("cases/*/n*/**/routing_result.pkl")):
        relative = pickle_path.relative_to(source)
        topology_name = relative.parts[1]
        n = int(relative.parts[2][1:])
        config = relative.parts[3]
        with pickle_path.open("rb") as handle:
            cells, result = pickle.load(handle)
        if result.failed_edges:
            rows.append(
                {
                    "topology": topology_name,
                    "N": n,
                    "config": config,
                    "status": "skipped_incomplete",
                    "failed_edges": len(result.failed_edges),
                }
            )
            continue
        audit_rules = replace(
            result.rules,
            min_crossing_clearance_um=10.0,
            drc_same_net_min_spacing=True,
            drc_perpendicular_clearance=True,
            drc_bend_radius_legality=True,
        )
        seed = replace(result, rules=audit_rules)
        topology = _make_topology(topology_name, n)
        plan = build_port_access_plan(
            _fabric_waveguide_paths(topology, result.graph),
            cells,
            audit_rules,
            2.0,
        )
        before_routes = physical_routes_from_fixed(seed)
        before = _counts(before_routes, cells, audit_rules)
        legalized = straighten_fixed_fabric(
            seed,
            cells,
            port_access_plan=plan,
            max_rounds=64,
            preserve_template=topology_name == "padded_benes",
        )
        after_routes = physical_routes_from_fixed(legalized.routing)
        after = _counts(after_routes, cells, audit_rules)
        regressions = {
            rule: after_count - before.get(rule, 0)
            for rule, after_count in after.items()
            if after_count > before.get(rule, 0)
        }
        row = {
            "topology": topology_name,
            "N": n,
            "config": config,
            "status": "legalized",
            "failed_edges": 0,
            "crossing_arm_before": before["crossing_clearance"],
            "crossing_arm_after": after["crossing_clearance"],
            "crossing_arm_removed": (
                before["crossing_clearance"] - after["crossing_clearance"]
            ),
            "drc_before": dict(sorted(before.items())),
            "drc_after": dict(sorted(after.items())),
            "drc_regressions": regressions,
            **asdict(legalized.stats),
        }
        rows.append(row)
        case_output = output / relative.parent
        case_output.mkdir(parents=True, exist_ok=True)
        with (case_output / "routing_result.pkl").open("wb") as handle:
            pickle.dump(
                (cells, legalized.routing),
                handle,
                protocol=pickle.HIGHEST_PROTOCOL,
            )

    attempted = [row for row in rows if row["status"] == "legalized"]
    before_total = sum(int(row["crossing_arm_before"]) for row in attempted)
    after_total = sum(int(row["crossing_arm_after"]) for row in attempted)
    removed_fraction = (
        1.0 if before_total == 0 else (before_total - after_total) / before_total
    )
    payload = {
        "schema": "v4-bend-slide-gate-1",
        "source": str(source.relative_to(ROOT)),
        "source_mutated": False,
        "crossing_arm_before": before_total,
        "crossing_arm_after": after_total,
        "removed_fraction": removed_fraction,
        "drc_regression_cases": sum(bool(row.get("drc_regressions")) for row in attempted),
        "loss_limit_failures": sum(
            float(row["loss_proxy_delta_db"]) > 0.01 + 1e-9
            for row in attempted
        ),
        "passed": (
            removed_fraction >= 0.80
            and not any(row.get("drc_regressions") for row in attempted)
            and not any(
                float(row["loss_proxy_delta_db"]) > 0.01 + 1e-9
                for row in attempted
            )
        ),
        "rows": rows,
    }
    (output / "results.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({key: value for key, value in payload.items() if key != "rows"}, indent=2))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    run(args.source.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
