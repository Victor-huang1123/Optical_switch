# Waksman Fixed-Fabric Results

Generated on 2026-08-02 with one immutable physical layout per switch size.
Physical routing runs once; exhaustive permutation evaluation changes only MRR
states and composes active paths from the already-routed `FabricEdge` geometry.

## Results

| Logical size | MRRs | Fabric edges | Layout crossings | Permutations | Worst IL (dB) | Worst routed length (um) | Failed edges | DRC |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 4x4 | 5 | 14 | 16 | 24 | 2.641890 | 1277.496 | 0 | 0 |
| 6x6 | 11 | 28 | 36 | 720 | 4.166768 | 2025.451 | 0 | 0 |
| 8x8 | 17 | 42 | 70 | 40,320 | 5.017315 | 2426.575 | 0 | 0 |

Worst-path details:

- 4x4: permutation `(0,1,2,3)`, `I1 -> O1`, MRR loss `0.086899 dB`,
  propagation loss `2.554991 dB`.
- 6x6: permutation `(0,1,2,3,4,5)`, `I3 -> O3`, MRR loss
  `0.115865 dB`, propagation loss `4.050903 dB`.
- 8x8: permutation `(0,3,2,4,1,5,6,7)`, `I1 -> O3`, MRR loss
  `0.164164 dB`, propagation loss `4.853150 dB`.

## Layouts

- `outputs/waksman_fixed_fabric_n4/fixed_fabric/fixed_fabric_layout.png`
- `outputs/waksman_fixed_fabric_n6/fixed_fabric/fixed_fabric_layout.png`
- `outputs/waksman_fixed_fabric_n8/fixed_fabric/fixed_fabric_layout.png`

Each directory also contains `fabric_edges.csv`,
`fabric_routing_summary.json`, `fabric_drc_violations.csv`,
`permutation_coverage.json`, `fabric_loss_summary.json`, and
`legacy_comparison.json`.

## Loss Model

The runs use the repository's default physical-loss settings and default MRR
S-parameter selection (`radius=5.0 um`, `channel=1550 nm`,
`wavelength=1550 nm`):

```text
IL = active MRR transfer loss
   + 0.002 dB/um * routed active-path length
   + 0.0 dB/bend * bend count
   + 0.0 dB/crossing * crossing count
```

The reported bend and crossing counts are therefore diagnostic only in these
runs. The numbers are deterministic software-model results, not foundry
sign-off or full-wave electromagnetic simulation.

## Scope

- Waksman pass-through stages are represented as longer immutable edges; no
  fictitious MRR is inserted.
- State assignment and active-path membership are exhaustively checked for all
  permutations.
- The physical fabric is routed once per size and has zero failed edges and
  zero current DRC violations.
- Thermal tuning, wavelength drift, phase coherence, process variation,
  electrical routing, heater power, packaging and foundry-specific DRC remain
  outside this result.
