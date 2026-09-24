# Codex Task — Loss-Aware State Assignment at N = 8

> 目的：量化「狀態指派若允許在合法組態中挑損耗最低者，worst-case insertion
> loss 能改善多少」，並比較 padded Beneš 與 Waksman 各自的改善幅度。
> 這是 analysis-only 的量測任務，不修改既有模組、不動 golden、不啟動 campaign。

## Background

目前的流程對給定排列 `π` 只產生**一組**狀態指派（`BenesLoopingStrategy` /
`WaksmanStrategy` 皆為確定性且對損耗無感），worst-case insertion loss 因此是

```
WIL_det = max_π  IL_worst( π , A_det(π) )
```

但同一個 `π` 通常有多組合法狀態指派。repo 既有筆記記載「確定性策略只用路徑
子集」，以 Beneš n8 用到 256 條路徑中的 80 條為證據。若允許挑最好的一組：

```
WIL_opt = max_π  min_{A ∈ Valid(π)}  IL_worst( π , A )
```

`WIL_opt ≤ WIL_det`，差額即確定性策略放棄掉的餘裕。

**待檢驗的預測（請以資料反駁或支持，不要預設成立）：** Beneš 的自由度較高
（Waksman 的遞迴會把部分變數釘死），因此 Beneš 的改善應大於 Waksman，
亦即此實驗會**擴大**而非縮小兩者差距。

平均而言 `|Valid(π)| = 2^C / N!`：Beneš P=8 為 `2^20 / 8! ≈ 26.0`，
Waksman N=8 為 `2^17 / 8! ≈ 3.25`，比值恰為 `2^3 = 8`。請實測其分布。

## Method

> **v2 修正（2026-09-20）：本任務書第一版要求比對 v2 幾何雜湊，那是錯的。**
> v2 產物是以 `LEGACY_V2_CELL_GEOMETRY`（`port_dy=4.0`、`waveguide_width=0.0`）
> 建的，現行預設為 `V3_CELL_GEOMETRY`（`5.5` / `0.45`），兩者不可能相同。
> 前一次執行正確地偵測到此不一致並停止回報，其重建結果本身無誤。
> **基準改為 v3-mini。**

1. **固定 fabric。** 以現行程式碼與預設幾何重建三個 case，
   envelope 用 `octave_envelope(8)`，`padded_benes` 走 template lane、
   `waksman` 走 astar lane。請直接呼叫
   `mrr_switch_optimizer.app.nsweep_campaign._route_case`，並傳入：

   ```
   pops_ladder=(30000,)
   min_crossing_clearance_um=10.0
   straighten_jogs=True
   physical_turn_guard=False
   v4_search=False
   output_root=Path("outputs/loss_aware_states_n8")
   campaign_version="loss-aware-n8"
   ```

   把 `output_root` 指到本任務自己的輸出目錄，`_route_case` 會把繞線結果
   快取在該處；waksman 首次重繞約需 16 分鐘，之後的執行會直接載入快取。

   **驗收條件（v3 修正）：**

   | case | 檢查 |
   |---|---|
   | `padded_benes` / 兩個配置 | 幾何雜湊必須為 `c5ad40e124038391…`，且 `WIL_det` 必須逐位元等於 4.574819091965323（B）與 2.4876633792340335（C） |
   | `waksman` / `B_db_placeholder` | **不做雜湊比對**，理由見下。改為要求 `failed_edges == 0`、legacy DRC 與 bend-radius 違規皆為 0 |

   > **為何 waksman 不比對雜湊。** v3-mini 的 waksman 產物生成於 2026-08-31 20:38，
   > 而工作區中 `routing/grid.py`、`physical.py`、`crossing.py`、`grid_router.py`、
   > `route_grid.py`、`types.py`、`fabric.py`、`port_access.py` 等 A* 核心檔案
   > 均在 2026-09-01 至 09-02 之間被修改且尚未 commit。因此現行程式碼**無法**
   > 重現當時的 A* 幾何，前兩次執行得到的 `5a67bc16…` 是現行程式碼下的正確結果。
   > template lane 不受影響（`benes_template_layout.py` 改於 v3-mini 之前），
   > 故 padded_benes 仍須比對。

2. **範圍與內部一致性。** `waksman` / `C_db_realistic` 在此幾何下為
   `route_failed`，不在範圍內，不要嘗試讓它路通。因此頭對頭比較在
   `B_db_placeholder` 進行，`C_db_realistic` 只有 `padded_benes` 一筆。

   由於 waksman 少了外部參照，請以**內部一致性**取代之：你自行計算的
   `WIL_det` 必須與 repo 既有的
   `analysis.nsweep.evaluate_fixed_fabric_path_space` 在**同一張 fabric** 上
   的結果逐位元相同。不同則停止回報。本實驗的效度只要求
   `WIL_det` 與 `WIL_opt` 來自同一張 fabric，不要求該 fabric 等於 v3-mini。

3. **預先計算路徑損耗。** fabric 固定 ⇒ 每條 fabric path 的物理損耗固定。
   先枚舉全部路徑建 `path → IL` 查表（可參考
   `analysis/nsweep.py:evaluate_fixed_fabric_path_space` 的既有作法），
   之後每組狀態指派只需查表取其作用路徑的最大值。

4. **枚舉全部合法狀態指派。** 枚舉 `2^C` 種狀態組合
   （Beneš `2^20 = 1,048,576`、Waksman `2^17 = 131,072`），
   以 `realize_state_assignment` 求其實現的排列，依排列分組得 `Valid(π)`。
   `BruteForceLUTStrategy` 用 `setdefault` 只保留第一組，本任務需要**全部**，
   請在 `scripts/` 下另寫，不要改動 `core/`。

5. **計算兩個指標。** 對每個 `π` 取 `min` 得該排列的最佳可達值，
   再對所有 `π` 取 `max` 得 `WIL_opt`；`WIL_det` 以現行確定性策略計算。

## Deliverables

寫入 `outputs/loss_aware_states_n8/`：

- `results.json` — 每個 (topology, config) 一筆（共三筆，見 Method 第 2 點），至少含
  `wil_det`、`wil_opt`、`delta_db`、`valid_counts`（mean / min / max / 分布摘要）、
  `paths_used_det`（確定性策略實際用到的相異路徑數）、`paths_total`、
  以及 worst-case 對應的 witness 排列。
- `REPORT.md` — 一頁結論，含兩個拓撲的對照表、對上述預測的判定
  （支持／反駁／不確定，附數據）、以及任何發現的異常。

腳本放 `scripts/loss_aware_states_n8.py`。

## Action safety

- 不修改 `mrr_switch_optimizer/` 下任何檔案；新程式一律放 `scripts/`。
- 不改動任何 golden 或 `tests/`。
- 不啟動 campaign、不跑長時間繞線；本任務的繞線只有 n8 兩個 case。
- 幾何雜湊與 v2 不符時**停止並回報**，不要自行調整參數硬湊。
- 若某個枚舉的成本超出預期（例如 Beneš 的 1M 組合在你的實作下超過 30 分鐘），
  先回報實測耗時與瓶頸，再提出抽樣方案，不要逕自改成抽樣。
