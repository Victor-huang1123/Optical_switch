# Codex Task — Fixed-Fabric Router Upgrade (cost unification + bend-radius legality + DRC blind spots)

> 目的（中文摘要）：修正 fixed-fabric router 的三個已量化問題 —— (1) A* 成本與 loss 模型不一致（router 用 µm 等效罰項閃 crossing，loss 模型卻記 crossing 0 dB）、(2) 彎折半徑在搜尋與 port 存取模板中無約束（committed n8 幾何有 11 對 bend 間距 < 2R，r=5 µm 圓角物理上畫不出來）、(3) DRC 三個盲區（同 net 間距豁免、垂直近接、local 段 bend 間距豁免）。先小規模（4×4/6×6）驗證並輸出 PNG 供人工審核，**Phase 3 結束即停**，不要自行進入大規模階段。

<task>
Implement the router upgrade described below in /home/jchuang/Optical_switch, run the small-scale validation (Waksman 4x4 and 6x6 only), emit comparison PNGs and a metrics report, then STOP for human review. Do NOT run 8x8 or any padded-Benes sweep in this session; that is Phase 4 and requires explicit approval.
</task>

<context_map>
Repo: /home/jchuang/Optical_switch (Python, `pyproject.toml`; run everything via `python -m` / `python main.py` from repo root).

Key files:
- `mrr_switch_optimizer/routing/types.py:99-134` — `RoutingRules`: min_spacing_um=4.0, mrr_keepout_um=6.0, port_escape_um=10.0, crossing_penalty_um=20.0, repeated_crossing_penalty_um=400.0, backtrack_penalty_um=80.0, hairpin_penalty_um=200.0, bend_radius_um=5.0, grid_pitch_um=8.0, prop_loss_db_per_um=0.002 (from `analysis/cost.py:14`), bend_loss_db_per_bend=0.0, crossing_loss_db_per_cross=0.0 (from `analysis/cost.py:16`).
- `mrr_switch_optimizer/routing/physical.py` (5421 lines) + `grid_router.py` / `route_grid.py` / `grid.py` — A* router. Cost terms are µm-equivalent penalties.
- `mrr_switch_optimizer/routing/port_access.py` — port stub (2 µm) / escape (10 µm) template; this template generates bends near cells.
- `mrr_switch_optimizer/routing/drc.py` — 8 centerline rules. Blind spots: same-net pairs exempt from overlap/spacing (`:58-59`); `_external_bend_pairs_too_close` (`:232-271`) skips bends at local-segment endpoints and never checks local segments; parallel-only spacing (perpendicular near-miss unchecked).
- `mrr_switch_optimizer/routing/fabric.py` — `route_fixed_fabric` (fixed-fabric entry; routes per-waveguide bar-state buses once).
- `mrr_switch_optimizer/analysis/fabric_loss.py` — loss model: IL = prop_loss_db_per_um × length + bend_loss × bends + crossing_loss × crossings + Σ s_table. THIS is the objective the router must optimize.
- `mrr_switch_optimizer/app/cli.py:464-510` — `_run_fixed_fabric`; CLI defaults `:664-668` (fabric-stage-pitch 140, fabric-wire-pitch 64, max-astar-pops 30000, max-ripup 5, grid-margin 20) and `:742-748` (x-start 20, x-end-margin 70).
- `mrr_switch_optimizer/output/visualize.py:193` — `save_fixed_fabric_png`.
- Realistic loss coefficients used elsewhere in this repo: `configs/nxn_worst_il_v2p2_hieffort.yaml:44-45` (0.005 dB/bend, 0.1 dB/crossing).
</context_map>

<baseline_verification>
Before changing any code, reproduce the audited baseline to prove your harness is sound. Build Waksman 8x8 with EXACT CLI defaults (WaksmanStrategy, build_cells(stage_pitch_um=140, wire_pitch_um=64), rules = RoutingRules() with max_astar_pops=30000, max_ripup_passes=5, grid_margin_tracks=20, x_start=20, x_end = max cell x + 70, wire_pitch_um=64, sparam dir `mrr_sparam_library`). Expected, previously verified numbers:
- `fixed_fabric_geometry_hash(result)` == dbee43715e20f6f1... (must match `outputs/waksman_fixed_fabric_n8/fixed_fabric/fabric_routing_summary.json` geometry_sha256)
- min cross-net segment distance (excluding true crossings, d>1e-6) = 2.0 µm (perpendicular/endpoint proximity, ext-ext)
- min same-net non-adjacent segment distance = 4.0 µm
- consecutive-bend pairs with Manhattan spacing < 2R=10 µm: 11 (min 4.0 µm), scanning full route waypoints
If any baseline number differs, STOP and report the discrepancy instead of proceeding.
</baseline_verification>

<phases>
PHASE 0 — DRC blind-spot rules (make problems measurable first)
Add three new DRC rule classes to `routing/drc.py`, each individually toggleable via new `RoutingRules` fields (all default OFF so existing behavior and golden hashes are unchanged):
1. `same_net_min_spacing` — non-adjacent same-net segment pairs closer than `min_spacing_um` (skip pairs sharing an endpoint and pairs whose indices are adjacent along the polyline).
2. `perpendicular_clearance` — perpendicular different-net segment pairs that approach closer than `min_spacing_um` WITHOUT actually crossing (a true orthogonal crossing is legal; a near-miss/T-approach is not).
3. `bend_radius_legality` — ANY consecutive bend pair (external AND local segments, no endpoint exemptions) with centerline spacing < 2×bend_radius_um.
Add a unit test that runs these rules against the reproduced baseline n8 geometry and asserts the expected violation counts (>0 for rules 1 and 3; report the count for rule 2). This test documents the blind spots; it must not run the new rules in default pipelines.

