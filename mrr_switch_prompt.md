# MRR Switch Synthesis — Canonical Spec

> **Topology-Agnostic Port-Aware MRR Optical Switch Synthesis with Calibrated Surrogates**

Single source of truth. All implementation must conform to the sections below.

---

## §0 Scope & Fixed Assumptions

```text
W = 1,  λ = λ₀                              (single wavelength, space-division)
s_i ∈ {0, 1}                               (0 = through/detuned, 1 = drop/resonant)
Unidirectional propagation (input → output)
θ_i = 0                                    (no MRR rotation, v2)
No process variation; no thermal coupling beyond hard spacing constraint
No device-level BO; MRR cells from fixed library L_MRR
N_logical is parameterized; topologies are padded Beneš, Waksman, and Spanke-Beneš
Physical routing claims are bounded to N_physical <= 8 for v2
```

These assumptions are locked for v2. Extensions (WDM, process variation, rotation, N=16 physical routing) go to future work and must be flagged `# FUTURE` in code.

---

## §1 MRR Library Cell

Every MRR is a library cell — not an abstract node.

```python
@dataclass(frozen=True)
class MRRCell:
    id:       str
    center:   tuple[float, float]           # (x_um, y_um)
    ports:    dict[str, PortDef]            # keys: "in", "th", "add", "drop"
    s_table:  STable                        # (port_in, port_out, state) → power ∈ [0,1]
    bbox:     BBox
    d_min_th: float                         # thermal spacing constraint (μm)

@dataclass(frozen=True)
class PortDef:
    dx: float; dy: float                    # offset from cell center
    phi: float                              # orientation (rad)
    kind: str                               # "in" | "th" | "add" | "drop"
```

Global port position: `p_global = cell.center + (dx, dy)`

### S-table (v1 mock values)

| port_in | port_out | state | power |
|---|---|---|---|
| in | th   | 0 (through) | 0.95  |
| in | drop | 0 (through) | 0.003 |
| in | th   | 1 (drop)    | 0.05  |
| in | drop | 1 (drop)    | 0.85  |
| add | drop | 0           | 0.95  |
| add | th   | 0           | 0.003 |
| add | drop | 1           | 0.05  |
| add | th   | 1           | 0.85  |

Mock data is acceptable for v1. Pipeline correctness takes priority over calibrated values.

---

## §2 Topology Abstraction Layer

All supported topologies implement the same interface. This is the foundation of the topology-agnostic evaluation layer.

```python
class RNBTopology:
    N_logical:  int    # active port count
    N_physical: int    # actual port count  (padded for Padded Beneš)
    n_MRR:      int    # total MRR count
    n_stages:   int    # worst-case path depth

    def get_state_assignment(self, π: Permutation) -> StateAssignment: ...
    def get_active_paths(self, π, σ) -> list[Path]: ...
    def get_mrr_stage(self, mrr_id: str) -> int: ...
    def has_native_crossings(self) -> bool: ...
```

### §2a — Padded Beneš

```text
N_logical is parameterized
N_physical = P = 2^ceil(log₂(N_logical))
n_MRR    = (P/2) × (2·log₂(P)−1)
n_stages = 2·log₂(P)−1
Blocked:  input/output ports {N_logical..P−1}
Routing:  standard recursive Beneš looping algorithm
has_native_crossings = True
```

Default v2 smoke case remains `N_logical = 6`, `N_physical = 8`,
`n_MRR = 20`, `n_stages = 5`.

### §2b — Native Spanke–Beneš

```text
N_logical is parameterized
N_physical = N_logical
n_MRR    = N(N−1)/2
n_stages = 2N−3
Routing:  planar odd/even transposition construction
has_native_crossings = False
```

Default v2 smoke case remains `N_logical = N_physical = 6`,
`n_MRR = 15`, `n_stages = 9`.

### §2c — Waksman

