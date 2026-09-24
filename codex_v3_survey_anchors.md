# TASK 2 — v3 Design Anchors Report

## A. Jog straightening (offline)

**A1 — pickle contents & round-trip: FEASIBLE (verified by loading)**
- Anchor: `outputs/nsweep_fixed_fabric_v2/cases/waksman/n06/C_db_realistic/routing_result.pkl` is a 2-tuple `(cells: dict[str, MRRCell], result: FixedFabricRoutingResult)` — written at `mrr_switch_optimizer/app/nsweep_campaign.py:264-265`, reloaded at `nsweep_campaign.py:274-285` with a geometry-hash cross-check at `:285`.
- `FixedFabricRoutingResult` (`mrr_switch_optimizer/routing/fabric.py:66-84`) carries `graph` (FabricGraph), `routes` (FabricRoute), `crossings` (FabricCrossing), `rules`, `stats`. `FabricRoute` (`fabric.py:33-42`) has `waypoints`, `external_segments`, `local_segments`, `edge_routes` — n06 sample: 48 waypoints, 12 external + 30 local segments, 32 crossings. Round-trip `pickle.dumps/loads` verified lossless (frozen dataclasses of tuples). Loads standalone as long as the `mrr_switch_optimizer` package is importable.
- Gotchas: (a) `input_port/output_port` are not on FabricRoute — recover via `result.graph.waveguides` exactly as `tests/test_nsweep.py:81-97` (`_physical_route`) already does; (b) v2 rules snapshot in the pkl: `cost_model="db"`, `enforce_bend_spacing=True`, `legalize_port_access=True`, `drc_bend_radius_legality=True`, `min_crossing_clearance_um=None`; (c) after straightening, `edge_routes` must be re-split (`fabric.py:261-309 _split_edge_routes`) — it locates edge endpoints by waypoint identity (`fabric.py:330-338 _find_waypoint`), so straightening must never delete endpoint waypoints of covered edges; (d) any waypoint change changes `fixed_fabric_geometry_hash` (`app/fabric_reports.py:15-25`, hashes owner ids + covered ids + waypoints only) → cached summaries invalidate at `nsweep_campaign.py:285`; rewrite pkl+summary together.

**A2 — legality primitives: ALL PRESENT, reusable offline**
| primitive | anchor |
|---|---|
| segment availability vs other nets (overlap/spacing/keepout/forbidden/port-access) | `routing/grid.py:1199-1237 _route_segment_available`; helpers `geometry.py:574 _segments_collinear_overlap`, `geometry.py:72 _parallel_spacing_violation`, `geometry.py:184 _orthogonal_crossing_point` |
| port-access reserved regions | `routing/port_access.py:93-141 build_port_access_plan`, owner-aware check `port_access.py:222-242 port_access_conflict` / `:245-265 _detail`, region rules `:183-219 _region_conflict` |
| same-net no-touch | post-route: `routing/drc.py:254-277 _same_net_drc_violations` (adjacent-index exemption at :265); search-time: `grid.py:108 _same_net_self_conflict_reason`, `grid.py:157 _same_net_touching_corner_reason` |
| 2R bend spacing | `drc.py:380-419 _external_bend_pairs_too_close` (always-on) and `drc.py:422-440 _consecutive_bend_pairs_too_close` (gated by `drc_bend_radius_legality`, ON in v2); min = `2*bend_radius_um` (=10 µm) |
| crossing recount | `routing/refinement.py:21-36 _route_crossings` — pure function over routes; counts external+local (`refinement.py:14-18`) |
- Gotcha: `build_port_access_plan` needs `paths`, which need the topology object (`fabric.py:194-226 _fabric_waveguide_paths`) — the pkl stores only `graph`. Rebuild topology from `graph.topology_name`/n (as nsweep_campaign does), or skip search-time port-access parity and rely on DRC + crossing-count-non-increase; note reserved runway corridors are search-policy-only and NOT covered by `validate_physical_routes`, so a straightened segment could legally (per DRC) enter a foreign runway — safest straightening constraint: only merge collinear jogs whose swept rectangle is empty per `_route_segment_available` with rebuilt blockers.

**A3 — standalone DRC re-validation: FEASIBLE (already exercised)**
- `validate_physical_routes(routes, cells, rules)` at `routing/drc.py:22-150` takes plain `PhysicalRoute` list + `cells` dict + `rules` — all three recoverable from the pkl (convert FabricRoute→PhysicalRoute per `tests/test_nsweep.py:81-97`). No router state needed. `benes_template_layout.py:75` already calls it on synthesized (non-A*) routes, proving the offline pattern.

