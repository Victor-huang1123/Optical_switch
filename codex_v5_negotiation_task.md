# Codex Task v5 — Rip-up / Negotiation Experiments (b) and (c)

> 中文摘要：目前 router 的 rip-up 已有 best-so-far（不會越繞越爛）與 blocker 拆線，但
> (b) 同 net 早期 hop 是**硬剪枝**、(c) 共享資源（他 net 線段、crossing 佔位、port 保留區）
> 也是**硬擋**——history cost 只能加價、無法疏通被剪掉的節點。本任務用兩個**可關閉的**實驗性
> 機制測試能否救回已知失敗案，並以數據決定是否採用。實驗 (a)（純 config 旋鈕）由人類端並行執行，
> 其結果會附在 §Results 供你參考。

<context>
Repo: /home/jchuang/Optical_switch。先讀 PROJECT_OVERVIEW_FOR_DISCUSSION.md（全專案脈絡）與
ARCHITECTURE.md。核心程式：
- routing/grid.py:_astar_route（單 hop A*；狀態 = RouterState(x,y,orientation,straight_run,crossing_arm)
  + crossing 簽名；九道合法性硬剪枝 @ grid.py:472-560，每道有計數器）
- routing/grid_router.py:neighbor_moves（四方向；禁回頭 / 2R 彎距 / crossing 臂長三道生成期剪枝）
- routing/physical.py:270-400（rip-up 主迴圈：best-so-far 已實作、_bounded_reroute_inputs 會拆
  blocker、ripup_route_budget=4、ripup_order_candidate_limit=1）
- routing/grid_router.py:HistoryCost（衝突加價，只作用於成本，無法疏通硬剪枝）

三類已知失敗（計數器簽名可精確分類，勿混淆）：
| 類型 | 案例 | 主導計數器 | 本質 |
|---|---|---|---|
| 自我圍困 | waksman n10-C, n11-C, n12-C | same_net:63-1536, pops 極低 | 同 net 早期 hop 擋死後期 hop |
| 外部保留區 | waksman n8-C (v3 幾何) | port_access 主導, frontier 窮盡 | 他 net 的 port runway 保留區 |
| 真實競爭 | waksman n8-B (v4, crossing 佔位開啟) | segment:4, pops:4, max_heap:3 | 他 net 幾何 + crossing 佔位擋死 |
</context>

<experiment_b>
**(b) 同 net 早期 hop 從硬剪枝改為可協商**

現況：A* 在展開時對「與同 net 早期 hop 衝突」的候選段落直接 `continue`
（grid.py:485-496 `_same_net_self_conflict_reason`）。早期 hop 已提交，後期 hop 只能撞牆。
v2 曾加 whole-net riser displacement ladder（±1/±2 軌），但候選常落在 hop routing window 外
而失效（n10-C 實測超窗 51 µm，n11-C 同樣 51 µm——這個常數說明是確定性幾何，不是隨機壅塞）。

實作要求（全部 config-gated，預設關閉，golden 必須 bit-identical）：
1. 新增 `same_net_negotiable: bool = False`。開啟時，A* 遇到「與**同 net 早期 hop**衝突」
   不再 `continue`，而是：(i) 記錄該候選需要拆掉哪個早期 hop（by hop index），
   (ii) 以 `same_net_conflict_penalty_um`（新欄位，預設 = hairpin_penalty_um）計價後照常展開。
   注意：與**本 hop 已走過的 partial path** 衝突（自我重疊）仍必須硬擋——那是無效幾何。
2. 若最終路徑帶有「需拆早期 hop」的標記，上層 `_route_physical_order` 必須：
   拆掉被標記的早期 hop、以新的幾何約束重繞它們（whole-net 局部重繞），
   全部成功才接受；否則退回原解（best-so-far 保護）。
3. 搜尋窗修正（v2 遺留缺陷）：位移/重繞候選的 RoutingWindow 必須**覆蓋候選線段全長**，
   而非「位移量 + 2 軌」——n10-C 只差 51 µm 即為此缺陷。

驗收（每項都要給數據）：
- G-b1: waksman n10-C 在 campaign 旗標 + `same_net_negotiable=True` 下 0 failed edges、
  legacy DRC = 0、bend_radius_legality = 0。（若仍失敗，回報新的計數器簽名。）
