from __future__ import annotations

from dataclasses import dataclass
from math import pi
from typing import Literal

from ..analysis.cost import ALPHA_DB_PER_UM, CROSSING_LOSS_DB_PER_CROSS
from ..core.models import DEFAULT_CELL_GEOMETRY

# Quarter-circle arc length for a 90-degree bend with minimum bend radius.
# Si photonics single-mode waveguides typically use R_min = 5 um.
BEND_RADIUS_UM: float = 5.0
BEND_ARC_UM: float = 0.5 * pi * BEND_RADIUS_UM
EPS: float = 1e-6

Point = tuple[float, float]
Segment = tuple[Point, Point]

@dataclass(frozen=True)
class Obstacle:
    cell_id: str
    left: float
    right: float
    bottom: float
    top: float

@dataclass(frozen=True)
class RoutingWindow:
    left: float
    right: float
    bottom: float
    top: float

    def contains_point(self, point: Point) -> bool:
        x, y = point
        return (
            self.left - EPS <= x <= self.right + EPS
            and self.bottom - EPS <= y <= self.top + EPS
        )

    def contains_segment(self, segment: Segment) -> bool:
        start, end = segment
        return self.contains_point(start) and self.contains_point(end)

    def expanded(
        self,
        tracks: int,
        pitch_um: float,
        bounds: "RoutingWindow | None" = None,
    ) -> "RoutingWindow":
        margin = max(0, tracks) * pitch_um
        expanded = RoutingWindow(
            self.left - margin,
            self.right + margin,
            self.bottom - margin,
            self.top + margin,
        )
        if bounds is None:
            return expanded
        return RoutingWindow(
            max(bounds.left, expanded.left),
            min(bounds.right, expanded.right),
            max(bounds.bottom, expanded.bottom),
            min(bounds.top, expanded.top),
        )

@dataclass(frozen=True)
class RoutedSegment:
    waypoints: tuple[tuple[float, float], ...]
    length_um: float
    bend_count: int

@dataclass(frozen=True)
class PhysicalRoute:
    input_port: int
    output_port: int
    waypoints: tuple[Point, ...]
    length_um: float
    bend_count: int
    external_segments: tuple[Segment, ...] = ()
    local_segments: tuple[Segment, ...] = ()
    crossing_count: int = 0

@dataclass(frozen=True)
class OwnedSegment:
    net_id: int
    segment: Segment
    kind: str
    index: int

@dataclass(frozen=True)
class OccupiedRouteSegment:
    owner_input: int | None
    segment: Segment
    kind: str = "external"

