from __future__ import annotations

from collections import Counter
from dataclasses import replace

import pytest

from mrr_switch_optimizer.app.fabric_reports import fixed_fabric_geometry_hash
from mrr_switch_optimizer.core.fabric import build_fabric_graph
from mrr_switch_optimizer.core.models import LEGACY_V2_CELL_GEOMETRY
from mrr_switch_optimizer.core.sparams import MOCK_S_TABLE
from mrr_switch_optimizer.core.state_assignment import BenesLoopingStrategy, WaksmanStrategy
from mrr_switch_optimizer.core.topology import PaddedBenesTopology, WaksmanTopology
from mrr_switch_optimizer.placement.layout import build_cells
from mrr_switch_optimizer.routing.drc import validate_physical_routes
from mrr_switch_optimizer.routing.fabric import (
    FixedFabricRoutingResult,
    _preferred_waveguide_order,
    route_fixed_fabric,
)
from mrr_switch_optimizer.routing.grid import (
    _bend_step_cost,
    _crossing_step_cost,
    _reserved_spacing_conflict,
    _search_guidance_cost,
)
from mrr_switch_optimizer.routing.grid_router import HistoryCost, RouterState, neighbor_moves
from mrr_switch_optimizer.routing.physical import (
    _candidate_route_cost,
    _effective_fallback_beam_width,
    _route_external_hop_via_waypoints,
    _validate_polyline_bend_spacing,
)
from mrr_switch_optimizer.routing.port_access import (
    _port_escape_point,
    _port_route_point,
)
from mrr_switch_optimizer.routing.types import (
    PhysicalRoute,
    RoutingError,
    RoutingRules,
    RoutingWindow,
)


def _route_waksman(
    n_logical: int,
    rules: RoutingRules | None = None,
) -> tuple[FixedFabricRoutingResult, dict]:
    topology = WaksmanTopology(n_logical, strategy=WaksmanStrategy())
    graph = build_fabric_graph(topology)
    cells = build_cells(
        topology,
        MOCK_S_TABLE,
        stage_pitch_um=140.0,
        wire_pitch_um=64.0,
        cell_geometry=LEGACY_V2_CELL_GEOMETRY,
    )
    legacy_rules = replace(
        rules
        or RoutingRules(
            max_astar_pops=30000,
            max_ripup_passes=5,
            grid_margin_tracks=20,
        ),
        waveguide_width_um=LEGACY_V2_CELL_GEOMETRY.waveguide_width_um,
    )
    result = route_fixed_fabric(
        topology,
        graph,
        cells,
        legacy_rules,
        x_start=20.0,
        x_end=max(cell.center[0] for cell in cells.values()) + 70.0,
        wire_pitch_um=64.0,
    )
    return result, cells


@pytest.fixture(scope="module")
def baseline_n8() -> tuple[FixedFabricRoutingResult, dict]:
    return _route_waksman(8)


def _physical_routes(result: FixedFabricRoutingResult) -> tuple[PhysicalRoute, ...]:
    waveguide_by_owner = {
        waveguide.owner_edge_id: waveguide for waveguide in result.graph.waveguides
    }
    return tuple(
        PhysicalRoute(
            input_port=waveguide_by_owner[route.edge_id].input_wire,
            output_port=waveguide_by_owner[route.edge_id].output_wire,
            waypoints=route.waypoints,
            length_um=route.length_um,
            bend_count=route.bend_count,
            external_segments=route.external_segments,
            local_segments=route.local_segments,
        )
        for route in result.routes
    )


def test_default_rules_preserve_waksman_n4_geometry_hash() -> None:
    result, _cells = _route_waksman(4)

    assert fixed_fabric_geometry_hash(result) == (
        "dc7b62ecb8ad46a3b06b1b707adc1278267c3b84befc73f29430c2d30a34b436"
    )


def test_crossing_outgoing_arm_state_rejects_turn_until_clear() -> None:
    moves = neighbor_moves(
        RouterState(1, 1, "E", crossing_arm_remaining_um=10.0),
        (0.0, 8.0, 16.0),
        (0.0, 8.0, 16.0),
    )

    assert {move.next_state.orientation for move in moves} == {"E"}
    assert moves[0].next_state.crossing_arm_remaining_um == pytest.approx(2.0)

    cleared = neighbor_moves(
        moves[0].next_state,
        (0.0, 8.0, 16.0, 24.0),
        (0.0, 8.0, 16.0),
    )
    assert {move.next_state.orientation for move in cleared} == {"E"}
    assert cleared[0].next_state.crossing_arm_remaining_um == 0.0


