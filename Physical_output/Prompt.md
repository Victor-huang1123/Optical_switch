# Physical Router — Codex Implementation Task

## Rules You Must Follow

1. **Run the full test suite after every change before declaring done.**
   ```bash
   cd /home/jchuang/Optical_switch && python -m pytest tests/test_physical_router.py -v
   ```
   Expected baseline: **10 passed**. If any test fails after your change, you must:
   - Read the failure message carefully
   - Revert the change (`git diff` to see what you changed, then undo it)
   - Explain specifically why it failed and what you would need to change differently
   - Do NOT declare the task done if tests fail

2. **Do not silently skip steps.** If you cannot implement something, say so explicitly.

3. **Do not add extra features, refactors, or cleanup** beyond what is specified. Change only what the task requires.

---

## Already Implemented — Do NOT Redo These

The following are confirmed in `mrr_switch_optimizer/router.py` and must not be changed:

| Task | What's done |
|------|-------------|
| 1 | `occupied_bends.update(pt for seg in route.external_segments for pt in seg)` in `_route_physical_order` |
| 2 | `_external_bend_pairs_too_close()` helper + `"min_bend_separation"` DRC rule in `validate_physical_routes` + repair in `_local_rollback_forbidden_points_by_input` |
| 3 | `_corridor_preferred_x` accepts `x_start, x_end, wire_pitch_um, n_physical` and covers first + last corridors |
| 4 | `_astar_route` has `reserve_space_penalty: bool = False` parameter; end corridors (first + last) pass `reserve_space_penalty=True` with coefficient hardcoded at `0.5`; inter-stage corridors do NOT use this penalty |

**Critical constraints on Task 4:**
- Do NOT add `reserve_space_penalty=True` to inter-stage corridor A\* calls. Tested: causes +7 crossings and new `min_bend_separation` violations in PaddedBenes 8×8.
- Do NOT increase the reserve_space coefficient above `0.5`. C=2.0 was tried and caused non-deterministic test failures (1μm min_bend_separation violations in SpankeBenes/PaddedBenes that rip-up could not resolve in 3 passes).

---

## Key Data Structures (do not change)

```python
@dataclass(frozen=True)
class RoutingRules:
    grid_pitch_um: float = 8.0
    min_spacing_um: float = 4.0
    bend_radius_um: float = 5.0
    crossing_penalty_um: float = 20.0
    turn_guard_um: float = 16.0
    turn_timing_penalty_um: float = 48.0
    backtrack_penalty_um: float = 80.0
    hairpin_penalty_um: float = 200.0
    max_ripup_passes: int = 3
    # Do NOT add new fields to RoutingRules
```

---

## Task — Vertical Backtrack Penalty (routing quality)

### Problem

Routes can travel **outside the design y-bounds** and fold back. Visible as the orange route
going above the S4 cell keepout before coming back down to reach its output wire.

Root cause: `_backtrack_penalty` (around line 2518) **only checks the x-direction**. There
is zero penalty for vertical steps that move *away from* `dst_y`. The routing grid has a
32 μm margin above and below all design elements (line ~1289), so those out-of-bounds grid
nodes are free to visit.

### What to change

`_backtrack_penalty` currently reads:

```python
def _backtrack_penalty(
    start: Point,
    end: Point,
    src: Point,
    dst: Point,
    rules: RoutingRules,
) -> float:
    if abs(start[0] - end[0]) >= EPS:
        route_dx = dst[0] - src[0]
        step_dx = end[0] - start[0]
        if abs(route_dx) >= EPS and route_dx * step_dx < -EPS:
            return rules.backtrack_penalty_um
    return 0.0
```

Add the analogous y-direction check **inside the same function**:

```python
def _backtrack_penalty(
    start: Point,
    end: Point,
    src: Point,
    dst: Point,
    rules: RoutingRules,
) -> float:
    if abs(start[0] - end[0]) >= EPS:
        route_dx = dst[0] - src[0]
        step_dx = end[0] - start[0]
        if abs(route_dx) >= EPS and route_dx * step_dx < -EPS:
            return rules.backtrack_penalty_um
    if abs(start[1] - end[1]) >= EPS:          # ← NEW: vertical step
        route_dy = dst[1] - src[1]
        step_dy = end[1] - start[1]
        if abs(route_dy) >= EPS and route_dy * step_dy < -EPS:
            return rules.backtrack_penalty_um
    return 0.0
```

This charges `backtrack_penalty_um = 80 μm` per step when moving **vertically away from
`dst_y`**, exactly mirroring the existing horizontal backtrack behaviour. It applies to all
corridors (end and inter-stage), which is correct — moving vertically away from the
destination is always suboptimal.

### Testing requirement

```bash
cd /home/jchuang/Optical_switch && python -m pytest tests/test_physical_router.py -v
```

All 10 tests must pass. If any fail after your change:
1. Run `git diff mrr_switch_optimizer/router.py` to see your exact change
2. Revert the change
3. Report the failure message and explain what went wrong

### Expected outcome

Routes in end corridors no longer overshoot in y before reaching the output wire. The
orange route that previously went above the S4 keepout box should now make its y-correction
within the expected y-range.

---

## What NOT to Change

- Do NOT change `_route_physical_order` occupied_bends logic
- Do NOT change A\* slot bias coefficient from `0.05`
- Do NOT add `occupied_bends` points to `reserved_port_access`
- Do NOT move `_simplify_physical_routes` back inside the rip-up loop
- Do NOT add new fields to `RoutingRules`
- Do NOT add `reserve_space_penalty=True` to inter-stage A\* calls
- Do NOT modify the `reserve_space_coeff` value (hardcoded at `0.5` inside `_astar_route`; C=2.0 was tested and caused non-deterministic failures)
- Do NOT modify the 5-point bridge detection in `_simplify_route_waypoints`

---

## Known Routing Quality Issue (do NOT attempt to fix)

**Pink route (I3) large rectangular loop** — I3 makes a ~254 μm horizontal run at y=166
instead of going straight to S3. Root cause (three layers):

1. The MRR switch lower bus is physically right-to-left (add→drop). S1 bar-state local
   segment correctly occupies y=140 from x=160–220 going leftward; this is not a bug.
2. After exiting S1 at out_escape (160, 140), y=140 is blocked rightward (S1 local segment),
   y=132 is inside the S1 cell obstacle (inflated keepout extends ~y=130–158), y=148 is
   occupied by I0 (teal), y=156 is also inside the S1 obstacle. Only y=166 is accessible.
3. At y=166 the route stays until after the S2 cells (x=265–325 also block y=140), so it
   can't return to y=140 until x=424 — creating the 254 μm run.

Task 5 (vertical backtrack penalty) does NOT fix this because the route has no alternative
to y=166; adding 80 μm penalty just makes the forced path cost more without changing it.

The proper fix would be to route I3 before I0 (so I3 can claim y=148 for a short bypass)
or add a y-slot reservation mechanism for inter-stage corridors after lower-bus cell exits.
That is a larger redesign — do NOT attempt it in this task.
