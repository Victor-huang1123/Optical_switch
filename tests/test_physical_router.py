from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from functools import lru_cache

import matplotlib
import pytest

matplotlib.use("Agg")

from mrr_switch_optimizer.analysis.cost import ALPHA_DB_PER_UM
from mrr_switch_optimizer.app.cli import _physical_route_il_db
from mrr_switch_optimizer.core.models import (
    LEGACY_V2_CELL_GEOMETRY,
    default_add_drop_ports,
)
from mrr_switch_optimizer.core.sparams import MOCK_S_TABLE
from mrr_switch_optimizer.core.topology import (
    PaddedBenesTopology,
    RNBTopology,
    SpankeBenesTopology,
    WaksmanTopology,
)
from mrr_switch_optimizer.output.visualize import save_routing_png
from mrr_switch_optimizer.placement.layout import build_cells, wire_y
from mrr_switch_optimizer.routing.drc import validate_physical_routes
from mrr_switch_optimizer.routing.crossing import (
    CrossingRule,
    crossing_budget_exceeded,
    endpoint_crossing_candidate,
    endpoint_crossing_required,
    legal_crossing_candidate,
)
from mrr_switch_optimizer.routing.geometry import (
    _axis_segments,
    _bend_count,
    _orthogonal_crossing_point,
    _point_on_segment,
    _polyline_length,
    _inflate_obstacle,
    _routing_obstacle,
    _routing_obstacles,
    _segment_contact_point,
    _segment_intersects_obstacle,
    _segments_collinear_overlap,
)
from mrr_switch_optimizer.routing.grid_router import HistoryCost, RouterState, neighbor_moves
from mrr_switch_optimizer.routing.physical import (
    _ExternalHopSpec,
    _PathBranch,
    _bend_spaced_candidates,
    _bump_failed_source_history,
    _bounded_reroute_inputs,
    _corridor_key,
    _corridor_preferred_x,
    _crossing_budget_owners,
    _crossing_count_by_pair_tuple,
    _crossing_sources,
    _crossing_source_points,
    _deferred_crossing_owners,
    _detour_track_sequence,
    _displace_candidate_risers,
    _external_hop_specs,
    _failure_owner_components,
    _failed_related_inputs,
    _failed_reroute_priority,
    _filtered_source_forbidden_points,
    _foreign_port_access_guard_blockers,
    _foreign_port_access_guard_segments,
    _future_waveguide_crossing_owners,
    _io_clamped_routing_bounds,
    _merge_priority_inputs,
    _deterministic_hop_candidates,
    _owner_crossing_sources_for_current_net,
    _pass_candidate_score,
    _port_approach_point,
    _record_failed_crossing_sources,
    _prioritized_reroute_order,
    _record_failed_source_points,
    _active_crossing_sources_for_fixed_routes,
    _candidate_route_cost,
    _candidate_validation_detour_tracks,
    _remediation_window_expansion_tracks,
    _reserved_guard_conflict_reason,
    _reserved_owner_avoidance_hop_candidates,
    _ripup_order_candidates,
    _route_external_hop,
    _route_external_hop_via_waypoints,
    _route_external_hop_via_approach,
    _route_external_forbidden_points,
    _same_net_avoidance_hop_candidates,
    _same_net_touch_hits_committed_branch,
    _source_reserved_crossing_owners,
    _source_detour_candidates,
    _source_detour_alt_values,
    _sort_candidates_by_history,
    _violation_inputs,
    route_physical_design,
    route_physical_paths,
)
from mrr_switch_optimizer.routing.route_grid import (
    CrossingFootprint,
    RouteGrid,
    crossing_footprint_conflict,
)
from mrr_switch_optimizer.routing.grid import (
    _astar_route,
    _insert_track,
    _reserved_guard_conflict,
    _reserved_spacing_conflict,
    _routing_grid,
    _route_segment_available,
    _same_net_self_conflict,
    _same_net_self_conflict_reason,
    _tracks_between,
)
from mrr_switch_optimizer.routing.port_access import (
    PortAccessLegality,
    PortAccessPlan,
    PortAccessRegion,
    _mrr_internal_points,
    _port_escape_point,
    _port_route_point,
    _port_stub_point,
    _port_stub_segment,
    build_port_access_plan,
    hop_crossing_class,
    port_access_conflict,
    port_optical_role,
    port_side,
    reserved_segments_for_net,
)
from mrr_switch_optimizer.routing.types import (
    FailedNet,
    Obstacle,
    OccupiedRouteSegment,
    PhysicalRoute,
    PhysicalRoutingResult,
    DRCViolation,
    RouteCrossing,
    RoutingError,
    RoutingRules,
    RoutingWindow,
)


def test_same_net_touch_signature_identifies_an_earlier_committed_hop() -> None:
    branch = _PathBranch(
        points=[],
        external_segments=[((968.0, 448.0), (968.0, 416.0))],
        local_segments=[],
        history_cost=HistoryCost(),
    )
    archived = RoutingError(
        "same_net_samples=same_net_touch at=(968.000,444.000) "
        "prior=(968.000,448.000)->(968.000,416.000) "
        "counters=window:0,same_net:63,pops:92"
    )
    unrelated = RoutingError(
        "same_net_samples=same_net_touch at=(8.000,4.000) "
        "prior=(8.000,8.000)->(8.000,0.000)"
    )
    assert _same_net_touch_hits_committed_branch(archived, branch)
    assert not _same_net_touch_hits_committed_branch(unrelated, branch)


def test_same_net_committed_hop_error_records_failed_hop_index() -> None:
    from mrr_switch_optimizer.routing.physical import _SameNetCommittedHopError

    error = _SameNetCommittedHopError("boxed", failed_hop_index=4)
    assert str(error) == "boxed"
    assert error.failed_hop_index == 4


def test_deterministic_riser_displacement_uses_exact_track_ladder() -> None:
    candidate = ((0.0, 0.0), (32.0, 0.0), (32.0, 32.0), (64.0, 32.0))
    rules = RoutingRules(grid_pitch_um=8.0)
    assert [
        _displace_candidate_risers(candidate, rules, rung)
        for rung in (1, -1, 2, -2)
    ] == [
        ((0.0, 0.0), (40.0, 0.0), (40.0, 32.0), (64.0, 32.0)),
        ((0.0, 0.0), (24.0, 0.0), (24.0, 32.0), (64.0, 32.0)),
        ((0.0, 0.0), (48.0, 0.0), (48.0, 32.0), (64.0, 32.0)),
        ((0.0, 0.0), (16.0, 0.0), (16.0, 32.0), (64.0, 32.0)),
    ]


def test_riser_displacement_window_expansion_is_gated_and_tracks_the_ladder() -> None:
    rules = RoutingRules(
        grid_pitch_um=8.0,
        route_window_max_detour_tracks=4,
        explicit_crossings=True,
    )
    assert _candidate_validation_detour_tracks(
        "deterministic hop candidate",
        1,
        rules,
    ) == 4
    assert _candidate_validation_detour_tracks(
        "riser-displaced deterministic hop candidate",
        1,
        rules,
    ) == 4
    assert [
        _remediation_window_expansion_tracks(rules, rung)
        for rung in (1, -1, 2, -2)
    ] == [0, 0, 0, 0]

    enabled = RoutingRules(
        grid_pitch_um=8.0,
        remediation_window_expansion=True,
    )
    assert [
        _remediation_window_expansion_tracks(enabled, rung)
        for rung in (1, -1, 2, -2)
    ] == [3, 3, 4, 4]
    hop_window = RoutingWindow(440.0, 1122.0, 298.0, 534.0)
    assert hop_window.expanded(
        _remediation_window_expansion_tracks(enabled, 2),
        enabled.grid_pitch_um,
    ) == RoutingWindow(408.0, 1154.0, 266.0, 566.0)


def test_riser_displacement_preserves_outward_port_approach() -> None:
    rules = RoutingRules(
        grid_pitch_um=8.0,
        bend_radius_um=5.0,
        enforce_bend_spacing=True,
    )
    # A right-side th->add hop must depart east and approach the destination
    # from the east.  Displacing the already-legal internal riser preserves
    # both directions; rebuilding an L at an in-corridor midpoint reversed the
    # add-port approach and made every remediation rung fail.
    candidate = (
        (357.0, 260.0),
        (613.0, 260.0),
        (613.0, 444.0),
        (589.0, 444.0),
    )
    displaced = _displace_candidate_risers(candidate, rules, 1)
    assert displaced == (
        (357.0, 260.0),
        (621.0, 260.0),
        (621.0, 444.0),
        (589.0, 444.0),
    )
    assert displaced is not None
    from mrr_switch_optimizer.routing.physical import _validate_polyline_bend_spacing

    _validate_polyline_bend_spacing(
        displaced,
        rules,
        src_label="waksman_10x10_s1_w4_6.th",
        dst_label="waksman_10x10_s2_w0_4.add",
    )

    endpoint_risers = (
        (509.0, 444.0),
        (509.0, 433.0),
        (1053.0, 433.0),
        (1053.0, 444.0),
    )
    endpoint_displaced = _displace_candidate_risers(
        endpoint_risers,
        rules,
        1,
        src_label="waksman_10x10_s2_w0_4.drop",
        dst_label="waksman_10x10_s4_w0_4.add",
    )
    assert endpoint_displaced == (
        (509.0, 444.0),
        (501.0, 444.0),
        (501.0, 433.0),
        (1061.0, 433.0),
        (1061.0, 444.0),
        (1053.0, 444.0),
    )
    assert endpoint_displaced is not None
    _validate_polyline_bend_spacing(
        endpoint_displaced,
        rules,
        src_label="waksman_10x10_s2_w0_4.drop",
        dst_label="waksman_10x10_s4_w0_4.add",
    )


PERMUTATION = (2, 0, 5, 1, 3, 4)
TOPOLOGIES = (PaddedBenesTopology, WaksmanTopology, SpankeBenesTopology)

# Valid optical transitions for a standard add-drop MRR.
VALID_ADD_DROP_TRANSITIONS = {("in", "th"), ("in", "drop"), ("add", "drop"), ("add", "th")}


def test_add_drop_ports_use_standard_physical_sides() -> None:
    # Standard add-drop ring: in/drop on the LEFT, th/add on the RIGHT. The
    # bottom bus runs right->left (add -> drop), so this must not be "fixed" into
    # a co-propagating in/add-left, th/drop-right layout.
    ports = default_add_drop_ports()
    assert ports["in"].dx < 0 and ports["in"].dy > 0
    assert ports["drop"].dx < 0 and ports["drop"].dy < 0
    assert ports["th"].dx > 0 and ports["th"].dy > 0
    assert ports["add"].dx > 0 and ports["add"].dy < 0


def test_physical_side_and_optical_role_are_distinct_classifications() -> None:
    assert {port_side(p) for p in ("in", "drop")} == {"left"}
    assert {port_side(p) for p in ("th", "add")} == {"right"}
    assert {port_optical_role(p) for p in ("in", "add")} == {"input"}
    assert {port_optical_role(p) for p in ("th", "drop")} == {"output"}
    # The two axes disagree: add is a right-side optical input, drop a left-side
    # optical output. A single left/right rule cannot capture both.
    assert port_side("add") == "right" and port_optical_role("add") == "input"
    assert port_side("drop") == "left" and port_optical_role("drop") == "output"


def test_hop_crossing_class_uses_physical_port_sides() -> None:
    assert hop_crossing_class("th", "in") == "inward"
    assert hop_crossing_class("drop", "add") == "outward"
    assert hop_crossing_class("th", "add") == "same_side"
    assert hop_crossing_class("drop", "in") == "same_side"
    assert hop_crossing_class(None, "in") == "same_side"
    assert hop_crossing_class("th", None) == "same_side"


def test_route_steps_use_valid_add_drop_transitions() -> None:
    permutations = [PERMUTATION, (0, 1, 2, 3, 4, 5), (5, 4, 3, 2, 1, 0)]
    for topology_cls in TOPOLOGIES:
        topology = topology_cls()
        for perm in permutations:
            for path in topology.get_active_paths(perm):
                for step in path.steps:
                    assert (step.in_port, step.out_port) in VALID_ADD_DROP_TRANSITIONS
                    assert port_optical_role(step.in_port) == "input"
                    assert port_optical_role(step.out_port) == "output"


def test_s_table_contains_exactly_the_add_drop_transitions() -> None:
    table_transitions = {(in_port, out_port) for in_port, out_port, _state in MOCK_S_TABLE}
    assert table_transitions == VALID_ADD_DROP_TRANSITIONS


def test_port_access_plan_covers_every_used_mrr_port() -> None:
    for topology_cls in TOPOLOGIES:
        topology = topology_cls()
        cells = build_cells(topology, MOCK_S_TABLE)
        paths = _active_paths(topology)
        plan = build_port_access_plan(paths, cells, RoutingRules(), port_stub_um=2.0)
        for path in paths:
            for step in path.steps:
                for port in (step.in_port, step.out_port):
                    point = plan.points_by_cell_port[(step.mrr_id, port)]
                    assert point.owner_input is not None
                    assert point.side == port_side(port)
                    assert point.optical_role == port_optical_role(port)


def test_corridor_key_is_direction_agnostic() -> None:
    # The corridor key is the sorted x-range, so a left-to-right hop and the
    # reverse right-to-left hop over the same span share one slot. This is what
    # lets the (physical add-drop) right-to-left case reuse corridor management
    # instead of being skipped.
    assert _corridor_key((100.0, 0.0), (20.0, 5.0)) == _corridor_key((20.0, 9.0), (100.0, 3.0))