## B. min_crossing_clearance

**B1 — usage sites & enforcement status: PARTIALLY ENFORCED, never as global legality**
- Declaration: `routing/types.py:124` (`min_crossing_clearance_um: float | None = None`); validation only non-negativity `drc.py:179-180`. v2 campaign ran with `None` (verified from pkl) → every clearance check degrades to 0.0 no-op.
- A*-search legality (active only when set): passed as `CrossingRule(min_clearance_um=...)` at `grid.py:533` (`legal_crossing_candidate`) and `grid.py:547` (`endpoint_crossing_candidate`).
- Repair/detour heuristics: escape-track guard `max(grid_pitch, 2R, min_spacing, clearance)` at `physical.py:3209` (`_reserved_owner_local_envelope_replacements`), `:3330`, `:3457`, `:3612`, `:3853`; hard RoutingError for source detours at `physical.py:4605-4608` (`_validate_source_detour_crossings`); rejection reason for direct port approach at `physical.py:4871` (`_direct_port_approach_crossing_reason`).
- **Not checked anywhere in post-route DRC** (`validate_physical_routes` has no crossing-clearance rule) and template-mode routes (`emit_benes_template`) bypass all A* checks entirely. Verdict: making it a hard rule requires (i) setting the field non-None in campaign configs and (ii) a new DRC rule.

**B2 — hard-check hook points**
- `routing/crossing.py:41-47 legal_crossing_candidate(segment, route_grid, rules, owner_input, crossing_rule)` — clearance already enforced at `:74-78` via `_clearance_to_segment_endpoints` (`:217-218`, Manhattan to segment endpoints).
- `crossing.py:88-94 endpoint_crossing_candidate` — clearance at `:130-131` (crossed segment only, own-move endpoints exempt by design).
- Gotcha: at search time the candidate `segment` is a single grid move (≈8 µm), so "endpoint clearance" measures to grid nodes, not to final merged-segment bends — search-time enforcement is granular/asymmetric. The authoritative hard rule belongs in `validate_physical_routes` (drc.py:22): recompute crossings (`refinement.py:21`) and measure distance from each crossing to the nearest bend/endpoint on both merged routes; the DRCViolation plumbing (drc.py:38-45 pattern) is ready.

**B3 — db_tie_breaker-scaled crossing-adjacent turn penalties**
- `turn_timing_penalty_um` (types.py:108): geometry test `geometry.py:222-235 _turn_timing_penalty` (bend within `turn_guard_um` of hop src/dst); **scaling site `grid.py:846-859 _turn_timing_cost` — db mode returns `penalty_um * rules.db_tie_breaker_scale` at `grid.py:856`**; applied at `grid.py:653-654` (suppressed for I*/O* hops per `grid.py:290`).
- `turn_guard_um` (types.py:107) also gates `grid.py:870-884 _bend_placement_cost` via `_bend_near_sensitive_geometry` (`grid.py:892-914`, guard radius vs obstacles/port points/reserved regions).
- Generic guidance scaler: `grid.py:861-868 _search_guidance_cost` (`penalty_um * db_tie_breaker_scale` at `:865`); repeated-crossing guidance scaled at `grid.py:823`. `db_tie_breaker_scale=0.05` default (types.py:133).

## C. Dimension unification (dy ±4 → ±5.5) — plan B cascade

**C1 — consumers of port dy: single definition, flows cleanly**
- Definition: `core/models.py:52-55` (`default_add_drop_ports`, dy=±4.0); only construction site `placement/layout.py:25` (`build_cells`, also bbox 28×22 at `:35`); `port_xy` at `models.py:30-32`. Grep confirms no other hardcoded ±4.
- port-access math: `port_access.py:348-355 _port_stub_point`, `:290-304 _port_escape_point`, `:307-322 _port_route_point` — y always from `cell.port_xy`, x from bbox edges → dy flows automatically. VERDICT: no code change needed.
- benes_template residue rule: `benes_template_layout.py:21-22` `_SOUTH_WRAP_OFFSET_Y_UM=20 / _NORTH_WRAP_OFFSET_Y_UM=24`, comment claims stage-0 bus-to-wrap riser 12 µm (dy-free: wire_y±32 → cy∓20) and cell-side wrap risers ≥16 µm. Cell-side add-ingress riser = `SOUTH − dy` (`_emit_wire_route` ingress at `benes_template_layout.py:212-216` + `_ingress_anchor:163-170`): 16 → **14.5 µm at dy=5.5** — still ≥2R=10 (legal, G3 passes) but breaks the documented 16 µm (=turn_guard_um) margin; consider re-residuing to 21.5/25.5.
- fabric_loss internal lengths: `analysis/fabric_loss.py:147-152` uses `_mrr_internal_points` (`port_access.py:370-376`) — cross-state internal riser = 2·dy: 8 → 11 µm (+3 µm, +2 bends unchanged) per cross hop → worst-IL values shift. Side note: 11 ≥ 2R=10 finally makes the internal riser bend-radius-legal (8 < 10 today), which is presumably the point of 5.5.
- envelope: `routing/envelope.py:14-53` — `x_end_um` (`:33-34`), `y_top/y_bottom` (`:37-45`), `envelope_id` (`:48-53`) use no dy. `envelope_id` hardcodes "bbox28x22" (`:50`) — unchanged if bbox stays.
- DRC obstacle: `geometry.py:603-620 _routing_obstacle` = max(bbox half-extent, port offset); inflation `geometry.py:63` with `mrr_keepout_um` at `drc.py:29-31` / `physical.py:1080`.