@dataclass(frozen=True)
class RoutingRules:
    grid_pitch_um: float = 8.0
    grid_merge_tol_um: float = 3.5
    min_spacing_um: float = 4.0
    waveguide_width_um: float = DEFAULT_CELL_GEOMETRY.waveguide_width_um
    mrr_keepout_um: float = 6.0
    port_escape_um: float = 10.0
    bend_radius_um: float = 5.0
    crossing_penalty_um: float = 20.0
    repeated_crossing_penalty_um: float = 400.0
    outward_repeated_crossing_scale: float = 0.25
    turn_guard_um: float = 16.0
    turn_timing_penalty_um: float = 48.0
    # When enabled, turn_guard_um is treated as a physical constraint in the
    # direct-dB search and is not attenuated by db_tie_breaker_scale.
    physical_turn_guard: bool = False
    backtrack_penalty_um: float = 80.0
    hairpin_penalty_um: float = 200.0
    # Same-net hairpin/spacing proxies are physical search discipline in v4,
    # not weak dB tie-break guidance.  Default-off preserves legacy hashes.
    physical_same_net_hairpin: bool = False
    local_repair_max_shift_tracks: int = 8
    route_window_max_detour_tracks: int = 4
    # Global routing-grid margin beyond the outermost cells/IO tracks, in grid
    # tracks. Bounds the vertical detour headroom above/below the fabric.
    grid_margin_tracks: float = 4.0
    max_ripup_passes: int = 3
    # Stop rip-up early after this many consecutive passes without score
    # improvement (0 disables early stopping and keeps legacy behavior).
    early_stop_stagnant_passes: int = 0
    ripup_route_budget: int | None = 4
    ripup_order_candidate_limit: int = 1
    allow_crossings: bool = True
    explicit_crossings: bool = True
    min_crossing_clearance_um: float | None = None
    fallback_beam_width: int = 0
    fallback_alternatives_per_hop: int = 2
    route_order_beam_width: int = 0
    route_order_beam_max_astar_pops: int | None = 2000
    max_astar_pops: int | None = None
    ripup_max_astar_pops: int | None = None
    astar_heuristic_weight: float = 1.0
    cost_model: Literal["um_penalty", "db"] = "um_penalty"
    db_tie_breaker_scale: float = 0.05
    # Compatibility switch for the pre-existing direct-dB search mode.
    loss_aware_cost: bool = False
    prop_loss_db_per_um: float = ALPHA_DB_PER_UM
    bend_loss_db_per_bend: float = 0.0
    crossing_loss_db_per_cross: float = CROSSING_LOSS_DB_PER_CROSS
    repeated_crossing_loss_db: float = 0.0
    jog_penalty_db: float = 0.0
    bend_placement_penalty_db: float = 0.0
    future_crossing_reservation: bool = False
    port_approach_fallback: bool = False
    hard_source_crossing_reservation: bool = False
    hard_reserved_crossing_reservation: bool = False
    hard_crossing_budget: bool = False
    source_hint_bounded_only: bool = True
    capped_ripup_bounded_only: bool = True
    # Strict owner-aware port access is implemented but staged off by default:
    # the current search still needs stronger route-window/order strategy before
    # non-owner T-touch rejection can be enabled without losing routability.
    strict_port_access: bool = False
    enforce_bend_spacing: bool = False
    legalize_port_access: bool = False
    port_access_runway_um: float = 10.0
    # Reserved-region escalation stages.  These are set only by the v4 campaign
    # retry ladder and are serialized with the resulting case.
    allow_foreign_outer_runway_transit: bool = False
    port_access_stagger_tracks: int = 0
    drc_same_net_min_spacing: bool = False
    drc_perpendicular_clearance: bool = False
    drc_bend_radius_legality: bool = False
    corridor_guide_mode: Literal["off", "soft", "hard"] = "off"
    # Campaign-only escape hatch for the same-net whole-net remediation.  When
    # enabled, only a riser-displaced deterministic candidate expands its
    # per-hop search window; the global grid/canvas and octave envelope do not
    # change.  Kept off by default to preserve every ordinary routing hash.
    remediation_window_expansion: bool = False

    @property
    def uses_db_cost(self) -> bool:
        return self.cost_model == "db" or self.loss_aware_cost

@dataclass(frozen=True)
class DRCViolation:
    rule: str
    net_id: str
    message: str
    location: Point | None = None

@dataclass(frozen=True)
class FailedNet:
    input_port: int
    output_port: int
    message: str

@dataclass(frozen=True)
class RouteCrossing:
    net_a: int
    net_b: int
    location: Point

@dataclass(frozen=True)
class RoutingStats:
    astar_calls: int = 0
    ripup_passes_executed: int = 0
    early_stopped: bool = False


@dataclass(frozen=True)
class StraightenStats:
    rounds: int = 0
    rewrites: int = 0
    candidate_windows: int = 0
    legal_candidates: int = 0
    bends_removed: int = 0
    length_saved_um: float = 0.0
    crossings_delta: int = 0
    loss_proxy_delta_db: float = 0.0
    bend_slides: int = 0
    crossing_arm_violations_removed: int = 0

@dataclass(frozen=True)
class PhysicalRoutingResult:
    routes: tuple[PhysicalRoute, ...]
    drc_violations: tuple[DRCViolation, ...]
    failed_nets: tuple[FailedNet, ...]
    crossings: tuple[RouteCrossing, ...]
    rules: RoutingRules
    crossing_count_by_pair: tuple[tuple[tuple[int, int], int], ...] = ()
    stats: RoutingStats = RoutingStats()

class RoutingError(RuntimeError):
    pass

__all__ = [
    'BEND_RADIUS_UM',
    'BEND_ARC_UM',
    'EPS',
    'Point',
    'Segment',
    'Obstacle',
    'RoutingWindow',
    'RoutedSegment',
    'PhysicalRoute',
    'OwnedSegment',
    'OccupiedRouteSegment',
    'RoutingRules',
    'DRCViolation',
    'FailedNet',
    'RouteCrossing',
    'RoutingStats',
    'StraightenStats',
    'PhysicalRoutingResult',
    'RoutingError',
]
