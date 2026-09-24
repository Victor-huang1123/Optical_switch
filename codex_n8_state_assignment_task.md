# Codex Task — N=8 Waksman Exact State-Assignment Optimization Study

> 目的：對同一個 permutation，Waksman 是否存在多組合法 BAR/CROSS state
> assignment？目前 recursive WaksmanStrategy 找到的任意 feasible assignment，
> 與所有合法 assignment 中 physical WIL 最低者差多少？
>
> 本任務不修改 topology、不修改 physical router、不修改 loss coefficients。

## 0. 先理解現有三層架構，不要改變其 semantics

1. **State assignment** — 給定 π 決定每顆 MRR 是 BAR 還是 CROSS。
   現行實作為 recursive Waksman + parity/XOR constraint propagation。
   **BFS 只出現在這一層。**
2. **Fixed fabric construction** — `build_fabric_graph`，logical wire 確定性
   映射到 physical MRR ports（upper wire: `in`/`th`；lower wire: `add`/`drop`），
   完全不依賴 permutation，不使用 BFS。
3. **Physical routing** — Beneš 用 template、Waksman 用 A*；fixed fabric 只 route 一次。

**硬性限制：**

- DO NOT change topology.
- DO NOT change `build_fabric_graph`.
- DO NOT change physical routing.
- DO NOT change loss coefficients.
- DO NOT re-route for different permutations or assignments.
- 所有 assignment 必須共用同一張 already-routed fixed fabric。
- 不得修改 `mrr_switch_optimizer/` 與 `tests/` 底下任何檔案。

## 0.1 本 repo 的已知事實（省去你摸索）

- 狀態指派策略類別在 `mrr_switch_optimizer/core/state_assignment.py`：
  `WaksmanStrategy`、`BruteForceLUTStrategy`、`realize_state_assignment`、
  `verify_state_assignment`、`_solve_binary_constraints`（parity 傳播）。
- 拓撲在 `core/topology.py`：`WaksmanTopology(n)`，
  `topology.stage_pairs`、`topology.n_MRR`、`topology.mrr_id(stage, pair)`、
  `topology.get_active_paths(perm, states)`。
- Fabric graph：`core/fabric.py:build_fabric_graph`。
- 繞線：`routing/fabric.py:route_fixed_fabric`；
  campaign 包裝在 `app/nsweep_campaign.py:_route_case`，
  它會把繞線結果快取在 `output_root` 指定的目錄。
- 損耗評估：`analysis/nsweep.py:evaluate_fixed_fabric_path_space`
  與 `evaluate_fixed_fabric_parallel`；`analysis/fabric_loss.py`。
- **請務必 reuse 既有的 fixed-fabric loss evaluator，不要另寫一套。**

**損耗配置固定用 `B_db_placeholder`。** 理由：`C_db_realistic` 在現行幾何下
waksman n8 為 `route_failed`（見 `outputs/nsweep_fixed_fabric_v3_mini/cases/
waksman/n08/C_db_realistic/case_metrics.json`）。B 配置的 crossing loss 為 0，
正好把 state assignment 的效應與交叉損耗隔離，是本實驗較乾淨的條件。

**不要比對 v3-mini 的 geometry hash。** 工作區中 `routing/` 下多個 A* 核心檔案
於 2026-09-01 至 09-02 被修改且未 commit，晚於 v3-mini 產物（08-31 20:38），
現行程式碼無法重現當時的 A* 幾何。請以現行程式碼產生的 fabric 為準，
只要求 `failed_edges == 0` 且 legacy DRC 與 bend-radius 違規皆為 0。

繞線建議呼叫 `_route_case`，並帶 `pops_ladder=(30000,)`、
`min_crossing_clearance_um=10.0`、`straighten_jogs=True`、
`physical_turn_guard=False`、`v4_search=False`、
`output_root=Path("results/n8_state_assignment")`，
讓繞線結果被快取（waksman n8 首次繞線約 16 分鐘，之後載入快取）。

## 1. 獨立的 experiment script

新增 `experiments/n8_waksman_state_assignment_study.py`。不要大改 core。
先搜尋 repository 確認實際函式名稱與介面再寫。

## 2. 建立 N=8 Waksman topology

取得實際 SE / MRR 數 `M`，輸出 `N`、`M`、`2^M`、`8!`。
標準 N=8 Waksman 預期 `M = 17`，但**不要硬寫死**：

