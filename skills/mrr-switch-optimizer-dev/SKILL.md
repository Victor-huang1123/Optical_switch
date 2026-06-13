---
name: mrr-switch-optimizer-dev
description: MRR optical switch synthesis framework. Topology-agnostic, port-aware, surrogate-calibrated single-wavelength synthesis for N=6, comparing Padded 8×8 Beneš vs Native 6×6 Spanke–Beneš. Trigger on: optical switch, MRR, Beneš, Spanke-Beneš, insertion loss, crosstalk, SA placement, surrogate calibration, S-parameter, permutation LUT. Canonical spec: mrr_switch_prompt.md.
---

# mrr-switch-optimizer-dev

Canonical spec: **`mrr_switch_prompt.md`** — read the relevant § directly.

## 研究定位

**Topology-agnostic, port-aware, surrogate-calibrated** single-wavelength MRR switch synthesis. N=6 on two RNB topologies simultaneously:

- **Padded 8×8 Beneš** — 20 MRR, 5 stages, native crossings, 2 blocked port pairs
- **Native 6×6 Spanke–Beneš** — 15 MRR, 9 stages, `has_native_crossings = False`

Four contributions: (1) port-to-port MRR graph, (2) LSE cross-path SI objective, (3) analytic+ridge surrogate with Kendall-τ rollback, (4) topology-agnostic framework.

## Spec 索引

| 主題 | § |
|---|---|
| Scope & fixed assumptions | §0 |
| MRR Library Cell + S-table | §1 |
| `RNBTopology` interface, both specs | §2 |
| Permutation LUT (S₆=720, 500/220 split) | §3 |
| Port-aware graph, edge geometry | §4 |
| IL path loss + incoherent crosstalk | §5 |
| LSE aggregation over paths & permutations | §6 |
| Hard constraints (SA reject) | §7 |
| Surrogate: analytic prior + ridge residual | §8 |
| SA placement + incremental affected-set eval | §9 |
| Physical routing & calibration loop + rollback | §10 |
| Final validation + topology cross-comparison | §11 |

## 紅線

- **Scope 鎖死**：W=1, binary state, unidirectional, θ=0, no process variation — 不改
- **Port-to-port edges**：不可 center-to-center（contribution 1）
- **Hard constraints → SA reject**：thermal spacing / bend radius / DRC 不可 soft penalty
- **LSE β 固定**：v1 不與 SA 溫度同時 anneal（防 debug 爆炸）
- **Surrogate = analytic + ridge residual**：不可純黑箱替代 analytic part
- **Rollback condition**：τ_{t+1} < τ_t − 0.10 → 必須 rollback，不可跳過
- **兩個 topology 必須並行**：不可只跑其中一個（contribution 4 的前提）
- **Incremental affected set**：每次 SA move 只重算含 LeakNeighbors 的 affected set
- **Spanke–Beneš crossing leak = 0 at logical level**：has_native_crossings=False
- **Calibration candidates**：每輪固定 9 個（3 best + 3 uncertain + 3 diverse）
- **Train/eval split 固定 seed**：500 train / 220 eval，不可 overlap
