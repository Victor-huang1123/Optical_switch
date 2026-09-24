# Optical Switch

Topology-agnostic add-drop MRR switch synthesis and evaluation for padded
Beneš, Waksman, and Spanke-Beneš RNB switch fabrics.

## Install

```bash
pip install -e .[dev]
```

The S-parameter CSV library is optional for development. If
`mrr_sparam_library/` is absent or incomplete, the loader falls back to
`mrr_switch_optimizer.core.sparams.MOCK_S_TABLE`.

## Quickstart

Default 6x6 logical comparison:

```bash
python main.py --topology all --outdir outputs/default_6x6
```

N x N logical run:

```bash
python main.py --topology waksman --n-logical 8 \
  --permutation 7,6,5,4,3,2,1,0 \
  --eval-mode sample --outdir outputs/waksman_n8
```

Logical matrix:

```bash
python main.py --logical-matrix --matrix-n-values 4,6,8,16 \
  --outdir outputs/logical_matrix
```

Reproducible paper run. `configs/paper_v1.yaml` writes the logical matrix,
breakeven sweep, layout gallery, bounded physical batch for N <= 8, one-shot
calibration, provenance, tables, and figures:

```bash
python main.py --paper-run configs/paper_v1.yaml
```

Regenerate only the paper tables and figures from an existing run directory:

```bash
python main.py --paper-artifacts outputs/paper_v1_seed42
```

Bounded physical batch smoke:

```bash
python main.py --physical-batch --physical-n-values 4 \
  --physical-batch-samples 1 --topology all \
  --max-astar-pops 1 --max-ripup-passes 0 \
  --outdir outputs/physical_smoke
```

One-shot surrogate calibration from a physical batch:

```bash
python main.py --calibrate --outdir outputs/physical_smoke
```

Fixed manufacturable Padded Beneš fabric. Physical routing runs once; all
permutations are then verified by changing only MRR states:

```bash
python main.py --fixed-fabric --topology benes --n-logical 6 \
  --outdir outputs/fixed_fabric_n6
```

The same route-once workflow supports Waksman, including stages where a wire
passes through without an MRR:

```bash
python main.py --fixed-fabric --topology waksman --n-logical 8 \
  --outdir outputs/waksman_fixed_fabric_n8
```

This mode uses a fabric-specific default floorplan (`140 um` stage pitch,
`64 um` wire pitch) without changing legacy `--physical-eval` defaults. Its
outputs are written under `fixed_fabric/`, including one layout PNG, immutable
edge CSV, physical routing/DRC summary, exhaustive permutation coverage, and a
worst-IL summary plus a legacy-model comparison.

## Flag Reference

Core run control:

- `--topology {main,all,benes,waksman,sb}` selects the topology set.
- `--n-logical N` selects the logical radix; defaults to 6.
- `--permutation a,b,c,...` selects one permutation; the default is
  `2,0,5,1,3,4` for N=6 and identity otherwise.
- `--eval-mode {exhaustive,sample}`, `--train-samples`, `--eval-samples`,
  and `--perm-seed` control the permutation protocol.
- `--outdir PATH` selects the output directory.

Experiment modes:

- `--logical-matrix` writes the topology x N logical/surrogate matrix.
- `--paper-run CONFIG` runs a YAML-configured paper experiment and writes
  `run_config.json`.
- `--paper-artifacts RUN_DIR` regenerates T1-T3 and F1-F6 from a run.
- `--physical-batch` routes sampled permutations for N <= 8 and reports
  failures as data.
- `--calibrate` fits the one-shot ridge residual surrogate from physical path
  rows.
- `--fixed-fabric` routes one immutable Padded Beneš or Waksman waveguide
  fabric, exhaustively verifies all permutations against that same geometry,
  and reports the worst insertion loss on routed edge-level geometry.

## Outputs

Paper-run output structure:

```text
outputs/<run_id>/
  run_config.json
  logical_matrix/
    logical_matrix_summary.csv
    <topology>_n<N>/eval_summary.csv
    <topology>_n<N>/eval_path_distribution.csv
  breakeven_sweep.csv
  physical_batch/
  surrogate/
  layouts/
  tables/
  figures/
```

CSV/JSON schemas are documented in `docs/schemas.md`.

Output glossary:

- `run_config.json`: resolved config, seeds, versions, git SHA, and permutation
  list hashes.
- `logical_matrix_summary.csv`: one row per topology and N logical evaluation
  cell.
- `eval_path_distribution.csv`: per-path logical IL/SXR/wiring rows.
- `breakeven_sweep.csv`: crossing-loss sweep for F3.
- `physical_summary.csv`: one row per physical routing attempt, including
  runtime, A* call count, and failure-rate fields.
- `physical_path_distribution.csv`: per-path physical routing rows with
  logical-vs-physical IL and wiring deltas.
- `calibration_report.csv`: analytic-vs-calibrated MAE and Kendall tau on a
  fixed holdout.
- `tables/T*.csv` and `figures/F*.png`: paper-facing tables and figures.

## Tests

Focused paper-plan regression suite:

```bash
pytest tests/test_reports.py \
  tests/test_topology_nxn.py \
  tests/test_state_assignment.py \
  tests/test_permutation_sampling.py \
  tests/test_logical_dataset.py \
  tests/test_reproducibility.py \
  tests/test_physical_batch.py \
  tests/test_calibration.py \
  tests/test_paper_figures.py -q
```

Full repository checks are still useful before release:

```bash
pytest -q
mypy mrr_switch_optimizer
```