def test_corridor_assigns_a_slot_to_every_spanning_hop() -> None:
    # Every external hop with horizontal extent -- input bus, inter-stage, and
    # output bus, in either direction -- must receive a corridor slot. The old
    # code dropped hops with src[0] >= dst[0]; register() now keys on span only.
    rules = RoutingRules()
    for topology_cls in TOPOLOGIES:
        topology = topology_cls()
        cells = build_cells(topology, MOCK_S_TABLE)
        n_physical = topology.N_physical
        x_start = 20.0
        x_end = max(cell.center[0] for cell in cells.values()) + 70.0
        paths = topology.get_active_paths(PERMUTATION)
        slots = _corridor_preferred_x(
            paths, cells, rules, 2.0, x_start, x_end, 36.0, n_physical
        )
        for path in paths:
            if not path.steps:
                continue
            hops = [(
                (x_start, wire_y(path.input_port, n_physical)),
                _port_escape_point(cells[path.steps[0].mrr_id], path.steps[0].in_port, rules, 2.0),
            )]
            for first, second in zip(path.steps, path.steps[1:]):
                hops.append((
                    _port_escape_point(cells[first.mrr_id], first.out_port, rules, 2.0),
                    _port_escape_point(cells[second.mrr_id], second.in_port, rules, 2.0),
                ))
            hops.append((
                _port_escape_point(cells[path.steps[-1].mrr_id], path.steps[-1].out_port, rules, 2.0),
                (x_end, wire_y(path.output_port, n_physical)),
            ))
            for src, dst in hops:
                if abs(src[0] - dst[0]) <= 1e-6:
                    continue
                assert _corridor_key(src, dst) in slots.get(path.input_port, {})


def test_reserved_segments_are_owner_aware() -> None:
    # A net must be reserved out of every OTHER net's access region but never
    # blocked by its own.
    topology = WaksmanTopology()
    cells = build_cells(topology, MOCK_S_TABLE)
    paths = _active_paths(topology)
    plan = build_port_access_plan(paths, cells, RoutingRules(), port_stub_um=2.0)

    owners = {region.owner_input for region in plan.reserved_regions if region.owner_input is not None}
    assert owners, "expected some owned port-access regions"
    for owner in owners:
        own_segments = {
            region.segment for region in plan.reserved_regions if region.owner_input == owner
        }
        reserved = set(reserved_segments_for_net(plan, owner))
        assert own_segments.isdisjoint(reserved)
        other = {
            region.segment for region in plan.reserved_regions if region.owner_input != owner
        }
        assert reserved == other


def test_failed_related_inputs_include_blocker_owners() -> None:
    failed = (
        FailedNet(
            3,
            1,
            "blocked by port access (port_access_overlap_foreign); "
            "region=I0/padded_benes_8x8_s2_w0_4.in:(279,184)->(265,184)",
        ),
        FailedNet(
            1,
            0,
            "port escape violates spacing; "
            "blocked_by=I5/external:(115,209)->(168,209)",
        ),
        FailedNet(
            4,
            3,
            "blocked by crossing budget; "
            "blocked_by=I2/crossing_budget pair=I2-I4 at=(475,152)",
        ),
    )

    assert _failed_related_inputs(failed) == {0, 1, 2, 3, 4, 5}


def test_failed_reroute_priority_interleaves_self_consumed_budget_blockers() -> None:
    failed = (
        FailedNet(
            0,
            2,
            "blocked_by=I1/crossing_budget pair=I0-I1 at=(128.000,238.000) "
            "source=I0->I1@(40.000,238.000):(20.000,238.000)->(55.000,238.000)"
            "x(40.000,230.000)->(40.000,251.000)",
        ),
        FailedNet(
            2,
            5,
            "blocked_by=I3/crossing_budget pair=I2-I3 at=(136.000,166.000) "
            "source=I2->I3@(55.000,144.000):(55.000,158.000)->(55.000,140.000)"
            "x(20.000,144.000)->(115.000,144.000)",
        ),
    )
    order = sorted(_active_paths(PaddedBenesTopology()), key=lambda path: path.input_port)

    assert _failed_related_inputs(failed) == {0, 1, 2, 3}
    assert _failed_reroute_priority(failed, order) == (0, 1, 2, 3)


def test_source_only_crossing_provenance_feeds_reroute_island() -> None:
    failed = (
        FailedNet(
            4,
            3,
            "blocked by crossing budget "
            "source=I5->I1@(88.000,184.000):(80.000,184.000)->(96.000,184.000)"
            "x(88.000,176.000)->(88.000,192.000)",
        ),
    )
    order = sorted(_active_paths(PaddedBenesTopology()), key=lambda path: path.input_port)

    assert _failed_related_inputs(failed) == {1, 4, 5}
    assert _failed_reroute_priority(failed, order) == (4, 5, 1)


def test_reserved_owner_provenance_feeds_reroute_island() -> None:
    failed = (
        FailedNet(
            0,
            2,
            "source-reserved hop exhausted bounded candidates before A*: "
            "reserved_owners=I5: reserved-owner avoidance candidate segment is blocked; "
            "blocked_by=I4/external/spacing:(372.000,65.000)->(372.000,160.000)",
        ),
    )
    order = sorted(_active_paths(PaddedBenesTopology()), key=lambda path: path.input_port)

    assert _failed_related_inputs(failed) == {0, 4, 5}
    assert _failure_owner_components(failed) == (frozenset({0, 4, 5}),)
    assert _failed_reroute_priority(failed, order) == (0, 4, 5)


def test_failure_owner_components_group_source_and_blocker_owners() -> None:
    failed = (
        FailedNet(
            0,
            2,
            "blocked_by=I5/crossing_budget "
            "source=I0->I1@(40.000,238.000):(20.000,238.000)->(55.000,238.000)"
            "x(40.000,230.000)->(40.000,251.000)",
        ),
        FailedNet(
            2,
            5,
            "blocked_by=I3/crossing_budget",
        ),
    )

    assert _failure_owner_components(failed) == (
        frozenset({0, 1, 5}),
        frozenset({2, 3}),
    )


def test_bounded_reroute_inputs_keep_direct_failure_components_coherent() -> None:
    topology = PaddedBenesTopology()
    order = sorted(_active_paths(topology), key=lambda path: path.input_port)

    selected = _bounded_reroute_inputs(
        order,
        direct_failed_inputs={0, 2},
        related_inputs={0, 1, 2, 3, 5},
        budget=4,
        priority_inputs=(0, 5, 2, 3, 1),
        components=(frozenset({0, 1, 5}), frozenset({2, 3})),
    )

    assert selected == {0, 1, 2, 3, 5}


def test_crossing_budget_owners_only_extracts_ledger_blockers() -> None:
    message = (
        "blocked by port access; region=I5/cell.drop; "
        "blocked_by=I1/crossing_budget pair=I0-I1 at=(10,20); "
        "blocked_by=I3/external:(0,0)->(10,0)"
    )

    assert _crossing_budget_owners(message) == {1}


def test_crossing_source_points_extract_retry_forbidden_locations() -> None:
    message = (
        "blocked_by=I1/crossing_budget pair=I0-I1 at=(128.000,238.000) "
        "source=I0->I1@(40.000,238.000):(20.000,238.000)->(55.000,238.000)"
        "x(40.000,230.000)->(40.000,251.000)"
    )

    assert _crossing_source_points(message) == {(40.0, 238.0)}


def test_crossing_sources_keep_segment_provenance_for_local_detours() -> None:
    message = (
        "blocked_by=I3/crossing_budget pair=I2-I3 at=(136.000,166.000) "
        "source=I2->I3@(55.000,144.000):(55.000,158.000)->(55.000,140.000)"
        "x(20.000,144.000)->(115.000,144.000)"
    )

    source = _crossing_sources(message)[0]

    assert source.owner_input == 2
    assert source.crossed_input == 3
    assert source.point == (55.0, 144.0)
    assert source.source_segment == ((55.0, 158.0), (55.0, 140.0))
    assert source.crossed_segment == ((20.0, 144.0), (115.0, 144.0))


def test_source_detour_candidates_route_around_crossed_segment_endpoint() -> None:
    rules = RoutingRules(grid_pitch_um=8.0, bend_radius_um=5.0)
    horizontal = _crossing_sources(
        "source=I0->I1@(40.000,238.000):(20.000,238.000)->(55.000,238.000)"
        "x(40.000,230.000)->(40.000,251.000)"
    )
    vertical = _crossing_sources(
        "source=I2->I3@(55.000,144.000):(55.000,158.000)->(55.000,140.000)"
        "x(20.000,144.000)->(115.000,144.000)"
    )

    horizontal_candidates = _source_detour_candidates(
        (20.0, 238.0),
        (55.0, 238.0),
        set(horizontal),
        ((0.0, 20.0, 40.0, 55.0, 80.0), (210.0, 220.0, 230.0, 238.0, 251.0, 261.0)),
        rules,
        owner_input=0,
    )
    vertical_candidates = _source_detour_candidates(
        (55.0, 158.0),
        (55.0, 140.0),
        set(vertical),
        ((10.0, 20.0, 55.0, 115.0, 125.0), (140.0, 144.0, 158.0)),
        rules,
        owner_input=2,
    )
    off_l_shape_vertical_candidates = _source_detour_candidates(
        (55.0, 158.0),
        (220.0, 176.0),
        set(vertical),
        ((10.0, 20.0, 55.0, 115.0, 125.0, 220.0), (140.0, 144.0, 158.0, 176.0)),
        rules,
        owner_input=2,
    )
    unrelated_hop_candidates = _source_detour_candidates(
        (160.0, 238.0),
        (220.0, 238.0),
        set(horizontal),
        ((20.0, 40.0, 55.0, 160.0, 220.0), (220.0, 238.0, 261.0)),
        rules,
        owner_input=0,
    )

    assert horizontal_candidates
    assert vertical_candidates
    assert off_l_shape_vertical_candidates
    assert not unrelated_hop_candidates
    for candidate, source in (
        (horizontal_candidates[0], horizontal[0]),
        (vertical_candidates[0], vertical[0]),
        (off_l_shape_vertical_candidates[0], vertical[0]),
    ):
        segments = _axis_segments(candidate)
        assert all(not _point_on_segment(source.point, segment) for segment in segments)
        assert all(
            _orthogonal_crossing_point(segment, source.crossed_segment) is None
            for segment in segments
        )


def test_failed_source_points_become_history_cost_for_next_retry() -> None:
    failed = (
        FailedNet(
            0,
            2,
            "blocked_by=I1/crossing_budget pair=I0-I1 at=(128.000,238.000) "
            "source=I0->I1@(40.000,238.000):(20.000,238.000)->(55.000,238.000)"
            "x(40.000,230.000)->(40.000,251.000)",
        ),
    )
    forbidden: dict[int, set[tuple[float, float]]] = {}

    _record_failed_source_points(forbidden, failed)

    assert forbidden == {}

    history = HistoryCost()
    _bump_failed_source_history(
        history,
        set(_crossing_sources(failed[0].message)),
        _crossing_source_points(failed[0].message),
        ((20.0, 40.0, 55.0), (230.0, 238.0, 251.0)),
        RoutingRules(hairpin_penalty_um=200.0),
        attempt_idx=0,
    )

    assert history.penalties
    assert any(key[0] == 1 and key[1] == 1 for key in history.penalties)


def test_failed_crossing_sources_are_recorded_for_next_ripup_pass() -> None:
    failed = (
        FailedNet(
            2,
            5,
            "blocked_by=I3/crossing_budget pair=I2-I3 at=(136.000,166.000) "
            "source=I2->I3@(55.000,144.000):(55.000,158.000)->(55.000,140.000)"
            "x(20.000,144.000)->(115.000,144.000)",
        ),
    )
    sources_by_input = {}

    _record_failed_crossing_sources(sources_by_input, failed)

    source = next(iter(sources_by_input[2]))
    assert source.point == (55.0, 144.0)
    assert source.source_segment == ((55.0, 158.0), (55.0, 140.0))
    assert source.crossed_segment == ((20.0, 144.0), (115.0, 144.0))


def test_failed_crossing_sources_are_mirrored_for_crossed_owner() -> None:
    failed = (
        FailedNet(
            0,
            2,
            "blocked_by=I1/crossing_budget pair=I0-I1 at=(128.000,238.000) "
            "source=I0->I1@(40.000,238.000):(20.000,238.000)->(55.000,238.000)"
            "x(40.000,230.000)->(40.000,251.000)",
        ),
    )
    sources_by_input = {}

    _record_failed_crossing_sources(sources_by_input, failed)

    original = next(source for source in sources_by_input[0] if source.owner_input == 0)
    mirrored = next(source for source in sources_by_input[1] if source.owner_input == 1)
    assert original.crossed_input == 1
    assert mirrored.crossed_input == 0
    assert mirrored.source_segment == original.crossed_segment
    assert mirrored.crossed_segment == original.source_segment
    assert _source_reserved_crossing_owners(
        {mirrored},
        (40.0, 230.0),
        (40.0, 251.0),
        RoutingRules(grid_pitch_um=8.0, bend_radius_um=5.0),
        owner_input=1,
    ) == {0}


def test_historical_crossing_source_points_only_bind_fixed_blockers() -> None:
    sources = set(
        _crossing_sources(
            "blocked_by=I1/crossing_budget pair=I0-I1 at=(128.000,238.000) "
            "source=I0->I1@(40.000,238.000):(20.000,238.000)->(55.000,238.000)"
            "x(40.000,230.000)->(40.000,251.000)"
        )
    )
    forbidden = {(40.0, 238.0), (88.0, 88.0)}

    moving_blocker_sources = _active_crossing_sources_for_fixed_routes(
        sources,
        fixed_inputs=set(),
    )
    fixed_blocker_sources = _active_crossing_sources_for_fixed_routes(
        sources,
        fixed_inputs={1},
    )

    assert moving_blocker_sources == set()
    assert fixed_blocker_sources == sources
    assert _filtered_source_forbidden_points(
        forbidden,
        sources,
        moving_blocker_sources,
    ) == {(88.0, 88.0)}
    assert _filtered_source_forbidden_points(
        forbidden,
        sources,
        fixed_blocker_sources,
    ) == forbidden


def test_owner_crossing_sources_survive_moving_blocker_without_forbidden_point() -> None:
    sources = set(
        _crossing_sources(
            "blocked_by=I1/crossing_budget pair=I0-I1 at=(128.000,238.000) "
            "source=I0->I1@(40.000,238.000):(20.000,238.000)->(55.000,238.000)"
            "x(40.000,230.000)->(40.000,251.000)"
        )
    )
    forbidden = {(40.0, 238.0)}

    assert _active_crossing_sources_for_fixed_routes(sources, fixed_inputs=set()) == set()
    assert _owner_crossing_sources_for_current_net(sources, owner_input=0) == sources
    assert _filtered_source_forbidden_points(
        forbidden,
        sources,
        active_sources=set(),
    ) == set()


