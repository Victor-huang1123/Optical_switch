# Codex Task — Crossing-Insertion Gating: Evidence Review and Design Recommendation

> 中文摘要：v4 在 2026-09-02 兩道 gate 都失敗後停住。新跑的 ablation 顯示 crossing-insertion
> 合法性的價值同時取決於 N 與 crossing-loss 配置，而且兩者相乘：N=10 的 B lane 開啟明顯有益，
> 但同樣的 N=10 在 C lane 開啟就路不出來、關閉 781 秒路通。v4 Phase 1 把這個機制標準化開啟到
> 所有配置，在 C lane 正好是反的，而 C lane 就是 v2 三個 route_failed 洞的所在。本任務要求評估
> 這個機制應該如何 gating，只做分析與書面建議，不改任何程式碼。

<current_state>
v4 (`codex_v4_crossing_task.md`) stopped at two failed gates on 2026-09-02 and has not moved since.

- `outputs/nsweep_fixed_fabric_v4_mini_gate/gate_results.json` — `all_zero_failed=false`.
  Wins: n4-B crossing_clearance 2→0, audit total 87→35 (`audits_strictly_reduced=true`).
  Losses: **waksman n8-B complete → 6 failed edges (NEW regression vs v3)**; C-lane WIL drift
  wipes out v3's gain (n4-C 0.720→1.455 dB, n6-C 1.638→2.714 dB, n6-C crossings 16→38).
  Trade: n8-C route_failed → routes (WIL 3.712, 86 crossings).
- `outputs/v4_bend_slide_gate/results.json` — `passed=false`, arm violations 10→7
  (30% removed vs 80% target), 0 DRC regressions, only waksman n8-B slid (3 slides).
- Phase 4 deliverable `placement_experiments.md` missing; Phase 5 not run, so
  `tests/test_v4_search.py` is RED (missing `tests/golden/v4_search_waksman_n4_b.json`;
  `scripts/regenerate_v4_search_golden.py` is ready). Phase 6 campaign never launched.
</current_state>

<ablation_method>
Scripts: `scripts/crossing_insert_n4.py` (N=4, 30k pops),
`scripts/crossing_insert_ablation.py` + `scripts/run_crossing_ablation_n10.sh` (N=10, 100k pops).
Results: `outputs/crossing_insert_n4/results.json`, `outputs/crossing_insert_ablation/*.json`.

Only `RoutingRules.explicit_crossings` is ablated. Both arms use `layout_mode="astar"` (so the
comparison is method-symmetric for both topologies) at identical pops, with otherwise stock
`_campaign_rules` — no v3 geometry flags, no v4 flags. Crossings are re-detected geometrically by
`drc.py:_external_route_crossings`, independent of the search flag, so crossing loss is charged
identically in both arms. Numbers are therefore NOT directly comparable to the v2/v3 campaign
tables; they are internally valid as an ablation.
</ablation_method>

<evidence_n4>
Insertion is never better at N=4. Two cells neutral, two harmful.

| topology | config | insert | failed | crossings | bends | WIL dB | perp_clearance | wall |
|---|---|---|---:|---:|---:|---:|---:|---:|
| waksman | B | ON | 0 | 14 | 39 | 2.5258511866719995 | 0 | 5 s |
| waksman | B | OFF | 0 | 14 | 41 | 2.5258511866719995 | 0 | 4 s |
| waksman | C | ON | 0 | 16 | 67 | 1.3486 | 6 | 6 s |
| waksman | C | OFF | 0 | 14 | 41 | 1.1964 | 0 | 4 s |
| padded_benes | B | ON | **4** | partial | partial | — | 0 | 173 s |
| padded_benes | B | OFF | 0 | 14 | 43 | 2.5668 | 0 | 5 s |
| padded_benes | C | ON | 0 | 14 | 44 | 1.1967 | 0 | 6 s |
| padded_benes | C | OFF | 0 | 14 | 45 | 1.1939 | 0 | 5 s |

- waksman-B: inert. WIL identical to 16 significant figures — same worst path.
- waksman-C: ON costs +0.152 dB, +2 crossings, +26 bends, and introduces 6 perpendicular-clearance
  violations where OFF has none.
- padded_benes-B: ON fails 4 edges and spends 173 s doing it; OFF routes clean in 5 s.
- Every OFF arm is audit-clean across all five audits.
- Repeated crossing pairs are identical in both arms (5 distinct pairs, max multiplicity 4) except
  waksman-C ON, where max multiplicity rises to 6. So ON buys no pair discipline at N=4.
</evidence_n4>

<evidence_n10>
The sign flips with N, and then flips again with the config.