```text
N_logical is parameterized
N_physical = N_logical
n_MRR    = sum(len(stage_pairs))
n_stages = len(stage_pairs)
Routing:  recursive Waksman state assignment over the generated stage tree
has_native_crossings = True
```

Default v2 smoke case remains `N_logical = N_physical = 6`,
`n_MRR = 11`, `n_stages = 5`.

---

## §3 Permutation LUT

```text
Π_all = S₆,  |S₆| = 720

Offline pre-generate for each topology:
    LUT: π → σ_π,   ∀π ∈ S₆

Train/eval split (fixed seed):
    |Π_train| = 500,   |Π_eval| = 220,   Π_train ∩ Π_eval = ∅
```

N=6 is small enough to generate all 720 permutations offline. This avoids routing-algorithm risk (especially SB routing, which is non-standard) and ensures surrogate generalization is tested on unseen permutations.

---

## §4 Port-Aware Graph

**Contribution 1.** Every routing edge is port-to-port, not center-to-center.

```text
e = (M_i, port_a) → (M_j, port_b)

Estimated geometric length:
    l̂_e = |x_{i,a} − x_{j,b}| + |y_{i,a} − y_{j,b}|

Active path for permutation π:
    P(π) = [p₁, p₂, ..., p_{N_logical}]
    Each path p = sequence of (MRR, in_port, out_port) + connecting edges
```

Center-to-center estimation misses the rotational asymmetry of the 4-port MRR geometry and can undercount wiring penalty by up to 4×.

---

## §5 Cost Model

### §5.1 Path Insertion Loss

```text
IL(p) = Σ_{e∈p} L_e  +  Σ_{M_i∈p} L_MRR(M_i, a→b, s_i)

L_e       = α·l_e + Σ_bends L_bend(R_q, θ_q) + N_cross(e)·L_cross_unit
L_MRR(·)  = −10·log₁₀ S_i(port_a, port_b, s_i)      ← from S-table
```

### §5.2 Signal-to-Crosstalk Ratio (incoherent power-domain)

```text
SXR(p_i) = 10·log₁₀( P_sig(p_i) / (Σ_{j≠i} P_leak(p_j→p_i) + ε) )
```

**Leak mechanism (a) — MRR finite extinction** (shared MRR, different entry port):

```text
P_leak^(m)(p_j→p_i) = P_j^@m · S_m(port_{p_j}^in, port_{p_i}^out, s_m)
```

Reads the same S-table entry — no extra model needed.

**Leak mechanism (b) — Waveguide crossing** (edges physically cross):

```text
P_leak^(cross)(p_j→p_i) = P_j^@cross · χ_cross
χ_cross = 10^(XT_cross/10),   XT_cross = −40 dB  →  χ = 1×10⁻⁴
```

For Spanke–Beneš: `has_native_crossings = False` → mechanism (b) = 0 at logical level. It can appear only after physical routing, where the calibration loop will capture it.

---

## §6 LSE Aggregation

```text
# Within one permutation
C(L, π) = (1/β_LSE) · log Σ_{p∈P(π)} exp(β_LSE · C_path(p))

C_path(p) = ω_IL · IL(p)  +  ω_XT · max(0, SXR_min − SXR(p))

# Across training permutations
C(L) = (1/β_Π) · log Σ_{π∈Π_train} exp(β_Π · C(L, π))
```

**v1 hyperparameter discipline:** both β values are fixed constants. Only SA temperature T is annealed. Changing two hyperparameters simultaneously makes root-cause debugging intractable.

LSE gives every path a nonzero gradient contribution. Hard-max concentrates signal on the single worst path and wastes most SA moves.

---

## §7 Hard Constraints

These are binary physical feasibility checks. Violating any one → **SA rejects the move**. They must never be converted to soft penalties.

```text
‖c_i − c_j‖₂ ≥ d_min_th,    ∀i≠j        # thermal spacing
R_bend ≥ R_min                             # minimum bend radius
Route(e) ∩ Blockage = ∅                   # DRC clean
∀π ∈ Π_eval: topology.get_state_assignment(π) physically routable  # RNB preserved
```

