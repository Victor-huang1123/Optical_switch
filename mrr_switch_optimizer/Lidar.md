# LiDAR Ideas Useful for Our Optical Switch Router

Source repo: https://github.com/ScopeX-ASU/LiDAR

LiDAR is useful as a routing-design reference, not as a direct replacement for our
current router. Our project still needs topology-specific logic for Waksman,
Padded Benes, Spanke-Benes, MRR state assignment, S-table loss, surrogate cost,
and permutation evaluation. LiDAR's value is in its detailed-router policies:
port access planning, orientation-aware A*, crossing insertion, congestion
history, and grid-based legality checks.

## Current Router Pain Points

Most physical routing behavior is currently concentrated in
`mrr_switch_optimizer/router.py`.

Relevant current pieces:

- `RoutingRules`: fixed global routing constants.
- `_route_physical_order`: net ordering, occupied segments, occupied bends,
  forbidden points, rip-up orchestration.
- `_route_one_path`: stitches input bus, MRR internals, port stubs, escape
  points, inter-stage routes, and output bus.
- `_astar_route`: Manhattan A* over tracks with state `(x_idx, y_idx, prev_dir)`.
- `_route_segment_available`: checks blockers, obstacles, forbidden points, and
  crossing policy.
- `_port_escape_point`, `_all_port_escape_segments`, `_port_stub_point`,
  `_port_stub_segment`: current port-access logic.
- `_route_crossings`, `_segment_crossing_count`: crossings are mostly detected
  after routing, not inserted as explicit route decisions.

The practical issue is that many route-quality fixes are now implemented as
penalties or repair passes. That makes local fixes fragile: a new penalty can
help one topology while breaking another.

## What We Should Borrow from LiDAR

### 1. Explicit Port Access Planning

LiDAR idea:

- Propagate ports out of component bounding boxes.
- Reserve access regions in front of ports so other nets cannot block them.
- Spread congested ports.
- Use staggered access offsets when many nearby ports need to enter the same
  routing channel.

Why it matters for us:

- Our MRR ports currently escape with a fixed left/right rule based on port kind.
- `_all_port_escape_segments` reserves all stub-to-escape segments globally, but
  we do not have an explicit per-port access object.
- Congestion near MRR cells is a major source of corner touches, bend conflicts,
  and late repair.

Suggested implementation:

Create `mrr_switch_optimizer/port_access.py`.

Initial data structures:

```python
@dataclass(frozen=True)
class PortAccessPoint:
    cell_id: str
    port: str
    owner_input: int | None
    port_xy: Point
    stub_xy: Point
    escape_xy: Point
    orientation: str  # "E", "W", "N", "S" for now


@dataclass(frozen=True)
class PortAccessRegion:
    owner_input: int | None
    segment: Segment
    reserved_for_owner_only: bool = True


@dataclass(frozen=True)
class PortAccessPlan:
    points_by_cell_port: dict[tuple[str, str], PortAccessPoint]
    reserved_regions: tuple[PortAccessRegion, ...]
```

Move these existing functions into that module first, with behavior unchanged:

- `_port_stub_point`
- `_port_stub_segment`
- `_port_escape_point`
- `_all_port_stub_segments`
- `_all_port_escape_segments`

Then add a planner:

```python
def build_port_access_plan(
    paths: list[Path],
    cells: dict[str, MRRCell],
    rules: RoutingRules,
    port_stub_um: float,
) -> PortAccessPlan:
    ...
```

Phase 1 behavior should match the current implementation exactly. The only
difference is that `_route_physical_order` receives a `PortAccessPlan` instead
of recomputing free-floating lists of segments.

Phase 2 behavior can add LiDAR-style policies:

- Detect same-side dense ports per cell, grouped by `(cell_id, side)`.
- If two escape points are closer than `2 * bend_radius_um + min_spacing_um`,
  stagger one escape point by one routing track.
- Reserve a bend-aware access length:
  `max(port_escape_um, 2 * bend_radius_um + grid_pitch_um)`.
- Treat own access region as enterable only by the owning net; all other nets
  see it as blocked.

Important: port access should not go into `geometry.py`. Geometry should only
provide primitives. Port access is router policy.

