"""Exact N=8 assignment oracle on one routed Waksman fabric.

Run from the repository: PYTHONDONTWRITEBYTECODE=1 python -u \
    experiments/n8_waksman_state_assignment_study.py
Bit i (least significant first in printed vectors) is iter_mrrs()[i].
No topology, routing, loss implementation, or protected source file is edited.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, replace
from itertools import permutations
from math import factorial
from pathlib import Path
from statistics import mean, median
from unittest.mock import patch
import csv
import hashlib
import json
import sys
import time
import traceback

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "results/n8_state_assignment"
TOL = 1e-10

import numpy as np
from mrr_switch_optimizer.core import state_assignment as assignment
from mrr_switch_optimizer.core.topology import WaksmanTopology
from mrr_switch_optimizer.core.fabric import build_fabric_graph
from mrr_switch_optimizer.core.sparams import load_mrr_s_table
from mrr_switch_optimizer.analysis.fabric_loss import _evaluate_path_loss, _active_edge_ids
from mrr_switch_optimizer.analysis.nsweep import (
    _loss_setup, enumerate_fabric_paths, _init_exhaustive_worker,
    _evaluate_prefix_chunk,
)
from mrr_switch_optimizer.app import nsweep_campaign as campaign
from mrr_switch_optimizer.app.fabric_reports import fixed_fabric_geometry_hash
from mrr_switch_optimizer.routing.envelope import octave_envelope


class CorrectnessFailure(AssertionError):
    """A failed required gate; never retried or bypassed."""


def require(condition, message, **diagnostic):
    if not condition:
        raise CorrectnessFailure(json.dumps(dict(assertion=message, **diagnostic), default=str))


def write_json(name, value):
    (OUTPUT / name).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_csv(name, rows, fields=None):
    with (OUTPUT / name).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def protected_hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("mrr_switch_optimizer", "tests")
            for p in sorted((ROOT / folder).rglob("*")) if p.is_file()}


def gf2_observation(variables, fixed, constraints):
    """Analyze the supplied equations without influencing the real solver."""
    adjacency = {v: set() for v in variables}
    for a, b, _ in constraints:
        adjacency[a].add(b)
        adjacency[b].add(a)
    seen, components, anchored = set(), 0, 0
    for v in variables:
        if v in seen:
            continue
        component, stack = set(), [v]
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency[current] - component)
        seen.update(component)
        components += 1
        anchored += bool(component.intersection(fixed))
    index = {v: i for i, v in enumerate(variables)}
    equations = [((1 << index[a]) ^ (1 << index[b]), parity)
                 for a, b, parity in constraints]
    equations.extend((1 << index[v], value) for v, value in fixed.items())
    pivots = {}
    for bits, rhs in equations:
        while bits:
            pivot = bits.bit_length() - 1
            if pivot not in pivots:
                pivots[pivot] = bits, rhs
                break
            old_bits, old_rhs = pivots[pivot]
            bits ^= old_bits
            rhs ^= old_rhs
        require(bits != 0 or rhs == 0, "instrumented GF(2) equations are consistent",
                variables=variables, fixed=fixed, constraints=constraints)
    nullity = len(variables) - len(pivots)
    require(nullity == components - anchored, "GF(2) nullity agrees with graph freedom")
    return dict(num_variables=len(variables), num_xor_constraints=len(constraints),
                num_fixed_constraints=len(fixed), num_components=components,
                num_anchored_components=anchored, num_unanchored_components=components-anchored,
                estimated_local_binary_freedom=components-anchored,
                local_solution_count=1 << nullity, rank=len(pivots), nullity=nullity)


def instrument(topology, perms, baseline, state_mask, groups):
    """Observe Python call frames; no monkeypatch or alternative state solver."""
    recursive_code = assignment._assign_waksman_recursive.__code__
    solver_code = assignment._solve_binary_constraints.__code__
    calls, stack = [], []

    def observe(frame, event, _arg):
        if frame.f_code is recursive_code:
            if event == "call":
                local = frame.f_locals
                size = len(local["wires"])
                row = dict(call_index=len(calls), depth=len(stack),
                           stage_offset=local["stage_offset"], wires=list(local["wires"]),
                           subpermutation=local["perm"], subproblem_size=size,
                           base_case=size <= 2)
                if size <= 2:
                    # A size-2 requested subpermutation fixes its switch uniquely.
                    # No parity system is constructed in the implementation.
                    row.update(num_variables=0, num_xor_constraints=0,
                               num_fixed_constraints=0, num_components=0,
                               num_anchored_components=0, num_unanchored_components=0,
                               estimated_local_binary_freedom=0, local_solution_count=1,
                               rank=0, nullity=0)
                calls.append(row)
                stack.append(row)
            elif event == "return":
                stack.pop()
        elif frame.f_code is solver_code and event == "call":
            local = frame.f_locals
            stack[-1].update(gf2_observation(local["variables"], local["fixed"], local["constraints"]))

    comparisons = []
    fields = ["permutation", "call_index", "depth", "stage_offset", "wires", "subpermutation",
              "subproblem_size", "base_case", "num_variables", "num_xor_constraints",
              "num_fixed_constraints", "num_components", "num_anchored_components",
              "num_unanchored_components", "estimated_local_binary_freedom",
              "local_solution_count", "rank", "nullity"]
    with (OUTPUT / "gf2_recursive_calls.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for perm in perms:
            calls.clear()
            stack.clear()
            old_profile = sys.getprofile()
            try:
                sys.setprofile(observe)
                states = topology.get_state_assignment(perm)
            finally:
                sys.setprofile(old_profile)
            require(state_mask(states) == baseline[perm], "instrumentation preserves BFS state", permutation=perm)
            for row in calls:
                writer.writerow(dict(permutation=json.dumps(perm), **row))
            summed_nullity = sum(row["nullity"] for row in calls)
            comparisons.append(dict(permutation=json.dumps(perm),
                                    root_nullity=calls[0]["nullity"],
                                    bfs_trace_sum_local_nullity=summed_nullity,
                                    naive_product_local_counts=1 << summed_nullity,
                                    exact_num_realizations=len(groups[perm]),
                                    naive_product_matches=(1 << summed_nullity) == len(groups[perm])))
    write_csv("gf2_permutation_summary.csv", comparisons)
    return dict(num_recursive_calls=len(perms) * len(calls),
                all_instrumented_states_match_baseline=True,
                naive_product_matches=sum(row["naive_product_matches"] for row in comparisons),
                naive_product_mismatches=sum(not row["naive_product_matches"] for row in comparisons),
                root_nullity_histogram=dict(Counter(row["root_nullity"] for row in comparisons)),
                sum_local_nullity_histogram=dict(Counter(row["bfs_trace_sum_local_nullity"] for row in comparisons)),
                example_mismatches=[row for row in comparisons if not row["naive_product_matches"]][:10],
                note="Local ranks include fixed-value equations. Base cases construct no parity variables. "
                     "The product along the one BFS recursion trace is only a diagnostic, not a global count.")


def run():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    source_before = protected_hashes()
    timings = {}
    summary = dict(status="running", tolerance_db=TOL, runtime_seconds=timings)
    write_json("source_hashes_before.json", source_before)
    current_section = "1–2: setup"
    route_calls = 0
    try:
        topology = WaksmanTopology(8, strategy=assignment.WaksmanStrategy(), verify_rnb=False)
        ids = [mrr for mrr, _, _ in topology.iter_mrrs()]
        n, m = topology.N_logical, topology.n_MRR
        require(len(set(ids)) == len(ids) == m, "unique MRR IDs cover actual M")
        total = 1 << m
        perms = list(permutations(range(n)))
        print(f"Sections 1–2: N={n}, M={m}, 2^M={total}, N!={factorial(n)}", flush=True)
        if m != 17:
            print(f"WARNING: expected M=17, actual M={m}; continuing with actual M.", flush=True)
        summary.update(N=n, M=m, num_states=total, num_permutations=len(perms),
                       mrr_bit_order=ids, config="B_db_placeholder",
                       state_vector_order="bit i / vector position i = mrr_bit_order[i]; 0=BAR, 1=CROSS",
                       relative_improvement_definition="100 * (WIL_BFS - WIL_OPT) / WIL_BFS",
                       optimum_tie_policy="strict minimum numeric WIL; then fewer CROSS, lower total IL, "
                       "lexicographic state vector. num_optimal_states counts WIL equality within 1e-10 dB; "
                       "num_exact_optimal_states records strict numeric equality.")

        def state_dict(mask):
            return {mrr: (mask >> i) & 1 for i, mrr in enumerate(ids)}

        def state_mask(states):
            require(set(states) == set(ids) and all(value in (0, 1) for value in states.values()),
                    "complete binary switch state", states=states)
            return sum(states[mrr] << i for i, mrr in enumerate(ids))

        def bits(mask):
            return "".join(str((mask >> i) & 1) for i in range(m))

        current_section = "3–4: exact enumeration and coverage"
        begin = time.perf_counter()
        groups = defaultdict(list)
        state_permutations = []
        for mask in range(total):
            perm = assignment.realize_state_assignment(topology, state_dict(mask))
            require(len(perm) == n and sorted(perm) == list(range(n)),
                    "every state realizes exactly one bijective permutation", state_id=mask, permutation=perm)
            groups[perm].append(mask)
            state_permutations.append(perm)
        require(len(state_permutations) == sum(map(len, groups.values())) == total,
                "total enumerated states == 2^M", expected=total, actual=len(state_permutations))
        missing = [perm for perm in perms if perm not in groups]
        summary.update(num_total_states=len(state_permutations),
                       num_distinct_permutations_realized=len(groups),
                       num_missing_permutations=len(missing), missing_permutations=missing,
                       coverage_complete=not missing)
        write_json("summary.json", summary)
        require(not missing and len(groups) == factorial(n), "all N! permutations covered", missing_permutations=missing)
        counts = [len(groups[perm]) for perm in perms]
        summary.update(min_realizations=min(counts), max_realizations=max(counts),
                       mean_realizations=mean(counts), median_realizations=median(counts),
                       realization_multiplicity_distribution=dict(sorted(Counter(counts).items())),
                       num_permutations_with_multiple_assignments=sum(v > 1 for v in counts),
                       fraction_with_multiple_assignments=mean(v > 1 for v in counts))
        timings["enumeration_and_coverage"] = time.perf_counter() - begin
        print(f"Sections 3–4: all {total} states enumerated once; coverage {len(groups)}/{len(perms)}; "
              f"multiplicity {summary['realization_multiplicity_distribution']}", flush=True)

        current_section = "5: official BFS baseline"
        begin = time.perf_counter()
        baseline = {}
        for perm in perms:
            states = topology.get_state_assignment(perm)
            realized = assignment.realize_state_assignment(topology, states)
            mask = state_mask(states)
            require(realized == perm, "BFS state realizes requested permutation",
                    permutation=perm, realized=realized, state_id=mask, states=states)
            require(mask in groups[perm], "BFS state exists in exact LUT",
                    permutation=perm, state_id=mask, feasible_states=groups[perm], states=states)
            baseline[perm] = mask
        timings["baseline_verification"] = time.perf_counter() - begin
        summary["baseline_assignments_verified"] = len(baseline)
        write_json("summary.json", summary)
        print(f"Section 5: all {len(baseline)} official BFS assignments verified.", flush=True)

        current_section = "6: one fixed fabric route"
        begin = time.perf_counter()
        graph = build_fabric_graph(topology)
        table = load_mrr_s_table(str(ROOT / "mrr_sparam_library"), strict=True)
        original_route = campaign.route_fixed_fabric

        def route_once(*args, **kwargs):
            nonlocal route_calls
            route_calls += 1
            require(route_calls == 1, "fixed fabric is routed at most once", route_calls=route_calls)
            print("Section 6: starting the single fixed-fabric route (may take about 16 minutes).", flush=True)
            return original_route(*args, **kwargs)

        with patch.object(campaign, "route_fixed_fabric", route_once):
            cells, result, route_wall, _, lane, *_ = campaign._route_case(
                "waksman", n, "B_db_placeholder", topology, graph, octave_envelope(n), table,
                output_root=OUTPUT, pops_ladder=(30000,), min_crossing_clearance_um=10.0,
                straighten_jogs=True, physical_turn_guard=False, v4_search=False,
                campaign_version="n8-state-assignment")
        audit = campaign._audit_counts(result, cells)
        geometry_hash = fixed_fabric_geometry_hash(result)
        summary["geometry"] = dict(sha256=geometry_hash, route_calls_this_run=route_calls,
                                   loaded_cached_fabric=route_calls == 0,
                                   original_route_wall_seconds=route_wall,
                                   failed_edges=len(result.failed_edges), audit_counts=audit,
                                   drc_counts=dict(Counter(v.rule for v in result.drc_violations)),
                                   effective_rules=asdict(result.rules), layout_mode=lane)
        write_json("summary.json", summary)
        require(not result.failed_edges and audit["legacy_drc"] == 0 and audit["bend_radius_legality"] == 0,
                "fixed geometry acceptance: no failed edges, legacy DRC, or bend-radius violations",
                geometry=summary["geometry"], failed_edges=[asdict(e) for e in result.failed_edges])
        # The campaign explicitly treats these three audits as measurement-only for A*.
        # Keep original geometry and all audits; apply its exact evaluator admission rule.
        measurement_only = {"same_net_min_spacing", "perpendicular_clearance", "crossing_clearance"}
        evaluation_result = replace(result, drc_violations=tuple(
            v for v in result.drc_violations if v.rule not in measurement_only))
        require(not evaluation_result.drc_violations, "no unaccepted DRC rules remain")
        require(fixed_fabric_geometry_hash(evaluation_result) == geometry_hash,
                "evaluator admission leaves geometry unchanged")
        require(result.rules.crossing_loss_db_per_cross == 0, "B configuration has zero crossing loss")
        timings["route_and_audit"] = time.perf_counter() - begin
        print(f"Section 6: geometry accepted, hash={geometry_hash}; audit={audit}", flush=True)

        current_section = "6: physical loss evaluation on frozen geometry"
        begin = time.perf_counter()
        setup = _loss_setup(result)
        paths = enumerate_fabric_paths(topology, graph)
        path_ids = {path: i for i, path in enumerate(paths)}
        losses = [_evaluate_path_loss(path, cells, result, *setup) for path in paths]
        write_json("path_catalog.json", [dict(path_id=i, path=asdict(path),
                   edge_ids=_active_edge_ids(path, setup[0]), loss=asdict(losses[i]))
                   for i, path in enumerate(paths)])
        path_il = np.array([loss.insertion_loss_db for loss in losses])
        active_ids = np.empty((total, n), dtype=np.int32)
        for mask, perm in enumerate(state_permutations):
            active = topology.get_active_paths(perm, state_dict(mask))
            require(len(active) == n and [p.input_port for p in active] == list(range(n)),
                    "exactly N ordered active paths per state", state_id=mask)
            require(all(path in path_ids for path in active),
                    "all state paths use the fixed fabric", state_id=mask, permutation=perm)
            active_ids[mask] = [path_ids[path] for path in active]
            if (mask + 1) % 32768 == 0:
                print(f"Section 6: {mask + 1}/{total} assignments evaluated on the same fabric.", flush=True)
        all_il = path_il[active_ids]
        wil = np.max(all_il, axis=1)
        total_il = np.sum(all_il, axis=1)
        worst_input = np.argmax(all_il, axis=1)
        np.savez_compressed(OUTPUT / "state_path_losses.npz", path_ids=active_ids, insertion_loss_db=all_il)
        summary.update(evaluated_state_count=total, active_paths_evaluated=total*n,
                       unique_physical_paths=len(paths), loss_evaluator="analysis.fabric_loss._evaluate_path_loss")
        timings["all_state_path_evaluation"] = time.perf_counter() - begin

        current_section = "7–8: exact optimal assignments and ties"
        begin = time.perf_counter()
        rows = []
        for perm in perms:
            candidates, bfs = groups[perm], baseline[perm]
            opt = min(candidates, key=lambda mask: (wil[mask], mask.bit_count(), total_il[mask], bits(mask)))
            delta = float(wil[bfs] - wil[opt])
            require(wil[opt] <= wil[bfs] + TOL, "WIL_OPT <= WIL_BFS + tolerance",
                    permutation=perm, bfs_state=bfs, opt_state=opt,
                    bfs_wil=float(wil[bfs]), opt_wil=float(wil[opt]), tolerance=TOL)
            row = dict(permutation=json.dumps(perm), num_realizations=len(candidates))
            for prefix, mask in (("bfs", bfs), ("opt", opt)):
                worst = int(worst_input[mask])
                row.update({f"{prefix}_state_id": mask, f"{prefix}_state_bits": bits(mask),
                            f"{prefix}_cross_count": mask.bit_count(),
                            f"{prefix}_wil_db": float(wil[mask]), f"{prefix}_total_il_db": float(total_il[mask]),
                            f"{prefix}_worst_input": worst, f"{prefix}_worst_output": perm[worst],
                            f"{prefix}_worst_path_id": int(active_ids[mask, worst])})
            row.update(delta_wil_db=delta,
                       relative_improvement_percent=100 * delta / float(wil[bfs]) if wil[bfs] else 0.0,
                       num_optimal_states=sum(abs(float(wil[s] - wil[opt])) <= TOL for s in candidates),
                       num_exact_optimal_states=int(sum(wil[s] == wil[opt] for s in candidates)),
                       assignment_wil_range_db=float(max(wil[s] for s in candidates) - wil[opt]))
            rows.append(row)
        timings["optimum_selection"] = time.perf_counter() - begin
        print("Sections 7–8: exact minima and ordered tie-breaking complete; all WIL inequalities passed.", flush=True)

        current_section = "9: recursive freedom instrumentation"
        begin = time.perf_counter()
        summary["gf2_instrumentation"] = instrument(topology, perms, baseline, state_mask, groups)
        timings["gf2_instrumentation"] = time.perf_counter() - begin
        print("Section 9: read-only recursion/GF(2) instrumentation complete; baseline states unchanged.", flush=True)

        current_section = "10–14: tables, summaries, plots and cases"
        begin = time.perf_counter()
        write_csv("permutation_summary.csv", rows)
        def lut_rows():
            for mask, perm in enumerate(state_permutations):
                worst = int(worst_input[mask])
                yield dict(state_id=mask, state_bits=bits(mask), permutation=json.dumps(perm),
                           cross_count=mask.bit_count(), wil_db=float(wil[mask]), worst_input=worst,
                           worst_output=perm[worst], worst_path_id=int(active_ids[mask, worst]),
                           total_il_db=float(total_il[mask]))
        write_csv("state_lut.csv", lut_rows(), ["state_id", "state_bits", "permutation", "cross_count",
                  "wil_db", "worst_input", "worst_output", "worst_path_id", "total_il_db"])
        from n8_state_assignment_report import summarize_and_plot, write_report
        summarize_and_plot(summary, rows, all_il, active_ids, paths, losses, OUTPUT, TOL)
        timings["deliverable_generation"] = time.perf_counter() - begin

        current_section = "15: reference exhaustive evaluator agreement"
        begin = time.perf_counter()
        _init_exhaustive_worker(topology, cells, evaluation_result)
        crosschecks = []
        # Three exact prefix chunks: 3 * 6! = 2160 permutations, 17280 paths.
        for prefix in ((0, 1), (3, 7), (7, 6)):
            reference = _evaluate_prefix_chunk(prefix)
            selected = [row for row in rows if tuple(json.loads(row["permutation"])[:2]) == prefix]
            ours = min(selected, key=lambda row: (-row["bfs_wil_db"], json.loads(row["permutation"]),
                                                 row["bfs_worst_input"]))
            value, perm, path, loss = reference["worst"]
            require(not reference["assignment_failures"] and not reference["fabric_path_failures"],
                    "existing exhaustive evaluator coverage", prefix=prefix, reference=reference)
            require(abs(value - ours["bfs_wil_db"]) <= TOL and tuple(json.loads(ours["permutation"])) == perm
                    and path.input_port == ours["bfs_worst_input"],
                    "existing exhaustive N! evaluator agrees on loss and witness",
                    prefix=prefix, reference_value=value, reference_perm=perm, ours=ours)
            crosschecks.append(dict(prefix=prefix, permutations_checked=reference["checked"],
                                    paths_checked=reference["paths_checked"], reference_wil_db=value,
                                    cached_wil_db=ours["bfs_wil_db"], absolute_difference_db=abs(value-ours["bfs_wil_db"]),
                                    witness_permutation=perm, witness_input=path.input_port))
        summary["reference_evaluator_crosschecks"] = crosschecks
        summary["correctness_assertions"] = dict(
            total_states_equal_2_power_M=True, each_state_has_one_bijection=True,
            full_factorial_coverage=True, all_bfs_realize_requested_permutation=True,
            all_bfs_in_exact_lut=True, all_opt_wil_le_bfs_plus_tolerance=True,
            reference_exhaustive_evaluator_agrees=True,
            all_state_evaluations_share_one_geometry=True)
        require(fixed_fabric_geometry_hash(result) == geometry_hash, "geometry unchanged after all evaluations")
        source_after = protected_hashes()
        write_json("source_hashes_after.json", source_after)
        require(source_before == source_after, "protected directories untouched",
                changed=[name for name in source_before.keys() | source_after.keys()
                         if source_before.get(name) != source_after.get(name)])
        timings["reference_crosscheck"] = time.perf_counter() - begin
        timings["total"] = time.perf_counter() - started
        summary.update(status="complete", protected_directories_unchanged=True)
        write_json("summary.json", summary)
        write_report(summary, rows, OUTPUT)
        print(f"Sections 15–18: COMPLETE in {timings['total']:.2f} s; "
              f"BFS optimal {100*summary['fraction_bfs_optimal']:.6f}%; "
              f"mean/max delta {summary['mean_delta_wil_db']:.12g}/{summary['max_delta_wil_db']:.12g} dB.", flush=True)
    except Exception as exc:
        timings["elapsed_until_stop"] = time.perf_counter() - started
        summary.update(status="stopped", failed_section=current_section,
                       diagnostic=str(exc), traceback=traceback.format_exc(), route_calls_this_run=route_calls)
        write_json("summary.json", summary)
        write_json("failure_diagnostic.json", summary)
        (OUTPUT / "REPORT.md").write_text(
            "# N=8 Waksman State-Assignment Optimization Study\n\n"
            f"Stopped in **{current_section}**. No retry or workaround was attempted.\n\n"
            f"```text\n{exc}\n```\n\nSee failure_diagnostic.json for the full diagnostic.\n")
        raise


if __name__ == "__main__":
    run()