def test_source_reserved_crossing_owners_only_apply_to_matching_hop_and_owner() -> None:
    rules = RoutingRules(grid_pitch_um=8.0, bend_radius_um=5.0)
    sources = set(
        _crossing_sources(
            "blocked_by=I1/crossing_budget pair=I0-I1 at=(128.000,238.000) "
            "source=I0->I1@(40.000,238.000):(20.000,238.000)->(55.000,238.000)"
            "x(40.000,230.000)->(40.000,251.000)"
        )
    )

    assert _source_reserved_crossing_owners(
        sources,
        (20.0, 238.0),
        (55.0, 238.0),
        rules,
        owner_input=0,
    ) == {1}
    assert _source_reserved_crossing_owners(
        sources,
        (20.0, 238.0),
        (55.0, 238.0),
        rules,
        owner_input=2,
    ) == set()
    assert _source_reserved_crossing_owners(
        sources,
        (160.0, 238.0),
        (220.0, 238.0),
        rules,
        owner_input=0,
    ) == set()


def test_source_reserved_hop_routes_bounded_detour_without_pair_crossing() -> None:
    rules = RoutingRules(
        grid_pitch_um=5.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
    )
    blocker = ((10.0, -5.0), (10.0, 5.0))
    source = set(
        _crossing_sources(
            "blocked_by=I1/crossing_budget pair=I0-I1 at=(10.000,0.000) "
            "source=I0->I1@(10.000,0.000):(0.000,0.000)->(20.000,0.000)"
            "x(10.000,-5.000)->(10.000,5.000)"
        )
    )
    route = _route_external_hop(
        (0.0, 0.0),
        (20.0, 0.0),
        [blocker],
        [],
        (
            (0.0, 5.0, 10.0, 15.0, 20.0),
            (-15.0, -10.0, -5.0, 0.0, 5.0, 10.0, 15.0),
        ),
        rules,
        src_label="synthetic.src",
        dst_label="synthetic.dst",
        forbidden_points=set(),
        current_segments=[],
        occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
        crossing_count_by_pair={},
        crossing_sources_by_pair={},
        soft_blockers=[],
        reserved_spacing_blockers=[],
        reserved_guard_blockers=[],
        reserved_crossing_owners=set(),
        deferred_crossing_owners=set(),
        port_access=PortAccessLegality(
            PortAccessPlan(points_by_cell_port={}, reserved_regions=()),
            current_net_id=0,
        ),
        grid_bounds=RoutingWindow(left=0.0, right=20.0, bottom=-15.0, top=15.0),
        base_detour_tracks=1,
        source_crossings=source,
    )

    assert any(abs(point[1]) > 0.0 for point in route)
    assert all(
        _orthogonal_crossing_point(segment, blocker) is None
        for segment in _axis_segments(route)
    )


def test_bend_spaced_candidates_drop_too_tight_local_shapes() -> None:
    rules = RoutingRules(bend_radius_um=5.0)
    valid = (0.0, 0.0), (12.0, 0.0), (12.0, 12.0)
    too_tight = (0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (12.0, 4.0)

    assert _bend_spaced_candidates([too_tight, valid], rules) == [valid]


def test_source_reserved_hop_fails_fast_after_bounded_candidates() -> None:
    rules = RoutingRules(
        grid_pitch_um=5.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
        hard_source_crossing_reservation=True,
    )
    blocker = ((10.0, -5.0), (10.0, 5.0))
    source = set(
        _crossing_sources(
            "blocked_by=I1/crossing_budget pair=I0-I1 at=(10.000,0.000) "
            "source=I0->I1@(10.000,0.000):(0.000,0.000)->(20.000,0.000)"
            "x(10.000,-5.000)->(10.000,5.000)"
        )
    )

    with pytest.raises(
        RoutingError,
        match="source-reserved hop exhausted bounded candidates",
    ) as exc_info:
        _route_external_hop(
            (0.0, 0.0),
            (20.0, 0.0),
            [blocker],
            [],
            ((0.0, 10.0, 20.0), (0.0,)),
            rules,
            src_label="synthetic.src",
            dst_label="synthetic.dst",
            forbidden_points=set(),
            current_segments=[],
            occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
            crossing_count_by_pair={},
            crossing_sources_by_pair={},
            soft_blockers=[],
            reserved_spacing_blockers=[],
            reserved_guard_blockers=[],
            reserved_crossing_owners=set(),
            deferred_crossing_owners=set(),
            port_access=PortAccessLegality(
                PortAccessPlan(points_by_cell_port={}, reserved_regions=()),
                current_net_id=0,
            ),
            grid_bounds=RoutingWindow(left=0.0, right=20.0, bottom=0.0, top=0.0),
            base_detour_tracks=1,
            ripup_pass_idx=1,
            source_crossings=source,
        )

    failed = (FailedNet(0, 2, str(exc_info.value)),)
    assert _failed_related_inputs(failed) == {0, 1}
    assert _failure_owner_components(failed) == (frozenset({0, 1}),)


def test_source_reserved_hop_is_soft_by_default() -> None:
    rules = RoutingRules(
        grid_pitch_um=5.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
    )
    blocker = ((10.0, -5.0), (10.0, 5.0))
    source = set(
        _crossing_sources(
            "blocked_by=I1/crossing_budget pair=I0-I1 at=(10.000,0.000) "
            "source=I0->I1@(10.000,0.000):(0.000,0.000)->(20.000,0.000)"
            "x(10.000,-5.000)->(10.000,5.000)"
        )
    )

    route = _route_external_hop(
        (0.0, 0.0),
        (20.0, 0.0),
        [blocker],
        [],
        ((0.0, 10.0, 20.0), (0.0,)),
        rules,
        src_label="synthetic.src",
        dst_label="synthetic.dst",
        forbidden_points=set(),
        current_segments=[],
        occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
        crossing_count_by_pair={},
        crossing_sources_by_pair={},
        soft_blockers=[],
        reserved_spacing_blockers=[],
        reserved_guard_blockers=[],
        reserved_crossing_owners=set(),
        deferred_crossing_owners=set(),
        port_access=PortAccessLegality(
            PortAccessPlan(points_by_cell_port={}, reserved_regions=()),
            current_net_id=0,
        ),
        grid_bounds=RoutingWindow(left=0.0, right=20.0, bottom=0.0, top=0.0),
        base_detour_tracks=1,
        ripup_pass_idx=1,
        source_crossings=source,
    )

    assert route == ((0.0, 0.0), (20.0, 0.0))


def test_history_cost_reorders_bounded_candidates() -> None:
    history = HistoryCost()
    grid = ((0.0, 10.0, 20.0), (0.0, 10.0))
    straight = ((0.0, 0.0), (20.0, 0.0))
    detour = ((0.0, 0.0), (0.0, 10.0), (20.0, 10.0), (20.0, 0.0))

    assert _sort_candidates_by_history((straight, detour), history, grid)[0] == straight

    history.bump_route(_axis_segments(straight), grid[0], grid[1], amount=1000.0)

    assert _sort_candidates_by_history((straight, detour), history, grid)[0] == detour


def test_capped_ripup_hop_fails_fast_after_bounded_candidates() -> None:
    rules = RoutingRules(
        grid_pitch_um=5.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
        max_astar_pops=10,
        allow_crossings=False,
    )
    blocker = ((10.0, -5.0), (10.0, 5.0))

    with pytest.raises(RoutingError, match="capped rip-up hop exhausted bounded candidates"):
        _route_external_hop(
            (0.0, 0.0),
            (20.0, 0.0),
            [blocker],
            [],
            ((0.0, 10.0, 20.0), (0.0,)),
            rules,
            src_label="synthetic.src",
            dst_label="synthetic.dst",
            forbidden_points=set(),
            current_segments=[],
            occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
            crossing_count_by_pair={},
            crossing_sources_by_pair={},
            soft_blockers=[],
            reserved_spacing_blockers=[],
            reserved_guard_blockers=[],
            reserved_crossing_owners=set(),
            deferred_crossing_owners=set(),
            port_access=PortAccessLegality(
                PortAccessPlan(points_by_cell_port={}, reserved_regions=()),
                current_net_id=0,
            ),
            grid_bounds=RoutingWindow(left=0.0, right=20.0, bottom=0.0, top=0.0),
            base_detour_tracks=1,
            ripup_pass_idx=1,
        )


def test_capped_ripup_hop_preserves_source_hint_for_components() -> None:
    rules = RoutingRules(
        grid_pitch_um=5.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
        max_astar_pops=10,
        allow_crossings=False,
    )
    blocker = ((10.0, -5.0), (10.0, 5.0))
    source = set(
        _crossing_sources(
            "blocked_by=I1/crossing_budget pair=I0-I1 at=(100.000,100.000) "
            "source=I0->I1@(100.000,100.000):(90.000,100.000)->(110.000,100.000)"
            "x(100.000,90.000)->(100.000,110.000)"
        )
    )

    with pytest.raises(
        RoutingError,
        match="capped rip-up hop exhausted bounded candidates",
    ) as exc_info:
        _route_external_hop(
            (0.0, 0.0),
            (20.0, 0.0),
            [blocker],
            [],
            ((0.0, 10.0, 20.0), (0.0,)),
            rules,
            src_label="synthetic.src",
            dst_label="synthetic.dst",
            forbidden_points=set(),
            current_segments=[],
            occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
            crossing_count_by_pair={},
            crossing_sources_by_pair={},
            soft_blockers=[],
            reserved_spacing_blockers=[],
            reserved_guard_blockers=[],
            reserved_crossing_owners=set(),
            deferred_crossing_owners=set(),
            port_access=PortAccessLegality(
                PortAccessPlan(points_by_cell_port={}, reserved_regions=()),
                current_net_id=0,
            ),
            grid_bounds=RoutingWindow(left=0.0, right=20.0, bottom=0.0, top=0.0),
            base_detour_tracks=1,
            ripup_pass_idx=1,
            source_crossings=source,
        )

    failed = (FailedNet(0, 2, str(exc_info.value)),)
    assert _failed_related_inputs(failed) == {0, 1}
    assert _failure_owner_components(failed) == (frozenset({0, 1}),)


def test_deterministic_hop_crossing_budget_reports_blocker_owner() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
        max_astar_pops=10,
        route_window_max_detour_tracks=0,
        hard_crossing_budget=True,
    )
    blocker = ((10.0, -5.0), (10.0, 5.0))

    with pytest.raises(
        RoutingError,
        match="blocked_by=I1/crossing_budget",
    ) as exc_info:
        _route_external_hop(
            (0.0, 0.0),
            (20.0, 0.0),
            [blocker],
            [],
            ((0.0, 10.0, 20.0), (0.0,)),
            rules,
            src_label="synthetic.src",
            dst_label="synthetic.dst",
            forbidden_points=set(),
            current_segments=[],
            occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
            crossing_count_by_pair={(0, 1): 1},
            crossing_sources_by_pair={},
            soft_blockers=[],
            reserved_spacing_blockers=[],
            reserved_guard_blockers=[],
            reserved_crossing_owners=set(),
            deferred_crossing_owners=set(),
            port_access=PortAccessLegality(
                PortAccessPlan(points_by_cell_port={}, reserved_regions=()),
                current_net_id=0,
            ),
            grid_bounds=RoutingWindow(left=0.0, right=20.0, bottom=0.0, top=0.0),
            base_detour_tracks=0,
            ripup_pass_idx=1,
    )

    failed = (FailedNet(0, 2, str(exc_info.value)),)
    assert _failed_related_inputs(failed) == {0}
    assert _failure_owner_components(failed) == (frozenset({0}),)


def test_candidate_crossing_budget_source_does_not_force_blocker_component() -> None:
    message = (
        "deterministic hop candidate exceeds crossing budget; "
        "blocked_by=I5/crossing_budget pair=I0-I5 at=(10.000,0.000) "
        "source=I0->I5@(10.000,0.000):(0.000,0.000)->(20.000,0.000)"
        "x(10.000,-5.000)->(10.000,5.000); "
        "source=I0->I1@(40.000,238.000):(20.000,238.000)->(55.000,238.000)"
        "x(40.000,230.000)->(40.000,251.000)"
    )
    failed = (FailedNet(0, 2, message),)

    assert _failed_related_inputs(failed) == {0, 1, 5}
    assert _failure_owner_components(failed) == (frozenset({0, 1}),)


def test_bounded_reroute_inputs_keep_direct_failures_and_budget_blockers() -> None:
    topology = PaddedBenesTopology()
    order = sorted(_active_paths(topology), key=lambda path: path.input_port)

    selected = _bounded_reroute_inputs(
        order,
        direct_failed_inputs={3, 4, 5},
        related_inputs={1, 2, 3, 4, 5},
        budget=4,
    )

    assert selected == {1, 3, 4, 5}
    assert _bounded_reroute_inputs(
        order,
        direct_failed_inputs={3, 4, 5},
        related_inputs={1, 2, 3, 4, 5},
        budget=2,
    ) == {3, 4, 5}


def test_ripup_order_candidates_try_direct_failures_before_blockers() -> None:
    paths = sorted(_active_paths(PaddedBenesTopology()), key=lambda path: path.input_port)
    order = [
        path
        for input_port in (0, 5, 3, 1, 2)
        for path in paths
        if path.input_port == input_port
    ]

    candidates = _ripup_order_candidates(
        order,
        direct_failed_inputs={0, 2, 3},
        related_inputs={0, 1, 2, 3, 5},
        priority_inputs=(0, 5, 3, 1, 2),
        limit=3,
    )

    assert [path.input_port for path in candidates[0]] == [0, 5, 3, 1, 2]
    assert [path.input_port for path in candidates[1]] == [0, 2, 3, 1, 5]
    assert [path.input_port for path in candidates[2]] == [0, 2, 3, 5, 1]


def test_drc_violations_feed_ripup_owner_priority() -> None:
    violations = (
        DRCViolation("same_net_hairpin", "I4", "hairpin"),
        DRCViolation("touching_corner", "I1/I3", "touch"),
    )

    assert _violation_inputs(violations) == {1, 3, 4}
    assert _merge_priority_inputs((0, 5, 3), _violation_inputs(violations)) == (
        0,
        5,
        3,
        1,
        4,
    )


