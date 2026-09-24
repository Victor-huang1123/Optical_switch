# Codex Task — Physical-Aware Waksman Embedding Optimization with Recursive DP (N=8)

> 問題：在保持同一個 logical Waksman RNB topology 的前提下，能否利用 Waksman
> 的 recursive structure 選擇更好的 physical embedding，使 fixed single-MRR PSE
> fabric 的 **global WIL** 降低？
>
> 這**不是** BAR/CROSS state assignment 最佳化。前一個 exact experiment 已證明
> state-assignment freedom 無法降低 headline global WIL（見 §2 baseline）。

## 0. CRITICAL SCOPE

**不得更動：** Waksman RNB semantics、logical SE 數、single-MRR PSE device model、
BAR/CROSS transition semantics、permutation set、loss coefficients、physical design rules。
**不得修改** `mrr_switch_optimizer/` 與 `tests/`（抽出必要 reusable helper 除外，
且須在報告中列明）。

**可更動：** logically equivalent 的 physical embedding choices。可能包括
recursive upper/lower child placement swap、等價子網的垂直順序、mirror/reflection、
等價 wire ordering、SE orientation / port-role orientation。

**但不要假設上述任何一項合法。** 第一步必須 inspect 現行 Waksman construction，
找出哪些 transformation 確實保持 logical semantics。每加入一種都要做 §4 的驗證。
會改變 logical connectivity、state semantics 或破壞 RNB 者，一律不加入。

## 0.1 本 repo 已知事實（省去摸索）

- 拓撲 `core/topology.py`：`WaksmanTopology(n)`、`build_waksman_stage_pairs`、
  `_build_waksman_for_wires`（遞迴以 `wires[0::2]` / `wires[1::2]` 交錯分割）。
- 狀態 `core/state_assignment.py`：`WaksmanStrategy`、`realize_state_assignment`、
  `verify_state_assignment`、`_solve_binary_constraints`。
- Fabric `core/fabric.py:build_fabric_graph`；繞線 `routing/fabric.py:route_fixed_fabric`；
  campaign 包裝 `app/nsweep_campaign.py:_route_case`（會在 `output_root` 快取繞線結果）。
- 損耗 `analysis/nsweep.py:evaluate_fixed_fabric_path_space` /
  `evaluate_fixed_fabric_parallel`、`analysis/fabric_loss.py`。**務必 reuse，不要另寫。**
- **配置固定用 `B_db_placeholder`**（`C_db_realistic` 在現行幾何下 waksman n8 為
  `route_failed`）。繞線旗標沿用：`pops_ladder=(30000,)`、
  `min_crossing_clearance_um=10.0`、`straighten_jogs=True`、
  `physical_turn_guard=False`、`v4_search=False`。
- **不要比對 v3-mini 的 geometry hash。** `routing/` 下多個 A* 核心檔案於
  2026-09-01~09-02 被修改且未 commit，晚於 v3-mini 產物，現行程式碼無法重現當時幾何。

## 1. NEW EXPERIMENT ONLY

新增 `experiments/n8_waksman_embedding_dp.py`，輸出 `results/n8_embedding_dp/`。
保存 source hashes before/after。

## 2. BASELINE（已驗證，可直接引用，但仍須以現行 evaluator 重算確認）

前一個實驗 `results/n8_state_assignment/` 的結果已通過獨立驗收：

| 量 | 值 |
|---|---|
| `N` / `M` | 8 / 17 |
| `num_states` | 131072 = 2^17（全部枚舉成功） |
| permutation 覆蓋 | 40320，100%，`missing = []` |
| realizations 總和 | 恰為 131072（無漏算無重複） |
| mean / median multiplicity | 3.250794 / 2 |
| **baseline global WIL (BFS)** | **5.181320181939005 dB** |
| **baseline GLOBAL_WIL_OPT_ASSIGN** | **5.181320181939005 dB（與 BFS 相同）** |
| median WIL BFS → OPT | 4.8644 → 4.6844 |
| p90 BFS → OPT | 5.0933 → 5.0776 |
| p99 BFS → OPT | 5.1813 → 5.1813（不動） |

**關鍵事實：state assignment 最佳化對 headline WIL 的改善精確為 0。**
原因是 max over π 被零自由度的排列釘死。

`WORST_BASELINE_SET`（1008 個）與 `UNIQUE_WORST_SET`（256 個）已萃取存於
`results/n8_state_assignment/worst_sets.json`，直接讀取即可，不要重算。

