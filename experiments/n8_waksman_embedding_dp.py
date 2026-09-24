"""Recursive physical Waksman experiment; no production source changes.

Run: PYTHONDONTWRITEBYTECODE=1 python -u experiments/n8_waksman_embedding_dp.py
Stages are checkpointed. Routing is sequential, once per candidate, with a
40-minute watchdog and a durable summary row before starting the next route.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from itertools import permutations, product
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import pickle
import random
import subprocess
import sys
import time
import traceback
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from mrr_switch_optimizer.core.topology import WaksmanTopology, _build_waksman_for_wires
from mrr_switch_optimizer.core.state_assignment import realize_state_assignment
from mrr_switch_optimizer.core.fabric import build_fabric_graph, validate_fabric_graph
from mrr_switch_optimizer.core.sparams import load_mrr_s_table
from mrr_switch_optimizer.routing.envelope import octave_envelope, build_envelope_cells
from mrr_switch_optimizer.routing.port_access import _port_route_point, _mrr_internal_points
from mrr_switch_optimizer.routing.fabric import FabricEdgeRoute, FabricRoute, FixedFabricRoutingResult
from mrr_switch_optimizer.routing.geometry import _polyline_length, _bend_count
from mrr_switch_optimizer.analysis import nsweep
from mrr_switch_optimizer.analysis.fabric_loss import _evaluate_path_loss
from mrr_switch_optimizer.app import nsweep_campaign as campaign
from mrr_switch_optimizer.app.fabric_reports import fixed_fabric_geometry_hash

OUT = ROOT / 'results/n8_embedding_dp'
OLD = ROOT / 'results/n8_state_assignment'
BASELINE = 5.181320181939005
OBJECTIVES = tuple('ABCDEF')
SUMMARY_FIELDS = ('candidate_id method dp_objective embedding_decisions proxy_score '
    'route_success routing_runtime total_length_um physical_crossings bend_count wrap_count '
    'global_wil_bfs_db global_wil_opt_assignment_db median_wil_db p90_wil_db p99_wil_db '
    'worst_permutation worst_input baseline_worst_set_improved_count '
    'unique_worst_set_improved_count').split()


def dump(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + '\n')
    temp.replace(path)


def csv_write(path, rows, fields=None):
    rows = list(rows)
    with Path(path).open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields or list(rows[0]))
        w.writeheader()
        w.writerows(rows)
        f.flush()
        os.fsync(f.fileno())


def hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for d in ('mrr_switch_optimizer', 'tests') for p in sorted((ROOT/d).rglob('*'))
            if p.is_file()}


def check_hashes():
    after = hashes()
    dump(OUT/'source_hashes_after.json', after)
    assert after == json.loads((OUT/'source_hashes_before.json').read_text()), 'Protected source changed'


@dataclass
class Node:
    node_id: str
    wires: tuple
    stage_start: int
    stage_end: int
    input_ses: tuple
    output_ses: tuple
    upper: object = None
    lower: object = None

    @property
    def size(self):
        return len(self.wires)

    @property
    def own(self):
        return self.input_ses + self.output_ses

    @property
    def choices(self):
        if self.size == 1:
            return ('normal',)
        if self.size > 2 and self.upper.size == self.lower.size:
            return ('normal', 'swap_children', 'mirror_y', 'swap_children+mirror_y')
        return ('normal', 'mirror_y')


def tree(top, wires=None, start=0, name='root'):
    wires = tuple(range(top.N_logical)) if wires is None else wires
    stages = _build_waksman_for_wires(wires)
    end = start + len(stages) - 1
    incoming = tuple(top.mrr_id(start, pair) for pair in stages[0]) if stages else ()
    outgoing = tuple(top.mrr_id(end, pair) for pair in stages[-1]) if len(stages) > 1 else ()
    node = Node(name, wires, start, end, incoming, outgoing)
    if len(wires) > 2:
        node.upper = tree(top, wires[::2], start+1, name+'.U')
        node.lower = tree(top, wires[1::2], start+1, name+'.L')
    return node


def nodes(node):
    return ([node] + nodes(node.upper) + nodes(node.lower)) if node.upper else [node]


def node_payload(node, n, pitch):
    return dict(node_id=node.node_id, size=node.size, logical_wires=node.wires,
                input_side_SEs=node.input_ses, output_side_SEs=node.output_ses,
                upper_child=node.upper.node_id if node.upper else None,
                lower_child=node.lower.node_id if node.lower else None,
                stage_range=[node.stage_start, node.stage_end],
                physical_wire_y_locations={w: (n-1-w)*pitch for w in node.wires},
                selected_embedding='normal', local_choices=node.choices)


def assemble(node, base, upper, lower, choice, n, pitch):
    """Rigid placement operations only: IDs, ports, graph and S-table stay fixed."""
    cells = {key: base[key] for key in node.own}
    for child, child_cells, sign in ((node.upper, upper, -1), (node.lower, lower, 1)):
        if child is None:
            continue
        shift = sign * (node.lower.wires[0]-node.upper.wires[0])*pitch if 'swap_children' in choice else 0
        cells.update({key: replace(cell, center=(cell.center[0], cell.center[1]+shift))
                      for key, cell in child_cells.items()})
    if 'mirror_y' in choice:
        axis = ((n-1-min(node.wires)) + (n-1-max(node.wires)))*pitch/2
        cells = {key: replace(cell, center=(cell.center[0], 2*axis-cell.center[1]),
                    ports={p: replace(v, dy=-v.dy) for p, v in cell.ports.items()})
                 for key, cell in cells.items()}
    return cells


def placement(node, base, decisions, n, pitch):
    u = placement(node.upper, base, decisions, n, pitch) if node.upper else {}
    l = placement(node.lower, base, decisions, n, pitch) if node.lower else {}
    return assemble(node, base, u, l, decisions.get(node.node_id, 'normal'), n, pitch)


def physical_contract(top, graph, base, cells):
    assert len(cells) == top.n_MRR and set(cells) == set(base)
    validate_fabric_graph(top, graph)
    assert graph == build_fabric_graph(top)
    for key, cell in cells.items():
        old = base[key]
        assert cell.id == old.id and cell.s_table == old.s_table
        assert cell.bbox == old.bbox and cell.d_min_th == old.d_min_th
        assert cell.center[0] == old.center[0]
        for p, v in cell.ports.items():
            assert v.dx == old.ports[p].dx and abs(v.dy) == abs(old.ports[p].dy)
            assert v.kind == old.ports[p].kind and v.phi == old.ports[p].phi
        for i in ('in', 'add'):
            for o in ('th', 'drop'):
                a, b = _mrr_internal_points(old, i, o), _mrr_internal_points(cell, i, o)
                assert _polyline_length(a) == _polyline_length(b) and _bend_count(a) == _bend_count(b)
    # An omitted redundant output switch makes a reflected column occupy a
    # different subset of slots. Check actual clearance, not identical slot sets.
    values=list(cells.values())
    for i,a in enumerate(values):
        for b in values[i+1:]:
            assert (abs(a.center[0]-b.center[0]) >= (a.bbox.width+b.bbox.width)/2+12 or
                    abs(a.center[1]-b.center[1]) >= (a.bbox.height+b.bbox.height)/2+12)


def verify_transformations(table):
    records = []
    for n in (4, 6):  # Explicit prerequisite order, before creating N=8 candidates.
        top = WaksmanTopology(n, verify_rnb=False)
        graph = build_fabric_graph(top)
        env = octave_envelope(n)
        base = build_envelope_cells(top, table, env)
        root = tree(top)
        ns = nodes(root)
        all_perms = list(permutations(range(n)))
        assignments = [(p, top.get_state_assignment(p)) for p in all_perms]
        variants = 0
        for choices in product(*(node.choices for node in ns)):
            decisions = dict(zip((node.node_id for node in ns), choices))
            cells = placement(root, base, decisions, n, env.wire_pitch_um)
            physical_contract(top, graph, base, cells)
            links = {(e.source.endpoint_id, e.target.endpoint_id) for e in graph.edges}
            for perm, states in assignments:
                assert realize_state_assignment(top, states) == perm
                for path in top.get_active_paths(perm, states):
                    endpoint = f'I{path.input_port}'
                    for step in path.steps:
                        assert (endpoint, f'{step.mrr_id}.{step.in_port}') in links
                        assert (step.in_port, step.out_port, step.state) in cells[step.mrr_id].s_table
                        assert step.state == states[step.mrr_id]
                        endpoint = f'{step.mrr_id}.{step.out_port}'
                    assert (endpoint, f'O{path.output_port}') in links
            variants += 1
        records.append(dict(N=n, variants=variants, permutations_per_variant=math.factorial(n),
            exhaustive_port_level_checks=variants*math.factorial(n), all_passed=True,
            stage_pairs_unchanged=True, state_bit_remapping='identity',
            nodes=[node_payload(node,n,env.wire_pitch_um) for node in ns]))
        dump(OUT/'transformation_validation.json', records)
        print(f'§4: N={n}: {variants} composed embeddings × {math.factorial(n)} permutations PASS', flush=True)
    dump(OUT/'transformation_discovery.json', dict(
        enabled=['normal', 'equal-size recursive child placement swap', 'vertical subtree reflection including cell ports'],
        geometry_only=True, logical_port_roles_unchanged=True,
        rejected=[dict(choice='horizontal reflection / 180-degree SE rotation',
                       reason='Production router hardcodes port sides; transformed dx contradicts port_side and escape directions.'),
                  dict(choice='unconjugated port-role or logical boundary relabeling',
                       reason='Changes named port connectivity / BAR-CROSS semantics; outside permitted geometry-only contract.'),
                  dict(choice='unequal-size child slot exchange',
                       reason='No isomorphic slot bijection; excluded from this finite design space.')],
        boundary_reversal='Physical vertical boundary reversal is the enabled mirror_y, not a logical wire renaming.'))


class Context:
    def __init__(self, table):
        self.top = WaksmanTopology(8, verify_rnb=False)
        self.graph = build_fabric_graph(self.top)
        self.env = octave_envelope(8)
        self.base = build_envelope_cells(self.top, table, self.env)
        self.root = tree(self.top)
        self.rules = campaign._campaign_rules('B_db_placeholder', pops=30000, topology='waksman',
                    min_crossing_clearance_um=10.0, physical_turn_guard=False)
        self.paths = nsweep.enumerate_fabric_paths(self.top, self.graph)
        catalog = json.loads((OLD/'path_catalog.json').read_text())
        assert [json.loads(json.dumps(asdict(p))) for p in self.paths] == [r['path'] for r in catalog]
        self.active = np.load(OLD/'state_path_losses.npz')['path_ids']
        self.perms = list(permutations(range(8)))
        self.perm_index = {p:i for i,p in enumerate(self.perms)}
        self.state_group = np.empty(1 << 17, dtype=np.int32)
        lut = list(csv.DictReader((OLD/'state_lut.csv').open()))
        assert [int(r['state_id']) for r in lut] == list(range(1 << 17))
        for row in lut:
            self.state_group[int(row['state_id'])] = self.perm_index[tuple(json.loads(row['permutation']))]
        counts = np.bincount(self.state_group, minlength=40320)
        assert self.top.n_MRR == 17 and self.active.shape == (131072,8)
        assert counts.sum() == 131072 and np.all(counts > 0)
        self.counts = counts
        prev = list(csv.DictReader((OLD/'permutation_summary.csv').open()))
        assert [tuple(json.loads(r['permutation'])) for r in prev] == self.perms
        self.bfs_masks = np.array([int(r['bfs_state_id']) for r in prev])
        self.old_opt = np.array([float(r['opt_wil_db']) for r in prev])
        self.old_bfs = np.array([float(r['bfs_wil_db']) for r in prev])
        worst = json.loads((OLD/'worst_sets.json').read_text())
        self.worst = np.array([self.perm_index[tuple(json.loads(p))] for p in worst['worst_baseline_set']])
        self.unique = np.array([self.perm_index[tuple(json.loads(p))] for p in worst['unique_worst_set']])
        assert len(self.worst) == 1008 and len(self.unique) == 256
        assert np.all(counts[self.unique] == 1)

    def verify_n8(self):
        dump(OUT/'baseline_structure.json',dict(stage_pairs=self.top.stage_pairs,
             fabric_graph=asdict(self.graph),cell_placement={k:dict(center=c.center,
             ports={p:asdict(v) for p,v in c.ports.items()}) for k,c in self.base.items()},
             physical_rules=asdict(self.rules),envelope=asdict(self.env)))
        ids = [m for m,_,_ in self.top.iter_mrrs()]
        path_ids = {p:i for i,p in enumerate(self.paths)}
        for i,perm in enumerate(self.perms):
            states = self.top.get_state_assignment(perm)
            mask = sum(states[m] << j for j,m in enumerate(ids))
            assert realize_state_assignment(self.top, states) == perm
            assert mask == self.bfs_masks[i] and self.state_group[mask] == i
        # Validate every reused LUT mapping and cached active path, with production semantics.
        for mask in range(131072):
            states = {m:(mask >> j)&1 for j,m in enumerate(ids)}
            perm = self.perms[self.state_group[mask]]
            assert realize_state_assignment(self.top, states) == perm
            assert [path_ids[p] for p in self.top.get_active_paths(perm,states)] == self.active[mask].tolist()
        dump(OUT/'n8_correctness.json', dict(M=17, states=131072, permutations=40320,
             missing=[], sum_multiplicities=int(self.counts.sum()),
             all_lut_rows_and_cached_state_paths_verified=True, bfs_assignments_verified=40320,
             per_candidate_certificate='Identical stage_pairs, named port graph, S-table and state bits; exhaustive certificate transfers by identity.',
             input_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
               [OLD/'state_lut.csv', OLD/'state_path_losses.npz', OLD/'worst_sets.json',OLD/'path_catalog.json',OLD/'permutation_summary.csv']}))
        print('§21: N=8 131072 LUT states / path rows and all 40320 BFS assignments verified.',flush=True)

    def endpoint(self, ep, cells):
        if ep.kind == 'mrr':
            cell = cells[ep.ref]
            xy = cell.port_xy(ep.port)
            access = _port_route_point(cell, ep.port, self.rules, 2.0)
            facing = 'W' if ep.port in ('in','drop') else 'E'
            return xy, access, facing
        xy = (self.env.x_start_um if ep.kind == 'input' else self.env.x_end_um,
              (7-ep.wire)*self.env.wire_pitch_um)
        return xy,xy,('E' if ep.kind == 'input' else 'W')

    def edge_profile(self, edge, cells):
        a,sa,fa = self.endpoint(edge.source,cells)
        b,tb,fb = self.endpoint(edge.target,cells)
        manhattan = abs(a[0]-b[0])+abs(a[1]-b[1])
        lb = abs(a[0]-sa[0])+abs(sa[0]-tb[0])+abs(sa[1]-tb[1])+abs(tb[0]-b[0])
        source_away = (b[0]-a[0]) * (1 if fa == 'E' else -1) < 0
        target_away = (a[0]-b[0]) * (1 if fb == 'E' else -1) < 0
        # Both endpoint tangents are horizontal. A non-straight connection needs >=2 bends.
        bends = 0 if a[1] == b[1] and not source_away and not target_away else 2
        span = (edge.target_stage if edge.target_stage is not None else self.top.n_stages) - (
                edge.source_stage if edge.source_stage is not None else -1)
        wrap = max(0.,lb-manhattan)
        assert not source_away or wrap > 0
        return dict(edge_id=edge.edge_id, stage_span=span, dy_um=abs(a[1]-b[1]),
             L_lb_um=lb, port_facing_wrap_penalty_um=wrap, minimum_bend_count_lb=bends,
             source_facing=fa,target_facing=fb,source_away=source_away,target_away=target_away,
             source_xy=a,target_xy=b,source_access_xy=sa,target_access_xy=tb,
             estimated_edge_cost_db=self.rules.prop_loss_db_per_um*lb+self.rules.bend_loss_db_per_bend*bends)

    def proxy_result(self,cells,profiles):
        edge_routes = tuple(FabricEdgeRoute(p['edge_id'],(tuple(p['source_xy']),tuple(p['target_xy'])),
                            p['L_lb_um'],p['minimum_bend_count_lb']) for p in profiles)
        # A synthetic *weight carrier*, never a claimed route or DRC certificate.
        route = FabricRoute('proxy',self.graph.edge_ids,(),sum(p['L_lb_um'] for p in profiles),
                            sum(p['minimum_bend_count_lb'] for p in profiles),(),(),edge_routes)
        return FixedFabricRoutingResult(self.graph,(route,),(),(),(),self.rules)

    def losses(self,cells,result):
        setup = nsweep._loss_setup(result)
        return [_evaluate_path_loss(p,cells,result,*setup) for p in self.paths]

    def aggregate(self,losses):
        path_il = np.array([p.insertion_loss_db for p in losses])
        state_wil = path_il[self.active].max(axis=1)
        opt = np.full(40320,np.inf)
        np.minimum.at(opt,self.state_group,state_wil)
        bfs = state_wil[self.bfs_masks]
        assert np.all(opt <= bfs+1e-10) and np.all(np.isfinite(opt))
        return path_il,state_wil,bfs,opt


@dataclass
class Entry:
    cells: dict
    decisions: dict
    profile: np.ndarray | None = None


def signature(ctx,node,cells):
    """All cut-edge terminals, including bypass wires; no routed geometry in key."""
    cut = []
    for e in ctx.graph.edges:
        sin = e.source.kind == 'mrr' and e.source.ref in cells
        tin = e.target.kind == 'mrr' and e.target.ref in cells
        if sin == tin:
            continue
        ep = e.source if sin else e.target
        xy,access,facing = ctx.endpoint(ep,cells)
        other = e.target if sin else e.source
        # The unresolved parent position is represented by identity, not guessed coordinates.
        cut.append(dict(edge=e.edge_id,side='output' if sin else 'input',wire=ep.wire,
                   endpoint=ep.endpoint_id,xy=xy,access=access,facing=facing,
                   logical_PSE_role='upper' if ep.port in ('in','th') else 'lower',
                   physical_PSE_role='upper' if cells[ep.ref].ports[ep.port].dy>0 else 'lower',
                   parent_endpoint=other.endpoint_id,
                   toward_parent_x=(facing=='W') if not sin else (facing=='E')))
    boundary = dict(ordered_input_wires=node.wires,ordered_output_wires=node.wires,
                    top_to_bottom=sorted(cut,key=lambda r:(-r['xy'][1],r['endpoint'],r['side'])))
    return json.dumps(boundary,sort_keys=True,separators=(',',':'))


def internal_profile(ctx,cells):
    values=[]
    for e in ctx.graph.edges:
        inside = all(ep.kind=='mrr' and ep.ref in cells for ep in (e.source,e.target))
        if inside:
            p=ctx.edge_profile(e,cells)
            values.extend((p['L_lb_um'],p['minimum_bend_count_lb'],p['port_facing_wrap_penalty_um']))
        else:
            values.extend((0.,0.,0.))
    return np.array(values)


def run_dp(ctx):
    stats=[]
    def solve(node):
        upper=solve(node.upper) if node.upper else [Entry({}, {})]
        lower=solve(node.lower) if node.lower else [Entry({}, {})]
        started=time.perf_counter()
        grouped=defaultdict(list)
        for u,l,choice in product(upper,lower,node.choices):
            cells=assemble(node,ctx.base,u.cells,l.cells,choice,8,ctx.env.wire_pitch_um)
            entry=Entry(cells,{**u.decisions,**l.decisions,node.node_id:choice},internal_profile(ctx,cells))
            grouped[signature(ctx,node,cells)].append(entry)
        retained=[]
        for sig,entries in grouped.items():
            profiles=np.array([e.profile for e in entries])
            keep=[]
            for i,entry in enumerate(entries):
                # Strict componentwise dominance only, never a scalar minimax comparison.
                dominated=np.any(np.all(profiles<=entry.profile,axis=1)&np.any(profiles<entry.profile,axis=1))
                if not dominated:
                    keep.append(entry)
            retained.extend(keep)
            stats.append(dict(node_id=node.node_id,subnet_size=node.size,boundary_signature=sig,
                num_raw_candidates=len(entries),num_after_dominance=len(keep),pareto_size=len(keep),
                runtime=(time.perf_counter()-started)/len(grouped)))
        print(f'§9–10 DP {node.node_id} N={node.size}: {sum(len(v) for v in grouped.values())} raw, '
              f'{len(retained)} retained, {len(grouped)} boundary states',flush=True)
        return retained
    entries=solve(ctx.root)
    csv_write(OUT/'dp_stats.csv',stats)
    return entries,stats


def candidate_id(cells):
    payload=[(k,c.center,[(p,v.dx,v.dy,v.phi,v.kind) for p,v in sorted(c.ports.items())])
             for k,c in sorted(cells.items())]
    return 'e_'+hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:12]


def stage1(ctx):
    started=time.perf_counter()
    entries,stats=run_dp(ctx)
    ns=nodes(ctx.root)
    dump(OUT/'recursive_tree.json',[node_payload(n,8,ctx.env.wire_pitch_um) for n in ns])
    # Score the complete finite space as well as the DP survivors, so top-K and
    # random baselines are not restricted by any proxy pruning.
    pool={}
    dp_ids={candidate_id(e.cells) for e in entries}
    raw_count=math.prod(len(n.choices) for n in ns)
    rng=random.Random(20260920)
    combinations=list(product(*(n.choices for n in ns)))
    random_choices=rng.sample(combinations,100)
    random_ids=[]
    for choices in random_choices:
        dec=dict(zip((n.node_id for n in ns),choices))
        random_ids.append(candidate_id(placement(ctx.root,ctx.base,dec,8,ctx.env.wire_pitch_um)))
    def greedy(node):
        u=greedy(node.upper) if node.upper else Entry({}, {})
        l=greedy(node.lower) if node.lower else Entry({}, {})
        options=[]
        for choice in node.choices:
            cells=assemble(node,ctx.base,u.cells,l.cells,choice,8,ctx.env.wire_pitch_um)
            # Current local interconnect only, excluding edges wholly within either child.
            local=[ctx.edge_profile(e,cells)['estimated_edge_cost_db'] for e in ctx.graph.edges
                   if all(ep.kind=='mrr' and ep.ref in cells for ep in (e.source,e.target))
                   and not any(all(ep.ref in child for ep in (e.source,e.target)) for child in (u.cells,l.cells))]
            options.append((sum(local),choice,Entry(cells,{**u.decisions,**l.decisions,node.node_id:choice})))
        return min(options,key=lambda x:(x[0],node.choices.index(x[1])))[2]
    greedy_entry=greedy(ctx.root)
    greedy_id=candidate_id(greedy_entry.cells)
    baseline_id=candidate_id(ctx.base)
    original_witness=nsweep._witness_for_path
    @lru_cache(maxsize=None)
    def cached_witness(top,path,*,random_tries):
        return original_witness(top,path,random_tries=random_tries)
    edge_rows=[]
    with patch.object(nsweep,'_witness_for_path',cached_witness):
        for j,choices in enumerate(combinations):
            decisions=dict(zip((n.node_id for n in ns),choices))
            cells=placement(ctx.root,ctx.base,decisions,8,ctx.env.wire_pitch_um)
            physical_contract(ctx.top,ctx.graph,ctx.base,cells)
            cid=candidate_id(cells)
            if cid in pool:
                pool[cid]['equivalent_decision_count']+=1
                continue
            prof=[ctx.edge_profile(e,cells) for e in ctx.graph.edges]
            synth=ctx.proxy_result(cells,prof)
            path_result=nsweep.evaluate_fixed_fabric_path_space(ctx.top,cells,synth,classify_all=False)
            losses=ctx.losses(cells,synth)
            _,_,bfs,opt=ctx.aggregate(losses)
            assert abs(max(bfs)-path_result.worst_insertion_loss_db)<1e-10
            by_id={p['edge_id']:p for p in prof}
            A=sum(p['estimated_edge_cost_db'] for p in prof)
            B=max(sum(by_id[e]['estimated_edge_cost_db'] for e in w.edge_ids) for w in ctx.graph.waveguides)
            C=sum(p['port_facing_wrap_penalty_um'] for p in prof)
            burdens=defaultdict(float)
            for e,p in zip(ctx.graph.edges,prof):
                burdens[e.source_stage if e.source_stage is not None else -1]+=p['stage_span']*p['estimated_edge_cost_db']
            D=max(burdens.values())
            E=A/len(prof)+B+ctx.rules.prop_loss_db_per_um*C/len(prof)+D/8
            F=path_result.worst_insertion_loss_db
            pool[cid]=dict(candidate_id=cid,embedding_decisions=decisions,
                objectives=dict(zip(OBJECTIVES,(A,B,C,D,E,F))),proxy_global_opt_assignment_db=float(max(opt)),
                proxy_witness_permutation=path_result.witness_permutation,
                proxy_witness_input=path_result.worst_path.input_port,
                is_dp=cid in dp_ids,is_random=cid in random_ids,is_greedy=cid==greedy_id,
                is_baseline=cid==baseline_id,equivalent_decision_count=1,
                wrap_count=sum(p['source_away'] or p['target_away'] for p in prof))
            edge_rows.extend(dict(candidate_id=cid,**p) for p in prof)
            if (j+1)%64==0:
                print(f'§7–8 proxy {j+1}/{raw_count}; {len(pool)} unique geometries; F={F:.9f}',flush=True)
    csv_write(OUT/'proxy_edges.csv',edge_rows)
    csv_write(OUT/'proxy_rankings.csv',[dict(candidate_id=cid,**r['objectives'],
        proxy_global_opt_assignment_db=r['proxy_global_opt_assignment_db'],is_dp=r['is_dp'],
        is_random=r['is_random'],is_greedy=r['is_greedy'],is_baseline=r['is_baseline']) for cid,r in pool.items()])
    dump(OUT/'candidates.json',pool)
    ranks={o:sorted(dp_ids,key=lambda cid:(round(pool[cid]['objectives'][o],12),round(pool[cid]['objectives']['F'],12),cid)) for o in OBJECTIVES}
    assert all(abs(min(r['objectives'][o] for r in pool.values())-
                   pool[ranks[o][0]]['objectives'][o])<1e-10 for o in OBJECTIVES)
    dump(OUT/'top_k.json',{o:ids[:20] for o,ids in ranks.items()})
    random_rank=sorted(set(random_ids),key=lambda cid:(round(pool[cid]['objectives']['F'],12),cid))
    dump(OUT/'random_100.json',dict(seed=20260920,sampling='100 distinct decision vectors without replacement',
         draws=random_ids,unique_geometries=len(set(random_ids)),rank_by_F=random_rank))
    selected=[]
    def add(cid,method,objective,priority):
        if cid not in {r['candidate_id'] for r in selected} and len(selected)<24:
            selected.append(dict(candidate_id=cid,method=method,dp_objective=objective,priority=priority))
    add(baseline_id,'baseline','F',1)
    for o in OBJECTIVES:
        add(ranks[o][0],'DP',o,2)
    for cid in ranks['F'][:5]:
        add(cid,'DP','F',3)
    add(greedy_id,'greedy','A',4)
    for cid in random_rank[:3]:
        add(cid,'random_best','F',5)
    mid=len(random_rank)//2
    for cid in random_rank[mid-1:mid+2]:
        add(cid,'random_median','F',5)
    for k in range(1,20):
        for o in OBJECTIVES:
            if k<len(ranks[o]):
                add(ranks[o][k],'DP',o,6)
    assert selected[0]['candidate_id']==baseline_id and len(selected)<=24
    dump(OUT/'routing_queue.json',selected)
    metadata=dict(status='complete',raw_design_space=raw_count,unique_geometries=len(pool),
        dp_root_survivors=len(entries),dp_unique_root_geometries=len(dp_ids),
        dp_states=len(stats),pareto_max=max(r['pareto_size'] for r in stats),
        pareto_mean=float(np.mean([r['pareto_size'] for r in stats])),
        total_raw_dp_entries=sum(r['num_raw_candidates'] for r in stats),
        total_retained_dp_entries=sum(r['pareto_size'] for r in stats),
        random_draws=100,greedy_id=greedy_id,baseline_id=baseline_id,
        dp_minima_match_full_space_for_all_objectives=True,
        ranking_tie_rule='Round objective and F secondary scores to 12 decimal places, then candidate ID; avoids summation roundoff determining ties.',
        routing_candidates=len(selected),routing_calls_during_stage1=0,
        elapsed_seconds=time.perf_counter()-started,
        objectives=dict(A='sum estimated edge dB',B='maximum BAR logical-wire chain estimated edge dB',
             C='total facing/wrap excess length um',D='maximum source-stage sum(stage_span * estimated edge dB)',
             E='A/42+B+0.002*C/42+D/8',F='production path-space/witness proxy BFS WIL; exact optimal-assignment proxy also reported'),
        dominance='Within identical cut-boundary signature, compare each labeled internal edge L_lb, bend_lb and wrap excess. Keep equal profiles. No scalar pruning.',
        proxy_crossing_lower_bound='omitted: no reliable decomposition',
        ordered_stages=['N4 validation','N6 validation','N8 certificate','all DP/proxy/random/greedy work','routing queue frozen'])
    dump(OUT/'stage1_complete.json',metadata)
    selected_ids={r['candidate_id'] for r in selected}
    assert all(ranks[o][0] in selected_ids for o in OBJECTIVES)
    assert set(ranks['F'][:5])<=selected_ids and greedy_id in selected_ids
    assert set(random_rank[:3]+random_rank[mid-1:mid+2])<=selected_ids
    dump(OUT/'routing_selection_audit.json',dict(all_required_priority_roles_present=True,
         unique_candidates=len(selected),maximum=24,objective_best={o:ranks[o][0] for o in OBJECTIVES},
         F_top_5=ranks['F'][:5],random_best_3=random_rank[:3],random_middle_3=random_rank[mid-1:mid+2],
         greedy=greedy_id,proxy_and_queue_sha256={name:hashlib.sha256((OUT/name).read_bytes()).hexdigest()
             for name in ('candidates.json','routing_queue.json','dp_stats.csv','proxy_rankings.csv','proxy_edges.csv','stage1_complete.json')}))
    print(f'STAGE 1 COMPLETE: {len(pool)} geometries; {len(selected)} queued routes. No A* called.',flush=True)
    return pool,selected


def route_worker(cid):
    """A separate process allows the parent to enforce the task's 40-minute stop."""
    table=load_mrr_s_table(str(ROOT/'mrr_sparam_library'),strict=True)
    top=WaksmanTopology(8,verify_rnb=False)
    graph=build_fabric_graph(top)
    env=octave_envelope(8)
    base=build_envelope_cells(top,table,env)
    record=json.loads((OUT/'candidates.json').read_text())[cid]
    cells=placement(tree(top),base,record['embedding_decisions'],8,env.wire_pitch_um)
    cells={key:cells[key] for key in base}  # Preserve production cell iteration order.
    physical_contract(top,graph,base,cells)
    calls=0
    original=campaign.route_fixed_fabric
    def once(*args,**kwargs):
        nonlocal calls
        calls+=1
        assert calls==1
        return original(*args,**kwargs)
    directory=OUT/'routes'/cid
    assert not directory.exists(), 'Never reroute or silently reuse a prior candidate'
    with patch.object(campaign,'build_envelope_cells',lambda *_args,**_kwargs:cells), \
         patch.object(campaign,'route_fixed_fabric',once):
        returned=campaign._route_case('waksman',8,'B_db_placeholder',top,graph,env,table,
                output_root=directory,pops_ladder=(30000,),min_crossing_clearance_um=10.0,
                straighten_jogs=True,physical_turn_guard=False,v4_search=False,
                campaign_version='n8-embedding-dp-'+cid)
    actual,result,runtime,*_=returned
    assert calls==1 and actual==cells
    audit=campaign._audit_counts(result,cells)
    accepted=not result.failed_edges and audit['legacy_drc']==0 and audit['bend_radius_legality']==0
    dump(OUT/'route_status'/f'{cid}.json',dict(route_success=accepted,routing_runtime=runtime,
         total_length_um=sum(r.length_um for r in result.routes),physical_crossings=len(result.crossings),
         bend_count=sum(r.bend_count for r in result.routes),audit=audit,route_calls=1,
         geometry_sha256=fixed_fabric_geometry_hash(result),failed_edges=[asdict(e) for e in result.failed_edges],
         drc_violations=[asdict(v) for v in result.drc_violations]))


