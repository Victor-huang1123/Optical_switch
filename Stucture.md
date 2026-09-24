# AI-assistant Code Structure Review

> 實際分析路徑：`/home/jchuang/Optical_switch`  
> 原指定的 `/home/jchuang/Optical_routing` 不存在；已依你確認改分析 `Optical_switch`。  
> 本次只做唯讀分析：沒有修改、刪除、安裝套件，也沒有執行會改變專案狀態的程式。  
> 讀取限制：已讀最多 10 個關鍵檔案；大型檔案只讀入口與主要流程片段，另外用唯讀的定義摘要與搜尋輔助判斷。

---

## 1. Project Structure

主要目錄與檔案如下：

```text
/home/jchuang/Optical_switch/
├── pyproject.toml
├── main.py
├── README.md
├── mrr_switch_prompt.md
├── mrr_switch_optimizer/
│   ├── __init__.py
│   ├── Lidar.md
│   ├── Schedule.md
│   ├── app/
│   │   └── cli.py
│   ├── core/
│   │   ├── models.py
│   │   ├── sparams.py
│   │   └── topology.py
│   ├── placement/
│   │   ├── layout.py
│   │   ├── lp.py
│   │   └── sa.py
│   ├── analysis/
│   │   ├── activity.py
│   │   ├── cost.py
│   │   ├── surrogate.py
│   │   └── task11_probe_artifacts.py
│   ├── routing/
│   │   ├── crossing.py
│   │   ├── drc.py
│   │   ├── geometry.py
│   │   ├── grid.py
│   │   ├── grid_router.py
│   │   ├── physical.py
│   │   ├── port_access.py
│   │   ├── refinement.py
│   │   ├── route_grid.py
│   │   └── types.py
│   └── output/
│       └── visualize.py
├── tests/
│   └── test_physical_router.py
├── Physical_output/
│   ├── *.png
│   ├── Prompt.md
│   ├── draw_basic_mrr.py
│   ├── layouts/
│   ├── output/
│   └── physical_eval_braid_fix/
└── skills/
    └── mrr-switch-optimizer-dev/
        └── SKILL.md
```

### 根目錄

- `pyproject.toml`  
  Python package 設定檔。定義 package 名稱 `mrr-switch-optimizer`、Python 版本需求、dependencies、dev dependencies、console script `mrr-switch`。

- `main.py`  
  很薄的啟動檔，內容只是：
  ```python
  from mrr_switch_optimizer.app.cli import main
  if __name__ == "__main__":
      main()
  ```
  所以真正的入口在 `mrr_switch_optimizer/app/cli.py`。

- `README.md`  
  目前檔案行數為 0，等於沒有專案說明文件。

- `mrr_switch_prompt.md`  
  看起來是 prompt / 專案說明類文件。本次沒有讀取內容，因此不推測細節。

### `mrr_switch_optimizer/app/`

- `cli.py`  
  主要 CLI orchestration 檔案。負責：
  - 解析 CLI 參數
  - 載入 S-parameter table
  - 選擇 topology
  - 執行 routing / placement / evaluation / physical routing
  - 輸出 PNG、GIF、CSV、JSON 等報告

### `mrr_switch_optimizer/core/`

- `models.py`  
  核心資料模型，例如：
  - `PortDef`
  - `BBox`
  - `MRRCell`
  - add-drop MRR 預設 port 幾何
  - wire 與 MRR port 的映射
  - MRR state 判斷：`0 = bar/through`，`1 = cross/drop`

- `topology.py`  
  定義 permutation network / routing topology，包括：
  - `RNBTopology`
  - `PaddedBenesTopology`
  - `SpankeBenesTopology`
  - `WaksmanTopology`
  - `RouteStep`
  - `Path`

  這是邏輯拓撲層，負責從 permutation 推導每條 input-to-output path 經過哪些 MRR、每個 MRR 的 state 是什麼。

- `sparams.py`  
  由 `cli.py` 引用 `load_mrr_s_table()`，測試中也引用 `MOCK_S_TABLE`。本次未讀內容，因此只能確認它負責 S-parameter table 載入與測試 mock 資料。

### `mrr_switch_optimizer/placement/`

- `layout.py`  
  負責把 topology 轉成實體 MRR cell placement。主要函式：
  - `wire_y()`：根據 wire index 與 pitch 計算 y 座標
  - `build_cells()`：根據 topology 與 s-table 建立 `dict[str, MRRCell]`

- `sa.py`  
  從定義摘要看，是 simulated annealing placement 模組。

- `lp.py`  
  從定義摘要與 import 看，是 linear programming placement 模組，使用 `scipy.optimize.linprog`。

### `mrr_switch_optimizer/analysis/`

- `cost.py`  
  Routing metric / insertion loss / SXR / cost aggregation 相關。

- `activity.py`  
  掃描 MRR activity，統計不同 permutation 下 MRR 使用狀態。

- `surrogate.py`  
  analytic surrogate feature / cost 模型。

- `task11_probe_artifacts.py`  
  有 `main()`，看起來是產生 Task 11 probe artifact / debug report 的輔助腳本。

### `mrr_switch_optimizer/routing/`

Routing 是本專案最大的部分。

- `types.py`  
  Routing 相關 dataclass 與型別集中處，例如：
  - `Point`
  - `Segment`
  - `Obstacle`
  - `RoutingWindow`
  - `PhysicalRoute`
  - `RoutingRules`
  - `DRCViolation`
  - `FailedNet`
  - `RouteCrossing`
  - `PhysicalRoutingResult`