---

## §8 Surrogate Model

**Contribution 3.** Analytic prior + ridge regression residual. Even when the residual is zero, the analytic baseline is physically meaningful.

```text
Ĉ_θ(e) = C_analytic(e) + r_θ(e)

C_analytic(e) = α·l̂_e + β·B̂_e + γ·Ĉ_e + η·Â_e + μ·D̂_e
    l̂_e  : Manhattan port-to-port length
    B̂_e  : estimated bend count (from port direction mismatch)
    Ĉ_e  : estimated crossing count (from edge geometry)
    Â_e  : port alignment penalty  f(|φ_a − φ_b|)
    D̂_e  : local density penalty

Features for r_θ (ridge regression):
    x_e = [Δx, Δy, |Δx|+|Δy|, Δφ,
           onehot(port_a), onehot(port_b),
           stage_idx, local_density, blockage_ratio, B̂_e, Ĉ_e]
```

### Calibration Loss & Monitoring

```text
L_cal(θ) = Σ_{e∈D} (C_analytic(e) + r_θ(e) − C_phys(e))²  +  λ‖θ‖²

Monitor:  τ_t = Kendall-τ( Ĉ_{θ_t}, C_phys )  on holdout set
```

Train with MSE; evaluate with Kendall-τ. SA uses ranking, not absolute values, so τ is the signal that matters.

### Rollback

```text
checkpoint θ before each update.
if τ_{t+1} < τ_t − 0.10:  θ ← θ_t   (mandatory rollback)
```

---

## §9 SA Placement Loop

**Variables:** `L = {c_i}_{i=1}^{n_MRR}`

### Moves (exactly four in v1)

```text
1. Move one MRR:      c_i ← c_i + Δ
2. Swap two MRRs:     c_i ↔ c_j
3. Stage-wise shift:  translate entire stage column
4. Local spreading:   push nearby MRRs apart to relieve density
```

### Incremental Evaluation — Contribution 4

Recomputing all paths on every move is O(n_MRR · |Π_train| · path_length) and makes the cross-path crosstalk objective intractable. Instead, maintain an affected set:

```text
A(m_i) = { (π, p) | m_i ∈ p,  π ∈ Π_train }

On move of m_i, recompute only:
    Affected(m_i) = A(m_i)  ∪  ⋃_{(π,p)∈A(m_i)} LeakNeighbors(p)

LSE accumulator Z(π) = Σ_p exp(β·C(p))  updated incrementally.
```

`LeakNeighbors(p)` = paths that share an MRR or a crossing with `p`. Omitting it silently drops all cross-path crosstalk signal.

### Acceptance

```text
P(accept) = min(1, exp(−ΔC / T))
T schedule: geometric cooling,  T_{k+1} = 0.95·T_k,  ~10⁴ moves/round
```

---

## §10 Physical Routing & Calibration Loop

### Candidate Selection (per round)

```text
M₁ = 3  candidates with best surrogate cost
M₂ = 3  candidates with highest surrogate uncertainty  (ensemble disagreement or residual variance)
M₃ = 3  candidates most diverse from current best
Total: 9 physical routing calls per round
```

### Physical Routing (gdsfactory)

For each candidate layout, run full gdsfactory routing and extract:

```text
C_phys(e) = α·L_e^real + β·N_bend^real + γ·N_cross^real + δ·N_DRC^real
```

### Update Cycle

```text
1. checkpoint θ_t
2. run ridge regression update on all accumulated (x_e, C_phys(e)) pairs
3. compute τ_{t+1} on holdout
4. if τ_{t+1} < τ_t − 0.10:  rollback θ ← θ_t
```

### Stopping Criteria (either triggers stop)

```text
|τ_t − τ_{t-1}| < 0.02   for 3 consecutive rounds
best C_phys(L*) shows no improvement for 5 consecutive rounds
t ≥ T_max = 20 rounds
```

---

