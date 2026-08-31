# Claude Prompt: Paper-Grade Planning For MRR Switch Optimizer

Act as a research planner and technical PM for a paper in photonic integrated circuits, optical switch synthesis, and CAD for photonics. Your task is not to implement code directly. Your task is to plan this project to a level suitable for an academic paper submission, and to produce an implementation plan that can later be handed to Codex phase by phase.

## Project Path And Required Reading

Project path:

```text
/home/jchuang/Optical_switch
```

Read and cite facts from the following files first. Do not invent assumptions:

```text
/home/jchuang/Optical_switch/Stucture.md
/home/jchuang/Optical_switch/mrr_switch_prompt.md
/home/jchuang/Optical_switch/pyproject.toml
/home/jchuang/Optical_switch/mrr_switch_optimizer/core/topology.py
/home/jchuang/Optical_switch/mrr_switch_optimizer/analysis/cost.py
/home/jchuang/Optical_switch/mrr_switch_optimizer/placement/layout.py
/home/jchuang/Optical_switch/mrr_switch_optimizer/app/cli.py
/home/jchuang/Optical_switch/tests/test_physical_router.py
```

Pay special attention to Section 8 of `Stucture.md`, `N×N Generalization / Refactor Notes`. The current project is a fixed 6×6 / padded 8×8 MRR switch optimizer prototype. The next goal is to refactor it so it can support parameterized `N×N input/output` designs, while computing MRR count, insertion loss, SXR, physical routing metrics, baseline ablations, and topology comparisons.

## Known Technical Context

The current core direction is:

```text
Topology-Agnostic Port-Aware MRR Optical Switch Synthesis
with calibrated surrogate / physical routing validation
```

The existing architecture roughly includes:

- `core/topology.py`: Padded Beneš, Spanke-Beneš, and Waksman topologies. Several parts are currently fixed to N=6 / padded physical N=8.
- `core/models.py`: add-drop MRR cell, port geometry, port mapping, and state semantics.
- `analysis/cost.py`: logical path insertion loss, SXR, crossing leakage, and LSE aggregate cost.
- `placement/layout.py`: physical MRR placement generation from topology.
- `placement/sa.py` / `placement/lp.py`: placement optimization.
- `routing/physical.py`: waveguide-aware Manhattan physical router, DRC, crossings, and rip-up.
- `app/cli.py`: CLI orchestration, CSV/JSON/PNG/GIF reports.

Known major problems:

- `RNBTopology._build_lut()` builds LUTs by brute-forcing `2^n_MRR` states. This only works for small N and cannot be the true `N×N` method.
- `PaddedBenesTopology` and `SpankeBenesTopology` have hard-coded 6/8 stage pairs.
- `WaksmanTopology` has a general stage-pair generator, but the class is still fixed to `N_logical=6`.
- The CLI parser requires permutations to be exactly `0..5`.
- `make_permutation_split()` and `scan_mrr_activity()` exhaustively enumerate `N!` permutations, which is infeasible for larger N.
- Documentation and report structure are not yet sufficient for paper-grade reproducibility.

## Your Goal

Produce a paper-grade project plan that Codex can later implement step by step. The plan must be concrete. Do not provide only high-level suggestions.

Prioritize answering this central question:

```text
What is the minimum publishable contribution needed to raise this prototype
to paper-submission quality?
What code refactors, algorithms, experiments, figures, ablations, and validation
are required?
How should each item be handed to Codex for implementation and acceptance?
```

## Paper-Grade Standard

Review the project using strict academic standards. Cover at least:

1. A clear research problem statement.
2. Explicit technical contributions. Do not repackage engineering chores as research contributions.
3. Comparability against baselines and ablations.
4. A reproducible experiment protocol.
5. Quantitative metrics.
6. Figures and tables that can support the paper claims.
7. Failure cases and limitations.
8. Theoretical and experimental discussion of `N×N` scalability.
9. Consistent calculation of insertion loss, crosstalk, and physical routability.
10. A clear distinction between logical estimates, surrogate estimates, and physical routing results.

## Required Output Format

Use the following exact structure.

### 1. One-Sentence Paper Thesis

State the paper's central claim in one sentence.

### 2. Minimum Publishable Contribution

List the 3 to 5 contributions required for the minimum publishable version. For each contribution, include:

- Claim
- Why it is non-trivial
- Evidence required
- Code / experiment artifact required

If the current scope is too large, separate it into:

- `MVP paper scope`
- `Stretch scope`
- `Do not do before first submission`

### 3. Gap Analysis From Current Code

Based on the actual files, identify the current gaps. Each gap must include:

- Current state
- Why it blocks paper quality
- Required change
- Files likely involved
- Validation method

### 4. N×N Architecture Plan

Propose a refactor architecture that Codex can implement. Include at least:

- Parameterized topology constructor design
- `n_MRR` / `n_stages` / blocked port calculation
- Constructive state assignment strategy that avoids `2^n_MRR` brute force
- Permutation sampling / exhaustive mode boundary
- Path / state / metric caching
- Insertion loss breakdown data model
- SXR / crosstalk breakdown data model
- Physical routing scalability boundary

Clearly state which items should not be touched in the first phase, in order to reduce risk.

### 5. Experiment Matrix

Design an experiment matrix sufficient to support the paper claims. Include at least:

- Topologies: Padded Beneš, Spanke-Beneš, Waksman.
- N values: for example 4, 6, 8, 16. If a topology is unsuitable for a specific N, explain why.
- Permutation protocol: exhaustive vs sampled, sample size, random seed.
- Baselines:
  - naive grid placement
  - port-unaware graph cost
  - uncalibrated analytic surrogate
  - calibrated surrogate
  - physical-only budget-matched baseline
- Ablations:
  - port-aware vs center-to-center
  - logical crossing only vs physical crossing calibrated
  - incremental SA update vs full recompute
  - with/without calibration rollback
  - Waksman / Beneš / Spanke topology comparison
- Metrics:
  - MRR count
  - path depth
  - worst / average insertion loss
  - worst / percentile SXR
  - SXR violation count
  - total waveguide length
  - bend count
  - crossing count
  - DRC violations
  - runtime
  - physical routing call count
  - surrogate MAE
  - Kendall tau ranking quality
  - area
- Figures / tables required for the paper.

### 6. Reproducibility Requirements

List the repository commands and outputs required for reproducible experiments. For example:

```text
python main.py --paper-run ...
python main.py --topology all --n-logical 8 --eval-mode sample ...
```

Specify output directory structure, CSV/JSON schemas, random seed discipline, version logging, and config file format.

### 7. Codex Implementation Roadmap

Break the follow-up work into phases that can be handed to Codex. Each phase must include:

- Objective
- Files to read first
- Files likely to modify
- Exact tasks
- Must-not-change constraints
- Tests to add/update
- Commands to run
- Acceptance criteria
- Expected artifacts

Avoid making any phase too large. Each phase should be independently completable while keeping tests passing.

Recommended phases should include at least:

1. Metric / report data model cleanup.
2. Topology constructor parameterization with defaults that preserve current 6×6 behavior.
3. Brute-force LUT isolation and constructive state-assignment strategy.
4. CLI `--n-logical` / permutation parsing / sampled eval mode.
5. N×N logical metric validation without physical routing.
6. Experiment runner and reproducible outputs.
7. Physical routing integration boundary for selected small N.
8. Paper table / figure generation.
9. Documentation and README for reproduction.

### 8. Codex-Ready Prompts

Finally, produce 3 to 5 prompts that can be pasted directly into Codex. Each prompt should cover exactly one phase and include:

- task scope
- exact file/module references
- constraints
- validation commands
- done criteria

These prompts should let Codex start implementation without asking many follow-up questions.

### 9. Risks, Kill Criteria, And Scope Control

List:

- The highest-risk technical points
- When to stop pursuing a given path
- How to adjust the paper claim if physical routing cannot produce clean routes for large N
- What short-term fallback to use if constructive state assignment is too difficult
- How to reduce the paper scope if surrogate calibration does not improve Kendall tau

### 10. Final Recommended Next Step

End by clearly stating the first phase that should be handed to Codex next, and why.

## Important Constraints

- Do not implement code directly.
- Do not give generic research advice.
- Do not assume the current code already supports `N×N`.
- Do not require all work to be completed at once.
- Do not make clean physical routing for large N a first-phase hard requirement.
- Every plan item must map to concrete files, tests, commands, and output artifacts.
- Preserving current 6×6 regression behavior is a hard constraint.
- Every future Codex phase must be independently verifiable.

