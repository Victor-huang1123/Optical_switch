# Optical Switch Router — Schedule & Execution Plan

This is the authoritative roadmap for completing `mrr_switch_optimizer` into a
**stable, maintainable** optical switch/router optimizer. It is not a single-bug
patch list.

Rule for this document: a task is **Done** only if (a) the code exists, (b)
relevant tests exist, and (c) the latest known test result is green. If code
exists but is not fully tested/green, it is **Partially Done / pending
verification**.

Reading list before editing the router: this file, `Lidar.md`, the files under
`routing/`, and `tests/test_physical_router.py`.

---

## 1. Current Verified State

The latest full-suite result is still the previous **RED** baseline:
`5 failed, 31 passed` (~996 s / 16.6 min). Do not treat the targeted checks below
as a full-suite replacement.

Previous full-suite per-topology physical routing on
`PERMUTATION = (2,0,5,1,3,4)`, default rules, same-net A* legality enabled
(measured before the latest ledger/access experiments):

| topology      | failed | crossings | limit | same-net self-cross | DRC                       |
|---------------|--------|-----------|-------|---------------------|---------------------------|
| Padded Beneš  | 0      | 37        | 23    | 0                   | clean                     |
| Waksman       | 0      | 25        | 19    | 0                   | 1 `same_net_hairpin` (I3) |
| Spanke-Beneš  | 0      | 27        | 5     | 0                   | clean                     |

Interpretation:

- Same-net **self-crossings are eliminated** (0 everywhere); Spanke-Beneš
  `I3`/`I4` self-touches are **fixed**. The same-net A* legality direction works.
- But **crossings inflated badly** (Spanke-Beneš 5 -> 27 is the clearest signal),
  and a Waksman `I3` hairpin survives — almost certainly a **local/stub** hairpin
  that the external-only `current_segments` check cannot see.
- Note `failed_nets = 0` everywhere: routes still complete, they are just worse.
  So the dominance risk (Task 7) currently shows up as **crossing inflation**,
  not vanished nets — verify before assuming a state-model rewrite is required.

Latest targeted state after Tasks 2/5/6 experiments (not a full suite):

