# Claude Review Prompt: Waksman Fixed Fabric

Please perform a skeptical, evidence-based architecture and physics review of
the Waksman fixed-waveguide implementation in this repository. Do not assume
that passing tests or exhaustive permutation enumeration proves the physical
model is complete. Begin read-only: identify verified facts, unverified
assumptions, likely defects and missing experiments before proposing code.

## Current Intent

The physical switch must contain one immutable set of MRR cells and waveguides.
A requested permutation may change only MRR states. It must not trigger new
physical routing. Waksman stages can omit a switch on some wires; the current
implementation collapses such pass-through spans into one longer
`FabricEdge`, rather than creating a fake MRR or stage node.

## Files to Inspect

1. `mrr_switch_optimizer/core/topology.py`
2. `mrr_switch_optimizer/core/state_assignment.py`
3. `mrr_switch_optimizer/core/models.py`
4. `mrr_switch_optimizer/core/fabric.py`
5. `mrr_switch_optimizer/routing/fabric.py`
6. `mrr_switch_optimizer/routing/physical.py`
7. `mrr_switch_optimizer/routing/drc.py`
8. `mrr_switch_optimizer/analysis/fabric_coverage.py`
9. `mrr_switch_optimizer/analysis/fabric_loss.py`
10. `mrr_switch_optimizer/app/fabric_reports.py`
11. `mrr_switch_optimizer/app/cli.py`
12. `tests/test_fabric_graph.py`
13. `tests/test_fixed_fabric_router.py`
14. `architecture/fixed_fabric_router.md`
15. `docs/waksman_fixed_fabric_results.md`

Inspect the generated evidence under:

- `outputs/waksman_fixed_fabric_n4/fixed_fabric/`
- `outputs/waksman_fixed_fabric_n6/fixed_fabric/`
- `outputs/waksman_fixed_fabric_n8/fixed_fabric/`

## Observed Results

| Size | MRRs | Edges | Permutations | Worst IL | Failed | DRC |
|---:|---:|---:|---:|---:|---:|---:|
| 4x4 | 5 | 14 | 24 | 2.641890 dB | 0 | 0 |
| 6x6 | 11 | 28 | 720 | 4.166768 dB | 0 | 0 |
| 8x8 | 17 | 42 | 40,320 | 5.017315 dB | 0 | 0 |

All three reports state `physical_fabric_routed_once=true` and
`physical_routing_permutation_count=0`.

## Questions That Must Be Challenged

1. Does `build_waksman_stage_pairs()` plus identity fixed stage permutations
   represent a canonical Waksman interconnect physically, or only a logically
   equivalent abstract switch schedule?
2. Is collapsing multiple no-MRR stages into one direct edge always valid for
   placement, crossings, port access and edge ownership?
3. Are the permanent `in-th` and `add-drop` bus continuities and standard
   add/drop port directions handled correctly for every Waksman pair,
   especially non-adjacent wire pairs?
4. Does splitting routed bus waypoints at exact MRR port coordinates assign all
   propagation length and bends to the correct `FabricEdge` exactly once?
5. Is MRR internal geometric length being double-counted when S-parameters
   already include device propagation loss? State explicitly which convention
   should be used.
6. Crossing and bend loss coefficients are currently zero. Assess realistic
   values and rerun sensitivity sweeps before treating the reported worst IL as
   a physical prediction.
7. Is `0.002 dB/um` propagation loss appropriate for the intended process, or
   merely a placeholder? Check units and compare against foundry data.
8. Does crossing attribution count only crossings actually traversed by each
   active path, without omissions or double counting when a path changes buses?
9. Are zero software DRC violations sufficient? List missing foundry rules,
   including waveguide width, bend discretization, crossing cell geometry,
   taper rules, minimum straight lengths and port orientation constraints.
10. Are geometry and route ordering deterministic for repeated 4x4, 6x6 and
    8x8 runs? Identify which hashes/tests prove this and which sizes lack an
    automated physical-routing regression.
11. Does exhaustive logical coverage verify only graph membership, or also
    optical directionality, power conservation, wavelength behavior and
    simultaneous multi-channel interference?
12. Compare Waksman and padded Beneš using identical placement pitch, routing
    rules and loss coefficients. Determine whether fewer MRRs actually reduce
    worst physical IL after longer wiring and more crossings are included.
13. The fixed router adapts immutable buses into synthetic bar `Path` objects
    to reuse the shared geometric engine. Check whether any legacy
    active-permutation assumptions still leak through this adapter.
14. Identify missing concerns: thermal crosstalk, heater control, phase and
    coherent interference, process corners, resonance drift, polarization,
    electrical/optical co-design, packaging and testability.

## Required Deliverable

Return:

1. A table of findings with severity (`critical`, `high`, `medium`, `low`),
   evidence file/line, consequence and recommended action.
2. A separate list of facts that are genuinely proven by current tests.
3. A separate list of assumptions that remain unvalidated.
4. The five highest-value next experiments, with expected observation and a
   falsification criterion for each.
5. A verdict on whether the current worst-IL numbers may be used for internal
   algorithm comparison, publication, or fabrication decisions.

Do not modify code until this review is complete and the proposed corrections
have been prioritized.