- `physical.py`  
  實體 waveguide routing 主模組，非常大，約 5329 行。主要公開函式：
  - `route_physical_paths()`
  - `route_physical_design()`

  功能包含：routing order、rip-up retry、crossing source tracking、failure analysis、candidate route、DRC validation 整合等。

- `geometry.py`  
  幾何工具，例如 segment intersection、Manhattan route、polyline length、bend count、obstacle 判斷等。

- `grid.py` / `grid_router.py` / `route_grid.py`  
  grid-based routing、A* route、grid node occupancy、history cost、neighbor moves 等。

- `drc.py`  
  Design rule check，檢查 physical routes 是否違反 spacing、obstacle、bend 等規則。

- `crossing.py`  
  crossing legality / crossing budget / crossing candidate 判斷。

- `port_access.py`  
  MRR port access region / stub / escape segment / owner-aware access 判斷。

- `refinement.py`  
  route crossing count、candidate score、route refinement 類功能。

### `mrr_switch_optimizer/output/`

- `visualize.py`  
  使用 matplotlib 產生：
  - routing PNG
  - routing GIF
  - IL/SXR CDF 圖

### `tests/`

- `test_physical_router.py`  
  單一大型測試檔，約 2980 行。測試範圍很廣，包含：
  - add-drop port 幾何
  - topology route step validity
  - port access plan
  - corridor / crossing / DRC / routing behavior
  - physical router helpers

### `Physical_output/`

這裡看起來主要是已產生的圖檔與實驗輸出，不是核心 source code。

包含：
- routing PNG debug 圖
- layout PNG
- physical eval CSV / JSON
- `drc_violations.csv`
- `physical_summary.csv`
- `physical_path_distribution.csv`
- `routing_summary.json`
- Task 10 / Task 11 相關 debug artifact

---

## 2. Tech Stack

根據 `pyproject.toml` 與實際 import 搜尋，技術堆疊如下：

### 語言與 package

- Python `>=3.11`
- Python package 名稱：`mrr-switch-optimizer`
- build backend：`setuptools.build_meta:__legacy__`
- console script：
  ```toml
  [project.scripts]
  mrr-switch = "mrr_switch_optimizer.app.cli:main"
  ```

### 宣告的 runtime dependencies

`pyproject.toml` 宣告：

- `matplotlib>=3.7`
- `numpy>=1.24`
- `PyYAML>=6.0`

### 宣告的 dev dependencies

- `pytest>=7`
- `mypy>=1.5`

### 型別檢查設定

`pyproject.toml` 設定：

```toml
[tool.mypy]
strict = true
ignore_missing_imports = false
```

表示專案目標是嚴格型別檢查。

### 實際程式中看到的第三方套件

- `matplotlib`：圖像、GIF、測試使用 Agg backend
- `numpy`：LP placement 或數值運算
- `pytest`：測試
- `scipy.optimize.linprog`：`placement/lp.py` 有使用，但 `pyproject.toml` 沒有宣告 `scipy`

### 可能的 dependency 狀態

