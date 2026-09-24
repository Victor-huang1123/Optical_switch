from __future__ import annotations

from dataclasses import replace
from math import inf

import pytest

from mrr_switch_optimizer.analysis.fabric_loss import (
    evaluate_fixed_fabric_worst_insertion_loss,
)
from mrr_switch_optimizer.analysis.nsweep import (
    evaluate_fixed_fabric_parallel,
    evaluate_fixed_fabric_path_space,
    verify_worst_il_witness,
)
from mrr_switch_optimizer.app.fabric_reports import fixed_fabric_geometry_hash
from mrr_switch_optimizer.app.nsweep_campaign import (
    METRIC_FIELDS,
    _acceptance_tier,
    _count_audit_violations,
    _manufacturability_status,
)
from mrr_switch_optimizer.core.fabric import build_fabric_graph
from mrr_switch_optimizer.core.models import LEGACY_V2_CELL_GEOMETRY
from mrr_switch_optimizer.core.sparams import MOCK_S_TABLE, load_mrr_s_table
from mrr_switch_optimizer.core.state_assignment import BenesLoopingStrategy, WaksmanStrategy
from mrr_switch_optimizer.core.topology import PaddedBenesTopology, WaksmanTopology
from mrr_switch_optimizer.routing.benes_template_layout import predicted_template_crossings
from mrr_switch_optimizer.routing.crossing import merged_route_arm_clearances
from mrr_switch_optimizer.routing.drc import _consecutive_bend_pairs_too_close
from mrr_switch_optimizer.routing.envelope import build_envelope_cells, octave_envelope
from mrr_switch_optimizer.routing.fabric import FixedFabricRoutingResult, route_fixed_fabric
from mrr_switch_optimizer.routing.geometry import _direction, _manhattan
from mrr_switch_optimizer.routing.types import (
    DRCViolation,
    PhysicalRoute,
    RoutingRules,
)


def _rules(**updates: object) -> RoutingRules:
    return replace(
        RoutingRules(),
        max_astar_pops=30000,
        max_ripup_passes=5,
        grid_margin_tracks=20,
        cost_model="db",
        enforce_bend_spacing=True,
        legalize_port_access=True,
        drc_same_net_min_spacing=True,
        drc_perpendicular_clearance=True,
        drc_bend_radius_legality=True,
        **updates,
    )


def test_phase5_crossing_clearance_acceptance_is_two_tier() -> None:
    astar_tier = _acceptance_tier("astar", crossing_clearance_enabled=True)
    template_tier = _acceptance_tier("template", crossing_clearance_enabled=True)

    assert astar_tier[0:2] == ("astar_measurement_audit", "measurement_only")
    assert astar_tier[3] == ("legacy_drc", "bend_radius_legality")
    assert template_tier[0:2] == ("template_hard_gate", "hard_gate")
    assert template_tier[3] == (
        "legacy_drc",
        "bend_radius_legality",
        "crossing_clearance",
    )
    assert "crossing_clearance" in METRIC_FIELDS
    assert "crossing_clearance_acceptance" in METRIC_FIELDS


def test_crossing_clearance_is_counted_outside_legacy_core_drc() -> None:
    counts = _count_audit_violations(
        (
            DRCViolation("crossing_clearance", "I0/I1", "crossing"),
            DRCViolation("perpendicular_clearance", "I0/I2", "perpendicular"),
            DRCViolation("same_net_min_spacing", "I0", "same net"),
            DRCViolation("bend_radius_legality", "I1", "bend"),
            DRCViolation("parallel_spacing", "I2/I3", "legacy core"),
        )
    )

    assert counts == {
        "legacy_drc": 1,
        "same_net_min_spacing": 1,
        "perpendicular_clearance": 1,
        "crossing_clearance": 1,
        "bend_radius_legality": 1,
    }
    assert _manufacturability_status("astar", counts) == "measurement_only"
    assert (
        _manufacturability_status("template", {name: 0 for name in counts})
        == "all_clear"
    )


def _template(
    n: int,
    *,
    n_logical: int | None = None,
    straighten_jogs: bool = False,
) -> FixedFabricRoutingResult:
    topology = PaddedBenesTopology(
        n if n_logical is None else n_logical,
        strategy=BenesLoopingStrategy(),
        verify_rnb=False,
    )
    envelope = octave_envelope(topology.N_logical)
    cells = build_envelope_cells(topology, MOCK_S_TABLE, envelope)
    return route_fixed_fabric(
        topology,
        build_fabric_graph(topology),
        cells,
        _rules(min_crossing_clearance_um=10.0),
        x_start=envelope.x_start_um,
        x_end=envelope.x_end_um,
        wire_pitch_um=envelope.wire_pitch_um,
        layout_mode="template",
        straighten_jogs=straighten_jogs,
    )


