# Optical_switch Architecture Inventory (v3 prune/parameterize prep)

## 1. Module map

*(top-level `.claude/worktrees/agent-a0df568d9cb621174/` is a stale agent worktree duplicating the tree — excluded)*

### mrr_switch_optimizer/core/
| file | lines | responsibility / entry points |
|---|---|---|
| models.py | 78 | MRR datatypes + device port geometry. `default_add_drop_ports` (models.py:35), `port_for_wire`, `state_for_transition`, `MRRCell.port_xy` |
| topology.py | 378 | Topology families + stage builders. `PaddedBenesTopology:150`, `SpankeBenesTopology:175`, `SpankeBenesRectTopology:199`, `WaksmanTopology:233`, `build_*_stage_pairs` |
| state_assignment.py | 387 | Switch-state solvers. `BruteForceLUTStrategy:19`, `BenesLoopingStrategy:60`, `WaksmanStrategy:83`, `verify_state_assignment:125` |
| fabric.py | 292 | Fixed-fabric graph. `build_fabric_graph:63`, `validate_fabric_graph:156` |
| sparams.py | 83 | `MOCK_S_TABLE:7`, `load_mrr_s_table:26` (warn-fallback + backfill :43-60) |

### placement/
| file | lines | responsibility |
|---|---|---|
| layout.py | 38 | Nominal placement: `wire_y:7`, `build_cells:11` (hardcodes BBox 28×22 :35, d_min_th=24 :36) |
| lp.py | 192 | LP placement (legacy active-perm flow): `lp_placement:14`; **`lp_wiring_cost:92` is dead** |
| sa.py | 358 | SA placement: `sa_placement:15`, `is_feasible_placement:198` |

### routing/
| file | lines | responsibility |
|---|---|---|
| types.py | 222 | All routing datatypes; `RoutingRules:97` (every knob), `BEND_RADIUS_UM/BEND_ARC_UM:11-12` |
| geometry.py | 771 | Pure geometry + soft-penalty primitives; also **dead legacy L-shape wiring estimator** (:326-467) |
| **grid.py** | **1424 ⚑** | A* core `_astar_route:206`; step-cost model :775-890; failure-detail string formatting :1041-1160; `_build_route_grid:1163` |
| grid_router.py | 171 | A* state model: `RouterState:19`, `NeighborMove:27`, `HistoryCost:36`, `neighbor_moves:74` |
| route_grid.py | 144 | `RouteGrid:33` occupancy index + segment queries |
| crossing.py | 218 | Crossing legality: `legal_crossing_candidate:41`, `endpoint_crossing_candidate:88`, `CrossingRule:24` |
| port_access.py | 376 | Port escape/runway macro: `build_port_access_plan:93`, `_port_route_point:307`, `_mrr_internal_points:370` |
| **physical.py** | **5796 ⚑** | Router orchestrator: `route_physical_design:92`, net ordering/beam :1031-1330, hop candidates :2020-3700, source-hint negotiation :3638-4000, candidate validation :4006-4530, ripup :5732 |
| fabric.py | 439 | `route_fixed_fabric:87` (template/astar dispatch :115-151), edge splitting :261, `_preferred_waveguide_order:341` |
| benes_template_layout.py | 261 | Deterministic Beneš template: `emit_benes_template:32`, `predicted_template_crossings:25` |
| envelope.py | 99 | Octave canvas: `octave_envelope:66`, `_STAGE_PITCH_BY_OCTAVE:11` |
| refinement.py | 84 | `_route_crossings:21`, `_route_with_crossing_count:66` |
| drc.py | 440 | `validate_physical_routes:22`, `_validate_rules:152` (imports `_canonical_track` from grid.py — coupling) |
| guides.py | 45 | **Entirely dead** — see §2 |