def append_summary(row):
    path=OUT/'summary.csv'
    new=not path.exists()
    with path.open('a',newline='') as f:
        w=csv.DictWriter(f,fieldnames=SUMMARY_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
        f.flush()
        os.fsync(f.fileno())


def stage2(pool,queue):
    assert (OUT/'stage1_complete.json').exists()
    existing=list(csv.DictReader((OUT/'summary.csv').open())) if (OUT/'summary.csv').exists() else []
    done={r['candidate_id'] for r in existing}
    for index,item in enumerate(queue):
        cid=item['candidate_id']
        if cid in done:
            continue
        assert index<24
        marker=OUT/'route_started'/f'{cid}.json'
        assert not marker.exists(), 'Interrupted route must be investigated, never rerun'
        dump(marker,dict(candidate_id=cid,sequence=index+1,started_unix=time.time()))
        print(f'STAGE 2 route {index+1}/{len(queue)}: {cid} {item["method"]}/{item["dp_objective"]}',flush=True)
        started=time.perf_counter()
        log=OUT/'route_logs'/f'{cid}.log'
        log.parent.mkdir(exist_ok=True)
        with log.open('w') as f:
            worker=subprocess.Popen([sys.executable,'-u',str(Path(__file__).resolve()),'--route-worker',cid],
                    stdout=f,stderr=subprocess.STDOUT,cwd=ROOT,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
            while worker.poll() is None:
                try:
                    worker.wait(timeout=min(30,max(.1,2400-(time.perf_counter()-started))))
                except subprocess.TimeoutExpired:
                    elapsed=time.perf_counter()-started
                    dump(OUT/'progress.json',dict(stage=2,candidate=cid,sequence=index+1,
                         completed=len(done),total=len(queue),elapsed_seconds=elapsed))
                    if elapsed>=2400:
                        worker.terminate()
                        try:
                            worker.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            worker.kill(); worker.wait()
                        row={k:'' for k in SUMMARY_FIELDS}
                        row.update(candidate_id=cid,method=item['method'],dp_objective=item['dp_objective'],
                            embedding_decisions=json.dumps(pool[cid]['embedding_decisions'],sort_keys=True),
                            proxy_score=pool[cid]['objectives']['F'],route_success=False,
                            routing_runtime=elapsed,wrap_count=pool[cid]['wrap_count'])
                        append_summary(row)
                        dump(OUT/'routing_stop.json',dict(reason='Single candidate exceeded the section 12 40-minute threshold',
                             candidate_id=cid,runtime_seconds=elapsed,remaining_candidates=len(queue)-index-1))
                        return False
        status_path=OUT/'route_status'/f'{cid}.json'
        status=json.loads(status_path.read_text()) if status_path.exists() else dict(
             route_success=False,routing_runtime=time.perf_counter()-started,error=f'worker exit {worker.returncode}; see {log.name}')
        row={k:'' for k in SUMMARY_FIELDS}
        row.update(candidate_id=cid,method=item['method'],dp_objective=item['dp_objective'],
             embedding_decisions=json.dumps(pool[cid]['embedding_decisions'],sort_keys=True),
             proxy_score=pool[cid]['objectives']['F'],wrap_count=pool[cid]['wrap_count'])
        row.update({k:v for k,v in status.items() if k in SUMMARY_FIELDS})
        # Durable Stage 2 record immediately on completion; Stage 3 fills exact columns later.
        append_summary(row)
        done.add(cid)
        print(f'ROUTE ROW FLUSHED {cid}: success={status["route_success"]}, {status["routing_runtime"]:.1f}s',flush=True)
        if worker.returncode or status['routing_runtime']>2400:
            dump(OUT/'routing_stop.json',dict(reason=status.get('error','Single route runtime exceeded 40 minutes'),candidate_id=cid))
            return False
    dump(OUT/'stage2_complete.json',dict(candidates=len(done),maximum_budget=24,route_calls=len(done),summary_rows_flushed_individually=True))
    return True


def stage3(ctx):
    # Reconfirm the quoted reference with the current evaluator, even if a fresh
    # baseline attempt was stopped by the mandatory runtime guard.
    with (OLD/'cases/waksman/n08/B_db_placeholder/routing_result.pkl').open('rb') as f:
        reference_cells,reference_geometry=pickle.load(f)
    reference_losses=ctx.losses(reference_cells,reference_geometry)
    _,_,reference_bfs,reference_opt=ctx.aggregate(reference_losses)
    assert abs(max(reference_bfs)-BASELINE)<1e-10 and abs(max(reference_opt)-BASELINE)<1e-10
    assert np.allclose(reference_opt,ctx.old_opt,rtol=0,atol=1e-10)
    dump(OUT/'baseline_reference_recomputed.json',dict(source='previous experiment frozen baseline geometry',
         evaluator='current analysis.fabric_loss._evaluate_path_loss',
         global_wil_bfs_db=float(max(reference_bfs)),global_wil_opt_assignment_db=float(max(reference_opt)),
         bfs_quantiles=np.percentile(reference_bfs,[50,90,99]).tolist(),
         opt_quantiles=np.percentile(reference_opt,[50,90,99]).tolist(),
         all_permutation_optima_match_previous=True))
    rows=list(csv.DictReader((OUT/'summary.csv').open()))
    for row in rows:
        if str(row['route_success']).lower()!='true' or row['global_wil_opt_assignment_db']:
            continue
        cid=row['candidate_id']
        path=OUT/'routes'/cid/'cases/waksman/n08/B_db_placeholder/routing_result.pkl'
        with path.open('rb') as f:
            cells,result=pickle.load(f)
        physical_contract(ctx.top,ctx.graph,ctx.base,cells)
        geom=fixed_fabric_geometry_hash(result)
        evaluation=replace(result,drc_violations=tuple(v for v in result.drc_violations if v.rule not in
             {'same_net_min_spacing','perpendicular_clearance','crossing_clearance'}))
        assert not evaluation.drc_violations
        losses=ctx.losses(cells,evaluation)
        path_il,state_wil,bfs,opt=ctx.aggregate(losses)
        reference=nsweep.evaluate_fixed_fabric_path_space(ctx.top,cells,evaluation,classify_all=False)
        assert abs(reference.worst_insertion_loss_db-float(max(bfs)))<1e-10
        worst_i=int(np.argmax(opt))
        masks=np.flatnonzero(ctx.state_group==worst_i)
        mask=int(min(masks,key=lambda m:(state_wil[m],m)))
        wi=int(np.argmax(path_il[ctx.active[mask]]))
        worst_delta=ctx.old_opt[ctx.worst]-opt[ctx.worst]
        unique_delta=ctx.old_opt[ctx.unique]-opt[ctx.unique]
        row.update(global_wil_bfs_db=float(max(bfs)),global_wil_opt_assignment_db=float(max(opt)),
            median_wil_db=float(np.median(opt)),p90_wil_db=float(np.percentile(opt,90)),
            p99_wil_db=float(np.percentile(opt,99)),worst_permutation=json.dumps(ctx.perms[worst_i]),worst_input=wi,
            baseline_worst_set_improved_count=int(np.sum(worst_delta>1e-10)),
            unique_worst_set_improved_count=int(np.sum(unique_delta>1e-10)))
        detail=dict(candidate_id=cid,global_wil_opt_assignment_db=float(max(opt)),
            global_wil_bfs_db=float(max(bfs)),bfs_quantiles=dict(zip(('median','p90','p99'),np.percentile(bfs,[50,90,99]).tolist())),
            optimal_quantiles=dict(zip(('median','p90','p99'),np.percentile(opt,[50,90,99]).tolist())),
            worst_baseline_set=dict(size=1008,improved_count=int(np.sum(worst_delta>1e-10)),
                mean_improvement_db=float(np.mean(worst_delta)),maximum_improvement_db=float(max(worst_delta)),
                still_at_5_181320181939005_db=int(np.sum(np.abs(opt[ctx.worst]-BASELINE)<=1e-10)),
                new_maximum_db=float(max(opt[ctx.worst]))),
            unique_worst_set=dict(size=256,improved_count=int(np.sum(unique_delta>1e-10)),
                mean_improvement_db=float(np.mean(unique_delta)),maximum_improvement_db=float(max(unique_delta)),
                still_at_5_181320181939005_db=int(np.sum(np.abs(opt[ctx.unique]-BASELINE)<=1e-10)),
                new_maximum_db=float(max(opt[ctx.unique]))),
            worst_state_id=mask,worst_permutation=ctx.perms[worst_i],worst_input=wi,
            worst_path=asdict(ctx.paths[int(ctx.active[mask,wi])]),
            worst_path_loss=asdict(losses[int(ctx.active[mask,wi])]),
            production_BFS_witness=asdict(reference),geometry_sha256=geom,
            all_40320_permutations_evaluated=True,all_131072_states_evaluated=True,
            geometry_unchanged=geom==fixed_fabric_geometry_hash(result))
        dump(OUT/'exact'/f'{cid}.json',detail)
        (OUT/'exact').mkdir(exist_ok=True)
        np.savez_compressed(OUT/'exact'/f'{cid}.npz',bfs=bfs,opt=opt,worst_delta=worst_delta,unique_delta=unique_delta)
        csv_write(OUT/'exact'/f'{cid}_permutations.csv',
             (dict(permutation=json.dumps(p),multiplicity=int(ctx.counts[i]),bfs_wil_db=bfs[i],opt_wil_db=opt[i])
              for i,p in enumerate(ctx.perms)),['permutation','multiplicity','bfs_wil_db','opt_wil_db'])
        csv_write(OUT/'summary.csv',rows,SUMMARY_FIELDS)
        print(f'STAGE 3 {cid}: GLOBAL_WIL_OPT_ASSIGN={max(opt):.15f}; unique worst improved={np.sum(unique_delta>1e-10)}/256',flush=True)
    dump(OUT/'stage3_complete.json',dict(evaluated_candidates=sum(bool(r['global_wil_opt_assignment_db']) for r in rows)))
    return rows


def finalize(ctx):
    """Read-only production-evaluator crosschecks and final artifact audit; no routing."""
    rows=list(csv.DictReader((OUT/'summary.csv').open()))
    queue=json.loads((OUT/'routing_queue.json').read_text())
    selection=json.loads((OUT/'routing_selection_audit.json').read_text())
    for name,expected in selection['proxy_and_queue_sha256'].items():
        assert hashlib.sha256((OUT/name).read_bytes()).hexdigest()==expected, name
    assert [r['candidate_id'] for r in rows]==[r['candidate_id'] for r in queue[:len(rows)]]
    markers=list((OUT/'route_started').glob('*.json'))
    assert len(markers)==len(rows)<=24
    assert len({r['candidate_id'] for r in rows})==len(rows)
    accepted=[r for r in rows if r['global_wil_opt_assignment_db']]
    old_files=json.loads((OUT/'n8_correctness.json').read_text())['input_hashes']
    for name,digest in old_files.items():
        assert hashlib.sha256((OLD/name).read_bytes()).hexdigest()==digest
    certificates=[]
    geometries={}
    for row in rows:
        cid=row['candidate_id']
        status_file=OUT/'route_status'/f'{cid}.json'
        if status_file.exists():
            status=json.loads(status_file.read_text())
            assert status['route_calls']==1
            directory=OUT/'routes'/cid/'cases/waksman/n08/B_db_placeholder'
            config=json.loads((directory/'config.json').read_text())
            assert config['max_astar_pops_rung']==30000 and config['min_crossing_clearance_um']==10
            assert config['straighten_jogs'] and not config['physical_turn_guard'] and not config['v4_search']
            assert config['rules']==json.loads(json.dumps(asdict(ctx.rules)))
        if not row['global_wil_opt_assignment_db']:
            continue
        with (OUT/'routes'/cid/'cases/waksman/n08/B_db_placeholder/routing_result.pkl').open('rb') as f:
            cells,result=pickle.load(f)
        physical_contract(ctx.top,ctx.graph,ctx.base,cells)
        admitted=replace(result,drc_violations=tuple(v for v in result.drc_violations if v.rule not in
                         {'same_net_min_spacing','perpendicular_clearance','crossing_clearance'}))
        nsweep._init_exhaustive_worker(ctx.top,cells,admitted)
        arrays=np.load(OUT/'exact'/f'{cid}.npz')
        if row['method']=='baseline':
            assert np.allclose(arrays['bfs'],ctx.old_bfs,rtol=0,atol=1e-10)
            assert np.allclose(arrays['opt'],ctx.old_opt,rtol=0,atol=1e-10)
        for prefix in ((0,1),(3,7),(7,6)):
            reference=nsweep._evaluate_prefix_chunk(prefix)
            indices=[i for i,p in enumerate(ctx.perms) if p[:2]==prefix]
            expected=float(max(arrays['bfs'][indices]))
            assert not reference['assignment_failures'] and not reference['fabric_path_failures']
            assert reference['checked']==720 and abs(reference['worst'][0]-expected)<1e-10
            certificates.append(dict(candidate_id=cid,prefix=prefix,permutations_checked=reference['checked'],
                 paths_checked=reference['paths_checked'],production_global_bfs_db=reference['worst'][0],
                 cached_global_bfs_db=expected,absolute_difference_db=abs(reference['worst'][0]-expected)))
        detail_path=OUT/'exact'/f'{cid}.json'
        detail=json.loads(detail_path.read_text())
        for label,indices in (('worst_baseline_set',ctx.worst),('unique_worst_set',ctx.unique)):
            comparisons={}
            for reference_name,reference_values in (('prior_bfs',ctx.old_bfs),('prior_opt_assignment',ctx.old_opt)):
                delta=reference_values[indices]-arrays['opt'][indices]
                comparisons[reference_name]=dict(improved_count=int(np.sum(delta>1e-10)),
                    worsened_count=int(np.sum(delta < -1e-10)),unchanged_count=int(np.sum(np.abs(delta)<=1e-10)),
                    mean_improvement_db=float(np.mean(delta)),maximum_improvement_db=float(max(delta)),
                    minimum_improvement_db=float(min(delta)))
            detail[label]['reference_comparisons']=comparisons
        detail['all_former_BFS_worst_below_reference_count']=int(np.sum(arrays['opt'][ctx.worst]<BASELINE-1e-10))
        dump(detail_path,detail)
        geometries[cid]=(cells,result)
    dump(OUT/'production_exhaustive_crosschecks.json',certificates)
    if accepted:
        best=min(accepted,key=lambda r:float(r['global_wil_opt_assignment_db']))
        cid=best['candidate_id']
        baseline_id=json.loads((OUT/'stage1_complete.json').read_text())['baseline_id']
        if baseline_id in geometries:
            baseline_detail=json.loads((OUT/'exact'/f'{baseline_id}.json').read_text())
            best_detail=json.loads((OUT/'exact'/f'{cid}.json').read_text())
            critical=[]
            for label,description in (('former_global_worst_path',baseline_detail['worst_path']),
                                       ('new_global_worst_path',best_detail['worst_path'])):
                path=next(p for p in ctx.paths if json.loads(json.dumps(asdict(p)))==description)
                comparison={}
                edge_comparison=defaultdict(dict)
                for name,geometry_id in (('baseline',baseline_id),('best',cid)):
                    cells,result=geometries[geometry_id]
                    setup=nsweep._loss_setup(result)
                    comparison[name]=asdict(_evaluate_path_loss(path,cells,result,*setup))
                    from mrr_switch_optimizer.analysis.fabric_loss import _active_edge_ids
                    for edge_id in _active_edge_ids(path,setup[0]):
                        edge=setup[1][edge_id]
                        edge_comparison[edge_id][name]=dict(length_um=edge.length_um,bend_count=edge.bend_count)
                assert comparison['baseline']['mrr_loss_db']==comparison['best']['mrr_loss_db']
                critical.append(dict(label=label,path=description,losses=comparison,edges=edge_comparison))
            dump(OUT/'bottleneck_analysis.json',dict(best_candidate=cid,
                 headline_reduction_db=BASELINE-float(best['global_wil_opt_assignment_db']),
                 old_and_new_critical_paths=critical,
                 worst_permutation_multiplicity=int(ctx.counts[ctx.perm_index[tuple(json.loads(best['worst_permutation']))]]),
                 explanation='MRR transition losses unchanged for each logical path. B configuration charges propagation only '
                   'in the routed external geometry: crossing and per-bend coefficients are zero. '
                   f'The new global bottleneck is I{best_detail["worst_path"]["input_port"]} to '
                   f'O{best_detail["worst_path"]["output_port"]}; propagation contributes '
                   f'{best_detail["worst_path_loss"]["propagation_loss_db"]} dB.'))
            # A concrete lower-bound witness for max_pi min_state: enumerate
            # every LUT realization of the new worst permutation and evaluate
            # its paths directly through production, without cached path IDs.
            permutation=tuple(json.loads(best['worst_permutation']))
            masks=np.flatnonzero(ctx.state_group==ctx.perm_index[permutation])
            ids=[m for m,_,_ in ctx.top.iter_mrrs()]
            cells,result=geometries[cid]
            setup=nsweep._loss_setup(result)
            witness=[]
            for mask in masks:
                states={m:(int(mask)>>i)&1 for i,m in enumerate(ids)}
                assert realize_state_assignment(ctx.top,states)==permutation
                paths=ctx.top.get_active_paths(permutation,states)
                values=[_evaluate_path_loss(p,cells,result,*setup).insertion_loss_db for p in paths]
                wi=int(np.argmax(values))
                witness.append(dict(state_id=int(mask),state_bits=''.join(str(states[m]) for m in ids),
                    wil_db=max(values),worst_input=wi,worst_path=asdict(paths[wi])))
            assert abs(min(r['wil_db'] for r in witness)-float(best['global_wil_opt_assignment_db']))<1e-10
            dump(OUT/'max_min_witness.json',dict(candidate_id=cid,permutation=permutation,
                 mrr_bit_order=ids,num_legal_realizations=len(witness),all_legal_realizations=witness,
                 exact_minimum_wil_db=min(r['wil_db'] for r in witness),
                 full_state_space_also_proves_no_permutation_has_larger_optimum=True))
    stop=json.loads((OUT/'routing_stop.json').read_text()) if (OUT/'routing_stop.json').exists() else None
    audit=dict(protected_sources_unchanged=True,stage1_artifacts_unchanged=True,
         original_lut_path_cache_and_worst_sets_unchanged=True,route_attempts=len(markers),
         completed_routes=sum((OUT/'route_status'/f'{r["candidate_id"]}.json').exists() for r in rows),
         accepted_routes=len(accepted),timed_out_routes=int(stop is not None),unattempted_candidates=len(queue)-len(rows),
         priorities_preserved=True,no_candidate_rerouted=True,all_route_rules_identical=True,
         exact_state_evaluations_per_accepted_candidate=131072,exact_permutations_per_accepted_candidate=40320,
         production_prefix_crosschecks=len(certificates),crosschecked_permutations=sum(r['permutations_checked'] for r in certificates),
         total_attempted_routing_runtime_seconds=sum(float(r['routing_runtime']) for r in rows),
         stage1_proxy_runtime_seconds=json.loads((OUT/'stage1_complete.json').read_text())['elapsed_seconds'])
    check_hashes()
    dump(OUT/'final_audit.json',audit)
    dump(OUT/'progress.json',dict(stage='finished',status='stopped_by_section_12' if stop else 'complete',
         attempted=len(rows),accepted=len(accepted),remaining_unattempted=len(queue)-len(rows)))
    dump(OUT/'reproducibility.json',dict(python=sys.version,command=
         'PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/jchuang-tmp/n8_embedding_mpl python -u experiments/n8_waksman_embedding_dp.py',
         experiment_source_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
             [ROOT/'codex_n8_embedding_dp_task.md',Path(__file__),ROOT/'experiments/n8_embedding_dp_report.py']}))
    from n8_embedding_dp_report import report
    report(OUT)
    print('FINAL AUDIT PASS: protected hashes, frozen selection, single routing calls, identical rules, production evaluator crosschecks.',flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--route-worker')
    parser.add_argument('--stage1-only',action='store_true')
    args=parser.parse_args()
    if args.route_worker:
        route_worker(args.route_worker)
        return
    OUT.mkdir(parents=True,exist_ok=True)
    if not (OUT/'source_hashes_before.json').exists():
        dump(OUT/'source_hashes_before.json',hashes())
    check_hashes()
    try:
        table=load_mrr_s_table(str(ROOT/'mrr_sparam_library'),strict=True)
        if not (OUT/'stage1_complete.json').exists():
            verify_transformations(table)
            ctx=Context(table)
            ctx.verify_n8()
            pool,queue=stage1(ctx)
        else:
            ctx=Context(table)
            pool=json.loads((OUT/'candidates.json').read_text())
            queue=json.loads((OUT/'routing_queue.json').read_text())
        from n8_embedding_dp_report import report
        report(OUT)
        if args.stage1_only:
            return
        # A plain rerun must preserve the mandatory stop instead of silently
        # continuing the remaining queue after a timed-out candidate.
        complete=False if (OUT/'routing_stop.json').exists() else stage2(pool,queue)
        stage3(ctx)
        finalize(ctx)
        dump(OUT/'experiment_status.json',dict(status='complete' if complete else 'stopped_by_section_12',
             primary_metric='GLOBAL_WIL_OPT_ASSIGN',baseline_db=BASELINE))
    except Exception:
        dump(OUT/'failure.json',dict(traceback=traceback.format_exc()))
        raise
    finally:
        check_hashes()


if __name__=='__main__':
    main()