@pytest.mark.parametrize("n_physical", [4, 8, 16])
def test_g1_template_crossing_counts(n_physical: int) -> None:
    result = _template(n_physical)
    assert len(result.crossings) == predicted_template_crossings(n_physical)


@pytest.mark.parametrize("n_physical", [4, 8, 16])
def test_g2_template_drc_is_clean(n_physical: int) -> None:
    result = _template(n_physical)
    assert not result.failed_edges
    assert not result.drc_violations


@pytest.mark.parametrize("n_physical", [4, 8, 16])
def test_g2_template_has_no_one_track_crossing_arm(n_physical: int) -> None:
    result = _template(n_physical)
    routes = {
        route.input_port: route
        for route in (
            _physical_route(result, route_index)
            for route_index in range(len(result.routes))
        )
    }
    owner_to_wire = {
        waveguide.owner_edge_id: waveguide.input_wire
        for waveguide in result.graph.waveguides
    }
    arm_clearances = [
        clearance
        for crossing in result.crossings
        for edge_id in (crossing.edge_a, crossing.edge_b)
        for clearance in merged_route_arm_clearances(
            routes[owner_to_wire[edge_id]],
            crossing.location,
        )
    ]

    assert min(arm_clearances) >= 10.0
    assert 8.0 not in arm_clearances


def test_template_straightening_flag_is_a_geometry_noop() -> None:
    baseline = _template(4)
    enabled = _template(4, straighten_jogs=True)

    assert fixed_fabric_geometry_hash(enabled) == fixed_fabric_geometry_hash(baseline)
    assert enabled.straighten_stats is not None
    assert enabled.straighten_stats.rewrites == 0


def _physical_route(result: FixedFabricRoutingResult, route_index: int) -> PhysicalRoute:
    route = result.routes[route_index]
    waveguide = next(
        waveguide
        for waveguide in result.graph.waveguides
        if waveguide.owner_edge_id == route.edge_id
    )
    return PhysicalRoute(
        input_port=waveguide.input_wire,
        output_port=waveguide.output_wire,
        waypoints=route.waypoints,
        length_um=route.length_um,
        bend_count=route.bend_count,
        external_segments=route.external_segments,
        local_segments=route.local_segments,
    )


@pytest.mark.parametrize("n_physical", [4, 8, 16])
def test_g3_template_bends_are_legal_by_construction(n_physical: int) -> None:
    result = _template(n_physical)
    minimum = inf
    for route_index in range(len(result.routes)):
        route = _physical_route(result, route_index)
        assert not _consecutive_bend_pairs_too_close(route, result.rules)
        points = route.waypoints
        bends = [
            point
            for before, point, after in zip(points, points[1:], points[2:])
            if _direction(before, point) != _direction(point, after)
        ]
        minimum = min(
            minimum,
            *(_manhattan(first, second) for first, second in zip(bends, bends[1:])),
        )
    assert minimum >= 10.0


@pytest.mark.parametrize(
    ("n_physical", "golden"),
    [
        (4, "85ff74a15c329e751866a9407a351a1b845299cc83f4d6ed7d917a56cdf5fc93"),
        (8, "c5ad40e124038391c6f1e9370af9c85b3e232445defec170e634db1b5093547a"),
        (16, "5da7c24cd2794cf351904ff75e541f9682f2d1f64248835f27133f65f126fa6a"),
    ],
)
def test_g4_template_is_deterministic_and_has_no_astar(
    n_physical: int,
    golden: str,
) -> None:
    first = _template(n_physical)
    second = _template(n_physical)
    assert first.stats.astar_calls == second.stats.astar_calls == 0
    assert fixed_fabric_geometry_hash(first) == fixed_fabric_geometry_hash(second) == golden


def test_g5_one_envelope_id_per_octave() -> None:
    by_octave: dict[int, set[str]] = {}
    for n in range(3, 13):
        envelope = octave_envelope(n)
        by_octave.setdefault(envelope.n_canvas, set()).add(envelope.envelope_id)
    assert all(len(ids) == 1 for ids in by_octave.values())


@pytest.mark.parametrize("logical_sizes", [(3, 4), (5, 6, 7, 8), (9, 10, 11, 12)])
def test_g6_padding_reuses_geometry_and_flags_blocked_ports(
    logical_sizes: tuple[int, ...],
) -> None:
    hashes = set()
    for n in logical_sizes:
        result = _template(octave_envelope(n).n_canvas, n_logical=n)
        hashes.add(fixed_fabric_geometry_hash(result))
        assert result.graph.blocked_ports == tuple(range(n, result.graph.n_physical))
        for edge in result.graph.edges:
            for endpoint in (edge.source, edge.target):
                if endpoint.kind in {"input", "output"}:
                    assert endpoint.blocked == (endpoint.wire >= n)
    assert len(hashes) == 1


