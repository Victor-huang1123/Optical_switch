# Paper-Grade Plan — Topology-Agnostic Port-Aware N×N MRR Switch Synthesis

> Planning document produced from direct reading of: `Stucture.md`, `mrr_switch_prompt.md`,
> `pyproject.toml`, `core/topology.py`, `analysis/cost.py`, `placement/layout.py`,
> `app/cli.py`, `tests/test_physical_router.py` (structure + test inventory),
> `analysis/activity.py` / `analysis/surrogate.py` (spot-checked signatures).
> All file:line references are to the current tree. Hard constraint throughout:
> **default 6×6 behavior must remain bit-identical.**

---

## 1. One-Sentence Paper Thesis

A topology-agnostic, port-aware synthesis and evaluation framework for add-drop MRR space switches — combining oracle-validated constructive N×N state routing, a strictly layered logical/surrogate/physical cost model, and waveguide-level Manhattan routing validation — yields the first consistent cross-topology (padded Beneš / Waksman / Spanke-Beneš) scaling and process-breakeven analysis of insertion loss, crosstalk, and physical routability.

---

## 2. Minimum Publishable Contribution

### C1 — Port-aware, layer-disciplined evaluation framework (framework contribution)

- **Claim.** One `RNBTopology` interface plus one metric stack produces IL/SXR/crossing/routability numbers for three RNB topologies, with every number tagged as *logical estimate*, *analytic surrogate*, or *physical routed* — never mixed.
- **Why non-trivial.** Port-to-port geometry vs center-to-center changes wiring estimates by up to 4× (spec §4, `mrr_switch_prompt.md:136`); Spanke-Beneš has `has_native_crossings() == False` at the logical level yet acquires crossings only after physical routing (spec §5.2, `mrr_switch_prompt.md:172`). Keeping these layers consistent across topologies and N is the methodological core; today the layer distinction is implicit in code paths (`analysis/cost.py` vs `app/cli.py:_physical_route_il_db`, cli.py:918–931) and not in the data model.
- **Evidence required.** Port-aware vs center-to-center ablation; per-path logical-vs-physical scatter with quantified deltas (`il_delta_db`, `wiring_delta_um` already computed in cli.py:813–814).
- **Artifacts.** New `analysis/summary.py` breakdown dataclasses with a `layer` field; eval CSVs; Figure F4.

### C2 — Constructive N×N state assignment, oracle-validated (enabling contribution)

- **Claim.** Per-topology constructive routers (Beneš looping, Waksman recursive setting, Spanke-Beneš planar sorting-network routing) replace the `2^n_MRR` brute-force LUT (`core/topology.py:133–159`) and the `N!` RNB assertion (`core/topology.py:45–54`), verified (a) exhaustively against the LUT oracle for N ≤ 6, and (b) by direct realized-permutation simulation (apply states, check achieved mapping == π) at N = 8 and 16.
- **Why non-trivial.** The individual algorithms are textbook; the contribution is framed honestly as *validated enabling infrastructure*: correct emission onto this codebase's staged `mrr_id`/state semantics is subtle — the Waksman generator merges upper/lower recursion levels into shared stages with non-adjacent pairs (`core/topology.py:230–263`), and padded Beneš must route blocked ports {N..P−1} as identity (`core/topology.py:121–131`). Do **not** claim the routing algorithms as novel.
- **Evidence required.** Property tests (all 720 π at N=6 per topology; ≥1000 sampled π at N=8/16); LUT-vs-constructive runtime table.
- **Artifacts.** New `core/state_assignment.py`; `tests/test_state_assignment.py`.

### C3 — Cross-topology scaling + breakeven study (main quantitative contribution)

- **Claim.** Quantified scaling of MRR count, path depth, worst/percentile IL, worst SXR, crossing count, and physical routability at N ∈ {4, 6, 8, 16}, plus process-dependent breakeven: the crossing-loss / extinction-ratio regime in which each topology wins (extends the existing single-N sweep, cli.py:961–1004).
- **Why non-trivial.** Published comparisons are typically single-N and metric-inconsistent across papers. The breakeven framing ("under which fabrication process does each topology win", spec §11 `mrr_switch_prompt.md:357`) is a stronger engineering claim than a leaderboard, and requires exactly the layer discipline of C1 to be defensible.
- **Evidence required.** Full experiment matrix (§5 below); Figures F2/F3; Tables T1/T2.
- **Artifacts.** `outputs/<run_id>/` matrix runs; paper table/figure scripts.

### C4 — Logical→physical fidelity and calibrated-surrogate ablation (validation contribution)

- **Claim.** Quantify how faithfully logical/analytic estimates *rank* physically routed outcomes (Kendall-τ, MAE), and show a **one-shot** ridge calibration on accumulated physical-route data improves τ over the uncalibrated analytic surrogate — the load-bearing ablation named in spec §11 (`mrr_switch_prompt.md:343`) and §13 ("baseline omitted → kills main claim", `mrr_switch_prompt.md:441`).
- **Why non-trivial.** No calibration code exists today: `analysis/surrogate.py` contains only `analytic_edge_costs` / `analytic_layout_cost` (features per spec §8) — no ridge regression, no τ monitoring, no rollback. This is new science-bearing code, deliberately reduced from the spec's 20-round loop (§10) to a one-shot fit to control risk.
- **Evidence required.** τ/MAE table: analytic-only vs calibrated, per topology, N ∈ {4,6,8}; holdout discipline.
- **Artifacts.** `analysis/calibration.py`; `calibration_report.csv`; Table T3 rows.

### Scope split

