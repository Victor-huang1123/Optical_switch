# v2 Offline Jog-Straightening Report

This analysis loaded the completed v2 `routing_result.pkl` artifacts read-only; it did not invoke the router or write into the v2 campaign tree. Candidate spans were confined to external segments, protected every edge endpoint and local runway/stub, checked both L variants against rebuilt blockers and port-access regions, reran full DRC, and required non-increasing loss under each case's stored coefficients.

## Sanity gates

- Completed cases analyzed: 37.
- Template cases: 20; all were structurally held at zero change.
- A* cases changed: 15/17.
- Final legality regressions: 0.

## Per-case results

| topology | N | config | mode | rewrites | bends removed | length saved (um) | crossings delta | WIL delta (dB) | runtime (s) |
|---|---:|---|---|---:|---:|---:|---:|---:|---:|
| padded_benes | 3 | B_db_placeholder | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 3 | C_db_realistic | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 4 | B_db_placeholder | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 4 | C_db_realistic | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 5 | B_db_placeholder | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 5 | C_db_realistic | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 6 | B_db_placeholder | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 6 | C_db_realistic | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 7 | B_db_placeholder | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 7 | C_db_realistic | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 8 | B_db_placeholder | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 8 | C_db_realistic | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 9 | B_db_placeholder | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 9 | C_db_realistic | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 10 | B_db_placeholder | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 10 | C_db_realistic | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 11 | B_db_placeholder | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 11 | C_db_realistic | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 12 | B_db_placeholder | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| padded_benes | 12 | C_db_realistic | template | 0 | 0 | 0.000 | +0 | 0.000000 | 0.000 |
| waksman | 3 | B_db_placeholder | astar | 0 | 0 | 0.000 | +0 | 0.000000 | 0.002 |
| waksman | 3 | C_db_realistic | astar | 3 | 13 | 306.102 | -6 | -0.459863 | 0.030 |
| waksman | 4 | B_db_placeholder | astar | 0 | 0 | 0.000 | +0 | 0.000000 | 0.006 |
| waksman | 4 | C_db_realistic | astar | 5 | 36 | 814.743 | -12 | -0.630406 | 0.367 |
| waksman | 5 | B_db_placeholder | astar | 1 | 4 | 125.416 | -4 | 0.000000 | 0.030 |
| waksman | 5 | C_db_realistic | astar | 7 | 38 | 858.451 | -10 | -0.555671 | 0.924 |
| waksman | 6 | B_db_placeholder | astar | 1 | 2 | 15.708 | -2 | 0.000000 | 0.086 |
| waksman | 6 | C_db_realistic | astar | 9 | 55 | 1181.969 | -14 | -0.676312 | 2.706 |
| waksman | 7 | B_db_placeholder | astar | 2 | 4 | 31.416 | -4 | -0.031416 | 0.535 |
| waksman | 7 | C_db_realistic | astar | 14 | 80 | 1902.319 | -36 | -1.425730 | 6.810 |
| waksman | 8 | B_db_placeholder | astar | 3 | 10 | 178.540 | -4 | 0.000000 | 0.654 |
| waksman | 8 | C_db_realistic | astar | 14 | 94 | 2300.274 | -30 | -0.919858 | 10.336 |
| waksman | 9 | B_db_placeholder | astar | 4 | 14 | 245.956 | -8 | -0.198832 | 1.405 |
| waksman | 9 | C_db_realistic | astar | 19 | 102 | 2363.106 | -42 | -0.965304 | 24.824 |
| waksman | 10 | B_db_placeholder | astar | 1 | 2 | 15.708 | +0 | 0.000000 | 2.223 |
| waksman | 11 | B_db_placeholder | astar | 6 | 16 | 225.664 | -18 | -0.031416 | 4.585 |
| waksman | 12 | B_db_placeholder | astar | 9 | 29 | 505.765 | -16 | -0.031416 | 11.907 |

## Aggregate A* deltas

- Rewrites: 98.
- Bends removed: 499.
- Length saved: 11071.137 um.
- Crossing delta: -206.
- Sum of per-case WIL deltas: -5.926223 dB.

A negative WIL delta is an improvement. The stored modified geometries are ephemeral analysis results only; no v2 pickle or summary was rewritten.
