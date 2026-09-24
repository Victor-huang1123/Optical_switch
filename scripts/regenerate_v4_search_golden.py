from __future__ import annotations

import json
from pathlib import Path
import pickle

from mrr_switch_optimizer.app.fabric_reports import fixed_fabric_geometry_hash
from mrr_switch_optimizer.app.nsweep_campaign import _audit_counts


ROOT = Path(__file__).resolve().parents[1]
CASE = (
    ROOT
    / "outputs"
    / "nsweep_fixed_fabric_v4_mini_gate"
    / "cases"
    / "waksman"
    / "n04"
    / "B_db_placeholder"
)
GOLDEN = ROOT / "tests" / "golden" / "v4_search_waksman_n4_b.json"


def main() -> None:
    with (CASE / "routing_result.pkl").open("rb") as handle:
        cells, result = pickle.load(handle)
    config = json.loads((CASE / "config.json").read_text())
    payload = {
        "schema": "v4-search-golden-1",
        "case_key": config["case_key"],
        "geometry_sha256": fixed_fabric_geometry_hash(result),
        "failed_edge_count": len(result.failed_edges),
        "route_count": len(result.routes),
        "crossing_count": len(result.crossings),
        "bend_count": sum(route.bend_count for route in result.routes),
        "audits": _audit_counts(result, cells),
        "search_policy": {
            "min_crossing_clearance_um": result.rules.min_crossing_clearance_um,
            "drc_same_net_min_spacing": result.rules.drc_same_net_min_spacing,
            "drc_perpendicular_clearance": result.rules.drc_perpendicular_clearance,
            "physical_turn_guard": result.rules.physical_turn_guard,
            "physical_same_net_hairpin": result.rules.physical_same_net_hairpin,
            "allow_foreign_outer_runway_transit": (
                result.rules.allow_foreign_outer_runway_transit
            ),
            "port_access_stagger_tracks": result.rules.port_access_stagger_tracks,
        },
    }
    GOLDEN.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(GOLDEN.relative_to(ROOT))


if __name__ == "__main__":
    main()
