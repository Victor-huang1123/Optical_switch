# Output Schemas

All paper-run outputs are written under `outputs/<run_id>/`.

## Fixed fabric outputs

`python main.py --fixed-fabric --topology benes --n-logical N` writes one
permutation-independent physical design under `<outdir>/fixed_fabric/`.

### `fabric_edges.csv`

One row per immutable external fabric edge. `waveguide_owner_edge_id` groups
edges connected through the permanent `in-th` or `add-drop` MRR bus. Only
physical boundary endpoints can have `source_blocked` or `target_blocked` set.

### `fabric_routing_summary.json`

Physical-only summary containing edge/waveguide counts, routed and failed edge
counts, DRC and crossing counts, routing rules/statistics, and
`geometry_sha256`. A successful design has `failed_edge_count == 0` and
`drc_violation_count == 0`.

### `fabric_drc_violations.csv`

DRC rows owned by stable fabric edge IDs, never permutation input IDs.

### `permutation_coverage.json`

Logical/state verification against the already-routed fabric. It records the
exhaustive permutation count, assignment/path failures, the same geometry hash,
and `physical_fabric_routed_once: true`. No physical reroute occurs during this
coverage pass.

### `fabric_loss_summary.json`

Exhaustive worst-path insertion loss evaluated on the already-routed edge-level
geometry. It records the worst permutation and input/output path, physical
length, MRR loss, propagation loss, bend/crossing counts and configured loss
coefficients. This pass changes MRR states only and never invokes physical
routing.

### `legacy_comparison.json`

Side-by-side metadata for the fixed-fabric model and the retained legacy
active-path model. The metrics are labeled as non-interchangeable because the
legacy geometry is permutation-specific.

### `fixed_fabric_layout.png`

The single immutable waveguide layout shared by every verified permutation.

## run_config.json

Fields:

- `argv`: command line used for the run.
- `config_path`: YAML config path.
- `config`: raw YAML config.
- `resolved`: resolved topology, matrix N values, optional breakeven,
  layout-gallery, physical-batch, calibration settings, and seeds.
- `versions`: git SHA, package version, Python version, and platform.
- `sparams`: S-parameter provenance — `source` is `library` (real CSVs),
  `mock_fallback` (library missing; numbers are mock physics and must not be
  quoted in paper artifacts), or `mock_explicit` (`--sparam-dir MOCK`), plus
  the resolved `library_dir`, `radius_um`, `channel_nm`, `wavelength_nm`.
- `permutation_lists`: per-N train/eval counts and SHA-256 hashes.
- `physical_permutation_lists`: per-N physical-batch attempt counts, mode
  (exhaustive for N <= `full_enum_max_n`, sampled otherwise), and SHA-256
  hashes (present when the physical batch is enabled).
- `timestamp`: local timestamp for provenance.

## logical_matrix/logical_matrix_summary.csv

One row per `(topology, n_logical)` logical matrix cell.

Required columns:

- `topology`, `n_logical`, `n_physical`, `layer`, `seed`, `eval_mode`
- `train_perms`, `eval_perms`
- `n_mrr`, `n_stages`
- `paths`
- `worst_il_db`, `avg_il_db`, `il_p50_db`, `il_p90_db`, `il_p95_db`, `il_p99_db`
- `il_std_db` (population std of per-path IL), `il_iqr_db` (p75 - p25)
- `worst_sxr_db`, `avg_sxr_db`, `sxr_p01_db`, `sxr_p05_db`
- `sxr_violation_count`
- `avg_crossing_count` (mean logical crossing proxy per evaluated permutation)
- `crossing_loss_db_per_cross` (dB charged per native crossing in this cell's
  logical IL; set `logical_matrix.crossing_loss_db_per_cross` equal to
  `physical_batch.crossing_loss_db_per_cross` for a layer-consistent model)

Percentiles use linear interpolation (numpy `linear` convention).

Logical matrix cells are resumable: when a cell's `eval_summary.csv` and
`eval_path_distribution.csv` already exist with matching
`n_logical`/`seed`/`eval_mode`/`train_perms`/`eval_perms`/
`crossing_loss_db_per_cross`, the cell is reused instead of recomputed.
With `train_samples: 0` the eval split covers all `n!` permutations at
N <= 6 (true exhaustive worst case).

## logical_matrix/<topology>_n<N>/eval_path_distribution.csv

One row per evaluated path.

Required columns:

- `topology`, `n_logical`, `layer`, `seed`, `eval_mode`
- `layout`, `perm_idx`, `permutation`
- `input_port`, `output_port`
- `insertion_loss_db`, `sxr_db`, `sxr_violation`
- `wiring_um`, `mrr_loss_db`, `signal_power`, `leak_power`
- `perm_worst_il_db`, `perm_worst_sxr_db`, `perm_crossing_count`

## logical_matrix/<topology>_n<N>/eval_summary.csv

Single-row summary for that matrix cell. It uses the same column set as
`logical_matrix_summary.csv`.

## physical_batch/physical_summary.csv

One row per physical routing attempt.
During long physical-batch runs, `physical_summary.csv`,
`physical_path_distribution.csv`, and `drc_violations.csv` are rewritten after
each attempted permutation with the rows accumulated so far. A killed run can
therefore still leave partial progress data. Re-running the same physical batch
in the same output directory resumes from `physical_summary.csv` and skips
attempts with matching topology, N, seed, permutation index, and permutation.

Required columns:

