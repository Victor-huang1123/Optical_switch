# N-sweep fixed-fabric campaign — Phase 1 interim

The primary result is the MRR-count and scalability advantage: Waksman uses strictly fewer MRRs at every sampled N and scales smoothly instead of following the padded-Beneš octave stair-step. At N=9 it uses 21 MRRs versus 56, a 62.5% reduction (reported as −63% on the figure). Worst insertion loss (WIL) is parity/supporting evidence, not a universal Waksman win.

All cases use the pinned v2 octave envelope; canvas steps at N=4→5 and N=8→9 by design. No v1 140-µm-pitch result is mixed into these charts. A search window is a per-hop search-policy bound that may expand only in a recorded remediation retry; the envelope is the constitutional canvas/stage/wire geometry and never expands. The octave envelope, canvas, stage pitch, wire pitch, and global grid bounds stayed pinned.

## Metrics

| topology | N | config | status | MRR | savings % | crossings | worst-path crossings | worst IL dB | agreement | same-net | perp | remediation | window tracks |
|---|---:|---|---|---:|---:|---:|---:|---:|---|---:|---:|---|---:|
| padded_benes | 4 | B_db_placeholder | complete | 6 | 16.7 | 14 | 7 | 2.512391 | True | 0 | 0 | none | 0 |
| waksman | 4 | B_db_placeholder | complete | 5 | 16.7 | 14 | 6 | 2.585851 | True | 0 | 1 | none | 0 |
| padded_benes | 4 | C_db_realistic | complete | 6 | 16.7 | 14 | 8 | 1.193164 | True | 0 | 0 | none | 0 |
| waksman | 4 | C_db_realistic | complete | 5 | 16.7 | 4 | 3 | 0.720030 | True | 5 | 2 | none | 0 |
| padded_benes | 6 | B_db_placeholder | complete | 20 | 45.0 | 60 | 13 | 4.438152 | True | 0 | 0 | none | 0 |
| waksman | 6 | B_db_placeholder | complete | 11 | 45.0 | 28 | 13 | 4.043649 | True | 1 | 2 | none | 0 |
| padded_benes | 6 | C_db_realistic | complete | 20 | 45.0 | 60 | 18 | 2.487663 | True | 0 | 0 | none | 0 |
| waksman | 6 | C_db_realistic | complete | 11 | 45.0 | 16 | 10 | 1.637784 | True | 11 | 4 | none | 0 |
| padded_benes | 8 | B_db_placeholder | complete | 20 | 15.0 | 60 | 13 | 4.574819 | True | 0 | 0 | none | 0 |
| waksman | 8 | B_db_placeholder | complete | 17 | 15.0 | 56 | 18 | 4.906407 | True | 5 | 0 | none | 0 |
| padded_benes | 8 | C_db_realistic | complete | 20 | 15.0 | 60 | 18 | 2.487663 | True | 0 | 0 | none | 0 |
| waksman | 8 | C_db_realistic | route_failed | 17 | 15.0 | 64 | — | — | — | 0 | 18 | none | 0 |

## Method and correctness record

- Exact exhaustive/path-space agreement: 11/11 certified routable cases (100%).
- Coverage failures: 0.
- Route-failed cases: 1; failed edge records: 4. Route-failed cases have no WIL row in the agreement file and no witness certificate.
- Legacy DRC findings: 0; bend-radius findings: 0.
- Every certificate contains a witness permutation and is rechecked with one strategy-route/loss call.
- Padded-Beneš n=6 uses BenesLoopingStrategy, not the older CLI LUT strategy, so older LUT-based n=6 artifacts are not geometry/WIL goldens for this campaign.

## Routing audits and deterministic-template advantage

`corridor_guide_mode` is a NO-OP: the flag is consumed nowhere. The always-on `_corridor_preferred_x` bias (`routing/physical.py`, historical line 877 anchor) runs in every mode, and corrected G7 compares identical geometry and crossings for `off` and `soft`. This campaign does not claim or implement a real guide layer.

Waksman A* same-net-min-spacing and perpendicular-clearance audit counts grow in scale with N and are generally worse under C, while every deterministic padded-Beneš template case is all-clean. These are measurement-only audits for A* acceptance, but the contrast is a real deterministic-template advantage: the template is clean by construction rather than merely finding a lower-loss A* score.

- Waksman B_db_placeholder same-net/perpendicular counts: N=4:0/1, N=6:1/2, N=8:5/0.
- Waksman C_db_realistic same-net/perpendicular counts: N=4:5/2, N=6:11/4, N=8:0/18.

## Required Phase 1 layouts

- `cases/waksman/n06/B_db_placeholder/fixed_fabric_layout.png`
- `cases/waksman/n06/C_db_realistic/fixed_fabric_layout.png`
- `cases/padded_benes/n06/B_db_placeholder/fixed_fabric_layout.png`
- `cases/padded_benes/n06/C_db_realistic/fixed_fabric_layout.png`

## Design decisions and N=10/C conclusion

The same-net whole-net remediation evolved through three mechanism iterations: (1) endpoint-axis handling was corrected so displaced endpoint risers preserve physical port approach; (2) displacement moved to the predecessor hop that created the committed obstruction and the bounded fallback beam was widened; (3) a default-off remediation-only search-window expansion added `abs(displacement)+2` tracks per ladder attempt. The final exact campaign-flags 300k run exhausted +1/−1/+2/−2 on all six routing passes. It retained 24 attempt records and still ended at I4→O4 with seven edge records; the final displaced segment `(493,444)→(493,593)` exceeded the expanded hop window whose top reached 582. Conclusion: N=10/C is `route_failed`, with artifacts retained and no certified WIL.

The rung-pollution diagnosis found no leakage: each pop rung rebuilt cells, routing state, and rules. The lone apparent success used `full_drc=True`; that activated same-net/perpendicular audit violations inside routing pass scoring and selected different geometry. It was a DRC-coupled experiment, not an exact campaign-flags success, and is excluded.

Search window and envelope are distinct. The search window is expandable per hop, only in the remediation retry, config-gated and recorded as `window_expansion_tracks`. The octave envelope is pinned and constitutional; it did not change.

## Runtime and anomalies

Observed wall time for this invocation: 5.4 s; summed recorded route/evaluation work across rows: 2758.6 s. Per-case times are recorded in `metrics.csv`/`metrics.partial.csv`.

No anomaly is silently normalized: status, pop rung, no-op guide flag, layout mode, optional DRC audit counts, remediation mechanism, search-window expansion, and envelope ID are recorded per case.

## Phase 0 algorithm decisions

See `PHASE0_DESIGN_DECISIONS.md` for the algorithm decisions, integrity corrections, and exactness lemma.