```
if M != 17:
    print a clear warning
    continue using the actual M from implementation
```

## 3. Exact enumeration of ALL switch-state assignments

這是本實驗的 golden oracle。枚舉 `state_mask = 0 … (1 << M) - 1`，
每個 bit 對應一顆 MRR（`0 = BAR`、`1 = CROSS`）。對每一組 state：

1. 以現有 topology simulator（`realize_state_assignment` 等價物）模擬
   input 0..7 最後到哪個 output。
2. 得到 `π_state = [out(0), …, out(7)]`。
3. 驗證 `sorted(π_state) == [0..7]`。
4. 建立反向 LUT `permutation_to_states[π_state].append(state_mask)`。

**只枚舉一次全部 `2^M` states**，每個 state 模擬一次，建立 state → permutation
的反向 index。不要對每個 permutation 各自暴力枚舉。

## 4. Exact RNB / coverage verification

枚舉 `8! = 40320` permutations，確認每個都至少有一個 state。輸出：

`num_total_states`、`num_distinct_permutations_realized`、
`num_missing_permutations`、`min/max/mean/median_realizations_per_permutation`。

`M = 17` 時理論 total states = 131072，平均 multiplicity ≈ `131072 / 40320 ≈ 3.25`。
**不要假設分布均勻，要實際統計。**

`missing permutation > 0` 立即報錯並列出，那代表 topology / state simulation /
RNB 有問題。

## 5. Current BFS baseline

對全部 40320 個 permutations 呼叫正式使用的 `WaksmanStrategy`，取得 `state_bfs(π)`，
並驗證：

- `realize(state_bfs(π)) == π`
- `state_bfs(π) in permutation_to_states[π]`

不成立則列出完整 diagnostic。此步在驗證 BFS solver 確實只是從 exact feasible
set 中挑了一組解。

## 6. 固定 geometry 下評估每一組合法 assignment

**physical geometry 只建立／route 一次，不得對每個 state 或 permutation 重跑 A*。**

對每個 π、每個 `state ∈ permutation_to_states[π]`，以固定 fabric 加上該 state 的
MRR transitions（BAR: `in→th`, `add→drop`；CROSS: `in→drop`, `add→th`）
trace 8 條 active path，算出 `IL_0 … IL_7`，取

```
WIL(state, π) = max_i IL_i
```

並記錄造成 WIL 的 witness input / output / path。

## 7. Exact optimal assignment

```
state_opt(π) = argmin_{state ∈ S(π)} WIL(state, π)
delta_WIL(π) = WIL_BFS(π) − WIL_OPT(π)      （應 ≥ 0，容許浮點誤差）
```

同時記錄 `num_realizations(π)`、BFS 與 OPT 的 state bit vector、
兩者的 CROSS MRR 數、兩者的 worst-path input。

## 8. Tie-breaking

多組 assignment 有相同最小 WIL 時，依序比較：

1. smaller WIL
2. fewer CROSS / resonant MRRs
3. lower total insertion loss across all 8 signals
4. lexicographically smaller state vector

並記錄 `num_optimal_states`，**不要把 multiple optimum 偷偷丟掉**。

## 9. GF(2) / BFS freedom instrumentation

**這一階段只做 instrumentation，不要用 GF(2) 取代 solver。**

在現行 recursive parity/XOR solver 中，對每個 recursive call 記錄：
變數數、XOR 約束數、連通分量數、被固定值錨定的分量數、未錨定分量數、
估計的 local binary freedom。

若容易實作，再對該 recursive level 建立 `A x = b (mod 2)` 並做 GF(2) 高斯消去，
輸出 `rank(A)` 與 `nullity = n_variables − rank(A)`。GF(2) 僅用於分析自由度，
**不得影響正式 BFS result**。

目的是比較「GF(2)/constraint 預測的自由度」與「`2^M` 枚舉得到的實際完整解數」。
注意 Waksman 有 recursion，**不要假設整網解數等於各 local nullity 直接相乘**。

## 10. 主要輸出 CSV

`results/n8_state_assignment/permutation_summary.csv`，每列一個 permutation，
欄位至少包含：

```
permutation, num_realizations,
bfs_state_bits, bfs_cross_count, bfs_wil_db, bfs_total_il_db,
bfs_worst_input, bfs_worst_output,
opt_state_bits, opt_cross_count, opt_wil_db, opt_total_il_db,
opt_worst_input, opt_worst_output,
delta_wil_db, relative_improvement_percent, num_optimal_states
```