PHASE 1 — Cost unification (router optimizes dB, same objective as fabric_loss.py)
Add a `RoutingRules` switch, e.g. `cost_model: Literal["um_penalty","db"] = "um_penalty"`. In "db" mode the A* edge cost is:
  cost_db = prop_loss_db_per_um × length_um + bend_loss_db_per_bend × (new bend?) + crossing_loss_db_per_cross × (new crossing?)
converted to the router's internal µm unit by dividing by prop_loss_db_per_um (i.e. crossing_penalty_um_effective = crossing_loss_db_per_cross / prop_loss_db_per_um, bend penalty analogous). Keep hairpin/backtrack/repeated-crossing penalties as SMALL tie-breakers in db mode (scale them down, e.g. ×0.05, or make them configurable) so degenerate zigzags are still avoided — document the chosen tie-breaker scale. "um_penalty" mode must remain bit-identical to today (golden hash test).

PHASE 2 — Bend-radius legality at generation time
Two mechanisms, both behind flags:
1. `enforce_bend_spacing: bool = False` — A* state extended with (incoming direction, straight-run length since last turn); a turn is only expandable when the straight run since the previous turn ≥ 2×bend_radius_um. With grid_pitch_um=8.0 < 2R=10.0 this means ≥ 2 grid steps between turns (ceil(2R/pitch)). Handle the start-of-net and port-junction cases explicitly.
2. Port-access legalization: the stub/escape template in `port_access.py` must produce port-adjacent geometry whose bends also satisfy 2R spacing. If the current 2 µm stub cannot satisfy it, extend the stub/escape distances for the wrap (parameterized, default preserving old values). Alternative acceptable design: a fixed pre-legalized per-port-side access macro that A* connects to at the escape point. Choose ONE design, state why, implement it.
Acceptance: with both flags on, the Phase-0 `bend_radius_legality` rule reports 0 violations on n4 and n6.

PHASE 3 — Small-scale validation (THE STOP POINT)
Run Waksman 4x4 and 6x6 fixed-fabric with these configs (real sparam library, all other CLI defaults):
  A. legacy: cost_model=um_penalty, no bend enforcement  (baseline; must reproduce committed hashes dc7b62ec... / 0e3bb955...)
  B. db-placeholder: cost_model=db with current coefficients (prop 0.002, crossing 0, bend 0) + enforce_bend_spacing + legalized port access
  C. db-realistic: cost_model=db with prop_loss_db_per_um=0.0002, crossing_loss_db_per_cross=0.1, bend_loss_db_per_bend=0.005 + enforce_bend_spacing + legalized port access
For each (size × config): write `fixed_fabric_layout.png` via `save_fixed_fabric_png` and the routing/loss summaries into `outputs/routing_upgrade_test/{n4,n6}/{A_legacy,B_db_placeholder,C_db_realistic}/`. Never write into the existing `outputs/waksman_fixed_fabric_*` or `outputs/fixed_fabric_*` directories.
Then STOP and emit the report defined in <structured_output_contract>. Do not proceed to 8x8 / Benes comparison (Phase 4 happens only after human approval of the PNGs).
</phases>

<action_safety>
- Work only on the files needed for the phases above; no unrelated refactors, no formatting sweeps.
- The working tree already contains uncommitted pre-existing changes (an old package refactor). Do NOT revert, commit, stash, or "clean up" anything you did not author. Do not run `git commit` at all; leave your changes unstaged and list them in the report.
- All new behavior behind flags defaulting to legacy; the three committed geometry hashes (dc7b62ec…, 0e3bb955…, dbee4371…) must still reproduce with default flags — add/keep a golden test asserting the n4 hash.
- Run the existing test suite (`pytest tests/ -x -q`) and keep it green; keep mypy clean if the repo currently is (`mypy mrr_switch_optimizer`).
</action_safety>

<default_follow_through_policy>
Proceed without asking for routine decisions (naming, file placement, test structure). Stop and report instead of guessing only for: baseline mismatch (see <baseline_verification>), a design fork that changes public CLI behavior beyond adding flags, or inability to reach 0 bend-radius violations on n4/n6 without >20% length regression.
</default_follow_through_policy>

<verification_loop>
After each phase: run the phase's acceptance checks and the golden-hash test before moving on. After Phase 3: re-run the full measurement harness (same metrics as baseline) on all six outputs and confirm config A matches committed hashes exactly.
</verification_loop>

<structured_output_contract>
Final report (markdown, save as `outputs/routing_upgrade_test/REPORT.md` and print inline):
1. Metrics table — rows = (n4, n6) × (A, B, C); columns = routed/failed edges, DRC(old rules), DRC(new rules: same_net / perpendicular / bend_radius counts), total crossings, worst-path crossings, worst-path length µm, worst IL dB, bend pairs < 2R, min same-net spacing µm, min cross-net perpendicular clearance µm, wall-clock s.
2. PNG paths for all six layouts.
3. Baseline reproduction confirmation (hash + the three blind-spot numbers).
4. Design decisions taken (tie-breaker scaling in db mode; port-access legalization design and why).
5. Files changed (path + one-line purpose each).
6. Open risks / anything deferred to Phase 4.
</structured_output_contract>