- G-b2: n11-C、n12-C 同上（同型失敗，預期同樣被修好或同樣失敗——兩者都是有價值的資訊）。
- G-b3: 對照組：waksman n4/n6/n8 B 與 C 在開啟後**不得退步**（failed edges 不增、
  WIL drift 記錄；WIL 若退步 > 0.05 dB 要說明原因）。
- G-b4: 預設關閉時三族 golden hash + G1-G7 + 全 pytest 全綠。
</experiment_b>

<experiment_c>
**(c) 協商式繞線（PathFinder 式軟化共享資源）**

現況：共享資源（他 net 線段、crossing 佔位、port 保留區）是二元硬擋，
`HistoryCost` 只加成本無法疏通（v4 n8-B 的 `pops:4` 即為證據：搜尋 4 步就被剪光）。

實作要求（config-gated，預設關閉）：
1. 新增 `negotiated_routing: bool = False` + `negotiation_iterations: int = 8`。
   開啟時走新的外層迴圈（**取代** rip-up 迴圈，不是疊加）：
   ```
   for k in 1..K:
       price_k(resource) = base_cost + present_overuse × alpha_k + history × beta
       alpha_k 遞增（建議 alpha_k = alpha_0 × growth^k，growth ≈ 1.5-2）
       所有 net 全部重繞（不只失敗的），順序可輪替
       允許暫時超額佔用共享資源（違法但計價）
       若無資源超額 → 收斂，輸出
   若達 K 未收斂 → 回報價格曲線（每輪的 overuse 總數），標 route_failed
   ```
2. **可軟化的資源**（設計選擇，非物理不可能）：他 net 線段的間距、crossing 佔位、
   port 保留區。**絕不可軟化**：同 net 重疊 / 波導交疊 / 2R 彎距 / DRC 硬規則——
   那些是無效幾何，軟化後收斂時也救不回來。請在程式碼中明確分成兩類並加註解。
3. 收斂判定與最終合法性：收斂後必須跑完整 `validate_physical_routes`，
   任何違規 = 未收斂（不得輸出違法幾何）。

驗收：
- G-c1: waksman n8-B（v4 旗標 + crossing 佔位）在 `negotiated_routing=True` 下 0 failed edges。
  這是最該被修好的案子（真實資源競爭）。
- G-c2: 回報每個測試案的價格曲線（每輪 overuse 總數）與收斂輪次；未收斂的案子要附曲線。
- G-c3: 對照組 n4/n6 不得退步；WIL drift 記錄。
- G-c4: 預設關閉時 golden 全綠。
- G-c5: 成本紀錄：每案 wall-clock 與 A* 呼叫數 vs baseline（預期 5-20×，需量化）。
</experiment_c>

<execution_order>
1. 先做 (b)——它針對的 n10/11/12-C 是三個案子，且有 v2 的位移梯度基礎可改。
2. 再做 (c)——架構改動較大；(b) 的結果會告訴你「自我圍困」是否已解決，
   讓 (c) 可以專注在真實競爭上。
3. 兩者都完成後，寫 `outputs/v5_negotiation/REPORT.md`：
   三類失敗 × 三種機制（(a) config / (b) 同 net 協商 / (c) 完整協商）的**成敗矩陣**，
   每格附 failed_edges、WIL、wall-clock。這張矩陣是本任務的主要交付物——
   它會決定哪個機制進入正式管線。
</execution_order>

<action_safety>
- 所有新機制 config-gated 且預設關閉；預設旗標下的行為必須 bit-identical（每個 phase 後驗
  三族 golden hash + G1-G7 + 全 pytest）。
- 不得修改 outputs/nsweep_fixed_fabric_v2、v3_mini、v4_mini_gate（唯讀歷史）。
  新產物一律在 outputs/v5_negotiation/。
- 不要 git commit；改動保持 unstaged 並列出。
- 任何閘門失敗 → 停下並原文回報計數器簽名（不要自行更換機制）。
- 開工前先 `ps` 確認沒有 campaign / 實驗腳本在跑（人類端可能正在跑實驗 (a)）。
</action_safety>