def test_failed_pass_candidate_score_penalizes_drc_violations() -> None:
    failed = (FailedNet(0, 2, "blocked by crossing budget"),)
    clean_score = _pass_candidate_score((), failed, ())
    dirty_score = _pass_candidate_score(
        (),
        failed,
        (DRCViolation("same_net_hairpin", "I3", "hairpin"),),
    )

    assert clean_score < dirty_score


def test_failed_reroute_priority_round_robins_blocker_owners() -> None:
    topology = PaddedBenesTopology()
    order = sorted(_active_paths(topology), key=lambda path: path.input_port)
    failed = (
        FailedNet(
            0,
            2,
            "blocked by port access; region=I5/cell.drop; "
            "blocked_by=I1/crossing_budget",
        ),
        FailedNet(
            2,
            5,
            "blocked by crossing budget; blocked_by=I3/crossing_budget",
        ),
    )

    priority = _failed_reroute_priority(failed, order)

    assert priority[:5] == (0, 5, 2, 3, 1)
    assert _bounded_reroute_inputs(
        order,
        direct_failed_inputs={0, 2},
        related_inputs={0, 1, 2, 3, 5},
        budget=4,
        priority_inputs=priority,
    ) == {0, 2, 3, 5}


def test_failed_reroute_priority_preserves_failure_occurrence_order() -> None:
    topology = PaddedBenesTopology()
    by_input = {path.input_port: path for path in _active_paths(topology)}
    complexity_order = [by_input[input_port] for input_port in (1, 5, 0, 4, 3, 2)]
    failed = (
        FailedNet(0, 2, "blocked_by=I4/crossing_budget"),
        FailedNet(3, 1, "A* pop limit exceeded"),
        FailedNet(1, 0, "blocked_by=I5/crossing_budget"),
        FailedNet(2, 5, "A* pop limit exceeded"),
    )

    assert _failed_reroute_priority(failed, complexity_order) == (0, 4, 3, 1, 5, 2)


def test_prioritized_reroute_order_uses_failed_priority_not_complexity_order() -> None:
    topology = PaddedBenesTopology()
    order = sorted(_active_paths(topology), key=lambda path: path.input_port)
    shuffled = [order[idx] for idx in (3, 1, 2, 0)]

    prioritized = _prioritized_reroute_order(shuffled, (0, 2, 1, 3))

    assert [path.input_port for path in prioritized] == [0, 2, 1, 3]


def test_deferred_crossing_owners_detect_future_access_corridor() -> None:
    plan, _region = _single_region_plan(owner=3)
    legality = PortAccessLegality(plan, current_net_id=7)

    assert _deferred_crossing_owners(
        [((-20.0, -10.0), (20.0, 10.0))],
        legality,
        current_owner=7,
    ) == {3}
    assert _deferred_crossing_owners(
        [((100.0, 100.0), (120.0, 120.0))],
        legality,
        current_owner=7,
    ) == set()
    assert _deferred_crossing_owners(
        [((-20.0, -10.0), (20.0, 10.0))],
        PortAccessLegality(plan, current_net_id=3),
        current_owner=3,
    ) == set()


def test_future_waveguide_crossing_owners_detect_forced_l_shape_crossers() -> None:
    occupied = [
        OccupiedRouteSegment(
            1,
            ((5.0, -5.0), (5.0, 15.0)),
            "external",
        ),
        OccupiedRouteSegment(
            2,
            ((2.0, 10.0), (8.0, 10.0)),
            "external",
        ),
        OccupiedRouteSegment(
            7,
            ((0.0, 20.0), (10.0, 20.0)),
            "external",
        ),
    ]

    # Future hop (0,0)->(10,10): both L-shapes cross I1's vertical segment.
    # Only the x-first L-shape crosses I2, so I2 is not a forced future owner.
    assert _future_waveguide_crossing_owners(
        [((0.0, 0.0), (10.0, 10.0))],
        occupied,
        current_owner=0,
    ) == {1}
    assert _future_waveguide_crossing_owners(
        [((0.0, 0.0), (10.0, 10.0))],
        occupied,
        current_owner=1,
    ) == set()


def _single_region_plan(owner: int | None) -> tuple[PortAccessPlan, PortAccessRegion]:
    # One left-side ("in") reserved corridor on the y=0 bus, running 14 um from
    # the stub at x=0 out to the escape at x=-14. Isolating a single region keeps
    # the legality assertions free of interference from neighbouring corridors.
    region = PortAccessRegion(
        owner_input=owner,
        segment=((0.0, 0.0), (-14.0, 0.0)),
        cell_id="cell",
        port="in",
        side="left",
        optical_role="input",
    )
    return PortAccessPlan(points_by_cell_port={}, reserved_regions=(region,)), region


def test_port_access_overlap_is_owner_aware_and_blocks_external_astar() -> None:
    # External A* may never run along a reserved corridor -- not even the owner's
    # own, which it reaches through the local stub/escape, not by routing the
    # corridor. The reason names whether the corridor is foreign or owned.
    rules = RoutingRules()
    owner_plan, region = _single_region_plan(owner=3)
    overlap = region.segment

    foreign = PortAccessLegality(owner_plan, current_net_id=7)
    assert port_access_conflict(overlap, foreign, rules) == "port_access_overlap_foreign"
    assert not _route_segment_available(overlap, [], [], rules, port_access=foreign)

    owner = PortAccessLegality(owner_plan, current_net_id=3)
    assert port_access_conflict(overlap, owner, rules) == "port_access_overlap_owner"
    assert not _route_segment_available(overlap, [], [], rules, port_access=owner)

    # No legality view -> no port-access opinion.
    assert port_access_conflict(overlap, None, rules) is None


def test_reserved_region_escalation_opens_only_foreign_outer_half() -> None:
    plan, _region = _single_region_plan(owner=3)
    foreign = PortAccessLegality(plan, current_net_id=7)
    owner = PortAccessLegality(plan, current_net_id=3)
    escalated = RoutingRules(
        allow_foreign_outer_runway_transit=True,
        strict_port_access=True,
    )

    outer_overlap = ((-14.0, 0.0), (-10.0, 0.0))
    inner_overlap = ((-6.0, 0.0), (0.0, 0.0))
    outer_crossing = ((-10.0, -10.0), (-10.0, 10.0))
    inner_crossing = ((-3.0, -10.0), (-3.0, 10.0))

    assert port_access_conflict(outer_overlap, foreign, escalated) is None
    assert port_access_conflict(outer_crossing, foreign, escalated) is None
    assert port_access_conflict(inner_overlap, foreign, escalated) == "port_access_overlap_foreign"
    assert port_access_conflict(inner_crossing, foreign, escalated) == "port_access_crossing_foreign"
    assert port_access_conflict(outer_overlap, owner, escalated) == "port_access_overlap_owner"


def test_reserved_region_stage_two_staggers_add_drop_runway_one_track() -> None:
    topology = WaksmanTopology()
    cell = next(iter(build_cells(topology, MOCK_S_TABLE).values()))
    base = RoutingRules(
        grid_pitch_um=8.0,
        legalize_port_access=True,
        port_access_stagger_tracks=0,
    )
    staggered = replace(base, port_access_stagger_tracks=1)

    for port in ("add", "drop"):
        base_point = _port_route_point(cell, port, base, 2.0)
        staggered_point = _port_route_point(cell, port, staggered, 2.0)
        assert abs(staggered_point[0] - base_point[0]) == 8.0

    for port in ("in", "th"):
        assert _port_route_point(cell, port, base, 2.0) == _port_route_point(
            cell,
            port,
            staggered,
            2.0,
        )


def test_strict_port_access_rejects_non_owner_t_touch_but_not_owner() -> None:
    # A vertical wire whose endpoint lands on the interior of the corridor is a
    # T-touch (end-block), not a crossing.
    plan, region = _single_region_plan(owner=3)
    touch = ((-7.0, -30.0), (-7.0, 0.0))
    foreign = PortAccessLegality(plan, current_net_id=7)
    owner = PortAccessLegality(plan, current_net_id=3)

    strict = RoutingRules(strict_port_access=True)
    assert port_access_conflict(touch, foreign, strict) == "port_access_touch_foreign"
    assert not _route_segment_available(touch, [], [], strict, port_access=foreign)
    # The owner may touch its own corridor; that is how it enters its port.
    assert port_access_conflict(touch, owner, strict) is None
    assert _route_segment_available(touch, [], [], strict, port_access=owner)

    # Default (non-strict) policy is behavior-preserving: the foreign touch is
    # allowed, so routes built under the default rules do not change.
    lax = RoutingRules(strict_port_access=False)
    assert port_access_conflict(touch, foreign, lax) is None
    assert _route_segment_available(touch, [], [], lax, port_access=foreign)


def test_strict_port_access_rejects_crossing_inside_region_only_when_strict() -> None:
    # A vertical wire passing through the corridor interior is a true crossing.
    plan, region = _single_region_plan(owner=3)
    crossing = ((-7.0, -30.0), (-7.0, 30.0))
    foreign = PortAccessLegality(plan, current_net_id=7)

    strict = RoutingRules(strict_port_access=True)
    assert port_access_conflict(crossing, foreign, strict) == "port_access_crossing_foreign"
    assert not _route_segment_available(crossing, [], [], strict, port_access=foreign)

    # With crossings globally allowed and strict mode off, crossing a reserved
    # region stays legal (the current default policy).
    lax = RoutingRules(strict_port_access=False, allow_crossings=True)
    assert port_access_conflict(crossing, foreign, lax) is None
    assert _route_segment_available(crossing, [], [], lax, port_access=foreign)


def test_future_port_access_spacing_reservation_blocks_only_lane_consumption() -> None:
    rules = RoutingRules(min_spacing_um=4.0)
    future_access = [((0.0, 0.0), (20.0, 0.0))]

    assert _reserved_spacing_conflict(((5.0, 0.0), (15.0, 0.0)), future_access, rules)
    assert _reserved_spacing_conflict(((5.0, 2.0), (15.0, 2.0)), future_access, rules)
    assert not _reserved_spacing_conflict(((10.0, -10.0), (10.0, 10.0)), future_access, rules)
    assert not _reserved_spacing_conflict(((20.0, -10.0), (20.0, 0.0)), future_access, rules)


def test_foreign_port_access_guard_blocks_escape_clearance_only() -> None:
    rules = RoutingRules(grid_pitch_um=10.0, bend_radius_um=5.0, min_spacing_um=4.0)
    plan, region = _single_region_plan(owner=3)

    guards = _foreign_port_access_guard_segments(plan, owner_input=7, rules=rules)

    assert guards == [((-14.0, 0.0), (-24.0, 0.0))]
    assert _foreign_port_access_guard_segments(plan, owner_input=3, rules=rules) == []
    assert _reserved_guard_conflict(((-22.0, 0.0), (-16.0, 0.0)), guards, rules)
    assert _reserved_guard_conflict(((-24.0, 2.0), (-14.0, 2.0)), guards, rules)
    assert not _reserved_guard_conflict(((-20.0, -10.0), (-20.0, 10.0)), guards, rules)
    assert not _reserved_guard_conflict(((-24.0, 0.0), (-24.0, 10.0)), guards, rules)
    assert not _reserved_guard_conflict(((-30.0, -10.0), (-30.0, 10.0)), guards, rules)


def test_foreign_port_access_guard_filters_inactive_reroute_owners() -> None:
    rules = RoutingRules(grid_pitch_um=10.0, bend_radius_um=5.0, min_spacing_um=4.0)
    plan, _region = _single_region_plan(owner=3)

    assert _foreign_port_access_guard_blockers(
        plan,
        owner_input=7,
        rules=rules,
        active_owner_inputs={2},
    ) == []
    guards = _foreign_port_access_guard_blockers(
        plan,
        owner_input=7,
        rules=rules,
        active_owner_inputs={3},
    )
    assert len(guards) == 1
    assert guards[0].owner_input == 3


def test_owned_port_access_guard_reports_blocker_owner() -> None:
    rules = RoutingRules(grid_pitch_um=10.0, bend_radius_um=5.0, min_spacing_um=4.0)
    guard = OccupiedRouteSegment(
        owner_input=5,
        segment=((-20.0, 0.0), (-30.0, 0.0)),
        kind="port_guard",
    )

    reason = _reserved_guard_conflict_reason(
        ((-25.0, 0.0), (-15.0, 0.0)),
        [guard],
        rules,
    )

    assert reason is not None
    assert "blocked_by=I5/port_guard" in reason
    assert _failed_related_inputs((FailedNet(0, 2, reason),)) == {0, 5}


def test_route_external_hop_preserves_owned_port_guard_in_bounded_failure() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
        max_astar_pops=10,
        route_window_max_detour_tracks=0,
    )
    guard = OccupiedRouteSegment(
        owner_input=5,
        segment=((10.0, -100.0), (10.0, 100.0)),
        kind="port_guard",
    )

    with pytest.raises(RoutingError, match="blocked_by=I5/port_guard") as exc_info:
        _route_external_hop(
            (0.0, 0.0),
            (20.0, 0.0),
            [],
            [],
            (
                (0.0, 10.0, 20.0),
                (-100.0, -50.0, 0.0, 50.0, 100.0),
            ),
            rules,
            src_label="synthetic.src",
            dst_label="synthetic.dst",
            forbidden_points=set(),
            current_segments=[],
            occupied_segments=[],
            crossing_count_by_pair={},
            crossing_sources_by_pair={},
            soft_blockers=[],
            reserved_spacing_blockers=[],
            reserved_guard_blockers=[guard],
            reserved_crossing_owners=set(),
            deferred_crossing_owners=set(),
            port_access=PortAccessLegality(
                PortAccessPlan(points_by_cell_port={}, reserved_regions=()),
                current_net_id=0,
            ),
            grid_bounds=RoutingWindow(left=0.0, right=20.0, bottom=-100.0, top=100.0),
            base_detour_tracks=0,
            ripup_pass_idx=1,
        )

    failed = (FailedNet(0, 2, str(exc_info.value)),)
    assert "deterministic hop candidate" in str(exc_info.value)
    assert _failed_related_inputs(failed) == {0, 5}
    assert _failure_owner_components(failed) == (frozenset({0, 5}),)