| check | result |
|-------|--------|
| fast syntax + synthetic port/crossing/window tests | green |
| Padded Beneš default (`strict_port_access=False`, staged) | **2 failed nets**; earlier "DRC clean on routed subset" reports were under-counted before the `best_violations` bookkeeping fix; latest bounded PB best candidate reports `same_net_hairpin` on I3 |
| Padded Beneš input-order only (`max_ripup_passes=0`) | **2 failed nets**, DRC clean on routed subset, 5 crossings, every crossed pair count = 1 |
| Padded Beneš with `explicit_crossings=False` diagnostic | 0 failed nets, but 35 crossings, repeated pairs, and same-net hairpin |
| Padded Beneš strict port access (`strict_port_access=True`) | fails earlier on `port_access_touch_foreign`; strict is implemented but not routable as default |
| Placement probes (`stage_pitch=140/160`, `wire_pitch=48`, stage y-stagger, `grid_pitch=4`) | did not recover zero failed nets under pair-budget rules |
| Port-escape probe (`port_escape_um=6`) | did not recover zero failed nets |
| Padded Beneš capped diagnostic after A* diagnostics/cache + future-access spacing reservation (`max_ripup_passes=0`, `fallback_beam_width=0`, `max_astar_pops=5000`) | routes 2 nets, then 4 nets hit the A* pop cap on inter-stage hops; failures include `reserved_spacing` blocks but still hit `pops=5001` |
| Padded Beneš capped diagnostic after non-cascading/progressive hop windows (`max_ripup_passes=0`, `fallback_beam_width=0`, `max_astar_pops=5000`) | routes 3 nets; I2 recovers, I3/I5 still hit pop cap, I4 terminal is blocked by crossing budget against I1 |
| Padded Beneš capped diagnostic after default `route_window_max_detour_tracks=4` (`max_ripup_passes=0`, `max_astar_pops=12000`) | routes 3 nets; failures are now explicit: I3/I4 crossing-budget against I1, I5 port-access overlap against I2 plus crossing-budget detail; related inputs = `[1,2,3,4,5]` |
| Padded Beneš one blocker-aware rip-up pass (`max_ripup_passes=1`, `max_astar_pops=12000`) | routes 4 nets, 2 failed; remaining failures shift to I0 blocked by I5 port access / I1 crossing budget and I2 blocked by I3 crossing budget; related inputs = `[0,1,2,3,5]` |
| Padded Beneš capped rip-up diagnostic (`max_ripup_passes=3`, `max_astar_pops=3000`) | exceeded the intended interactive wait and was stopped; current rip-up remains too slow for inner-loop validation |
| Padded Beneš two-pass capped rip-up diagnostic (`max_ripup_passes=2`, `max_astar_pops=12000`) | exceeded the intended interactive wait and was stopped, even after pass-2 order was changed to prioritize failed/blocker owners |
| Padded Beneš bounded reroute diagnostic (`max_ripup_passes=2`, `max_astar_pops=12000`, `ripup_max_astar_pops=2000`) | returns with routes 4, failed 2; same blocker shape as the one-pass result, so bounded reroute controls the pass but does not yet improve routability |
| Padded Beneš priority-aware reroute diagnostic (`max_ripup_passes=2`, `max_astar_pops=12000`, `ripup_max_astar_pops=2000`) | exceeded the intended interactive wait and was stopped; fast unit tests verify the new priority selection, but PB still needs single-hop/local alternatives before more long diagnostics |
| Padded Beneš crossing-budget feedback diagnostic (`max_ripup_passes=2`, `max_astar_pops=12000`, `ripup_max_astar_pops=2000`) | returns with routes 4, failed 2; penalizing owners named by `blocked_by=I*/crossing_budget` is implemented and unit-tested, but it does not change the PB endpoint |
| Padded Beneš larger reroute-budget diagnostic (`ripup_route_budget=5`, same caps) | returns with routes 4, failed 2 and the same I0/I5/I1 + I2/I3 blocker shape; this rules out "budget 4 simply skipped I1" as the primary cause |
| Padded Beneš wider route-window diagnostic (`route_window_max_detour_tracks=8`, same caps) | exceeded the 150 s probe timeout; do not fix PB by restoring a globally wider window |
| Padded Beneš local pre-escape approach diagnostic (`port_approach_fallback=True`, same caps) | returns with routes 4, failed 2; I0 changes to `direct port approach has no legal crossing`, I2 remains crossing-budget against I3. The helper is unit-tested but staged off by default because it does not recover PB and adds runtime |
| Padded Beneš foreign port-escape guard diagnostic (default guard on, same caps) | returns with routes 4, failed 2, crossings 5; pre-bookkeeping DRC was reported as 0, but latest `best_violations` fix means old partial-route DRC counts should not be treated as authoritative. I0/I5 port-access clearance is gone, remaining failures are pure crossing-budget: I0/I1 and I2/I3 |
| Padded Beneš hard future-crossing reservation diagnostic (`future_crossing_reservation=True`, same caps) | returns with routes 4, failed 2; failure moves earlier (`I0` first port entry and `I2` earlier hop), so hard reservation is implemented/tested but staged off by default |
| Padded Beneš priority-ordered reroute diagnostic (default rules, same caps) | returns with routes 4, failed 2 and the same I0/I1 + I2/I3 crossing-budget failures; priority now orders reroute attempts, not just selection, but this alone does not recover PB |
| Padded Beneš crossing-budget provenance diagnostic | returns with routes 4, failed 2; I0/I1 budget was first consumed by I0's earlier segment `(20,238)->(55,238)` crossing I1 at `(40,238)`, and I2/I3 by I2's earlier segment `(55,158)->(55,140)` crossing I3 at `(55,144)` |
| Padded Beneš source-forbidden retry diagnostic | source points are recorded and used in later rip-up passes, but under the capped diagnostics later passes become A* pop-limit/no-route cases and the best result remains 4/6 |
| Padded Beneš structured-source/local-detour diagnostic | structured `source=I*->I*@...:seg x seg` provenance is recorded across rip-up passes; local source-detour candidates and deterministic rip-up-pass hop candidates are implemented and focused tests are green, but capped PB still returns routes 4, failed 2 |
| Padded Beneš interleaved failed/blocker reroute diagnostic | pass 1 now orders the first PB reroute as `[0,1,2,3]` instead of `[0,2,1,3]`; with `ripup_max_astar_pops=2000`, pass 1 routes only `[5,4,1]` and fails I0/I2/I3; pass 2 includes I5 but regresses to routes `[4,5]`, so local rip-up currently cascades rather than converges |
| Padded Beneš route-order beam diagnostic | gated `route_order_beam_width=3` / `route_order_beam_max_astar_pops=1200` still returns routes 4, failed 2 after several minutes; routed set `[1,3,4,5]`, failed I0/I2. Naive global order search is too slow and does not recover PB as currently implemented |
| Padded Beneš crossing-history A* dominance diagnostic | A* state key now includes a crossing-budget signature so histories that consumed different pair budgets are not collapsed into one `RouterState`; focused A* tests are green, but PB capped still returns routes 4, failed 2 with the same I0/I1 and I2/I3 source failures |
| Padded Beneš stale-source filtering diagnostic | historical crossing source points are now applied only when the crossed owner remains fixed; if the crossed owner is part of the same reroute island, the stale source/forbidden point is filtered. Focused tests are green, but PB capped still returns routes 4, failed 2 with I0/I1 and I2/I3 source failures |
| Padded Beneš conflict-aware order probe | order `[0,2,1,3,5,4]` routes `[0,2,1,4]` and fails I3/I5 with 2 DRC violations; order `[0,2,3,1,5,4]` routes `[0,2,3,4]` and fails I1/I5 with DRC clean. Order can move the conflict, but not solve it |
| Padded Beneš bounded rip-up order candidates | `ripup_order_candidate_limit=3` was implemented and unit-tested, but the PB capped diagnostic exceeded 120 s before returning. Default is staged at `1` so normal routing keeps the previous runtime/behavior |
| Padded Beneš pair-level allocation probe | I1 fixed then I0 fails at I0's first port escape; I0 fixed then I1 succeeds. `[0,1]` routes clean with 1 crossing, `[1,0]` fails. `[2,3]` and `[3,2]` both route in isolation. Combined `[0,1,2,3]` fails I3 on I1/I3 crossing budget, so pair-local feasibility does not compose under sequential routing |
| Padded Beneš I4/I5 interaction probe | `[0,1,4,5]` routes `[0,1,4]` and fails I5 with an I4 same-net hairpin; `[0,1,5,4]` routes `[0,1,5]` and fails I4 on I4/I5 crossing budget. I4/I5 is another local allocation pair, not a separate topology bug |
| Padded Beneš DRC-feedback reroute diagnostic | DRC violation owners are now fed into reroute feedback together with failed-net owners; helper tests are green. PB bounded diagnostic remains routes 4, failed 2, crossings 5; old DRC-0 wording is superseded by the latest `best_violations` bookkeeping fix |
| Padded Beneš failure-occurrence priority diagnostic | `_failed_reroute_priority()` now preserves actual failure occurrence order instead of re-sorting direct failures by the original complexity order. This prevents a later failed blocker such as I1 from being forced before the earlier failed owner I0. Focused tests are green, but PB 3-pass capped diagnostic still returns routes 4, failed 2 |
| Padded Beneš I0/I5 bidirectional conflict probe | `[0,4,1]` routes clean, but adding I5 fails on I0 port-access / I0-I5 budget; `[5,0]` fails I0 on I5 crossing budget. This is a bidirectional local allocation conflict, not a simple precedence/order bug |
| Padded Beneš small island probe after source-provenance cleanup | `[0,1]` routes clean; `[1,0]` fails on I0/I1 budget; `[0,1,2,3]` routes `[0,1,2]` then fails I3 on I1/I3 budget; `[0,2,1,3]` routes `[0,2]` then hits bounded A* pop limits for I1/I3; `[0,4,1,5]` routes `[0,1,5]` then hits bounded A* pop limit for I4. This confirms local pair precedence is real but does not compose into a full PB solution by static order alone |
| Padded Beneš precedence-cycle probe | Order `[0,2,3,5,1,4]` routes only `[0,2]` under a 2200-pop cap; I1 later fails with reverse provenance `source=I1->I0`, while I3/I5/I4 hit pop-limit/search blockers. This confirms that source-derived precedence constraints can cycle; the fix needs hop-level shaping/allocation, not another static whole-net order |
| Coherent reroute-island selection | Failure messages are now converted into owner graph components using direct failures, `blocked_by`/`region` owners, and structured `source=Ix->Iy` provenance. `_bounded_reroute_inputs()` keeps any component containing a direct failed net together, even when that slightly exceeds the nominal `ripup_route_budget`. Focused helper tests are green; PB endpoint is not yet revalidated |
| Coherent reroute PB timeout probe | A guarded PB run after coherent island selection (`max_ripup_passes=3`, first-pass pop cap 6000, rip-up pop cap 1200) hit the 75 s timeout before returning. Treat coherent island selection as necessary plumbing, not as a completed routing fix |
| Source hint split for moving blockers | Historical source coordinates remain forbidden only when the crossed owner is fixed, but owner-pair allocation hints now survive when the crossed owner is rerouted in the same island. Focused helper tests are green; this is the first hop-level allocation hint, not a full allocator |
| Beam fallback source-reservation bug fix | `_source_reserved_crossing_owners()` now centralizes source-derived hop reservation, and the bounded beam fallback no longer passes the unsupported `source_crossings=` keyword into grid A*. Focused helper tests and `py_compile` are green. Route-level beam fallback coverage is still missing |
| Mirrored pair-source hints | `_record_failed_crossing_sources()` now records each structured source for both owners: the original owner gets the original segment, and the crossed owner gets a mirrored source/crossed segment. This lets both sides of a conflicted pair reserve the pair on their matching hop. Focused helper tests are green; a guarded PB probe (`max_ripup_passes=2`, first cap 4500, rip-up cap 900) still timed out at 60 s |
| Pair-local source-detour runtime lock | A synthetic `_route_external_hop()` runtime test now proves a source-reserved hop can take a bounded detour around the recorded blocker and avoid re-crossing the same pair. This validates the local primitive; PB still needs multi-hop/blocker-local allocation |
| Source-hint bounded-only fail-fast | `RoutingRules.source_hint_bounded_only=True` now prevents a source-reserved reroute hop from falling through to broad A* after source/deterministic bounded candidates are exhausted. Focused tests are green. A guarded PB probe with the same 4500/900 caps still timed out at 45 s, so remaining runtime is not only source-reserved-hop fallback |
| Capped rip-up bounded-only diagnostic guard | `RoutingRules.capped_ripup_bounded_only=True` now prevents capped rip-up passes from falling through to broad A* after bounded candidates fail. Focused tests are green. The PB 4500/900 capped probe now returns in ~33 s with routes `[1,3,4,5]`, failed `I0/I2`, crossings 5; latest DRC bookkeeping reports `same_net_hairpin` on I3. The remaining failures are explicit I0/I1 and I2/I3 crossing-budget source conflicts |
| Bounded fail-fast provenance preservation | Source/capped fail-fast messages now include structured `source=Ix->Iy@...` provenance. Pass-trace verification shows reroute components remain `{0,1}` and `{2,3}` across passes instead of degenerating to single owners. The remaining pass-1/2 failures are now local candidate quality: source detours violate bend spacing or same-net self-conflict |
| Owner-aware port-guard diagnostics | Physical-layer reserved port guards now keep `OccupiedRouteSegment.owner_input` through source/deterministic/approach validation and convert to bare `Segment` only at A* grid entry. Focused tests are green. PB pass trace now reports `blocked_by=I1/port_guard` instead of `blocked_by=unknown/port_guard`; the endpoint remains routes `[5,4]` in pass 1/2 with failed `{0,1,2,3}` reroute islands |
| Same-net conflict diagnostics | `_same_net_self_conflict_reason()` now reports conflict kind and prior segment for source-detour/direct-approach failures. Focused tests are green. PB pass trace shows I1 source detours overlap/touch its own port-side geometry near `(69,230)->(55,230)`, while I2/I3 source detours cross same-net vertical geometry near `x=220`; this confirms the next fix is candidate shaping, not weaker same-net legality |
| Foreign guard-off probe | A read-only monkeypatch probe disabling foreign port guards made PB worse: pass 1/2 routed only `[5]`, introduced an I4 same-net hairpin, and expanded the reroute island to `{0,1,2,3,4}`. Do not recover PB by globally disabling port guards |
| Wider source-detour probe | A read-only monkeypatch increasing `_source_detour_alt_values` from 8 to 24 alternatives did not improve PB; pass 1/2 remained routes `[5,4]`, failed `{0,1,2,3}`, and I1 source failures increased from 16 to 48. Do not fix PB by blindly expanding source-detour candidate count |
| Candidate-origin diagnostics | `_route_external_hop_via_waypoints()` now distinguishes `source detour`, `deterministic hop candidate`, and `same-net avoidance candidate` failures. Focused tests are green. PB traces confirm the remaining pass-1/2 failures are deterministic bounded candidates, not the source-detour primitive itself |
| Same-net avoidance candidates | A bounded same-net avoidance candidate path now rewrites deterministic candidates that would orthogonally cross committed same-net geometry, and filters generated candidates through full same-net legality before validation. Focused tests are green. PB pass 1/2 improves locally from routes `[5,4]` to `[5,4,1]`, but best-route summary remains `[1,3,4,5]`; I2/I3 still fail on deterministic same-net conflicts near `x=220` |
| Committed-owner rip-up port guards | On rip-up passes, foreign port guards now hard-protect only already committed/fixed owners; future owners in the same reroute batch are not treated as immovable guard walls. Focused tests are green. PB I0 moves from `blocked_by=I1/port_guard` to a real crossing allocation failure against I5, confirming the guard over-reservation was reduced but not solved |
| Deterministic crossing-budget provenance | Deterministic candidate crossing-budget rejects now include `blocked_by=I*/crossing_budget`, pair, crossing point, and segment/crossed geometry. Focused tests are green. This exposes I0/I5 in PB pass 1, but pass 2 can over-reroute I5 and drop to routes `[4,1]`; owner selection/scoring must account for disruptive blocker inclusion |
| Candidate-source reroute policy | Bounded-candidate crossing-budget provenance is now emitted as `candidate_source=...`: it is recorded as a source hint for the failed owner, but `_failed_related_inputs`, `_failed_reroute_priority`, and `_failure_owner_components` do not treat the crossed owner as a hard reroute island member. Focused tests are green. PB pass 2 now reroutes `[0,2,3]` instead of `[0,2,3,5]`, preserving routes `[5,4,1]` rather than regressing to `[4,1]`; best-route summary is still `[1,3,4,5]`, failed `I0/I2`, crossings 5 |
| Result DRC bookkeeping fix | Failed-route best-candidate selection now preserves `best_violations` instead of accidentally reporting an empty DRC list. Focused tests and `py_compile` are green. The same bounded PB summary is now correctly reported as routes `[1,3,4,5]`, failed `I0/I2`, crossings 5, **DRC `same_net_hairpin` on I3** |
| Source-detour side-balanced tracks | `_source_detour_alt_values()` now samples both sides of the crossed segment instead of spending all 8 bounded alternatives on the closest side. Focused tests are green. PB endpoint is unchanged because the I0/I5 case has no legal lower-side escape in the current grid and upper-side candidates still cross fixed I5 geometry |
| External forbidden-point cleanup | Fixed-route `occupied_bends` now stores external hop endpoints and true bends only, not every collinear split point. Focused tests are green. This prevents non-physical split points from becoming forbidden, but PB endpoint is unchanged because `(160,184)` is also a real port-access/escape point in the I1 guard area |
| Guard-aware same-net candidates | `_same_net_avoidance_hop_candidates()` can now shape/filter bounded candidates around reserved guard segments before validation. Focused tests are green. PB endpoint is unchanged; I2/I3 still need a stronger local pair allocation, not just one more guard-aware envelope |
| Failed-candidate DRC scoring | Failed-route best-candidate scoring and route-order beam scoring now use `_pass_candidate_score(...)`, so DRC violations are preserved and penalized instead of being side-channel metadata. Focused tests are green. PB endpoint changes only after hairpin validation below |
| Post-A* same-net hairpin validation | Same-net hairpin is now a routing-level reject: deterministic/source candidates check it before commit, and generic A* paths are validated after reconstruction and retried with a history penalty. A full partial-tail A* key was probed and correctly prevented hidden hairpin histories, but it exploded the 4500-pop PB diagnostic to only one routed net, so it is not a default strategy. The cheaper post-A* validation makes PB DRC-clean and recovers I2: latest bounded PB routes `[1,2,4,5]`, failed `I0/I3`, crossings 6, DRC 0 |
| Reserved-owner avoidance candidates | Source-reserved rip-up hops can now generate bounded detours around the actual external segments of reserved owners, with wider validation windows for fixed waypoint repair candidates and prefilters for obstacle/forbidden-point conflicts. Focused tests are green. This changes the remaining PB failures to concrete local blockers: `I0` is blocked by fixed `I4` spacing while avoiding `I5`, and `I3` exhausts/filters reserved-owner candidates then falls back to an `I1` reserved crossing |
| Reserved-owner envelope probe | Reserved-owner avoidance now also tries bounded envelope candidates around the reserved owner's external-route bbox, with prefilters for reserved-owner crossings, obstacles, forbidden points, and occupied non-crossing conflicts. Focused tests are green. PB endpoint is unchanged: `[1,2,4,5]`, failed `I0/I3`, crossings 6, DRC 0. A 3-pass bounded probe is also unchanged, so simply adding another rip-up pass does not move the fixed `I4` / `I1` blockers |
| Soft source-derived crossing reservation | `RoutingRules.hard_source_crossing_reservation=False` now makes source-derived pair hints a deferred/high-cost crossing preference instead of a hard reservation. Hard mode still exists for diagnostics. Focused tests are green. PB bounded probe no longer dies with `source-reserved hop exhausted...`; it reaches real negotiated-congestion blockers: `I0/I1` crossing budget and `I3` A* pop limit against `I1`. Endpoint remains `[1,2,4,5]`, failed `I0/I3`, crossings 6, DRC 0 |
| History-aware bounded candidate ordering | Deterministic/source/reserved-owner/same-net bounded candidates are now sorted by `HistoryCost` before length, so per-net retry can avoid previously bumped congested segments instead of always replaying the shortest local shape. Focused tests are green. PB endpoint is unchanged; this is necessary negotiated-congestion plumbing, not sufficient cross-net congestion memory |
| Task 6.1 soft repeated-crossing penalty | Different-net repeated pair crossings are now soft-costed instead of hard-rejected by default. Same-net self conflicts remain hard. `hop_crossing_class()` scales the repeated-crossing penalty by port-side pattern: inward/same-side high, outward/wrap low. Focused tests and `py_compile` are green. PB bounded probe (`max_ripup_passes=4`, `max_astar_pops=4500`, `ripup_max_astar_pops=900`, no beams) now routes `[0,1,2,4,5]`, fails only `I3` on A* pop limit, reports 14 crossings, max pair crossing count 3, DRC 0 |
| Task 7 step 0 reserved-crossing softening + diagnostics | `reserved_crossing_owners` is no longer a default different-net hard wall. `RoutingRules.hard_reserved_crossing_reservation=True` keeps the old behavior only for diagnostics. A* failure counters now separate `crossing_budget` (true hard budget), `hard_reserved_crossing`, `soft_repeated_crossing`, and `soft_reserved_crossing`. Focused tests and `py_compile` are green. PB bounded probe at 4500/900 still fails I3, but now clearly reports `crossing_budget:0`, `hard_reserved_crossing:0`, `soft_reserved_crossing:0`, `soft_repeated_crossing:25`; the remaining blocker is search/pop-limit, not a hidden hard crossing wall |
| Task 7 high-pop probe | With reserved crossings soft and default `astar_heuristic_weight=1.0`, PB high-pop probe (`max_astar_pops=12000`, `ripup_max_astar_pops=2400`) routes I3 and fails I2 instead: routes `[0,1,3,4,5]`, failed I2 on `deterministic hop candidate creates same-net self conflict; same_net_touch at=(55,166)`, crossings 18, max pair count 3, DRC 0. A weighted heuristic probe at `astar_heuristic_weight=1.15` made I3 worse and is not the default |

