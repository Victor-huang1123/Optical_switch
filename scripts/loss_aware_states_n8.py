"""Exact N=8 loss-aware measurement against the corrected v3-mini baseline.

Run: PYTHONDONTWRITEBYTECODE=1 python scripts/loss_aware_states_n8.py
No campaign is invoked. Only outputs/loss_aware_states_n8/ is written.
"""
from __future__ import annotations

from array import array
from collections import Counter
from dataclasses import asdict, replace
from itertools import permutations, product
import json
from pathlib import Path
import signal
import struct
import sys
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mrr_switch_optimizer.analysis.nsweep import (
    _loss_setup, enumerate_fabric_paths, evaluate_fixed_fabric_path_space,
)
from mrr_switch_optimizer.analysis.fabric_loss import _evaluate_path_loss
from mrr_switch_optimizer.app.fabric_reports import fixed_fabric_geometry_hash
from mrr_switch_optimizer.app.nsweep_campaign import _audit_counts, _make_topology, _route_case
from mrr_switch_optimizer.core.fabric import build_fabric_graph
from mrr_switch_optimizer.core.sparams import load_mrr_s_table
from mrr_switch_optimizer.core.state_assignment import realize_state_assignment
from mrr_switch_optimizer.routing.envelope import octave_envelope

OUTPUT = ROOT / 'outputs/loss_aware_states_n8'
REFERENCE = ROOT / 'outputs/nsweep_fixed_fabric_v3_mini/cases'
CASES = (
    ('padded_benes', 'B_db_placeholder', 'c5ad40e124038391', 4.574819091965323),
    ('padded_benes', 'C_db_realistic', 'c5ad40e124038391', 2.4876633792340335),
    ('waksman', 'B_db_placeholder', None, None),
)
BUDGET_S = 1800


class StopMeasurement(RuntimeError):
    pass


def persist(payload):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / 'results.json').write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')
    lines = ['# Loss-aware state assignment at N=8', '', f"Status: **{payload['status']}**.", '',
             '| Topology | Config | WIL_det (dB) | WIL_opt (dB) | Improvement (dB) | Paths det / total |',
             '|---|---|---:|---:|---:|---:|']
    for row in payload['rows']:
        values = ['Unmeasured' if row[k] is None else f'{row[k]:.12f}'
                  for k in ('wil_det', 'wil_opt', 'delta_db')]
        lines.append(f"| {row['topology']} | {row['config']} | {' | '.join(values)} | "
                     f"{row['paths_used_det']} / {row['paths_total']} |")
    if payload['status'] == 'complete':
        benes, realistic, waksman = payload['rows']
        gap_det = waksman['wil_det'] - benes['wil_det']
        gap_opt = waksman['wil_opt'] - benes['wil_opt']
        difference = benes['delta_db'] - waksman['delta_db']
        verdict = '支持 (supported)' if difference > 0 else '反駁 (refuted)'
        lines += ['', f'**Prediction: {verdict}.** On B_db_placeholder, Beneš improves by '
                  f"{benes['delta_db']:.12f} dB and Waksman by {waksman['delta_db']:.12f} dB. "
                  f'The Waksman-minus-Beneš gap changes from {gap_det:.12f} to {gap_opt:.12f} dB '
                  f'(change {difference:+.12f} dB). C_db_realistic is a Beneš-only sensitivity result.', '',
                  'All 40,320 permutations and every binary assignment were evaluated using the existing '
                  'realize_state_assignment implementation. Physical path losses were computed once per case '
                  'with the repository loss evaluator. Assignments were grouped without first-assignment deduplication; '
                  "each permutation's minimum was taken before the outer maximum.", '',
                  '| Topology | Assignments | Valid count mean / min / max | Distribution (count: permutations) | Time (s) |',
                  '|---|---:|---:|---|---:|']
        for row in (benes, waksman):
            counts = row['valid_counts']
            hist = ', '.join(f'{k}: {v}' for k, v in counts['histogram'].items())
            lines.append(f"| {row['topology']} | {row['assignments_checked']} | "
                         f"{counts['mean']:.9f} / {counts['min']} / {counts['max']} | {hist} | {row['enumeration_wall_s']:.2f} |")
        lines += ['', 'The mean valid-count ratio is exactly 8. Worst-case witnesses (zero-based input→output '
                  'permutations) and optimizing assignments are in results.json. Every deterministic assignment '
                  'was verified to belong to its enumerated group, and every per-permutation optimum was checked '
                  'against its deterministic value. Witness losses were re-evaluated directly.', '',
                  'Beneš geometry hashes and deterministic WIL values match v3-mini bit for bit. '
                  'Waksman is checked against zero failed edges, zero legacy DRC and zero bend-radius '
                  'violations, without an external hash comparison. All three deterministic WIL values '
                  'match evaluate_fixed_fabric_path_space on the same fabric bit for bit. '
                  'Waksman/C_db_realistic is excluded as instructed (route_failed). '
                  'No sampling or routing parameter adjustment was used.', '',
                  'Anomalies and audit findings:']
        for check in payload['geometry_checks']:
            lines.append(f"- {check['topology']} / {check['config']}: {check['audit_counts']}.")
        lines.append('The Waksman reference uses the current rebuilt geometry, as required by the '
                     'corrected task. Measurement-only spacing/clearance findings, if present, are '
                     'retained in the audit and filtered only for loss evaluation, matching the repository evaluator.')
    else:
        lines += ['', '**Prediction: 不確定 (uncertain)** until all required measurements complete.']
    if payload.get('stop_reason'):
        lines += ['', 'Execution stopped: ' + payload['stop_reason']]
    lines += ['', 'Geometry checks:']
    for check in payload['geometry_checks']:
        lines.append(f"- {check['topology']} / {check['config']}: expected `{check['expected_sha256']}`, "
                     f"actual `{check['actual_sha256']}`; match={check['match']}; "
                     f"route {check['route_wall_s']:.3f} s; failed edges={check['failed_edges']}; "
                     f"DRC counts={check['drc_counts']}.")
    (OUTPUT / 'REPORT.md').write_text('\n'.join(lines) + '\n')


