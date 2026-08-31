# N-sweep fixed-fabric campaign — final report

The primary result is the MRR-count and scalability advantage: Waksman uses strictly fewer MRRs at every sampled N and scales smoothly instead of following the padded-Beneš octave stair-step. At N=9 it uses 21 MRRs versus 56, a 62.5% reduction (reported as −63% on the figure). Worst insertion loss (WIL) is parity/supporting evidence, not a universal Waksman win.

All cases use the pinned v2 octave envelope; canvas steps at N=4→5 and N=8→9 by design. No v1 140-µm-pitch result is mixed into these charts. A search window is a per-hop search-policy bound that may expand only in a recorded remediation retry; the envelope is the constitutional canvas/stage/wire geometry and never expands. The octave envelope, canvas, stage pitch, wire pitch, and global grid bounds stayed pinned.

## Metrics

| topology | N | config | status | MRR | savings % | crossings | worst-path crossings | worst IL dB | agreement | same-net | perp | remediation | window tracks |
|---|---:|---|---|---:|---:|---:|---:|---:|---|---:|---:|---|---:|
| padded_benes | 3 | B_db_placeholder | complete | 6 | 50.0 | 14 | 6 | 2.309724 | True | 0 | 0 | none | 0 |
| waksman | 3 | B_db_placeholder | complete | 3 | 50.0 | 4 | 2 | 1.897596 | True | 0 | 0 | none | 0 |
| padded_benes | 3 | C_db_realistic | complete | 6 | 50.0 | 14 | 8 | 1.180632 | True | 0 | 0 | none | 0 |
| waksman | 3 | C_db_realistic | complete | 3 | 50.0 | 8 | 6 | 1.012823 | True | 15 | 3 | none | 0 |
| padded_benes | 4 | B_db_placeholder | complete | 6 | 16.7 | 14 | 7 | 2.482391 | True | 0 | 0 | none | 0 |
| waksman | 4 | B_db_placeholder | complete | 5 | 16.7 | 14 | 6 | 2.487851 | True | 0 | 0 | none | 0 |
| padded_benes | 4 | C_db_realistic | complete | 6 | 16.7 | 14 | 8 | 1.191964 | True | 0 | 0 | none | 0 |
| waksman | 4 | C_db_realistic | complete | 5 | 16.7 | 16 | 10 | 1.349836 | True | 32 | 6 | none | 0 |
| padded_benes | 5 | B_db_placeholder | complete | 20 | 60.0 | 60 | 16 | 4.390654 | True | 0 | 0 | none | 0 |
| waksman | 5 | B_db_placeholder | complete | 8 | 60.0 | 24 | 9 | 3.838982 | True | 4 | 1 | none | 0 |
| padded_benes | 5 | C_db_realistic | complete | 20 | 60.0 | 60 | 18 | 2.486463 | True | 0 | 0 | none | 0 |
| waksman | 5 | C_db_realistic | complete | 8 | 60.0 | 22 | 12 | 1.849897 | True | 34 | 5 | none | 0 |
| padded_benes | 6 | B_db_placeholder | complete | 20 | 45.0 | 60 | 13 | 4.396152 | True | 0 | 0 | none | 0 |
| waksman | 6 | B_db_placeholder | complete | 11 | 45.0 | 30 | 13 | 4.023357 | True | 0 | 1 | none | 0 |
| padded_benes | 6 | C_db_realistic | complete | 20 | 45.0 | 60 | 18 | 2.486463 | True | 0 | 0 | none | 0 |
| waksman | 6 | C_db_realistic | complete | 11 | 45.0 | 32 | 15 | 2.277754 | True | 52 | 9 | none | 0 |
| padded_benes | 7 | B_db_placeholder | complete | 20 | 30.0 | 60 | 13 | 4.396152 | True | 0 | 0 | none | 0 |
| waksman | 7 | B_db_placeholder | complete | 14 | 30.0 | 48 | 21 | 4.853028 | True | 3 | 4 | none | 0 |
| padded_benes | 7 | C_db_realistic | complete | 20 | 30.0 | 60 | 18 | 2.486463 | True | 0 | 0 | none | 0 |
| waksman | 7 | C_db_realistic | complete | 14 | 30.0 | 70 | 30 | 3.765468 | True | 68 | 19 | none | 0 |
| padded_benes | 8 | B_db_placeholder | complete | 20 | 15.0 | 60 | 15 | 4.524152 | True | 0 | 0 | none | 0 |
| waksman | 8 | B_db_placeholder | complete | 17 | 15.0 | 68 | 21 | 4.927447 | True | 7 | 0 | none | 0 |
| padded_benes | 8 | C_db_realistic | complete | 20 | 15.0 | 60 | 18 | 2.486463 | True | 0 | 0 | none | 0 |
| waksman | 8 | C_db_realistic | complete | 17 | 15.0 | 88 | 30 | 3.798421 | True | 93 | 23 | none | 0 |
| padded_benes | 9 | B_db_placeholder | complete | 56 | 62.5 | 232 | 33 | 7.955748 | True | 0 | 0 | none | 0 |
| waksman | 9 | B_db_placeholder | complete | 21 | 62.5 | 88 | 20 | 6.787492 | True | 10 | 3 | none | 0 |
| padded_benes | 9 | C_db_realistic | complete | 56 | 62.5 | 232 | 36 | 4.721762 | True | 0 | 0 | none | 0 |
| waksman | 9 | C_db_realistic | complete | 21 | 62.5 | 96 | 32 | 4.271954 | True | 98 | 22 | none | 0 |
| padded_benes | 10 | B_db_placeholder | complete | 56 | 55.4 | 232 | 33 | 7.955748 | True | 0 | 0 | none | 0 |
| waksman | 10 | B_db_placeholder | complete | 25 | 55.4 | 112 | 21 | 7.100242 | True | 10 | 2 | none | 0 |
| padded_benes | 10 | C_db_realistic | complete | 56 | 55.4 | 232 | 36 | 4.721762 | True | 0 | 0 | none | 0 |
| waksman | 10 | C_db_realistic | route_failed | 25 | 55.4 | 102 | — | — | — | 99 | 20 | same_net_whole_net_riser_displacement:attempted_exhausted:+1,-1,+2,-2,+1,-1,+2,-2,+1,-1,+2,-2,+1,-1,+2,-2,+1,-1,+2,-2,+1,-1,+2,-2 | 4 |
| padded_benes | 11 | B_db_placeholder | complete | 56 | 48.2 | 232 | 33 | 7.955748 | True | 0 | 0 | none | 0 |
| waksman | 11 | B_db_placeholder | complete | 29 | 48.2 | 134 | 25 | 7.131658 | True | 12 | 8 | none | 0 |
| padded_benes | 11 | C_db_realistic | complete | 56 | 48.2 | 232 | 36 | 4.721762 | True | 0 | 0 | none | 0 |
| waksman | 11 | C_db_realistic | route_failed | 29 | 48.2 | 120 | — | — | — | 100 | 26 | same_net_whole_net_riser_displacement:attempted_exhausted:+1,-1,+2,-2,+1,-1,+2,-2,+1,-1,+2,-2,+1,-1,+2,-2,+1,-1,+2,-2,+1,-1,+2,-2 | 4 |
| padded_benes | 12 | B_db_placeholder | complete | 56 | 41.1 | 232 | 33 | 7.955748 | True | 0 | 0 | none | 0 |
| waksman | 12 | B_db_placeholder | complete | 33 | 41.1 | 154 | 36 | 7.331658 | True | 26 | 13 | none | 0 |
| padded_benes | 12 | C_db_realistic | complete | 56 | 41.1 | 232 | 36 | 4.721762 | True | 0 | 0 | none | 0 |
| waksman | 12 | C_db_realistic | route_failed | 33 | 41.1 | 146 | — | — | — | 110 | 37 | same_net_whole_net_riser_displacement:attempted_exhausted:+1,-1,+2,-2,+1,-1,+2,-2,+1,-1,+2,-2,+1,-1,+2,-2,+1,-1,+2,-2,+1,-1,+2,-2 | 4 |