- **MVP paper scope.** C1–C4 as stated; physical routing at N ≤ 8 only; logical/surrogate metrics to N = 16; one-shot calibration.
- **Stretch scope.** Full iterative calibration loop with τ-rollback (spec §10); incremental SA affected-set evaluation (spec §9) and its ablation; physical routing at N = 16; LP placement study; GDS export via gdsfactory.
- **Do not do before first submission.** WDM / W>1, MRR rotation θ≠0, thermal co-simulation, coherent crosstalk, any rewrite of `routing/physical.py` (5,329 lines), additional topologies (Clos), device-level BO.

---

## 3. Gap Analysis From Current Code

**G1 — Brute-force LUT and N! RNB assertion.**
*Current:* `RNBTopology.__init__` builds the full LUT by enumerating `product((0,1), repeat=n_MRR)` (`core/topology.py:141`) and asserts RNB over all `N!` permutations (`core/topology.py:46–48`). Already ≈1.05M state vectors for the 20-MRR padded Beneš.
*Blocks:* N=16 padded Beneš needs 2^56 states; SB N=8 needs 2^28 — the framework cannot even instantiate the objects the paper is about.
*Change:* Strategy interface + constructive routers (C2); LUT demoted to test oracle with an `n_MRR ≤ 20` guard.
*Files:* `core/topology.py`, new `core/state_assignment.py`, `tests/`.
*Validation:* exhaustive equivalence at N≤6; realized-permutation simulation at N=8/16; construction time N=16 < 5 s.

**G2 — Hard-coded topology classes.**
*Current:* `PaddedBenesTopology` (name `padded_benes_8x8`, literal 5-stage pairs, `core/topology.py:162–174`) and `SpankeBenesTopology` (literal 9-stage triangle, `core/topology.py:180–196`) are fixed; `WaksmanTopology` has a general generator but pins `N_logical = 6` (`core/topology.py:202–215`).
*Blocks:* no N×N experiments at all.
*Change:* constructors `Topology(n_logical: int = 6)`; new `build_benes_stage_pairs(P)` and `build_spanke_benes_stage_pairs(N)` generators; formulas per `Stucture.md` §8.2 (lines 1156–1244).
*Files:* `core/topology.py`, `tests/test_topology_nxn.py` (new).
*Validation:* generated default stage pairs equal the current literals exactly; count formulas match Table T1 (§5).

**G3 — CLI locked to S₆.**
*Current:* `_parse_permutation` rejects anything but a permutation of 0..5 (`app/cli.py:430–434`); default `2,0,5,1,3,4` (cli.py:336); no `--n-logical`.
*Blocks:* reproducible N×N runs; the paper's commands.
*Change:* `--n-logical`, permutation-length inference, `--eval-mode {exhaustive,sample}`, `--eval-samples/--train-samples/--perm-seed`.
*Files:* `app/cli.py` (+ split `cli_args.py` per `Stucture.md` §6.3 if convenient).
*Validation:* zero-arg invocation byte-identical to today; milestone command of `Stucture.md` §8.7 runs.

**G4 — N! enumeration in analysis.**
*Current:* `make_permutation_split` materializes all of S_N (`analysis/cost.py:149`); `scan_mrr_activity` "Scan all S_N permutations" (`analysis/activity.py:25,33`).
*Blocks:* N=10 is already 3.6M perms; N=16 is 2×10¹³.
*Change:* fixed-seed sampler (`sample_permutations(n, k, seed)` with dedupe), exhaustive allowed only for N ≤ 8 and opt-in; activity scan gets a sampled mode with a `permutations_scanned` column.
*Files:* `analysis/cost.py`, `analysis/activity.py`, `app/cli.py`.
*Validation:* N=6 default split reproduces today's 500/220 seed-42 split exactly; sampler statistical tests (no duplicates, deterministic per seed).

**G5 — No loss/crosstalk breakdown data model.**
*Current:* `PathMetric` carries only totals (`analysis/cost.py:17–27`): no `propagation_loss_db`, `bend_count/bend_loss_db`, `crossing_count/crossing_loss_db` per path, no layer tag; crossing IL is folded into `insertion_loss_db` (cost.py:185).
*Blocks:* paper standard #9/#10 (consistent IL/XT accounting; logical vs surrogate vs physical distinction); every table currently re-derives fields ad hoc in `cli.py`.
*Change:* `PathLossBreakdown` per `Stucture.md` §8.3 field list (lines 1277–1292) + SXR breakdown (`leak_mrr_power`, `leak_crossing_power`) + `layer` enum.
*Files:* new `analysis/summary.py`; `analysis/cost.py` populates it; `app/cli.py` consumes it.
*Validation:* golden-file regression on current CSVs; sum-of-parts == total invariant test.

**G6 — Reproducibility infrastructure absent.**
*Current:* ~10 ad-hoc `_write_*` functions inside `app/cli.py` (lines 449–1212); no run-config/seed/git-SHA logging; `README.md` is empty (`Stucture.md` §6.1); `scipy` imported by `placement/lp.py` but undeclared in `pyproject.toml` (`Stucture.md` §6.4); artifacts mixed into the repo under `Physical_output/`.
*Blocks:* paper standard #4 (reproducible protocol); reviewers cannot re-run.
*Change:* `run_config.json` per run, `outputs/<run_id>/` layout, YAML config + `--paper-run`, declare `scipy` (dependency or `lp` extra), reproduction README.
*Files:* `app/reports.py` (new), `app/cli.py`, `pyproject.toml`, `README.md`, `configs/`.
*Validation:* two runs with same config produce identical CSVs (except timestamps); `pip install -e .[dev]` + documented commands succeed on a clean checkout.