Interpretation of the latest targeted state:

- Same-net self-crossing remains a hard legality rule. Different-net repeated
  pair crossing is now a soft loss/reporting concern, not a hard DRC or routing
  wall. This recovered PB `I0` in the bounded probe, but exposes the next
  blocker: `I3` still hits an A* pop limit at low caps.
- Raising the cap confirms `I3` is routable under the current legality model.
  The failure then moves to I2's bounded deterministic same-net touch, so the
  next useful implementation is candidate shaping / local same-net geometry, not
  another crossing hard-wall relaxation.
- Current-net future port-access reservation fixed the observed same-net local
  escape blocker (`blocked_by=I3/external`), but foreign port-access corridor
  collisions remain.
- The router is **not complete**: PB still loses nets because the stricter
  crossing/access legality can block local port escapes or inter-stage hops.
- The current bottleneck is search strategy, not topology generation: route
  order, future/current port-access visibility, local-port escape protection,
  and routing-window/performance need a coherent pass.
- Runtime regressed badly: a single PB targeted summary can take several minutes
  after endpoint-crossing / pair-budget enforcement.
- Repeated diagnostics now show the remaining failures are dominated by
  `port_access_overlap_foreign`, segment blockers, and crossing-budget pressure,
  not by topology generation or basic route-window bounds.
- Naive per-net multi-hop retry was attempted through `HistoryCost`; unrestricted
  retry caused unacceptable runtime, so it is now guarded to at most one retry
  and only on rip-up passes. This is not yet sufficient to make PB green.
- A bounded per-net beam fallback exists now, but the first PB input-order probe
  with the default-sized beam ran too long and had to be stopped. It is therefore
  staged off by default (`fallback_beam_width=0`) and should be treated as an
  experiment, not a project-completion feature.
- A* now reports `pops` and `max_heap`, supports `max_astar_pops`, and caches
  static per-segment geometry/crossing checks inside each search. The capped PB
  diagnostic still shows repeated 5000-pop inter-stage failures, so the next
  fix must reduce the search space or change route strategy, not merely add
  more fallback attempts.
- Future foreign port-access corridors now hard-block only overlap/spacing
  (cross/contact remain soft/staged). This is the narrower version of "reserve
  future port access" and avoids turning every planned port into a global wall,
  but it does not by itself recover PB.
- Hop routing windows no longer cascade across the whole chip. The previous
  obstacle expansion mutated the relevance window while scanning obstacles,
  which let one hop grow from a local corridor into an almost global window.
  Non-cascading relevance plus progressive detour windows recovered one PB net
  in the capped diagnostic, but PB is still not green.
- `route_window_max_detour_tracks=4` is now separate from
  `local_repair_max_shift_tracks=8`. This keeps normal A* windows conservative
  while preserving the old repair limit as an explicit knob.
- Crossing-budget rejects now carry `blocked_by=I*/crossing_budget` when the
  crossed owner is known, so rip-up can include the actual blocker owner instead
  of only the failed net.
- One blocker-aware rip-up pass improves PB from 3 routed nets to 4 routed nets
  under the 12000-pop diagnostic. Multi-pass rip-up is still too slow and needs
  bounded candidate selection rather than free-running full-route attempts.
- Rip-up now preserves previous routes outside the selected reroute set and has
  a `ripup_route_budget` plus `ripup_max_astar_pops` diagnostic cap. This makes
  bounded reroute behavior explicit, but the latest PB diagnostic still ends at
  4/6 routed.
- Reroute selection is now priority-aware: direct failed nets are selected first,
  then blocker owners are added round-robin by failed net. This prevents a budget
  from accidentally selecting two blockers for one failed net while missing the
  first blocker for another. It is unit-tested but not yet proven to improve PB.
- Crossing-budget failure feedback now feeds the single-net retry path: owners
  named by `blocked_by=I*/crossing_budget` are treated as high-cost crossing
  owners on the retry, and fallback hop specs keep base detour windows so
  alternatives start narrow. This is unit-tested, but the capped PB probe still
  ends at 4/6 routed.
- A local pre-escape port-approach helper exists and is tested: it approaches a
  target MRR port from outside the reserved access corridor, then connects by a
  checked direct segment. It is staged off by default
  (`port_approach_fallback=False`) because the PB diagnostic still ends at 4/6
  and the failure becomes a crossing-budget issue rather than an access-overlap
  recovery.
- Foreign port-escape guards are now default-on and unit-tested. They reserve
  only the short external approach clearance zone beyond another net's escape
  point, so earlier routes cannot cross one micron outside a future port escape.
  This removes the observed I0/I5 clearance blocker without turning all future
  port-access corridors into hard global walls. The remaining PB failures are
  now crossing-budget allocation problems.
- Future-waveguide crossing owner detection and hard reservation are implemented
  and unit-tested, but `future_crossing_reservation=False` by default. The PB
  probe with hard reservation still routes 4/6 and moves failures earlier, so it
  is not a completion path as currently formulated.
- Reroute priority now affects both selected inputs and the order in which those
  inputs are retried. The PB probe still returns 4/6 with I0/I1 and I2/I3
  crossing-budget failures, so blocker selection/order is not sufficient by
  itself.
- Crossing-budget provenance is now reported in failure messages and recorded
  into per-input forbidden points for later rip-up passes. The provenance shows
  the remaining pair budgets are consumed by earlier hops of the same failed net,
  not by an unclassified blocker. Avoiding those source points in later passes
  currently turns the problem into harder A* searches rather than recovering PB.
- Structured crossing provenance now preserves the source segment and crossed
  segment, not only the point. This enables bounded local detour candidates and
  owner-specific crossing reservation on the affected hop, but PB still does not
  converge because the required blocker owner often must also move.
- Deterministic fixed-polyline hop candidates now run before A* on rip-up passes
  and use the same spacing, port-access, same-net, and crossing-budget legality.
  This bounds some local alternatives but has not removed PB's remaining
  `A* pop limit exceeded` failures.
- Reroute priority now interleaves each failed net with its first blocker owner.
  This exposes real progress in manual high-cap probes (I2 can recover), but the
  bounded capped run still cascades to new blockers (I0->I5, I3->I1, etc.).
- A gated route-order beam search exists as an experiment. It tries multiple
  whole-net order prefixes after ordinary rip-up fails, but the first PB probe
  (`width=3`, 1200-pop cap) still returns 4/6 and takes too long for the inner
  loop. Keep it off by default until it has memoization or stronger pruning.
- A* dominance now keys states by `(RouterState, crossing_signature)` so two
  histories reaching the same grid point with different same-hop pair-budget
  consumption are not merged. This removes one correctness risk but does not
  improve the current PB endpoint.
- Historical crossing-source constraints are now owner-aware: source points from
  a previous pass constrain only fixed blocker owners. When the blocker owner is
  being rerouted in the same island, those stale points are filtered out and the
  live route grid owns legality. This is correct, but it does not recover PB.
- Bounded rip-up order candidates exist behind
  `RoutingRules.ripup_order_candidate_limit`; the default is `1` after the PB
  probe with limit `3` ran too long. Treat it as an opt-in diagnostic until it
  has memoization or earlier pruning.
- Pair-local probes show the core missing model: individual pairs can be routed
  if the owner that needs port access is committed first (`[0,1]` succeeds,
  `[1,0]` fails), but composing several such pairs sequentially moves the
  failure to another pair (`I1/I3`, `I4/I5`, `I0/I4`). The next implementation
  must allocate local pair crossings/hops before committing whole-net routes.
- Reroute priority now preserves failure occurrence order. This fixes a real
  sequencing bug where the original complexity order could put I1 before I0
  even after I0 failed first. The PB endpoint is unchanged, so priority ordering
  is not the missing allocator.
- I0/I5 is now confirmed bidirectional: routing I0 before I5 blocks I5 at
  I0's port access, while routing I5 before I0 blocks I0 on I0/I5 crossing
  budget. This rules out any pure owner-order precedence fix for that pair.
- Source-derived crossing reservation is now soft by default. The latest PB
  bounded probe changes the failure from `source-reserved hop exhausted...` to
  explicit `I0/I1` crossing-budget pressure and `I3` A* pop limit against `I1`.
  This confirms the earlier hard reservation was over-constraining, but also
  confirms that the remaining fix must be negotiated congestion across owners,
  not another single-hop detour template.

Independently verified correct (read-only / targeted checks, not the full suite):

- **Core logic** — over all 720 permutations x 3 topologies: RNB holds, every
  `RouteStep` is a valid add-drop transition, `state`<->transition consistent,
  input i delivered to `permutation[i]`, S-table equals exactly the 4 transitions.
- **Placement** — default placement feasible; LP reduces wiring ~20% and stays
  feasible; SA produces feasible placements.
- **Add-drop convention** — standard physical placement is in `core/models.py`
  and consistent with `core/sparams.py` (see Appendix A).

Module layout: see Appendix C.

---

## 2. Done

(Code exists and is individually verified. NOT all green *together* right now —
see §1 and §3.)

- **Subpackage reorg** (`core / placement / analysis / output / app / routing`).
  Root wrappers removed intentionally; tests import subpackage paths.
- **Standard add-drop convention locked + verified** (Appendix A).
- **side vs optical_role separated** (`port_side`, `port_optical_role`;
  `PortAccessPoint`/`PortAccessRegion` carry both).
- **Bidirectional corridor slots** — `_corridor_preferred_x` registers any
  horizontally-spanning hop in either direction (defensive; inter-stage hops are
  left-to-right in practice).
- **Owner-aware port access** (`PortAccessLegality`, `port_access_conflict`)
  implemented in A*. Strict non-owner touch rejection is staged off by default
  until search/order can route without failed nets.
- **Orientation-aware A*** (`RouterState(x_idx, y_idx, orientation)`,
  `neighbor_moves`) with **hard bend-spacing** rejection.