def test_default_rules_preserve_waksman_n6_geometry_hash() -> None:
    result, _cells = _route_waksman(6)

    assert fixed_fabric_geometry_hash(result) == (
        "0e3bb955f0aecf0b77d4f625fe81c5f973faaf027803626d3f202b1bb70ee1b1"
    )


def test_new_drc_rules_document_waksman_n8_blind_spots(
    baseline_n8: tuple[FixedFabricRoutingResult, dict],
) -> None:
    result, cells = baseline_n8
    assert fixed_fabric_geometry_hash(result) == (
        "dbee43715e20f6f13d9d603f0f9029caebf058418fe84dc5509fa6455b36b67f"
    )
    rules = replace(
        result.rules,
        drc_same_net_min_spacing=True,
        drc_perpendicular_clearance=True,
        drc_bend_radius_legality=True,
    )

    counts = Counter(
        violation.rule
        for violation in validate_physical_routes(_physical_routes(result), cells, rules)
    )

    assert counts["same_net_min_spacing"] > 0
    assert counts["perpendicular_clearance"] == 2
    assert counts["bend_radius_legality"] == 11


def test_db_cost_model_normalizes_physical_loss_and_scales_guidance() -> None:
    rules = RoutingRules(
        cost_model="db",
        prop_loss_db_per_um=0.0002,
        crossing_loss_db_per_cross=0.1,
        bend_loss_db_per_bend=0.005,
        bend_radius_um=5.0,
        db_tie_breaker_scale=0.05,
    )

    assert _crossing_step_cost(1, rules) == pytest.approx(500.0)
    assert _bend_step_cost(rules) == pytest.approx(25.0 + 2.5 * 3.141592653589793)
    assert _search_guidance_cost(200.0, rules) == pytest.approx(10.0)


def test_db_candidate_cost_matches_fabric_loss_objective_in_internal_units() -> None:
    rules = RoutingRules(
        cost_model="db",
        prop_loss_db_per_um=0.0002,
        crossing_loss_db_per_cross=0.1,
        bend_loss_db_per_bend=0.005,
        bend_radius_um=5.0,
    )
    route = ((0.0, 0.0), (20.0, 0.0), (20.0, 20.0))
    blocker = ((10.0, -10.0), (10.0, 10.0))
    expected_loss_db = 0.0002 * (40.0 + 2.5 * 3.141592653589793) + 0.005 + 0.1

    assert _candidate_route_cost(route, [], None, rules, [blocker]) == pytest.approx(
        expected_loss_db / rules.prop_loss_db_per_um
    )


def test_db_candidate_rejects_nonadjacent_same_net_corner_contact() -> None:
    rules = RoutingRules(
        cost_model="db",
        grid_pitch_um=10.0,
        min_spacing_um=0.0,
        bend_radius_um=5.0,
    )
    committed = [
        ((0.0, 0.0), (20.0, 0.0)),
        ((20.0, 0.0), (20.0, 20.0)),
    ]

    with pytest.raises(RoutingError, match="same_net_touching_corner"):
        _route_external_hop_via_waypoints(
            ((0.0, 0.0), (0.0, 10.0)),
            [],
            [],
            ((0.0, 10.0, 20.0), (0.0, 10.0, 20.0)),
            rules,
            src_label="m0.th",
            dst_label="m1.in",
            forbidden_points=set(),
            current_segments=committed,
            current_external_segments=committed,
            occupied_segments=[],
            crossing_count_by_pair={},
            crossing_sources_by_pair={},
            soft_blockers=[],
            reserved_spacing_blockers=[],
            reserved_guard_blockers=[],
            reserved_crossing_owners=set(),
            deferred_crossing_owners=set(),
            preferred_bend_x=None,
            reserve_space_penalty=False,
            port_access=None,
            grid_bounds=RoutingWindow(
                left=0.0,
                right=20.0,
                bottom=0.0,
                top=20.0,
            ),
            base_detour_tracks=1,
            ripup_pass_idx=0,
            history_cost=HistoryCost(),
            candidate_label="db hard-contact candidate",
        )


def test_db_fixed_fabric_orders_unequal_paths_longest_first() -> None:
    topology = WaksmanTopology(6, strategy=WaksmanStrategy())
    graph = build_fabric_graph(topology)

    assert _preferred_waveguide_order(graph) is None
    ordered = _preferred_waveguide_order(graph, RoutingRules(cost_model="db"))

    assert ordered is not None
    assert tuple(waveguide.input_wire for waveguide in ordered) == (0, 1, 2, 3, 4, 5)


