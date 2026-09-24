from __future__ import annotations

import json
from pathlib import Path

import pytest

from mrr_switch_optimizer.analysis.fabric_coverage import verify_permutation_coverage
from mrr_switch_optimizer.analysis.fabric_loss import (
    evaluate_fixed_fabric_worst_insertion_loss,
)
from mrr_switch_optimizer.app.fabric_reports import write_fixed_fabric_reports
from mrr_switch_optimizer.core.fabric import build_fabric_graph
from mrr_switch_optimizer.core.models import LEGACY_V2_CELL_GEOMETRY
from mrr_switch_optimizer.core.sparams import MOCK_S_TABLE
from mrr_switch_optimizer.core.state_assignment import WaksmanStrategy
from mrr_switch_optimizer.core.topology import PaddedBenesTopology, WaksmanTopology
from mrr_switch_optimizer.placement.layout import build_cells
from mrr_switch_optimizer.output.visualize import save_fixed_fabric_png
from mrr_switch_optimizer.routing.fabric import route_fixed_fabric
from mrr_switch_optimizer.routing.types import RoutingRules


def test_fixed_fabric_router_uses_edge_ids_and_is_permutation_independent(
    tmp_path: Path,
) -> None:
    topology = PaddedBenesTopology(4)
    graph = build_fabric_graph(topology)
    cells = build_cells(topology, MOCK_S_TABLE)
    rules = RoutingRules(
        max_ripup_passes=0,
        max_astar_pops=8000,
        grid_margin_tracks=12,
    )

    first = route_fixed_fabric(topology, graph, cells, rules)
    second = route_fixed_fabric(topology, graph, cells, rules)

    assert first == second
    assert all(route.edge_id in graph.edge_ids for route in first.routes)
    assert all(failure.edge_id in graph.edge_ids for failure in first.failed_edges)
    assert set(first.routed_edge_ids) == set(graph.edge_ids)
    assert all(
        tuple(edge_route.edge_id for edge_route in route.edge_routes)
        == route.covered_edge_ids
        for route in first.routes
    )
    assert all(
        route.length_um
        == pytest.approx(
            sum(edge_route.length_um for edge_route in route.edge_routes)
            + 16.0 * (len(route.edge_routes) - 1)
        )
        for route in first.routes
    )
    assert not first.failed_edges
    assert not first.drc_violations

    coverage = verify_permutation_coverage(topology, graph)
    loss_report = evaluate_fixed_fabric_worst_insertion_loss(topology, cells, first)
    write_fixed_fabric_reports(
        tmp_path,
        graph,
        first,
        coverage,
        {"legacy": True},
        loss_report,
    )
    save_fixed_fabric_png(
        topology,
        cells,
        first,
        tmp_path / "fixed_fabric_layout.png",
        wire_pitch_um=36.0,
    )
    summary = json.loads((tmp_path / "fabric_routing_summary.json").read_text())
    coverage_payload = json.loads((tmp_path / "permutation_coverage.json").read_text())
    assert summary["routed_edge_count"] == 16
    assert summary["failed_edge_count"] == 0
    assert summary["drc_violation_count"] == 0
    assert coverage_payload["permutations_checked"] == 24
    assert coverage_payload["passed"] is True
    assert coverage_payload["physical_fabric_routed_once"] is True
    assert coverage_payload["physical_routing_permutation_count"] == 0
    assert coverage_payload["geometry_sha256"] == summary["geometry_sha256"]
    assert summary["mrr_count"] == topology.n_MRR
    assert summary["worst_insertion_loss_db"] > 0.0
    assert (tmp_path / "fabric_edges.csv").is_file()
    assert (tmp_path / "fabric_drc_violations.csv").is_file()
    assert (tmp_path / "legacy_comparison.json").is_file()
    assert (tmp_path / "fabric_loss_summary.json").is_file()
    assert (tmp_path / "fixed_fabric_layout.png").stat().st_size > 0


def test_waksman_fixed_fabric_routes_pass_through_stages_once() -> None:
    topology = WaksmanTopology(
        4,
        strategy=WaksmanStrategy(),
        verify_rnb=False,
    )
    graph = build_fabric_graph(topology)
    cells = build_cells(
        topology,
        MOCK_S_TABLE,
        stage_pitch_um=140.0,
        wire_pitch_um=64.0,
        cell_geometry=LEGACY_V2_CELL_GEOMETRY,
    )
    result = route_fixed_fabric(
        topology,
        graph,
        cells,
        RoutingRules(
            max_astar_pops=30000,
            max_ripup_passes=5,
            grid_margin_tracks=20,
            waveguide_width_um=LEGACY_V2_CELL_GEOMETRY.waveguide_width_um,
        ),
        wire_pitch_um=64.0,
    )

    assert topology.n_MRR == 5
    assert len(graph.edges) == 14
    assert set(result.routed_edge_ids) == set(graph.edge_ids)
    assert not result.failed_edges
    assert not result.drc_violations
    assert verify_permutation_coverage(topology, graph).permutations_checked == 24

    loss_report = evaluate_fixed_fabric_worst_insertion_loss(
        topology,
        cells,
        result,
    )
    assert loss_report.mrr_count == 5
    assert loss_report.permutations_checked == 24
    assert loss_report.paths_checked == 96
    assert loss_report.worst_insertion_loss_db > 0.0