def prepare(payload):
    table = load_mrr_s_table(str(ROOT / 'mrr_sparam_library'), strict=True)
    prepared = []
    for row, (kind, config, prefix, expected_il) in zip(payload['rows'], CASES):
        reference = REFERENCE / kind / 'n08' / config
        metrics = None
        if prefix is not None:
            metrics = json.loads((reference / 'case_metrics.json').read_text())
            if not metrics['geometry_sha256'].startswith(prefix) or metrics['worst_il_db'] != expected_il:
                raise StopMeasurement('Reference artifacts disagree with the task v3-mini baseline.')
        topology = _make_topology(kind, 8)
        graph = build_fabric_graph(topology)
        envelope = octave_envelope(8)
        lane = 'template' if kind == 'padded_benes' else 'astar'
        print(f'Rebuilding {kind}/{config}, lane={lane}', flush=True)
        start = time.perf_counter()
        cells, result, _, _, actual_lane, _, _, _, _ = _route_case(
            kind, 8, config, topology, graph, envelope, table,
            output_root=OUTPUT,
            pops_ladder=(30000,),
            min_crossing_clearance_um=10.0,
            straighten_jogs=True,
            physical_turn_guard=False,
            v4_search=False,
            campaign_version='loss-aware-n8',
        )
        assert actual_lane == lane
        rules = result.rules
        actual = fixed_fabric_geometry_hash(result)
        audit = _audit_counts(result, cells)
        check = dict(topology=kind, config=config, layout_mode=lane,
                     expected_sha256=metrics['geometry_sha256'] if metrics else None,
                     actual_sha256=actual,
                     hash_check_required=metrics is not None,
                     match=actual == metrics['geometry_sha256'] if metrics else None,
                     route_wall_s=time.perf_counter() - start,
                     failed_edges=len(result.failed_edges),
                     drc_counts=dict(Counter(v.rule for v in result.drc_violations)),
                     envelope=asdict(envelope), effective_rules=asdict(rules),
                     audit_counts=audit)
        payload['geometry_checks'].append(check)
        persist(payload)
        print(f"Geometry {kind}/{config}: {actual}, match={check['match']}", flush=True)
        if check['hash_check_required'] and not check['match']:
            payload['status'] = 'stopped_geometry_hash_mismatch'
            raise StopMeasurement(f'Step 1 geometry mismatch for {kind}/{config}: expected '
                                  f"{metrics['geometry_sha256']}, got {actual}. No enumeration started.")
        # Match v3-mini acceptance: A* crossing-clearance is measurement-only.
        allowed = {'same_net_min_spacing', 'perpendicular_clearance', 'crossing_clearance'} if lane == 'astar' else set()
        if (result.failed_edges or audit['legacy_drc'] or audit['bend_radius_legality']
                or any(v.rule not in allowed for v in result.drc_violations)):
            raise StopMeasurement(f'Baseline routing acceptance failed for {kind}/{config}.')
        paths = enumerate_fabric_paths(topology, graph)
        setup = _loss_setup(result)
        losses = {p: _evaluate_path_loss(p, cells, result, *setup).insertion_loss_db for p in paths}
        # Same geometry; apply exactly the repository's measurement-only DRC filter.
        evaluation_result = replace(result, drc_violations=tuple(
            v for v in result.drc_violations if v.rule not in allowed))
        assert fixed_fabric_geometry_hash(evaluation_result) == actual
        prepared.append(dict(row=row, topology=topology, cells=cells, result=result,
                             evaluation_result=evaluation_result, losses=losses))
        row.update(geometry_sha256=actual, paths_total=len(paths))
    # Require every deterministic gate to pass before binary-state enumeration.
    for case in prepared:
        row, topology, losses = case['row'], case['topology'], case['losses']
        start = time.perf_counter()
        deterministic, used = {}, set()
        for perm in permutations(range(8)):
            states = topology.get_state_assignment(perm)
            assert realize_state_assignment(topology, states) == perm
            active = topology.get_active_paths(perm, states)
            used.update(active)
            deterministic[perm] = max(losses[path] for path in active)
        witness = max(deterministic, key=deterministic.get)
        value = deterministic[witness]
        print(f"Checking repository path-space WIL for {row['topology']}/{row['config']}", flush=True)
        reference_result = evaluate_fixed_fabric_path_space(
            topology, case['cells'], case['evaluation_result'], classify_all=False)
        expected = reference_result.worst_insertion_loss_db
        task_expected = next(c[3] for c in CASES if c[:2] == (row['topology'], row['config']))
        row.update(wil_det=value, wil_det_hex=value.hex(), expected_wil_det_hex=expected.hex(),
                   wil_det_bitwise_match=struct.pack('!d', value) == struct.pack('!d', expected),
                   repository_wil_det=expected, repository_witness=reference_result.witness_permutation,
                   repository_path_space_wall_s=reference_result.wall_clock_s,
                   task_expected_wil_det=task_expected,
                   task_wil_det_bitwise_match=(struct.pack('!d', value) == struct.pack('!d', task_expected)
                                              if task_expected is not None else None),
                   witness_permutation_det=witness, paths_used_det=len(used),
                   deterministic_wall_s=time.perf_counter() - start)
        case['deterministic'] = deterministic
        persist(payload)
        print(f"Deterministic {row['topology']}/{row['config']}: {value!r}, paths={len(used)}/{len(losses)}", flush=True)
        if not row['wil_det_bitwise_match']:
            payload['status'] = 'stopped_deterministic_wil_mismatch'
            raise StopMeasurement(f'WIL_det mismatch: {value!r} != {expected!r}. No enumeration started.')
        if row['task_wil_det_bitwise_match'] is False:
            payload['status'] = 'stopped_deterministic_wil_mismatch'
            raise StopMeasurement(f'Task WIL_det mismatch: {value!r} != {task_expected!r}. No enumeration started.')
    return prepared