**C2 — bbox 28×22 need not change: CONFIRMED**
- |dy|=5.5 ≤ height/2=11 and |dx|=8 ≤ width/2=14 → ports stay inside the obstacle, `_routing_obstacle` extents unchanged. Empirically verified: template P=4/8/16 and A* waksman n=4/6 all route DRC-clean at dy=5.5 (in-memory patch, repo untouched).

**C3 — goldens/gates that break (empirically checked at dy=5.5):**
- BREAK — geometry hashes: `tests/test_nsweep.py:117-132` G4 template hashes (measured new: P4 f7e5e6ac…, P8 932bbd21…, P16 ab823ddf…); `tests/test_routing_upgrade.py:97-118` waksman n4/n6 hash goldens; all v2 cached `geometry_sha256` (cache invalidates at `nsweep_campaign.py:285` → full re-route, expected).
- BREAK — IL reference gates: `tests/test_nsweep.py:235-248` (waksman n8 4.575739…), `:251-279` (padded_benes n8 4.749987…) — internal path length +3 µm/cross hop.
- BREAK — byte-wise CLI goldens: `tests/test_reports.py:40-41` vs `tests/golden/routing_summary.json` etc. (wiring lengths change) — regenerate.
- HOLD — G1 crossing counts: `predicted_template_crossings` (`benes_template_layout.py:25-29`) is a hardcoded dict {4:14, 8:60, 16:232} — zero dy dependence in the gate value; measured actual counts at dy=5.5: 14/60/232 ✓ (crossings come from riser/run interleaving; a ±1.5 µm row shift reorders nothing, and `_orthogonal_crossing_point` geometry.py:184-199 is strict-interior so no tangency flips).
- HOLD — G2/G3/G5/G6: DRC clean, min bend separation measured 12.0 ≥ 10 (`test_nsweep.py:98-114`), envelope ids dy-free, per-octave hash equality preserved (all sizes shift together).
- CAUTION — A*-lane crossing counts are NOT invariant: waksman n6 re-routed at dy=5.5 gave 30 crossings vs 32 in the v2 pkl (search re-decides); nsweep summary crossing columns will move even though template T(N) holds.

**C4 — waveguide width: natural home is RoutingRules**
- Add `waveguide_width_um` to `routing/types.py:97-159` (beside `min_spacing_um` at `:100`, whose semantics it modifies), + non-negativity in `_validate_rules` (`drc.py:152`). It is a routing/DRC semantic, not device geometry, so `types.py` beats `models.py`; it also auto-serializes into `fabric_routing_summary.json` via `asdict(rules)` (`nsweep_campaign.py:218`) and does NOT perturb geometry hashes (hash covers waypoints only, `fabric_reports.py:15-25`).
- Edge-to-edge consumers (all currently centerline): `geometry.py:72-88 _parallel_spacing_violation` (used by DRC `drc.py:71`, A* `grid.py:1222-1224`, RouteGrid `route_grid.py:138-143`, reserved-spacing `grid.py:960`); `drc.py:280-306 _same_net_min_spacing_violations`; `drc.py:309-325 _perpendicular_clearance_violation` via `drc.py:328-374 _axis_segment_clearance`; crossing clearance (`crossing.py:74-78, 130-131` + new DRC rule from B2); optionally obstacle keepout inflation (`drc.py:30`, `physical.py:1080`) by +width/2. Converting to edge-to-edge = subtract `waveguide_width_um` from measured centerline distances (or add it to thresholds) at exactly these sites.