- **RouteGrid** occupancy cache; **`legal_crossing_candidate`** and
  endpoint-start crossing detection; **explicit crossing move**
  (`rules.explicit_crossings`); **HistoryCost**; **RoutingWindow** no-overshoot.
- **A* diagnostics/cache** — per-hop failures now include `pops` and `max_heap`,
  `RoutingRules.max_astar_pops` can cap runaway diagnostics, and static
  directed-segment checks are cached inside one A* run.
- **Future port-access spacing reservation** — future foreign access corridors
  hard-block overlap/parallel-spacing only, while crossing/contact remain staged
  through the existing soft/strict policies.
- **Non-cascading/progressive hop windows** — a hop window is expanded only by
  obstacles relevant to the original hop corridor, and external hops try small
  detour windows before the maximum detour window. The default route-window max
  detour is now 4 tracks.
- **Crossing-budget owner diagnostics** — pair-budget failures report the
  crossed owner as `blocked_by=I*/crossing_budget`, feeding conflict-driven
  rip-up.
- **Bounded rip-up plumbing** — later passes can preserve non-selected previous
  routes, reroute only direct failed + budgeted blocker owners, and apply a
  separate `ripup_max_astar_pops` cap for diagnostics.
- **Priority-aware blocker selection** — failed-route messages are converted
  into a deterministic reroute priority: direct failed nets first, then one
  blocker per failed net in round-robin order.
- **Crossing-budget owner feedback for retries** — failed hop messages can name
  ledger blockers, and same-net retry / fallback can penalize crossing those
  owners on the next local attempt. This is a scoped search hint, not a
  correctness relaxation.
- **Staged local port-approach helper** — `_port_approach_point()` and the
  direct pre-escape connector are implemented and covered by synthetic tests,
  but `RoutingRules.port_approach_fallback` defaults to `False` after the PB
  diagnostic showed no route-count improvement.
- **Foreign port-escape guard blockers** — each non-owner port escape has a
  short outside guard segment. A* treats guard overlap/spacing/cross/contact as
  illegal for other nets. This is default-on because it protects real port
  clearance and narrowed PB failures from port access overlap to crossing-budget
  allocation.
- **Staged future-crossing reservation** — future hop L-shape analysis can
  identify owners whose crossings should be saved for later hops, and A* can
  hard-reserve those owners. It is unit-tested but defaults off after the PB
  diagnostic showed no route-count improvement.
- **Priority-ordered reroute** — conflict priority is now used to order reroute
  attempts, not only to choose the reroute set.
- **Crossing-budget provenance** — crossing-budget failures now report the
  source crossing that consumed the pair budget, and those source points are
  carried into later rip-up passes as per-input forbidden points. This is
  diagnostic and partially corrective, but not sufficient to route PB green.
- **Soft repeated pair-crossing policy (Task 6.1)** — different-net repeated
  pair crossings are soft-costed by default through
  `RoutingRules.repeated_crossing_penalty_um`; `hard_crossing_budget=True`
  preserves the old hard wall only for diagnostics. `hop_crossing_class()` keeps
  inward/same-side hops expensive while outward/wrap hops use
  `outward_repeated_crossing_scale`.
- **Soft reserved crossing policy (Task 7 step 0)** — reserved crossing owners
  now add high cost by default instead of hard-rejecting the move.
  `hard_reserved_crossing_reservation=True` preserves the old behavior for
  diagnostics. A* counters distinguish hard budget blocks from soft repeated and
  soft reserved crossing hits.
- **Source-only provenance feeds reroute islands** — if a failure message has
  structured `source=I*->I*` provenance but no `blocked_by=I*` / `region=I*`
  owner, both source owners now enter `_failed_related_inputs()` and
  `_failed_reroute_priority()`. Focused tests are green. This keeps later
  allocator work from losing owner information when the ledger can name the
  source segment but not a final blocker.
- **Coherent reroute island extraction** — `_failure_owner_components()` builds
  connected owner components from failure provenance, and bounded reroute keeps
  direct-failure components intact instead of cutting them at an arbitrary
  budget boundary. This is plumbing for the allocator, not yet proof of PB
  recovery.
- **Source hint split for moving blockers** — `_owner_crossing_sources_for_current_net()`
  keeps pair-allocation hints for the current owner even if the crossed owner is
  being rerouted too, while `_filtered_source_forbidden_points()` still drops
  stale coordinates unless the crossed route is fixed.
- **Soft source-derived crossing reservation** — source-derived pair hints now
  default to deferred/high-cost crossing pressure instead of hard crossing
  reservation (`hard_source_crossing_reservation=False`). Hard mode remains
  available for diagnostics.
- **History-aware bounded candidate ordering** — bounded waypoint candidates are
  sorted by `HistoryCost` before length, so local retry can avoid previously
  bumped congested segments instead of replaying the same shortest deterministic
  candidate.
- **Source-derived hop reservation helper** — `_source_reserved_crossing_owners()`
  is shared by ordinary hop routing and bounded beam fallback. This removes a
  latent `source_crossings=` keyword bug in the fallback path and keeps source
  allocation behavior consistent across both paths.
- **Mirrored pair-source hints** — failed crossing sources are now recorded for
  both owners of the pair. The crossed owner receives a mirrored
  `_CrossingSource`, so it can reserve the same pair on the segment that was
  formerly the crossed segment. This is still only an allocation hint; it does
  not by itself make PB green.
- **Pair-local source-detour runtime lock** — focused route-level coverage now
  confirms `_route_external_hop()` can use a source hint to produce a bounded
  detour that avoids re-crossing the same blocker. This narrows the remaining
  failure to multi-hop/blocker interaction rather than the source-detour
  primitive itself.
- **Source-hint bounded-only fail-fast** — source-reserved reroute hops now stop
  after bounded alternatives fail instead of entering generic A*. This is a
  runtime guard for known pair-allocation conflicts; it does not relax legality.
- **Capped rip-up bounded-only guard** — capped rip-up diagnostics now stop
  after bounded candidates fail, so PB no longer spends the whole timeout inside
  broad A* on later passes. This keeps diagnostics actionable without changing
  uncapped first-pass/default A* behavior.
- **Bounded fail-fast provenance preservation** — fail-fast messages carry
  structured source hints so `_failed_related_inputs()` and
  `_failure_owner_components()` keep pair islands connected across bounded
  reroute passes.
- **Owner-aware port-guard diagnostics** — reserved port guards retain owner
  tags through physical validation, so fail-fast messages can name
  `blocked_by=I*/port_guard`. A* still receives plain geometry-only guard
  segments.
- **Same-net conflict diagnostics** — same-net legality remains hard, but
  source-detour/direct-approach failures now include the conflict kind and prior
  same-net segment. This is diagnostic plumbing for candidate shaping.

---

## 3. Partially Done

**Same-net self-knot prevention** (`routing/grid.py`):

- `_same_net_self_conflict()` exists; A* rejects same-net self-overlap,
  self-crossing, T-touch; reconstructs the current hop's partial path from the
  `prev`-chain; synthetic tests added (self-cross, T-touch, overlap, predecessor
  endpoint, join point). `refinement.py` not expanded.

Why not Done:

1. `_route_one_path()` now passes `external_segments + local_segments` into
   same-net checking, and synthetic local/stub checks exist. End-to-end
   verification across all topologies is still pending after the latest access
   and ledger changes.
2. `_build_route_grid()` now accepts owner-tagged occupied external segments, so
   crossing candidates can report the real crossed input.
3. A history-light net-pair ledger exists and records classified crossings.
   Same-net conflicts are hard rejects; different-net repeated pair crossings
   are now soft-costed and reported. PB targeted runs can now report pair counts
   greater than 1; this is intentional loss/reporting data, not a DRC failure.
4. A* dominance key is only `RouterState(x_idx, y_idx, orientation)` while
   legality is now path-history dependent.
5. Performance risk remains high. Partial same-net geometry is cached by state,
   and static per-segment checks are now cached inside A*, but endpoint-crossing
   / pair-budget enforcement still makes single-topology PB checks take minutes.
6. Full suite is RED and latest PB targeted routing still has failed nets (§1).
7. Bounded per-net beam fallback exists but is staged off by default after a PB
   probe exceeded the acceptable targeted-test runtime.

---

## 4. Required Next Tasks

Order matters. Testing discipline per task: prefer `py_compile` + targeted `-k`
subsets; run the full suite only at the end of a logical batch (see §9).

### Task 1 — Confirm baseline without over-testing

- Record the latest full-suite result: pass/fail, failed nets, DRC, per-topology
  crossings, runtime vs the ~526 s prior baseline. Current values are in §1.
- Do not re-run the full ~16 min suite after every small change.

### Task 2 — Same-net local/stub visibility

Status: code exists, synthetic tests exist, final e2e re-verification pending.

- `_route_one_path()` now passes committed `external_segments + local_segments`
  into `_same_net_self_conflict`.
- Synthetic tests cover crossing / T-touch / overlap against same-net
  local/stub-like geometry and preserve legal adjacent-endpoint connection.
- Internal MRR geometry remains excluded: it is inside the inflated MRR keepout
  that external A* already avoids.
- Still required: rerun targeted Waksman same-net validation after the latest
  port-access / ledger changes; do not mark done from synthetic tests alone.

### Task 3 — Broaden same-net end-to-end validation

Status: test code exists, currently pending green routing.

- E2E validation now checks same-net DRC rules and
  `external_segments + local_segments` self-conflict shape.
- It cannot be considered green while PB still has failed nets under the latest
  default targeted run.

### Task 4 — Optimize same-net conflict performance

Status: partially done, still a blocker.

- Partial same-net geometry is cached per `RouterState`; the old per-pop
  `prev`-chain reconstruction is no longer the only mechanism.
- Runtime is still unacceptable after endpoint crossing / pair ledger: a single
  PB targeted summary can take several minutes.
- Implemented instrumentation: failure summaries include A* `pops` and
  `max_heap`; `max_astar_pops` gives bounded diagnostics.
- Implemented optimization: static directed-segment checks inside one A* run are
  cached (availability, crossing info, soft penalties, backtrack penalty,
  committed same-net checks).
- Implemented optimization: current-hop partial same-net geometry is kept
  collinear-merged per state, reducing repeated self-conflict scans on long
  straight grid runs.
- Implemented optimization: hop windows no longer cascade to unrelated
  obstacles; external hops try progressive detour windows `(base, 4, max)`.
  The normal maximum is now `route_window_max_detour_tracks=4`; setting it to 8
  restores the wider diagnostic behavior.
- Still required: reduce the number of expanded states or avoid doomed searches
  earlier. The latest 5000-pop PB diagnostic still caps I3 and I5 inter-stage
  hops, and I4 now exposes a terminal crossing-budget conflict against I1.
  With the 12000-pop diagnostic, the same single pass finishes with 3 routed
  nets and explicit blockers for all failed nets.
- Bounded reroute plumbing now exists, but it has not yet changed the PB endpoint:
  bounded two-pass diagnostics still return 4/6 routed. The remaining issue is
  choosing better local alternatives for the specific failed/blocker pairs, not
  simply trying more whole-net attempts.
- Priority-aware reroute selection is implemented and unit-tested. It does not
  remove the need for single-hop alternatives; a PB diagnostic with it still ran
  past the interactive wait budget.
- Crossing-budget retry feedback and `ripup_route_budget=5` were both checked on
  the capped PB diagnostic. Both still return 4/6 routed with the same failures,
  so the next implementation should focus on local port-approach alternatives
  for the I0/I5 access-lane collision and the I2/I3 crossing-budget hop, not on
  adding more whole-net retry variants.
