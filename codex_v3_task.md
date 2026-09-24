# Codex Task v3 — Prune/Parameterize, Jog Straightening, Crossing Manufacturability, Dimension Unification

> 中文摘要：五個 phase。P0 先把 v2 戰役收尾（條款修正後續跑 + 圖表 + final_results）。
> P1 架構修剪與參數化（hash 中立，golden 不得動）。P2 對 v2 產物做離線分析：jog 拉直
> 原型 + crossing 可製造性掃描（不重繞）。P3 依 P2 數據決定是否把兩者進管線（config-gated）。
> P4 尺寸統一方案 B（dy ±4→±5.5、線寬參數化，版本化幾何變更、重生 golden）。
> P5 小規模 v3 驗證後停下等人工審核。全程單線作業，禁止與跑動中的 campaign 並行改 code。

<reference_docs>
Read FIRST (both at repo root; they contain every file:line anchor this task relies on):
- codex_v3_survey_architecture.md — module map, dead-code evidence, constants inventory,
  golden-critical list, top-10 prune/parameterize priorities with risk ratings.
- codex_v3_survey_anchors.md — verified feasibility anchors for straightening (pkl round-trip,
  legality primitives), min_crossing_clearance hook points, and the dy=5.5 cascade
  (EMPIRICALLY verified: template P4/8/16 + waksman n4/n6 route DRC-clean at dy=5.5;
  G1 counts hold at 14/60/232; new template hashes measured).
</reference_docs>

<phases>
PHASE 0 — Finish the v2 campaign (blocking; nothing else may touch routing code meanwhile)
Resume the paused campaign with the gate-semantics correction:
- Verification (a) verdict = PASS: for A*-routed cases the HARD DRC bar is legacy==0 AND
  bend_radius_legality==0 (waksman n10-C achieved 0 failed edges, 0/0). same_net_min_spacing
  and perpendicular_clearance are measurement-only audit counts (inclusive 4.0 um boundary)
  per the task file — n10-C's 64/11 is in-family (n9-C: 98/22). The all-green wording applied
  to template cases only.
- Still mandatory before continuing: verification (b) 3-case checkpoint-hash re-check and
  (c) golden + G1-G7 + full pytest. Additionally explain in REPORT design-decisions why the
  n10-C rerun succeeded at 300k pops WITHOUT the remediation trigger firing when the pre-fix
  300k rung failed identically (identify the exact fallback code-path difference).
- Then: remaining cases (padded_benes n10-12 template, waksman n11/n12 with the same-type
  auto-remediation rule), Phase 3 dual-method evals, Phase 4 charts per paper_narrative_charts,
  metrics.csv, REPORT.md, assemble final_results/.
- REPORT INTEGRITY FIX (from the architecture survey): corridor_guide_mode is a NO-OP — the
  flag is consumed nowhere; the always-on _corridor_preferred_x (physical.py:877) runs in every
  mode, so G7 compared two identical routings and the v2 claim "soft guides for waksman" is
  wrong. Correct the REPORT wording (guides = always-on preferred-bend-x bias; no gated guide
  layer exists), and adjust test_nsweep.py G7 to document reality. Do NOT implement a real
  guide layer now.

PHASE 1 — Architecture prune & parameterize (HASH-NEUTRAL: all goldens, G1-G7, and the three
committed geometry hash families must be bit-identical after this phase; full pytest green)
Execute survey priority items (see codex_v3_survey_architecture.md §5) in this order:
1. Delete dead code: geometry.py:326-467 legacy wiring trio; guides.py (whole module) and the
   corridor_guide_mode flag (coordinate with the P0 report fix); surrogate.analytic_layout_cost;
   placement/lp.py lp_wiring_cost; archive routing_upgrade_validation.py and
   task11_probe_artifacts.py to scripts/ (git mv).
2. Consolidate byte-identical helpers (grid.py:916/922/937 == physical.py:5252/5258/5280) and
   the 3 segment-debug formatters into one location each.
3. Introduce a single CellGeometry dataclass (bbox w/h, port dx/dy, d_min_th, waveguide_width_um)
   as the one source of truth, DEFAULTS UNCHANGED (28,22, ±8, ±4, 24.0, width=None). Thread it
   through placement/layout.py, envelope.py (whose bbox fields are currently never passed!),
   analysis/paper_figures.py:16-17. envelope_id derivation reads from CellGeometry.
4. Gate the cli.py legacy comparison (:509-537) behind --legacy-comparison (default off);
   make write_fixed_fabric_reports' legacy arg optional.
5. Strict s-table loading: load_mrr_s_table(strict=True) raises instead of MOCK-fallback;
   campaign + future GDS entry points use strict; tests/demo keep the warn path.
6. Parameterize campaign literals: OUTPUT_ROOT, pops ladder, remediation trigger list,
   _STAGE_PITCH_BY_OCTAVE — all constructor/config args of run_campaign.
DEFERRED to v4 (do NOT do now; record in ARCHITECTURE.md): shared cost-model extraction
(survey item 6), structured failure payloads replacing the grid->physical string protocol
(item 7), db_tie_breaker unbundling / promoting same-net hairpin penalties to hard rules
(item 8), physical.py split along its seams.
Deliverable: ARCHITECTURE.md at repo root — module map (from the survey, updated), what was
pruned/parameterized with one-line rationale each, and the v4 deferred list.