`results/n8_state_assignment/state_lut.csv`：
`state_id, state_bits, permutation, cross_count`，
檔案不過大時附 `wil_db, worst_input`。

## 11. Global summary

`results/n8_state_assignment/summary.json`，至少包含：

```
N, M, num_states, num_permutations,
coverage_complete, missing_permutations,
min/max/mean/median_realizations,
num_permutations_with_multiple_assignments, fraction_with_multiple_assignments,
num_permutations_bfs_is_optimal, fraction_bfs_optimal,
num_permutations_bfs_is_not_optimal,
mean/median/max_delta_wil_db,
mean/max_relative_improvement_percent,
permutation_with_max_improvement
```

以及該 permutation 的 BFS state、OPT state、BFS WIL、OPT WIL、8 條 path loss 比較。

## 12. 圖表

1. `realization_multiplicity_histogram`（x = 該 π 的合法 state 數，y = π 數）
2. `delta_wil_histogram`
3. `bfs_vs_opt_wil_scatter`（加 `y = x` 參考線）
4. `multiplicity_vs_gain_scatter`（回答：routing freedom 越大是否真的帶來
   更多 physical optimization opportunity）
5. optional：`cross_count_difference` histogram

## 13. 必須列出的 worst / interesting cases

A. BFS 最差於 optimum 的 top 20 permutations（依 `delta_WIL` 降序），
   每個列出 π、multiplicity、BFS/OPT state、BFS/OPT WIL、delta、worst path。
B. realization multiplicity 最大的 top cases。
C. 有很多 legal assignments 但所有 assignment WIL 幾乎一樣的 cases。
D. 只有一組 legal assignment 的 cases。

這四類用於理解 combinatorial freedom 是否等於 physical freedom。

## 14. Performance

**不要做 `40320 × 131072` 的雙重暴力。** 正確流程為
`2^M` 枚舉 → state → permutation LUT → 每個 permutation 只 evaluate 自己的
candidate states；總 candidate 數仍為 `2^M`。

盡可能 cache：fixed geometry、edge physical loss、port graph、MRR lookup、
state-independent route quantities。**每個 state evaluation 不得重跑 A\*。**

## 15. Correctness assertions

必須加入：

- `total enumerated states == 2^M`
- every state realizes exactly one permutation
- all realized mappings are bijections
- all `8!` permutations are covered
- BFS state realizes requested permutation
- BFS state exists in exact LUT
- `WIL_OPT <= WIL_BFS + tolerance`

若既有 exhaustive N! WIL evaluator 可用，抽樣或全部比對，確保新 evaluator
沒有改變既有 loss semantics。

## 16. 不要先做 DP / MILP

先用 exact enumeration 當 oracle，回答：

1. 一個 N=8 permutation 平均有多少 legal state assignments？
2. BFS 有多少比例已經是 WIL-optimal？
3. BFS 非 optimal 時平均損失多少 dB？
4. 最大可改善多少 dB？
5. routing freedom 與 WIL improvement 有無 correlation？
6. optimum 通常只是減少 CROSS MRR，還是真的改變了 critical signal path？
7. 是否存在明顯值得做 GF(2)+DP/MILP 的 optimization gap？

## 17. 最終 report

`results/n8_state_assignment/REPORT.md`，結構：

```
# N=8 Waksman State-Assignment Optimization Study
## 1. Experimental setup
## 2. Exact state-space coverage
## 3. Routing multiplicity
## 4. Current BFS baseline
## 5. Exact minimum-WIL assignment
## 6. BFS vs optimum
## 7. GF(2)/constraint freedom observations
## 8. Representative cases
## 9. Runtime
## 10. Conclusion
```

**最後不要下 novelty 結論**，只依數據回答：是否存在 meaningful optimization
space、gap 多大、下一步較適合 GF(2)+enumeration／recursive DP／MILP／
或其實 BFS 已經夠好。

## 18. 最後回報

1. 修改／新增哪些檔案
2. N=8 實際 MRR/SE 數
3. `2^M` states 是否全部成功 enumerate
4. 40320 permutations 是否 100% covered
5. legal assignment multiplicity distribution
6. BFS optimal percentage
7. mean / max delta WIL
8. 最大改善 permutation 的完整案例
9. runtime
10. 依結果判斷是否值得做 GF(2)+DP/MILP