def test_progressive_detour_sequence_starts_with_small_windows() -> None:
    rules = RoutingRules(explicit_crossings=True, local_repair_max_shift_tracks=8)

    assert _detour_track_sequence(1, rules) == (1, 4)
    assert _detour_track_sequence(2, rules) == (2, 4)
    assert _detour_track_sequence(
        2,
        RoutingRules(
            explicit_crossings=True,
            local_repair_max_shift_tracks=8,
            route_window_max_detour_tracks=8,
        ),
    ) == (2, 4, 8)
    assert _detour_track_sequence(
        2,
        RoutingRules(explicit_crossings=True, local_repair_max_shift_tracks=2),
    ) == (2,)


def test_port_approach_point_uses_physical_port_side() -> None:
    rules = RoutingRules(grid_pitch_um=8.0, bend_radius_um=5.0)

    assert _port_approach_point("cell.in", (265.0, 184.0), rules) == (255.0, 184.0)
    assert _port_approach_point("cell.drop", (265.0, 176.0), rules) == (255.0, 176.0)
    assert _port_approach_point("cell.th", (325.0, 184.0), rules) == (335.0, 184.0)
    assert _port_approach_point("cell.add", (325.0, 176.0), rules) == (335.0, 176.0)
    assert _port_approach_point("O2", (500.0, 72.0), rules) is None


def test_external_hop_via_approach_ends_outside_reserved_port_lane() -> None:
    rules = RoutingRules(grid_pitch_um=10.0, min_spacing_um=4.0, turn_guard_um=0.0)
    owner_region = PortAccessRegion(
        owner_input=0,
        segment=((44.0, 0.0), (30.0, 0.0)),
        cell_id="cell",
        port="in",
        side="left",
        optical_role="input",
    )
    foreign_region = PortAccessRegion(
        owner_input=5,
        segment=((44.0, -8.0), (30.0, -8.0)),
        cell_id="cell",
        port="drop",
        side="left",
        optical_role="output",
    )
    port_access = PortAccessLegality(
        PortAccessPlan(points_by_cell_port={}, reserved_regions=(owner_region, foreign_region)),
        current_net_id=0,
    )

    route = _route_external_hop_via_approach(
        (0.0, -8.0),
        (20.0, 0.0),
        (30.0, 0.0),
        [],
        [],
        ((0.0, 10.0, 20.0, 30.0, 40.0), (-8.0, 0.0, 8.0)),
        rules,
        src_label="I0",
        dst_label="cell.in",
        forbidden_points=set(),
        current_segments=[],
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
        port_access=port_access,
        grid_bounds=RoutingWindow(left=0.0, right=40.0, bottom=-8.0, top=8.0),
        base_detour_tracks=1,
        ripup_pass_idx=0,
        history_cost=HistoryCost(),
    )

    assert route[-2:] == ((20.0, 0.0), (30.0, 0.0))
    final_segment = (route[-2], route[-1])
    assert not _segments_collinear_overlap(final_segment, owner_region.segment)
    assert not _segments_collinear_overlap(final_segment, foreign_region.segment)

    crossing_segment = ((25.0, -10.0), (25.0, 10.0))
    crossed_route = OccupiedRouteSegment(7, crossing_segment, "external")
    crossing_route = _route_external_hop_via_approach(
        (0.0, -8.0),
        (20.0, 0.0),
        (30.0, 0.0),
        [crossing_segment],
        [],
        ((0.0, 10.0, 20.0, 25.0, 30.0, 40.0), (-8.0, 0.0, 8.0)),
        rules,
        src_label="I0",
        dst_label="cell.in",
        forbidden_points=set(),
        current_segments=[],
        occupied_segments=[crossed_route],
        crossing_count_by_pair={},
        crossing_sources_by_pair={},
        soft_blockers=[],
        reserved_spacing_blockers=[],
        reserved_guard_blockers=[],
        reserved_crossing_owners=set(),
        deferred_crossing_owners=set(),
        preferred_bend_x=None,
        reserve_space_penalty=False,
        port_access=port_access,
        grid_bounds=RoutingWindow(left=0.0, right=40.0, bottom=-8.0, top=8.0),
        base_detour_tracks=1,
        ripup_pass_idx=0,
        history_cost=HistoryCost(),
    )
    assert crossing_route[-2:] == ((20.0, 0.0), (30.0, 0.0))

    hard_rules = RoutingRules(
        grid_pitch_um=10.0,
        min_spacing_um=4.0,
        turn_guard_um=0.0,
        hard_crossing_budget=True,
    )
    with pytest.raises(RoutingError, match="direct_port_approach_budget"):
        _route_external_hop_via_approach(
            (0.0, -8.0),
            (20.0, 0.0),
            (30.0, 0.0),
            [crossing_segment],
            [],
            ((0.0, 10.0, 20.0, 25.0, 30.0, 40.0), (-8.0, 0.0, 8.0)),
            hard_rules,
            src_label="I0",
            dst_label="cell.in",
            forbidden_points=set(),
            current_segments=[],
            occupied_segments=[crossed_route],
            crossing_count_by_pair={(0, 7): 1},
            crossing_sources_by_pair={(0, 7): ("seed",)},
            soft_blockers=[],
            reserved_spacing_blockers=[],
            reserved_guard_blockers=[],
            reserved_crossing_owners=set(),
            deferred_crossing_owners=set(),
            preferred_bend_x=None,
            reserve_space_penalty=False,
            port_access=port_access,
            grid_bounds=RoutingWindow(left=0.0, right=40.0, bottom=-8.0, top=8.0),
            base_detour_tracks=1,
            ripup_pass_idx=0,
            history_cost=HistoryCost(),
        )


def test_fallback_hop_specs_keep_base_detours_for_progressive_windows() -> None:
    topology = PaddedBenesTopology()
    cells = build_cells(topology, MOCK_S_TABLE)
    path = _active_paths(topology)[0]
    rules = RoutingRules(
        explicit_crossings=True,
        local_repair_max_shift_tracks=8,
        route_window_max_detour_tracks=8,
    )
    specs = _external_hop_specs(
        path,
        path.steps,
        cells,
        rules,
        n_physical=topology.N_physical,
        port_stub_um=2.0,
        x_start=20.0,
        x_end=max(cell.center[0] for cell in cells.values()) + 70.0,
        wire_pitch_um=36.0,
        corridor_slots={},
    )

    assert specs[0].base_detour_tracks == 1
    assert specs[-1].base_detour_tracks == 1
    assert {spec.base_detour_tracks for spec in specs[1:-1]} <= {2}
    assert _detour_track_sequence(specs[0].base_detour_tracks, rules) == (1, 4, 8)


def test_lax_routing_ignores_strict_port_access_flag() -> None:
    # Locks the preservation mode: a real plan's corridors never raise a
    # strict-only (touch/crossing) reason when strict access is explicitly off.
    rules = RoutingRules(strict_port_access=False)
    topology = WaksmanTopology()
    cells = build_cells(topology, MOCK_S_TABLE)
    paths = _active_paths(topology)
    plan = build_port_access_plan(paths, cells, rules, port_stub_um=2.0)
    owner = next(r.owner_input for r in plan.reserved_regions if r.owner_input is not None)
    legality = PortAccessLegality(plan, owner)
    for region in plan.reserved_regions:
        reason = port_access_conflict(region.segment, legality, rules)
        # Only overlap (always-on, redundant with the flat blockers) may fire.
        assert reason is None or reason.startswith("port_access_overlap_")


def test_route_grid_reports_crossed_waveguide_orientation() -> None:
    rules = RoutingRules()
    grid = RouteGrid((0.0, 20.0, 40.0), (-20.0, 0.0, 20.0))
    grid.mark_route(1, (((0.0, 0.0), (40.0, 0.0)),))

    hits = grid.segment_query(((20.0, -20.0), (20.0, 20.0)), rules)

    waveguide_hits = [hit for hit in hits if hit.kind == "waveguide"]
    assert len(waveguide_hits) == 1
    assert waveguide_hits[0].owner_input == 1
    assert waveguide_hits[0].orientation == "H"


def test_route_grid_owner_can_enter_own_port_access_only() -> None:
    plan, region = _single_region_plan(owner=3)
    grid = RouteGrid((-20.0, 0.0), (0.0,))
    grid.mark_port_access(region)

    assert grid.port_access_entry_allowed(region.segment, owner_input=3)
    assert not grid.port_access_entry_allowed(region.segment, owner_input=7)

    strict = RoutingRules(strict_port_access=True)
    foreign = PortAccessLegality(plan, current_net_id=7)
    assert grid.port_access_reason(region.segment, foreign, strict) == "port_access_overlap_foreign"


def test_explicit_crossing_candidate_rejects_endpoint_and_port_access() -> None:
    rules = RoutingRules()
    grid = RouteGrid((0.0, 20.0, 40.0), (-20.0, 0.0, 20.0))
    grid.mark_route(1, (((0.0, 0.0), (40.0, 0.0)),))

    legal = legal_crossing_candidate(
        ((20.0, -20.0), (20.0, 20.0)),
        grid,
        rules,
        owner_input=2,
    )
    assert legal is not None
    assert legal.location == (20.0, 0.0)
    assert legal.crossed_input == 1

    endpoint_touch = legal_crossing_candidate(
        ((0.0, -20.0), (0.0, 20.0)),
        grid,
        rules,
        owner_input=2,
    )
    assert endpoint_touch is None

    _plan, region = _single_region_plan(owner=3)
    grid.mark_port_access(region)
    blocked_by_access = legal_crossing_candidate(
        ((-7.0, -20.0), (-7.0, 20.0)),
        grid,
        rules,
        owner_input=2,
    )
    assert blocked_by_access is None


def test_crossing_candidate_relaxes_default_clearance_only() -> None:
    rules = RoutingRules()
    grid = RouteGrid((0.0, 4.0, 40.0), (-20.0, 0.0, 20.0))
    grid.mark_route(1, (((0.0, 0.0), (40.0, 0.0)),))

    relaxed = legal_crossing_candidate(
        ((4.0, -20.0), (4.0, 20.0)),
        grid,
        rules,
        owner_input=2,
    )
    strict = legal_crossing_candidate(
        ((4.0, -20.0), (4.0, 20.0)),
        grid,
        rules,
        owner_input=2,
        crossing_rule=CrossingRule(min_clearance_um=5.0),
    )

    assert relaxed is not None
    assert relaxed.location == (4.0, 0.0)
    assert strict is None


def test_crossing_candidate_uses_merged_arms_on_both_nets() -> None:
    rules = RoutingRules()
    grid = RouteGrid((0.0, 4.0, 10.0, 20.0), (-20.0, -4.0, 0.0, 4.0, 20.0))
    grid.mark_route(
        1,
        (
            ((0.0, 0.0), (4.0, 0.0)),
            ((4.0, 0.0), (20.0, 0.0)),
        ),
    )
    crossing_rule = CrossingRule(
        min_clearance_um=10.0,
        candidate_entry_point=(10.0, -20.0),
        defer_candidate_exit=True,
    )

    candidate = legal_crossing_candidate(
        ((10.0, -4.0), (10.0, 4.0)),
        grid,
        rules,
        owner_input=2,
        crossing_rule=crossing_rule,
        candidate_arm_segment=((10.0, -20.0), (10.0, 4.0)),
    )

    assert candidate is not None
    assert candidate.crossed_segment == ((0.0, 0.0), (20.0, 0.0))
    assert candidate.segment == ((10.0, -20.0), (10.0, 4.0))
    assert legal_crossing_candidate(
        ((10.0, -4.0), (10.0, 4.0)),
        grid,
        rules,
        owner_input=2,
        crossing_rule=replace(
            crossing_rule,
            candidate_entry_point=(10.0, -8.0),
        ),
        candidate_arm_segment=((10.0, -8.0), (10.0, 4.0)),
    ) is None


def test_crossing_footprint_permits_only_registered_through_arms() -> None:
    footprint = CrossingFootprint(
        location=(20.0, 20.0),
        side_um=10.0,
        horizontal_owner=1,
        vertical_owner=2,
    )

    assert not crossing_footprint_conflict(
        ((0.0, 20.0), (40.0, 20.0)), footprint, owner_input=1
    )
    assert not crossing_footprint_conflict(
        ((20.0, 0.0), (20.0, 40.0)), footprint, owner_input=2
    )
    assert crossing_footprint_conflict(
        ((0.0, 22.0), (40.0, 22.0)), footprint, owner_input=1
    )
    assert crossing_footprint_conflict(
        ((20.0, 0.0), (20.0, 40.0)), footprint, owner_input=3
    )
    assert crossing_footprint_conflict(
        ((0.0, 20.0), (20.0, 20.0)), footprint, owner_input=2
    )


def test_crossing_budget_rejects_second_crossing_between_same_pair() -> None:
    rules = RoutingRules()
    grid = RouteGrid((0.0, 20.0, 40.0), (-20.0, 0.0, 20.0))
    grid.mark_route(1, (((0.0, 0.0), (40.0, 0.0)),))
    candidate = legal_crossing_candidate(
        ((20.0, -20.0), (20.0, 20.0)),
        grid,
        rules,
        owner_input=2,
    )

    assert candidate is not None
    assert not crossing_budget_exceeded(candidate, {})
    assert crossing_budget_exceeded(candidate, {(1, 2): 1})


def test_crossing_candidate_rejects_same_net_crossing() -> None:
    rules = RoutingRules()
    grid = RouteGrid((0.0, 20.0, 40.0), (-20.0, 0.0, 20.0))
    grid.mark_route(2, (((0.0, 0.0), (40.0, 0.0)),))

    assert legal_crossing_candidate(
        ((20.0, -20.0), (20.0, 20.0)),
        grid,
        rules,
        owner_input=2,
    ) is None