state → permutation LUT 位於 `results/n8_state_assignment/state_lut.csv`。
**若 transformation 為 geometry-only（logical `stage_pairs` 完全不變），
則該 LUT 與 permutation multiplicity 逐筆不變，可直接重用，只有 path loss 改變。**
這使每個 candidate 的 exact 評估不需重跑枚舉。需要 state-bit remapping 的
transformation 則須建立 deterministic remapping 並驗證後才能重用。

重建 baseline 時記錄：`stage_pairs`、fabric graph、cell placement、routed geometry、
crossings、total length、bends、routing runtime、route success、exact global WIL、
median / p90 / p99。

## 3. RECURSIVE WAKSMAN TREE

建立 explicit recursive representation，不要當成 flat stage-pair list。
每個 node 至少記：`node_id`、size、logical wire set、input-side SEs、output-side SEs、
upper child、lower child、stage range、physical wire y locations、目前選定的 embedding。

注意 logical wire set 可能是 `{0,2,4,6}` / `{1,3,5,7}` 這種非連續集合。

## 4. DISCOVER LEGAL EMBEDDING CHOICES

對每個 node 列出候選 local choices（normal / swap children / mirror / reverse
boundary ordering / flip SE orientation），**逐一驗證而非憑直覺**：

1. 建構 transformed logical/port-level fabric
2. 先在 **N=4** 窮舉全部 `4!` 驗證
3. 通過才驗 **N=6**
4. 再通過才在 **N=8** 啟用

合法條件：`∀π ∈ S_N`，存在合法 state assignment 實現 π。

僅改 physical coordinates / orientation 而 logical `stage_pairs` 完全不變者，
明確標為 **geometry-only transformation**（可重用 LUT，見 §2）。

## 5-6. DP STATE 與 BOUNDARY SIGNATURE

物理成本不因 subnet 尺寸相同而相同，**不要用 `DP[n][permutation]`**。
建議 `DP[node_id][boundary_signature]`，必要時加 `[subproblem_signature]`。

boundary signature 至少考慮：ordered input/output boundary wires、physical
top-to-bottom order、port facing direction、boundary 上的 upper/lower PSE role、
boundary port 朝向或背離 parent connection。**不要把完整 routed geometry 放進 key。**
報告中須明確回答：「哪些資訊必須保留，兩個 child solution 才能被安全視為等價？」

## 7. PRE-ROUTING PHYSICAL COST MODEL

DP 內不得跑 A*。對每條 fixed port-to-port connection `e` 計算並**分項輸出**
（不要只留一個加權純量）：stage span、vertical span `dy`、rectilinear lower bound
`L_lb`（須考慮 port direction）、port-facing / wrap penalty（source port 初始方向
背離 destination 時須為非零）、minimum bend count lower bound、
optional crossing lower bound（若無可靠下界則略過，不要硬湊）。

## 8. MULTIPLE DP OBJECTIVES

至少測 A 總估計邊成本、B 最大 logical-wire chain 成本、C 總 wrap penalty、
D 最大 stage-span 負擔、E 加權組合。

**Objective F 優先度最高**：若既有 path-space evaluator 能在 estimated edge
weights 上運作，則以 pre-routing proxy 當 edge cost，用既有 path-space + witness
機制算 proxy-WIL，因為它最接近真正的 headline WIL。

## 9-10. DP 與 PARETO

bottom-up；leaf W2 枚舉所有合法 embedding/orientation。較大 subnet 對每個合法
local choice 組合上下 child 的 DP entry、加 parent interconnect cost、
建新 signature、算 cost profile，再做 dominance pruning。

**minimax 目標下不得用純量比較丟棄 candidate。** A 支配 B 僅當所有相關分量
`A_i <= B_i` 且至少一項嚴格小於。記錄每個 DP state 的 Pareto set size
（per-state、max、mean）。**Pareto set 爆炸本身就是重要實驗結果。**

## 11-12. TOP-K 與 PHYSICAL VALIDATION

每個 objective 保留 top-K root candidates。每個 candidate 用**同一套 production
Waksman A\* router**、**不得逐 candidate 調參**，記錄 route success、crossings、
total length、bends、runtime。

### 繞線預算（本任務書新增的硬性限制）

waksman n8 單次 A* 繞線實測約 **16 分鐘**。原規格的 top-K=20 × 多 objective
加上 100 個 random，將超過 50 小時，不可行。因此**分三階段執行**：

- **Stage 1（純 proxy，不繞線）**：完成 §3–§10，跑滿所有 objective 與
  random 100 / greedy 的 **proxy** 評估。寫出 `dp_stats.csv`、proxy 排名、
  設計空間大小、Pareto 統計。此階段必須完整。