### analysis/
| file | lines | responsibility |
|---|---|---|
| cost.py | 598 | Logical-layer IL/SXR: `evaluate_routing:90`, `aggregate_cost:188`; `ALPHA_DB_PER_UM=0.002:14`, `CROSSING_LOSS_DB_PER_CROSS=0.0:16` |
| nsweep.py | 501 | Worst-IL engines: `evaluate_fixed_fabric_parallel:180`, `evaluate_fixed_fabric_path_space:392`, `certificate_payload:478` |
| fabric_loss.py | 243 | Physical per-path IL: `evaluate_fixed_fabric_worst_insertion_loss:52` |
| fabric_coverage.py | 84 | `verify_permutation_coverage:25` |
| surrogate.py | 179 | Analytic surrogate (legacy flow): `analytic_edge_costs:32`; **`analytic_layout_cost:104` has no callers**; duplicates `_segments_cross:147` |
| activity.py | 113 | `scan_mrr_activity:25` (legacy demo path) |
| calibration.py | 190 | Ridge calibration: `calibrate_examples:63`, `kendall_tau:162` |
| **paper_figures.py** | **1031 ⚑** | Paper tables/figures: `generate_paper_artifacts:27`; duplicates BBox consts :16-17 |
| summary.py | 67 | Dataclasses only |
| task11_probe_artifacts.py | 352 | One-shot probe renderer; **zero importers** (standalone `main:35`) |

### output/, app/, top level
| file | lines | responsibility |
|---|---|---|
| output/visualize.py | 480 | Matplotlib renderers: `save_fixed_fabric_png:193`, `save_routing_png:109`, `save_routing_gif:143` |
| **app/cli.py** | **2382 ⚑** | All legacy CLI modes (`main:85`): `--fixed-fabric` (`_run_fixed_fabric:464`), `--paper-run:920`, `--physical-batch:1488`, `--logical-matrix:1232`, `_physical_rules_from_args:1965` |
| **app/nsweep_campaign.py** | **1130 ⚑** | v2 campaign driver: `run_campaign:765`, `_route_case:290` (pops ladder + hardcoded remediation :328-409), `_campaign_rules:113`, `CONFIGS:38`, `OUTPUT_ROOT:37` |
| app/fabric_reports.py | 143 | `fixed_fabric_geometry_hash:15`, `write_fixed_fabric_reports:28` |
| app/reports.py | 310 | CSV/JSON writers for legacy modes |
| app/routing_upgrade_validation.py | 596 | One-shot upgrade report (`main:86`); **zero importers** |
| main.py | 5 | shim → cli.main |
| Physical_output/draw_basic_mrr.py | 190 | standalone drawing demo |

### tests/ (line counts): **test_physical_router.py 3079 ⚑** (pins many `_private` router hooks), test_routing_upgrade.py 362, test_nsweep.py 317 (G1–G7), test_paper_figures.py 348, test_physical_batch.py 239, test_topology_nxn.py 189, test_fabric_graph.py 164, test_state_assignment.py 161, test_logical_dataset.py 139, test_fixed_fabric_router.py 134, test_calibration.py 104, test_cli_protocol.py 90, test_permutation_sampling.py 74, test_reproducibility.py 68, test_reports.py 41.

## 2. Dead/legacy candidates (grep-verified)

**(a) Zero call sites anywhere:**
- `routing/geometry.py:326 route_l_shape`, `:358 route_path_wiring`, `:401 count_routed_crossings`, `:447 _route_external_segment` — referenced only by each other and `__all__` (:31-35). Pre-A* wiring estimator. **PRUNE-SAFE.**
- `routing/guides.py` (whole module, 45 lines) — `corridor_guide`, `CorridorGuide`, `guide_fallback_order` have zero call sites. **Critically: `rules.corridor_guide_mode` is consumed nowhere in the router** — only guides.py:22 (dead), drc.py:217 (validation), and campaign metadata (nsweep_campaign.py:132,374,586). The corridor behavior that exists (`_corridor_preferred_x`, physical.py:877, applied at :1069) runs **unconditionally**. So the campaign's "soft guides for waksman" label and gate G7's soft-vs-off comparison (test_nsweep.py:160-192) compare identical routing. **PRUNE guides.py; fix or document the no-op flag** (G7 stays green either way).
- `analysis/surrogate.py:104 analytic_layout_cost` — only `__init__.py:5,52` export. **PRUNE-SAFE** (API removal only).
- `placement/lp.py:92 lp_wiring_cost` — zero refs. **PRUNE-SAFE.**
- `app/routing_upgrade_validation.py`, `analysis/task11_probe_artifacts.py` — zero importers, one-shot `python -m` scripts whose reports are already committed artifacts. **PRUNE-SAFE (archive to scripts/), keep git history.**

