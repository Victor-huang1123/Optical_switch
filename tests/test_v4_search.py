from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from mrr_switch_optimizer.app.fabric_reports import fixed_fabric_geometry_hash
from mrr_switch_optimizer.app.nsweep_campaign import (
    METRIC_FIELDS,
    _audit_counts,
    _campaign_rules,
    _foreign_port_access_frontier_exhausted,
    _make_topology,
    _route_case,
)
from mrr_switch_optimizer.core.fabric import build_fabric_graph
from mrr_switch_optimizer.core.sparams import load_mrr_s_table
from mrr_switch_optimizer.routing.envelope import octave_envelope


ROOT = Path(__file__).resolve().parents[1]


def test_v4_campaign_rules_promote_physical_search_and_escalation() -> None:
    base = _campaign_rules(
        "C_db_realistic",
        pops=30000,
        topology="waksman",
        full_drc=True,
        min_crossing_clearance_um=10.0,
        physical_turn_guard=True,
        physical_same_net_hairpin=True,
    )
    transit = _campaign_rules(
        "C_db_realistic",
        pops=30000,
        topology="waksman",
        reserved_region_stage=1,
    )
    stagger = _campaign_rules(
        "C_db_realistic",
        pops=30000,
        topology="waksman",
        reserved_region_stage=2,
    )

    assert base.min_crossing_clearance_um == 10.0
    assert base.drc_same_net_min_spacing
    assert base.drc_perpendicular_clearance
    assert base.physical_turn_guard
    assert base.physical_same_net_hairpin
    assert transit.allow_foreign_outer_runway_transit
    assert transit.port_access_stagger_tracks == 0
    assert stagger.allow_foreign_outer_runway_transit
    assert stagger.port_access_stagger_tracks == 1
    assert "reserved_region_escalation" in METRIC_FIELDS


def test_reserved_region_escalation_requires_a_foreign_geometric_wall() -> None:
    def result(message: str, max_pops: int = 30000) -> SimpleNamespace:
        return SimpleNamespace(
            failed_edges=(SimpleNamespace(message=message),),
            rules=SimpleNamespace(max_astar_pops=max_pops),
        )

    foreign_wall = (
        "blocked by port access (port_access_overlap_foreign); "
        "counters=port_access:66,pops:10220"
    )
    budget_exhausted = foreign_wall.replace("pops:10220", "pops:30000")
    owner_wall = foreign_wall.replace("_foreign", "_owner")

    assert _foreign_port_access_frontier_exhausted(result(foreign_wall))
    assert not _foreign_port_access_frontier_exhausted(result(budget_exhausted))
    assert not _foreign_port_access_frontier_exhausted(result(owner_wall))


def test_v4_search_waksman_n4_b_matches_golden(tmp_path: Path) -> None:
    topology = _make_topology("waksman", 4)
    cells, result, *_rest = _route_case(
        "waksman",
        4,
        "B_db_placeholder",
        topology,
        build_fabric_graph(topology),
        octave_envelope(4),
        load_mrr_s_table("mrr_sparam_library", strict=True),
        output_root=tmp_path,
        pops_ladder=(30000,),
        min_crossing_clearance_um=10.0,
        physical_turn_guard=True,
        v4_search=True,
        campaign_version="v4-gate",
    )
    snapshot = {
        "schema": "v4-search-golden-1",
        "case_key": (
            "v4-gate:waksman:n4:B_db_placeholder:"
            f"{octave_envelope(4).envelope_id}"
        ),
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
    expected = json.loads(
        (ROOT / "tests" / "golden" / "v4_search_waksman_n4_b.json").read_text()
    )

    assert snapshot == expected