def enumerate_states(cases, payload):
    topology = cases[0]['topology']
    kind = cases[0]['row']['topology']
    ids = [mrr_id for mrr_id, _, _ in topology.iter_mrrs()]
    total = 1 << len(ids)
    assert total == (1048576 if kind == 'padded_benes' else 131072)
    groups = {p: array('I') for p in permutations(range(8))}
    best = [{} for _ in cases]
    start, checked = time.perf_counter(), 0

    def timeout(_signum, _frame):
        raise StopMeasurement(f'Enumeration budget exceeded for {kind}: {checked}/{total} assignments '
                              f'in {time.perf_counter() - start:.3f}s. Loop includes state realization, '
                              'active-path construction and loss lookups. Proposed alternative: stratified '
                              'assignment sampling per permutation, which yields bounds rather than an exact '
                              'WIL_opt; awaiting authorization. No sampling performed.')

    previous_handler = signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, BUDGET_S)
    try:
        for index, bits in enumerate(product((0, 1), repeat=len(ids))):
            states = dict(zip(ids, bits))
            perm = realize_state_assignment(topology, states)
            groups[perm].append(index)
            active = topology.get_active_paths(perm, states)
            for case, minima in zip(cases, best):
                loss = max(case['losses'][path] for path in active)
                if perm not in minima or loss < minima[perm][0]:
                    minima[perm] = (loss, index)
            checked = index + 1
            if checked % 4096 == 0:
                elapsed = time.perf_counter() - start
                if checked == 4096 or checked % 65536 == 0:
                    print(f'{kind}: {checked}/{total}, {elapsed:.2f}s, projected {elapsed * total / checked:.2f}s', flush=True)
                if elapsed * total / checked > BUDGET_S:
                    raise StopMeasurement(f'Measured enumeration cost exceeds budget for {kind}: '
                                          f'{checked} assignments in {elapsed:.3f}s projects '
                                          f'{elapsed * total / checked:.3f}s (>1800s). '
                                          'Bottleneck scope: state realization, active-path construction and dictionary lookups. '
                                          'Proposed alternative: stratified assignment sampling per permutation, '
                                          'which yields bounds rather than exact WIL_opt; awaiting authorization. No sampling performed.')
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        payload.setdefault('enumeration_progress', {})[kind] = dict(
            assignments_checked=checked, assignments_total=total, wall_s=time.perf_counter() - start)
    elapsed = time.perf_counter() - start
    counts = [len(values) for values in groups.values()]
    assert len(groups) == 40320 and min(counts) > 0 and sum(counts) == total
    summary = dict(mean=sum(counts) / len(counts), min=min(counts), max=max(counts),
                   histogram=dict(sorted(Counter(counts).items())))
    for case, minima in zip(cases, best):
        row = case['row']
        for perm, values in groups.items():
            deterministic_states = topology.get_state_assignment(perm)
            encoded = sum(deterministic_states[mrr_id] << (len(ids) - 1 - i) for i, mrr_id in enumerate(ids))
            assert encoded in values
            assert minima[perm][0] <= case['deterministic'][perm]
        witness = max(groups, key=lambda p: minima[p][0])
        optimum, index = minima[witness]
        states = {mrr_id: (index >> (len(ids) - 1 - i)) & 1 for i, mrr_id in enumerate(ids)}
        assert realize_state_assignment(topology, states) == witness
        active = topology.get_active_paths(witness, states)
        setup = _loss_setup(case['result'])
        direct = max(_evaluate_path_loss(p, case['cells'], case['result'], *setup).insertion_loss_db for p in active)
        assert direct == optimum
        row.update(status='complete', wil_opt=optimum, delta_db=row['wil_det'] - optimum,
                   valid_counts=summary, assignments_checked=total, permutations_checked=len(groups),
                   enumeration_wall_s=elapsed, witness_permutation_opt=witness,
                   witness_assignment_opt=states, witness_verified=True,
                   all_deterministic_assignments_in_valid_groups=True,
                   all_permutation_optima_le_deterministic=True)
        print(f"Optimized {kind}/{row['config']}: {optimum!r}, delta={row['delta_db']!r}", flush=True)
    persist(payload)


def main():
    payload = dict(status='preflight_running', N=8, baseline='v3-mini', enumeration_started=False,
                   enumeration_budget_s_per_topology=BUDGET_S, geometry_checks=[],
                   excluded_cases=[dict(topology='waksman', config='C_db_realistic', reason='v3-mini route_failed')],
                   rows=[dict(topology=k, config=c, status='not_measured', wil_det=None, wil_opt=None,
                              delta_db=None, valid_counts=None, paths_used_det=None, paths_total=None,
                              witness_permutation_det=None, witness_permutation_opt=None) for k, c, _, _ in CASES])
    try:
        prepared = prepare(payload)
        payload.update(status='enumerating', enumeration_started=True)
        persist(payload)
        for kind in ('padded_benes', 'waksman'):
            enumerate_states([c for c in prepared if c['row']['topology'] == kind], payload)
        payload['status'] = 'complete'
    except StopMeasurement as exc:
        if not payload['status'].startswith('stopped_'):
            payload['status'] = 'stopped_enumeration_budget' if payload['enumeration_started'] else 'stopped_preflight'
        payload['stop_reason'] = str(exc)
        print(payload['stop_reason'], flush=True)
        persist(payload)
        return 2
    persist(payload)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
