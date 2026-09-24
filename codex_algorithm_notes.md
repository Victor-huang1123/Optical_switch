ㄌ# Algorithm Notes for the N-Sweep Campaign — Discussion Handoff
(整理自 2026-08 系列討論，供與 Codex 討論實作細節用；配套規格見 codex_nsweep_task.md)

## 0. 討論脈絡（一段話）
Fixed-fabric 審查證實架構健全但 headline IL 非實體預測 → routing upgrade（dB 成本統一、
bend 2R 合法化、port runway、新 DRC）→ n8 實測 Waksman vs padded Beneš 勝負隨 crossing
係數翻轉 → N=3~12 全面戰役設計 → 可行性探測發現 N! 爆炸與 benes-16 繞線失敗 → 解法：
平行全枚舉 + 路徑枚舉雙法交叉驗證、確定性 template 佈局、八度 envelope 公平化、
corridor guides。論文主敘事：MRR 數量優勢 + N×N 擴展性 + WIL parity（非 WIL 更低）。

---

## 1. 演算法細節

### 1.1 平行全枚舉（exact worst-IL + RNB coverage，單趟）
- 把 N! 依「前兩個位置」切成 N(N-1) 個 chunk；每 worker 對 chunk 內每個 permutation：
  (a) states = strategy.assign(perm)；verify_state_assignment（wire-swap 模擬 == 目標排列）
      + 每 hop ∈ 固定 fabric 有向 edge 集合 → coverage；
  (b) 對 N 條 active path 用 _evaluate_path_loss 算 IL，記 chunk 局部 argmax。
- max-reduce 全 chunk；tie-break 用字典序最小 permutation（決定性）。
- 實測吞吐 ~47k path-evals/s/core；n12 = 5.75G path-evals → 64 workers ≈ 45-90 min。
- 正確性閘門：n8 平行結果必須 float-equal 於單程序 evaluate_fixed_fabric_worst_insertion_loss。

### 1.2 路徑枚舉法（strategy-relative exact worst-IL，免 N!）
- 路徑枚舉：fabric 是 DAG；從每個 I_i 出發，每進一顆 MRR（經 in 或 add）分支兩個出口
  （th=bar / drop=cross），沿唯一 outgoing edge 走到某 O_j。路徑總數與 N! 無關
  （n8: 176、n10: 436——Beneš 類網路每對 (i,o) 的路徑數 ~N/2，經典結果）。
- 每條路徑用同一個 _evaluate_path_loss 算 IL（不得重新實作 loss model）。
- 可實現性（關鍵細節）：worst IL 在本 codebase 是「策略相對」的——決定性策略只會產生
  路徑空間的子集（實測 Beneš n8 有 80/256 條圖上存在但策略永不使用的路徑，裸 path-max
  高估 0.063 dB）。認證程序：由 IL 高到低，對每條候選找 witness permutation
  （先隨機快篩，再決定性回溯窮盡部分排列補全——回溯終止即給出可實現/不可實現的
  確定verdict；禁止「隨機 5000 次沒找到」當作證明）。
- 正確性引理（見 §2 表，需在論文附錄補 2 行證明）：
  max over permutations = max over realizable paths。
  證明骨架：⊆ 每個 permutation 的每條 active path 依定義 realizable；
  ⊇ 每條 realizable path 依定義存在 witness permutation 包含之。∎
- 閘門：waksman n8 = 176 條全 realizable、max 4.575739 == 窮舉；
  benes n8 = 80 條 proven-unrealizable、realizable-max 4.749987 == 窮舉；
  waksman n10 certified worst 5.892242（witness 存 certificate）。
- 兩法對每個案子都跑，method_agreement.csv 要求 100% 吻合，不吻合即停。

### 1.3 確定性 Beneš template 佈局（power-of-2，n_physical ∈ {4,8,16}）
- 每 stage 一欄（文獻慣例：Lu 16×16 = 7 欄 × 56 cell）；線永不換軌
  （stage_permutations 為空，butterfly 編碼在 pair 距離裡）。
- Add-drop wrap（convention A，knot-free）：上線 in→th 直通 y=c+4；下線
  inbound 走南軌 y=c−h_s 進 add（cell 右側）、outbound 從 drop（左側）升到北軌
  y=c+h_n 越過 cell——強制產生 1 個 wrap crossing/cell。offset 規則保證所有 riser ≥ 2R。
- Channel 軌道指派（單 riser 版 swap-sort）：每 channel 每線一個垂直 riser 軌；
  rising nets 按出發 y 降序、falling 按升序排列 → 交叉恰為 inversion 對、各一次。
  同 y 出發/到達的碰撞用 topo-sort 合法化，環用 +2 軌 dogleg（決定性）。
- Crossing 數模型（本 session 以兩種獨立推導機器驗證）：
  T(N) = Σ channel inversions + 2·n_MRR = 14 / 60 / 232（benes-4/8/16），
  vs 教科書 X(N)=(N/2)(N−log₂N−1) = 2/16/88——差額是 add-drop wrap 的物理代價。
  此三數為 G1 硬閘門。
- stage_pitch 公式：2·ceil8(cell半寬+keepout+escape+runway+2R) + (P−1)·8
  → 以真實 _routing_obstacle BBox(28,22) 計：232/168/136 µm（benes-16/8/4）。
- Padding：同八度 N 共用同一版圖（geometry hash 必須一致，G6）；astar_calls == 0（G4）；
  產出後仍過完整 DRC（G2）——template 不自證，DRC 是仲裁者。