## §11 Final Validation & Topology Comparison

### Per-Topology Metrics (on Π_eval — unseen 220 permutations)

```text
Insertion loss:   worst-case IL, average IL, IL distribution (CDF)
Crosstalk:        worst-case SXR, SXR violation count (< SXR_min)
Physical layout:  total waveguide length, bend count, crossing count, area
Reliability:      DRC violations, thermal spacing violations
Efficiency:       physical routing call count, total runtime
Surrogate:        final MAE, Kendall-τ, speedup vs physical-only baseline
```

### Baseline Ablation (run for each topology)

| Baseline | What it proves |
|---|---|
| Naive grid placement | layout floor — no optimization |
| Port-unaware graph SA | contribution 1: port-aware edges matter |
| Uncalibrated analytic surrogate | **main ablation** — contribution 3: calibration must improve over analytic-only |
| Physical-only (budget-matched) | contribution 3: surrogate gives runtime value |

The "uncalibrated analytic surrogate" baseline is the load-bearing ablation. If calibration does not improve over it, the paper's core claim collapses.

### Topology Cross-Comparison (key figure)

| Metric | Padded 8×8 Beneš | Native 6×6 Spanke–Beneš | Winner |
|---|---|---|---|
| MRR count | 20 | 15 | SB |
| Path depth (stages) | 5 | 9 | Beneš |
| Worst IL (dB) | — | — | TBD |
| Worst SXR (dB) | — | — | TBD |
| Crossing count | — | — | TBD |
| Physical area | — | — | TBD |
| Calibration rounds | — | — | TBD |

**Breakeven analysis:** identify the `L_MRR_through / L_cross` ratio at which each topology becomes preferred. This elevates the comparison from "who wins" to "under what fabrication process does each win" — a stronger engineering claim.

---

## §12 Coding-Ready Flow

```text
Inputs:
    N_logical = 6
    topology  ∈ {PaddedBenes, SpankeBenes}      ← run both
    MRR library (mock S-table acceptable for v1)
    Physical constants:  α, β, γ, η, μ, L_cross_unit, R_min, d_min_th
    Objective weights:   ω_IL, ω_XT, SXR_min, β_LSE, β_Π
    Permutations:        Π_train (500), Π_eval (220), fixed seed
    SA schedule:         T₀, cooling = 0.95, n_moves ≈ 10⁴/round
    Calibration:         M₁=M₂=M₃=3, T_max=20, τ_rollback=0.10, τ_conv=0.02

Step 0   Fix scope (§0). Fail fast on any W>1 or rotation code.
Step 1   Build MRR library cells with port geometry and S-table.
Step 2   Instantiate both RNBTopology objects.
Step 3   Pre-generate permutation LUT for each topology.
Step 4   Build port-aware routing graph (§4).
Step 5   Initialize surrogate θ=0; Ĉ_θ(e) = C_analytic(e).

Step 6   Outer calibration loop  t = 0, 1, ..., T_max:
  6a   Run SA with current θ:
         propose move → check hard constraints (§7) → reject if violated
         update affected set incrementally (§9)
         recompute IL, SXR, C_path, LSE
         Metropolis accept/reject
  6b   Select 9 candidate layouts (§10).
  6c   Physical routing via gdsfactory; extract C_phys(e) per edge.
  6d   Checkpoint θ. Ridge regression update. Evaluate τ_{t+1}.
  6e   Rollback if τ drops > 0.10. Check stopping criteria.

Step 7   Final layout = argmin C_phys over all routed candidates.
Step 8   Validate on Π_eval with full physical routing on best layout.
Step 9   Compute all metrics (§11). Run all four baselines.
Step 10  Repeat Steps 0–9 for the other topology.
Step 11  Produce cross-comparison table + breakeven analysis.

Outputs (per topology):
    optimal placement L*
    state-assignment LUT σ
    GDS from gdsfactory
    IL/SXR distribution on Π_eval
    baseline ablation table
Final output:
    cross-topology comparison table + breakeven curve
```