- **Stage 2（繞線，硬上限 24 個 candidate）**：依下列優先序挑選並繞線，
  **每完成一個就立刻把該列寫進 `summary.csv`**，不要全部跑完才寫：
  1. baseline（現行 embedding）— 必繞
  2. 各 objective 的 proxy 最佳各 1 個
  3. Objective F 的 top-5
  4. greedy 最佳 1 個
  5. random 中 proxy 最佳 3 個與 proxy 中位 3 個（用於檢驗 proxy 相關性）
  6. 額度未滿則補其他 objective 的次佳
- **Stage 3**：對已繞線的 candidate 做 §13 exact 評估與 §14 worst-set 分析。

若 Stage 2 的 16 分鐘估計嚴重失準（例如單次超過 40 分鐘），**停下回報**，
不要自行縮減 candidate 數以外的範圍。

## 13. EXACT STATE-ASSIGNMENT EVALUATION（headline metric）

每個已繞線 candidate 算兩個值：

- `WIL_BFS(candidate)`
- `GLOBAL_WIL_OPT_ASSIGN(candidate) = max_π min_{legal states} WIL(candidate, state, π)`

後者是**最重要的 headline metric**，回答「即使 state assignment 做到理論最佳，
這個 embedding 的 intrinsic worst-case 是多少」。可重用 §2 的 LUT。

## 14. SPECIAL FOCUS: 前一實驗的 worst-case set

對每個 candidate 重新評估 `WORST_BASELINE_SET`（1008）與
`UNIQUE_WORST_SET`（256），輸出：改善了幾個、平均改善、最大改善、
仍停留在 5.1813 dB 的有幾個、256 個零自由度案例改善了幾個及其新最大值。

**若 candidate 只改善 median 而這 256 個不動，則對 headline WIL 無價值。**

## 15-16. BASELINES 與 PRIMARY METRIC

比較：current embedding、random legal（proxy 階段 100 個）、greedy、
DP objectives A/B/C/F。Greedy 定義為每個 node 只選當下 local proxy 最小者。

**PRIMARY METRIC = `GLOBAL_WIL_OPT_ASSIGN`。**
成功的第一判準是它嚴格小於 baseline 的 **5.181320181939005 dB**。
**不要因為 median 漂亮就宣稱成功。**

## 17-19. 輸出

`results/n8_embedding_dp/summary.csv`（每列一 candidate）欄位依原規格：
`candidate_id, method, dp_objective, embedding_decisions, proxy_score,
route_success, routing_runtime, total_length_um, physical_crossings, bend_count,
wrap_count, global_wil_bfs_db, global_wil_opt_assignment_db, median_wil_db,
p90_wil_db, p99_wil_db, worst_permutation, worst_input,
baseline_worst_set_improved_count, unique_worst_set_improved_count`

`dp_stats.csv`：`node_id, subnet_size, boundary_signature, num_raw_candidates,
num_after_dominance, pareto_size, runtime`

圖：`proxy_score_vs_exact_global_WIL`、各方法的 exact WIL 分布、
Pareto-set size vs subnet size、baseline worst-set improvement histogram、
`crossing_count_vs_global_WIL`、`total_length_vs_global_WIL`。

## 20. ACCEPTANCE / FAILURE INTERPRETATION

結束後不要只說 DP works / doesn't work，請歸類：
**A** 顯著降低 global WIL → recursive physical-aware embedding 有前景；
**B** 只降 proxy/median 而 global WIL 不變 → proxy 未瞄準真正瓶頸，或
worst-case 結構性地固定在別處；
**C** proxy 對 exact WIL 預測力差 → pre-routing decomposition 不足，
A* 交互作用主導；
**D** Pareto set 爆炸 → exact DP 表示法不可擴展，考慮 branch-and-bound；
**E** greedy/random 追平 DP → DP 的複雜度不必要。

## 21. CORRECTNESS CHECKS

每個 candidate 必須：MRR/SE 數不變、保持 logical Waksman functionality、
N=8 RNB 驗證通過、40320 permutations 全部仍可實現、無 permutation 消失、
fixed fabric 維持 permutation-independent、無 per-permutation rerouting、
每個 candidate 只繞線一次、state 僅改變 MRR 內部 transition。
logical semantics 不同者一律丟棄。

## 22-23. FINAL REPORT

`results/n8_embedding_dp/REPORT.md`，回答原規格 22 節的十個問題。

**不要宣稱 novelty。** 先判斷是否真能改變 5.1813 dB。若 global WIL 不變，
必須直接說明原因並指出 remaining bottleneck 在哪。