- `scipy` 被程式 import，但沒有出現在 `pyproject.toml` dependencies。這可能導致安裝後使用 `--lp` 時出錯。
- `PyYAML` 有被宣告，但這次搜尋未看到明顯 `yaml` import；可能是預留或已不再使用。需要讀更多檔案才能確認。
` 被程式 import，但沒有出現在 `pyproject.toml` dependencies。這可能導致安裝後使用 `--lp` 時出錯。
- `PyYAML` 有被宣告，但這次搜尋未看到明顯 `yaml` import；可能是預留或已不再使用。需要讀更多檔案才能確認。
---

## 3. Entry Points

### 主要入口

#### 1. `main.py`

可透過：

```bash
python main.py
```

概念上啟動，但我沒有實際執行。

它只是轉呼叫：

```python
mrr_switch_optimizer.app.cli.main()
```

#### 2. console script：`mrr-switch`

`pyproject.toml` 定義：

```toml
mrr-switch = "mrr_switch_optimizer.app.cli:main"
```

如果 package 被安裝，理論上可用：

```bash
mrr-switch ...
```

但我沒有安裝，也沒有執行。

#### 3. `mrr_switch_optimizer/app/cli.py:main()`

這是實際主要入口。

主要 CLI input：

- `--permutation`  
  預設：`2,0,5,1,3,4`  
  意義：input 0..5 對應到哪個 logical output。

- `--topology`  
  choices：`main`, `all`, `benes`, `waksman`, `sb`  
  預設：`main`  
  意義：選擇要比較或輸出的 topology。

- `--sparam-dir`  
  預設使用：`project_root / "mrr_sparam_library"`  
  意義：MRR S-parameter library 目錄。

- `--outdir`  
  預設：`outputs`  
  意義：輸出 PNG / GIF / CSV / JSON 的目錄。

- S-parameter 選擇：
  - `--radius-um`
  - `--channel-nm`
  - `--wavelength-nm`

- placement / evaluation flag：
  - `--gif`
  - `--sa`
  - `--lp`
  - `--eval`
  - `--activity-scan`
  - `--paper-table`
  - `--plot-distributions`
  - `--breakeven`
  - `--analytic-edges`
  - `--physical-eval`

- physical routing rule input：
  - `--grid-pitch-um`
  - `--grid-merge-tol-um`
  - `--min-spacing-um`
  - `--mrr-keepout-um`
  - `--port-escape-um`
  - `--crossing-penalty-um`
  - `--turn-guard-um`
  - `--turn-timing-penalty-um`
  - `--backtrack-penalty-um`
  - `--hairpin-penalty-um`
  - `--loss-aware-cost`
  - loss 相關參數
  - `--max-astar-pops`
  - `--ripup-max-astar-pops`
  - `--max-ripup-passes`
  - `--physical-debug-overlay`

### 其他可能入口

- `mrr_switch_optimizer/analysis/task11_probe_artifacts.py:main()`  
  從定義摘要看是一個 artifact 產生腳本，但本次未讀完整內容。

- `Physical_output/draw_basic_mrr.py`  
  看起來是 standalone drawing script，但在 artifact 目錄中。本次未讀內容。

---

## 4. Main Modules

### 4.1 CLI / orchestration

檔案：`mrr_switch_optimizer/app/cli.py`

負責把各模組串起來。

Input：
- CLI args
- permutation
- topology selector
- sparam dir
- output dir
- routing / placement / physical eval 參數

Output：
- PNG / GIF routing visualization
- states CSV
- summary JSON
- comparison CSV
- optional SA / LP / eval / physical eval outputs

---

### 4.2 Core topology

檔案：`mrr_switch_optimizer/core/topology.py`

負責：
- 定義 topology
- 建立 permutation-to-state lookup table
- 驗證 topology 是否 rearrangeably non-blocking
- 根據 permutation 產生 active paths

Input：
- permutation，例如 `(2, 0, 5, 1, 3, 4)`
- topology class，例如 `PaddedBenesTopology()`

Output：
- `StateAssignment`: `dict[str, int]`
- `list[Path]`
- 每條 path 裡包含多個 `RouteStep`

意義：
- `state = 0`：bar / through
- `state = 1`：cross / drop
- `RouteStep` 描述某個 input signal 在某一 stage 經過哪個 MRR、從哪個 port 進、哪個 port 出。

---

### 4.3 Core device model

檔案：`mrr_switch_optimizer/core/models.py`

負責：
- 定義 MRR cell 幾何
- 定義 port 幾何與 port role
- 把 wire transition 映射到 add-drop MRR port

關鍵內容：

- `default_add_drop_ports()` 定義：
  - `in` / `drop` 在左側
  - `th` / `add` 在右側
  - optical role 和 physical side 不可混為一談

- `port_for_wire()`：wire + input/output side -> port name
- `state_for_transition()`：wire 是否換線 -> MRR state

---

### 4.4 Placement

檔案：
- `mrr_switch_optimizer/placement/layout.py`
- `mrr_switch_optimizer/placement/sa.py`
- `mrr_switch_optimizer/placement/lp.py`

負責：
- 把 logical topology 放到 physical layout 上
- 預設 layout 依 stage pitch / wire pitch 排列
- 可選 SA placement
- 可選 LP placement

`layout.py` 的 `build_cells()` input/output：

Input：
- topology
- s-table
- `stage_pitch_um`
- `wire_pitch_um`
- `x0_um`
- optional centers

Output：
- `dict[str, MRRCell]`

意義：
- 每個 MRR 有中心座標、port geometry、bbox、thermal spacing 參數等。

---

### 4.5 Analysis

檔案：
- `mrr_switch_optimizer/analysis/cost.py`
- `mrr_switch_optimizer/analysis/activity.py`
- `mrr_switch_optimizer/analysis/surrogate.py`

負責：
- routing metric
- insertion loss
- SXR
- crossing loss
- activity scan
- analytic surrogate feature / cost

Input：
- topology
- permutations
- s-table
- optional placement centers

Output：
- metric dataclass
- CSV rows
- summary rows

---

### 4.6 Physical routing

檔案：`mrr_switch_optimizer/routing/physical.py`

主要 API：

- `route_physical_paths()`  
  較簡化的 wrapper。若有 failed net，會 raise `RoutingError`。

- `route_physical_design()`  
  較完整的 routing API，回傳 `PhysicalRoutingResult`。

Input：
- `paths: list[Path]`
- `cells: dict[str, MRRCell]`
- `rules: RoutingRules`
- physical routing parameters，例如 port stub、x_start、x_end、wire pitch、max stage

Output：
- `PhysicalRoutingResult`
  - `routes`
  - `drc_violations`
  - `failed_nets`
  - `crossings`
  - `rules`
  - `crossing_count_by_pair`

意義：
- 將 topology 產生的 logical active paths 轉換成實際 waveguide Manhattan polyline。
- 同時處理 MRR keepout、spacing、port escape、crossing、rip-up retry、DRC 等。

---

### 4.7 Routing support modules

檔案：
- `routing/types.py`
- `routing/geometry.py`
- `routing/grid.py`
- `routing/grid_router.py`
- `routing/route_grid.py`
- `routing/drc.py`
- `routing/crossing.py`
- `routing/port_access.py`
- `routing/refinement.py`

大致分工：

- `types.py`：routing dataclass / rules / result types
- `geometry.py`：幾何與 segment 判斷
- `grid.py`：A* / route grid / track search
- `grid_router.py`：router state、neighbor move、history cost
- `route_grid.py`：grid occupancy 與查詢
- `drc.py`：routing result validation
- `crossing.py`：crossing legality / budget
- `port_access.py`：MRR port access region 與 owner legality
- `refinement.py`：route scoring / crossing count / refinement

---

### 4.8 Output visualization

檔案：`mrr_switch_optimizer/output/visualize.py`

負責：
- routing PNG
- routing GIF
- IL/SXR CDF plot

Input：
- topology
- permutation
- states
- s-table
- optional centers
- optional routing rules
- physical debug overlay flag

Output：
- image files，例如 `.png`, `.gif`

---

## 5. Execution Flow

以下是根據 `main.py`、`cli.py`、`topology.py`、`layout.py`、`physical.py`、`visualize.py` 觀察到的大致流程。

### 5.1 一般 routing / visualization flow

1. 使用者啟動：

   ```bash
   python main.py
   ```

   或安裝後使用：

   ```bash
   mrr-switch ...
   ```

2. `main.py` 呼叫：

   ```python
   mrr_switch_optimizer.app.cli.main()
   ```

3. `cli.py:main()` 執行 `_parse_args()`。

4. CLI 決定：
   - `project_root`
   - `sparam_dir`
   - `outdir`
   - permutation
   - topology list
   - physical routing rules

5. `load_mrr_s_table()` 載入 S-parameter table。

6. `_select_topologies()` 根據 `--topology` 建立 topology：
   - `main`：`PaddedBenesTopology`, `WaksmanTopology`
   - `all`：再加 `SpankeBenesTopology`
   - `benes` / `waksman` / `sb`：單一 topology

7. 每個 topology 初始化時：
   - `RNBTopology.__init__()` 建立 LUT
   - `_assert_rnb()` 檢查所有 logical permutation 是否可 route

8. 對每個 topology：
   - `get_state_assignment(permutation)` 取得每個 MRR state
   - `evaluate_routing()` 產生 routing metric
   - `build_cells()` 建立 physical MRR cells
   - `save_routing_png()` 輸出 routing 圖
   - optional `save_routing_gif()` 輸出動畫
   - `_write_states()` 輸出 MRR state CSV

### 5.2 Optional placement flow

如果使用 `--sa`：

1. `make_permutation_split()` 產生 training permutations。
2. `aggregate_cost()` 計算 default layout cost。
3. `_run_sa_restarts()` 執行 SA placement。
4. `evaluate_routing(..., centers=sa_centers)` 評估 SA layout。
5. 輸出：
   - `*_sa_centers.csv`
   - `*_sa.png`
   - optional `*_sa.gif`
   - `sa_summary.csv`

如果使用 `--lp`：

1. `make_permutation_split()` 產生 LP training permutations。
2. `lp_placement()` 計算 LP placement。
3. 輸出：
   - `*_lp_centers.csv`

### 5.3 Optional evaluation flow

如果使用 `--eval`：

輸出：

- `eval_path_distribution.csv`
- `eval_summary.csv`

如果搭配 `--plot-distributions`：

- `il_sxr_cdf.png`

如果使用 `--activity-scan`：

- `mrr_activity.csv`
- `mrr_activity_summary.csv`

如果使用 `--paper-table`：

- `paper_comparison_table.csv`

### 5.4 Physical routing flow

如果使用 `--physical-eval` 或 visualization 開啟 debug physical overlay，會使用 physical routing。

核心流程在 `route_physical_design()`：

1. `_validate_rules(rules)` 驗證 physical routing rules。
2. 如果沒有 paths，回傳空的 `PhysicalRoutingResult`。
3. 決定 `x_end`，預設為最右邊 cell center + 70 um。
4. 根據 routing complexity 或 input port 排序 paths。
5. 進入 rip-up passes：
   - 初次 routing
   - 若失敗或有 DRC violation，根據 failed inputs / crossing sources / forbidden points 重新排序與 reroute
6. 每個 pass 呼叫 `_route_physical_order()` route 多條 path。
7. 使用 `validate_physical_routes()` 檢查 DRC。
8. 若成功，計算 crossings，排序 route，回傳 `PhysicalRoutingResult`。
9. 若失敗，紀錄：
   - failed net
   - failed source points
   - crossing source
   - reroute priority

### 5.5 主要輸出檔案與意義

根據 `cli.py` 目前邏輯，常見 output 包含：

- `{topology}_{permutation}.png`  
  Routing visualization。

- `{topology}_{permutation}.gif`  
  若 `--gif` 啟用，輸出 stage-by-stage 動畫。

- `{topology}_{permutation}_states.csv`  
  欄位：
  - `mrr_id`
  - `state`
  - `meaning`

  意義：每個 MRR 是 `through/bar` 還是 `drop/cross`。

- `routing_summary.json`  
  將 routing metric dataclass 轉成 JSON。

- `routing_comparison.csv`  
  欄位包含：
  - topology
  - mrr_count
  - stages
  - path depth
  - active states
  - total wiring
  - logical crossings
  - worst / average insertion loss
  - worst SXR

- `*_sa_centers.csv`  
  SA placement 後每個 MRR 的座標。

- `*_lp_centers.csv`  
  LP placement 後每個 MRR 的座標。

- `sa_summary.csv`  
  SA placement 與 default placement 的 cost / IL 比較。

- `physical_path_distribution.csv`  
  Physical routing path-level distribution。

- `physical_summary.csv`  
  Physical routing summary。

- `drc_violations.csv`  
  Physical routing DRC violation 列表。欄位包含：
  - topology
  - layout
  - permutation
  - rule
  - net_id
  - message
  - x_um
  - y_um

---

## 6. Possible Improvement Areas

### 6.1 README 為空，專案缺少基本說明

- 問題：
  `README.md` 目前 0 行，沒有 quickstart、架構圖、CLI 範例、input/output 說明。

- 影響：
  新開發者很難知道如何準備 S-parameter library、如何跑 demo、哪些輸出檔是什麼意思，也不容易知道哪些參數安全可調。

- 建議：
  補上 README，至少包含：
  - 專案目的
  - 安裝方式
  - 最小執行範例
  - `--permutation` 格式
  - `--sparam-dir` 要求
  - output 檔案說明
  - 常見 physical routing rules 意義

- 涉及檔案：
  - `README.md`
  - `pyproject.toml`
  - `mrr_switch_optimizer/app/cli.py`

- 優先級：High

---

### 6.2 `routing/physical.py` 過大且責任過多

- 問題：
  `mrr_switch_optimizer/routing/physical.py` 約 5329 行，包含 routing orchestration、rip-up、candidate generation、crossing source tracking、failure handling、scoring、helper functions 等大量邏輯。

- 影響：
  - 很難定位 bug
  - 很難 review
  - 很難重構
  - private helper 互相依賴複雜
  - 新增 routing rule 容易牽動大量程式碼

- 建議：
  逐步拆分，例如：
  - `physical_orchestrator.py`
  - `candidate_generation.py`
  - `ripup.py`
  - `crossing_sources.py`
  - `failure_analysis.py`
  - `route_scoring.py`

- 涉及檔案：
  - `mrr_switch_optimizer/routing/physical.py`
  - `mrr_switch_optimizer/routing/types.py`
  - `tests/test_physical_router.py`

- 優先級：High

---

### 6.3 `app/cli.py` 同時處理 CLI、流程控制與輸出寫檔

- 問題：
  `mrr_switch_optimizer/app/cli.py` 約 1216 行，負責 CLI parsing、執行流程、evaluation、CSV/JSON writing、plot trigger、physical eval 等。

- 影響：
  - CLI 介面與核心流程耦合
  - 很難從其他 Python 程式重用核心功能
  - 測試 CLI 與測試 domain logic 容易混在一起

- 建議：
  拆分：
  - `cli_args.py`：只處理 argparse
  - `runner.py`：執行主流程
  - `reports.py`：CSV/JSON output
  - `physical_eval.py`：physical evaluation orchestration

- 涉及檔案：
  - `mrr_switch_optimizer/app/cli.py`

- 優先級：High

---

### 6.4 dependency 宣告可能不完整

- 問題：
  `placement/lp.py` 有 `from scipy.optimize import linprog`，但 `pyproject.toml` 沒有宣告 `scipy`。

- 影響：
  使用者安裝 package 後，如果執行 `--lp`，可能遇到 `ModuleNotFoundError: No module named 'scipy'`。

- 建議：
  - 若 LP 是正式功能，把 `scipy` 加到 dependencies。
  - 若 LP 是 optional 功能，新增 optional extra，例如：
    ```toml
    [project.optional-dependencies]
    lp = ["scipy>=..."]
    ```
  - 在 CLI 啟用 `--lp` 時提供清楚錯誤訊息。

- 涉及檔案：
  - `pyproject.toml`
  - `mrr_switch_optimizer/placement/lp.py`
  - `mrr_switch_optimizer/app/cli.py`

- 優先級：High

---

### 6.5 預設 S-parameter 目錄在目前檔案樹中未看到

- 問題：
  `cli.py` 預設使用：
  ```python
  project_root / "mrr_sparam_library"
  ```
  但本次列出的專案檔案樹中沒有看到 `mrr_sparam_library/`。

- 影響：
  使用預設參數執行時，可能需要外部資料目錄；若使用者不知道，會很難順利啟動。

- 建議：
  - 在 README 明確說明 S-parameter library 取得方式與目錄格式。
  - 若可行，提供 sample / mock data。
  - `load_mrr_s_table()` 若找不到資料，應輸出可操作的錯誤訊息。

- 涉及檔案：
  - `mrr_switch_optimizer/app/cli.py`
  - `mrr_switch_optimizer/core/sparams.py`
  - `README.md`

- 優先級：High

---

### 6.6 routing / layout 參數分散且多處 hard-coded

- 問題：
  routing 與 layout 預設值分散在多個地方，例如：
  - `RoutingRules` in `routing/types.py`
  - `build_cells()` default pitch / bbox / thermal spacing in `placement/layout.py`
  - CLI default args in `app/cli.py`
  - port geometry in `core/models.py`

- 影響：
  - 調參不容易
  - 文件容易與程式不同步
  - 不同實驗可能不易重現

- 建議：
  - 建立集中式 config dataclass 或 config schema。
  - 支援讀取 YAML/JSON config。
  - 將 CLI args 視為 override，而不是所有參數唯一來源。

- 涉及檔案：
  - `mrr_switch_optimizer/routing/types.py`
  - `mrr_switch_optimizer/placement/layout.py`
  - `mrr_switch_optimizer/core/models.py`
  - `mrr_switch_optimizer/app/cli.py`

- 優先級：Medium

---

### 6.7 wildcard imports 與 private helper 依賴偏多

- 問題：
  `routing/physical.py` 使用：
  ```python
  from .geometry import *
  from .grid import *
  from .port_access import *
  from .refinement import *
  from .types import *
  ```
  測試檔也大量 import `_xxx` private helpers。

- 影響：
  - 模組依賴不清楚
  - refactor 時容易破壞測試
  - public API 邊界模糊
  - mypy strict 下可讀性仍然受影響

- 建議：
  - 改成 explicit imports。
  - 明確定義 public API。
  - 測試盡量測 public behavior；必要的內部 helper 可整理成較穩定的小模組。

- 涉及檔案：
  - `mrr_switch_optimizer/routing/physical.py`
  - `tests/test_physical_router.py`

- 優先級：Medium

---

### 6.8 測試檔過於集中

- 問題：
  `tests/test_physical_router.py` 約 2980 行，測試範圍橫跨 topology、port access、geometry、grid routing、physical routing、DRC、helper functions。

- 影響：
  - 測試閱讀成本高
  - 失敗時定位較慢
  - 不同 domain 的 fixture / helper 容易互相混雜

- 建議：
  拆成多個測試檔，例如：
  - `test_models.py`
  - `test_topology.py`
  - `test_port_access.py`
  - `test_geometry.py`
  - `test_grid_router.py`
  - `test_physical_routing.py`
  - `test_drc.py`

- 涉及檔案：
  - `tests/test_physical_router.py`

- 優先級：Medium

---

### 6.9 logging / diagnostics 可以更系統化

- 問題：
  `physical.py` 目前有 failure message、failed nets、debug artifacts，但沒有明顯看到標準 logging flow。CLI 也主要是直接產生檔案。

- 影響：
  - routing 失敗時，使用者可能只能看 output artifacts 或錯誤訊息
  - 長時間 eval / physical routing 時不易追蹤進度
  - debug 開關與輸出可能分散

- 建議：
  - 使用 Python `logging`
  - 新增 `--verbose` / `--debug` flag
  - routing failure 統一輸出 structured diagnostic JSON
  - 將 debug artifact 產生規則集中管理

- 涉及檔案：
  - `mrr_switch_optimizer/app/cli.py`
  - `mrr_switch_optimizer/routing/physical.py`
  - `mrr_switch_optimizer/analysis/task11_probe_artifacts.py`

- 優先級：Medium

---

### 6.10 產生物與 source code 混在 repository 內

- 問題：
  `Physical_output/` 內包含大量 PNG、CSV、JSON、debug output。這些看起來多數是執行結果或研究 artifact。

- 影響：
  - repository 變大
  - source code 與產生物界線不清楚
  - binary PNG diff 不易 review

- 建議：
  - 明確區分 `examples/`、`docs/figures/`、`outputs/`。
  - 若是可重建 artifact，考慮加入 `.gitignore`。
  - 保留少量代表性圖檔作為 documentation，其餘改由 script 產生。

- 涉及檔案：
  - `Physical_output/**`
  - `.gitignore`
  - `README.md`

- 優先級：Low / Medium

---

### 6.11 LLM / prompt 相關功能目前看不出是核心程式路徑

- 問題：
  專案內有 `mrr_switch_prompt.md`、`Physical_output/Prompt.md`、`skills/.../SKILL.md`，但目前讀到的 Python 主流程沒有看到 LLM API 呼叫或 prompt runtime loading。

- 影響：
  如果這些 prompt 是開發流程文件，問題不大；如果它們預期驅動程式行為，則目前 runtime path 不明確。

- 建議：
  - 若 prompt 只是文件，放到 `docs/` 並命名清楚。
  - 若 prompt 會影響程式執行，應建立明確 prompt loader、版本管理與測試。

- 涉及檔案：
  - `mrr_switch_prompt.md`
  - `Physical_output/Prompt.md`
  - `skills/mrr-switch-optimizer-dev/SKILL.md`

- 優先級：Low

---

## 7. Files Worth Reading First

如果你要開始開發，我建議依序先看這些檔案：

1. `pyproject.toml`  
   了解 package 名稱、Python 版本、dependencies、console script、mypy 設定。

2. `mrr_switch_optimizer/app/cli.py`  
   這是實際主流程。看完可以知道使用者輸入如何轉成 topology、routing、placement、evaluation 與輸出檔。

3. `mrr_switch_optimizer/core/topology.py`  
   了解 topology、permutation、state assignment、active path 是怎麼產生的。這是理解整個專案的核心。

4. `mrr_switch_optimizer/core/models.py`  
   了解 MRR cell、port geometry、add-drop port mapping、state semantics。

5. `mrr_switch_optimizer/core/sparams.py`  
   了解 S-parameter table 的檔案格式、mock table、loading rule。這次尚未讀內容，但它是執行時資料來源關鍵檔。

6. `mrr_switch_optimizer/placement/layout.py`  
   了解 logical topology 如何變成 physical cell coordinates。

7. `mrr_switch_optimizer/routing/types.py`  
   了解 physical routing 的資料結構與所有 default rules。

8. `mrr_switch_optimizer/routing/physical.py`  
   了解真正的 physical waveguide routing 流程。這檔很大，建議從 `route_physical_design()` 開始看。

9. `mrr_switch_optimizer/output/visualize.py`  
   了解輸出的 PNG / GIF / CDF 圖是怎麼畫的，也能幫助理解 route 結果的視覺意義。

10. `tests/test_physical_router.py`  
   雖然很大，但它透露目前作者最在意的行為規格，特別是 port side / optical role / physical routing / DRC 的邊界案例。

---

## 補充：目前資訊不足或尚未深入的地方

因為你限制最多先讀 10 個關鍵檔案，本次沒有深入讀以下檔案內容：

- `mrr_switch_optimizer/core/sparams.py`
- `mrr_switch_optimizer/analysis/cost.py`
- `mrr_switch_optimizer/analysis/activity.py`
- `mrr_switch_optimizer/analysis/surrogate.py`
- `mrr_switch_optimizer/routing/geometry.py`
- `mrr_switch_optimizer/routing/grid.py`
- `mrr_switch_optimizer/routing/drc.py`
- `mrr_switch_optimizer/routing/port_access.py`
- `mrr_switch_optimizer/placement/sa.py`
- `mrr_switch_optimizer/placement/lp.py`

因此，以上模組的細節描述主要依據：

- 檔名
- import 關係
- definition summary
- 已讀檔案中的呼叫關係

若下一步要做更深入 code review，我會建議優先讀：

1. `core/sparams.py`
2. `analysis/cost.py`
3. `routing/geometry.py`
4. `routing/grid.py`
5. `routing/drc.py`
6. `routing/port_access.py`

---

## 8. N×N Generalization / Refactor Notes

以下是把目前 MRR switch 架構從固定 6×6 推到參數化 `N×N` 時，建議優先整理的計算與程式切分。

### 8.1 目前狀態判斷

目前專案已經有清楚的分層：

- `core/topology.py`：拓撲、permutation、MRR state assignment、active paths。
- `core/models.py`：MRR cell / port geometry / state semantics。
- `placement/layout.py`：由 topology 產生 MRR physical placement。
- `analysis/cost.py`：insertion loss、SXR、crossing、aggregate cost。
- `routing/physical.py`：實體 waveguide routing 與 DRC。
- `app/cli.py`：CLI、流程控制、報表輸出。

其中 `analysis/cost.py`、`placement/layout.py` 已經部分依賴 `topology.N_logical` / `topology.N_physical`，比較容易泛化；真正卡住 `N×N` 的地方主要是 topology 生成、state assignment、CLI permutation parsing、以及全 permutation 掃描策略。

### 8.2 MRR 數量與 stage 數量公式

#### Padded Beneš

令：

```text
N = logical input/output 數
P = 2^ceil(log2(N))      # padded physical port count
k = log2(P)
```

則：

```text
N_logical  = N
N_physical = P
n_stages   = 2k - 1
n_MRR      = (P / 2) * (2k - 1)
blocked ports = P - N
```

例子：

```text
N=6 -> P=8,  k=3, n_stages=5, n_MRR=20
N=8 -> P=8,  k=3, n_stages=5, n_MRR=20
N=16 -> P=16, k=4, n_stages=7, n_MRR=56
```

注意：目前 `PaddedBenesTopology` 固定為 `N_logical=6`、`N_physical=8`，stage pairs 也是手寫 8×8。

#### Native Spanke-Beneš

依目前 canonical spec：

```text
N_logical  = N
N_physical = N
n_MRR      = N(N - 1) / 2
n_stages   = 2N - 3
has_native_crossings = False
```

例子：

```text
N=6 -> n_MRR=15, n_stages=9
N=8 -> n_MRR=28, n_stages=13
N=16 -> n_MRR=120, n_stages=29
```

注意：目前 `SpankeBenesTopology` 固定為 6×6，stage pairs 手寫，尚未有 `N` 參數化 generator。

#### Waksman

目前 `build_waksman_stage_pairs(n)` 已經可對任意 `n` 產生 recursive stage pairs，但 `WaksmanTopology` class 仍固定：

```python
N_logical = 6
N_physical = 6
```

對 power-of-two `N`，目前 generator 的 MRR count 符合：

```text
n_MRR = N log2(N) - N + 1
n_stages = 2 log2(N) - 1
```

例子：

```text
N=8  -> n_MRR=17, n_stages=5
N=16 -> n_MRR=49, n_stages=7
```

對非 power-of-two `N`，建議直接用 generator 結果計算：

```python
n_stages = len(stage_pairs)
n_MRR = sum(len(stage) for stage in stage_pairs)
```

目前 generator 結果：

```text
N=6 -> n_MRR=11, n_stages=5
N=7 -> n_MRR=14, n_stages=5
N=8 -> n_MRR=17, n_stages=5
```

### 8.3 Insertion Loss 計算整理

目前 `analysis/cost.py` 的 path-level IL 已經符合下列形式：

```text
IL_path = IL_MRR + IL_wire + IL_crossing

IL_MRR      = Σ -10 log10(S(port_in, port_out, state))
IL_wire     = α * waveguide_length_um
IL_crossing = N_cross * L_cross_unit
```

在尚未 full physical routing 前，`waveguide_length_um` 目前用 port-to-port Manhattan 估計：

```text
length = input bus -> first MRR port
       + Σ inter-MRR port-to-port Manhattan distance
       + last MRR port -> output bus
```

進入 physical routing 後，應改用實際 route：

```text
IL_physical = IL_MRR
            + route.length_um * α
            + route.bend_count * L_bend
            + route.crossing_count * L_cross_unit
            + repeated_crossing_loss
            + jog / bend-placement penalties if enabled
```

建議重構時把 IL 拆成明確欄位，避免只有單一總和：

```text
path_id
input_port
output_port
mrr_count_on_path
mrr_loss_db
waveguide_length_um
propagation_loss_db
bend_count
bend_loss_db
crossing_count
crossing_loss_db
total_insertion_loss_db
```

### 8.4 Crosstalk / SXR 計算整理

目前 SXR 主要有兩類 leakage：

```text
SXR = 10 log10(P_signal / (P_leak_mrr + P_leak_crossing + epsilon))
```

MRR finite extinction：

```text
P_leak_mrr += P_other_at_mrr * S(other_in_port, victim_out_port, state)
```

Waveguide crossing：

```text
P_leak_crossing += χ_cross
χ_cross = 10^(XT_cross_db / 10)
```

注意：

- `SpankeBenesTopology.has_native_crossings() == False` 時，logical-level crossing leakage 應為 0。
- physical routing 後若產生實體 crossing，應由 physical route 統計補回。

### 8.5 目前 N×N 主要阻塞點

#### 1. `_build_lut()` 暴力枚舉 `2^n_MRR`

目前 `RNBTopology._build_lut()`：

```python
for bits in product((0, 1), repeat=switch_count):
    ...
```

這對現有 6×6 / 8×8 尚可，但不能作為 `N×N` 的主策略。

例子：

```text
Padded Beneš N=16: n_MRR=56 -> 2^56 impossible
Spanke N=16: n_MRR=120 -> 2^120 impossible
```

建議：

- 保留 brute-force LUT 只作小 N 測試 oracle。
- 新增 constructive state assignment：
  - `PaddedBenesStateRouter`
  - `WaksmanStateRouter`
  - `SpankeBenesStateRouter`
- `RNBTopology` 只保留 interface，不負責所有 topology 的 state search。

#### 2. `PaddedBenesTopology` / `SpankeBenesTopology` 固定 6/8

目前 class-level constants 寫死：

```python
N_logical = 6
N_physical = 8 or 6
name = "..._6x6" / "..._8x8"
stage_pairs = (...)
```

建議改為：

```python
PaddedBenesTopology(n_logical: int, n_physical: int | None = None)
SpankeBenesTopology(n_logical: int)
WaksmanTopology(n_logical: int)
```

並由 constructor 產生：

```text
name
N_logical
N_physical
n_stages
n_MRR
stage_pairs
blocked ports
```

#### 3. CLI permutation parser 固定 `0..5`

目前：

```python
if sorted(permutation) != list(range(6)):
    raise argparse.ArgumentTypeError(...)
```

建議新增：

```text
--n-logical N
--permutation 2,0,5,1,3,4
```

規則：

- 如果有 `--permutation`，可從 permutation 長度 infer `N`。
- 如果沒有 `--permutation`，用 `--n-logical` 產生 identity 或固定 seed random permutation。
- topology selector 建立 topology 時使用同一個 `N`。

#### 4. `N!` 全 permutation 掃描不可無限制使用

目前 `make_permutation_split()` 和 `scan_mrr_activity()` 會列舉 `S_N`：

```python
list(permutations(range(n_logical)))
```

這對 `N=6` 是 720，沒問題；但：

```text
N=8  -> 40320
N=10 -> 3628800
N=16 -> 20,922,789,888,000
```

建議：

- `N <= 8` 可允許 exhaustive。
- `N > 8` 預設改 sampling。
- CLI 明確區分：
  - `--eval-mode exhaustive`
  - `--eval-mode sample`
  - `--eval-samples`
  - `--train-samples`

### 8.6 建議重構順序

#### Phase 1 — 先整理計算，不改行為

目標：現有 6×6 結果完全不變。

建議新增：

```text
mrr_switch_optimizer/analysis/summary.py
```

集中輸出：

```text
TopologySummary
PathLossBreakdown
PermutationSummary
```

讓 `routing_comparison.csv`、`physical_summary.csv`、`paper_comparison_table.csv` 都由同一套 summary 結構產生。

#### Phase 2 — 拓撲 constructor 參數化，但 default 保持 6

先做：

```python
WaksmanTopology(n_logical: int = 6)
PaddedBenesTopology(n_logical: int = 6)
SpankeBenesTopology(n_logical: int = 6)
```

同時保留舊測試：

```python
PaddedBenesTopology()
SpankeBenesTopology()
WaksmanTopology()
```

不能破壞目前 6×6 regression。

#### Phase 3 — 把 brute-force LUT 隔離成小 N backend

建議 interface：

```python
class StateAssignmentStrategy:
    def get_state_assignment(self, topology, permutation) -> StateAssignment: ...
```

`RNBTopology` 不再直接 `_build_lut()`；而是：

```text
small N / test: BruteForceLUTStrategy
production: ConstructiveStrategy
```

這是 `N×N` 最重要的一步。

#### Phase 4 — permutation sampling 與 caching

要避免每個流程重複：

```text
topology.get_state_assignment(permutation)
topology.get_active_paths(permutation, states)
build_cells(topology, s_table)
```

建議 cache：

```text
PermutationPlan:
  permutation
  states
  paths

EvaluationDataset:
  train_plans
  eval_plans
```

這會直接改善 SA / LP / eval / analytic_edges 的 runtime。

#### Phase 5 — physical routing 再做 N scaling

等 topology 與 metric 都可參數化後，再處理 physical routing 的擴展。

原因：`routing/physical.py` 已經很大，且目前瓶頸不只是 N，而是 crossing budget、rip-up、port access、A* 搜尋空間。太早一起改會讓 bug 來源混在一起。

### 8.7 最小可執行目標

第一個合理 milestone：

```text
python main.py --topology waksman --n-logical 8 --permutation 7,6,5,4,3,2,1,0 --eval-mode sample
```

先要求能輸出：

```text
routing_summary.json
routing_comparison.csv
*_states.csv
logical IL/SXR metrics
```

暫時不要求：

```text
physical routing clean
full exhaustive S_N eval
SA/LP 最佳化完全穩定
```

原因：這能先驗證 topology、state assignment、MRR 數量、path loss breakdown 都已經脫離固定 6×6。
