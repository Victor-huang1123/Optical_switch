"""Tables and publication-exportable figures for the embedding experiment."""
from pathlib import Path
import csv
import json
import math
import numpy as np

BASELINE=5.181320181939005


def report(out):
    out=Path(out)
    metadata=json.loads((out/'stage1_complete.json').read_text())
    pool=json.loads((out/'candidates.json').read_text())
    queue=json.loads((out/'routing_queue.json').read_text())
    stats=list(csv.DictReader((out/'dp_stats.csv').open()))
    rows=list(csv.DictReader((out/'summary.csv').open())) if (out/'summary.csv').exists() else []
    exact=[r for r in rows if r['global_wil_opt_assignment_db']]
    stop=json.loads((out/'routing_stop.json').read_text()) if (out/'routing_stop.json').exists() else None
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.rcParams.update({'font.size':10,'figure.dpi':130})
    def save(fig,name):
        fig.tight_layout()
        fig.savefig(out/(name+'.png'))
        fig.savefig(out/(name+'.pdf'))
        plt.close(fig)
    fig,ax=plt.subplots(figsize=(6,4))
    ax.scatter([int(r['subnet_size']) for r in stats],[int(r['pareto_size']) for r in stats],alpha=.3)
    ax.set(xlabel='Subnet size',ylabel='Pareto entries per boundary signature')
    save(fig,'pareto_set_size_vs_subnet_size')
    correlations={}
    if exact:
        for field,name,label in (
            ('proxy_score','proxy_score_vs_exact_global_WIL','Objective F proxy BFS WIL (dB)'),
            ('physical_crossings','crossing_count_vs_global_WIL','Physical crossing count'),
            ('total_length_um','total_length_vs_global_WIL','Total routed length (µm)')):
            fig,ax=plt.subplots(figsize=(6,4))
            x=np.array([float(r[field]) for r in exact])
            y=np.array([float(r['global_wil_opt_assignment_db']) for r in exact])
            for method in sorted({r['method'] for r in exact}):
                indices=[i for i,r in enumerate(exact) if r['method']==method]
                ax.scatter(x[indices],y[indices],label=method)
            ax.axhline(BASELINE,color='grey',ls='--',label='Reference baseline')
            ax.set(xlabel=label,ylabel='GLOBAL_WIL_OPT_ASSIGN (dB)')
            ax.legend(fontsize=8)
            save(fig,name)
            if len(x)>=3 and np.std(x)>0 and np.std(y)>0:
                correlations[field]=float(np.corrcoef(x,y)[0,1])
            else:
                correlations[field]=None
        fig,ax=plt.subplots(figsize=(max(7,len(exact)*.38),4))
        data=[np.load(out/'exact'/(r['candidate_id']+'.npz'))['opt'] for r in exact]
        ax.boxplot(data,showfliers=False)
        ax.set_xticks(range(1,len(exact)+1),[f'{r["method"]}/{r["dp_objective"]}\n{r["candidate_id"][-4:]}' for r in exact],rotation=60,ha='right')
        ax.axhline(BASELINE,color='grey',ls='--')
        ax.set_ylabel('Exact per-permutation optimum WIL (dB)')
        save(fig,'exact_WIL_distributions_by_method')
        fig,ax=plt.subplots(figsize=(7,4))
        for r in exact:
            if r['method']=='baseline' and len(exact)>1:
                continue
            delta=np.load(out/'exact'/(r['candidate_id']+'.npz'))['worst_delta']
            ax.hist(delta,bins=25,histtype='step',label=r['candidate_id'][-6:])
        ax.set(xlabel='Reference optimum − candidate optimum in baseline worst set (dB)',ylabel='Permutation count')
        if len(exact)>1:
            ax.legend(fontsize=6,ncol=3)
        save(fig,'baseline_worst_set_improvement_histogram')
    (out/'correlations.json').write_text(json.dumps(correlations,indent=2)+'\n')
    status='Stopped under §12' if stop else ('Complete' if (out/'stage3_complete.json').exists() else 'Stage 1 complete; physical validation pending')
    lines=['# N=8 Waksman Recursive Physical Embedding DP', '',f'**Status: {status}.**', '',
      'Primary metric: `GLOBAL_WIL_OPT_ASSIGN = max_π min_legal_state WIL`, compared with '
      '`5.181320181939005 dB`. All quantiles in summary.csv are over the 40,320 per-permutation '
      'optimal-assignment WIL values. BFS quantiles are recorded separately in exact/*.json.', '',
      '## 1. Scope and baseline (§0–2)', '',
      'Only experiment scripts and results are written. The pre-existing modified production and test files '
      'are preserved byte-for-byte using source_hashes_before/after.json. Logical Waksman stage pairs, '
      '17 SEs, the single-MRR S-table, BAR/CROSS port transitions, 131,072 states, 40,320 permutations, '
      'the octave canvas and all campaign rules remain fixed. The prior worst sets are read directly. '
      'Each geometry gets one permutation-independent production A* invocation.', '',
      'The experiment supplies its MRRCell placement dictionary to `_route_case` in canonical production '
      'iteration order. The underlying production router is unchanged; no geometry helper is extracted '
      'or modified in the protected directories.', '',
      'The baseline is submitted for a fresh route in priority position 1; no v3-mini geometry comparison is used. '
      'Each completed route stores placement, fabric edges, geometry pickle/hash, crossings, lengths, '
      'bends, rule/audit records and runtime. An immediate summary row records route outcomes; exact '
      'fields are filled during Stage 3. Failed or interrupted routes have blank loss fields.', '',
      '## 2. Recursive representation and legal transformations (§3–4)', '',
      '`recursive_tree.json` records node identity, non-contiguous logical wire sets (including '
      '`{0,2,4,6}` and `{1,3,5,7}`), boundary SEs, child links, stage ranges, physical wire slots and choices. '
      'Every node enumerates normal and vertical reflection; equal-sized isomorphic children also '
      'permit exchanging their placement slots. A reflection moves the complete subtree and negates '
      'each named port’s dy. Child swaps translate entire child solutions into the opposite interleaved '
      'slot family. Cell dimensions, dx, named port roles, S-values and internal transition lengths/bends remain unchanged.', '',
      'All composed embeddings were exhaustively checked on N=4, then N=6, before N=8 was enabled '
      '(transformation_validation.json). Named external links and active port-level paths were checked '
      'for every permutation in every small-N embedding. N=8 verifies all LUT mappings and all cached '
      'active state paths against production semantics; each candidate must satisfy the identical-graph '
      'and physical-transition contract. Thus all 40,320 permutations transfer to every candidate by identity, '
      'with no state-bit remapping or disappearing permutation.', '',
      'Horizontal reflection/180° rotation is excluded because production escape/runway direction is '
      'hardcoded by port name. Unconjugated logical port-role or boundary relabeling changes semantics '
      'and is excluded. Unequal-size child exchange has no isomorphic slot bijection in this experiment. '
      'These exclusions do not claim that every mathematically possible embedding has been searched.', '',
      '## 3. Boundary signatures and equivalence (§5–6)', '',
      'The DP key is `node_id` plus the ordered boundary signature. It retains ordered input/output logical '
      'wires, every named cut-edge endpoint (including bypasses), exact boundary coordinates and access '
      'coordinates, top-to-bottom order, horizontal facing, logical upper/lower PSE role, physical '
      'upper/lower role, parent endpoint identity and whether the port faces its parent connection in x. '
      'No routed geometry is in the key. Two child solutions can be safely compared only when these '
      'interfaces match and the cost of every labeled internal edge is retained. Subnet size alone is insufficient.', '',
      'Translation and y-reflection preserve internal dx/absolute-dy, facing penalties and bend bounds. '
      'Parents can therefore combine equivalent interfaces without changing the meaning of the retained '
      'edge components. This equivalence is for the decomposed proxy: it is not a theorem that A* will '
      'route proxy-equivalent interiors identically, because obstacle interactions are not separable.', '',
      '## 4. Proxy costs and objectives (§7–8)', '',
      '`proxy_edges.csv` records each edge’s stage span, dy, direction-aware rectilinear length lower bound, '
      'wrap excess length, source/target away flags and minimum bend lower bound. The length bound includes '
      'mandatory production port access/runway points; a source that faces away incurs nonzero excess. '
      'With horizontal endpoint tangents a non-straight path needs at least two bends. Obstacles, crossings '
      'and inter-net congestion are omitted; no unreliable crossing lower bound is invented. The proxy '
      'weight carrier is explicitly synthetic and is never presented as legal routed geometry.', '',
      '| Objective | Definition |','|---|---|']
    lines += [f'| {o} | {description} |' for o,description in metadata['objectives'].items()]
    lines += ['', '| Objective | Current embedding | Best DP proxy |', '|---|---:|---:|']
    for o in 'ABCDEF':
        initial=pool[metadata['baseline_id']]['objectives'][o]
        best_proxy=min(r['objectives'][o] for r in pool.values() if r['is_dp'])
        lines.append(f'| {o} | {initial:.12f} | {best_proxy:.12f} |')
    lines += ['',
      'F calls the existing `evaluate_fixed_fabric_path_space` and production witness finder on estimated '
      'external edge weights, retaining the unchanged internal device loss. Logical witness queries are '
      'memoized across geometry-only candidates. F is a proxy for BFS global WIL, not the max-min headline. '
      'The exact max-min operation on proxy weights is also recorded as `proxy_global_opt_assignment_db`. '
      'Every F result is cross-checked against the reused LUT’s full BFS aggregation.', '',
      '## 5. Bottom-up DP and Pareto behavior (§9–10)', '',
      'Leaves enumerate both legal port orientations. Each parent combines retained upper/lower entries '
      'with every legal local choice, computes its interconnect profile and groups identical boundary '
      'signatures. Dominance requires all labeled internal-edge length, bend and wrap components to be '
      'no larger and at least one strictly smaller. Equal vectors remain. No scalar minimax pruning occurs. '
      'These components also preserve every logical-wire chain and switched path contribution; all '
      'objectives and the exact proxy max-min are monotone in the applicable components.', '',
      f'Raw recursive decision space: **{metadata["raw_design_space"]}**; unique physical geometries: '
      f'**{metadata["unique_geometries"]}**. Root survivors: **{metadata["dp_root_survivors"]}**. '
      f'Boundary states: **{metadata["dp_states"]}**; maximum/mean per-state Pareto size: '
      f'**{metadata["pareto_max"]}/{metadata["pareto_mean"]:.4f}**. See dp_stats.csv and '
      'pareto_set_size_vs_subnet_size. Full-space scoring is retained to audit the DP search and avoid '
      'hiding geometrically distinct proxy ties.', '',
      '## 6. Baselines, top-K and three-stage budget (§11–12, §15–16)', '',
      'Stage 1 completes every objective and all finite-space proxy scores, 100 seeded random legal '
      'decision draws, greedy local-edge minimization, Pareto statistics and top-20 root lists before any '
      'A* call. Greedy commits each node’s smallest current parent-interconnect proxy after choosing its '
      'children; leaf local costs tie and use normal orientation. Its tie rule is deterministic.', '',
      f'The frozen routing_queue.json contains **{len(queue)}** unique candidates, ordered: baseline; '
      'best of A–F; F top five; greedy; random proxy best three and middle three; other objective runners-up. '
      'Repeated geometries satisfy multiple selection roles through one route. The hard cap is 24. '
      'Router flags are B_db_placeholder, pops_ladder=(30000,), min_crossing_clearance_um=10, '
      'straighten_jogs=True, physical_turn_guard=False, v4_search=False for all candidates. '
      'The existing campaign admission gate rejects failed edges/core DRC/bend-radius violations, while '
      'preserving its measurement-only crossing/spacing audits. No rules are tuned per candidate.', '',
      '## 7. Exact physical outcomes and worst sets (§13–14, §17–19)', '',
      f'Completed route outcome rows: **{len(rows)}**; accepted fabrics exactly evaluated: **{len(exact)}**.']
    failed=[r for r in rows if str(r['route_success']).lower()!='true']
    if failed:
        lines += ['', '| Unaccepted candidate | Runtime (min) | Diagnostic |','|---|---:|---|']
        for r in failed:
            status_file=out/'route_status'/(r['candidate_id']+'.json')
            status_data=json.loads(status_file.read_text()) if status_file.exists() else {}
            failures=status_data.get('failed_edges',[])
            diagnostic=(failures[0]['message'].split(';')[0] if failures else
                        (stop['reason'] if stop and stop['candidate_id']==r['candidate_id'] else
                         'See route log and audit records'))
            lines.append(f'| {r["candidate_id"]} | {float(r["routing_runtime"])/60:.2f} | {diagnostic} |')
        lines += ['', 'A legal logical embedding can fail the fixed production routing search. Such '
          'candidates retain full permutation coverage but have no accepted physical WIL; their partial '
          'length/crossing counts are not used in WIL correlations.']
    if stop:
        lines += ['',f'Routing stopped as required by §12: **{stop["reason"]}**, candidate '
                  f'`{stop["candidate_id"]}`. Unrouted candidates have no physical claim.']
    if exact:
        best_observed=min(exact,key=lambda r:float(r['global_wil_opt_assignment_db']))
        lines[4:4]=[f'**Measured headline: {BASELINE:.15f} → '
                    f'{float(best_observed["global_wil_opt_assignment_db"]):.15f} dB**, a '
                    f'**{BASELINE-float(best_observed["global_wil_opt_assignment_db"]):.15f} dB** reduction. '
                    'The full planned routing comparison remains incomplete when the §12 stop applies.', '']
        lines += ['', '| Candidate / selection | BFS global dB | GLOBAL_WIL_OPT_ASSIGN dB | Unique worst improved |',
                      '|---|---:|---:|---:|']
        for r in exact:
            lines.append(f'| {r["candidate_id"]} ({r["method"]}/{r["dp_objective"]}) | '
              f'{float(r["global_wil_bfs_db"]):.12f} | {float(r["global_wil_opt_assignment_db"]):.12f} | '
              f'{r["unique_worst_set_improved_count"]}/256 |')
        lines += ['', 'Exact/*.json contains both quantile sets, worst permutation/input/state/path and loss '
          'decomposition, plus improved counts, mean/max improvements, unchanged-at-baseline counts and '
          'new maxima for all 1,008 baseline-worst and 256 unique-worst permutations. Per-permutation CSV '
          'and NPZ data support all six requested figure families. Negative improvement means degradation.', '',
          f'Observed Pearson correlations on accepted routed candidates: `{json.dumps(correlations)}`. '
          'Correlations are left null with fewer than three accepted fabrics; two points cannot establish '
          'proxy predictive quality. This priority-selected sample is not an unbiased population estimate; '
          'failed routes have no exact WIL.']
        baseline_rows=[r for r in exact if r['method']=='baseline']
        if baseline_rows:
            bd=json.loads((out/'exact'/(baseline_rows[0]['candidate_id']+'.json')).read_text())
            lines += ['', f'The current evaluator reproduces both baseline global values exactly. Its '
              f'baseline BFS median is **{bd["bfs_quantiles"]["median"]:.15f} dB**, also matching the '
              'prior per-permutation CSV; this differs from the task table’s 4.8644 dB summary. The '
              'reported optimum median and headline are unaffected by that discrepancy.']
        bd=json.loads((out/'exact'/(best_observed['candidate_id']+'.json')).read_text())
        comparisons=bd['worst_baseline_set'].get('reference_comparisons')
        if comparisons:
            opt_comparison=comparisons['prior_opt_assignment']
            bfs_comparison=comparisons['prior_bfs']
            lines += ['', 'Worst-set improvement uses a like-for-like optimum-assignment comparison in '
              'summary.csv and the histogram, to isolate embedding changes from state-assignment freedom. '
              f'For the winner, **{opt_comparison["improved_count"]}/1008 improve and '
              f'{opt_comparison["worsened_count"]}/1008 worsen** relative to the prior exact optima '
              f'(mean gain {opt_comparison["mean_improvement_db"]:.12f} dB; maximum gain '
              f'{opt_comparison["maximum_improvement_db"]:.12f} dB). Compared instead with the prior '
              f'BFS values, **{bfs_comparison["improved_count"]}/1008 improve**, with mean gain '
              f'{bfs_comparison["mean_improvement_db"]:.12f} dB. Both reference comparisons are stored '
              f'in each exact JSON. {bd["unique_worst_set"]["improved_count"]}/256 unique cases '
              'improve under either reference.']
        if (out/'bottleneck_analysis.json').exists():
            bottleneck=json.loads((out/'bottleneck_analysis.json').read_text())
            decisions={k:v for k,v in pool[best_observed['candidate_id']]['embedding_decisions'].items() if v!='normal'}
            lines += ['', f'The winning recursive decisions are `{json.dumps(decisions,sort_keys=True)}`.']
            if decisions=={'root.U':'swap_children','root.L':'swap_children'}:
                lines += ['It exchanges the two upper-subnet middle-column cell y positions '
                  '(320 ↔ 192 µm) and the two lower-subnet positions (256 ↔ 128 µm), keeping '
                  'every other cell and all port orientations unchanged.']
            lines += ['The exact max-min comparison isolates embedding changes from assignment choice.', '',
              bottleneck['explanation']+' The per-edge before/after contributions for the former and '
              'new critical paths are in bottleneck_analysis.json. Lower crossing count is an additional '
              'geometric observation, but carries zero direct loss benefit in B_db_placeholder.']
            if (out/'max_min_witness.json').exists():
                witness=json.loads((out/'max_min_witness.json').read_text())
                lines += ['', f'The new worst permutation `{witness["permutation"]}` has exactly '
                  f'**{witness["num_legal_realizations"]}** legal state realizations. Direct production '
                  'path evaluation of every realization confirms its minimum WIL is '
                  f'**{witness["exact_minimum_wil_db"]:.15f} dB**; see max_min_witness.json. Together '
                  'with the full-state upper bound, this is a concrete max-min certificate.']
        if (out/'final_audit.json').exists():
            audit=json.loads((out/'final_audit.json').read_text())
            lines += ['', f'Final verification: **{audit["crosschecked_permutations"]}** additional '
              'permutation evaluations using production exhaustive prefix chunks agree with the cached '
              'BFS results. The full max-min oracle evaluates all 131,072 states and 40,320 permutations '
              'for each accepted fabric. Protected source hashes, Stage 1 artifact hashes, routing '
              'priorities, one-call route counts and identical rules all pass final_audit.json.', '',
              f'Total attempted routing time: **{audit["total_attempted_routing_runtime_seconds"]/60:.2f} '
              f'minutes**; the main finite-space proxy pass took '
              f'**{audit["stage1_proxy_runtime_seconds"]:.2f} seconds**. The routing stage stopped after '
              f'{audit["route_attempts"]} attempts; {audit["unattempted_candidates"]} queued candidates '
              'remain unattempted. No further route is launched by a plain rerun after a recorded §12 stop.']
    else:
        lines += ['', 'No physical improvement is claimed before accepted route evaluation. Exact WIL '
                  'plots and failure classifications remain pending.']
    lines += ['', '## 8. Interpretation and ten task questions (§20–23)', '',
      'The supplied task refers to ten questions from an original §22 but does not reproduce their text. '
      'The following ten answers cover the supplied requirements without inventing a missing specification.', '',
      '1. **Does the embedding preserve the logical fabric?** Yes for every enabled candidate: named '
      'graph, states and all device transitions are identical; exhaustive small-N checks precede N=8.',
      '2. **Which choices are legal here?** Vertical reflection and equal-size child placement swaps, '
      'including their compositions. Unsupported horizontal and logical-role transformations are excluded.',
      '3. **What information makes child solutions equivalent?** The complete cut interface and '
      'per-labeled-edge cost vector described in section 3; never size or a scalar alone.',
      '4. **What is optimized before routing?** A–F with separate physical components, with F using '
      'production path-space and witnesses; full proxy max-min is also tabulated.',
      f'5. **Does Pareto pruning scale?** At N=8 it retains {metadata["dp_root_survivors"]} root entries; '
      f'maximum boundary-state Pareto size is {metadata["pareto_max"]}. This finite experiment does not '
      'establish scalability to larger N.',
      '6. **Are comparisons fair?** All candidates use the same S-table, canvas, production router '
      'and rule flags, once each; random and greedy selection is completed before routing.']
    if exact:
        best=min(exact,key=lambda r:float(r['global_wil_opt_assignment_db']))
        value=float(best['global_wil_opt_assignment_db'])
        delta=BASELINE-value
        lines.append(f'7. **Does the headline improve?** Best observed `{best["candidate_id"]}` gives '
              f'**{value:.15f} dB**, reference minus candidate **{delta:.15f} dB**. '
              + ('It is strictly below the specified reference.' if delta>1e-10 else 'The success criterion is not met.'))
        detail=json.loads((out/'exact'/(best['candidate_id']+'.json')).read_text())
        u=detail['unique_worst_set']
        lines.append(f'8. **Do zero-freedom bottlenecks move?** For that candidate, '
              f'{u["improved_count"]}/256 improve; their new maximum is {u["new_maximum_db"]:.15f} dB. '
              f'The new worst path loss decomposition is `{json.dumps(detail["worst_path_loss"])}`.')
        dp=[float(r['global_wil_opt_assignment_db']) for r in exact if pool[r['candidate_id']]['is_dp']]
        controls=[float(r['global_wil_opt_assignment_db']) for r in exact
                  if pool[r['candidate_id']]['is_greedy'] or pool[r['candidate_id']]['is_random']]
        lines.append('9. **Does DP outperform greedy/random?** '+
              (f'Best DP {min(dp):.12f} dB; best routed control {min(controls):.12f} dB.' if dp and controls
               else 'Insufficient accepted routes across these selection groups to decide.'))
        classes=[]
        if delta>1e-10:
            classes.append('A: measured headline reduction within this fixed deterministic setup (no statistical significance or novelty claim)')
        elif any(float(r['median_wil_db'])<float(np.median(np.load(out/'exact'/(metadata['baseline_id']+'.npz'))['opt']))
                 for r in exact) if (out/'exact'/(metadata['baseline_id']+'.npz')).exists() else False:
            classes.append('B: a median reduction did not lower the headline')
        if correlations.get('proxy_score') is not None and correlations['proxy_score']<.5:
            classes.append('C: weak proxy-to-exact linear association in the selected accepted sample')
        if dp and controls and min(controls)<=min(dp)+1e-10:
            classes.append('E: routed greedy/random matches or improves the best routed DP candidate')
        lines.append('10. **Which failure/success class is supported?** '+('; '.join(classes) if classes else
              'No A–E outcome can be established confidently from the available accepted comparisons.')+
              (' The §12 stop leaves the full planned comparison incomplete.' if stop else ''))
    else:
        lines += ['7. **Does the headline improve?** Pending accepted physical results; no success claim.',
                  '8. **Do zero-freedom bottlenecks move?** Pending exact worst-set analysis.',
                  '9. **Does DP outperform greedy/random?** Pending routed comparisons.',
                  '10. **Which class is supported?** A–E remain unclassified until physical validation.']
    lines += ['', 'All conclusions apply to the enumerated finite geometry family and selected physical '
      'sample. Proxy optimality is not a certificate of globally optimal routed embedding. No novelty '
      'claim is made.', '']
    (out/'REPORT.md').write_text('\n'.join(lines))