PHASE 2 — Offline analyses of v2 artifacts (read v2 pkls; NO re-routing, NO code-path changes)
2a. Jog straightening prototype (new module routing/straighten.py + scripts/straighten_v2_offline.py):
    - Detect alternating turn triples (LRL/RLR) and longer monotone staircases from waypoints
      (turn sign = cross product of consecutive segment vectors).
    - Rewrite candidates: both L variants (x-first / y-first) between anchor waypoints.
    - MUST preserve: edge endpoint waypoints (fabric.py _split_edge_routes locates by waypoint
      identity — see anchors doc A1 gotcha), port stub/runway/local segments (anchors only on
      external segments), same-net no-touch, 2R bend spacing at the new corner.
    - Legality via existing primitives (anchors doc A2 table): swept-rectangle-empty per
      _route_segment_available with blockers rebuilt from the other routes; then full
      validate_physical_routes re-run.
    - Acceptance per rewrite: all-legal AND delta-loss <= 0 under THAT case's coefficients
      (crossings recounted via refinement._route_crossings — the L variant may add/remove
      crossings; pick the best legal variant).
    - Iterate round-robin over nets to fixpoint, deterministic order (input_port), cap 10 rounds.
    - Run OFFLINE over all completed v2 cases: report per case bends_removed, length_saved_um,
      crossings_delta, WIL_delta (re-evaluate worst IL on modified geometry), runtime.
    - SANITY GATE: template cases must be zero-change.
2b. Crossing manufacturability scan (scripts/crossing_scan_v2.py -> crossing_manufacturability.csv):
    for every crossing in every v2 case: distance to nearest other crossing, and the straight
    clearance of all 4 arms (distance from crossing point to nearest bend/endpoint on both
    merged routes). Summarize violations at clearance thresholds {5, 10, 15} um.
Deliverable: outputs/v3_offline_analysis/{straighten_report.md, crossing_manufacturability.csv}.
STOP-AND-REPORT if 2a shows template changes or any legality regression.

PHASE 3 — Decision-gated pipeline integration (data-driven from Phase 2)
3a. IF the scan shows arm-clearance violations at 10 um in A* cases (expected): implement the
    authoritative post-route DRC rule crossing_clearance (new check in validate_physical_routes:
    recompute crossings, measure to nearest bend on BOTH routes, threshold =
    min_crossing_clearance_um) + set min_crossing_clearance_um non-None in v3 campaign configs
    (10.0 initial) + un-scale turn-guard near crossings in db mode (reclassify as physical
    constraint — see anchors doc B3 sites; config-gated, default legacy).
    IF zero violations: keep rule default-off defensive, document.
3b. IF 2a shows net-positive deltas (expected): wire straighten pass into route_fixed_fabric as
    post-route step, flag straighten_jogs (default False), stats recorded per case.
PHASE 3 exit: new unit tests for both features; all v2 goldens still green under default flags.

PHASE 4 — Dimension unification plan B (VERSIONED geometry change; goldens regenerate here and
only here)
- CellGeometry: port dy ±4 -> ±5.5 (fits r=5 um ring: bus separation 11 um); set
  waveguide_width_um = 0.45; re-residue template wrap offsets 20/24 -> 21.5/25.5 (anchors doc C1);
  envelope_id gains "dy5p5-w450" (bbox stays 28x22 — verified ports remain inside obstacle).
- Convert spacing DRC semantics to edge-to-edge at the sites listed in anchors doc C4 (subtract
  width from measured centerline distances); min_spacing_um stays 4.0 centerline-equivalent
  (i.e., threshold becomes 4.0 - width where applicable) — document the chosen convention.
- Expected (verify, do not copy): G1 crossing counts UNCHANGED 14/60/232; template hashes change
  (survey measured f7e5e6ac/932bbd21/ab823ddf as reference points — re-derive independently);
  IL reference gates re-referenced (+3 um per cross-state hop, internal riser 8->11 um which
  FIXES the latent bend-radius illegality of the old 8 um < 2R internal riser — document this);
  test_reports.py golden CSVs regenerate.
- All regenerated goldens land in one commit-able unit with a GEOMETRY_V3.md changelog.

PHASE 5 — v3 mini-validation, then STOP
- Run n4/n6/n8 x {waksman, padded_benes} x {B, C} with v3 features on (straighten_jogs=True,
  min_crossing_clearance=10.0, dy=5.5, width=0.45) into outputs/nsweep_fixed_fabric_v3_mini/.
- Deliver: v2-vs-v3 comparison table (WIL, bends, crossings, manufacturability all-clear,
  same-net/perpendicular audit counts), all PNGs, and a one-page delta explanation.
- STOP for human review. The full v3 N=3-12 campaign runs only after approval.
</phases>

<action_safety>
- NEVER modify routing/analysis code while a campaign process is running (check ps first).
- Phase 1 is hash-neutral: assert the three v2 hash families + G1-G7 + full pytest after
  every prune step; any drift -> revert that step and report.
- Phase 4 is the ONLY place goldens regenerate; keep old goldens retrievable (rename _v2).
- v2 artifacts under outputs/nsweep_fixed_fabric_v2/ are read-only history from Phase 2 onward.
- No git commit; leave changes unstaged and list them.
</action_safety>

<default_follow_through_policy>
Proceed on routine choices. STOP and report verbatim for: any Phase-1 hash drift, template
non-zero-change in 2a, straightening legality regression, gate failures, or if Phase 4
re-residue cannot keep all template risers >= 2R.
</default_follow_through_policy>

<structured_output_contract>
Final reply per phase completed: what was done, gate results, files changed (path + one line),
deferred items. Phase 5 ends with the v2-vs-v3 table + PNG paths + open risks for the full
v3 campaign.
</structured_output_contract>
