# N-sweep fixed-fabric campaign — Phase 1 interim

The primary result is the MRR-count and scalability advantage: Waksman uses strictly fewer MRRs at every sampled N and scales smoothly instead of following the padded-Beneš octave stair-step. Worst insertion loss is treated as parity/supporting evidence, not as a universal Waksman win.

All cases use the pinned v2 octave envelope; canvas steps at N=4→5 and N=8→9 by design. No v1 140-µm-pitch result is mixed into these charts.

## Metrics

| topology | N | config | MRR | savings % | crossings | worst-path crossings | worst IL dB | agreement | route s | eval s |
|---|---:|---|---:|---:|---:|---:|---:|---|---:|---:|
| padded_benes | 3 | B_db_placeholder | 6 | 50.0 | 14 | 6 | 2.309724 | True | 0.0 | 0.0 |
| waksman | 3 | B_db_placeholder | 3 | 50.0 | 4 | 2 | 1.897596 | True | 2.0 | 0.0 |
| padded_benes | 3 | C_db_realistic | 6 | 50.0 | 14 | 8 | 1.180632 | True | 0.0 | 0.0 |
| waksman | 3 | C_db_realistic | 3 | 50.0 | 8 | 6 | 1.012823 | True | 2.2 | 0.0 |
| padded_benes | 4 | B_db_placeholder | 6 | 16.7 | 14 | 7 | 2.482391 | True | 0.0 | 0.0 |
| waksman | 4 | B_db_placeholder | 5 | 16.7 | 14 | 6 | 2.487851 | True | 4.0 | 0.0 |
| padded_benes | 4 | C_db_realistic | 6 | 16.7 | 14 | 8 | 1.191964 | True | 0.0 | 0.0 |
| waksman | 4 | C_db_realistic | 5 | 16.7 | 16 | 10 | 1.349836 | True | 5.5 | 0.0 |
| padded_benes | 5 | B_db_placeholder | 20 | 60.0 | 60 | 16 | 4.390654 | True | 0.1 | 0.0 |
| waksman | 5 | B_db_placeholder | 8 | 60.0 | 24 | 9 | 3.838982 | True | 15.8 | 0.0 |
| padded_benes | 5 | C_db_realistic | 20 | 60.0 | 60 | 18 | 2.486463 | True | 0.1 | 0.0 |
| waksman | 5 | C_db_realistic | 8 | 60.0 | 22 | 12 | 1.849897 | True | 19.1 | 0.0 |
| padded_benes | 6 | B_db_placeholder | 20 | 45.0 | 60 | 13 | 4.396152 | True | 0.1 | 0.0 |
| waksman | 6 | B_db_placeholder | 11 | 45.0 | 30 | 13 | 4.023357 | True | 23.3 | 0.0 |
| padded_benes | 6 | C_db_realistic | 20 | 45.0 | 60 | 18 | 2.486463 | True | 0.1 | 0.0 |
| waksman | 6 | C_db_realistic | 11 | 45.0 | 32 | 15 | 2.277754 | True | 30.9 | 0.0 |
| padded_benes | 7 | B_db_placeholder | 20 | 30.0 | 60 | 13 | 4.396152 | True | 0.1 | 0.5 |
| waksman | 7 | B_db_placeholder | 14 | 30.0 | 48 | 21 | 4.853028 | True | 38.7 | 0.3 |
| padded_benes | 7 | C_db_realistic | 20 | 30.0 | 60 | 18 | 2.486463 | True | 0.1 | 0.2 |
| waksman | 7 | C_db_realistic | 14 | 30.0 | 70 | 30 | 3.765468 | True | 57.0 | 0.2 |
| padded_benes | 8 | B_db_placeholder | 20 | 15.0 | 60 | 15 | 4.524152 | True | 0.1 | 1.3 |
| waksman | 8 | B_db_placeholder | 17 | 15.0 | 68 | 21 | 4.927447 | True | 58.2 | 2.2 |
| padded_benes | 8 | C_db_realistic | 20 | 15.0 | 60 | 18 | 2.486463 | True | 0.1 | 1.3 |
| waksman | 8 | C_db_realistic | 17 | 15.0 | 88 | 30 | 3.798421 | True | 125.2 | 1.2 |

## Method and correctness record

- Exact exhaustive/path-space agreement: 24/24 cases (100%).
- Coverage failures: 0.
- Route failures: 0 edges.
- Legacy DRC findings: 0; bend-radius findings: 0.
- Every certificate contains a witness permutation and is rechecked with one strategy-route/loss call.
- Padded-Beneš n=6 uses BenesLoopingStrategy, not the older CLI LUT strategy, so older LUT-based n=6 artifacts are not geometry/WIL goldens for this campaign.

## Required Phase 1 layouts

- `cases/waksman/n06/B_db_placeholder/fixed_fabric_layout.png`
- `cases/waksman/n06/C_db_realistic/fixed_fabric_layout.png`
- `cases/padded_benes/n06/B_db_placeholder/fixed_fabric_layout.png`
- `cases/padded_benes/n06/C_db_realistic/fixed_fabric_layout.png`

## Runtime and anomalies

Observed wall time for this invocation: 0.7 s. Per-case routing and evaluation times are recorded in `metrics.csv`/`metrics.partial.csv`.

No anomaly is silently normalized: pop rung, guide mode, layout mode, optional DRC audit counts, and envelope ID are recorded per case.

## Phase 0 algorithm decisions

See `PHASE0_DESIGN_DECISIONS.md` for the five open-question resolutions and exactness lemma.