- A wider `route_window_max_detour_tracks=8` probe exceeded the 150 s timeout,
  so window widening is a performance regression, not a confirmed recovery path.
- A simple pre-escape approach alternative was implemented and tested, then
  staged off by default after the PB probe still returned 4/6. The result is
  useful evidence: I0 is no longer just an overlap problem, it needs crossing
  budget to be reserved for the final port approach or the blocker route to move.
  The next step should be owner/pair-aware port-lane planning or blocker-local
  reroute, not another generic A* fallback.
- A narrow foreign port-escape guard is now implemented and default-on. The PB
  probe still returns 4/6, but the I0 failure moves from port-access clearance to
  `blocked_by=I1/crossing_budget`; I2 remains `blocked_by=I3/crossing_budget`.
  This confirms the next implementation must allocate a pair's single legal
  crossing across a whole net, or locally reroute blocker owners I1/I3, rather
  than only protecting port lanes.
- Hard future-crossing reservation and priority-ordered reroute were both tested
  and do not recover PB. The next useful diagnostic is provenance: identify
  whether the consumed pair budget comes from a fixed blocker route, a rerouted
  blocker that still precedes the failed net, or an earlier hop of the same
  failed net. Do not add more generic A* retries before that is known.
- Provenance now shows both current PB failures are caused by earlier hops of
  the same failed net consuming the pair crossing. Source-forbidden retry avoids
  repeating those exact points but exposes A* pop-limit/no-route failures. The
  next implementation should narrow the local alternative space around those
  source hops, e.g. a small set of explicit alternate approach tracks, rather
  than free-running another full A*.
- Structured source provenance and bounded source-detour candidates are now
  implemented. They can avoid a known source crossing in isolation, but in the
  real PB fixed-route context I0's first hop cannot preserve the I0/I1 pair
  budget unless blocker geometry also moves; candidate samples hit I1's port
  guard or I1's horizontal segment. Treat "failed net only" reroute as disproven.
- Interleaved failed/blocker reroute is implemented and unit-tested. Capped PB
  evidence: pass 1 `[0,1,2,3]` routes `[5,4,1]` and fails I0/I2/I3 on pop-limit
  or new crossing-budget blockers; pass 2 includes I5 but routes only `[4,5]`.
  The next implementation should prevent cascading degeneration, likely by
  keeping a larger coherent reroute island or by using a route-order/global
  candidate strategy instead of preserving only the previous pass's non-selected
  routes.
- A naive route-order beam has been implemented behind `route_order_beam_width`
  but is not a completion path yet. It recomputes too much state and still lands
  on the same 4/6 PB endpoint. If this path is continued, add memoization of
  `(routed_input_set, next_input, conflict signature)` or a stronger order
  heuristic before expanding the beam width.
- Bounded rip-up order candidates can generate useful alternative failed sets,
  but the first `limit=3` PB diagnostic timed out. Keep the default limit at `1`
  and do not promote order-candidate search until it can prune by conflict
  signature before launching full A* routes.
- DRC owners now feed reroute feedback when a partial pass has both failed nets
  and routed violations. This prevents a routed same-net hairpin owner from
  being treated as a harmless fixed blocker. It is necessary plumbing, but the
  PB endpoint is still unchanged.
- Failure occurrence order now feeds reroute priority. Keep this behavior:
  sorting direct failed nets back through the original complexity order hides
  the actual conflict cascade observed in rip-up passes.
- Source-only crossing provenance now feeds reroute related-input and priority
  extraction. This closes a bookkeeping gap for future pair-allocation work, but
  it is not expected to recover PB by itself because the latest failures already
  usually include explicit `blocked_by` owners.
- A direct precedence-order probe can now produce reverse source provenance
  (`I1->I0` after forcing `I0` before `I1`). Treat source constraints as
  allocation evidence, not as a static total ordering rule.
- Bounded reroute no longer slices through direct failure components. This
  prevents a reroute pass from preserving fixed geometry from the same local
  conflict island, but the actual hop-level pair allocation remains pending.
- Source-history handling now separates geometry from allocation: stale source
  points are not blindly forbidden when blockers move, but the owner pair can
  still be reserved on matching future hops. This is the narrowest current
  implementation of hop-level allocation pressure.
- Bounded beam fallback now uses the same source-derived crossing reservation
  helper as normal hop routing. It still needs a small route-level runtime test
  before being considered a supported completion path.
- Pair-source hints are now bidirectional. This removes a blind spot where the
  blocker side of a reroute island could recreate the same pair consumption
  without seeing the previous source provenance. The latest low-cap PB probe
  still timed out, so the next step is a smaller pair-local runtime test or a
  true allocator, not another whole-PB diagnostic.
- The pair-local runtime test is now in place and green. Since the local source
  detour primitive works, the next implementation should allocate/shape across
  multiple hops or reroute the blocker owner locally, not add another source
  detour variant for one hop.
- Source-reserved hops no longer fall through to broad A* on reroute passes, but
  the 45 s PB probe still timed out. The next diagnostic should identify the
  remaining non-source-reserved pop-limit hops and add an equally bounded local
  strategy for those, rather than expanding the global search.
- Capped rip-up diagnostics now return instead of timing out. The current PB
  capped endpoint is stable and explicit: routed `[1,2,4,5]`, failed `I0/I3`,
  crossings 6, DRC 0. The previous `I3` same-net hairpin is now prevented before
  commit, and `I2` recovers. The next implementation should target the remaining
  concrete local blockers: `I0` while preserving the `I0/I5` pair allocation and
  avoiding fixed `I4` spacing, and `I3` while preserving the `I1/I3` allocation
  without crossing the fixed `I1` lane or MRR obstacle window. A bounded
  3-pass probe remains unchanged, so the next fix needs better local allocation
  or blocker-local route shaping, not merely one more rip-up pass.
- Pass-trace verification now shows the pair islands stay connected across
  reroute passes. The next blocker is not owner selection; it is source-detour
  candidate quality (`bend spacing` and `same-net self conflict`) for the
  `{0,1}` and `{2,3}` local islands.
- Owner-aware port-guard diagnostics now identify the remaining `{0,1}` guard
  blocker (`blocked_by=I1/port_guard`) instead of `unknown`. This closes the
  provenance gap; it does not recover PB. The next implementation should use
  that owner information to shape a local pair/hop allocation, not add another
  generic full-net retry.
- Same-net conflict diagnostics now identify the remaining `{2,3}` failures as
  source-detour candidates crossing already committed same-net vertical geometry
  near `x=220`. The next implementation should either generate candidate
  detours that avoid same-net committed tracks, or score/filter source-detour
  alternatives with same-net geometry before exhausting the bounded set.
- A guard-off probe is explicitly negative evidence: future/foreign port guards
  are not the thing to delete. The useful path is finer candidate allocation for
  the named owner pairs.
- A wider source-detour probe is also negative evidence: more alternate tracks
  do not recover PB. Candidate generation needs same-net/future-port awareness,
  not a larger blind candidate pool.
- Candidate-origin diagnostics show the remaining PB failures are now
  deterministic bounded candidates, not source-detour candidates. Continue work
  in deterministic/local hop shaping before adding any broader A* fallback.
- Same-net avoidance candidates are implemented and locally useful, but they do
  not yet recover the best PB endpoint. The next candidate-shaping work must
  handle deterministic candidates that self-conflict with local port-side
  geometry and repeated vertical approach tracks, not just one orthogonal
  committed-segment crossing.
- Rip-up port guards are now committed-owner aware. This removes the false
  `I1/port_guard` wall for I0 during the same reroute batch, but exposes the
  real I0/I5 crossing allocation conflict. Do not revert to global guard-off;
  instead improve owner-pair allocation and blocker inclusion scoring.
- Deterministic crossing-budget provenance is now complete enough to pull I5
  into related-input analysis. The observed PB pass-2 regression means reroute
  selection must distinguish helpful blockers from disruptive blockers, or use
  best-pass retention deliberately.
- Candidate-source reroute policy now separates allocation hints from hard
  reroute membership. This prevents candidate-level blockers such as I5 from
  being ripped up automatically, while still allowing I0 to see the I0/I5 source
  hint. The next remaining PB work is not owner selection for I5; it is better
  local candidate generation for I0 around fixed I5/I1 and for I2/I3 near the
  x=220 same-net vertical approach.
- Source-derived reservation is now soft by default. This removes the
  over-constraining "reserve a future pair crossing before it is actually
  needed" behavior, and the latest PB failure shape confirms it: I0 now reaches
  an explicit I0/I1 crossing-budget conflict instead of dying on the old
  source-reserved I0/I5/I4 geometry. Do not turn this back into a hard heuristic.
- History-aware bounded candidate ordering is implemented, but PB still fails
  with routes `[1,2,4,5]`, failed `I0/I3`, crossings 6, DRC 0. The next real
  implementation should add cross-owner negotiated congestion memory: bump
  congestion from failed source segments/components across reroute passes and
  let earlier owners be rerouted away from lanes they consumed, rather than only
  retrying the failed owner's local hop.

### Task 5 — Owner-tagged occupied route segments

Status: implemented and synthetically verified.

- `OccupiedRouteSegment(owner_input, segment, kind)` exists.
- Ownership is threaded through `_route_physical_order -> _route_one_path ->
  _astar_route -> _build_route_grid`.
- `RouteGrid` now marks occupied external waveguides with the real input owner.
- Keep this as a regression lock; do not reintroduce anonymous
  `owner_input=-1` marking for routed waveguides.

### Task 6 — Net-pair crossing ledger

Status: Task 6.1 is implemented and focused-test green; project routing is not
done because PB still has a failed net under bounded routing.

- The ledger is seeded from already committed routes and the current net's
  already committed external hops.
- `legal_crossing_candidate`, `endpoint_crossing_candidate`, and
  `endpoint_crossing_required` classify both interior crossings and grid-node
  crossings that become real crossings after collinear merge.
- Same-net self-conflict remains hard: overlap, interior self-crossing, T-touch,
  hairpin/loop are routing rejects.
- Different-net repeated crossings are no longer hard-rejected by default.
  `RoutingRules.repeated_crossing_penalty_um` prices the second+ crossing, and
  `RoutingRules.hard_crossing_budget=True` keeps the old hard wall available
  only for diagnostics/regression tests.
- `hop_crossing_class(src_port, dst_port)` maps port-side pattern into the cost:
  inward and same-side hops use the high repeated-crossing penalty; outward/wrap
  hops use `outward_repeated_crossing_scale`.
- `PhysicalRoutingResult.crossing_count_by_pair` and the CLI summary expose
  repeated pair counts. A count >1 is a report/loss signal, not a hard
  `DRCViolation`.
- Latest PB bounded probe after Task 6.1: routes `[0,1,2,4,5]`, failed `I3`,
  crossings 14, max pair count 3, DRC 0. This confirms the old hard <=1 wall
  was blocking real routes, but remaining failure is now A* pop-limit/search.
- Still required: finish search/performance/rip-up so the remaining PB `I3`
  route completes, then run the e2e topology tests.

### Task 7 — Review A* history-dependent dominance

Risk: legality depends on partial route history but dominance uses only
`RouterState(x_idx, y_idx, orientation)`; two histories can reach the same state
with different future legality. **Verify first**: today nets do not vanish
(`failed_nets=0`) but crossings inflate — confirm whether the inflation / any
future `failed_nets` trace to dominance discarding a viable history. Implement
**only if needed**: add a compact path/footprint signature to `RouterState`;
conservative dominance when footprints differ; persistent path-node; or a local
rolling footprint around the current corridor.