**(b) Legacy active-permutation comparison** (cli.py:509-537 inside `_run_fixed_fabric:464`): runs `route_physical_design` on one comparison permutation solely to emit `legacy_comparison.json` (fabric_reports.py:133). No test runs `--fixed-fabric`; test_fixed_fabric_router.py:89 only checks the file exists after passing its own dict `{"legacy": True}` (:64). README.md:72-112 documents the mode. **PARAMETERIZE**: make the `legacy_comparison` arg of `write_fixed_fabric_reports` optional and gate the cli block behind a flag; keep `--fixed-fabric` itself (README quickstart).

**(c) MOCK_S_TABLE** (sparams.py:7): **KEEP** — 30+ test call sites; test_reports.py golden CSVs generated with mock; `--sparam-dir MOCK` explicit path (cli.py:90-93). Risk item: warn-only silent fallback + silent backfill (sparams.py:43-60). **PARAMETERIZE**: add `strict=True` loading for campaign/GDS contexts (nsweep_campaign.py:27 uses the real library but would silently fall back if it went missing).

**(d) physical.py ↔ grid.py/grid_router.py/route_grid.py duplication** (grid_router/route_grid themselves are *not* duplicates — they're the state/occupancy model):
1. **Byte-identical helpers**: `_point_to_obstacle_distance` (grid.py:916 = physical.py:5252), `_point_to_axis_segment_distance` (grid.py:922 = physical.py:5258), `_turn_sign` (grid.py:937 = physical.py:5280). **PRUNE-SAFE** (import one copy).
2. **Cost model maintained twice**: A* step costs (grid.py:799-885) vs deterministic-candidate cost `_candidate_route_cost` (physical.py:5151-5197) re-derives length+bend-arc+dB-normalization independently. Divergence would silently split search vs. candidate ranking. **PARAMETERIZE** (shared cost module).
3. **Jog detection twice**: `_alternating_jog` (grid.py:955) vs `_candidate_jog_count` (physical.py:5199) / `_alternating_jog_windows` (physical.py:4318).
4. **Sensitive-bend detection twice**: `_bend_near_sensitive_geometry` (grid.py:892) vs `_point_near_sensitive_geometry` (physical.py:5228).
5. **Segment debug formatting ×3**: grid.py:200 `_format_segment_debug`, grid.py:1072 `_format_debug_segment`, physical.py:1547 `_format_segment`.
6. **String-protocol coupling**: grid.py formats failure details (:1078-1148) that physical.py **parses back with regex** (`_crossing_budget_owners:602`, `_reserved_owner_inputs:605`, `_crossing_sources:619`). Reroute decisions ride on message text. **PARAMETERIZE into structured exception payloads** (high value, high risk).
7. **Analysis-layer copy**: surrogate.py:133/147 `_segment_crossing_count`/`_segments_cross` duplicate geometry.py:201/427. KEEP-or-merge (low value).

**(e) CLI paths vs nsweep_campaign.py**: nothing in cli.py is superseded *and* unpinned except `--fixed-fabric`'s campaign role (nsweep_campaign owns campaigns now; `--fixed-fabric` survives as a demo). Default demo path pinned by test_reports.py goldens; `--paper-run`/`--paper-artifacts`/`--physical-batch` pinned by test_reproducibility/test_paper_figures/test_physical_batch; `--calibrate` feeds the paper pipeline. `--sa/--lp/--gif` (and activity/surrogate/reports.py) belong to the legacy active-permutation study — **KEEP but quarantine as "legacy paper flow"**; only `SpankeBenesRect*` and SA/LP have no fixed-fabric future.

## 3. Hardcoded constants → parameters for GDS readiness

| constant | location | note |
|---|---|---|
| **BBox(28, 22)** | placement/layout.py:35 — **duplicated** at envelope.py:25-26 (fields never passed to `build_cells`, envelope.py:85-91) and paper_figures.py:16-17; also baked into `envelope_id` string "bbox28x22" (envelope.py:50) | one `CellGeometry` source of truth |
| **port dx/dy (±8, ±4)** | models.py:52-55 (`default_add_drop_ports`) | v3 target dy ±5.5. Beware: test_fixed_fabric_router.py:50-52 asserts internal traverse `16.0` = 2·dx; `_mrr_internal_points` (port_access.py:370) and fabric.py:275-303 derive from these; changing dy moves **every** geometry hash |
| **waveguide width** | **absent everywhere** (grep: no `width_um`/`wg_width` in routing) — only `min_spacing_um=4.0` (types.py:100) as center-line proxy | add `waveguide_width_um` to RoutingRules + DRC edge-to-edge conversion |
| **d_min_th=24.0** | layout.py:36 | thermal spacing, only SA uses it (sa.py:337) |
| **bend arc convention** | arc = ½π·R **added** to full Manhattan length (never fillet-subtracted): types.py:11-12, geometry.py:351-354, grid.py:829-837, physical.py:2009/5167/5195/5578, fabric.py:275, benes_template_layout.py:240, fabric_loss.py:141 | 8 sites, one convention; GDS fillet replaces 2R of corner with ½πR (Δ = −0.43R/bend) — extract one helper + document |
| **crossing footprint** | none — crossings are zero-area points (`RouteCrossing` types.py:179). Clearance knob exists but defaults off: `RoutingRules.min_crossing_clearance_um=None` (types.py:124) → `CrossingRule.min_clearance_um` (crossing.py:28, enforced :74-77,130) wired at grid.py:533,547 and physical.py:3209-4871. DRC only checks the *config value* is non-negative (drc.py:179-180) — **no post-route crossing-clearance DRC rule exists** | v3's `min_crossing_clearance` hard rule = new check in drc.py + non-None default |
| **db_tie_breaker_scale=0.05** | types.py:133; applied via `_search_guidance_cost` (grid.py:861-868) and `_turn_timing_cost` (grid.py:846-859) | **lumps these penalties** (all in `_astar_route`): ① deferred/reserved-crossing avoidance `8.0×hairpin_penalty_um` (grid.py:622) — guidance; ② backtrack penalty (grid.py:627 ← geometry.py:208) — guidance; ③ same-net hairpin penalty (grid.py:628 ← geometry.py:236) — **physical-constraint proxy** demoted to guidance; ④ hairpin soft penalty (grid.py:632) — same; ⑤ soft-blocker congestion (grid.py:637 ← grid.py:1022) — guidance; ⑥ preferred-bend-x deviation `0.05·|Δx|` (grid.py:657) — guidance; ⑦ history/negotiation cost (grid.py:662) — guidance; ⑧ reserve-space lookahead (grid.py:668) — guidance; ⑨ turn-timing (grid.py:654 → :856) — guidance proxy for bend placement; ⑩ repeated-crossing *guidance component* `repeated_crossing_penalty_um×scale` stacked on the physical `repeated_crossing_loss_db` (grid.py:821-824) — mixed. **Not** scaled (true physical): length (:799), crossing loss (:807), bend arc+loss (:829), jog dB (:839), bend-placement dB (:870). ③/④ deserve promotion to hard rules rather than a shared scalar |
| loss-model defaults | cost.py:14-16 (`ALPHA=0.002`, `CROSSING=0.0` — placeholder physics as defaults; realistic values only in nsweep_campaign.py:44-48 CONFIGS) | make config-driven, keep B/C presets |
| campaign literals | nsweep_campaign.py:37 `OUTPUT_ROOT` v2 path, :328-333 pops ladder + `known_trigger`, :398-405 hardcoded n∈{11,12} remediation, envelope pitches `_STAGE_PITCH_BY_OCTAVE` (envelope.py:11) | parameterize for v3 campaign |

## 4. Golden-critical — must stay bit-identical under default flags

- **test_routing_upgrade.py:97-119** — Waksman N4/N6/N8 geometry hashes under **default `RoutingRules()`** (um_penalty, astar). Pins: `RoutingRules` defaults (types.py:97-159), the entire A* path — grid.py (`_astar_route` + all step costs), physical.py (ordering :1031, hop candidates, ripup :5732, string-parsed hints :602-648), crossing.py, port_access.py, geometry.py penalty helpers, route_grid.py, grid_router.py — plus fabric.py `route_fixed_fabric`/`_preferred_waveguide_order:341`, layout.py `build_cells` (pitches, x0), **models.py port dx/dy**, core/fabric.py `build_fabric_graph`, and the hash serialization `fixed_fabric_geometry_hash` (fabric_reports.py:15).
- **test_nsweep.py G1-G7** — G1-G4 pin `benes_template_layout.py` (template emission, `predicted_template_crossings`), envelope.py octave pitches, G4 template geometry hashes :117-132 (also asserts `astar_calls==0`); G5/G6 pin `octave_envelope`/blocked-port padding; G7 pins astar Waksman routability (and currently only *documents* the guide no-op).
- **test_nsweep.py:224-317 reference gates** — float-exact IL values pin `analysis/fabric_loss.py` arithmetic, `analysis/nsweep.py` (parallel + path-space), `mrr_sparam_library` loading, and old-pitch (140/64) build_cells geometry.
- **test_reports.py** golden CSVs — pin the default cli demo: cost.py logical model, state_assignment strategies, topology stage builders, reports.py writers, with MOCK physics.
- Also pinned: test_routing_upgrade.py:137-166 (db cost arithmetic incl. `db_tie_breaker_scale` semantics), test_fixed_fabric_router.py:50-52 (2·dx=16 internal traverse).
- **Consequence for v3**: dimension unification (dy ±4→±5.5, BBox, wg width) **cannot** be bit-identical — it must land as a versioned geometry change with regenerated goldens, while the prune itself (dead code, dedup, flag-gating) must not move any of the above hashes when defaults are unchanged.

## 5. Prune/parameterize priority list (top 10)

| # | action | class | risk |
|---|---|---|---|
| 1 | Delete geometry.py legacy wiring trio + `_route_external_segment` (:326-467) | PRUNE | **Low** — zero callers |
| 2 | Delete guides.py; either wire `corridor_guide_mode` for real or rename campaign metadata to reflect the always-on `_corridor_preferred_x` | PRUNE + doc fix | **Low code / Medium reporting** — v2 report claims soft guides |
| 3 | Consolidate byte-identical helpers (grid.py:916/922/937 ↔ physical.py:5252/5258/5280) + 3 segment formatters | PRUNE | **Low** — pure moves, hash-verifiable |
| 4 | Single `CellGeometry`/PDK dataclass: BBox, d_min_th, port dx/dy, **new waveguide_width_um**; thread through layout.py/envelope.py/paper_figures.py; then flip dy→±5.5 behind explicit version + regenerate goldens | PARAMETERIZE | **High** — every geometry hash moves at flip; low if defaults kept first |
| 5 | `min_crossing_clearance_um`: non-None default + new hard DRC check in drc.py (currently search-only, default off) | PARAMETERIZE | **Medium** — routability at N≥10 waksman; needs campaign re-run |
| 6 | Extract shared cost model (grid.py:799-885 ↔ physical.py:5151) into one module | PARAMETERIZE | **Medium** — must be float-identical; hashes are the gate |
| 7 | Replace grid→physical failure-string protocol with structured payloads (grid.py:1041-1160 ↔ physical.py:602-648) | PARAMETERIZE | **High** — reroute negotiation rides on message text; do after v3 goldens stabilize |
| 8 | Unbundle `db_tie_breaker_scale`: promote same-net hairpin penalties (items ③④) to hard candidate rules, keep one guidance scale for congestion/history items | PARAMETERIZE | **Medium** — search behavior shifts; G-gates + campaign re-cert |
| 9 | Gate cli.py legacy comparison (:509-537) behind `--legacy-comparison`; optional param in `write_fixed_fabric_reports` | PRUNE-SAFE | **Low** — file-existence test uses its own dict |
| 10 | Archive `routing_upgrade_validation.py` + `task11_probe_artifacts.py` to scripts/; strict s-table loading (no silent MOCK fallback) for campaign/GDS entry points | PRUNE + PARAMETERIZE | **Low** |

Deferred but recommended: split physical.py (5796 lines) along its existing seams (ordering/beam :1031-1330, hop candidates :2020-3700, source hints :3638-4000, validation :4006-4530, ripup :5589-5796) — mechanical, but touch it only with hash gates green; and prune `analytic_layout_cost`/`lp_wiring_cost` exports (trivial).
