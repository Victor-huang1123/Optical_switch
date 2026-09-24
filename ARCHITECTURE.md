# v3 architecture record

This document records the hash-neutral Phase 1 architecture work. The Phase 0
artifacts and v2 campaign outputs remain historical inputs; they were not
regenerated or rewritten.

## Module map

| Area | Modules | Current responsibility |
|---|---|---|
| Core | `core/models.py`, `topology.py`, `state_assignment.py`, `fabric.py`, `sparams.py` | Device/cell data, topology construction, switch states, fixed-fabric graph, and S-parameter loading. `CellGeometry` in `models.py` is the physical cell source of truth. |
| Placement | `placement/layout.py`, `lp.py`, `sa.py` | Nominal placement plus retained legacy LP/SA flows. `build_cells` consumes `CellGeometry`; the dead LP cost helper was removed. |
| Routing primitives | `routing/types.py`, `geometry.py`, `crossing.py`, `port_access.py`, `grid_router.py`, `route_grid.py` | Routing data, pure geometry, crossing rules, port-access macros, A* state, and occupancy queries. Wildcard imports were eliminated. |
| Routing engine | `routing/grid.py`, `physical.py`, `fabric.py`, `benes_template_layout.py`, `envelope.py`, `refinement.py`, `drc.py` | A* search, physical orchestration, fixed-fabric route assembly, deterministic template emission, octave canvases, crossing refinement, and post-route DRC. The dead `guides.py` module was removed; preferred-bend-x remains always on in `physical.py`. |
| Analysis | `analysis/cost.py`, `nsweep.py`, `fabric_loss.py`, `fabric_coverage.py`, `surrogate.py`, `activity.py`, `calibration.py`, `paper_figures.py`, `summary.py` | Logical/physical loss, exhaustive/path-space evaluation, coverage, legacy surrogate/activity/calibration, and paper artifacts. Paper layout dimensions now read `CellGeometry`. |
| Application/output | `app/cli.py`, `nsweep_campaign.py`, `fabric_reports.py`, `reports.py`, `output/visualize.py` | CLI flows, resumable campaigns, structured reports, and rendering. Campaign paths use strict S-parameter loading. |
| Archived tools | `scripts/routing_upgrade_validation.py`, `scripts/task11_probe_artifacts.py` | Standalone one-shot validation/probe renderers, removed from importable package namespaces but kept executable with absolute imports. |

## Explicit import boundary

Phase 1 step 0 replaced all 14 `import *` statements across nine routing
modules: `crossing.py`, `drc.py`, `geometry.py`, `grid.py`, `grid_router.py`,
`physical.py`, `port_access.py`, `refinement.py`, and `route_grid.py`.
`pyflakes` reports no undefined or unused names after the conversion.

The conversion exposed two dependencies missed by the original direct-reference
survey:

- `geometry._candidate_routes` is called by `physical.py` (900 full-pytest hits;
  14 representative-campaign hits).
- `geometry._append_points` is called by `grid.py` and `physical.py` (6,797
  full-pytest hits; 84 representative-campaign hits).

Both are retained as live routing helpers.

## Dual-evidence prune ledger

Static evidence used the installed `pyflakes` binding graph after wildcard
elimination. Runtime evidence used targeted wrappers during a green full test
run (`298 passed`) and a temporary-output Waksman N=4/C campaign `_route_case`
(`failed_edges=0`, `drc_violations=0`). Vulture was unavailable locally and
could not be installed because the environment has no network access.

| Action | Static evidence | Runtime evidence | Rationale |
|---|---|---|---|
| Removed `route_path_wiring`, `count_routed_crossings`, `_route_external_segment` | Unused function bindings | Zero hits in full pytest and campaign case | Dead pre-A* roots. |
| Removed `route_l_shape` in a second pass | Became unused after its dead callers were removed | Zero hits in both runs | Dead legacy L-route implementation. |
| Removed `placement.lp.lp_wiring_cost` | Unused function binding | Zero hits in both runs | No caller in current or legacy entry paths. |
| Removed `corridor_guide`, `guide_fallback_order`, then `CorridorGuide`/`guides.py` | Dead roots first; class became unused on the second pass; no importers | Zero hits in both runs | No gated guide layer exists. |
| Removed remaining unused imports/locals | `pyflakes` unused bindings | Full pytest and campaign path remained green | Cleanup exposed by explicit imports. |
| Archived two one-shot modules to `scripts/` | Zero package importers | Package tests and scripts compile | Preserve tools without presenting them as library modules. |

### Retained because evidence is ambiguous

- `analysis.surrogate.analytic_layout_cost`: its local binding and runtime hit
  count are unused/zero, but it is re-exported from the public package API.
  Removing it could break an external consumer not represented by this repo.
- `RoutingRules.corridor_guide_mode`: it does not gate routing, but it is still
  read by validation and campaign metadata/report compatibility paths. It is
  therefore not statically unused. The routing behavior is accurately described
  as the always-on `_corridor_preferred_x` preferred-bend bias.
- Public legacy CLI, SA/LP, activity, and surrogate entry points: low internal
  usage is not sufficient evidence that external/demo consumers are absent.

## Consolidated helpers

- `_point_to_obstacle_distance`, `_point_to_axis_segment_distance`, and
  `_turn_sign` now have one implementation in `routing/grid.py`; `physical.py`
  imports them.
- Segment debug formatting now has one implementation,
  `grid._format_segment_debug`; grid failure strings and physical diagnostics
  share it without changing text.

## Parameterized interfaces

- `CellGeometry` owns bbox width/height, port dx/dy, thermal minimum distance,
  and waveguide width. Phase 4 makes v3 `(28, 22, 8, 5.5, 24.0, 0.45)` the
  default; named `LEGACY_V2_CELL_GEOMETRY` permanently pins the 140 um router
  regression tier to `(28, 22, 8, 4, 24.0, 0.0)`. See `GEOMETRY_V3.md`.
- `default_add_drop_ports`, `build_cells`, `OctaveEnvelope`,
  `build_envelope_cells`, envelope IDs, and paper layout estimates consume that
  geometry. V3 envelope IDs carry the `dy5p5-w450` geometry tag.
- `--legacy-comparison` gates the legacy active-permutation route in fixed-fabric
  CLI mode. `write_fixed_fabric_reports` accepts an optional comparison and only
  writes `legacy_comparison.json` when one is supplied.
- `load_mrr_s_table(strict=True)` rejects missing files and incomplete canonical
  tables. Campaign/validation entry points are strict; demo/test callers retain
  warning fallback by default.
- `run_campaign` now accepts `output_root`, `pops_ladder`,
  `remediation_trigger_ns`, and `stage_pitch_by_octave`. Defaults remain the v2
  root, `(30000, 100000, 300000)`, `(10, 11, 12)`, and
  `{4: 136.0, 8: 168.0, 16: 232.0}`.

## Deferred to v4

- Extract the duplicated A*/deterministic-candidate cost model into a shared
  module, preserving float operation order.
- Replace the grid-to-physical failure-detail string protocol with structured
  failure payloads.
- Unbundle `db_tie_breaker_scale`; promote same-net hairpin rules to explicit
  physical constraints and retain a separate guidance scale.
- Split `physical.py` along ordering/beam, hop-candidate, source-hint,
  validation, and rip-up seams after v3 geometry stabilizes.

## Phase 1 hash discipline

No golden was regenerated. Every prune/consolidation/parameterization unit
passed the report-golden, committed geometry-hash, and G1-G7 subset immediately
after the edit. Phase 1 closes only after the complete test suite also passes.