Latest evidence: both a full positive-ledger signature and a narrower
"pairs-added-in-this-hop" signature were tried. Neither improved PB failed nets;
both made routing slower. The diagnostic `explicit_crossings=False` run proves
the topology can route physically, but only by allowing repeated pair crossings
and a same-net hairpin. The current blocker is therefore the interaction between
pair-budget legality, port-access corridor occupancy, and route ordering, not a
plain dominance-key bug. Revisit only with concrete hop-level evidence.

Current code state: A* now uses the narrower "pairs added in this hop" crossing
signature as part of the search key. Keep it unless performance becomes the
dominant issue again; it is not the PB recovery lever.

Latest Task 7 evidence:

- `reserved_crossing_owners` has been softened by default, so default routing no
  longer has a hidden different-net reserved-crossing hard wall. The diagnostic
  hard mode remains available through `hard_reserved_crossing_reservation=True`.
- Crossing diagnostics are now split. A PB 4500/900 bounded probe fails I3 with
  `crossing_budget:0`, `hard_reserved_crossing:0`,
  `soft_reserved_crossing:0`, and `soft_repeated_crossing:25`. This rules out a
  remaining hard crossing wall for that failure.
- A PB 12000/2400 high-pop probe routes I3 and fails I2 instead on a deterministic
  same-net touch near `(55,166)`. This shows I3 is search-cap limited, not
  physically illegal.
- Weighted A* (`astar_heuristic_weight=1.15`) was tested and made I3 worse at
  12000 pops, so the default is back to `1.0`. The heap still carries remaining
  Manhattan distance as a low-risk tie-break.

Next action: do not put the full same-net footprint into the dominance key yet.
First improve bounded deterministic/same-net candidate shaping around local
vertical port geometry, especially the I2 same-net touch case. Revisit
footprint dominance only if a concrete trace shows a viable same-net-safe
history being evicted.

### Task 8 — Resolve local port escape / visible port-access search blocker

New blocker found during Task 6 verification.

- Default strict port access is staged off (`strict_port_access=False`) because
  `strict_port_access=True` causes PB to fail early on `port_access_touch_foreign`.
- Planned future port-access regions are no longer all exposed to A* as hard
  blockers; visible access is current net + already routed nets. This avoids
  future-port over-reservation but lets earlier routes sometimes block later
  local port escapes.
- Current net's own future port-access segments are now hard blockers during
  that net's external A* hops. This fixed the observed same-net case where an
  earlier external segment blocked a later local escape within the same route.
- Future foreign port-access segments are soft blockers (penalty only), so A*
  prefers not to consume future access corridors without turning them into
  global walls.
- Future foreign port-access segments now also hard-block overlap/parallel
  spacing only. This reserves the physical access lane without forbidding every
  planned access crossing/contact as a global wall.
- Conflict-driven rip-up now parses `region=I*` and `blocked_by=I*` from failure
  messages and includes those blocker owners in the next failed-input set.
- A future-access lookahead penalty now detects visible owners whose port-access
  corridors lie in later hop windows and penalizes crossing those owners too
  early. It changes the failure shape and reduces crossing-budget rejects, but
  does not yet make PB routable.
- Crossing-budget failures now name the crossed owner when available, so
  `_failed_related_inputs()` can bring that blocker into the next rip-up pass.
- The second rip-up order now also prioritizes failed/blocker owners before
  sorting by complexity. This uses the richer failure set rather than ignoring
  it on pass 2.
- Later rip-up passes now preserve previous routes outside the selected reroute
  set. The reroute set always includes direct failed nets and then fills from
  blocker/related owners up to `ripup_route_budget`.
- Reroute set filling is priority-aware: after direct failures, blockers are
  selected round-robin across failed nets instead of by global order only.
- `ripup_max_astar_pops` lets diagnostic passes fail individual A* searches
  earlier than the first-pass cap. It prevents hidden unbounded behavior, but
  does not by itself find better routes.
- Per-net retry/history is implemented in a guarded form: a failed later hop can
  bump the already chosen external segments and reroute the whole net once on
  rip-up passes. Unrestricted retry was too slow and did not recover PB.
- Bounded multi-hop beam fallback has a first implementation, but it is staged
  off by default because the first PB probe exceeded the targeted-test runtime.
  Do not turn it on by default until it is narrowed to the failing-hop region or
  otherwise proven fast.
- Previous uncapped PB default state before the route-window cap still failed 2
  nets after those changes:
  one `port_access_overlap_foreign` against a routed access corridor and one
  route-window/no-route failure, while pair counts stay <= 1.
- Latest capped PB diagnostics after this turn:
  single pass routes 3/6; one blocker-aware rip-up pass routes 4/6; bounded
  two-pass diagnostics return 4/6 when `ripup_max_astar_pops=2000`; the
  priority-aware reroute diagnostic still exceeded the interactive wait.
  Crossing-budget feedback and a `ripup_route_budget=5` probe both still return
  4/6, so the budget/order hypothesis is not sufficient. A global 8-track route
  window probe timed out, so keep the default small-window strategy and solve the
  remaining cases with local alternatives. A first pre-escape local alternative
  is implemented but staged off by default because it still returns 4/6; the
  remaining issue is crossing-budget ownership around the port lane, not merely
  geometric access to the escape point. The default foreign port-escape guard
  removes the I0/I5 clearance issue; the current blocker is now route-local
  pair-budget allocation for I0/I1 and I2/I3. Hard future-crossing reservation
  and priority-ordered reroute have both been ruled out as sufficient fixes.
  Provenance confirms the pair budget is consumed by earlier hops of the same
  failed net; source-forbidden retry needs a bounded local alternative strategy
  to avoid turning into broad A* search.
- Required next implementation work:
  1. use the new region-level diagnostics to distinguish unavoidable corridor
     conflicts from route-window/search misses;
  2. improve rip-up beyond simple reordering: when A overlaps B's access and B
     overlaps A's access in another order, the router needs history penalties or
     blocker-owner reroute, not just a new static order;
     crossing-budget blocker owners are now visible in failure messages and
     one pass improves PB. Bounded reroute plumbing exists, but it still needs
     better local alternatives for the failed/blocker owner pairs;
  3. investigate a stronger bounded multi-hop/path-level search for one net at a
     time. The current guarded retry is too weak, while unrestricted retry is too
     slow. The first full-hop beam implementation is also too slow as a default.
     A useful design likely needs failing-hop-local alternatives, reusable
     route-grid/candidate caches across alternatives, and pruning by
     pair-ledger/port-access conflicts before launching another full A*;
  4. add local crossing-allocation or blocker-local reroute for a conflict pair:
     the router must decide whether the failed owner or crossed owner should
     consume the pair's single crossing before it commits all hops for either
     net. The current sequential order search only moves failures between
     I0/I2 and I1/I5/I3. Pair probes show this must happen at hop level:
     route `[0,1]` works while `[1,0]` fails, but combining `[0,1]` with
     `[2,3]` or `[4,5]` creates new pair-budget/hairpin failures. I0/I5 is
     bidirectional, so the allocator must choose/shape the specific hop
     crossing, not just choose which net routes first;
  5. keep strict `port_access_touch_foreign` staged until zero failed nets are
     recovered under default rules.

### Task 9 — Keep `refinement.py` small (explicit constraint)

Do **not** expand `routing/refinement.py` to repair self-loops, same-net knots,
repeated crossings, or braids — those are A* / RouteGrid / crossing-ledger
legality problems. `refinement.py` stays limited to: crossing-count reporting,
scoring helper, route-result assembly, and simple canonicalization (collinear
merge / dedupe) only if needed. No topology-changing post-route surgery.

---

### Task 10 — Loss-aware A* cost + W/jog → mirror-L shape quality

#### Why (grounded in current code)
- Today the A* step cost is **geometric, in um-equivalent length** units
  (`routing/grid.py` ~457-500): `manhattan(p0,p1)` + `crossing_penalty_um *
  step_crossings` + `repeated_crossing_penalty_um * scale` + `_backtrack_penalty`
  + `_same_net_hairpin_penalty` + bend arc `0.5*pi*bend_radius_um` + turn-timing +
  `history_cost` + reserve-space. None of these are calibrated to optical
  insertion loss, so the router cannot trade "one extra crossing" against "three
  extra bends" in physically meaningful units.
- A loss model already exists in `analysis/cost.py`: `alpha_db_per_um = 0.002`
  (propagation), `xt_cross_db = -40` (crossing crosstalk), `crossing_loss_db_per_cross
  = 0.0` (crossing insertion loss, currently zero), and **no bend-loss term**.
  Router and evaluator must not diverge — reuse these as the single source of truth.
- A* state is `(RouterState(x,y,orientation), crossing-count signature)`; it tracks
  only the previous orientation, **not a recent turn sequence**, so it cannot
  distinguish a clean L from a W/S/jog (see `Physical_output/output/
  padded_benes_highpop_routing.png`: pink `I3->O1` and others route but with ugly
  staircase detours; `crossings=18, max pair=3`).
- Mirror-L machinery already exists: `_l_shape_route_options(src,dst)`
  (`routing/physical.py:4403`) and `_deterministic_hop_candidates` (`:2288`).

#### Task 10a — Loss-aware cost (decision: dB units, flag-gated, single source)
- **Decision: add dB-based loss fields to `RoutingRules`, do not silently reinterpret
  the existing `*_penalty_um`.** Add: `loss_aware_cost: bool = False` (gate, keep
  current geometric behavior by default), `prop_loss_db_per_um` (default = cost.py
  `alpha_db_per_um`), `bend_loss_db_per_bend`, `crossing_loss_db_per_cross` (= cost.py
  value), `repeated_crossing_loss_db`, `jog_penalty_db`. Reuse `analysis/cost.py`
  constants so router and evaluator stay consistent.
- When `loss_aware_cost` is on, compute `step_cost` in dB: `length_um *
  prop_loss_db_per_um` + `bend * bend_loss_db_per_bend` + `crossings *
  crossing_loss_db_per_cross` + repeated-crossing extra + jog penalty (10b).
- **Heuristic must stay admissible in the new units**: scale the Manhattan
  heuristic by `prop_loss_db_per_um` (a true lower bound on remaining loss).
  Honor the existing `astar_heuristic_weight`.
- Keep the old `*_penalty_um` path intact under the default (flag off) so the
  change is incremental and A/B-measurable.
- Acceptance: a synthetic test where, given configured losses, the router picks a
  crossing over N bends iff `crossing_loss_db < N * bend_loss_db` (and vice-versa).

#### Task 10b — W/jog detection and mirror-L (decision: candidate-gen first, shallow turn-cost second, NOT refinement)
- **Primary (lowest risk): bounded candidate generation in `physical.py`.** For a
  hop, try the two `_l_shape_route_options` (the L and its mirror) and accept the
  cleanest legal one before falling through to full A*. This is also where the I2
  blocker is fixed (10c). It does not touch A* dominance.
- **Secondary (only if shapes persist): shallow turn-history as a COST term in A*,
  not a dominance-key field.** Track the last 1-2 turn directions (extend the
  existing `last_bend` bookkeeping) and add `jog_penalty_db` when a third
  *alternating* turn forms a W/S. **Do not** put turn-history in the dist key
  (avoids the state explosion warned about in Task 7).