**G7 — Calibration loop does not exist.**
*Current:* `analysis/surrogate.py` implements only analytic features/costs; no ridge fit, no Kendall-τ, no rollback — despite spec §8/§10 defining them and spec §13 flagging the missing baseline as claim-killing.
*Blocks:* C4 entirely.
*Change:* one-shot `analysis/calibration.py`: fit ridge residual on (analytic features → physical edge cost) pairs from Phase-7 routes; report τ/MAE on holdout. Iterative loop stays stretch.
*Files:* `analysis/calibration.py`, `analysis/surrogate.py` (feature reuse), `pyproject.toml` (scipy for `kendalltau` or hand-rolled τ).
*Validation:* τ(calibrated) reported with CI vs τ(analytic); seed-fixed holdout split; unit test on synthetic data where true model is known.

**G8 — Logical crossing count is a proxy.**
*Current:* non-adjacent switch pairs add `|pair[1]−pair[0]|−1` as a "conservative native-crossing proxy" (`analysis/cost.py:285–290`); schematic segment intersections do the rest.
*Blocks:* cross-topology crossing comparisons (Waksman/Beneš are crossing-heavy) could be attacked as artifacts of the proxy.
*Change:* keep the proxy but *validate* it: correlate logical crossing counts vs physical `crossing_count` from routed designs at N ≤ 8; report correlation in the paper; document the proxy explicitly.
*Files:* `analysis/cost.py` (docstring), Phase-8 figure script.
*Validation:* scatter + rank correlation in F4; stated limitation.

**G9 — No incremental SA evaluation.**
*Current:* `aggregate_cost` recomputes state assignment, paths, and all path metrics for every permutation on every call (`analysis/cost.py:117–139`); spec §9 requires affected-set incremental updates and lists full recomputation as a red flag (`mrr_switch_prompt.md:429`).
*Blocks:* the "incremental vs full recompute" ablation; SA wall-time at N ≥ 8.
*Change (MVP):* cache `PermutationPlan` (π, states, paths) and cells across calls — plans are placement-independent, so this is safe and large. Affected-set incremental SA moves to stretch; drop that ablation from the MVP claims.
*Files:* `analysis/cost.py`, `placement/sa.py` (call-site only).
*Validation:* identical costs before/after caching (regression test); measured speedup logged.

**G10 — Monolithic tests import private router internals.**
*Current:* single 2,980-line `tests/test_physical_router.py` importing ~30 `_private` helpers from `routing/physical.py` (test file lines 46–60).
*Blocks:* any refactor of topology/state code risks collateral test breakage; no topology-level N×N tests exist.
*Change:* add *new* test modules (`test_topology_nxn.py`, `test_state_assignment.py`, `test_reports.py`) rather than splitting the old file pre-submission; leave router tests untouched.
*Files:* `tests/`.
*Validation:* `pytest -q` green throughout; new tests cover every phase's acceptance criteria.

**G11 — Physical evaluation is single-permutation and scalability-unknown.**
*Current:* `--physical-eval` routes exactly the one CLI permutation per topology (cli.py:227–260); router behavior beyond the 6×6/8×8 corridor structure has never been exercised.
*Blocks:* physical claims at N=8; runtime/failure-rate metrics.
*Change:* Phase-7 batch driver: K sampled permutations per (topology, N ≤ 8), per-net failure accounting as *data* (not a hard error), runtime + A*/rip-up call counters; **no router algorithm changes**.
*Files:* `app/cli.py` (or `app/physical_eval.py`), `routing/physical.py` untouched except counters if trivially injectable from outside.
*Validation:* pipeline completes on all cells of the physical matrix even when nets fail; `physical_summary.csv` gains `n_logical`, `runtime_s`, `failure_rate` columns.

**G12 — Spec lock contradicts the paper goal.**
*Current:* spec §0 locks `N_logical = 6` for v1 (`mrr_switch_prompt.md:18`) and §2 names two topologies while the code and paper need three.
*Blocks:* "single source of truth" claim of the spec; Codex phases would violate the spec as written.
*Change:* amend `mrr_switch_prompt.md` §0/§2 (documentation-only) to v2 scope: parameterized N, three topologies, physical boundary N ≤ 8.
*Files:* `mrr_switch_prompt.md`.
*Validation:* doc review; no code change.

---

## 4. N×N Architecture Plan

### 4.1 Parameterized topology constructors

```python
PaddedBenesTopology(n_logical: int = 6)      # P = 2^ceil(log2 n_logical)
SpankeBenesTopology(n_logical: int = 6)      # N_physical = n_logical
WaksmanTopology(n_logical: int = 6)          # generator already general
```

Constructors derive `name` (`padded_benes_{P}x{P}` etc. — defaults must keep the exact current names `padded_benes_8x8`, `spanke_benes_6x6`, `waksman_6x6` because they key CSVs, PNG stems, and `centers_by_topology`), `N_logical`, `N_physical`, `n_stages`, `n_MRR`, `stage_pairs`, blocked ports. New pure generators `build_benes_stage_pairs(P)` (recursive butterfly reproducing the literal at P=8) and `build_spanke_benes_stage_pairs(N)` (brick-wall triangle reproducing the literal at N=6) live beside `build_waksman_stage_pairs` (`core/topology.py:218`).

### 4.2 Count formulas (from `Stucture.md` §8.2 — become tested invariants)

| Topology | N_physical | n_stages | n_MRR | Blocked ports |
|---|---|---|---|---|
| Padded Beneš | P = 2^⌈log₂N⌉ | 2·log₂P − 1 | (P/2)(2·log₂P − 1) | P − N |
| Spanke-Beneš | N | 2N − 3 | N(N−1)/2 | 0 |
| Waksman | N | `len(stage_pairs)` | `sum(len(s))` (= N·log₂N − N + 1 for powers of 2) | 0 |

### 4.3 Constructive state assignment (no 2^n_MRR)

```python
class StateAssignmentStrategy(Protocol):
    def assign(self, topology: RNBTopology, permutation: Permutation) -> StateAssignment: ...

BruteForceLUTStrategy      # oracle; hard guard n_MRR <= 20
BenesLoopingStrategy       # Opferman–Tsao–Wu looping on P wires; blocked ports routed as identity
WaksmanStrategy            # recursive setting on the recursion tree (generator refactored to also emit the tree)
SpankeBenesStrategy        # planar/odd-even transposition sorting-network routing
```