Tests to add:

- Every path still contains its port stub and port escape points.
- No non-owner net intersects another net's access region.
- Dense same-side ports produce distinct escape points.
- Staggered escapes do not create new MRR keepout violations.

### 2. Grid Occupancy Map Instead of Segment Lists Everywhere

LiDAR idea:

- Use a GridMap/bitmap with node type: empty, blockage, port, waveguide,
  compound.
- Store waveguide orientation in occupied grid cells.
- Use the map during neighbor generation, not only after route construction.

Why it matters for us:

- Our A* currently passes around `blockers`, `obstacles`, `forbidden_points`,
  `current_segments`, and `reserved_port_access`.
- This works, but every neighbor expansion recomputes legality from segment
  geometry.
- Crossing decisions need to know the orientation of the route being hit. A
  segment list can answer that, but a route grid makes it simpler and more
  explicit.

Suggested implementation:

Create `mrr_switch_optimizer/route_grid.py`.

Initial data structures:

```python
@dataclass(frozen=True)
class GridNodeOccupancy:
    kind: str  # "empty", "obstacle", "port_access", "waveguide"
    owner_input: int | None = None
    orientation: str | None = None  # "H", "V"; future: "D45", "D135"


class RouteGrid:
    def __init__(self, x_tracks: tuple[float, ...], y_tracks: tuple[float, ...]):
        ...

    def mark_obstacle(self, obstacle: Obstacle) -> None:
        ...

    def mark_port_access(self, region: PortAccessRegion) -> None:
        ...

    def mark_route(self, input_port: int, segments: tuple[Segment, ...]) -> None:
        ...

    def segment_query(self, segment: Segment) -> list[GridNodeOccupancy]:
        ...
```

Do not replace all geometry DRC immediately. Start by using `RouteGrid` as a
cache for A* availability and keep `validate_physical_routes` as the final
source of truth.

Tests to add:

- Marking a route then querying a crossing segment returns the occupied
  orientation.
- Own port-access segment is legal for the owner and illegal for other nets.
- RouteGrid and existing `_route_segment_available` agree on simple synthetic
  cases.

### 3. Orientation-Aware A*

LiDAR idea:

- A routing node is `(x, y, orientation)`, not only `(x, y)`.
- Neighbor generation is based on bend geometry and orientation.
- 45-degree neighbors can be enabled, but only after the Manhattan router is
  stable.

Why it matters for us:

- Current A* state is `(x_idx, y_idx, prev_dir)`, which is close but still treats
  orientation mostly as a cost/tie-break side effect.
- Bend legality is checked indirectly through penalties and post-route DRC.
- Recent fixes such as backtrack penalty, turn timing penalty, preferred bend
  x-slot, and reserve-space penalty are signs that the state model is too weak.

Suggested implementation:

Create `mrr_switch_optimizer/grid_router.py`.

Proposed state:

```python
@dataclass(frozen=True)
class RouterState:
    x_idx: int
    y_idx: int
    orientation: str  # "", "E", "W", "N", "S"
```

Proposed neighbor type:

```python
@dataclass(frozen=True)
class NeighborMove:
    next_state: RouterState
    segment: Segment
    move_type: str  # "straight", "bend90", "crossing"
    bend_point: Point | None = None
    crossing: RouteCrossing | None = None
```

Cost components:

- segment length
- bend arc cost
- bend spacing risk
- crossing insertion cost
- port-access violation cost or hard block
- congestion/history cost
- horizontal and vertical backtrack cost
- corridor policy cost

Keep 45-degree routing out of the first implementation. LiDAR supports it, but
our current topology tests and visualization assume Manhattan segments. Add 45
only after a clean Manhattan orientation-aware router exists.

Tests to add:

- A vertical step away from destination receives backtrack penalty.
- A 90-degree turn near source/destination receives turn timing penalty.
- A route cannot place consecutive external bends closer than
  `2 * bend_radius_um`.
- End-corridor policy avoids vertical overshoot outside the design y-range.

### 4. Crossing as an Explicit Inserted Event

LiDAR idea:

- If a neighbor hits an existing waveguide, check whether crossing insertion is
  legal.