def test_db_padded_benes_routes_boundary_then_descending_inputs() -> None:
    topology = PaddedBenesTopology(8, strategy=BenesLoopingStrategy())
    graph = build_fabric_graph(topology)

    legacy = _preferred_waveguide_order(graph)
    db_order = _preferred_waveguide_order(graph, RoutingRules(cost_model="db"))

    assert legacy is not None
    assert db_order is not None
    assert tuple(waveguide.input_wire for waveguide in legacy) == (6, 7, 0, 1, 2, 3, 4, 5)
    assert tuple(waveguide.input_wire for waveguide in db_order) == (6, 7, 5, 4, 3, 2, 1, 0)


def test_db_same_net_dead_end_enables_bounded_hop_backtracking() -> None:
    rules = RoutingRules(cost_model="db")

    assert _effective_fallback_beam_width(
        rules,
        RoutingError("cannot route waksman_s1 to waksman_s2 counters=same_net:1"),
    ) == 4
    assert _effective_fallback_beam_width(
        RoutingRules(),
        RoutingError("cannot route counters=same_net:1,segment:2"),
    ) == 0
    assert _effective_fallback_beam_width(
        rules,
        RoutingError("cannot route padded_benes_s1 counters=same_net:1"),
    ) == 0
    assert _effective_fallback_beam_width(
        rules,
        RoutingError("cannot route padded_benes_s3: A* pop limit exceeded"),
    ) == 2


def test_db_reserved_future_port_rejects_endpoint_near_miss() -> None:
    candidate = ((40.0, 313.0), (185.0, 313.0))
    future_port_runway = ((185.0, 316.0), (195.0, 316.0))

    assert _reserved_spacing_conflict(
        candidate,
        [future_port_runway],
        RoutingRules(cost_model="db", min_spacing_um=4.0),
    )
    assert not _reserved_spacing_conflict(
        candidate,
        [future_port_runway],
        RoutingRules(min_spacing_um=4.0),
    )
    assert not _reserved_spacing_conflict(
        ((40.0, 316.0), (185.0, 316.0)),
        [future_port_runway],
        RoutingRules(cost_model="db", min_spacing_um=4.0),
    )
def test_bend_spacing_state_requires_two_grid_steps_between_turns() -> None:
    x_tracks = (0.0, 8.0, 16.0)
    y_tracks = (0.0, 8.0)
    short_run = RouterState(1, 0, "E", 8.0)
    legal_run = RouterState(1, 0, "E", 10.0)

    short_orientations = {
        move.next_state.orientation
        for move in neighbor_moves(
            short_run,
            x_tracks,
            y_tracks,
            bend_spacing_um=10.0,
        )
    }
    legal_moves = neighbor_moves(
        legal_run,
        x_tracks,
        y_tracks,
        bend_spacing_um=10.0,
    )

    assert short_orientations == {"E"}
    north = next(move for move in legal_moves if move.next_state.orientation == "N")
    assert north.next_state.straight_run_um == pytest.approx(8.0)


def test_port_access_macro_adds_a_two_radius_runway_only_when_enabled() -> None:
    topology = WaksmanTopology(4, strategy=WaksmanStrategy())
    cells = build_cells(topology, MOCK_S_TABLE)
    cell = next(iter(cells.values()))
    legacy = RoutingRules()
    legal = RoutingRules(
        legalize_port_access=True,
        bend_radius_um=5.0,
        port_access_runway_um=10.0,
    )

    assert _port_route_point(cell, "th", legacy, 2.0) == _port_escape_point(
        cell,
        "th",
        legacy,
        2.0,
    )
    escape = _port_escape_point(cell, "th", legal, 2.0)
    route = _port_route_point(cell, "th", legal, 2.0)
    assert route == (escape[0] + 10.0, escape[1])


def test_junction_validation_rejects_a_short_final_run() -> None:
    rules = RoutingRules(enforce_bend_spacing=True, bend_radius_um=5.0)

    with pytest.raises(RoutingError, match="port junction violates bend spacing"):
        _validate_polyline_bend_spacing(
            ((0.0, 0.0), (8.0, 0.0), (8.0, 8.0)),
            rules,
            src_label="m0.th",
            dst_label="m1.in",
        )


@pytest.mark.parametrize("n_logical", [4, 6])
def test_enforced_waksman_has_no_bend_radius_violations(n_logical: int) -> None:
    result, _cells = _route_waksman(
        n_logical,
        RoutingRules(
            max_astar_pops=30000,
            max_ripup_passes=5,
            grid_margin_tracks=20,
            cost_model="db",
            enforce_bend_spacing=True,
            legalize_port_access=True,
            drc_bend_radius_legality=True,
        ),
    )

    assert not result.failed_edges
    assert not [
        violation
        for violation in result.drc_violations
        if violation.rule == "bend_radius_legality"
    ]
