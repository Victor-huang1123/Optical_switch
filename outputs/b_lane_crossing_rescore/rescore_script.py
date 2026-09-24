import sys, pickle, json, time, warnings, random
sys.path.insert(0, "/home/jchuang/Optical_switch")
warnings.filterwarnings("ignore")
from mrr_switch_optimizer.analysis.nsweep import (
    enumerate_fabric_paths, _loss_setup, _benes_forced_bar_switches, _path_for_permutation)
from mrr_switch_optimizer.analysis.fabric_loss import _evaluate_path_loss
from mrr_switch_optimizer.core.topology import WaksmanTopology, PaddedBenesTopology
from mrr_switch_optimizer.core.state_assignment import WaksmanStrategy, BenesLoopingStrategy

XL = [0.0, 0.02, 0.05, 0.1, 0.2]
TRIES = 20000
LOG = open(sys.argv[1], "w", buffering=1)

def witness(T, path, forced):
    fb = [s.mrr_id for s in path.steps if s.state == 1 and s.mrr_id in forced]
    if fb: return None, "forced-bar prune"
    ip, op = path.input_port, path.output_port
    src = [v for v in range(T.N_logical) if v != ip]
    tgt = [v for v in range(T.N_logical) if v != op]
    rng = random.Random(sum((i+1)*sum(map(ord, s.mrr_id))*(s.state+1) for i, s in enumerate(path.steps)))
    for a in range(1, TRIES+1):
        sh = rng.sample(tgt, len(tgt))
        c = [0]*T.N_logical; c[ip] = op
        for s, t in zip(src, sh): c[s] = t
        pm = tuple(c)
        if _path_for_permutation(T, pm, ip) == path: return pm, "random witness"
    return None, "unproven"

out = {}
for n in range(3, 13):
    for topo in ("waksman", "padded_benes"):
        p = f"outputs/nsweep_fixed_fabric_v2/cases/{topo}/n{n:02d}/B_db_placeholder/routing_result.pkl"
        try: cells, res = pickle.load(open(p, "rb"))
        except FileNotFoundError: continue
        if res.failed_edges: continue
        T = (WaksmanTopology(n, strategy=WaksmanStrategy(), verify_rnb=False) if topo == "waksman"
             else PaddedBenesTopology(n, strategy=BenesLoopingStrategy(), verify_rnb=False))
        t0 = time.perf_counter()
        paths = enumerate_fabric_paths(T, res.graph)
        link, er, xi = _loss_setup(res)
        comp = []
        for pa in paths:
            L = _evaluate_path_loss(pa, cells, res, link, er, xi)
            comp.append((pa, L.mrr_loss_db + L.propagation_loss_db + L.bend_loss_db, L.crossing_count, L.path_length_um))
        forced = _benes_forced_bar_switches(T)
        cache, row = {}, {}
        for xl in XL:
            sc = sorted(((b + xl*xc, pa, xc, ln) for pa, b, xc, ln in comp), key=lambda t: -t[0])
            lo, skipped = None, 0
            for il, pa, xc, ln in sc:
                if pa not in cache: cache[pa] = witness(T, pa, forced)
                if cache[pa][0] is not None:
                    lo = {"wil": il, "xings": xc, "len": ln, "io": [pa.input_port, pa.output_port], "skipped": skipped}
                    break
                skipped += 1
            row[str(xl)] = {"lower": lo, "upper": sc[0][0], "upper_x": sc[0][2], "exact": skipped == 0}
        row["_meta"] = {"paths": len(paths), "cert": len(cache), "wall_s": round(time.perf_counter()-t0, 1)}
        out[f"{topo}_n{n}"] = row
        print(f'{topo:13s} n={n:2d} paths={len(paths):4d} {time.perf_counter()-t0:6.1f}s  ' +
              '  '.join(f'{x}:{row[str(x)]["lower"]["wil"]:.3f}{"=" if row[str(x)]["exact"] else "~"}{row[str(x)]["upper"]:.3f}' for x in XL), file=LOG)
        json.dump(out, open(sys.argv[2], "w"), indent=1)
print("DONE", file=LOG)