- Legal crossing requires compatible orientations, local clearance, crossing
  budget, and no nearby port/obstacle conflict.

Why it matters for us:

- We currently count geometric crossings after route generation.
- The cost model needs crossing loss and crossing crosstalk as physical events.
- A crossing should not be accepted just because two axis-aligned segments
  geometrically intersect.

Suggested implementation:

Add `mrr_switch_optimizer/crossing.py`.

Initial data structures:

```python
@dataclass(frozen=True)
class CrossingCandidate:
    location: Point
    host_input: int
    crossed_input: int
    host_orientation: str
    crossed_orientation: str
    clearance_um: float


@dataclass(frozen=True)
class CrossingRule:
    min_clearance_from_bend_um: float
    min_clearance_from_port_um: float
    crossing_loss_db: float
    crossing_xt_db: float
```

Candidate check:

```python
def legal_crossing_candidate(
    segment: Segment,
    route_grid: RouteGrid,
    rules: RoutingRules,
    owner_input: int,
) -> CrossingCandidate | None:
    ...
```

Minimum legal checks:

- Segment must intersect exactly one existing waveguide segment.
- Orientations must be orthogonal for Manhattan v1.
- Crossing point cannot be on an endpoint, bend, port stub, or port access
  region.
- There must be enough straight clearance before and after the crossing.
- The crossed net and host net must not exceed crossing budget, if budgets are
  introduced.

Router integration:

- `_astar_route` should consider a crossing move as a legal neighbor, not as a
  post-hoc count.
- `PhysicalRoute` should retain crossing events or the router result should
  include a separate crossing table.
- Cost model should consume crossing events for insertion loss and crosstalk.

Tests to add:

- Orthogonal mid-segment crossing is legal.
- Endpoint/T-junction contact is illegal and remains a DRC violation.
- Crossing too close to a bend is illegal.
- Crossing count in `PhysicalRoutingResult` matches inserted crossing events.

### 5. Congestion History and Better Net Ordering

LiDAR idea:

- Track history cost in the grid.
- Increase cost where failed or conflicting routes repeatedly pass.
- Route nets by topology/order/group rather than a fixed naive sequence.

Why it matters for us:

- We have `_path_routing_complexity`, rip-up passes, forbidden points, local
  rollback, and braid repair. These are useful, but reactive.
- A history map lets the next A* pass avoid bad channels before DRC fails.

Suggested implementation:

In `route_grid.py` or `grid_router.py`:

```python
@dataclass
class HistoryCost:
    by_track_orientation: dict[tuple[int, int, str], float]

    def bump_route(self, route: PhysicalRoute, amount: float) -> None:
        ...

    def cost(self, state: RouterState) -> float:
        ...
```

Integration:

- On a failed route or DRC violation, bump history cost along the conflicting
  route/window.
- A* adds `history.cost(next_state)` to the neighbor cost.
- Keep `forbidden_points_by_input` initially, but gradually downgrade it from
  hard repair mechanism to last-resort hint.

Net ordering:

- Continue using `_path_routing_complexity` as baseline.
- Add congestion score from port-access groups and direct-line crossing
  estimates.
- Route simple/low-conflict nets first only if they claim stable corridors; route
  high-risk nets first if port access is scarce. This should be policy-driven and
  tested per topology.

Tests to add:

- Re-running after a synthetic conflict increases cost on the same track.
- A second routing pass chooses an alternate track when one exists.
- Routing remains deterministic for the same permutation and seed.

### 6. Per-Net Routing Bounds with Controlled Expansion

LiDAR idea:

- Each net has a routing bound around its source and destination.
- Bounds expand after failures.

Why it matters for us:

- Current routing grid includes margin above and below design elements.
- That makes vertical overshoot possible unless penalties prevent it.
- We should prefer hard per-net corridor bounds over accumulating more
  backtrack penalties.

Suggested implementation:

Add a `RoutingWindow` policy:

```python
@dataclass(frozen=True)
class RoutingWindow:
    left: float
    right: float
    bottom: float
    top: float

    def expanded(self, tracks: int, pitch_um: float) -> "RoutingWindow":
        ...
```

Policy:

- End corridors: y-window should cover source wire, destination escape y, one
  bend-radius margin, and one track of slack.
- Inter-stage corridors: y-window should cover both MRR port escape y-values,
  inflated obstacles in between, and one or two detour tracks.
- On failure: expand by `grid_pitch_um * pass_idx`, capped by design bounds.

Tests to add:

- End-corridor route does not leave its y-window when a legal route exists.
- Expanding the window can recover from a synthetic blockage.
- Waksman, Padded Benes, and Spanke-Benes still pass baseline physical tests.

### 7. Keep Geometry, Port Access, DRC, and Search Separate

Recommended module split:

```text
mrr_switch_optimizer/geometry.py
    Point, Segment, obstacle inflation, axis alignment, crossing/contact,
    overlap, spacing, bend count, track canonicalization.

mrr_switch_optimizer/port_access.py
    MRR-specific stub/escape/access-region planning.

mrr_switch_optimizer/route_grid.py
    Occupancy map, history cost, route marking, segment query.

mrr_switch_optimizer/crossing.py
    Crossing candidate legality and crossing-event data structures.

mrr_switch_optimizer/grid_router.py
    A* state, neighbor generation, cost model, corridor policies.

mrr_switch_optimizer/drc.py
    Final route validation rules.

mrr_switch_optimizer/physical_router.py
    Orchestration: route order, rip-up, repair, result assembly.
```

Do this as a no-behavior-change refactor first. The current test suite should
stay green after each extraction.

## Suggested Implementation Order

### Phase 0: Lock Behavior with Tests

Before changing routing strategy, add characterization tests for current pain
points:

- vertical overshoot in end corridor
- min bend separation near MRR keepout
- no non-owner net through port-access segment
- crossing count upper bounds for Waksman and Padded Benes
- deterministic routing for the same permutation

### Phase 1: Extract Modules Without Changing Behavior

Move code only:

1. `geometry.py`
2. `port_access.py`
3. `drc.py`

Expected command:

```bash
cd /home/jchuang/Optical_switch && python -m pytest tests/test_physical_router.py -v
```

Expected result should remain 10 passed.

### Phase 2: Introduce `PortAccessPlan`

Keep current escape coordinates first. Replace loose
`reserved_port_access: list[Segment]` with a structured `PortAccessPlan`.

Then add owner-aware access reservations:

- own net can use its access segment
- other nets cannot cross or touch it except at true legal crossings, if we later
  allow crossing there

### Phase 3: Add RouteGrid as a Legality Cache

Use `RouteGrid` inside `_astar_route` or the new `grid_router.py`, but keep final
DRC unchanged. This creates the foundation for crossing insertion and history
cost.

### Phase 4: Make Crossing an A* Move

Replace pure `_segment_crossing_count` scoring with crossing-candidate
generation:

- illegal touch remains illegal
- legal crossing becomes a move with explicit cost and event output
- route result records crossing location and net pair

### Phase 5: Replace A* State with Orientation-Aware State

Move from `(x_idx, y_idx, prev_dir)` to `RouterState(x_idx, y_idx, orientation)`.
Keep Manhattan neighbors only at first. Add 45-degree neighbors only after the
Manhattan version is clean.

### Phase 6: Optional LiDAR/GDSFactory Backend Experiment

Only after the internal router is modular:

- Build an adapter from our active paths to a LiDAR-style netlist.
- Run LiDAR as an external comparison backend, not as the default.
- Compare route length, crossings, DRC violations, and GDS output quality.

## Things Not to Do Yet

- Do not vendor LiDAR directly into this project.
- Do not replace topology-aware routing with LiDAR's generic netlist flow.
- Do not enable 45-degree routing before the Manhattan router is stable.
- Do not put port-access policy into `geometry.py`.
- Do not keep adding global penalties to `_astar_route` without separating
  corridor policies and route-grid legality.

## Practical Priority

The highest-value first step is:

1. Extract `port_access.py` with no behavior change.
2. Introduce `PortAccessPlan`.
3. Make access regions owner-aware.
4. Add tests proving port access is not blocked by other nets.

This directly addresses the current router's MRR-nearby congestion without
requiring a full LiDAR-style rewrite.