---

## §13 Red Flags

```text
Scope:
  Any W>1, multi-wavelength, or coherent crosstalk code in v1
  Only one topology implemented (must be both)
  θ_i ≠ 0 without explicitly marking FUTURE

Graph:
  Edge length estimated center-to-center instead of port-to-port
  Crossing leak counted for Spanke–Beneš at logical level

Cost & Aggregation:
  β_LSE or β_Π scheduled simultaneously with SA temperature T
  Pure black-box model replaces analytic term in surrogate
  Surrogate quality assessed only by MSE, not Kendall-τ

SA:
  Hard constraint violation handled as soft penalty term
  Full path recomputation on every move (not incremental)
  LeakNeighbors excluded from affected set
  Affected set reused across non-adjacent moves without invalidation

Calibration:
  Rollback skipped when τ drops by more than 0.10
  Fewer or more than 9 candidates per round without documented reason
  Physical routing replaced by analytic estimate during calibration rounds
  Only one stopping criterion implemented
  Train/eval sets overlap or seed differs across runs

Validation:
  "Uncalibrated analytic surrogate" baseline omitted (kills main claim)
  Breakeven analysis missing from topology comparison
  Π_eval permutations used during training
```

---

## §14 Physical Router — Waveguide-Aware Routing Constraints

This section specifies correctness requirements for the Manhattan A\* physical router
(`router.py`). All items below are **hard requirements** for DRC-clean, physically
fabricable layouts.

---

### §14.1 Root Causes of Routing Failures

Two distinct failure modes arise when multiple routes share an inter-stage corridor:

**Failure A — Touching-corner (T-junction DRC violation)**
One route's A\* segment passes through the INTERIOR of another route's bend point
(waypoint). This creates a T-junction: one endpoint of route A lands on the interior
of route B, or vice versa. The DRC rule `touching_corner` fires.

Root cause: A\* routes sequentially. Route B does not know that route A will place a
bend at a specific x-column. Both routes independently choose nearby x-columns for
their bends, and one ends up passing through the other's corner.

**Failure B — Corridor knot (rectangular loop)**
Route A turns too late in the corridor (bend close to the right-side cell column).
Route A's horizontal output segment then blocks route B from travelling along the same
y-level. Route B must detour: travel past route A's bend, turn, come back, forming a
rectangular loop with route A. This also manifests as `touching_corner` at the corners
of the rectangle, and produces routes with excessive bend count.

Root cause: A\* minimises Manhattan distance independently per route. Without knowledge
of later routes, route A freely selects a late bend position that maximally blocks the
corridor for route B.

---

### §14.2 Port-Aware Corridor Staggering

**Principle:** In each inter-stage corridor, routes whose y-destination is lower should
make their vertical transition EARLIER (smaller x); routes whose y-destination is higher
should transition LATER (larger x). This is the Manhattan routing analogue of VLSI
dogleg ordering.

**Why this fixes Failure B:**
After staggering, no route's horizontal output segment overlaps the vertical transition
zone of any other route in the same corridor. Routes cross each other's y-levels cleanly
without one blocking the other's path.

**Implementation — occupied bend points in `_route_physical_order`:**

Before routing each net, accumulate the intermediate waypoints (bend points) of all
already-routed nets into a set `occupied_bends`. Pass this set as additional
`forbidden_points` for the current net's A\* calls.

```python
occupied_bends: set[Point] = set()

for path in order:
    net_forbidden = (forbidden_points_by_input or {}).get(path.input_port, set())
    route = _route_one_path(
        ...,
        forbidden_points=net_forbidden | occupied_bends,
    )
    routes.append(route)
    occupied.extend(route.external_segments)
    occupied.extend(route.local_segments)
    # Register this route's bend points (intermediate waypoints only, not src/dst)
    occupied_bends.update(route.waypoints[1:-1])
```

