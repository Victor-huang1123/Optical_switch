from __future__ import annotations

from dataclasses import replace
from math import pi

import pytest

from mrr_switch_optimizer.core.fabric import (
    FabricGraph,
    FabricWaveguide,
)
from mrr_switch_optimizer.routing.drc import validate_physical_routes
from mrr_switch_optimizer.routing.fabric import (
    FabricEdgeRoute,
    FabricRoute,
    FixedFabricRoutingResult,
)
from mrr_switch_optimizer.routing.grid import _turn_timing_cost
from mrr_switch_optimizer.routing.straighten import straighten_fixed_fabric
from mrr_switch_optimizer.routing.types import PhysicalRoute, RoutingRules


def _physical(input_port: int, points: tuple[tuple[float, float], ...]) -> PhysicalRoute:
    segments = tuple(zip(points, points[1:]))
    return PhysicalRoute(
        input_port=input_port,
        output_port=input_port,
        waypoints=points,
        length_um=sum(
            abs(end[0] - start[0]) + abs(end[1] - start[1])
            for start, end in segments
        ),
        bend_count=max(0, len(points) - 2),
        external_segments=segments,
    )


def test_crossing_clearance_is_default_off_and_checks_all_four_arms() -> None:
    routes = (
        _physical(0, ((0.0, 0.0), (20.0, 0.0))),
        _physical(1, ((5.0, -20.0), (5.0, 20.0))),
    )
    rules = RoutingRules(min_spacing_um=0.0, mrr_keepout_um=0.0)

    assert not validate_physical_routes(routes, {}, rules)
    violations = validate_physical_routes(
        routes,
        {},
        replace(rules, min_crossing_clearance_um=10.0),
    )

    assert len(violations) == 1
    assert violations[0].rule == "crossing_clearance"
    assert "minimum=5.000 um" in violations[0].message


def test_crossing_clearance_accepts_exact_threshold() -> None:
    routes = (
        _physical(0, ((0.0, 0.0), (20.0, 0.0))),
        _physical(1, ((10.0, -20.0), (10.0, 20.0))),
    )
    rules = RoutingRules(
        min_spacing_um=0.0,
        mrr_keepout_um=0.0,
        min_crossing_clearance_um=10.0,
    )

    assert not validate_physical_routes(routes, {}, rules)


def test_crossing_component_rules_ignore_mrr_local_contacts() -> None:
    local = _physical(0, ((0.0, 0.0), (20.0, 0.0)))
    local = replace(
        local,
        external_segments=(),
        local_segments=local.external_segments,
    )
    external = _physical(1, ((5.0, -20.0), (5.0, 20.0)))
    rules = RoutingRules(
        min_spacing_um=0.0,
        mrr_keepout_um=0.0,
        min_crossing_clearance_um=10.0,
    )

    # The optical loss model still counts this contact elsewhere, but no
    # standalone crossing cell was inserted for an MRR-internal segment.
    assert not validate_physical_routes((local, external), {}, rules)


def test_physical_turn_guard_bypasses_db_tie_scaling_only_when_enabled() -> None:
    legacy = RoutingRules(
        cost_model="db",
        prop_loss_db_per_um=0.0002,
        turn_guard_um=10.0,
        turn_timing_penalty_um=48.0,
        db_tie_breaker_scale=0.05,
    )
    physical = replace(legacy, physical_turn_guard=True)

    assert _turn_timing_cost((5.0, 5.0), (0.0, 5.0), (20.0, 5.0), legacy) == pytest.approx(2.4)
    assert _turn_timing_cost((5.0, 5.0), (0.0, 5.0), (20.0, 5.0), physical) == pytest.approx(48.0)