### 1.4 八度 envelope 公平規則
- N∈(2,4]→benes-4 畫布、(4,8]→benes-8、(8,16]→benes-16；同八度所有案子
  （兩拓撲 × 全 config）共用同一 OctaveEnvelope（x/y 範圍、stage/wire pitch、margin 全釘死）。
- Waksman stage 數 = 2⌈log₂N⌉−1 與同八度 padded Beneš 相同 → 欄位 x 自然對齊。
- 幾何旋鈕禁止逐案升級；搜尋旋鈕（pops 30k/100k/300k、guide 模式）可升但逐案記錄。
- 每列 metrics 蓋 envelope_id；圖表在 N=4→5、8→9 畫八度邊界；v1(140-pitch) 與 v2 禁止混圖。

### 1.5 Corridor guides（Waksman A* 案專用）
- 每 (net, stage-channel) 預指派 y-band + preferred riser x 軌（用 §1.3 的指派演算法
  跑在 Waksman channel map 上）；soft = 出band長度罰項 + preferred_bend_x 偏好，
  hard = 與 hop RoutingWindow 交集；逐 hop 降級 hard→soft→off，絕不因 guide 失敗。
- G7：guide 開啟後 0 failed edges 且 crossing 不得多於 A* baseline。
- 動機證據：pb9 braided-pair 餓死（走廊容量事前不可見）、waksman n8-C crossing 70→92。

### 1.6 RNB 三層保證
1. 建構性定理：WaksmanStrategy = Waksman 1968 遞迴 2-coloring（含被移除 switch 的
   強制位元）；BenesLoopingStrategy = 經典 looping。矛盾即 raise。
2. 全排列窮舉驗證：§1.1 的 coverage 與 IL 共用同趟平行枚舉，n9~12 全部逐一驗
   （n12 = 4.79 億個排列，~92 µs/perm）；coverage failures == 0 為硬閘門。
3. Witness 憑證：每案 worst_il_certificate.json 存 witness permutation，
   一步可重驗（--verify-certificates）。

---

## 2. 理論出處表（哪些前人已證、哪些是我們的構造）

| 構件 | 出處狀態 | 引用 / 需補證明 |
|---|---|---|
| Beneš 可重排非阻塞 + looping 演算法 | 前人已證 | Beneš 1962; Opferman & Tsao-Wu 1971 |
| Waksman 網路、W(n) 最優性、2log₂N−1 深度最優 | 前人已證 | Waksman 1968（JACM）；AS-Waksman: Beauquier & Darrot |
| Channel crossing 下界 = permutation inversions；swap-sort 達成下界 | 前人已證 | Condrat, Kalla & Blair, SLIP 2013；odd-even transposition sort: Habermann 1972 / Knuth TAOCP v3 |
| Beneš 每對 (i,o) 路徑數 ~N/2（路徑空間小） | 前人已證（經典計數） | 標準 switching theory 結果 |
| worst-path 組合公式（如 worst crossings 2N−2log₂N−2） | 前人已證 | US10616670；Lu et al. 2016 |
| 平行 max-reduce 窮舉正確性 | 初等（無需引用） | 分割窮舉 + 結合律 |
| **引理：max over perms == max over realizable paths** | **我們的（trivial 但需明寫）** | 論文附錄 2 行證明（§1.2） |
| **策略相對性觀察（決定性策略只用路徑子集）** | **我們的（實證 + 易證）** | Beneš n8 80/256 為證據；對特定策略可形式化 |
| **witness 回溯 = realizability 決定程序** | **我們的（工程構造）** | 完備性 = 窮盡補全搜尋；正確性 = 建構式 |
| **T(N) = inversions + 2·n_MRR（add-drop wrap 嵌入的 crossing 模型）** | **我們的（雙推導 + 機器驗證）** | 論文附錄給推導；G1 閘門為機器證據 |
| **單 riser 排序規則的 inversion-最優性** | **我們的（Condrat 的特化）** | 已 case-check；論文給短證明 |
| 八度 envelope 公平法、corridor guides | 我們的（方法論/工程） | 無需定理；寫清楚規則即可 |

**回答「複雜度爆炸的解法是前人證過的嗎」**：三個成分三種答案——
(1) 平行窮舉：初等，無需前人；(2) 路徑空間縮減：**支撐磚塊全是前人已證**
（路徑計數、worst-path 分析是光交換文獻的標準做法），但「策略相對的精確版 +
witness 憑證」這個組合是我們的構造，核心引理 trivial、需在論文明寫；
(3) template 佈局：swap-sort 最優性是 SLIP 2013 已證，add-drop wrap 的 T(N) 修正是我們的。

---

## 3. 與 Codex 討論時的開放問題
1. 回溯 witness search 的剪枝設計：能否利用策略遞迴結構 memoize（子排列可行性）？
   最壞情況複雜度界？（實務上 n10 rank-7 兩次嘗試即中，但需要 worst-case 保底）
2. 平行枚舉的 IPC 開銷：chunk 大小 vs pickle 成本；建議 fork + 唯讀共享 fabric 結構。
3. G6 的 hash 一致性 vs blocked_ports 影響 endpoint flags——確認 hash 只蓋幾何不蓋 flags。
4. template 的同 y 合法化 dogleg 是否改變 T(N)？（設計要求：任何 dogleg 必須反映在
   crossing 數 delta 並讓 G1 失敗，不得靜默漂移）
5. n6 策略釘死（constructive everywhere）後與舊 LUT 產物的差異記錄方式。