- **Explicitly rejected: a post-route flatten pass in `refinement.py`.** A blind
  mirror/flatten after routing can break spacing, port-access, crossing-budget,
  and same-net legality that A* already guarantees; and `Lidar.md` (#6, lines
  33/347-348/373) prefers prevention via window+cost over reactive repair. Shape
  quality must live in A* cost and/or bounded candidates.
- Acceptance: a synthetic W/jog candidate is penalized (10a flag on) or replaced
  by a legal mirrored-L; the mirrored-L is accepted **only when legal**; a hop with
  no legal L still routes via A*.

#### Task 10c — Specific blocker: high-pop I2 same-net touch
- Failure: `deterministic hop candidate creates same-net self conflict;
  same_net_touch at=(55,166) prior=(55,180)->(55,166)` — the candidate touches its
  own prior **vertical port-side** segment (the stub/escape verticals share an x).
- **Decision: yes — `_deterministic_hop_candidates` / the same-net candidate path
  should generate a mirrored route around the local vertical port-side geometry**
  (the other `_l_shape_route_options` option, or a bounded jog that approaches the
  port from the opposite turn side) *before* reporting a same-net conflict failure.
- Same-net self-conflict stays a **hard** reject (Task 2); 10c only adds a legal
  alternative candidate so the router does not give up at the first self-touch.
- Acceptance: PB high-pop probe routes I2 (no `same_net_touch` failure), DRC clean,
  no same-net self-conflict introduced.

#### Task 10d — Testing strategy (do not run the full suite per edit)
1. `python -m py_compile` the changed modules.
2. Focused synthetic tests:
   - loss-aware cost chooses crossing vs bend according to configured losses;
   - a W/jog pattern is penalized (flag on) or replaced by a mirrored-L;
   - the mirrored-L candidate is generated and accepted **only when legal**;
   - same-net self-conflict remains hard (regression).
3. One PB targeted probe at both caps: bounded `max_astar_pops=4500 /
   ripup_max_astar_pops=900`, and high-pop `12000 / 2400`. Report routed/failed
   nets, crossings, max pair-crossing, DRC, and (eyeball) shape via the PNG.
4. Full `tests/test_physical_router.py` only at the task boundary.

#### Task 10e — Constraints (unchanged)
- Do not weaken same-net legality. Do not make port access a global hard wall.
- Do not make repeated pair crossing hard again. Do not expand `refinement.py`.
- Keep it incremental (flag-gated cost) and measurable (probe numbers + PNG).

#### Task 10f — Definition of done for this task
- `loss_aware_cost` flag exists; with it on, cost is dB-calibrated and the
  heuristic stays admissible; synthetic loss-tradeoff test passes.
- W/jog shapes are reduced (mirror-L preferred when legal); PB high-pop I2 routes.
- PB bounded + high-pop probes route with no new failures or DRC, and visibly
  cleaner shapes; full suite green at the task boundary.

#### Task 10g — Bend-placement policy

Bend *position* matters as much as bend *count*: a turn placed inside an MRR
keepout fringe, right at a port, or inside a port-access lane is both lossy
(tight, scatter-prone) and congestion-creating. Make bend placement a first-class
preference, composed with 10a (cost units) and 10b (candidate generation).