`RNBTopology` gains a `strategy` attribute; `get_state_assignment` delegates. `_assert_rnb` runs only when `N_logical ≤ 6` or under an explicit `verify_rnb=True` flag; for constructive strategies RNB holds by construction and is spot-checked by the universal verifier: **simulate states through `stage_pairs` (+ fixed permutations) and require realized mapping == π** — this verifier is the single acceptance mechanism at every N and costs O(n_MRR) per permutation.

### 4.4 Permutation sampling / exhaustive boundary

- N ≤ 6: exhaustive is default (720; preserves current behavior).
- N = 7, 8: exhaustive allowed opt-in (5,040 / 40,320); sampled default.
- N > 8: sampled only; `--eval-mode exhaustive` raises with a clear message.
- Sampler: fixed-seed Fisher–Yates draws with dedupe; train/eval disjoint by construction; sizes logged in `run_config.json`.

### 4.5 Path / state / metric caching

`PermutationPlan {permutation, states, paths}` computed once per (topology, π) — placement-independent, so reusable across default/SA/LP layouts and across `--eval`, `--breakeven`, `--analytic-edges` (which currently each re-derive them, e.g. cli.py:174–226, 290–316). `EvaluationDataset {train_plans, eval_plans}` built once per topology per run. `build_cells` result reused per (topology, centers) instead of per `evaluate_routing` call (`analysis/cost.py:60`).

### 4.6 Insertion-loss breakdown data model (per path, per layer)

`path_id, layer{logical|surrogate|physical}, input_port, output_port, mrr_count_on_path, mrr_loss_db, waveguide_length_um, propagation_loss_db, bend_count, bend_loss_db, crossing_count, crossing_loss_db, total_insertion_loss_db` — with the tested invariant *total = mrr + propagation + bend + crossing*. Logical layer sets `bend_* = 0` explicitly rather than omitting fields.

### 4.7 SXR / crosstalk breakdown data model

`leak_mrr_power, leak_crossing_power, leak_total_power, signal_power, sxr_db, sxr_violation(bool, vs sxr_min_db)`. Invariant: SB logical layer has `leak_crossing_power == 0` exactly (spec §5.2).

### 4.8 Physical routing scalability boundary

Physical routing is **only** claimed for `N_physical ≤ 8` in the MVP. Rationale: the router's cost drivers are corridor congestion, crossing budgets, and rip-up (`Stucture.md` §8.6 Phase 5, lines 1513–1517), not merely N; `routing/physical.py` (5,329 lines) must not be modified pre-submission. N=16 physical is a stretch goal and a stated limitation.

### 4.9 Do not touch in the first phase

`routing/physical.py` and all of `routing/`; `placement/sa.py` move set; `core/models.py` port geometry; `output/visualize.py` rendering logic; the existing test file. Phase 1 is behavior-preserving by definition.

---

## 5. Experiment Matrix

### 5.1 Topology × N grid (logical + surrogate layers)

| Topology | N=4 | N=6 | N=7 | N=8 | N=16 |
|---|---|---|---|---|---|
| Padded Beneš (n_MRR / stages) | 6 / 3 (P=4, no padding) | 20 / 5 (P=8) | 20 / 5 (P=8) | 20 / 5 (P=8, no padding) | 56 / 7 (P=16) |
| Spanke-Beneš | 6 / 5 | 15 / 9 | 21 / 11 | 28 / 13 | 120 / 29 |
| Waksman | 5 / 3 | 11 / 5 | 14 / 5 | 17 / 5 | 49 / 7 |

Notes: N=7 exercises non-power-of-two Waksman and padding (P=8, one blocked port) — include at least in T1/F2; N=16 is logical/surrogate only. If any (topology, N) is infeasible, the table cell reports *why*, which is itself data.

### 5.2 Permutation protocol

| N | Mode | Train / Eval | Seed |
|---|---|---|---|
| 4 | exhaustive | 14 / 10 (of 24) | 42 |
| 6 | exhaustive (current) | 500 / 220 (spec §3) | 42 |
| 7, 8 | sampled (exhaustive opt-in) | 500 / 300 | 42 |
| 16 | sampled | 500 / 300 | 42 |

Every run logs mode, sizes, seed, and the SHA of the permutation list in `run_config.json`.

### 5.3 Baselines (mapped to code)

| Baseline | Code mapping | Status |
|---|---|---|
| Naive grid placement | default `build_cells` layout (`placement/layout.py:11–38`) | exists |
| Port-unaware graph cost | center-to-center variant of `_path_wiring` behind a flag | new, small |
| Uncalibrated analytic surrogate | `analytic_edge_costs` / `analytic_layout_cost` | exists |
| Calibrated surrogate | one-shot ridge residual (`analysis/calibration.py`) | new (C4) |
| Physical-only budget-matched | SA scored by `route_physical_design` with call budget matched to calibration's routing-call count; N=6 only | new, expensive |

### 5.4 Ablations

| Ablation | Supports | Scope |
|---|---|---|
| Port-aware vs center-to-center edges | C1 | MVP |
| Logical crossing proxy vs physical crossing counts | C1/G8 | MVP |
| Uncalibrated analytic vs calibrated surrogate (τ, MAE) | C4 | MVP (load-bearing) |
| Waksman vs Beneš vs Spanke-Beneš | C3 | MVP |
| Incremental SA vs full recompute | spec §9 | **Stretch** (needs new code, G9) |
| With/without calibration rollback | spec §10 | **Stretch** (needs iterative loop) |

### 5.5 Metrics (with source layer)

