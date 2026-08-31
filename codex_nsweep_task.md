# Codex Task — N=3..12 Waksman vs Padded-Beneš Full Fixed-Fabric Sweep

> 中文摘要：對 N=3~12 的 Waksman 與 padded Beneš 做完整 fixed-fabric 實體繞線（B/C 兩種係數情境），每個 fabric 以「平行全枚舉」+「路徑枚舉法」兩種獨立方法計算 worst insertion loss 並要求互相吻合（演算法正確性的交叉驗證），全枚舉同時做 permutation coverage 檢查，最後畫出 worst-IL-vs-N 交叉圖（折線）與各 N 長條圖。分四個 phase，Phase 1 結束停下等人工審核。

<task>
Build and run an N-sweep campaign in /home/jchuang/Optical_switch producing outputs/nsweep_fixed_fabric_v1/ with, for every (topology ∈ {waksman, padded_benes}) × (N ∈ 3..12) × (config ∈ {B_db_placeholder, C_db_realistic}): a routed fixed fabric, DRC audit, EXACT worst-IL with a verifiable certificate, exhaustive permutation coverage, per-case artifacts and layout PNG; then cross-method agreement checks, summary charts, and REPORT.md. Execute in phases; STOP after Phase 1 for human review.
</task>

<context_map>
Everything from codex_routing_upgrade_task.md still applies (same repo, same entry points, flags now implemented). Configs:
- B_db_placeholder: cost_model="db", enforce_bend_spacing=True, legalize_port_access=True, coefficients prop=0.002, crossing=0.0, bend=0.0
- C_db_realistic: same flags + prop_loss_db_per_um=0.0002, crossing_loss_db_per_cross=0.1, bend_loss_db_per_bend=0.005
- rules base: replace(RoutingRules(), max_astar_pops=30000, max_ripup_passes=5, grid_margin_tracks=20); build_cells(stage_pitch_um=140, wire_pitch_um=64); x_start=20, x_end=max_cell_x+70, wire_pitch_um=64; s_table=load_mrr_s_table('mrr_sparam_library'); WaksmanTopology(n, strategy=WaksmanStrategy()), PaddedBenesTopology(n, strategy=BenesLoopingStrategy()).
Machine: 288 cores, 282 GB RAM, mostly idle, BUT another user job runs long-term — cap workers at 64 and run heavy passes under `nice -n 10`.
</context_map>

<measured_feasibility>
Probe results (scripts preserved in scratchpad — task1_timing.py, task2_path_enum.py, task2_certify.py, task3_*.py under /tmp/jchuang-tmp/claude-1237/-home-jchuang/37bb9dcf-f37d-47fc-8cf9-243de3f31363/scratchpad/ — REUSE their logic):
- Exhaustive worst-IL eval throughput ≈ 47k path-evals/s single core (waksman n8: 322,560 evals in 6.8 s). Projected single-core: n9≈70s, n10≈13min, n11≈2.6h, n12≈34h → with 64 workers: n12 ≈ 30-60 min. Parallelize by chunking permutation prefixes.
- Path-enumeration method VALIDATED at n8: waksman 176 paths, all realizable, path-max == exhaustive worst (4.575739) exactly. padded_benes 256 paths, 80 unrealizable under BenesLoopingStrategy (raw path-max overestimates by 0.063 dB), but realizable-max == exhaustive worst (4.749987) exactly. Conclusion: "worst IL" in this repo is strategy-relative; realizability must be checked against the deterministic strategy.
- n10 waksman path count = 436; certification-by-witness found the exact worst (5.892242, witness perm recorded) after descending through 6 unwitnessed candidates.
- padded benes with n_physical=16 (N=9..12) routing in dB mode took >60 min in the probe (exact time in scratchpad task3_route.log once finished) — schedule these 8 runs as the long pole, sequential or 2-way parallel.
</measured_feasibility>