What already exists (extend, don't duplicate):
- `_turn_timing_penalty` (`routing/geometry.py:222`) + `turn_guard_um=16`,
  `turn_timing_penalty_um=48` already penalize a turn within `turn_guard_um` of
  the hop's **own** src/dst, but: (a) only the hop's own endpoints, not other
  cells' ports/keepouts/access-lanes the route passes; (b) disabled at bus
  endpoints (`use_turn_timing` is off when src is `I..` or dst is `O..`).
- `_l_shape_route_options(src,dst)` (`routing/physical.py:4403`) yields only the 2
  basic L corners — no delayed-turn variants.

Policy to add:
1. **Penalize turns near sensitive geometry (cost term, not a hard wall).**
   At a bend point, add a placement penalty when the bend lies within a guard
   distance of: an inflated MRR keepout edge (`obstacles`), any port / stub /
   escape point, or a reserved port-access lane (`port_access.plan.reserved_regions`).
   All are already available in the A* loop. Reuse `turn_guard_um` for the
   distance; express the penalty in dB when `loss_aware_cost` is on (a
   `bend_placement_penalty_db` field), else in the current um-equivalent units.
   Generalize the existing turn-timing so it also fires near *foreign* cell
   geometry, and let the keepout/access-lane case apply even at bus/output hops
   (where `use_turn_timing` is currently off). This stays a **soft cost** — do not
   turn it into a hard reject (constraint: no new global walls).
2. **Generate delayed-L and mirrored-L candidates before broad A*.**
   Extend the bounded candidate set (10b, `_deterministic_hop_candidates` /
   `_l_shape_route_options`) with: the mirrored-L (10b) **and** delayed-L variants
   that push the single turn onto a track *outside* the sensitive zones (turn
   later/earlier so the corner clears keepout/port/access lanes). Try these
   bounded candidates before falling through to full A*.
3. **Score candidates with insertion-loss-aware cost, not Manhattan length.**
   Rank the delayed-/mirrored-L candidates by the 10a dB cost (propagation + bend
   loss + crossing loss + bend-placement penalty), so a slightly longer route with
   a cleanly-placed bend beats a shorter route that turns inside a keepout/lane.
4. **Same-net self-conflict stays hard.** Bend-placement is only a
   preference/penalty and an extra candidate source; it never relaxes the hard
   same-net self-overlap / self-cross / T-touch / hairpin reject (Task 2), and it
   must not place a bend that creates one.

Decision on where it lives: the penalty is an A* **cost term** (turn cost,
generalizing `_turn_timing_penalty`); the delayed-/mirrored-L generation is
**bounded candidate generation** in `physical.py`. Not a `refinement.py` pass.

Tests (synthetic first, then PB probe):
- a turn placed within `turn_guard_um` of a keepout edge / port / access lane is
  penalized; an equivalent-length route that turns in open space is preferred.
- a delayed-L candidate that moves the bend out of a sensitive zone is generated
  and chosen when legal; if no legal delayed/mirrored L exists the hop still
  routes via A*.
- candidate ranking uses the 10a loss cost (turn-in-the-clear beats shorter
  turn-in-keepout).
- same-net self-conflict remains hard (regression).
- PB bounded (4500/900) + high-pop (12000/2400) probes: no new failed nets / DRC,
  visibly fewer bends inside keepout/port/access regions.

2026-06-17 implementation status:
- Implemented flag-gated `loss_aware_cost` in A* with dB propagation,
  bend, crossing, repeated-crossing, jog, and bend-placement terms. Default
  geometric mode remains on the legacy cost path.
- Added bounded L/mirror-L and delayed-L candidate generation for loss-aware
  routing; candidates still pass through the existing hard legality gate before
  acceptance. Same-net self-conflict remains a hard reject.
- Added CLI knobs for loss-aware routing and capped probes:
  `--loss-aware-cost`, dB loss terms, `--max-astar-pops`, and
  `--ripup-max-astar-pops`.
- Focused synthetic tests pass for crossing-vs-bend loss tradeoff, jog cost,
  bend-placement cost, legal mirror-L acceptance, delayed-L preference, and
  illegal candidate rejection.
- Uncapped PB loss-aware run routed all 6 paths with 0 DRC and saved
  `Physical_output/output/padded_benes_8x8_2-0-5-1-3-4.png`.
- Capped PB probes are not yet clean. After relaxing clean foreign port-access
  crossings, turning future/uncommitted access spacing into soft cost, allowing
  reserved-guard crossing/touch except overlap/spacing, and converting source
  forbidden-point retry memory into history cost, the 4500/900 bounded probe
  routes 4/6 with DRC 0 and fails I0/I2 on A* pop limits. The failed counters
  are dominated by segment/window/same-net/crossing-candidate search, not hidden
  guard or hard crossing walls. Debug artifacts:
  `Physical_output/output/task10_relaxed_bounded_I0_failure_debug.png` and
  `Physical_output/output/task10_relaxed_bounded_I0_failure_debug.txt`.

#### Task 11 — Sparsify the A* routing grid (fix pop-limit by reducing state, not legality)

##### Why (measured)
The bounded PB probe (4500/900) fails I0/I2 with **`A* pop limit exceeded`**, and
the counters prove it is **search exhaustion, not a legality wall**:
`crossing_budget:0, hard_reserved_crossing:0`; dominant blocks are
`segment:643/938` and `window:211/252`; and critically **`pops:4501` while
`max_heap≈400`** — A* churns inside a cramped region without reaching the target.
(Matches the 2026-06-17 status note above.)

Root cause is an over-dense, irregular global grid. Measured for PB:
- `x_tracks=126, y_tracks=101` → **12,726 nodes** to route short corridors.
- median spacing **x=5.0 µm / y=3.0 µm** (finer than `grid_pitch=8.0`); min 1.0 µm.
- **46 x-tracks and 64 y-tracks are closer than `grid_pitch/2` (4 µm)** — many
  near-duplicate tracks.

Source: `_routing_grid` (`routing/grid.py:1077`) injects `escape`, `stub`, AND
`port_xy` x/y for **every** cell port (lines 1096-1097) into one global track set,
so each cell sprays sub-pitch tracks near its ports. That dense+irregular grid,
multiplied by orientation and the crossing-count signature, explodes the A* state
space → the pop-limit failures. The "too dense candidate" is the **grid tracks**,
not the L-shape candidates.

##### Task 11a — Inject only `escape` points as A* tracks (structural reduction)
- A* only routes `escape -> escape`; the `escape -> stub -> port` leg is built by
  `_append_local_axis` and needs no grid node. In `_routing_grid` drop
  `stub[0]/port_xy[0]` and `stub[1]/port_xy[1]` (lines 1096-1097); keep
  `escape[0]/escape[1]`, obstacle edges, and the uniform pitch.
- Removes the per-cell `center±4` / stub/port clusters directly.
- Acceptance: PB global node count drops materially from 12,726; routes still
  contain their stub/port points (those come from local axis, not the grid).

##### Task 11b — Dedup near-duplicate base tracks (tolerance merge)
- `_canonical_track` (`grid.py:1143`) only merges *exact* duplicates. Add a
  tolerance merge in `_tracks_between` (`grid.py:1120`): after building the base
  list, collapse tracks closer than `grid_merge_tol_um` into one representative.
- Add `RoutingRules.grid_merge_tol_um: float` (default conservative, e.g. **2.0**;
  must stay **< `min_spacing_um`=4.0** and ≪ `wire_pitch`=36, so distinct legal
  parallel positions are never merged).
- **Reachability guarantee (must hold):** `_astar_route` already calls
  `_insert_track(grid[0], src[0], dst[0])` per hop, so the hop's exact src/dst
  (escape points) are re-inserted after base-grid dedup — dedup of the *base* grid
  can never remove a coordinate A* must land on. Do **not** add the tolerance merge
  into `_insert_track`; keep per-hop endpoint insertion exact.

##### Task 11c — (optional, larger) per-corridor grid
- If 11a+11b are insufficient, build the track set per hop/corridor (bounded by the
  routing window) instead of one global grid. Defer unless probes still pop-limit.

##### Interaction with Task 7
After the grid is thinner, re-trim the crossing-count signature (Task 7-a) so each
cell holds fewer states. 11a/11b are the cheap structural win — do them first and
re-measure before touching dominance.

##### Constraints
- Do not change any routing legality (same-net, crossing, port-access, spacing).
- Preserve reachability (exact per-hop endpoints, as above).
- `grid_merge_tol_um < min_spacing_um`; never merge two legal parallel positions.

##### Testing (do not run full suite per edit)
1. `py_compile`.
2. Synthetic grid-density test: build the PB grid and assert (a) node count drops
   substantially vs the 12,726 baseline, (b) every adjacent base-track gap
   `>= grid_merge_tol_um` (except re-inserted exact endpoints), (c) a hop's
   src/dst/escape coordinates are present after `_insert_track`.
3. Bounded PB probe (`4500/900`): expect `pops` no longer pinned at the cap,
   `segment`/`window` counters down, and I0/I2 ideally route (failed → 0). Re-run
   the density measurement; record before/after node counts.
4. Full `tests/test_physical_router.py` only at the task boundary.

2026-06-17 implementation status:
- Implemented 11a/11b: `_routing_grid` now injects only escape coordinates for
  cell ports, and `_tracks_between` merges near-duplicate base tracks with
  `RoutingRules.grid_merge_tol_um=3.5` while keeping `_insert_track` exact for
  per-hop endpoints. CLI exposes `--grid-merge-tol-um`; DRC validation enforces
  `grid_merge_tol_um < min_spacing_um` when spacing is active.
- PB grid density now measures `x_tracks=83, y_tracks=36` → **2,988 nodes**,
  down from 12,726 (**76.5% reduction**). Minimum adjacent gaps are x=4.0 µm and
  y=6.0 µm; no `<4 µm` sub-pitch gaps remain.
- Focused compile/tests pass:
  `python -m py_compile mrr_switch_optimizer/routing/grid.py mrr_switch_optimizer/routing/types.py mrr_switch_optimizer/routing/drc.py mrr_switch_optimizer/routing/physical.py mrr_switch_optimizer/app/cli.py tests/test_physical_router.py`
  and
  `python -m pytest tests/test_physical_router.py -k "sparsified_routing_grid or grid_merge_tolerance or alternating_jog or insertion_loss or loss_aware or deterministic_mirror_l or deterministic_delayed_l" -q`
  (`10 passed, 101 deselected`).
- Bounded PB `4500/900` no longer reproduces the old I0/I2 pair, but is not yet
  clean: routed 4/6, failed I4/I2, DRC 0, crossings 13. I4 still hits rip-up
  A* cap (`pops:901`) with counters dominated by same-net/window/segment search;
  I2 fails before A* because a same-net avoidance candidate is blocked by the
  prior forbidden/history point at `(152,251)`.
- Raising only the rip-up cap to `2400` changes the failure set, not success:
  routed 4/6, failed I1/I2, DRC 1, crossings 13. I1 is blocked by an I3 external
  spacing segment `(440,112)->(440,191)`; I2 remains the same forbidden/history
  point blocker. This suggests 11a/11b solved grid density, but acceptance still
  needs Task 7-a state-signature trimming and/or 11c per-corridor/bounded-candidate
  changes.
- Added an I/O-side x-bound clamp so routing windows cannot detour left of
  `x_start` or right of `x_end`. A stricter input/output access-stub experiment
  was not kept: forcing A* to start/end inside the terminal column caused broad
  foreign port-access overlap failures, so "no vertical routing on the terminal
  column" needs a dedicated access-channel rule rather than a hard window shrink.
- 2026-06-18 relaxation probe:
  - Kept Task 11 grid sparsification and raised the default route-window detour
    cap to `route_window_max_detour_tracks=8`.
  - Changed same-net hairpin from a hard route/DRC reject into soft search cost;
    same-net overlap, self-crossing, and T/corner touch remain hard.
  - Relaxed default explicit-crossing clearance classification: if
    `min_crossing_clearance_um` is unset, a crossing no longer inherits
    `bend_radius_um` as a hidden clearance wall. Explicit nonzero clearance still
    rejects near-endpoint crossings.
  - Result for PB `(2,0,5,1,3,4)`: both `4500/900` and `4500/2400` bounded probes
    route **6/6**, DRC 0, grid 2,988 nodes, with 38 crossings and max pair count 4.
    This confirms the previous failures were search/legality over-pruning, but
    the route quality now needs crossing-count/loss cleanup.
- Artifacts:
  `Physical_output/output/task11_grid_sparsified_probe/task11_grid_sparsified_report.txt`,
  `task11_probe_summary.csv`,
  `task11_bounded_4500_900_partial_failure.png`, and
  `task11_bounded_4500_2400_partial_failure.png`. The 2026-06-18 routed copies are
  `task11_relaxed_4500_900_routed.png` and `task11_relaxed_4500_2400_routed.png`.

##### Definition of done
- PB global grid node count materially reduced (target: well under half of 12,726).
- Bounded PB probe no longer pop-limits on I0/I2; DRC clean; no same-net knots.
- Full suite green at the task boundary; no legality changes.

---

## 5. Later Cleanup

- Shrink `routing/refinement.py` once A* prevents knots and repeated crossings:
  retire reactive repair families (`_hairpin_*`, `_small_jog_*`, `_turn_timing_*`,
  `_simplify_*` window replacement, `_braid_*`). Keep final DRC, crossing
  reporting, collinear merge/dedupe, route assembly, rip-up ordering.
- Move `_canonical_track` from `routing/grid.py` into `routing/geometry.py` so
  `routing/drc.py` stops importing the search layer.
- Concise docs of module responsibilities and routing rules (see §6 Documentation).

---

## 6. Project Completion Tasks

After routing correctness is stable.

**Core / topology.** Keep RNB / Padded-Beneš / Waksman / Spanke-Beneš generation
verified; route steps always valid add-drop transitions (`in->th`, `in->drop`,
`add->drop`, `add->th`); topology output deterministic for a fixed permutation.
(Already verified over all permutations — keep as regression locks.)

**Add-drop physical convention.** Keep the standard convention (Appendix A); do
not revert to the co-propagating shortcut. Confirm consistency across
`core/models.py`, `core/sparams.py`, `routing/port_access.py`,
`output/visualize.py`.

**Routing correctness guarantees** (default rules): zero failed nets; DRC-clean;
no same-net self-cross / T-touch / overlap / hairpin; no net pair crosses more
than once; no non-owner port-access overlap/T-touch/crossing; deterministic
routes for fixed topology/permutation.

**CLI and reports.** Verify smoke:

```bash
python main.py --topology waksman --permutation 2,0,5,1,3,4 --physical-eval --outdir <tmp>
```

Expected: `physical_path_distribution.csv`, `physical_summary.csv`,
`drc_violations.csv`.

**Visualization.** After routing changes: PNG generation succeeds; port labels
match the add-drop convention; routed paths visible and Manhattan; no obvious
route/port mismatch.

**Performance budget.** Track full physical-suite runtime, route time per
topology, and the cost of self-conflict checks and the crossing ledger. Staged
testing (§9), not repeated full runs.

**Documentation.** Module responsibilities; routing legality rules; port-access
policy; crossing policy; why `refinement.py` is intentionally small; relationship
to `Lidar.md` (Appendix D).

---

## 7. Optional Experiments

LiDAR / GDSFactory backend — only after the internal router is stable. Do not
vendor LiDAR; do not replace topology-aware routing. Build an optional comparison
backend only, comparing route length, crossings, DRC, and layout quality.

---

## 8. Definition of Done

The project is complete only when:

- full tests are green
- all three topologies route with zero failed nets
- default routing is DRC-clean
- same-net knots are impossible under tested cases
- net pairs cross at most once by default
- routing is deterministic
- strict port access is enforced
- `refinement.py` remains small
- CLI physical evaluation works
- visualization works
- `Schedule.md` accurately says what is done vs pending
- performance is acceptable and documented

---

## 9. Testing Strategy / When to Run Full Suite

The full suite is ~16 min; do not run it repeatedly while developing. Staged:

1. `python -m py_compile` the changed modules.
2. Small **synthetic** unit tests for the changed helper (fast, no routing) —
   e.g. `pytest -k "same_net or crossing or corridor"`.
3. One **targeted topology** route/test for the affected behavior.
4. **Full** `tests/test_physical_router.py` only after a logical batch is
   complete, or at a task boundary, or before declaring anything Done.

Record full-suite results in §1 when run. Never mark a task Done from synthetic
tests alone.

---

## Appendix A — Add-Drop Port Convention (locked)

Standard physical add-drop ring; do NOT use the co-propagating shortcut.

```text
top bus:     in   -> th       (left to right)
bottom bus:  drop <- add      (right to left)

physical side:  left = {in, drop},  right = {th, add}
optical role:   input = {in, add},  output = {th, drop}
```

These two classifications differ and must not be collapsed into one left/right
rule. The S-parameter CSV mapping `A/B/C/D -> in/th/add/drop` assumes the standard
physical port locations, so moving `add` left / `drop` right would describe a
different device than the S-table measures — logically wrong even if it improves
route monotonicity. (`core/models.py:default_add_drop_ports`: in=(-8,+4),
th=(+8,+4), add=(+8,-4), drop=(-8,-4).)

## Appendix B — Port Access policy

`PortAccessPlan` / `PortAccessLegality` (`routing/port_access.py`), enforced via
`port_access_conflict` in `routing/grid.py`: owner may enter/leave its own
region; other nets may not block/overlap/T-touch it; crossings inside
port-access regions are illegal; regions reserve room for bend radius + escape.

## Appendix C — Code Organization (module responsibilities)

- `core/models.py` — MRR cell/port dataclasses, add-drop convention, transition
  helpers. `core/topology.py` — topology generation, route steps, state
  assignment. `core/sparams.py` — S-table + CSV mapping.
- `placement/layout.py` — default placement + `wire_y`. `placement/sa.py` — SA.
  `placement/lp.py` — LP minimum-wiring.
- `analysis/cost.py|activity.py|surrogate.py` — IL/SXR metrics, activity scans,
  surrogate.
- `output/visualize.py` — plots. `app/cli.py` — CLI (`pyproject` ->
  `mrr_switch_optimizer.app.cli:main`).
- `routing/types.py` — route dataclasses, `RoutingRules`, `RoutingWindow`,
  `OwnedSegment`, results. `routing/geometry.py` — Manhattan primitives.
  `routing/port_access.py` — side/role, stubs, escapes, plan, legality.
  `routing/route_grid.py` — `RouteGrid` occupancy. `routing/crossing.py` —
  crossing candidate legality. `routing/grid_router.py` — `RouterState`,
  `neighbor_moves`, `HistoryCost`. `routing/grid.py` — A* search, grid, segment
  legality, `_same_net_self_conflict`. `routing/physical.py` — orchestration.
  `routing/drc.py` — final validation. `routing/refinement.py` — small
  canonicalization/scoring only.

Root-level wrappers were removed intentionally; use subpackage paths only.

## Appendix D — Lidar.md alignment

`Lidar.md` has no explicit "self-loop detector", but its architecture is exactly
the prevention path: RouteGrid/occupancy (#2) -> `route_grid.py` built;
orientation-aware A* (#3) -> built, dominance refinement = Task 7; crossing as an
explicit move (#4) -> `legal_crossing_candidate` built, per-pair budget = Tasks
5-6; congestion/history (#5) -> `HistoryCost` built (use rip-up + history bump for
completed-route failures, not refinement); routing windows (#6) -> `RoutingWindow`
built; port-access planning (#1) -> built; module split (#7) -> the subpackage
layout. Same-net knot prevention is the concrete realization of #2/#3/#4 together
(Tasks 2-8), never a refinement repair (#9 / Things-not-to-do).