@pytest.mark.parametrize("n", range(3, 9))
def test_g7_corridor_guide_mode_is_a_documented_no_op(n: int) -> None:
    topology = WaksmanTopology(n, strategy=WaksmanStrategy(), verify_rnb=False)
    envelope = octave_envelope(n)
    cells = build_envelope_cells(topology, MOCK_S_TABLE, envelope)
    graph = build_fabric_graph(topology)
    base_rules = replace(
        _rules(),
        drc_same_net_min_spacing=False,
        drc_perpendicular_clearance=False,
        corridor_guide_mode="off",
    )
    baseline = route_fixed_fabric(
        topology,
        graph,
        cells,
        base_rules,
        x_start=envelope.x_start_um,
        x_end=envelope.x_end_um,
        wire_pitch_um=envelope.wire_pitch_um,
        layout_mode="astar",
    )
    guided = route_fixed_fabric(
        topology,
        graph,
        cells,
        replace(base_rules, corridor_guide_mode="soft"),
        x_start=envelope.x_start_um,
        x_end=envelope.x_end_um,
        wire_pitch_um=envelope.wire_pitch_um,
        layout_mode="astar",
    )
    # corridor_guide_mode is consumed nowhere.  Both calls receive the same
    # always-on _corridor_preferred_x preferred-bend bias in physical.py, so G7
    # documents identical routing rather than claiming a gated guide layer.
    assert not baseline.failed_edges and not guided.failed_edges
    assert fixed_fabric_geometry_hash(guided) == fixed_fabric_geometry_hash(baseline)
    assert guided.crossings == baseline.crossings


@pytest.fixture(scope="module")
def old_pitch_waksman_n8() -> tuple:
    topology = WaksmanTopology(8, strategy=WaksmanStrategy(), verify_rnb=False)
    from mrr_switch_optimizer.placement.layout import build_cells

    cells = build_cells(
        topology,
        load_mrr_s_table("mrr_sparam_library"),
        stage_pitch_um=140.0,
        wire_pitch_um=64.0,
        cell_geometry=LEGACY_V2_CELL_GEOMETRY,
    )
    result = route_fixed_fabric(
        topology,
        build_fabric_graph(topology),
        cells,
        replace(
            _rules(),
            waveguide_width_um=LEGACY_V2_CELL_GEOMETRY.waveguide_width_um,
            drc_same_net_min_spacing=False,
            drc_perpendicular_clearance=False,
        ),
        x_start=20.0,
        x_end=max(cell.center[0] for cell in cells.values()) + 70.0,
        wire_pitch_um=64.0,
        layout_mode="astar",
    )
    assert not result.failed_edges and not result.drc_violations
    return topology, cells, result


def test_parallel_n8_is_float_identical_to_single_process(
    old_pitch_waksman_n8: tuple,
) -> None:
    topology, cells, result = old_pitch_waksman_n8
    single = evaluate_fixed_fabric_worst_insertion_loss(topology, cells, result)
    parallel = evaluate_fixed_fabric_parallel(topology, cells, result, workers=8)
    assert parallel.loss_report.worst_insertion_loss_db == single.worst_insertion_loss_db
    assert parallel.loss_report.worst_permutation == single.worst_permutation
    assert parallel.coverage_report.passed


def test_path_space_waksman_n8_reference_gate(old_pitch_waksman_n8: tuple) -> None:
    topology, cells, result = old_pitch_waksman_n8
    report = evaluate_fixed_fabric_path_space(topology, cells, result, random_tries=512)
    assert report.enumerated_paths == 176
    assert report.unrealizable_paths == 0
    assert report.worst_insertion_loss_db == pytest.approx(4.575739459285832)
    verify_worst_il_witness(
        topology,
        cells,
        result,
        witness_permutation=report.witness_permutation,
        input_port=report.worst_path.input_port,
        expected_il_db=report.worst_insertion_loss_db,
    )


def test_path_space_padded_benes_n8_reference_gate() -> None:
    topology = PaddedBenesTopology(8, strategy=BenesLoopingStrategy(), verify_rnb=False)
    from mrr_switch_optimizer.placement.layout import build_cells

    cells = build_cells(
        topology,
        load_mrr_s_table("mrr_sparam_library"),
        stage_pitch_um=140.0,
        wire_pitch_um=64.0,
        cell_geometry=LEGACY_V2_CELL_GEOMETRY,
    )
    result = route_fixed_fabric(
        topology,
        build_fabric_graph(topology),
        cells,
        replace(
            _rules(),
            waveguide_width_um=LEGACY_V2_CELL_GEOMETRY.waveguide_width_um,
            drc_same_net_min_spacing=False,
            drc_perpendicular_clearance=False,
        ),
        x_start=20.0,
        x_end=max(cell.center[0] for cell in cells.values()) + 70.0,
        wire_pitch_um=64.0,
        layout_mode="astar",
    )
    assert not result.failed_edges and not result.drc_violations
    report = evaluate_fixed_fabric_path_space(topology, cells, result, random_tries=512)
    assert report.enumerated_paths == 256
    assert report.unrealizable_paths == 80
    assert report.worst_insertion_loss_db == pytest.approx(4.749987238893526)