## Method and correctness record

- Exact exhaustive/path-space agreement: 37/37 certified routable cases (100%).
- Coverage failures: 0.
- Route-failed cases: 3; failed edge records: 21. Route-failed cases have no WIL row in the agreement file and no witness certificate.
- Legacy DRC findings: 0; bend-radius findings: 0.
- Every certificate contains a witness permutation and is rechecked with one strategy-route/loss call.
- Padded-Beneš n=6 uses BenesLoopingStrategy, not the older CLI LUT strategy, so older LUT-based n=6 artifacts are not geometry/WIL goldens for this campaign.

## Routing audits and deterministic-template advantage

`corridor_guide_mode` is a NO-OP: the flag is consumed nowhere. The always-on `_corridor_preferred_x` bias (`routing/physical.py`, historical line 877 anchor) runs in every mode, and corrected G7 compares identical geometry and crossings for `off` and `soft`. This campaign does not claim or implement a real guide layer.

Waksman A* same-net-min-spacing and perpendicular-clearance audit counts grow in scale with N and are generally worse under C, while every deterministic padded-Beneš template case is all-clean. These are measurement-only audits for A* acceptance, but the contrast is a real deterministic-template advantage: the template is clean by construction rather than merely finding a lower-loss A* score.

- Waksman B_db_placeholder same-net/perpendicular counts: N=3:0/0, N=4:0/0, N=5:4/1, N=6:0/1, N=7:3/4, N=8:7/0, N=9:10/3, N=10:10/2, N=11:12/8, N=12:26/13.
- Waksman C_db_realistic same-net/perpendicular counts: N=3:15/3, N=4:32/6, N=5:34/5, N=6:52/9, N=7:68/19, N=8:93/23, N=9:98/22, N=10:99/20, N=11:100/26, N=12:110/37.