MRR count, blocked ports, path depth min/avg/max (logical); worst/avg/p95 IL, worst/p05 SXR, SXR-violation count (logical + physical); total waveguide length, bend count, crossing count, DRC violations, area (bbox of cells ∪ routes — **new**, currently uncomputed), failure rate (physical); runtime, physical-routing call count, A*/rip-up pass counts (**new counters**); surrogate MAE and Kendall-τ (surrogate vs physical).

### 5.6 Figures / tables for the paper

- **T1** Topology scaling table (formulas + measured counts, all N).
- **T2** Cross-topology comparison at N=6 and N=8 (all §5.5 metrics; logical and physical columns side by side).
- **T3** Baseline/ablation table at N=6 (per §5.3/§5.4).
- **F1** IL and SXR CDFs on eval set, per topology, N ∈ {6, 8} (extends `il_sxr_cdf.png`).
- **F2** Scaling curves: n_MRR, worst IL, worst SXR vs N (three topologies).
- **F3** Breakeven curves: worst IL/SXR vs crossing-loss-per-crossing, per N ∈ {6, 8} (extends `breakeven_sweep.csv`).
- **F4** Logical-vs-physical scatter (per-path wiring and IL) with MAE/τ annotations, N ∈ {4, 6, 8}; includes crossing-proxy correlation (G8).
- **F5** Routed layout renders per topology at N = 6 and 8 (qualitative; from existing PNG pipeline).
- **F6** Runtime / routing-call / failure-rate scaling vs N (the routability-boundary figure).

---

## 6. Reproducibility Requirements

### 6.1 Commands (post-Phase-6 targets)

```bash
pip install -e .[dev]                     # scipy declared by then
pytest -q && mypy mrr_switch_optimizer    # gates for every phase

# single logical run
python main.py --topology all --n-logical 8 --eval-mode sample \
    --eval-samples 300 --perm-seed 42 --eval --outdir outputs/logical_n8

# Stucture.md §8.7 milestone
python main.py --topology waksman --n-logical 8 --permutation 7,6,5,4,3,2,1,0 --eval-mode sample

# full paper reproduction
python main.py --paper-run configs/paper_v1.yaml
```

### 6.2 Output directory structure

```text
outputs/<run_id>/                  # run_id = <config-name>_<YYYYMMDD>_<seed>
  run_config.json                  # argv, resolved args, all seeds, git SHA, package versions, timestamp
  logical/<topology>_n<N>/         # routing_comparison.csv, eval_summary.csv, eval_path_distribution.csv, states, PNGs
  physical/<topology>_n<N>/        # physical_summary.csv, physical_path_distribution.csv, drc_violations.csv
  surrogate/                       # analytic_edges.csv, calibration_report.csv
  tables/                          # paper T1–T3 CSVs
  figures/                         # F1–F6 PNGs
```

### 6.3 Schemas, seeds, versions, config

- CSV/JSON schemas documented in `docs/schemas.md`; every CSV carries `topology, n_logical, layer, seed` columns; per-path files use the §4.6/§4.7 field lists verbatim.
- Seed discipline: `--split-seed` (default 42), `--perm-seed`, `--sa-seed` all recorded; permutation lists hashed into `run_config.json`; no unseeded RNG anywhere (grep-audited in Phase 6).
- Version logging: `git rev-parse HEAD`, `pyproject` version (currently 2.0.0), Python and package versions.
- Config format: YAML (PyYAML is already a declared dependency, `pyproject.toml:13`), one file per paper run under `configs/`; CLI flags override config values, and the *resolved* config is what gets written to `run_config.json`.

---

## 7. Codex Implementation Roadmap

Ordering: 1 → 2 → 3a → 3b → 3c → 4 → 5 → 6 → 7 → 10 → 8 → 9. Every phase ends with `pytest -q` and `mypy mrr_switch_optimizer` green, and the Phase-1 golden regression passing.

### Phase 1 — Metric/report data model + golden 6×6 regression
- **Objective:** one summary data model; convert "preserve 6×6 behavior" into an executable test.
- **Read first:** `analysis/cost.py`, `app/cli.py`, `Stucture.md` §8.3/§8.6-Phase-1.
- **Modify:** new `analysis/summary.py`, new `app/reports.py`, `app/cli.py` (writers delegated), new `tests/test_reports.py`.
- **Tasks:** define `PathLossBreakdown` / `PermutationSummary` / `TopologySummary` (fields §4.6–4.7, incl. `layer`); move all `_write_*` from cli.py into `reports.py` unchanged; add a golden test that runs the default flow with `MOCK_S_TABLE` and compares `routing_comparison.csv` + `*_states.csv` + `routing_summary.json` against checked-in goldens.
- **Must not change:** any numeric output; `core/`, `routing/`, `placement/` untouched.
- **Commands:** `pytest -q`; `python main.py --topology all --outdir /tmp/p1check`; `mypy mrr_switch_optimizer`.
- **Acceptance:** goldens match exactly; mypy strict passes; cli.py no longer contains csv writing logic.
- **Artifacts:** `tests/golden/` fixtures.

### Phase 2 — Topology constructor parameterization (defaults preserve 6×6)
- **Objective:** `Topology(n_logical)` for all three; generators + formula tests; large-N construction fails *loudly* pending Phase 3.
- **Read first:** `core/topology.py`, `Stucture.md` §8.2, `mrr_switch_prompt.md` §2.
- **Modify:** `core/topology.py`, new `tests/test_topology_nxn.py`.
- **Tasks:** add `build_benes_stage_pairs`, `build_spanke_benes_stage_pairs`; constructors with `n_logical=6` defaults; derived names/counts; assert generated defaults equal current literals; guard: constructing with `n_MRR > 20` raises `NotImplementedError("constructive strategy required — Phase 3")` instead of attempting 2^n_MRR.
- **Must not change:** default-constructed `stage_pairs`, `name`, `n_MRR`, `n_stages`, LUT contents; Phase-1 goldens.
- **Tests:** formula table (§4.2) for N ∈ {2..8, 16}; literal-equality tests; guard test.
- **Acceptance:** all existing tests + goldens pass; `PaddedBenesTopology(4)` and `WaksmanTopology(8)` construct and route via LUT.