- `topology`, `n_logical`, `layer`, `seed`
- `layout`, `perm_idx`, `permutation`
- `route_status`, `paths`, `routed_paths`, `failed_paths`, `failure_rate`
- `runtime_s`, `astar_calls`, `ripup_passes`
- `drc_violation_count`, `crossing_count`
- routing-rule columns such as `grid_pitch_um`, `min_spacing_um`, `max_astar_pops`

`astar_calls` is counted by the physical router for completed routing attempts.
Rows produced from top-level exceptions may leave it blank because no routing
result was returned.

## physical_batch/physical_path_distribution.csv

One row per physical path, including failed paths.

Required columns:

- `topology`, `n_logical`, `layer`, `seed`
- `layout`, `perm_idx`, `permutation`
- `input_port`, `output_port`, `route_status`, `failure_reason`
- `physical_wiring_um`, `bend_count`, `crossing_count`, `physical_il_db`
- `topology_il_db`, `il_delta_db`, `topology_wiring_um`, `wiring_delta_um`

`physical_summary.csv` additionally records the routing-space and budget
parameters per attempt (`route_window_max_detour_tracks`,
`effective_route_window_slack_um`, `grid_margin_tracks`, `stage_pitch_um`,
`wire_pitch_um`, `x_start_um`, `x_end_um`, `max_astar_pops`,
`ripup_max_astar_pops`) plus rip-up telemetry: `ripup_passes` (budget),
`ripup_passes_executed`, `early_stop_enabled`, and `early_stopped`.

## surrogate/calibration_report.csv

One row per `(topology, n_logical)` calibration group.

Required columns:

- `topology`, `n_logical`
- `examples`, `train_examples`, `holdout_examples`
- `mae_analytic`, `mae_calibrated`
- `kendall_tau_analytic`, `kendall_tau_calibrated`

## tables/ and figures/

Generated by:

```bash
python main.py --paper-artifacts outputs/<run_id>
```

Equivalent module entrypoint:

```bash
python -m mrr_switch_optimizer.analysis.paper_figures outputs/<run_id>
```

Table artifacts:

- `T1_topology_scaling.csv`
- `T2_logical_comparison.csv` (one row per `(topology, n_logical)` cell; every N
  present in `logical_matrix_summary.csv` is included)
- `T2_logical_physical_comparison.csv`
- `T3_available_baselines.csv`
- `routing_parameter_summary.csv` (per `(topology, n_logical)`: MRR/stage counts,
  stage/wire pitch, estimated layout width/height/area, grid pitch and margin,
  route-window detour tracks and effective slack, A* pop budgets, rip-up pass
  budget, early-stop setting, and the reported-IL loss model; physical-routing
  parameters are read from `run_config.json`'s `physical_batch` section)
- `physical_worst_il_vs_n.csv` (see below)

## tables/physical_worst_il_vs_n.csv

One row per `(topology, n_logical)` physical-batch cell. Column conditioning is
explicit: routed-only statistics cover attempts with `route_status == "routed"`;
`drc_violation` attempts (all nets routed but DRC violations) are excluded from
IL statistics and reported separately so the censoring stays visible.

Columns:

- `topology`: full topology name including the `_<N>x<N>` size suffix.
- `topology_family`: topology name with the trailing `_<digits>x<digits>` size
  suffix stripped (e.g. `padded_benes_8x8` -> `padded_benes`,
  `spanke_benes_rect_12x12` -> `spanke_benes_rect`). Figures draw one series
  per family across N.
- `n_logical`
- `attempts`, `fully_routed_attempts`, `fully_routed_rate`
- `drc_violation_attempts`: attempts with `route_status == "drc_violation"`.
- `worst_il_db`: max per-attempt physical worst IL over routed attempts only.
- `worst_il_permutation`: permutation of the routed attempt realizing `worst_il_db`.
- `routed_worst_il_mean_db`, `routed_worst_il_std_db`: mean / population std of
  per-attempt physical worst IL over routed attempts only.
- `runtime_s_avg_all_attempts`: mean `runtime_s` over all attempts.
- `runtime_s_avg_routed`: mean `runtime_s` over routed attempts only.

Routed-only columns are empty for cells without a routed attempt.

Figure artifacts:

- `F1_il_sxr_cdf.png` (one CDF per topology family and representative N; up to
  3 N values are selected as min/median/max of the available N, stated in the
  panel title and legend)
- `F2_scaling_curves.png`
- `F3_breakeven_curves.png`
- `F4_logical_physical_scatter.png`
- `F5_routed_layouts.png`
- `F6_physical_failure_runtime.png` (success rate, runtime, A* calls, rip-up passes executed)
- `F7_crossing_bend_scaling.png` (logical crossing proxy, physical crossings,
  physical bends vs N; physical crossing/bend averages cover
  `route_status == "routed"` attempts only)
- `F8_layout_scaling.png` (estimated layout width/height/area vs N)
- `physical_worst_il_vs_n.png` (worst IL vs N per family from
  `physical_worst_il_vs_n.csv`; cells without a routed attempt render as line
  breaks and each point is annotated with `fully_routed_attempts/attempts`)

Figures group series by `topology_family` with a stable family -> color/marker
mapping, so an N=3..16 run renders one series per family instead of one series
per sized topology name.

F3-F6 consume optional `breakeven_sweep.csv`, `physical_batch/`, and routed
PNG inputs when present. If an optional dataset is absent, the generator writes
a deterministic placeholder figure so the artifact set remains complete and
auditable.
