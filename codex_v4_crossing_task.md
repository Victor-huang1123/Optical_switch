# Codex Task v4 — Complete the Crossing Mechanism, Search-Discipline Fixes, Placement Experiments

> 中文摘要：v4 的目標是終結「規避-纏繞螺旋」（C 配置：天價 crossing 罰項 → 猛彎閃避 →
> 閃不掉還是交叉 → 纏繞自身 → route_failed）。四個修正 + 一組放置實驗，然後單一一次
> 完整戰役（v3 幾何 + v4 搜尋）。預期成果：n10/11/12-C 的 route_failed 洞被補回。
> 前置條件：v3 mini 已完成且人工審核通過；無 campaign 進程在跑。

<evidence_anchors>
All prior evidence in this repo:
- n4-B crossing-arm violations at (181,153) and (304,69.5) in
  outputs/nsweep_fixed_fabric_v3_mini/cases/waksman/n04/B_db_placeholder/ — both in EMPTY
  regions (bend sits 5 um from crossing; 20+ um of free space unused). Cost-blindness, not congestion.
- waksman n10-C witness: full_drc=True routed 0-failed in the SAME envelope (58 min, 300k pops)
  while campaign flags failed with same_net:63/pops:92 self-boxing — search-time same-net
  discipline prevents self-tangling. n11-C identical signature (51 um window overshoot both).