### Phase 3a — Strategy isolation + universal verifier
- **Objective:** extract `BruteForceLUTStrategy`; add `verify_state_assignment(topology, π, states)` simulator; `_assert_rnb` gated to N ≤ 6 / opt-in flag.
- **Modify:** `core/topology.py`, new `core/state_assignment.py`, new `tests/test_state_assignment.py`.
- **Acceptance:** goldens pass; verifier confirms all 720 π on all three default topologies; LUT strategy refuses n_MRR > 20.

### Phase 3b — Beneš + Waksman constructive routers
- **Objective:** `BenesLoopingStrategy` (blocked ports as identity) and `WaksmanStrategy` (generator refactored to also emit recursion tree; flattened `stage_pairs` unchanged).
- **Acceptance:** exhaustive N=6 verifier pass; ≥1000 sampled π verified at N=8 and N=16 each; `PaddedBenesTopology(16)` + assignment < 5 s; goldens pass (default topologies may keep LUT strategy to guarantee identical behavior).
- **Artifacts:** runtime comparison CSV (LUT vs constructive).

### Phase 3c — Spanke-Beneš constructive router
- **Objective:** planar sorting-network routing for the brick-wall triangle.
- **Acceptance:** exhaustive N ∈ {4,5,6} verifier pass (LUT cross-check at N ≤ 6); ≥1000 sampled π at N ∈ {8, 16}; states valid on the literal 6×6 topology.

### Phase 4 — CLI `--n-logical` / sampled eval mode
- **Objective:** N×N runs from the CLI; sampling infrastructure (G3, G4).
- **Modify:** `app/cli.py`, `analysis/cost.py` (`sample_permutations`, split refactor), `analysis/activity.py` (sampled mode).
- **Must not change:** zero-argument behavior (byte-identical outputs vs Phase-1 goldens); N=6 seed-42 split identical to today.
- **Acceptance:** `python main.py --topology waksman --n-logical 8 --permutation 7,6,5,4,3,2,1,0 --eval-mode sample` writes `routing_summary.json`, `routing_comparison.csv`, `*_states.csv` (the `Stucture.md` §8.7 milestone); `--eval-mode exhaustive` at N=16 raises cleanly.

### Phase 5 — N×N logical metric validation + caching
- **Objective:** run the full logical matrix (§5.1); `PermutationPlan`/`EvaluationDataset` caching; invariants.
- **Tests:** IL monotone-ish in depth sanity checks; SB `leak_crossing_power == 0`; breakdown sum invariant; cache-equivalence regression (identical numbers with/without cache).
- **Acceptance:** matrix completes; N=16 sampled (300 perms) per topology < 10 min wall-clock on the dev machine (else profile before optimizing further); `outputs/logical_matrix/` populated.

### Phase 6 — Experiment runner + reproducible outputs
- **Objective:** `--paper-run configs/paper_v1.yaml`, `run_config.json`, run-id directories, schema docs; declare `scipy`.
- **Modify:** `app/cli.py`/`app/reports.py`, `pyproject.toml`, `configs/paper_v1.yaml`, `docs/schemas.md`.
- **Acceptance:** two identical-config runs → identical CSVs; `run_config.json` contains git SHA, versions, seeds, permutation-list hashes.

### Phase 7 — Physical routing boundary (N ≤ 8)
- **Objective:** batch physical eval over K=20 sampled perms per (topology, N ∈ {4,6,8}); failure accounting as data; runtime + call counters.
- **Must not change:** any routing algorithm in `routing/` (counters only, injected from the caller if possible).
- **Acceptance:** pipeline never aborts on failed nets; `physical_summary.csv` gains `n_logical`, `runtime_s`, `astar_calls`, `ripup_passes`, `failure_rate`; F6 data exists.

### Phase 10 (MVP, runs before Phase 8) — One-shot surrogate calibration
- **Objective:** ridge residual fit on Phase-7 (analytic features → physical edge cost) pairs; τ/MAE vs uncalibrated analytic on a fixed holdout.
- **Modify:** new `analysis/calibration.py`, `tests/test_calibration.py` (synthetic ground-truth test).
- **Acceptance:** `calibration_report.csv` with τ_analytic, τ_calibrated, MAE, per topology and N; deterministic under fixed seed.

### Phase 8 — Paper tables and figures
- **Objective:** `analysis/paper_figures.py` (or `scripts/`) generating T1–T3, F1–F6 deterministically from a run directory.
- **Acceptance:** one command regenerates every figure/table from `outputs/<run_id>/` with no manual steps.

### Phase 9 — README + reproduction documentation
- **Objective:** fill the empty `README.md` (install, S-param library expectation incl. `MOCK_S_TABLE` fallback, quickstart, flag reference, output glossary); amend `mrr_switch_prompt.md` §0/§2 to v2 scope (G12).
- **Acceptance:** clean-checkout reproduction following only the README succeeds.

---

## 8. Codex-Ready Prompts

### Prompt A — Phase 1