<phases>
PHASE 0 — Infrastructure + correctness gates (no campaign runs yet)
1. mrr_switch_optimizer/app/nsweep_campaign.py: library-level runner (NOT via CLI subprocess), resumable per-case (skip cases whose artifacts already exist and validate), --only topo=,n=,config= filters, worker cap 64, nice 10.
2. Parallel exhaustive evaluator: one pass per fabric enumerating all N! permutations with multiprocessing (chunk = fixed first-two positions → N(N-1) chunks), computing BOTH (a) permutation coverage (verify_state_assignment + fabric-link containment, count failures) and (b) worst IL (max-reduce with per-chunk argmax, tie-break lexicographic smallest permutation for determinism). MUST reuse _evaluate_path_loss and the setup maps from evaluate_fixed_fabric_worst_insertion_loss — do not reimplement the loss model.
   Gate: at waksman n8 B, parallel result == single-process evaluate_fixed_fabric_worst_insertion_loss result exactly (worst IL float-equal, same argmax after tie-break).
3. Path-space evaluator: enumerate all I→O paths (branch th/drop at every entered MRR), IL per path via _evaluate_path_loss; realizability via (i) fast randomized witness search then (ii) DETERMINISTIC backtracking over partial permutations that proves realizable/unrealizable (no "5000 random tries" as proof — backtracking must terminate with a verdict; memoize on the strategy recursion where possible).
   Gates: waksman n8 → 176 paths, 0 unrealizable, max 4.575739; padded_benes n8 → 256 paths, exactly 80 proven-unrealizable, realizable-max 4.749987; waksman n10 → certified worst 5.892242 with a stored witness.