def test_endpoint_crossing_candidate_counts_departure_only() -> None:
    rules = RoutingRules()
    grid = RouteGrid((0.0, 20.0, 40.0), (-20.0, 0.0, 20.0))
    grid.mark_route(1, (((0.0, 0.0), (40.0, 0.0)),))

    approach = endpoint_crossing_candidate(
        ((20.0, -20.0), (20.0, 0.0)),
        grid,
        rules,
        owner_input=2,
    )
    assert approach is None
    assert not endpoint_crossing_required(
        ((20.0, -20.0), (20.0, 0.0)),
        grid,
        rules,
        owner_input=2,
    )

    departure = endpoint_crossing_candidate(
        ((20.0, 0.0), (20.0, 20.0)),
        grid,
        rules,
        owner_input=2,
    )
    assert departure is not None
    assert departure.location == (20.0, 0.0)
    assert departure.crossed_input == 1
    assert endpoint_crossing_required(
        ((20.0, 0.0), (20.0, 20.0)),
        grid,
        rules,
        owner_input=2,
    )

    crossed_endpoint = endpoint_crossing_candidate(
        ((0.0, 0.0), (0.0, 20.0)),
        grid,
        rules,
        owner_input=2,
    )
    assert crossed_endpoint is None
    assert not endpoint_crossing_required(
        ((0.0, 0.0), (0.0, 20.0)),
        grid,
        rules,
        owner_input=2,
    )


def test_orientation_neighbors_reject_immediate_backtracking() -> None:
    state = RouterState(1, 1, "E")
    moves = neighbor_moves(state, (0.0, 10.0, 20.0), (0.0, 10.0, 20.0))

    assert {move.next_state.orientation for move in moves} == {"E", "N", "S"}
    assert all(move.next_state.orientation != "W" for move in moves)


def test_history_cost_can_penalize_a_track_state() -> None:
    history = HistoryCost()
    segment = ((0.0, 0.0), (20.0, 0.0))
    x_tracks = (0.0, 10.0, 20.0)
    y_tracks = (0.0, 10.0)

    history.bump_segment(segment, x_tracks, y_tracks)

    assert history.cost(RouterState(1, 0, "E")) > 0.0
    assert history.cost(RouterState(1, 1, "E")) == 0.0


def test_routing_is_deterministic() -> None:
    topology = WaksmanTopology()
    cells = build_cells(topology, MOCK_S_TABLE)
    paths = _active_paths(topology)
    first = route_physical_design(paths, cells, rules=RoutingRules())
    second = route_physical_design(paths, cells, rules=RoutingRules())
    assert [r.waypoints for r in first.routes] == [r.waypoints for r in second.routes]
    assert len(first.crossings) == len(second.crossings)


def test_astar_route_stays_inside_window_when_legal_route_exists() -> None:
    rules = RoutingRules(grid_pitch_um=10.0, min_spacing_um=0.0, turn_guard_um=0.0)
    grid = ((0.0, 10.0, 20.0, 30.0, 40.0), (-10.0, 0.0, 10.0))
    blockers = [((10.0, 0.0), (30.0, 0.0))]
    window = RoutingWindow(left=0.0, right=40.0, bottom=0.0, top=10.0)

    route = _astar_route(
        (0.0, 0.0),
        (40.0, 0.0),
        blockers,
        [],
        grid,
        rules,
        src_label="synthetic.src",
        dst_label="synthetic.dst",
        routing_window=window,
    )

    assert any(point[1] == 10.0 for point in route)
    assert all(window.contains_point(point) for point in route)
    assert all(window.contains_segment(segment) for segment in _axis_segments(route))


def test_route_window_expansion_recovers_from_mid_corridor_blockage() -> None:
    rules = RoutingRules(grid_pitch_um=10.0, min_spacing_um=0.0, turn_guard_um=0.0)
    grid = ((0.0, 10.0, 20.0, 30.0, 40.0), (-10.0, 0.0, 10.0))
    blockers = [((10.0, 0.0), (30.0, 0.0))]
    narrow = RoutingWindow(left=0.0, right=40.0, bottom=0.0, top=0.0)

    with pytest.raises(RoutingError, match="route window"):
        _astar_route(
            (0.0, 0.0),
            (40.0, 0.0),
            blockers,
            [],
            grid,
            rules,
            src_label="synthetic.src",
            dst_label="synthetic.dst",
            routing_window=narrow,
        )

    expanded = narrow.expanded(1, rules.grid_pitch_um)
    route = _astar_route(
        (0.0, 0.0),
        (40.0, 0.0),
        blockers,
        [],
        grid,
        rules,
        src_label="synthetic.src",
        dst_label="synthetic.dst",
        routing_window=expanded,
    )

    assert any(point[1] != 0.0 for point in route)
    assert all(expanded.contains_point(point) for point in route)


def test_reserved_crossing_owner_forces_astar_to_save_pair_budget() -> None:
    rules = RoutingRules(grid_pitch_um=10.0, min_spacing_um=0.0, turn_guard_um=0.0)
    blocker = ((10.0, -10.0), (10.0, 10.0))
    grid = ((0.0, 20.0), (0.0, 20.0))
    route = _astar_route(
        (0.0, 0.0),
        (20.0, 0.0),
        [blocker],
        [],
        grid,
        rules,
        src_label="synthetic.src",
        dst_label="synthetic.dst",
        occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
        reserved_crossing_owners={1},
        routing_window=RoutingWindow(left=0.0, right=20.0, bottom=0.0, top=20.0),
    )

    assert (0.0, 20.0) in route
    assert (20.0, 20.0) in route


def test_reserved_crossing_owner_is_soft_when_no_alternative() -> None:
    rules = RoutingRules(grid_pitch_um=10.0, min_spacing_um=0.0, turn_guard_um=0.0)
    blocker = ((10.0, -10.0), (10.0, 10.0))

    route = _astar_route(
        (0.0, 0.0),
        (20.0, 0.0),
        [blocker],
        [],
        ((0.0, 20.0), (0.0,)),
        rules,
        src_label="synthetic.src",
        dst_label="synthetic.dst",
        occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
        reserved_crossing_owners={1},
    )

    assert route == ((0.0, 0.0), (20.0, 0.0))


def test_hard_reserved_crossing_reservation_remains_diagnostic_mode() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
        hard_reserved_crossing_reservation=True,
    )
    blocker = ((10.0, -10.0), (10.0, 10.0))

    with pytest.raises(RoutingError, match="reserved_crossing"):
        _astar_route(
            (0.0, 0.0),
            (20.0, 0.0),
            [blocker],
            [],
            ((0.0, 20.0), (0.0,)),
            rules,
            src_label="synthetic.src",
            dst_label="synthetic.dst",
            occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
            reserved_crossing_owners={1},
        )


def test_hard_crossing_budget_failure_reports_consumed_pair_source() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
        hard_crossing_budget=True,
    )
    blocker = ((10.0, -10.0), (10.0, 10.0))
    port_access = PortAccessLegality(
        PortAccessPlan(points_by_cell_port={}, reserved_regions=()),
        current_net_id=2,
    )

    with pytest.raises(RoutingError, match="source=seed-crossing"):
        _astar_route(
            (0.0, 0.0),
            (20.0, 0.0),
            [blocker],
            [],
            ((0.0, 10.0, 20.0), (0.0,)),
            rules,
            src_label="synthetic.src",
            dst_label="synthetic.dst",
            occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
            crossing_count_by_pair={(1, 2): 1},
            crossing_sources_by_pair={(1, 2): ("seed-crossing",)},
            port_access=port_access,
        )


def test_repeated_crossing_soft_budget_routes_when_only_legal_option() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
        repeated_crossing_penalty_um=1000.0,
    )
    blocker = ((10.0, -5.0), (10.0, 5.0))
    port_access = PortAccessLegality(
        PortAccessPlan(points_by_cell_port={}, reserved_regions=()),
        current_net_id=0,
    )

    route = _astar_route(
        (0.0, 0.0),
        (20.0, 0.0),
        [blocker],
        [],
        ((0.0, 20.0), (0.0,)),
        rules,
        src_label="synthetic.src",
        dst_label="synthetic.dst",
        occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
        crossing_count_by_pair={(0, 1): 1},
        crossing_sources_by_pair={(0, 1): ("seed-crossing",)},
        port_access=port_access,
    )

    assert route == ((0.0, 0.0), (20.0, 0.0))


def test_high_repeated_crossing_penalty_avoids_second_crossing_when_possible() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        crossing_penalty_um=0.0,
        repeated_crossing_penalty_um=40.0,
        turn_guard_um=0.0,
    )
    blocker = ((10.0, -5.0), (10.0, 5.0))
    port_access = PortAccessLegality(
        PortAccessPlan(points_by_cell_port={}, reserved_regions=()),
        current_net_id=0,
    )

    route = _astar_route(
        (0.0, 0.0),
        (20.0, 0.0),
        [blocker],
        [],
        ((0.0, 20.0), (0.0, 10.0)),
        rules,
        src_label="synthetic.src",
        dst_label="synthetic.dst",
        occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
        crossing_count_by_pair={(0, 1): 1},
        crossing_sources_by_pair={(0, 1): ("seed-crossing",)},
        port_access=port_access,
        repeated_crossing_penalty_scale=1.0,
    )

    assert (0.0, 10.0) in route
    assert (20.0, 10.0) in route


def test_outward_repeated_crossing_scale_can_keep_wrap_hop_direct() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        crossing_penalty_um=0.0,
        repeated_crossing_penalty_um=40.0,
        outward_repeated_crossing_scale=0.25,
        turn_guard_um=0.0,
    )
    blocker = ((10.0, -5.0), (10.0, 5.0))
    port_access = PortAccessLegality(
        PortAccessPlan(points_by_cell_port={}, reserved_regions=()),
        current_net_id=0,
    )

    route = _astar_route(
        (0.0, 0.0),
        (20.0, 0.0),
        [blocker],
        [],
        ((0.0, 20.0), (0.0, 10.0)),
        rules,
        src_label="synthetic.src",
        dst_label="synthetic.dst",
        occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
        crossing_count_by_pair={(0, 1): 1},
        crossing_sources_by_pair={(0, 1): ("seed-crossing",)},
        port_access=port_access,
        repeated_crossing_penalty_scale=rules.outward_repeated_crossing_scale,
    )

    assert route == ((0.0, 0.0), (20.0, 0.0))


def test_loss_aware_cost_trades_crossing_against_bends() -> None:
    blocker = ((10.0, -5.0), (10.0, 5.0))
    grid = ((0.0, 20.0), (0.0, 10.0))
    port_access = PortAccessLegality(
        PortAccessPlan(points_by_cell_port={}, reserved_regions=()),
        current_net_id=0,
    )

    crossing_is_cheaper = RoutingRules(
        grid_pitch_um=10.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
        loss_aware_cost=True,
        prop_loss_db_per_um=0.0,
        bend_loss_db_per_bend=1.0,
        crossing_loss_db_per_cross=0.25,
    )
    direct = _astar_route(
        (0.0, 0.0),
        (20.0, 0.0),
        [blocker],
        [],
        grid,
        crossing_is_cheaper,
        src_label="synthetic.src",
        dst_label="synthetic.dst",
        occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
        port_access=port_access,
    )
    assert direct == ((0.0, 0.0), (20.0, 0.0))

    bends_are_cheaper = RoutingRules(
        grid_pitch_um=10.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
        loss_aware_cost=True,
        prop_loss_db_per_um=0.0,
        bend_loss_db_per_bend=1.0,
        crossing_loss_db_per_cross=3.0,
    )
    detour = _astar_route(
        (0.0, 0.0),
        (20.0, 0.0),
        [blocker],
        [],
        grid,
        bends_are_cheaper,
        src_label="synthetic.src",
        dst_label="synthetic.dst",
        occupied_segments=[OccupiedRouteSegment(1, blocker, "external")],
        port_access=port_access,
    )
    assert (0.0, 10.0) in detour
    assert (20.0, 10.0) in detour


def test_loss_aware_candidate_cost_penalizes_jogs() -> None:
    rules = RoutingRules(
        loss_aware_cost=True,
        prop_loss_db_per_um=0.0,
        bend_loss_db_per_bend=0.0,
        jog_penalty_db=2.0,
        turn_guard_um=0.0,
    )
    clean = ((0.0, 0.0), (30.0, 0.0), (30.0, 20.0))
    jog = (
        (0.0, 0.0),
        (10.0, 0.0),
        (10.0, 10.0),
        (20.0, 10.0),
        (20.0, 20.0),
        (30.0, 20.0),
    )

    assert _polyline_length(clean) == _polyline_length(jog)
    assert _candidate_route_cost(jog, [], None, rules) > _candidate_route_cost(
        clean,
        [],
        None,
        rules,
    )


def test_loss_aware_candidate_cost_penalizes_sensitive_bend_placement() -> None:
    obstacle = Obstacle("m0", left=18.0, right=22.0, bottom=-2.0, top=2.0)
    rules = RoutingRules(
        loss_aware_cost=True,
        prop_loss_db_per_um=0.0,
        bend_loss_db_per_bend=0.0,
        bend_placement_penalty_db=4.0,
        turn_guard_um=8.0,
    )
    sensitive = ((0.0, 0.0), (20.0, 0.0), (20.0, 20.0))
    clear = ((0.0, 0.0), (0.0, 20.0), (20.0, 20.0))

    assert _polyline_length(sensitive) == _polyline_length(clear)
    assert _candidate_route_cost(sensitive, [obstacle], None, rules) > _candidate_route_cost(
        clear,
        [obstacle],
        None,
        rules,
    )


def test_deterministic_delayed_l_moves_bends_out_of_sensitive_zones() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        bend_radius_um=2.0,
        loss_aware_cost=True,
        prop_loss_db_per_um=0.0,
        bend_loss_db_per_bend=0.0,
        bend_placement_penalty_db=5.0,
        turn_guard_um=6.0,
    )
    obstacles = [
        Obstacle("near_x_first", left=23.0, right=33.0, bottom=-5.0, top=5.0),
        Obstacle("near_y_first", left=-13.0, right=-3.0, bottom=15.0, top=25.0),
    ]
    x_first = ((0.0, 0.0), (20.0, 0.0), (20.0, 20.0))
    y_first = ((0.0, 0.0), (0.0, 20.0), (20.0, 20.0))

    candidates = _deterministic_hop_candidates(
        (0.0, 0.0),
        (20.0, 20.0),
        obstacles,
        rules,
        base_detour_tracks=1,
        grid=((0.0, 10.0, 20.0), (0.0, 10.0, 20.0)),
    )

    assert candidates
    assert candidates[0] not in {x_first, y_first}
    assert _candidate_route_cost(candidates[0], obstacles, None, rules) < min(
        _candidate_route_cost(x_first, obstacles, None, rules),
        _candidate_route_cost(y_first, obstacles, None, rules),
    )