```text
Task: Behavior-preserving metric/report consolidation in /home/jchuang/Optical_switch.
Read first: mrr_switch_optimizer/analysis/cost.py, mrr_switch_optimizer/app/cli.py, Stucture.md section 8.3.
1) Create mrr_switch_optimizer/analysis/summary.py with frozen dataclasses:
   PathLossBreakdown (path_id, layer, input_port, output_port, mrr_count_on_path, mrr_loss_db,
   waveguide_length_um, propagation_loss_db, bend_count, bend_loss_db, crossing_count,
   crossing_loss_db, total_insertion_loss_db, leak_mrr_power, leak_crossing_power, signal_power,
   sxr_db, sxr_violation), PermutationSummary, TopologySummary. layer is Literal["logical","surrogate","physical"].
2) Create mrr_switch_optimizer/app/reports.py and move every _write_* function from app/cli.py into it
   unchanged; cli.py imports them. Do not alter any field name, ordering, or formatting.
3) Add tests/test_reports.py with a golden-file regression: run the default CLI flow using
   mrr_switch_optimizer.core.sparams.MOCK_S_TABLE into a tmp dir and compare routing_comparison.csv,
   routing_summary.json, and each *_states.csv byte-for-byte against fixtures you generate ONCE from
   the current unmodified code and check into tests/golden/.
Constraints: no changes to core/, routing/, placement/, output/; no numeric output may change;
mypy strict must pass.
Validate: pytest -q; mypy mrr_switch_optimizer; python main.py --topology all --outdir /tmp/p1check.
Done when: goldens pass, cli.py contains no csv/json writing bodies, all existing tests pass.
```

### Prompt B — Phase 2

```text
Task: Parameterize topology constructors in /home/jchuang/Optical_switch, preserving 6x6 defaults exactly.
Read first: mrr_switch_optimizer/core/topology.py (all), Stucture.md section 8.2, tests/golden/.
1) Add pure functions build_benes_stage_pairs(n_physical) and build_spanke_benes_stage_pairs(n) in
   core/topology.py. build_benes_stage_pairs(8) must equal the current PaddedBenesTopology.stage_pairs
   literal exactly; build_spanke_benes_stage_pairs(6) must equal the current SpankeBenesTopology literal.
2) Give PaddedBenesTopology, SpankeBenesTopology, WaksmanTopology an __init__(self, n_logical: int = 6)
   that derives name, N_logical, N_physical, n_stages, n_MRR, stage_pairs, and blocked ports.
   Default-constructed instances must have byte-identical name, stage_pairs, n_MRR, n_stages, and LUT
   behavior as today (names stay padded_benes_8x8, spanke_benes_6x6, waksman_6x6).
   Padded Benes: P = 2^ceil(log2(n_logical)); stages = 2*log2(P)-1; n_MRR = (P/2)*(2*log2(P)-1).
   Spanke-Benes: n_MRR = N(N-1)/2, stages = 2N-3.
3) Guard: if the derived n_MRR > 20, __init__ must raise NotImplementedError mentioning that a
   constructive strategy (Phase 3) is required, BEFORE attempting the 2^n_MRR LUT build.
4) Add tests/test_topology_nxn.py: formula table for N in {2,3,4,5,6,7,8,16} per topology (guard-raises
   count as expected outcomes where n_MRR > 20); literal-equality tests for defaults; a test that
   PaddedBenesTopology(4) and WaksmanTopology(8) construct, pass _assert_rnb, and route a sample permutation.
Constraints: do not modify _build_lut logic, models.py, layout.py, cli.py; golden regression from
tests/golden/ must still pass.
Validate: pytest -q; mypy mrr_switch_optimizer.
Done when: all above tests pass and default 6x6 outputs are unchanged.
```

### Prompt C — Phase 3a

```text
Task: Isolate brute-force state assignment behind a strategy interface with a universal verifier.
Read first: mrr_switch_optimizer/core/topology.py, tests/test_topology_nxn.py, Stucture.md section 8.5/8.6.
1) Create mrr_switch_optimizer/core/state_assignment.py with:
   - StateAssignmentStrategy Protocol: assign(topology, permutation) -> StateAssignment
   - BruteForceLUTStrategy implementing the current _build_lut behavior, refusing n_MRR > 20
   - verify_state_assignment(topology, permutation, states) -> bool: simulate wires through
     topology.stage_pairs applying states and stage fixed permutations (reuse the exact semantics of
     RNBTopology._build_lut's inner loop and _physical_target padding) and check the realized
     input->output mapping equals the padded target.
2) RNBTopology.get_state_assignment delegates to a strategy instance chosen in __init__
   (BruteForceLUTStrategy by default so behavior is unchanged). _assert_rnb runs only when
   N_logical <= 6, or when verify_rnb=True is passed.
3) tests/test_state_assignment.py: for each default topology, verify_state_assignment holds for all
   720 permutations; BruteForceLUTStrategy equals the pre-refactor LUT on spot-checked permutations;
   the n_MRR > 20 refusal is tested.
Constraints: golden regression passes; no public API removals; mypy strict.
Validate: pytest -q; mypy mrr_switch_optimizer.
Done when: strategies are swappable per instance and all tests pass.
```

### Prompt D — Phase 3b

```text
Task: Constructive state assignment for padded Benes and Waksman (no 2^n_MRR).
Read first: core/topology.py (build_waksman_stage_pairs and _build_waksman_for_wires),
core/state_assignment.py, tests/test_state_assignment.py.
1) BenesLoopingStrategy: standard Benes looping (Opferman-Tsao-Wu) over P = N_physical wires for the
   butterfly stage_pairs produced by build_benes_stage_pairs. Blocked ports (indices N_logical..P-1,
   see RNBTopology._physical_target) are routed as identity. Emit states keyed by
   topology.mrr_id(stage_idx, pair).
2) WaksmanStrategy: refactor _build_waksman_for_wires so it can also return the recursion tree
   (sub-network wire lists per level) WITHOUT changing the flattened stage_pairs output (add a
   parallel function or a richer return consumed only by the strategy). Implement recursive Waksman
   setting on that tree; the removed redundant switch per even sub-network is the last outer output
   pair (see the outer_pairs[:-1] logic) and is fixed to bar.
3) Tests: for PaddedBenesTopology() and WaksmanTopology() run verify_state_assignment on ALL 720
   permutations with the new strategies; for PaddedBenesTopology(16), WaksmanTopology(16),
   WaksmanTopology(8): 1000 seed-fixed sampled permutations each, all verified; construction plus one
   assignment at N=16 completes in under 5 seconds; lift the Phase-2 n_MRR > 20 guard for topologies
   constructed with a constructive strategy.
Constraints: default-constructed topologies keep BruteForceLUTStrategy (golden regression must pass);
stage_pairs outputs unchanged; no changes outside core/ and tests/.
Validate: pytest -q; mypy mrr_switch_optimizer.
Done when: all verifier tests pass at N in {6, 8, 16}.
```