4. Witness certificates: for every case store worst_il_certificate.json {method, worst_il_db, worst_io_pair, witness_permutation, path_rank_table, unrealizable_proofs_count}; verifying a witness must be a one-call check (route the witness permutation's paths, recompute that path IL, assert equality) — implement --verify-certificates mode.
5. Unit tests in tests/test_nsweep.py covering the three gates above + determinism (same case rerun → identical geometry hash).
PHASE 0 exit: pytest green, gates pass.

PHASE 1 — Cheap half: N=3..8, both topologies, both configs (24 cases; all exhaustive single/low-parallel)
- Also cross-run the path-space evaluator on ALL 24 cases and record agreement in crosscheck/method_agreement.csv (worst IL both methods, equal true/false, gap).
- Golden continuity: waksman n4/6/8 B and C geometry hashes must equal the corresponding outputs/routing_upgrade_test values; padded benes n8 B/C likewise.
- Sanity: MRR counts == theory (W(n) = n*ceil(log2 n) - 2^ceil(log2 n) + 1; padded Beneš = (P/2)(2*log2 P - 1), P = next pow2); coverage failures == 0 everywhere; legacy DRC == 0 and bend_radius_legality == 0 for every case.
- STOP: write interim REPORT.md with the 24-case metrics table + n6 both-topology PNGs listed, and wait for human approval.

PHASE 2 — N=9..10 (both topologies/configs; benes now on 16-physical fabrics)
- Exhaustive (parallel) + path-method on every case; agreement required. n10 exhaustive ≈ minutes at 64 workers; benes-16 routing is the slow part.

PHASE 3 — N=11..12
- Path-method first (results available early), then parallel exhaustive as confirmation (n11 ≈ minutes-hour, n12 ≈ 30-90 min at 64 workers, nice'd). Both must agree; if they ever disagree, STOP and report the case verbatim.

PHASE 4 — Charts + report
- metrics.csv: one row per case (topology, N, config, n_physical, mrr, stages, routed/failed, legacy DRC, new-rule counts, total & worst-path crossings, worst path length, worst IL, method agreement, eval wall-clock, witness perm).
- charts/: worst_il_vs_n_B.png and _C.png (line plot, both topologies, mark crossover regions), worst_il_bars_B.png/_C.png (grouped bars per N), mrr_count_vs_n.png, worst_path_crossings_vs_n.png, win_margin_vs_n.png (Waksman-Beneš dB, positive = Beneš wins). matplotlib, no seaborn, readable at paper size.
- REPORT.md: full metrics table, the physical-layer win window per config (which N Waksman wins), comparison against the logical-layer window (N=9..12 win, crossover 12→13, from "N*N results/nxn_worst_il_v2p1_seed4*/logical_matrix/logical_matrix_summary.csv"), method-agreement summary, runtimes, anomalies.
</phases>

<action_safety>
- New artifacts only under outputs/nsweep_fixed_fabric_v1/; never touch outputs/waksman_fixed_fabric_*, outputs/fixed_fabric_*, outputs/routing_upgrade_test/, or "N*N results/".
- No git commit; leave changes unstaged and list them.
- Default-flag behavior unchanged; golden hash test must stay green; pytest green throughout.
- Long runs: background with logs under the campaign folder; cap 64 workers; nice -n 10; checkpoint after every case so the campaign is resumable.
</action_safety>

<default_follow_through_policy>
Proceed without asking on routine choices. STOP and report (instead of guessing) only for: any exhaustive-vs-path-method disagreement, any coverage failure, any routing failure (failed edges > 0), gate failures in Phase 0, or benes-16 routing exceeding 4 h per case (then propose alternatives rather than silently downgrading).
</default_follow_through_policy>

<structured_output_contract>
Phase 1 interim and final REPORT.md as specified above; final reply summarizes: win window per config at the physical layer, largest winning N per config, method-agreement record (must be 100%), total wall-clock, and files changed with one-line purposes.
</structured_output_contract>

<probe_final_addendum>
Final probe results (2026-08-13) that OVERRIDE earlier assumptions:
1. BLOCKER — padded_benes n_logical=9 (16-physical, 56 MRR) under B-config FAILED to route with the standard budget: 92.4 min wall-clock, 16/128 edges failed (2 of 16 waveguides; "A* pop limit exceeded" pops:30001 at hop s4_w9_13.drop->s5_w13_15.in, dominated by soft_repeated_crossing prunes). max_astar_pops is the binding constraint at 16 physical wires, NOT ripup passes. REMEDIATION (required): for n_physical=16 cases use an escalation ladder max_astar_pops ∈ [30000, 100000, 300000] (retry the case at the next rung only if edges fail), record the rung used in config.json and metrics.csv. Budget benes-16 routing at 1.5-4 h per case.
2. evaluate_fixed_fabric_worst_insertion_loss raises ValueError on ANY failed edge (fabric_loss.py:63-66). The campaign runner MUST write routing artifacts BEFORE attempting evaluation and must catch failures so a failed route still produces a routing report + DRC csv (marked status=route_failed) instead of crashing the case.
3. Timing revision: Waksman/Beneš N=9-12 have 7 stages (probe measured 5-stage fabrics); scale per-path cost by 7/5 → single-core eval: n9≈97s, n10≈18min, n11≈3.6h, n12≈47h → at 64 workers n12 ≈ 45-90 min. Waksman n12 routes clean in 6.7 min (403 s) with zero failures and zero bend violations (min bend spacing exactly 10.0 um).
4. Strategy pinning: the CLI switches to BruteForceLUTStrategy ONLY at n_logical=6 (padded benes n_MRR=20 sits exactly at the LUT guard). The campaign must pass constructive strategies (WaksmanStrategy / BenesLoopingStrategy) explicitly at EVERY n including 6, and REPORT.md must note that padded-benes n6 worst IL may therefore differ from older LUT-based artifacts (waksman n6 B was spot-checked identical: 3.784).
5. MRR-count theory check already passed for all n=3..12 both topologies (probe table) — keep the assertion in Phase 1 anyway.
</probe_final_addendum>

<benes16_remediation_ladder>
Diagnosis (2026-08-14): the pb9 failure is corridor saturation + search-budget shortfall, not geometric infeasibility. Inter-stage corridor = stage_pitch/grid_pitch ≈ 17.5 tracks minus cell+keepout ≈ 14 usable, vs 16 wires needing simultaneous vertical movement in distance-8 butterfly stages plus add/drop wrap overhead; 14/16 waveguides routed, the two failures are late-order braided pairs killed by soft_repeated_crossing prunes (2787) under a 30k pop budget that was tuned BEFORE enforce_bend_spacing enlarged the A* state space (direction × straight-run).
Escalation ladder for n_physical=16 cases (apply in order, record the rung in config.json + metrics.csv):
  R1. max_astar_pops 30000 → 100000 → 300000 (as already specified).
  R2. In db mode, when crossing_loss_db_per_cross == 0, weaken repeated-crossing pruning/guidance (e.g. scale soft_repeated_crossing prune threshold or set repeated_crossing_penalty guidance scale to 0.01) — crossings are free in the objective, so pruning the braided-pair solutions is self-defeating. Config-gated, default off, applied only at this rung.
  R3. Geometry relief: raise grid_margin_tracks 20 → 32 first (adds perimeter tracks, does not change cell spacing hence keeps comparisons fair); only if still failing, raise stage_pitch_um 140 → 200 FOR BENES-16 CASES ONLY and document prominently in REPORT.md that padded-Beneš then pays a longer-chip propagation penalty — this is a physically honest cost of padding, but it changes the IL comparison and must be labeled on every chart that mixes pitches.
If a case still fails at R3, mark status=route_failed, keep artifacts, continue the campaign; report "Beneš-16 unroutable under router X constraints" as a finding.
</benes16_remediation_ladder>

<envelope_template_amendment>
Supersedes: <benes16_remediation_ladder> R3 geometry escalation (retired); revises
Phase 0/1 gates and runtime budgets. New output root: outputs/nsweep_fixed_fabric_v2/
(v1 results use stage_pitch=140 and are not chart-comparable).

PHASE 0 additions (before any campaign runs):
0a. routing/envelope.py — OctaveEnvelope per P in {4,8,16}: wire_pitch 64, grid_pitch 8,
    grid_margin_tracks 20, stage_pitch from
    2*ceil8(cell_half_w + keepout + escape + runway + 2R) + (P-1)*8
    (216/152/120 for the 16x8 port cell; 232/168/136 for the current BBox(28,22) —
    pick per the real _routing_obstacle and record in envelope_id; RECOMMENDED: use the
    real _routing_obstacle bbox => 232/168/136). Both topologies at every N build cells
    and route inside their octave envelope: N in (2,4] -> benes-4 canvas, (4,8] ->
    benes-8 canvas, (8,16] -> benes-16 canvas; Waksman shares the same stage-column x
    positions (same 2*ceil(log2 N)-1 stages) and leaves slack tracks.
0b. routing/benes_template_layout.py — deterministic canonical layout for padded-benes
    (layout_mode="auto" in route_fixed_fabric; template when eligible, else A*):
    convention-A wrap (inbound south / outbound north, offsets h_s/h_n by the residue
    rule so every riser >= 2R), one riser track per wire per channel (rising nets
    sorted by descending departure y, falling by ascending; legalization topo-sort for
    same-y collisions), wrap rises share the wire's own track via disjoint y-spans.
    Emits FabricRoute-compatible waypoints incl. exact bus/port endpoint waypoints that
    _split_edge_routes requires; then _route_crossings + validate_physical_routes run
    unchanged (DRC stays the authority). stats.astar_calls == 0 for template cases.
0c. routing/guides.py + grid.py hook — per-net stage-corridor guides for A* cases
    (Waksman): corridor_guide_mode off|soft|hard (default off), soft band penalty +
    preferred_riser_x via the existing preferred_bend_x mechanism, hard = intersect the
    hop RoutingWindow with the corridor window; per-hop escalation hard->soft->off
    (never fail a hop because of a guide), mode recorded per case.
Gates (Phase 0 exit, in tests/test_nsweep.py):
  G1 template crossing counts == predicted_crossings: benes-4 == 14, benes-8 == 60
     (channel inversions [0,6,12,12,6,4]=40 + 20 wrap), benes-16 == 232.
  G2 DRC clean under campaign flags for template cases: legacy DRC == 0 AND
     bend_radius_legality == 0 AND same-net/perpendicular audits == 0.
  G3 bend legality by construction: min inter-bend spacing >= 10.0 um exactly,
     verified geometrically (no search).
  G4 determinism: two emissions -> identical geometry hash; astar_calls == 0.
  G5 envelope fairness: runner asserts one envelope_id per octave across all cases.
  G6 padding reuse: template geometry hash identical for all N in an octave
     (pb n5..8 == benes-8 layout; pb n9..12 == benes-16 layout), blocked ports flagged.
  G7 guides do no harm: waksman n<=8 with corridor_guide_mode=soft routes with
     0 failed edges and crossings <= A*-baseline; any increase is reported, not hidden.
Golden-continuity revision: outputs/routing_upgrade_test hashes remain the gate ONLY
for layout_mode=astar at the old 140-pitch config (router-code regression check);
template + v2-envelope cases get fresh Phase-0 goldens.

Runtime revision (replaces probe_final_addendum items 1 and 3's routing budgets):
  - padded-benes n9..12 routing: 92.4 min..4 h -> < 1 s/case (template; no A*).
    The benes16 escalation ladder R1/R2 applies only if layout_mode=astar is forced
    for cross-validation; R3 stage-pitch escalation is deleted (envelope is pinned).
  - Waksman routing stays A*: n<=8 minutes; n11/12 budget 10-30 min/case at the larger
    v2 envelope (140->232 pitch grows the grid; guides offset the pop pressure —
    keep the max_astar_pops ladder 30k/100k/300k for Waksman only).
  - New long pole: parallel exhaustive eval (n12 ~ 45-90 min at 64 workers), unchanged.
  - One-time A*-vs-template cross-check (Phase 1): route padded-benes n8 both modes at
    the v2 envelope; template must dominate or tie on (failed edges, DRC, crossings).
Chart rule: octave-boundary vertical markers at N=4->5 and 8->9 with a footnote that
the canvas steps up by design; never mix v1 (140-pitch) and v2 data on one chart.
</envelope_template_amendment>

<paper_narrative_charts>
The campaign's headline claim is: "Waksman keeps WIL comparable to the deterministic-layout
padded Benes while using strictly fewer MRRs at every N, with smooth W(n) scaling instead of
the padded stair-step" — NOT "Waksman has lower WIL" (falsified at power-of-two N earlier).
Charts must serve that narrative:
1. mrr_count_vs_n.png: W(n) = n*ceil(log2 n) - 2^ceil(log2 n) + 1 as a smooth-ish curve vs
   padded-Benes stair-step ((P/2)(2 log2 P - 1), flat within each octave); annotate savings %
   at every N (e.g. N=9: 21 vs 56 = -63%); this is the primary paper figure.
2. Add mrr_savings_pct_vs_n.png: (Benes-Waksman)/Benes MRR savings % vs N, marking octave
   boundaries — the "N*N scalability" figure (savings peak just above each power of two).
3. worst_il_vs_n_{B,C}.png: frame as PARITY evidence — same octave envelope, annotate the
   delta in dB at each N; do not visually exaggerate sub-0.1 dB differences (start y-axis
   at 0 or state the axis range in the caption data).
4. win_margin_vs_n.png stays (Waksman-Benes dB); REPORT.md must state explicitly where
   Waksman wins, ties, and loses on WIL, and lead its conclusion with the MRR-count and
   scalability claims backed by charts 1-2, with WIL parity as the supporting claim.
5. metrics.csv gains columns: mrr_waksman_theory, mrr_benes_padded, mrr_savings_pct.
</paper_narrative_charts>