def test_straightener_preserves_edge_endpoints_and_removes_external_jog() -> None:
    points = (
        (0.0, 0.0),
        (20.0, 0.0),
        (20.0, 20.0),
        (40.0, 20.0),
        (40.0, 40.0),
        (60.0, 40.0),
    )
    physical = replace(
        _physical(0, points),
        length_um=100.0 + 4 * 0.5 * pi * 5.0,
        bend_count=4,
    )
    edge_route = FabricEdgeRoute("e0", points, physical.length_um, 4)
    fabric_route = FabricRoute(
        edge_id="e0",
        covered_edge_ids=("e0",),
        waypoints=points,
        length_um=physical.length_um,
        bend_count=4,
        external_segments=physical.external_segments,
        local_segments=(),
        edge_routes=(edge_route,),
    )
    graph = FabricGraph(
        topology_name="unit",
        n_logical=1,
        n_physical=1,
        blocked_ports=(),
        edges=(),
        waveguides=(
            FabricWaveguide(
                owner_edge_id="e0",
                edge_ids=("e0",),
                input_wire=0,
                output_wire=0,
                blocked_boundary=False,
            ),
        ),
    )
    result = FixedFabricRoutingResult(
        graph=graph,
        routes=(fabric_route,),
        failed_edges=(),
        drc_violations=(),
        crossings=(),
        rules=RoutingRules(min_spacing_um=0.0, mrr_keepout_um=0.0),
    )

    straightened = straighten_fixed_fabric(result, {})

    assert straightened.stats.rewrites == 1
    assert straightened.stats.bends_removed == 3
    assert straightened.routing.routes[0].waypoints[0] == points[0]
    assert straightened.routing.routes[0].waypoints[-1] == points[-1]
    assert straightened.routing.routes[0].edge_routes[0].waypoints[0] == points[0]
    assert straightened.routing.routes[0].edge_routes[0].waypoints[-1] == points[-1]


def test_bend_slide_repairs_crossing_arm_with_bounded_loss() -> None:
    point_sets = (
        ((0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (40.0, 20.0)),
        ((15.0, -20.0), (15.0, 30.0)),
    )
    rules = RoutingRules(
        min_spacing_um=0.0,
        mrr_keepout_um=0.0,
        min_crossing_clearance_um=10.0,
        grid_pitch_um=8.0,
        prop_loss_db_per_um=0.0002,
        crossing_loss_db_per_cross=0.1,
        bend_loss_db_per_bend=0.005,
    )
    physical = tuple(_physical(index, points) for index, points in enumerate(point_sets))
    fabric_routes = tuple(
        FabricRoute(
            edge_id=f"e{index}",
            covered_edge_ids=(f"e{index}",),
            waypoints=route.waypoints,
            length_um=route.length_um,
            bend_count=route.bend_count,
            external_segments=route.external_segments,
            local_segments=(),
            edge_routes=(
                FabricEdgeRoute(
                    f"e{index}",
                    route.waypoints,
                    route.length_um,
                    route.bend_count,
                ),
            ),
        )
        for index, route in enumerate(physical)
    )
    graph = FabricGraph(
        topology_name="slide_unit",
        n_logical=2,
        n_physical=2,
        blocked_ports=(),
        edges=(),
        waveguides=tuple(
            FabricWaveguide(
                owner_edge_id=f"e{index}",
                edge_ids=(f"e{index}",),
                input_wire=index,
                output_wire=index,
                blocked_boundary=False,
            )
            for index in range(2)
        ),
    )
    result = FixedFabricRoutingResult(
        graph=graph,
        routes=fabric_routes,
        failed_edges=(),
        drc_violations=(),
        crossings=(),
        rules=rules,
    )
    before = validate_physical_routes(physical, {}, rules)

    straightened = straighten_fixed_fabric(result, {})

    assert [violation.rule for violation in before] == [
        "crossing_clearance",
        "crossing_footprint",
    ]
    assert straightened.stats.bend_slides == 1
    assert straightened.stats.crossing_arm_violations_removed == 1
    assert straightened.stats.loss_proxy_delta_db <= 0.01
    assert not straightened.routing.drc_violations
    assert straightened.routing.routes[0].waypoints[1][0] == 28.0