**Effect on A\*:** When the current net's A\* proposes a segment whose interior contains
a point in `occupied_bends`, `_segment_hits_forbidden_point` returns True and the
segment is blocked. The A\* naturally finds a path that bends at a different x-column —
the "advance the turn point" behaviour described in §14.3.

**Scope:** `occupied_bends` is rebuilt fresh each routing pass (each call to
`_route_physical_order`). It covers only the **external** waypoints (inter-stage
corridor bends), not local cell-internal segment points.

**Interaction with `allowed_touch_points`:** The A\* for each segment already passes
`allowed_touch_points=(src, dst)`. Cell escape points are always the src or dst of an
A\* call, so they are always in `allowed_touch_points` and are never blocked even if
they coincidentally appear in `occupied_bends`.

---

### §14.3 A\* Backtrack Interpretation

The "backtrack to last turn point, advance forward, re-turn" behaviour described
informally maps directly onto the standard A\* mechanism once §14.2 is in place:

- Blocking a segment that passes through an occupied bend → that A\* branch is pruned.
- The A\* heap contains alternative branches that bent at EARLIER or LATER x-columns.
- The branch with a bend at a free x-column is the lowest-cost surviving path.

No explicit backtracking logic or state machine is needed. The A\* handles it
implicitly through the priority queue.

---

### §14.4 Minimum Bend-to-Bend Distance (Hard Block)

**Physical constraint:** A waveguide executing two consecutive 90° bends requires a
minimum straight segment between them equal to `2 × bend_radius_um` (≈ 10 μm for
`bend_radius_um = 5 μm`). A bend separation smaller than this causes the two bend
arcs to physically overlap — the layout is unfabricable.

**Current gap:** `turn_guard_um = 16 μm` protects the distance from the segment src/dst
to the first/last bend, but does NOT enforce minimum distance between two consecutive
bends in the MIDDLE of a route. The `same_net_hairpin` penalty (200 μm) discourages
but does NOT hard-block close bends on the same net.

**Grid pitch hazard:** `grid_pitch_um = 8 μm` < `2 × bend_radius_um = 10 μm`. A
one-grid-step jog has only 8 μm between its two bends — physically invalid.

**Required fix — track last bend in A\* state or post-filter:**

Option A (A\* state extension): Extend the A\* state to `(x, y, direction,
last_bend_x, last_bend_y)`. When a new direction change is proposed, check:
`_manhattan(current_point, last_bend) >= 2 * bend_radius_um`. If not, block the
transition. This is a hard block, not a penalty.

Option B (post-route hard validation): After constructing the waypoint list, scan
consecutive bend-to-bend distances. If any pair is closer than `2 × bend_radius_um`,
raise `RoutingError` so the route is marked failed and rip-up is triggered.

Option B is simpler to implement and preferred for v1. Add validation in
`_route_one_path` immediately after assembling `points`.

**Relationship to §14.2:** Port-aware staggering (§14.2) largely eliminates the
conditions that force A\* into small jogs. §14.4 is the safety net for residual cases.

---

### §14.5 Red Flags for Physical Routing

```text
Occupied bends:
  occupied_bends rebuilt only once per topology run, not per routing pass
  occupied_bends includes local (cell-internal) segment waypoints
  allowed_touch_points not set to (src, dst) when forbidden_points is non-empty

Minimum bend distance:
  Same-net hairpin penalty used as a substitute for a hard bend-distance block
  Minimum distance enforced only near src/dst, not between consecutive mid-route bends
  grid_pitch_um increased without re-verifying 2*bend_radius_um < grid_pitch_um

Port-aware staggering:
  All routes in a corridor get the same x-slot (staggering not applied)
  Staggering applied globally instead of per corridor
  Routes re-routed in rip-up pass without rebuilding occupied_bends from scratch

General:
  touching_corner DRC violations accepted as "acceptable crossings"
  Small jog (< 2*bend_radius_um bend-to-bend) present in final routed layout
  Rectangular loop (knot) pattern present between two routes in the same corridor
```