### Prompt E — Phase 4

```text
Task: CLI N-logical support and sampled permutation protocol, preserving zero-arg behavior.
Read first: app/cli.py (_parse_args, _parse_permutation, main), analysis/cost.py
(make_permutation_split), analysis/activity.py (scan_mrr_activity), tests/golden/.
1) Add sample_permutations(n, k, seed) to analysis/cost.py: fixed-seed, duplicate-free, deterministic.
   Rework make_permutation_split so that for n_logical <= 8 with mode "exhaustive" it reproduces
   today's behavior EXACTLY (same seed-42 shuffle of the full S_n list -> identical 500/220 split at
   n=6), and for mode "sample" it draws disjoint train/eval sets via sample_permutations.
2) CLI: add --n-logical (default 6), --eval-mode {exhaustive,sample} (default: exhaustive for
   n<=6, sample otherwise), --eval-samples (default 300), --train-samples (default 500),
   --perm-seed (default 42). _parse_permutation validates against range(n) where n is inferred from
   the permutation length; it must equal --n-logical when both are given. Topology selection passes
   n_logical to the constructors. scan_mrr_activity gains an optional permutations argument for the
   sampled mode and a permutations_scanned count in its rows.
3) Guards: --eval-mode exhaustive with n_logical > 8 exits with a clear error.
Constraints: running with NO arguments must produce byte-identical outputs to tests/golden/;
mypy strict passes.
Validate: pytest -q; mypy mrr_switch_optimizer; then the milestone:
python main.py --topology waksman --n-logical 8 --permutation 7,6,5,4,3,2,1,0 --eval-mode sample
must write routing_summary.json, routing_comparison.csv, and *_states.csv without touching physical routing.
Done when: milestone command succeeds and golden regression passes.
```

---

## 9. Risks, Kill Criteria, And Scope Control

**R1 — Spanke-Beneš constructive routing is the hardest algorithmic piece** (non-standard, spec §3 explicitly avoided it at N=6 by using the LUT).
*Kill criterion:* if after two focused Codex attempts the verifier still fails on exhaustive N ≤ 6, stop.
*Fallback:* SB stays LUT-backed at N ≤ 6 (n_MRR = 15 → 2^15 is fine) and is reported at N ∈ {8, 16} only for closed-form counts (n_MRR, stages, depth) — the scaling table survives; per-permutation IL/SXR for SB large-N is dropped and stated as a limitation.

**R2 — Physical routing may not produce clean routes at N = 8** (28 SB MRRs, 13 stages; denser corridors).
*Kill criterion:* >50% net failure rate on default layouts after reasonable rule tuning (no router code changes).
*Claim adjustment:* physical validation narrows to N ∈ {4, 6}; the failure-rate-vs-N curve (F6) is *promoted* to a finding — "the routability boundary of Manhattan single-layer MRR fabrics" — rather than hidden.

**R3 — Calibration may not improve Kendall-τ** (spec §11 calls this the claim-killing outcome).
*Kill criterion:* τ_calibrated − τ_analytic below noise (bootstrap CI overlapping zero) on two of three topologies.
*Scope reduction:* drop the "calibrated" claim from C4 and reframe as *quantifying the logical–physical fidelity gap* (MAE/τ of the analytic surrogate is still a publishable characterization); the paper thesis loses the word "calibrated" but C1–C3 stand.

**R4 — Waksman recursion→stage mapping bugs** (merged stages, non-adjacent pairs, `mrr_id` collisions).
*Mitigation:* invariant tests — unique `mrr_id`s, disjoint pairs per stage, generator output unchanged by the tree refactor. Caught entirely by the Phase-3b verifier if wrong.

**R5 — Logical-eval runtime blow-up at N = 16** (crossing counting is O(paths²·segments²), `analysis/cost.py:311–326`; SB N=16 has 120 MRRs / 29 stages).
*Kill criterion:* logical matrix cell exceeding ~1 hour.
*Fallback:* reduce eval sample to 100 for N=16 before touching code; only then add segment bucketing (still no router changes).

**R6 — Scope creep into `routing/physical.py`** (5,329 lines, 2,980-line test file, private-API coupling).
*Control:* hard rule in every Codex prompt — `routing/` is read-only until after first submission. Any physical-layer shortfall is handled by narrowing claims (R2), never by router surgery.

**R7 — Golden-regression brittleness** (float formatting differences masquerading as behavior changes).
*Control:* goldens compare parsed values at fixed precision (the current writers already format, e.g. `.4f`/`.6f` in cli.py:499–513, 826–837); any intentional schema change requires regenerating goldens in the same PR with an explicit note.

---

## 10. Final Recommended Next Step

Hand **Phase 1 (Prompt A)** to Codex now. It is the only phase with zero algorithmic risk, it touches nothing numerical, and it produces the golden 6×6 regression fixtures that convert the project's hardest constraint — "current 6×6 behavior must not change" — from a hope into an executable gate. Every subsequent phase (constructor parameterization, strategy extraction, CLI changes) is only safe to hand to Codex once that gate exists, and the summary data model it introduces is the substrate all paper tables and figures (T1–T3, F1–F6) are generated from.
