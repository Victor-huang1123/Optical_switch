"""Tables, figures and report for the exact N=8 experiment; no routing calls."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from pathlib import Path
from statistics import mean, median
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import spearmanr


def save_json(output, name, value):
    (output / name).write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def association(x, y):
    if len(set(x)) <= 1 or len(set(y)) <= 1:
        return dict(pearson=None, spearman=None, reason="At least one variable is constant.")
    return dict(pearson=float(np.corrcoef(x, y)[0, 1]),
                spearman=float(spearmanr(x, y).statistic))


def summarize_and_plot(summary, rows, all_il, active_ids, paths, losses, output, tol):
    # Normalize numeric-library scalar values at the JSON serialization boundary.
    for row in rows:
        for key, value in row.items():
            if isinstance(value, np.generic):
                row[key] = value.item()
    delta = [row["delta_wil_db"] for row in rows]
    relative = [row["relative_improvement_percent"] for row in rows]
    multiplicity = [row["num_realizations"] for row in rows]
    suboptimal = [row for row in rows if row["delta_wil_db"] > tol]
    best = min(rows, key=lambda row: (-row["delta_wil_db"], json.loads(row["permutation"])))
    summary.update(
        num_permutations_bfs_is_optimal=len(rows)-len(suboptimal),
        fraction_bfs_optimal=(len(rows)-len(suboptimal))/len(rows),
        num_permutations_bfs_is_not_optimal=len(suboptimal),
        mean_delta_wil_db=mean(delta), median_delta_wil_db=median(delta), max_delta_wil_db=max(delta),
        mean_delta_wil_db_when_bfs_not_optimal=mean(r["delta_wil_db"] for r in suboptimal) if suboptimal else 0.0,
        mean_relative_improvement_percent=mean(relative), max_relative_improvement_percent=max(relative),
        permutation_with_max_improvement=json.loads(best["permutation"]),
        multiplicity_vs_gain_correlation=association(multiplicity, delta),
        multiplicity_vs_assignment_wil_range_correlation=association(
            multiplicity, [row["assignment_wil_range_db"] for row in rows]),
        num_permutations_with_multiple_wil_optima=sum(row["num_optimal_states"] > 1 for row in rows),
        num_permutations_where_tolerance_changes_optimum_count=sum(
            row["num_optimal_states"] != row["num_exact_optimal_states"] for row in rows),
        all_assignment_wil_range_db=dict(
            mean=mean(row["assignment_wil_range_db"] for row in rows),
            median=median(row["assignment_wil_range_db"] for row in rows),
            maximum=max(row["assignment_wil_range_db"] for row in rows)))

    def detail(row):
        full = dict(row)
        full["permutation"] = json.loads(row["permutation"])
        full["path_loss_comparison"] = []
        for input_port, output_port in enumerate(full["permutation"]):
            comparison = dict(input=input_port, output=output_port)
            for prefix in ("bfs", "opt"):
                mask = row[f"{prefix}_state_id"]
                pid = int(active_ids[mask, input_port])
                comparison[f"{prefix}_path_id"] = pid
                comparison[f"{prefix}_loss"] = asdict(losses[pid])
                comparison[f"{prefix}_path"] = asdict(paths[pid])
            comparison["delta_il_db"] = (comparison["bfs_loss"]["insertion_loss_db"]
                                         - comparison["opt_loss"]["insertion_loss_db"])
            full["path_loss_comparison"].append(comparison)
        for prefix in ("bfs", "opt"):
            pid = row[f"{prefix}_worst_path_id"]
            full[f"{prefix}_worst_path"] = asdict(paths[pid])
        return full

    summary["maximum_improvement_case"] = detail(best)
    cross_stats = Counter()
    for row in suboptimal:
        difference = row["bfs_cross_count"] - row["opt_cross_count"]
        cross_stats["opt_fewer_cross" if difference > 0 else
                    "opt_more_cross" if difference < 0 else "same_cross_count"] += 1
        cross_stats["worst_input_changed" if row["bfs_worst_input"] != row["opt_worst_input"]
                    else "worst_input_unchanged"] += 1
        old_input = row["bfs_worst_input"]
        old_path = paths[int(active_ids[row["bfs_state_id"], old_input])]
        new_path = paths[int(active_ids[row["opt_state_id"], old_input])]
        old_route = [(s.mrr_id, s.in_port, s.out_port) for s in old_path.steps]
        new_route = [(s.mrr_id, s.in_port, s.out_port) for s in new_path.steps]
        cross_stats["bfs_critical_signal_path_changed" if old_route != new_route
                    else "bfs_critical_signal_path_unchanged"] += 1
    summary["suboptimal_bfs_path_and_cross_observations"] = {
        key: cross_stats[key] for key in ("opt_fewer_cross", "opt_more_cross", "same_cross_count",
        "worst_input_changed", "worst_input_unchanged", "bfs_critical_signal_path_changed",
        "bfs_critical_signal_path_unchanged")}
    conditional = []
    for count in sorted(set(multiplicity)):
        subset = [row for row in rows if row["num_realizations"] == count]
        conditional.append(dict(num_realizations=count, num_permutations=len(subset),
                               bfs_optimal_fraction=mean(row["delta_wil_db"] <= tol for row in subset),
                               mean_delta_wil_db=mean(row["delta_wil_db"] for row in subset),
                               max_delta_wil_db=max(row["delta_wil_db"] for row in subset),
                               mean_assignment_wil_range_db=mean(row["assignment_wil_range_db"] for row in subset)))
    summary["conditional_gain_by_multiplicity"] = conditional

    many_threshold = 8
    flat = [row for row in rows if row["num_realizations"] >= many_threshold
            and row["assignment_wil_range_db"] <= tol]
    single = [row for row in rows if row["num_realizations"] == 1]
    cases = dict(
        A_top_20_bfs_regret=[detail(row) for row in sorted(rows, key=lambda r: (-r["delta_wil_db"], r["permutation"]))[:20]],
        B_top_multiplicity=[detail(row) for row in sorted(rows, key=lambda r: (-r["num_realizations"], r["permutation"]))[:20]],
        C_many_assignments_flat_wil=dict(minimum_multiplicity=many_threshold, max_wil_range_db=tol,
                                       num_matching_permutations=len(flat),
                                       examples=[detail(row) for row in sorted(flat, key=lambda r: (-r["num_realizations"], r["permutation"]))[:20]]),
        D_single_assignment=dict(num_matching_permutations=len(single), examples=[detail(row) for row in single[:20]]))
    save_json(output, "interesting_cases.json", cases)
    summary["interesting_case_counts"] = dict(many_assignments_flat_wil=len(flat),
                                              many_assignment_threshold=many_threshold,
                                              single_assignment=len(single))
    case_lines = ["# Representative permutations", "", "All ports are zero-based. State vectors use mrr_bit_order in summary.json.",
                  "Full paths and all eight loss decompositions appear in interesting_cases.json; path IDs resolve in path_catalog.json.", ""]
    categories = [("A. Top 20 BFS regret", cases["A_top_20_bfs_regret"]),
                  ("B. Largest realization multiplicity", cases["B_top_multiplicity"]),
                  (f"C. At least {many_threshold} assignments with WIL range ≤ {tol:g} dB ({len(flat)} cases)",
                   cases["C_many_assignments_flat_wil"]["examples"]),
                  (f"D. Unique assignment ({len(single)} cases)", cases["D_single_assignment"]["examples"])]
    for title, examples in categories:
        case_lines.extend([f"## {title}", ""])
        if not examples:
            case_lines.extend(["No cases satisfy this definition.", ""])
            continue
        case_lines.extend(["| Permutation | Multiplicity | BFS state | OPT state | BFS WIL | OPT WIL | Δ dB | BFS worst I→O / path | OPT worst I→O / path |",
                           "|---|---:|---|---|---:|---:|---:|---|---|"])
        for row in examples:
            case_lines.append(f"| {row['permutation']} | {row['num_realizations']} | `{row['bfs_state_bits']}` | "
                              f"`{row['opt_state_bits']}` | {row['bfs_wil_db']:.9f} | {row['opt_wil_db']:.9f} | "
                              f"{row['delta_wil_db']:.9f} | {row['bfs_worst_input']}→{row['bfs_worst_output']} / "
                              f"{row['bfs_worst_path_id']} | {row['opt_worst_input']}→{row['opt_worst_output']} / {row['opt_worst_path_id']} |")
        case_lines.append("")
    (output / "REPRESENTATIVE_CASES.md").write_text("\n".join(case_lines) + "\n")

    plt.rcParams.update({"figure.dpi": 140, "savefig.dpi": 180, "font.size": 10})

    def save(fig, name):
        fig.tight_layout()
        fig.savefig(output / (name + ".png"))
        fig.savefig(output / (name + ".pdf"))
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    distribution = Counter(multiplicity)
    ax.bar([str(k) for k in sorted(distribution)], [distribution[k] for k in sorted(distribution)], color="#277da1")
    ax.set(xlabel="Legal assignments per permutation", ylabel="Number of permutations",
           title="N=8 Waksman exact realization multiplicity")
    save(fig, "realization_multiplicity_histogram")

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.hist(delta, bins=60, color="#277da1")
    ax.set(xlabel="WIL(BFS) − WIL(OPT) [dB]", ylabel="Number of permutations",
           title="Exact improvement on one fixed B fabric")
    save(fig, "delta_wil_histogram")

    fig, ax = plt.subplots(figsize=(5.7, 5.2))
    bfs, opt = [r["bfs_wil_db"] for r in rows], [r["opt_wil_db"] for r in rows]
    ax.scatter(bfs, opt, s=5, alpha=0.18, color="#277da1", rasterized=True)
    bounds = min(min(bfs), min(opt)), max(max(bfs), max(opt))
    ax.plot(bounds, bounds, "--", color="#d1495b", label="y = x")
    ax.set(xlabel="BFS WIL [dB]", ylabel="Exact OPT WIL [dB]", title="BFS versus optimum")
    ax.legend()
    save(fig, "bfs_vs_opt_wil_scatter")

    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.scatter(multiplicity, delta, s=7, alpha=0.12, color="#277da1", rasterized=True)
    ax.plot([r["num_realizations"] for r in conditional], [r["mean_delta_wil_db"] for r in conditional],
            "o-", color="#d1495b", label="Mean per multiplicity")
    ax.set(xlabel="Legal assignments per permutation", ylabel="WIL(BFS) − WIL(OPT) [dB]",
           title="Combinatorial freedom and physical gain")
    ax.set_xticks(sorted(distribution))
    ax.legend()
    save(fig, "multiplicity_vs_gain_scatter")

    fig, ax = plt.subplots(figsize=(7, 4.5))
    cross_diff = [row["bfs_cross_count"] - row["opt_cross_count"] for row in rows]
    ax.hist(cross_diff, bins=np.arange(min(cross_diff)-0.5, max(cross_diff)+1.5), color="#277da1")
    ax.set(xlabel="CROSS count(BFS) − CROSS count(OPT)", ylabel="Number of permutations",
           title="Switch-count difference after exact WIL optimization")
    save(fig, "cross_count_difference")


def write_report(s, rows, output):
    case = s["maximum_improvement_case"]
    corr = s["multiplicity_vs_gain_correlation"]
    observations = s["suboptimal_bfs_path_and_cross_observations"]
    gf = s["gf2_instrumentation"]
    sections = [
        "# N=8 Waksman State-Assignment Optimization Study", "",
        "## 1. Experimental setup", "",
        f"N={s['N']}; actual MRR/SE count M={s['M']}. One current-code Waksman fabric, B_db_placeholder, "
        "fixed topology, physical port mapping, router and loss coefficients. "
        f"The fabric was routed {s['geometry']['route_calls_this_run']} time(s) in this run "
        f"(cache used: {s['geometry']['loaded_cached_fabric']}); every assignment uses geometry "
        f"`{s['geometry']['sha256']}`. No comparison to an older geometry hash is made.", "",
        "The routing call used pops_ladder=(30000,), min_crossing_clearance_um=10.0, "
        "straighten_jogs=True, physical_turn_guard=False and v4_search=False. "
        "MRR S-parameters were loaded through the existing strict library loader. "
        "The full effective routing/loss rules and protected-file hashes are saved alongside the results.", "",
        f"Propagation loss: {s['geometry']['effective_rules']['prop_loss_db_per_um']} dB/µm; "
        f"bend loss: {s['geometry']['effective_rules']['bend_loss_db_per_bend']} dB/bend; "
        f"crossing loss: {s['geometry']['effective_rules']['crossing_loss_db_per_cross']} dB/crossing. "
        "The evaluator includes both external waveguides and MRR-internal geometry.", "",
        f"Failed edges: {s['geometry']['failed_edges']}; audits: `{s['geometry']['audit_counts']}`. "
        "The existing A* campaign treats same-net spacing, perpendicular clearance and crossing clearance as "
        "measurement-only. Their findings are retained; its identical admission filter is applied only to "
        "the reference evaluator's DRC metadata. Geometry and losses are unchanged. "
        "Legacy DRC and bend-radius violations are required to be zero.", "",
        "Each distinct directed fabric path is evaluated once by the repository's "
        "`analysis.fabric_loss._evaluate_path_loss`, with `_loss_setup` caching edge geometry and crossings. "
        f"There are {s['unique_physical_paths']} unique physical paths and {s['active_paths_evaluated']:,} active-path "
        "lookups across all assignments. The study adds no alternative loss formula.", "",
        "All ports are zero-based. The printed bit vector lists bit 0 first; its MRR order is "
        "`mrr_bit_order` in summary.json. BAR=0, CROSS=1. The official strategy is explicitly pinned to "
        "WaksmanStrategy. The task's 'BFS' label refers to its recursive parity propagation; the current "
        "implementation traverses constraints using a stack (`pop()`), which does not change this baseline definition.", "",
        "## 2. Exact state-space coverage", "",
        f"Enumerated all {s['num_total_states']:,} = 2^{s['M']} states exactly once using "
        "`realize_state_assignment`. Every mapping is a bijection and appears in exactly one inverse-LUT group. "
        f"Realized {s['num_distinct_permutations_realized']:,} distinct permutations; missing permutations: "
        f"{s['num_missing_permutations']}. Coverage is 100%.", "",
        "## 3. Routing multiplicity", "",
        f"Legal assignments per permutation: min {s['min_realizations']}, max {s['max_realizations']}, "
        f"mean {s['mean_realizations']:.9f}, median {s['median_realizations']}. "
        f"{s['num_permutations_with_multiple_assignments']:,} permutations "
        f"({100*s['fraction_with_multiple_assignments']:.6f}%) have multiple assignments.", "",
        "| Legal assignments | Permutations |", "|---:|---:|"]
    sections += [f"| {count} | {number} |" for count, number in s['realization_multiplicity_distribution'].items()]
    sections += ["", "![Multiplicity](realization_multiplicity_histogram.png)", "",
        "## 4. Current BFS baseline", "",
        f"For all {s['baseline_assignments_verified']:,} permutations, the official recursive assignment was checked "
        "both by simulation against the requested permutation and by membership in the exact inverse LUT. "
        "All checks passed. Instrumentation later reproduced each same baseline state.", "",
        "## 5. Exact minimum-WIL assignment", "",
        "All candidate assignments were evaluated once, grouped by their already-known permutation. "
        "Each candidate's WIL is the maximum of its eight active-path losses. "
        "The ordering is numeric WIL, total CROSS count, sum of eight insertion losses, then lexicographic "
        "state vector. The WIL comparison is strict; the tolerance used for optimality classification and "
        f"counting equivalent WIL optima is {s['tolerance_db']:g} dB. Both tolerant and strict optimum "
        "counts are exported, retaining multiple optima.", "",
        f"{s['num_permutations_with_multiple_wil_optima']:,} permutations have multiple minimum-WIL states "
        f"within tolerance. For {s['num_permutations_where_tolerance_changes_optimum_count']:,} permutations, "
        "tolerant and exact floating-point counts differ. Each optimum satisfies WIL_OPT ≤ WIL_BFS + tolerance.", "",
        "## 6. BFS vs optimum", "",
        f"BFS is WIL-optimal for {s['num_permutations_bfs_is_optimal']:,}/{s['num_permutations']:,} "
        f"permutations ({100*s['fraction_bfs_optimal']:.6f}%). It is suboptimal for "
        f"{s['num_permutations_bfs_is_not_optimal']:,} permutations.", "",
        f"Across all permutations, mean / median / maximum improvement is {s['mean_delta_wil_db']:.9f} / "
        f"{s['median_delta_wil_db']:.9f} / {s['max_delta_wil_db']:.9f} dB. "
        f"Conditioned on suboptimal BFS, mean improvement is {s['mean_delta_wil_db_when_bfs_not_optimal']:.9f} dB. "
        f"Mean / maximum relative reduction in the numerical dB value is {s['mean_relative_improvement_percent']:.6f}% / "
        f"{s['max_relative_improvement_percent']:.6f}%, defined as 100 × ΔWIL / WIL_BFS; "
        "this is not a transmitted-power percentage.", "",
        "![Improvement distribution](delta_wil_histogram.png)", "",
        "![BFS versus optimum](bfs_vs_opt_wil_scatter.png)", "",
        f"Multiplicity versus BFS regret: Pearson r={corr['pearson']}, Spearman ρ={corr['spearman']}. "
        "These are descriptive statistics over the complete permutation population; multiplicity is not "
        "assumed to determine physical gain. Candidate WIL range is also recorded separately from BFS regret.", "",
        "| Multiplicity | Permutations | BFS optimal % | Mean gain dB | Max gain dB | Mean candidate WIL range dB |",
        "|---:|---:|---:|---:|---:|---:|"]
    sections += [f"| {r['num_realizations']} | {r['num_permutations']} | {100*r['bfs_optimal_fraction']:.3f} | "
                 f"{r['mean_delta_wil_db']:.9f} | {r['max_delta_wil_db']:.9f} | {r['mean_assignment_wil_range_db']:.9f} |"
                 for r in s['conditional_gain_by_multiplicity']]
    sections += ["", "![Multiplicity versus gain](multiplicity_vs_gain_scatter.png)", "",
        f"Among suboptimal BFS cases, OPT has fewer / equal / more total CROSS MRRs in "
        f"{observations['opt_fewer_cross']} / {observations['same_cross_count']} / {observations['opt_more_cross']} cases. "
        f"The worst input changes in {observations['worst_input_changed']} cases. "
        f"The old BFS critical signal follows a different MRR/port path in "
        f"{observations['bfs_critical_signal_path_changed']} cases. "
        "Thus total CROSS count and critical-signal routing are measured separately; fewer total resonant "
        "switches alone is not assumed to explain a minimax improvement.", "",
        "## 7. GF(2)/constraint freedom observations", "",
        "Read-only Python call profiling observes every recursive call and its actual parity-solver inputs. "
        "The solver runs unmodified. Each non-base call records variables, XOR equations, fixed equations, "
        "connected components, anchored components, free components, GF(2) rank and nullity. "
        "Rank includes the fixed-value equations. Base cases construct no parity system and have zero local "
        "assignment freedom for their fixed subpermutation.", "",
        f"Recorded {gf['num_recursive_calls']:,} calls. Graph freedom and GF(2) nullity agree for every observed "
        "system. Multiplying local solution counts along the single BFS-selected recursion trace matches the "
        f"exact global multiplicity for {gf['naive_product_matches']:,} permutations and differs for "
        f"{gf['naive_product_mismatches']:,}. That product is only a diagnostic: a different local coloring "
        "can change the child subpermutations and their remaining freedom. It is not used to predict or prune "
        "the exact feasible set. The observed multiplicity 5 is also direct evidence that the fixed-permutation "
        "solution set need not be one affine GF(2) space.", "",
        "Full observations are in gf2_recursive_calls.csv and gf2_permutation_summary.csv; mismatch examples "
        "are in summary.json.", "",
        "## 8. Representative cases", "",
        "[REPRESENTATIVE_CASES.md](REPRESENTATIVE_CASES.md) contains the four required categories, including "
        "the top 20 BFS regrets, largest multiplicities, many-assignment flat-WIL cases, and unique-assignment "
        "cases. [interesting_cases.json](interesting_cases.json) includes full witness paths and all eight "
        "path-loss comparisons for every displayed case.", "",
        f"The flat-WIL category uses at least {s['interesting_case_counts']['many_assignment_threshold']} "
        f"assignments and maximum-minus-minimum WIL ≤ {s['tolerance_db']:g} dB; "
        f"{s['interesting_case_counts']['many_assignments_flat_wil']} permutations qualify. "
        f"There are {s['interesting_case_counts']['single_assignment']} unique-assignment permutations.", "",
        f"Maximum improvement permutation: **{case['permutation']}**, multiplicity {case['num_realizations']}.", "",
        f"BFS `{case['bfs_state_bits']}` (state {case['bfs_state_id']}, {case['bfs_cross_count']} CROSS) has WIL "
        f"{case['bfs_wil_db']:.12f} dB, worst input/output {case['bfs_worst_input']}→{case['bfs_worst_output']}. "
        f"OPT `{case['opt_state_bits']}` (state {case['opt_state_id']}, {case['opt_cross_count']} CROSS) has WIL "
        f"{case['opt_wil_db']:.12f} dB, worst input/output {case['opt_worst_input']}→{case['opt_worst_output']}. "
        f"Improvement: **{case['delta_wil_db']:.12f} dB**; equivalent optimum count: {case['num_optimal_states']}.", "",
        "| Input | Output | BFS IL dB | OPT IL dB | Reduction dB | BFS path ID | OPT path ID |",
        "|---:|---:|---:|---:|---:|---:|---:|"]
    sections += [f"| {p['input']} | {p['output']} | {p['bfs_loss']['insertion_loss_db']:.12f} | "
                 f"{p['opt_loss']['insertion_loss_db']:.12f} | {p['delta_il_db']:.12f} | "
                 f"{p['bfs_path_id']} | {p['opt_path_id']} |" for p in case['path_loss_comparison']]
    sections += ["", "Full MRR transitions, fixed edge IDs and loss components resolve through path_catalog.json. "
                 "state_path_losses.npz retains all eight loss values and path IDs for every enumerated state.", "",
                 "## 9. Runtime", "", "| Phase | Wall time (s) |", "|---|---:|"]
    sections += [f"| {phase} | {seconds:.3f} |" for phase, seconds in s['runtime_seconds'].items()]
    checks = s['reference_evaluator_crosschecks']
    sections += ["", f"The existing exhaustive evaluator's exact prefix-chunk routine independently recomputed "
        f"{sum(c['permutations_checked'] for c in checks):,} BFS permutations and "
        f"{sum(c['paths_checked'] for c in checks):,} paths. All three prefix worst losses and full "
        "permutation/input witnesses agree. The maximum numerical difference was "
        f"{max(c['absolute_difference_db'] for c in checks):g} dB. Every required correctness assertion passed. "
        "The hashes of all files under mrr_switch_optimizer/ and tests/ are unchanged.", "",
        "Reproduce with `PYTHONDONTWRITEBYTECODE=1 python -u experiments/n8_waksman_state_assignment_study.py`. "
        "The campaign reloads the routed artifact from this output directory on subsequent runs; "
        "no assignment evaluation routes a fabric.", "",
        "## 10. Conclusion", "",
        f"For this N=8 B-configured geometry, an average permutation has {s['mean_realizations']:.6f} legal "
        f"assignments. BFS is already optimal in {100*s['fraction_bfs_optimal']:.3f}% of cases; "
        f"the remaining cases lose an average {s['mean_delta_wil_db_when_bfs_not_optimal']:.6f} dB, "
        f"with a maximum recoverable gap of {s['max_delta_wil_db']:.6f} dB.", ""]
    if s['max_delta_wil_db'] > s['tolerance_db']:
        sections += ["The measured nonzero gaps establish physical optimization space on this fabric. "
                     "Whether that gap is meaningful for a product depends on its optical link budget. "
                     "Exact enumeration is already small at N=8 and provides the appropriate oracle. "
                     "For larger N, the next useful experiment is recursion-aware GF(2) freedom enumeration "
                     "checked against this oracle. Recursive DP is worth investigating only with a state that "
                     "retains the physical-path information needed for the minimax objective; local freedom "
                     "counts alone are insufficient. These data do not require a MILP as the first next step.", ""]
    else:
        sections += ["No WIL gap above tolerance is observed in this experiment. BFS is sufficient for the "
                     "measured objective here; these results do not motivate DP or MILP on this fabric.", ""]
    sections += ["The conclusions apply to this one current-code geometry and B coefficients, with zero "
                 "crossing loss. They do not establish performance on C_db_realistic or on other sizes and "
                 "layouts. No novelty claim is made.", ""]
    (output / "REPORT.md").write_text("\n".join(sections))