def test_deterministic_mirror_l_is_accepted_only_when_legal() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        min_spacing_um=0.0,
        bend_radius_um=2.0,
        turn_guard_um=0.0,
        max_astar_pops=1,
    )
    obstacle = Obstacle("block_x_first", left=15.0, right=25.0, bottom=-5.0, top=5.0)
    route = _route_external_hop(
        (0.0, 0.0),
        (20.0, 20.0),
        [],
        [obstacle],
        ((0.0, 10.0, 20.0), (0.0, 10.0, 20.0)),
        rules,
        src_label="synthetic.src",
        dst_label="synthetic.dst",
        forbidden_points=set(),
        current_segments=[],
        occupied_segments=[],
        crossing_count_by_pair={},
        crossing_sources_by_pair={},
        soft_blockers=[],
        reserved_spacing_blockers=[],
        reserved_guard_blockers=[],
    )
    assert route == ((0.0, 0.0), (0.0, 20.0), (20.0, 20.0))

    with pytest.raises(RoutingError, match="segment is blocked"):
        _route_external_hop_via_waypoints(
            ((0.0, 0.0), (20.0, 0.0), (20.0, 20.0)),
            [],
            [obstacle],
            ((0.0, 10.0, 20.0), (0.0, 10.0, 20.0)),
            rules,
            src_label="synthetic.src",
            dst_label="synthetic.dst",
            forbidden_points=set(),
            current_segments=[],
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
            grid_bounds=RoutingWindow(left=0.0, right=20.0, bottom=0.0, top=20.0),
            base_detour_tracks=1,
            ripup_pass_idx=0,
            history_cost=HistoryCost(),
            candidate_label="deterministic hop candidate",
        )


def test_port_to_port_alternating_jog_shortcuts_to_legal_l() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        min_spacing_um=0.0,
        bend_radius_um=2.0,
        turn_guard_um=0.0,
    )
    jog = (
        (0.0, 0.0),
        (10.0, 0.0),
        (10.0, 10.0),
        (20.0, 10.0),
        (20.0, 20.0),
        (30.0, 20.0),
    )

    shortcut = _route_external_hop_via_waypoints(
        jog,
        [],
        [],
        ((0.0, 10.0, 20.0, 30.0), (0.0, 10.0, 20.0)),
        rules,
        src_label="m0.th",
        dst_label="m1.in",
        forbidden_points=set(),
        current_segments=[],
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
        grid_bounds=RoutingWindow(left=0.0, right=30.0, bottom=0.0, top=20.0),
        base_detour_tracks=1,
        ripup_pass_idx=0,
        history_cost=HistoryCost(),
        candidate_label="deterministic hop candidate",
    )

    assert shortcut == ((0.0, 0.0), (30.0, 0.0), (30.0, 20.0))
    assert _bend_count(shortcut) == 1


def test_alternating_jog_shortcut_is_limited_to_port_to_port_hops() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        min_spacing_um=0.0,
        bend_radius_um=2.0,
        turn_guard_um=0.0,
    )
    jog = (
        (0.0, 0.0),
        (10.0, 0.0),
        (10.0, 10.0),
        (20.0, 10.0),
        (20.0, 20.0),
        (30.0, 20.0),
    )

    unchanged = _route_external_hop_via_waypoints(
        jog,
        [],
        [],
        ((0.0, 10.0, 20.0, 30.0), (0.0, 10.0, 20.0)),
        rules,
        src_label="I0",
        dst_label="m1.in",
        forbidden_points=set(),
        current_segments=[],
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
        grid_bounds=RoutingWindow(left=0.0, right=30.0, bottom=0.0, top=20.0),
        base_detour_tracks=1,
        ripup_pass_idx=0,
        history_cost=HistoryCost(),
        candidate_label="deterministic hop candidate",
    )

    assert unchanged == jog
    assert _bend_count(unchanged) == 4


def test_sparsified_routing_grid_uses_escape_tracks_and_merges_near_duplicates() -> None:
    topology = PaddedBenesTopology()
    cells = build_cells(topology, MOCK_S_TABLE)
    paths = _active_paths(topology)
    rules = RoutingRules()
    obstacles = [
        _inflate_obstacle(_routing_obstacle(cell), rules.mrr_keepout_um)
        for cell in cells.values()
    ]

    grid = _routing_grid(
        paths,
        cells,
        obstacles,
        rules,
        x_start=20.0,
        x_end=max(cell.center[0] for cell in cells.values()) + 70.0,
        wire_pitch_um=36.0,
        port_stub_um=2.0,
    )
    x_tracks, y_tracks = grid

    assert len(x_tracks) * len(y_tracks) < 12726 / 2
    assert all(
        upper - lower >= rules.grid_merge_tol_um - 1e-6
        for lower, upper in zip(x_tracks, x_tracks[1:])
    )
    assert all(
        upper - lower >= rules.grid_merge_tol_um - 1e-6
        for lower, upper in zip(y_tracks, y_tracks[1:])
    )

    first_step = paths[0].steps[0]
    escape = _port_escape_point(cells[first_step.mrr_id], first_step.in_port, rules, 2.0)
    assert escape[0] in _insert_track(x_tracks, escape[0])
    assert escape[1] in _insert_track(y_tracks, escape[1])

    exact_x = escape[0] + 0.375
    exact_y = escape[1] + 0.625
    assert exact_x in _insert_track(x_tracks, exact_x)
    assert exact_y in _insert_track(y_tracks, exact_y)


def test_grid_merge_tolerance_keeps_insert_track_exact() -> None:
    tracks = _tracks_between(
        0.0,
        24.0,
        8.0,
        [8.5, 9.25],
        merge_tol_um=3.5,
    )

    assert 8.5 in tracks
    assert 9.25 not in tracks
    assert 9.25 in _insert_track(tracks, 9.25)


def test_io_clamped_routing_bounds_keep_routes_between_input_and_output() -> None:
    grid = ((-16.0, 0.0, 20.0, 100.0, 575.0, 600.0), (0.0, 40.0, 80.0))

    bounds = _io_clamped_routing_bounds(grid, 20.0, 575.0)

    assert bounds.left == pytest.approx(20.0)
    assert bounds.right == pytest.approx(575.0)
    assert bounds.contains_segment(((20.0, 40.0), (575.0, 40.0)))
    assert not bounds.contains_segment(((0.0, 40.0), (100.0, 40.0)))
    assert not bounds.contains_segment(((500.0, 40.0), (600.0, 40.0)))


def test_physical_routes_are_manhattan_and_externally_non_overlapping() -> None:
    for topology_cls in TOPOLOGIES:
        topology = topology_cls()
        cells = build_cells(
            topology,
            MOCK_S_TABLE,
            cell_geometry=LEGACY_V2_CELL_GEOMETRY,
        )
        paths = _active_paths(topology)
        routes = route_physical_paths(
            paths,
            cells,
            waveguide_width_um=LEGACY_V2_CELL_GEOMETRY.waveguide_width_um,
        )

        assert len(routes) == topology.N_logical
        external_segments = []
        non_external_segments = _internal_segment_keys(paths, cells) | _stub_segment_keys(cells)
        obstacles = _routing_obstacles(cells)
        for route in routes:
            for segment in _axis_segments(route.waypoints):
                if _segment_key(segment) not in non_external_segments:
                    external_segments.append((route.input_port, segment))

        for idx, (path_a, segment_a) in enumerate(external_segments):
            assert not any(
                _segment_intersects_obstacle(segment_a, obstacle)
                for obstacle in obstacles
            )
            for path_b, segment_b in external_segments[idx + 1 :]:
                if path_a == path_b:
                    continue
                assert not _segments_collinear_overlap(segment_a, segment_b)


def test_physical_routes_include_two_um_port_stubs() -> None:
    for topology_cls in TOPOLOGIES:
        topology = topology_cls()
        cells = build_cells(
            topology,
            MOCK_S_TABLE,
            cell_geometry=LEGACY_V2_CELL_GEOMETRY,
        )
        paths = _active_paths(topology)
        routes = route_physical_paths(
            paths,
            cells,
            port_stub_um=2.0,
            waveguide_width_um=LEGACY_V2_CELL_GEOMETRY.waveguide_width_um,
        )
        route_by_input = {route.input_port: route for route in routes}

        for path in paths:
            route_points = set(route_by_input[path.input_port].waypoints)
            for step in path.steps:
                cell = cells[step.mrr_id]
                assert _port_stub_point(cell, step.in_port, 2.0) in route_points
                assert _port_stub_point(cell, step.out_port, 2.0) in route_points


def test_physical_design_default_rules_has_no_hard_drc() -> None:
    for topology_cls in TOPOLOGIES:
        result = _default_physical_design(topology_cls)
        topology = topology_cls()

        assert len(result.routes) == topology.N_logical
        assert not result.failed_nets
        assert not result.drc_violations
        assert result.crossings
        for route in result.routes:
            for segment in (*route.external_segments, *route.local_segments):
                assert _segment_is_manhattan(segment)


def test_corner_touching_is_reported_as_drc() -> None:
    # Verify the DRC rule fires on synthetic routes with a known T-junction:
    # horizontal net I0 passes through (50, 0) while vertical net I1 ends there.
    routes = (
        PhysicalRoute(
            input_port=0,
            output_port=0,
            waypoints=((0.0, 0.0), (100.0, 0.0)),
            length_um=100.0,
            bend_count=0,
            external_segments=(((0.0, 0.0), (100.0, 0.0)),),
            local_segments=(),
        ),
        PhysicalRoute(
            input_port=1,
            output_port=1,
            waypoints=((50.0, -50.0), (50.0, 0.0)),
            length_um=50.0,
            bend_count=0,
            external_segments=(((50.0, -50.0), (50.0, 0.0)),),
            local_segments=(),
        ),
    )
    violations = validate_physical_routes(routes, {}, RoutingRules())
    assert any(v.rule == "touching_corner" for v in violations)


def test_default_refinement_removes_corner_touching() -> None:
    # Current deterministic routing baselines: this test primarily guards that
    # default refinement removes DRC corner-touching while keeping crossings
    # bounded and reproducible for each topology.
    # Limits include inter-net crossings through local (port-access) segments;
    # counting only external x external undercounts real crossings (PB 39,
    # WK 40, SB 27 under the old counting).
    crossing_limits = {
        PaddedBenesTopology: 43,
        WaksmanTopology: 45,
        SpankeBenesTopology: 27,
    }
    for topology_cls, crossing_limit in crossing_limits.items():
        result = _default_physical_design(topology_cls)

        assert not result.failed_nets
        assert not result.drc_violations
        assert len(result.crossings) <= crossing_limit


def test_waksman_i4_has_no_same_net_knot() -> None:
    result = _default_physical_design(WaksmanTopology)
    i4_rules = {
        violation.rule
        for violation in result.drc_violations
        if violation.net_id == "I4"
    }

    assert not result.failed_nets
    assert "same_net_touching_corner" not in i4_rules
    assert "same_net_hairpin" not in i4_rules


def test_physical_routes_include_port_escape_points() -> None:
    rules = RoutingRules(port_escape_um=10.0)
    topology = WaksmanTopology()
    cells = build_cells(topology, MOCK_S_TABLE)
    paths = _active_paths(topology)
    result = _default_physical_design(WaksmanTopology)
    route_by_input = {route.input_port: route for route in result.routes}

    for path in paths:
        route_points = set(route_by_input[path.input_port].waypoints)
        for step in path.steps:
            cell = cells[step.mrr_id]
            assert _port_escape_point(cell, step.in_port, rules, 2.0) in route_points
            assert _port_escape_point(cell, step.out_port, rules, 2.0) in route_points


def test_forced_congestion_returns_clean_failure() -> None:
    topology = WaksmanTopology()
    cells = build_cells(topology, MOCK_S_TABLE)
    rules = RoutingRules(min_spacing_um=1000.0, max_ripup_passes=1)

    result = route_physical_design(_active_paths(topology), cells, rules=rules)

    assert result.failed_nets
    assert all(failed.message for failed in result.failed_nets)


def test_routing_visualization_smoke(tmp_path) -> None:
    topology = WaksmanTopology()
    states = topology.get_state_assignment(PERMUTATION)
    out_path = tmp_path / "waksman_physical_route.png"

    save_routing_png(topology, PERMUTATION, states, MOCK_S_TABLE, out_path)

    assert out_path.exists()
    assert out_path.stat().st_size > 0


def test_physical_eval_cli_writes_reports(tmp_path) -> None:
    outdir = tmp_path / "physical_eval"

    subprocess.run(
        [
            sys.executable,
            "main.py",
            "--topology",
            "waksman",
            "--permutation",
            "2,0,5,1,3,4",
            "--physical-eval",
            "--outdir",
            str(outdir),
        ],
        cwd="/home/jchuang/Optical_switch",
        check=True,
    )

    for filename in (
        "physical_path_distribution.csv",
        "physical_summary.csv",
        "drc_violations.csv",
    ):
        path = outdir / filename
        assert path.exists()
        assert path.stat().st_size > 0


def test_physical_eval_includes_bend_loss_in_insertion_loss() -> None:
    rules = RoutingRules(bend_loss_db_per_bend=0.125)
    route = PhysicalRoute(
        input_port=0,
        output_port=1,
        waypoints=((0.0, 0.0), (10.0, 0.0), (10.0, 10.0)),
        length_um=42.0,
        bend_count=3,
        crossing_count=2,
    )

    physical_il = _physical_route_il_db(
        1.5,
        route,
        rules,
        alpha_db_per_um=ALPHA_DB_PER_UM,
        crossing_loss_db_per_cross=0.25,
    )

    assert physical_il == pytest.approx(
        1.5
        + ALPHA_DB_PER_UM * 42.0
        + 3 * rules.bend_loss_db_per_bend
        + 2 * 0.25
    )


def _active_paths(topology: RNBTopology):
    states = topology.get_state_assignment(PERMUTATION)
    return topology.get_active_paths(PERMUTATION, states)


@lru_cache(maxsize=None)
def _default_physical_design(
    topology_cls: type[RNBTopology],
) -> PhysicalRoutingResult:
    topology = topology_cls()
    cells = build_cells(topology, MOCK_S_TABLE)
    return route_physical_design(_active_paths(topology), cells, rules=RoutingRules())