- LiDAR (ISPD'25) crossing insertion has three legality checks we only half-ported:
  arm straight-length + orientation, component bounding-box fit, port match. Our crossings
  are zero-size points with no footprint in the occupancy map (codex_v3_survey_anchors.md B).
- db_tie_breaker_scale lumps physical-constraint proxies with guidance
  (codex_v3_survey_architecture.md §3): turn-timing/turn-guard near crossings (item ⑨) and
  same-net hairpin penalties (③④) are physical, scaled to 5% today.
- P2 offline scan: crossing arm violations at 10 um = ~70% of A* crossings, template ~60%
  pre-re-residue (outputs/v3_offline_analysis/crossing_manufacturability.csv).
- v3 mini results (outputs/nsweep_fixed_fabric_v3_mini/V2_V3_COMPARISON.md, 12/12 accounted,
  11 complete, dual-method agreement 100%):
  * Waksman C-lane HUGE improvement where routing succeeds: n4-C WIL 1.350->0.720 dB
    (crossings 16->4, bends -36); n6-C 2.278->1.638 dB (crossings 32->16, bends -53);
    same-net audits collapsed (52/9 -> 11/4). Search-time clearance cost + dy5.5 geometry
    already tames the avoidance-tangle spiral at small N.
  * Template: 10 um arm clearance hard-gate ALL-CLEAR after the targeted fix (G1 held at
    14/60/232); WIL cost of v3 geometry on template cases is negligible (+0.0012 to +0.05 dB).
  * NEW REGRESSION (v4 Phase 2 target #2): waksman n8-C complete -> route_failed. I6->O6
    final hop blocked by the WIDER FOREIGN I3 port-access region (dy5.5 enlarged runway
    geometry). Signature: port_access_overlap_foreign dominant; frontier EXHAUSTED at
    10,220 pops (max_heap 224 — walled, not budget-limited); counters port_access:66,
    window:847, same_net:1536, segment:2033. This is a third failure class: foreign
    reserved-region blocking (distinct from pb9 congestion and n10-C self-boxing).
PHASE 2 additions for the third failure class:
2c. Reserved-region escalation: when a hop's frontier exhausts and port_access_foreign
    dominates the prune counters, apply staged relaxation — (i) permit transit through the
    OUTER half of a foreign runway with full spacing legality, then (ii) stagger the foreign
    escape/runway points (the LiDAR port-spread idea, adoption-audit item 1d — the one
    LiDAR mechanism never ported). Config-gated, recorded per case.
GATE addition: waksman n8-C must route 0-failed under v4 campaign flags (alongside n10-C).
</evidence_anchors>

<phases>
PHASE 1 — Complete the LiDAR crossing mechanism (search-time)
1a. Crossing candidates require arm legality on BOTH nets at insertion time:
    min_crossing_clearance_um becomes standard-on in campaign configs (10.0 until FDTD picks
    the cell); legal_crossing_candidate/endpoint_crossing_candidate enforce arm straight-length
    on the crossing move AND the crossed segment (extend crossing.py checks; see anchors doc B2
    for the merged-segment granularity caveat — enforce against merged geometry, not single moves).
1b. Register a crossing FOOTPRINT in the occupancy map at insertion: Manhattan square of side
    min_crossing_clearance_um centered on the crossing point, owned by neither net; later moves
    and bends of ANY net must not enter it (except the four through-arms). This kills
    crossing-next-to-bend and crossing-next-to-crossing classes at the source.
GATES: re-run v3-mini waksman n4-B: the two known violations disappear (0 crossing_clearance
audit at 10 um) with 0 failed edges; full v3-mini set re-routes with no new failures and
audit counts strictly reduced vs v3-mini baseline.

PHASE 2 — Search-discipline promotions (reclassify physical constraints out of tie-breaker scaling)
2a. Turn-guard/turn-timing near crossings: full strength in db mode (no db_tie_breaker_scale).
2b. Search-time same-net spacing becomes campaign standard (the full_drc discipline whose
    witness routed n10-C clean). Same-net hairpin penalties (survey items 3/4) promoted to
    full strength.
GATES: waksman n10-C routes 0-failed under campaign-standard flags (witness predicts yes);
waksman n11-C attempted with same flags (report outcome verbatim); no regression in the
v3-mini set (all previously-routing cases still route; WIL drift reported per case).

PHASE 3 — Straightener extension: bend-slide legalization
Slide bends along their segments away from crossing footprints / to lengthen arms
(deterministic order, legality via existing primitives, acceptance: all-legal AND
delta-loss <= +0.01 dB when the slide fixes a manufacturability violation, else <= 0).
GATE: offline application to v3-mini artifacts removes >= 80% of remaining arm violations
without creating any DRC violation.

PHASE 4 — Placement uniform-rule experiments (report-only, no adoption decision)
4a. Cell y-bias toward the add/drop wire: delta in {0, 4, 8, 12} um applied as a UNIFORM rule,
    waksman + padded_benes n6/n8 B/C (A* lane; template lane requires re-deriving h_s/h_n for
    the biased center — do template n8 only, verify T(N)=60 holds or report the delta).
    Measure: wrap length, bends, WIL, same-net audit counts vs delta.
4b. In-column x-stagger (alternate cells offset by {0, 16, 32} um): measure crossing arm
    clearance distribution + port-access congestion.
Deliverable: placement_experiments.md with tables; adoption deferred to human review.

PHASE 5 — Goldens + stop
Single golden regeneration point for the v4 search behavior (v3 geometry unchanged);
GEOMETRY_V3.md gains a V4_SEARCH.md sibling changelog; full pytest green; STOP for human
review with the Phase 1-4 gate results and a recommendation on full-campaign configs.

PHASE 6 (after approval) — THE single full campaign
N=3-12, both topologies, v3 geometry + v4 search, configs: B (crossing 0), C @ 0.1 dB/x,
C2 @ 0.02 dB/x (optimized-cell scenario; gives the avoidance-pressure sensitivity axis).
Everything else per codex_nsweep_task.md conventions (dual-method verification, coverage,
certificates, charts incl. the octave narrative set, output root outputs/nsweep_fixed_fabric_v4/).
Expectation to verify: n10/11/12-C route under the new discipline, closing the C-curve holes.
</phases>

<action_safety>
Same as v3: no concurrent campaign, hash gates after every step until the Phase 5 regen point,
no git commit, v2/v3 artifacts read-only, stop-and-report on any gate failure or novel
failure signature.
</action_safety>
