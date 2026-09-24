# N=8 Waksman Recursive Physical Embedding DP

**Status: Stopped under §12.**

**Measured headline: 5.181320181939005 → 4.699529235180638 dB**, a **0.481790946758367 dB** reduction. The full planned routing comparison remains incomplete when the §12 stop applies.

Primary metric: `GLOBAL_WIL_OPT_ASSIGN = max_π min_legal_state WIL`, compared with `5.181320181939005 dB`. All quantiles in summary.csv are over the 40,320 per-permutation optimal-assignment WIL values. BFS quantiles are recorded separately in exact/*.json.

## 1. Scope and baseline (§0–2)

Only experiment scripts and results are written. The pre-existing modified production and test files are preserved byte-for-byte using source_hashes_before/after.json. Logical Waksman stage pairs, 17 SEs, the single-MRR S-table, BAR/CROSS port transitions, 131,072 states, 40,320 permutations, the octave canvas and all campaign rules remain fixed. The prior worst sets are read directly. Each geometry gets one permutation-independent production A* invocation.

The experiment supplies its MRRCell placement dictionary to `_route_case` in canonical production iteration order. The underlying production router is unchanged; no geometry helper is extracted or modified in the protected directories.

The baseline is submitted for a fresh route in priority position 1; no v3-mini geometry comparison is used. Each completed route stores placement, fabric edges, geometry pickle/hash, crossings, lengths, bends, rule/audit records and runtime. An immediate summary row records route outcomes; exact fields are filled during Stage 3. Failed or interrupted routes have blank loss fields.

## 2. Recursive representation and legal transformations (§3–4)

`recursive_tree.json` records node identity, non-contiguous logical wire sets (including `{0,2,4,6}` and `{1,3,5,7}`), boundary SEs, child links, stage ranges, physical wire slots and choices. Every node enumerates normal and vertical reflection; equal-sized isomorphic children also permit exchanging their placement slots. A reflection moves the complete subtree and negates each named port’s dy. Child swaps translate entire child solutions into the opposite interleaved slot family. Cell dimensions, dx, named port roles, S-values and internal transition lengths/bends remain unchanged.

All composed embeddings were exhaustively checked on N=4, then N=6, before N=8 was enabled (transformation_validation.json). Named external links and active port-level paths were checked for every permutation in every small-N embedding. N=8 verifies all LUT mappings and all cached active state paths against production semantics; each candidate must satisfy the identical-graph and physical-transition contract. Thus all 40,320 permutations transfer to every candidate by identity, with no state-bit remapping or disappearing permutation.

Horizontal reflection/180° rotation is excluded because production escape/runway direction is hardcoded by port name. Unconjugated logical port-role or boundary relabeling changes semantics and is excluded. Unequal-size child exchange has no isomorphic slot bijection in this experiment. These exclusions do not claim that every mathematically possible embedding has been searched.

## 3. Boundary signatures and equivalence (§5–6)

The DP key is `node_id` plus the ordered boundary signature. It retains ordered input/output logical wires, every named cut-edge endpoint (including bypasses), exact boundary coordinates and access coordinates, top-to-bottom order, horizontal facing, logical upper/lower PSE role, physical upper/lower role, parent endpoint identity and whether the port faces its parent connection in x. No routed geometry is in the key. Two child solutions can be safely compared only when these interfaces match and the cost of every labeled internal edge is retained. Subnet size alone is insufficient.

Translation and y-reflection preserve internal dx/absolute-dy, facing penalties and bend bounds. Parents can therefore combine equivalent interfaces without changing the meaning of the retained edge components. This equivalence is for the decomposed proxy: it is not a theorem that A* will route proxy-equivalent interiors identically, because obstacle interactions are not separable.

## 4. Proxy costs and objectives (§7–8)

`proxy_edges.csv` records each edge’s stage span, dy, direction-aware rectilinear length lower bound, wrap excess length, source/target away flags and minimum bend lower bound. The length bound includes mandatory production port access/runway points; a source that faces away incurs nonzero excess. With horizontal endpoint tangents a non-straight path needs at least two bends. Obstacles, crossings and inter-net congestion are omitted; no unreliable crossing lower bound is invented. The proxy weight carrier is explicitly synthetic and is never presented as legal routed geometry.

| Objective | Definition |
|---|---|
| A | sum estimated edge dB |
| B | maximum BAR logical-wire chain estimated edge dB |
| C | total facing/wrap excess length um |
| D | maximum source-stage sum(stage_span * estimated edge dB) |
| E | A/42+B+0.002*C/42+D/8 |
| F | production path-space/witness proxy BFS WIL; exact optimal-assignment proxy also reported |

| Objective | Current embedding | Best DP proxy |
|---|---:|---:|
| A | 23.330000000000 | 23.330000000000 |
| B | 3.692000000000 | 3.480000000000 |
| C | 2176.000000000000 | 2176.000000000000 |
| D | 14.222000000000 | 13.432000000000 |
| E | 6.128845238095 | 5.982190476190 |
| F | 4.034329063508 | 4.033077933090 |

F calls the existing `evaluate_fixed_fabric_path_space` and production witness finder on estimated external edge weights, retaining the unchanged internal device loss. Logical witness queries are memoized across geometry-only candidates. F is a proxy for BFS global WIL, not the max-min headline. The exact max-min operation on proxy weights is also recorded as `proxy_global_opt_assignment_db`. Every F result is cross-checked against the reused LUT’s full BFS aggregation.

## 5. Bottom-up DP and Pareto behavior (§9–10)

Leaves enumerate both legal port orientations. Each parent combines retained upper/lower entries with every legal local choice, computes its interconnect profile and groups identical boundary signatures. Dominance requires all labeled internal-edge length, bend and wrap components to be no larger and at least one strictly smaller. Equal vectors remain. No scalar minimax pruning occurs. These components also preserve every logical-wire chain and switched path contribution; all objectives and the exact proxy max-min are monotone in the applicable components.

Raw recursive decision space: **1024**; unique physical geometries: **1024**. Root survivors: **576**. Boundary states: **104**; maximum/mean per-state Pareto size: **9/5.9231**. See dp_stats.csv and pareto_set_size_vs_subnet_size. Full-space scoring is retained to audit the DP search and avoid hiding geometrically distinct proxy ties.

## 6. Baselines, top-K and three-stage budget (§11–12, §15–16)

Stage 1 completes every objective and all finite-space proxy scores, 100 seeded random legal decision draws, greedy local-edge minimization, Pareto statistics and top-20 root lists before any A* call. Greedy commits each node’s smallest current parent-interconnect proxy after choosing its children; leaf local costs tie and use normal orientation. Its tie rule is deterministic.

The frozen routing_queue.json contains **24** unique candidates, ordered: baseline; best of A–F; F top five; greedy; random proxy best three and middle three; other objective runners-up. Repeated geometries satisfy multiple selection roles through one route. The hard cap is 24. Router flags are B_db_placeholder, pops_ladder=(30000,), min_crossing_clearance_um=10, straighten_jogs=True, physical_turn_guard=False, v4_search=False for all candidates. The existing campaign admission gate rejects failed edges/core DRC/bend-radius violations, while preserving its measurement-only crossing/spacing audits. No rules are tuned per candidate.

## 7. Exact physical outcomes and worst sets (§13–14, §17–19)

Completed route outcome rows: **4**; accepted fabrics exactly evaluated: **2**.

| Unaccepted candidate | Runtime (min) | Diagnostic |
|---|---:|---|
| e_2dc7ec51ea33 | 28.37 | cannot route I7->O7 with bounded multi-hop fallback at waksman_8x8_s2_w3_7.drop->O7: cannot route waksman_8x8_s2_w3_7.drop to O7 on physical grid |
| e_86a531d2390c | 40.00 | Single candidate exceeded the section 12 40-minute threshold |

A legal logical embedding can fail the fixed production routing search. Such candidates retain full permutation coverage but have no accepted physical WIL; their partial length/crossing counts are not used in WIL correlations.

Routing stopped as required by §12: **Single candidate exceeded the section 12 40-minute threshold**, candidate `e_86a531d2390c`. Unrouted candidates have no physical claim.

| Candidate / selection | BFS global dB | GLOBAL_WIL_OPT_ASSIGN dB | Unique worst improved |
|---|---:|---:|---:|
| e_695b5f4fafd4 (baseline/F) | 5.181320181939 | 5.181320181939 | 0/256 |
| e_1a30e83a0382 (DP/B) | 4.699529235181 | 4.699529235181 | 256/256 |

Exact/*.json contains both quantile sets, worst permutation/input/state/path and loss decomposition, plus improved counts, mean/max improvements, unchanged-at-baseline counts and new maxima for all 1,008 baseline-worst and 256 unique-worst permutations. Per-permutation CSV and NPZ data support all six requested figure families. Negative improvement means degradation.

Observed Pearson correlations on accepted routed candidates: `{"proxy_score": null, "physical_crossings": null, "total_length_um": null}`. Correlations are left null with fewer than three accepted fabrics; two points cannot establish proxy predictive quality. This priority-selected sample is not an unbiased population estimate; failed routes have no exact WIL.

The current evaluator reproduces both baseline global values exactly. Its baseline BFS median is **4.826237198448586 dB**, also matching the prior per-permutation CSV; this differs from the task table’s 4.8644 dB summary. The reported optimum median and headline are unaffected by that discrepancy.

Worst-set improvement uses a like-for-like optimum-assignment comparison in summary.csv and the histogram, to isolate embedding changes from state-assignment freedom. For the winner, **968/1008 improve and 40/1008 worsen** relative to the prior exact optima (mean gain 0.429983082623 dB; maximum gain 0.511415926536 dB). Compared instead with the prior BFS values, **1008/1008 improve**, with mean gain 0.598462967264 dB. Both reference comparisons are stored in each exact JSON. 256/256 unique cases improve under either reference.

The winning recursive decisions are `{"root.L": "swap_children", "root.U": "swap_children"}`.
It exchanges the two upper-subnet middle-column cell y positions (320 ↔ 192 µm) and the two lower-subnet positions (256 ↔ 128 µm), keeping every other cell and all port orientations unchanged.
The exact max-min comparison isolates embedding changes from assignment choice.

MRR transition losses unchanged for each logical path. B configuration charges propagation only in the routed external geometry: crossing and per-bend coefficients are zero. The new global bottleneck is I5 to O1; propagation contributes 4.496699081698724 dB. The per-edge before/after contributions for the former and new critical paths are in bottleneck_analysis.json. Lower crossing count is an additional geometric observation, but carries zero direct loss benefit in B_db_placeholder.

The new worst permutation `[0, 2, 3, 7, 4, 1, 5, 6]` has exactly **2** legal state realizations. Direct production path evaluation of every realization confirms its minimum WIL is **4.699529235180638 dB**; see max_min_witness.json. Together with the full-state upper bound, this is a concrete max-min certificate.

Final verification: **4320** additional permutation evaluations using production exhaustive prefix chunks agree with the cached BFS results. The full max-min oracle evaluates all 131,072 states and 40,320 permutations for each accepted fabric. Protected source hashes, Stage 1 artifact hashes, routing priorities, one-call route counts and identical rules all pass final_audit.json.

Total attempted routing time: **88.37 minutes**; the main finite-space proxy pass took **17.04 seconds**. The routing stage stopped after 4 attempts; 20 queued candidates remain unattempted. No further route is launched by a plain rerun after a recorded §12 stop.

## 8. Interpretation and ten task questions (§20–23)

The supplied task refers to ten questions from an original §22 but does not reproduce their text. The following ten answers cover the supplied requirements without inventing a missing specification.

1. **Does the embedding preserve the logical fabric?** Yes for every enabled candidate: named graph, states and all device transitions are identical; exhaustive small-N checks precede N=8.
2. **Which choices are legal here?** Vertical reflection and equal-size child placement swaps, including their compositions. Unsupported horizontal and logical-role transformations are excluded.
3. **What information makes child solutions equivalent?** The complete cut interface and per-labeled-edge cost vector described in section 3; never size or a scalar alone.
4. **What is optimized before routing?** A–F with separate physical components, with F using production path-space and witnesses; full proxy max-min is also tabulated.
5. **Does Pareto pruning scale?** At N=8 it retains 576 root entries; maximum boundary-state Pareto size is 9. This finite experiment does not establish scalability to larger N.
6. **Are comparisons fair?** All candidates use the same S-table, canvas, production router and rule flags, once each; random and greedy selection is completed before routing.
7. **Does the headline improve?** Best observed `e_1a30e83a0382` gives **4.699529235180638 dB**, reference minus candidate **0.481790946758367 dB**. It is strictly below the specified reference.
8. **Do zero-freedom bottlenecks move?** For that candidate, 256/256 improve; their new maximum is 4.669904255403107 dB. The new worst path loss decomposition is `{"bend_count": 25, "bend_loss_db": 0.0, "crossing_count": 16, "crossing_loss_db": 0.0, "insertion_loss_db": 4.699529235180638, "mrr_loss_db": 0.20283015348191358, "path_length_um": 2248.349540849362, "propagation_loss_db": 4.496699081698724}`.
9. **Does DP outperform greedy/random?** Insufficient accepted routes across these selection groups to decide.
10. **Which failure/success class is supported?** A: measured headline reduction within this fixed deterministic setup (no statistical significance or novelty claim) The §12 stop leaves the full planned comparison incomplete.

All conclusions apply to the enumerated finite geometry family and selected physical sample. Proxy optimality is not a certificate of globally optimal routed embedding. No novelty claim is made.