def _internal_segment_keys(paths, cells) -> set[tuple[tuple[float, float], tuple[float, float]]]:
    keys = set()
    for path in paths:
        for step in path.steps:
            points = _mrr_internal_points(cells[step.mrr_id], step.in_port, step.out_port)
            keys.update(_segment_key(segment) for segment in _axis_segments(points))
    return keys


def _stub_segment_keys(cells) -> set[tuple[tuple[float, float], tuple[float, float]]]:
    return {
        _segment_key(_port_stub_segment(cell, port, 2.0))
        for cell in cells.values()
        for port in cell.ports
    }


def _segment_key(segment):
    start, end = segment
    rounded = (
        (round(start[0], 6), round(start[1], 6)),
        (round(end[0], 6), round(end[1], 6)),
    )
    return tuple(sorted(rounded))


def _segment_is_manhattan(segment) -> bool:
    (x1, y1), (x2, y2) = segment
    return abs(x1 - x2) < 1e-6 or abs(y1 - y2) < 1e-6


# ── Same-net knot prevention (A* candidate legality) ──────────────────────────

def test_same_net_self_crossing_candidate_is_rejected() -> None:
    own = [((0.0, 0.0), (0.0, 10.0)), ((0.0, 10.0), (10.0, 10.0))]
    # crosses the first vertical segment at its interior point (0, 5)
    candidate = ((-5.0, 5.0), (5.0, 5.0))
    assert _same_net_self_conflict(candidate, own)
    reason = _same_net_self_conflict_reason(candidate, own)
    assert reason is not None
    assert "same_net_crossing" in reason
    assert "prior=(0.000,0.000)->(0.000,10.000)" in reason


def test_same_net_hairpin_candidate_is_detected_with_rules() -> None:
    rules = RoutingRules(grid_pitch_um=8.0, min_spacing_um=4.0, bend_radius_um=5.0)
    own = [
        ((0.0, 0.0), (20.0, 0.0)),
        ((20.0, 0.0), (20.0, 10.0)),
    ]
    candidate = ((20.0, 10.0), (0.0, 10.0))

    reason = _same_net_self_conflict_reason(candidate, own, rules=rules)

    assert reason is not None
    assert "same_net_hairpin" in reason


def test_same_net_hairpin_is_soft_in_hop_validation() -> None:
    rules = RoutingRules(grid_pitch_um=10.0, min_spacing_um=0.0, bend_radius_um=5.0)
    route = _route_external_hop_via_waypoints(
        ((20.0, 10.0), (0.0, 10.0)),
        [],
        [],
        ((0.0, 10.0, 20.0), (0.0, 10.0)),
        rules,
        src_label="m0.th",
        dst_label="m1.in",
        forbidden_points=set(),
        current_segments=[],
        current_external_segments=[
            ((0.0, 0.0), (20.0, 0.0)),
            ((20.0, 0.0), (20.0, 10.0)),
        ],
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
        grid_bounds=RoutingWindow(left=0.0, right=20.0, bottom=0.0, top=10.0),
        base_detour_tracks=1,
        ripup_pass_idx=0,
        history_cost=HistoryCost(),
        candidate_label="hairpin-soft candidate",
    )

    assert route == ((20.0, 10.0), (0.0, 10.0))


def test_same_net_avoidance_candidates_detour_around_committed_segment() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
    )
    committed = [((10.0, 0.0), (10.0, 20.0))]

    candidates = _same_net_avoidance_hop_candidates(
        (0.0, 10.0),
        (20.0, 10.0),
        committed,
        [],
        (
            (0.0, 10.0, 20.0),
            (-20.0, -10.0, 0.0, 10.0, 20.0, 30.0),
        ),
        rules,
        base_detour_tracks=0,
    )

    assert candidates
    assert all(
        _orthogonal_crossing_point(segment, committed[0]) is None
        for candidate in candidates
        for segment in _axis_segments(candidate)
    )


def test_same_net_avoidance_candidates_detour_around_endpoint_touch() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
    )
    committed = [((20.0, 0.0), (20.0, 10.0))]

    candidates = _same_net_avoidance_hop_candidates(
        (0.0, 10.0),
        (40.0, 0.0),
        committed,
        [],
        (
            (0.0, 20.0, 40.0),
            (-10.0, 0.0, 10.0, 20.0, 30.0),
        ),
        rules,
        base_detour_tracks=0,
    )

    assert candidates
    assert all(
        not _same_net_self_conflict(segment, committed, join_points=((0.0, 10.0), (40.0, 0.0)))
        for candidate in candidates
        for segment in _axis_segments(candidate)
    )


def test_same_net_avoidance_candidates_avoid_reserved_guard_segments() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
    )
    committed = [((20.0, 0.0), (20.0, 10.0))]
    guard = ((30.0, -10.0), (30.0, 30.0))

    candidates = _same_net_avoidance_hop_candidates(
        (0.0, 10.0),
        (40.0, 0.0),
        committed,
        [],
        (
            (0.0, 10.0, 20.0, 30.0, 40.0),
            (-20.0, -10.0, 0.0, 10.0, 20.0, 30.0),
        ),
        rules,
        base_detour_tracks=0,
        avoid_segments=(guard,),
    )

    assert candidates
    assert all(
        _orthogonal_crossing_point(segment, guard) is None
        and _segment_contact_point(segment, guard) is None
        for candidate in candidates
        for segment in _axis_segments(candidate)
    )


def test_reserved_owner_avoidance_candidates_detour_around_reserved_crossing() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
    )
    reserved = OccupiedRouteSegment(
        owner_input=1,
        segment=((0.0, 10.0), (30.0, 10.0)),
        kind="external",
    )

    candidates = _reserved_owner_avoidance_hop_candidates(
        (10.0, 0.0),
        (10.0, 30.0),
        [reserved],
        {1},
        set(),
        [],
        (
            (-20.0, -10.0, 0.0, 10.0, 20.0, 30.0, 40.0),
            (0.0, 10.0, 20.0, 30.0),
        ),
        rules,
        base_detour_tracks=0,
    )

    assert candidates
    assert all(
        _orthogonal_crossing_point(segment, reserved.segment) is None
        for candidate in candidates
        for segment in _axis_segments(candidate)
    )


def test_reserved_owner_avoidance_candidates_add_envelope_escape() -> None:
    rules = RoutingRules(
        grid_pitch_um=10.0,
        bend_radius_um=2.0,
        min_spacing_um=0.0,
        turn_guard_um=0.0,
    )
    reserved = [
        OccupiedRouteSegment(1, ((0.0, 10.0), (30.0, 10.0)), "external"),
        OccupiedRouteSegment(1, ((30.0, 10.0), (30.0, 30.0)), "external"),
    ]

    candidates = _reserved_owner_avoidance_hop_candidates(
        (10.0, 0.0),
        (40.0, 30.0),
        reserved,
        {1},
        set(),
        [],
        (
            (-20.0, -10.0, 0.0, 10.0, 20.0, 30.0, 40.0, 50.0),
            (-20.0, -10.0, 0.0, 10.0, 20.0, 30.0, 40.0),
        ),
        rules,
        base_detour_tracks=0,
    )

    assert candidates
    assert any(any(point[1] <= -10.0 for point in candidate) for candidate in candidates)


def test_source_detour_alt_values_try_both_sides_of_crossed_segment() -> None:
    values = _source_detour_alt_values(
        (56.0, 64.0, 66.0, 72.0, 80.0, 88.0, 200.0, 208.0, 216.0, 224.0),
        197.0,
        76.0,
        181.0,
        10.0,
    )

    assert any(value <= 66.0 for value in values)
    assert any(value >= 207.0 for value in values)


def test_route_external_forbidden_points_ignore_collinear_split_points() -> None:
    route = PhysicalRoute(
        input_port=0,
        output_port=0,
        waypoints=(),
        length_um=0.0,
        bend_count=0,
        external_segments=(
            ((0.0, 0.0), (10.0, 0.0)),
            ((10.0, 0.0), (20.0, 0.0)),
            ((20.0, 0.0), (20.0, 10.0)),
            ((40.0, 0.0), (50.0, 0.0)),
        ),
    )

    forbidden = _route_external_forbidden_points(route)

    assert (10.0, 0.0) not in forbidden
    assert (20.0, 0.0) in forbidden
    assert (0.0, 0.0) in forbidden
    assert (20.0, 10.0) in forbidden
    assert (40.0, 0.0) in forbidden
    assert (50.0, 0.0) in forbidden


def test_same_net_self_t_touch_candidate_is_rejected() -> None:
    own = [((0.0, 0.0), (0.0, 10.0)), ((0.0, 10.0), (10.0, 10.0))]
    # candidate ends on the interior of the horizontal segment -> T-junction
    candidate = ((5.0, 0.0), (5.0, 10.0))
    assert _same_net_self_conflict(candidate, own)


def test_same_net_self_overlap_candidate_is_rejected() -> None:
    own = [((0.0, 0.0), (10.0, 0.0))]
    # runs back on top of the predecessor (collinear overlap) -> illegal
    candidate = ((10.0, 0.0), (3.0, 0.0))
    assert _same_net_self_conflict(candidate, own)


def test_predecessor_endpoint_continuation_is_allowed() -> None:
    own = [((0.0, 0.0), (10.0, 0.0))]
    # bends off the predecessor at the shared endpoint -> legal
    assert not _same_net_self_conflict(((10.0, 0.0), (10.0, 10.0)), own)
    # straight continuation past the predecessor endpoint -> legal
    assert not _same_net_self_conflict(((10.0, 0.0), (20.0, 0.0)), own)


def test_contact_at_join_point_is_allowed() -> None:
    own = [((0.0, 0.0), (0.0, 10.0)), ((0.0, 10.0), (10.0, 10.0))]
    # candidate touches an earlier segment, but only at a declared hop-join point
    candidate = ((0.0, 5.0), (8.0, 5.0))
    assert _same_net_self_conflict(candidate, own)
    assert not _same_net_self_conflict(candidate, own, join_points=((0.0, 5.0),))


def test_routed_paths_have_no_same_net_self_crossings() -> None:
    for topology_cls in TOPOLOGIES:
        result = _default_physical_design(topology_cls)
        for route in result.routes:
            segs = route.external_segments
            for i, a in enumerate(segs):
                for b in segs[i + 2 :]:
                    assert _orthogonal_crossing_point(a, b) is None


def test_same_net_local_or_stub_conflict_candidate_is_rejected() -> None:
    local_or_stub = [((0.0, 0.0), (20.0, 0.0))]

    assert _same_net_self_conflict(((10.0, -10.0), (10.0, 10.0)), local_or_stub)
    assert _same_net_self_conflict(((10.0, -10.0), (10.0, 0.0)), local_or_stub)
    assert _same_net_self_conflict(((5.0, 0.0), (15.0, 0.0)), local_or_stub)
    assert not _same_net_self_conflict(((20.0, 0.0), (20.0, 10.0)), local_or_stub)


def test_routed_paths_have_no_same_net_self_touch_overlap_or_hairpin() -> None:
    same_net_rules = {"same_net_touching_corner", "same_net_hairpin"}
    for topology_cls in TOPOLOGIES:
        result = _default_physical_design(topology_cls)

        same_net_violations = [
            violation
            for violation in result.drc_violations
            if violation.rule in same_net_rules
        ]
        assert not same_net_violations
        for route in result.routes:
            assert not _same_route_segment_conflicts(
                tuple(route.external_segments) + tuple(route.local_segments)
            )


def test_crossing_count_by_pair_is_reported_not_a_hard_drc_rule() -> None:
    crossings = (
        RouteCrossing(0, 1, (10.0, 10.0)),
        RouteCrossing(1, 0, (20.0, 20.0)),
        RouteCrossing(0, 2, (30.0, 30.0)),
    )

    assert _crossing_count_by_pair_tuple(crossings) == (
        ((0, 1), 2),
        ((0, 2), 1),
    )


def _same_route_segment_conflicts(segments: tuple) -> bool:
    for idx, first in enumerate(segments):
        for second in segments[idx + 1 :]:
            if _segments_collinear_overlap(first, second):
                return True
            if _orthogonal_crossing_point(first, second) is not None:
                return True
            contact = _segment_contact_point(first, second)
            if contact is None:
                continue
            first_endpoint = any(_same_test_point(contact, point) for point in first)
            second_endpoint = any(_same_test_point(contact, point) for point in second)
            if not (first_endpoint and second_endpoint):
                return True
    return False


def _same_test_point(a, b) -> bool:
    return abs(a[0] - b[0]) < 1e-6 and abs(a[1] - b[1]) < 1e-6


def test_route_crossings_counts_local_segment_crossings() -> None:
    # Inter-net crossings through port-access (local) segments are physically
    # real and must be counted; only same-net and non-orthogonal geometry is
    # exempt. Net I0's external wire crosses net I1's vertical local stub.
    from mrr_switch_optimizer.routing.refinement import (
        _route_crossings,
        _route_with_crossing_count,
    )

    external_route = PhysicalRoute(
        input_port=0,
        output_port=0,
        waypoints=((0.0, 0.0), (100.0, 0.0)),
        length_um=100.0,
        bend_count=0,
        external_segments=(((0.0, 0.0), (100.0, 0.0)),),
        local_segments=(),
    )
    local_route = PhysicalRoute(
        input_port=1,
        output_port=1,
        waypoints=((50.0, -50.0), (50.0, 50.0)),
        length_um=100.0,
        bend_count=0,
        external_segments=(),
        local_segments=(((50.0, -50.0), (50.0, 50.0)),),
    )
    crossings = _route_crossings((external_route, local_route))
    assert len(crossings) == 1
    assert {crossings[0].net_a, crossings[0].net_b} == {0, 1}
    assert _same_test_point(crossings[0].location, (50.0, 0.0))
    for route in (external_route, local_route):
        assert _route_with_crossing_count(route, crossings).crossing_count == 1

    # Local x local inter-net crossings count too.
    horizontal_local = PhysicalRoute(
        input_port=2,
        output_port=2,
        waypoints=((0.0, 10.0), (100.0, 10.0)),
        length_um=100.0,
        bend_count=0,
        external_segments=(),
        local_segments=(((0.0, 10.0), (100.0, 10.0)),),
    )
    crossings_all = _route_crossings((external_route, local_route, horizontal_local))
    assert len(crossings_all) == 2