## Physical-layer WIL wins, ties, and losses

- B_db_placeholder: Waksman wins at N=[3, 5, 6, 9, 10, 11, 12], ties at N=none, loses at N=[4, 7, 8], and is unavailable at N=none; largest winning N=12.
- C_db_realistic: Waksman wins at N=[3, 5, 6, 9], ties at N=none, loses at N=[4, 7, 8], and is unavailable at N=[10, 11, 12]; largest winning N=9.

The logical-layer reference window is N=9..12 with a reported 12→13 crossover. The physical result above is the authoritative matched-envelope comparison; differences arise from routed length, bends, crossings, and deterministic strategy-relative path use.

## Figures

The primary paper figures are `charts/mrr_count_vs_n.png` and `charts/mrr_savings_pct_vs_n.png`. WIL parity, bars, crossing, and win-margin figures are in the same directory. All figures mark the octave canvas boundaries.
The WIL axes start at 0 so sub-0.1 dB deltas are not visually exaggerated. Config C explicitly marks the Waksman N=10 route-failed gap instead of joining or imputing it.

## Design decisions and N=10/C conclusion

The same-net whole-net remediation evolved through three mechanism iterations: (1) endpoint-axis handling was corrected so displaced endpoint risers preserve physical port approach; (2) displacement moved to the predecessor hop that created the committed obstruction and the bounded fallback beam was widened; (3) a default-off remediation-only search-window expansion added `abs(displacement)+2` tracks per ladder attempt. The final exact campaign-flags 300k run exhausted +1/−1/+2/−2 on all six routing passes. It retained 24 attempt records and still ended at I4→O4 with seven edge records; the final displaced segment `(493,444)→(493,593)` exceeded the expanded hop window whose top reached 582. Conclusion: N=10/C is `route_failed`, with artifacts retained and no certified WIL.

The rung-pollution diagnosis found no leakage: each pop rung rebuilt cells, routing state, and rules. The lone apparent success used `full_drc=True`; that activated same-net/perpendicular audit violations inside routing pass scoring and selected different geometry. It was a DRC-coupled experiment, not an exact campaign-flags success, and is excluded.

Search window and envelope are distinct. The search window is expandable per hop, only in the remediation retry, config-gated and recorded as `window_expansion_tracks`. The octave envelope is pinned and constitutional; it did not change.

## Runtime and anomalies

Observed wall time for this invocation: 85280.2 s; summed recorded route/evaluation work across rows: 58983.1 s. Per-case times are recorded in `metrics.csv`/`metrics.partial.csv`.

No anomaly is silently normalized: status, pop rung, no-op guide flag, layout mode, optional DRC audit counts, remediation mechanism, search-window expansion, and envelope ID are recorded per case.

## Phase 0 algorithm decisions

See `PHASE0_DESIGN_DECISIONS.md` for the algorithm decisions, integrity corrections, and exactness lemma.