@pytest.fixture(scope="module")
def v3_envelope_waksman_n8() -> tuple:
    topology = WaksmanTopology(8, strategy=WaksmanStrategy(), verify_rnb=False)
    envelope = octave_envelope(8)
    cells = build_envelope_cells(
        topology,
        load_mrr_s_table("mrr_sparam_library"),
        envelope,
    )
    result = route_fixed_fabric(
        topology,
        build_fabric_graph(topology),
        cells,
        replace(
            _rules(),
            drc_same_net_min_spacing=False,
            drc_perpendicular_clearance=False,
        ),
        x_start=envelope.x_start_um,
        x_end=envelope.x_end_um,
        wire_pitch_um=envelope.wire_pitch_um,
        layout_mode="astar",
    )
    assert envelope.envelope_id == (
        "octave-p8-bbox28x22-dy5p5-w450-sp168-wp64-gp8-m20"
    )
    assert result.rules.waveguide_width_um == 0.45
    assert result.stats.astar_calls > 0
    assert not result.failed_edges and not result.drc_violations
    return topology, cells, result


def test_v3_envelope_waksman_n8_reference_gate(
    v3_envelope_waksman_n8: tuple,
) -> None:
    topology, cells, result = v3_envelope_waksman_n8
    report = evaluate_fixed_fabric_path_space(topology, cells, result, random_tries=512)
    assert report.enumerated_paths == 176
    assert report.unrealizable_paths == 0
    assert report.worst_insertion_loss_db == pytest.approx(4.736990589704455)
    verify_worst_il_witness(
        topology,
        cells,
        result,
        witness_permutation=report.witness_permutation,
        input_port=report.worst_path.input_port,
        expected_il_db=report.worst_insertion_loss_db,
    )


def test_v3_envelope_padded_benes_n8_reference_gate() -> None:
    topology = PaddedBenesTopology(8, strategy=BenesLoopingStrategy(), verify_rnb=False)
    envelope = octave_envelope(8)
    cells = build_envelope_cells(
        topology,
        load_mrr_s_table("mrr_sparam_library"),
        envelope,
    )
    result = route_fixed_fabric(
        topology,
        build_fabric_graph(topology),
        cells,
        _rules(),
        x_start=envelope.x_start_um,
        x_end=envelope.x_end_um,
        wire_pitch_um=envelope.wire_pitch_um,
        layout_mode="template",
    )
    assert result.stats.astar_calls == 0
    assert not result.failed_edges and not result.drc_violations
    report = evaluate_fixed_fabric_path_space(topology, cells, result, random_tries=512)
    assert report.enumerated_paths == 256
    assert report.unrealizable_paths == 80
    assert report.worst_insertion_loss_db == pytest.approx(4.574819091965323)


def test_path_space_waksman_n10_reference_certificate() -> None:
    topology = WaksmanTopology(10, strategy=WaksmanStrategy(), verify_rnb=False)
    from mrr_switch_optimizer.placement.layout import build_cells

    cells = build_cells(
        topology,
        load_mrr_s_table("mrr_sparam_library"),
        stage_pitch_um=140.0,
        wire_pitch_um=64.0,
        cell_geometry=LEGACY_V2_CELL_GEOMETRY,
    )
    result = route_fixed_fabric(
        topology,
        build_fabric_graph(topology),
        cells,
        replace(
            _rules(),
            waveguide_width_um=LEGACY_V2_CELL_GEOMETRY.waveguide_width_um,
            drc_same_net_min_spacing=False,
            drc_perpendicular_clearance=False,
        ),
        x_start=20.0,
        x_end=max(cell.center[0] for cell in cells.values()) + 70.0,
        wire_pitch_um=64.0,
        layout_mode="astar",
    )
    assert not result.failed_edges and not result.drc_violations
    report = evaluate_fixed_fabric_path_space(topology, cells, result, random_tries=512)
    assert report.enumerated_paths == 436
    assert report.worst_insertion_loss_db == pytest.approx(5.892242, abs=5e-7)
    verify_worst_il_witness(
        topology,
        cells,
        result,
        witness_permutation=report.witness_permutation,
        input_port=report.worst_path.input_port,
        expected_il_db=report.worst_insertion_loss_db,
    )