| topology | config | insert | failed | crossings | pairs | max mult | bends | WIL dB | perp | wall |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| waksman | B | ON | 0 | 106 | 32 | 6 | 189 | 7.0171 | 1 | 270 s |
| waksman | B | OFF | 0 | 120 | 35 | 8 | 217 | 7.4177 | 7 | 251 s |
| waksman | C | ON | — | — | — | — | — | **no route in 70 min** | — | timeout |
| waksman | C | OFF | 0 | 110 | 32 | 8 | 211 | 3.9560 | 7 | 781 s |
| padded_benes | B | ON | — | — | — | — | — | **no route in 60 min** | — | timeout |
| padded_benes | B | OFF | 0 | 274 | — | — | 457 | 8.5472 | — | 828 s |

- **n10 B lane (crossing loss 0): ON wins on every axis** — WIL better by 0.40 dB, 14 fewer
  crossings, 3 fewer distinct pairs, max multiplicity 6 vs 8, 28 fewer bends, and 1 vs 7
  perpendicular-clearance violations. Pair discipline demonstrably works here.
- **n10 C lane (crossing loss 0.1 dB/x): ON cannot route, OFF routes in 781 s.** This closes one
  of v2's three `route_failed` holes (v2 n10-C: route_failed after 10810 s at 300k pops, with
  same_net 99 / perp 20 on the retained partial). It is also ~4.5x cheaper in wall time and 3x
  cheaper in search budget than the known v4 witness (`full_drc=True`, 58 min at 300k pops).
- padded_benes n10-B with A* also fails with ON and routes with OFF, so the effect is not
  waksman-specific.

Caveats to preserve in any writeup:
- The ON-arm failures are **budget-bounded** statements ("did not route within the cap"), not
  proofs of unroutability. Do not restate them as `route_failed`.
- OFF trades some geometric cleanliness: 7 perpendicular-clearance violations at n10 in both
  configs, vs 1 for ON at n10-B. That audit is measurement-only for the astar lane, not a hard
  gate, but the trade is real and should be reported.
- padded_benes n10 C lane never ran (previous session ended). That cell is the main data gap.
</evidence_n10>

<hypothesis>
Crossing-insertion legality and a high crossing-loss coefficient **compound**.

In the C lane the router is already under avoidance pressure from the 0.1 dB/crossing penalty.
Adding insertion legality on top over-constrains the same decision and produces the
avoidance-tangle spiral that `codex_v4_crossing_task.md` itself set out to kill — the router
detours to avoid a crossing, cannot avoid it, tangles, and fails.

In the B lane the crossing coefficient is zero, so there is no avoidance pressure. Legality is
then pure discipline with nothing to fight, and it reduces crossings, bends and repeated pairs.

At small N the canvas is uncongested, so legality has nothing to buy in either config; it only
removes freedom, which is why N=4 shows harm or inertia everywhere.

If this hypothesis holds, **v4 Phase 1's decision to make `min_crossing_clearance_um` standard-on
across all campaign configs is inverted precisely in the C lane** — which is where v2's holes were.
It is also a candidate explanation for the n8-B regression.
</hypothesis>

<why_this_matters_beyond_the_router>
The crossing model is not an internal router detail; it is the paper's pivot.

A physical single-ring add-drop cell counter-propagates its two buses (in=left-top, th=right-top,
add=right-bottom, drop=left-bottom, see `core/models.py:port_for_wire`), so every cell forces a
wrap crossing. The physical crossing count is therefore `T(N) = inversions + 2·n_MRR` = 14/60/232
at N=4/8/16, against the textbook `X(N) = (N/2)(N − log₂N − 1)` = 2/16/88
(`codex_algorithm_notes.md:48,54`; `PROJECT_OVERVIEW_FOR_DISCUSSION.md:186`).

Since the topology winner flips with the crossing coefficient, anything that changes how crossings
are counted or produced changes the paper's headline claim. Gating decisions here should be judged
on whether they make the crossing count physically faithful, not only on whether they make cases
route.
</why_this_matters_beyond_the_router>

<scope>
Produce a written assessment covering:

1. How the crossing-insertion mechanism should be gated — by config, by N, by a measured
   congestion statistic, or by something else. Say what the gating signal should be and why it is
   observable before routing starts.
2. How v4 Phase 1's "standard-on in campaign configs" decision should be corrected, and what that
   implies for the Phase 6 campaign configs (B, C @ 0.1 dB/x, C2 @ 0.02 dB/x).
3. Whether the n8-B regression is explained by this hypothesis, and if not, what else explains it.
4. What experiments would confirm or refute the hypothesis at least cost. Name the specific cells
   and budgets. The padded_benes n10 C lane gap is the obvious first candidate.
5. Whether the OFF arm's higher perpendicular-clearance counts indicate a real manufacturability
   cost that would need separate remediation, or an artifact of the measurement-only audit tier.
</scope>

<action_safety>
Analysis and written recommendation only.

- Do NOT modify any source, config, test, or golden file.
- Do NOT launch a campaign or any long routing run.
- v2/v3/v4 artifacts are read-only.
- If a claim cannot be supported from the files listed above, say so rather than inferring it.
</action_safety>
