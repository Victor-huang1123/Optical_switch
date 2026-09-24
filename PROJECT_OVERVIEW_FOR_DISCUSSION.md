# MRR 光交換晶片合成專案 — 完整技術總覽
（2026-09-11 整理，供外部討論用；本文自足，不需其他背景）

---

## 0. 一句話摘要

我們在做「N×N 微環諧振器（MRR）光交換晶片」的自動化合成與評估流程：給定交換網路拓撲
（Waksman / padded Beneš），自動產生**固定不變的實體版圖**（placement + routing），
然後對**全部 N! 個排列**做精確的最差插入損耗（Worst Insertion Loss, WIL）評估，
用來回答一個論文級問題：**Waksman 拓撲（元件數較少）相對於確定性佈局的 padded Beneš，
在實體層是否有優勢、優勢在哪個 N 區間**。

---

## 1. 問題定義

### 1.1 物理載體
- **交換元件**：add-drop 微環諧振器（MRR），四個 port：`in`、`th`(through)、`add`、`drop`。
  - 兩條平行匯流排（bus）耦合一個環。上 bus：`in → th`（左→右）；下 bus：`add → drop`（右→左，**反向傳播**）。
  - **bar 狀態**（off-resonance）：`in→th`、`add→drop`；**cross 狀態**（on-resonance）：`in→drop`、`add→th`。
  - 一顆 MRR = 一個 2×2 switch。狀態由熱調諧（heater）控制。
- **S 參數**：解析模型產生的 library（κ=0.15，半徑 4.5/5.0/5.5/6.0 µm，波長 1545–1555 nm）。
  r=5 µm @1550 nm 實測值：through 0.029 dB、drop 0.048 dB、off 態 extinction −21.8 dB、on 態 −45 dB。

### 1.2 拓撲
- **padded Beneš**：N 非 2 的冪次時 pad 到 P = 2^⌈log2 N⌉。switch 數 = (P/2)(2·log₂P − 1)。
  stage 數 = 2·log₂P − 1。butterfly 連接（stage k 的配對距離 2^k）。
- **Waksman**：Beneš 的最小化版本（Waksman 1968, JACM），每個偶數遞迴區塊移除一顆冗餘 output switch。
  switch 數 W(n) = n·⌈log₂n⌉ − 2^⌈log₂n⌉ + 1。**不需要 padding**（支援任意 N）。
- 兩者都是 rearrangeably non-blocking（RNB）：任意排列可繞，但需要全域重新設定狀態。

### 1.3 Fixed fabric 公理（本專案的核心設計）
> **一套固定的 MRR、波導、placement、physical routing。不同 permutation 只能改變 MRR 的 bar/cross 狀態，
> 不得重新產生實體繞線。**

這是與「每個 permutation 各自繞線」的舊做法（legacy active-permutation router）的根本差異，
也是所有後續驗證的基礎。

---

## 2. 系統架構（資料流）

```
Topology (stage_pairs)                  ← core/topology.py
   ↓
FabricGraph (固定的有向邊集合)            ← core/fabric.py
   │  節點 = {I_w, O_w, mrr.port}；邊 = 實體波導段
   │  連續無 switch 的 stage 被合併成單一較長 FabricEdge（不造假 MRR）
   ↓
build_cells (placement: 每 stage 一欄、cell 在 wire pair 中點)  ← placement/layout.py
   ↓
route_fixed_fabric  ← routing/fabric.py
   ├── layout_mode="template" → 確定性佈局（padded Beneš, P∈{4,8,16}）
   └── layout_mode="astar"    → A* 詳細繞線（Waksman）
   ↓
FixedFabricRoutingResult (waypoints, crossings, DRC)
   ↓
┌─ 評估（只讀幾何，不重繞）────────────────────────────┐
│  A. verify_permutation_coverage：N! 個排列的 RNB 驗證    │
│  B. worst-IL：兩種獨立方法（見 §4），互相交叉驗證        │
│  C. DRC：8 條 centerline 規則 + 3 條新增審計規則          │
└──────────────────────────────────────────────────┘
```

**關鍵模組**（`mrr_switch_optimizer/`）：
| 模組 | 職責 |
|---|---|
| `core/topology.py` | 拓撲生成（遞迴 Waksman、butterfly Beneš） |
| `core/state_assignment.py` | 狀態指派策略（WaksmanStrategy 遞迴 2-coloring、BenesLoopingStrategy） |
| `core/fabric.py` | FabricGraph 建構 + 結構驗證 |
| `routing/physical.py` (5.8k 行) | A* router 總控（net 排序、hop 候選、rip-up、協商） |
| `routing/grid.py` (1.4k 行) | A* 核心 + step cost model |
| `routing/benes_template_layout.py` | 確定性 Beneš 佈局（零搜尋） |
| `routing/envelope.py` | 八度畫布（octave envelope）計算 |
| `routing/drc.py` | DRC 規則 |
| `analysis/fabric_loss.py` | 每條路徑的 IL 計算 |
| `analysis/nsweep.py` | 平行全枚舉 + 路徑空間法 |
| `app/nsweep_campaign.py` | 戰役驅動器（resumable、per-case checkpoint） |

---

## 3. 幾何與損耗模型

### 3.1 幾何規格（v3 統一後）
| 參數 | 值 | 說明 |
|---|---|---|
| 波導寬度 | 0.45 µm | v3 才明確參數化（先前是純中心線模型） |
| MRR 半徑 | 5 µm | |
| Cell bbox（routing obstacle） | 28 × 22 µm | 含 heater/電極的佔位 |
| Port 位置 | dx=±8, **dy=±5.5** µm | v3 從 ±4 改為 ±5.5（±4 時雙 bus 間距 8 µm < r=5 環所需的 ~10.9 µm，且 cross 態內部 riser 8 µm < 2R=10 µm 違反彎折合法性） |
| Bend radius | 5 µm | 最小彎折半徑，2R=10 µm 為相鄰彎折最小間距 |
| Wire pitch | 64 µm | 水平線軌間距 |
| Grid pitch | 8 µm | 繞線網格 |
| **Stage pitch** | **232 / 168 / 136 µm** | P=16/8/4，由容量公式算出（見 §3.2） |
| Min spacing（跨 net） | 4 µm | 中心線距 |
| MRR keepout | 6 µm | |

### 3.2 Octave envelope（八度畫布）— 公平比較的憲法
```
stage_pitch = 2 · X_wrap + (P − 1) · grid_pitch
X_wrap = ceil8(cell半寬 + keepout + port_escape + wrap_runway + 2R)
```
**規則**：N ∈ (2,4] 用 P=4 畫布、(4,8] 用 P=8、(8,16] 用 P=16。
**同一八度內，兩個拓撲、所有 N、所有 config 共用完全相同的畫布**（x/y 範圍、所有 pitch、margin）。
Waksman 的 stage 數 = 2⌈log₂N⌉−1 與同八度 padded Beneš 相同 → 欄位 x 座標自然對齊，線少的只是留空軌。

理由：padding 的代價必須誠實呈現（Beneš 在 N=9 就要用 16 線畫布），但畫布本身不能逐案調整，
否則 40 個案子無法放在同一張圖上比較。**幾何旋鈕釘死，只有搜尋旋鈕（pops 預算、guide 模式）可逐案升級且必須記錄。**

### 3.3 損耗模型
```
IL(path) = Σ_MRR (−10·log₁₀ s_table[in_port, out_port, state])
         + α · path_length_um
         + L_bend · bend_count
         + L_cross · crossing_count
```
兩組係數情境：
| Config | α (dB/µm) | L_cross (dB) | L_bend (dB) | 性質 |
|---|---|---|---|---|
| **B_db_placeholder** | 0.002 | 0 | 0 | 原始 placeholder（0.002 dB/µm = 20 dB/cm，比典型 SOI strip 的 1–3 dB/cm 高 ~10×） |
| **C_db_realistic** | 0.0002 | 0.1 | 0.005 | 較寫實（crossing 0.1 dB 為未最佳化十字元件的典型值） |

**重要**：router 的成本函數在 `cost_model="db"` 模式下與這個損耗模型一致
（crossing 等效罰項 = L_cross ÷ α，C 情境下 = 500 µm），hairpin/backtrack 等只作為 tie-breaker（×0.05）。
這是 v2 的一個關鍵修正——先前 router 用 µm 罰項閃避 crossing，但損耗模型記 crossing 為 0 dB，兩者目標不一致。

---

## 4. WIL 評估：兩種獨立方法 + 交叉驗證

### 4.1 問題規模
N=12 有 12! = **4.79 億**個排列。單核吞吐實測 ~47k path-evals/s → 單核需 ~47 小時。

### 4.2 方法一：平行全枚舉（ground truth）
按排列前綴切成 N(N−1) 個 chunk，64 workers 平行，每個 chunk 內：
- 狀態指派 → `verify_state_assignment`（wire-swap 模擬 == 目標排列）+ 每個 hop ∈ 固定 fabric 有向邊集合 → **RNB 驗證**
- N 條 active path 各自算 IL，記 chunk 局部 argmax
最後 max-reduce，tie-break 用字典序最小排列（確定性）。n12 約 45–90 分鐘。
**RNB 與 WIL 共用同一趟枚舉**（RNB 檢查 ~92 µs/perm，比 IL 便宜）。

### 4.3 方法二：路徑空間法（降維）
**數學基礎**：損耗泛函 L(p) 具有**上下文無關性**（只依賴路徑 p 自身，不含與其他同時活躍路徑的交叉項），
因此：
```
max_π max_{p∈R(π)} L(p) = max_{p ∈ ⋃_π R(π)} L(p) = max_{p∈P*} L(p)
```
（⊆：每個 R(π) ⊆ P*；⊇：P* 中每條 p 依定義存在 witness π。第二個等號只用 max 對集合聯集的冪等性。）

**基數坍縮**：P*（所有 I→O 路徑）的大小受限於分層 DAG 結構——每條路徑最多過 ~7 顆 switch、每顆二分支
→ |P*| ≈ 幾百（n8: 176、n10: 436），與 N! 無關。**4.79 億 → ~500，精確不近似。**

**可實現性認證（關鍵細節）**：圖上存在的路徑不一定會被決定性策略使用。
實測 Beneš n8 有 **80/256 條路徑**是 looping 演算法永遠不會產生的 → 裸路徑最大值高估 0.063 dB。
因此每條候選路徑（由 IL 高到低）必須：找到 witness permutation（證明會被用到）或以決定性回溯證明不可實現。
每案存 `worst_il_certificate.json`（含 witness permutation），任何人可一步重驗。

### 4.4 交叉驗證
兩法對**每個案子**都跑，`method_agreement.csv` 要求 100% 吻合，不吻合則整條產線停下。
v2 戰役 37 個完成案全部吻合。

### 4.5 邊界：此分解只對 IL 成立
| 物理量 | 可否路徑分解 | 原因 |
|---|---|---|
| IL | ✅ | 上下文無關 |
| **SXR（串擾比）** | ❌ | 洩漏來自**所有同時活躍的其他路徑**經共享 MRR 的耦合 → 本質上是排列耦合量 |
| 熱串擾、相干干涉 | ❌ | 同樣依賴全局狀態 |

---

## 5. 確定性 Beneš Template 佈局（v3 引入）

### 5.1 動機
A* 繞 padded Beneš 的 16 線 fabric（P=16、56 顆 MRR、128 條 edge）需 **92 分鐘且仍有 16/128 edge 失敗**。
而文獻上已流片的 Beneš 晶片（Lu et al. OE 2016 的 16×16、Qiao et al. 2017 的 32×32）全部是
**每 stage 一欄 + 欄間確定性 shuffle 區**的規則佈局——根本不用搜尋。

### 5.2 演算法
- **軌道指派**：每個 channel（stage 間走廊）每條線一個垂直 riser 軌。
  rising nets 按出發 y 降序排、falling nets 按升序排 → 交叉恰為 inversion 對、各一次。
  理論基礎：Condrat, Kalla & Blair (SLIP 2013) 證明 channel 內 crossing 下界 = 排列的 inversion 數，
  odd-even transposition（swap-sort）達成下界。我們用的是「單 riser」特化版。
- **Add-drop wrap（convention A，knot-free）**：
  上線 `in→th` 直通 y=c+4；下線 inbound 走南軌 y=c−h_s 進 add（cell 右側）、
  outbound 從 drop（左側）升到北軌 y=c+h_n 越過 cell。offset 由 residue 規則保證所有 riser ≥ 2R。
  **每顆 cell 必產生 1 個 wrap crossing**——這是單環 add-drop 元件的固有稅（MZI 實作沒有）。
- **Crossing 數模型（可驗證的硬閘門）**：
```
T(N) = Σ(channel inversions) + 2 · n_MRR
     = 14 / 60 / 232   （benes-4 / 8 / 16）
vs 教科書 X(N) = (N/2)(N − log₂N − 1) = 2 / 16 / 88
```
差額就是 add-drop wrap 的實體代價，是本專案的一個小貢獻（文獻的 Beneš 晶片用 MZI，不需要 wrap）。

### 5.3 效果
padded Beneš n9–12 的繞線從 **92 分鐘 + 失敗 → < 1 秒 + 零失敗**，且 crossing 數 by construction 可驗證。
Padding 的案子（N=9..16 用同一張 benes-16 版圖）geometry hash 完全相同（閘門 G6）。

---

## 6. Router 機制（A* 車道）

繼承自 **LiDAR**（Zhou, Zhu, Gu, ISPD 2025, ScopeX-ASU）的 detailed router 設計：
| LiDAR 機制 | 我們的狀態 |
|---|---|
| Port access planning（保留區、escape/runway 巨集） | ✅ 已採納 |
| Orientation-aware A*（狀態含方向） | ✅ 已採納，並加上 straight-run 距離維度強制 2R 彎距 |
| Crossing 作為插入動作（budget + cost） | ⚠️ **只採納了決策邏輯，沒採納元件佔位**（我們的 crossing 是零尺寸的點） |
| Congestion history cost + rip-up | ✅ 已採納 |
| Per-net 搜尋窗 + 失敗擴張 | ✅ 已採納（per-hop 變體） |
| Port stagger/spread | ❌ 未採納（唯一完全沒移植的） |

**LiDAR 沒有 global routing**（論文明確把 global route planning 定位為它取代的方法）。
我們也是純 detailed routing，但多了三塊「準 global」結構：
envelope 容量公式（靜態 global 容量規劃）、Beneš template（global+detailed 合一的解析解）、
`_corridor_preferred_x`（很弱的彎折欄位指派）。

**DRC（8 條硬規則 + 3 條 v2 新增）**：
manhattan 軸對齊、MRR keepout（外部段）、跨 net collinear overlap、跨 net 平行間距 4 µm、
touching corner、crossing（allow_crossings=true 時不檢查）、同 net touching corner、連續外部 bend ≥ 2R；
**新增**：same_net_min_spacing、perpendicular_clearance、bend_radius_legality（全段含 local）。
**缺席（尚未實作）**：線寬控制、彎折離散化、crossing cell 佔位、taper、port 朝向、金屬/heater 層。

---

## 7. 工程演進史（v1 → v4）

### v1（起點，2026-08-02 審查前）
- 每 permutation 各自繞線的 legacy router + 初版 fixed fabric。
- **獨立審查（9 個維度 + 對抗式驗證）發現**：架構健全（拓撲經圖同構驗證是正規 AS-Waksman、
  fabric 確實只繞一次、port 慣例正確），但 headline IL 不是實體預測：
  96.7% 來自 20 dB/cm 的 placeholder 傳播損耗、bend/crossing 記 0、完全沒有 crosstalk。
  且 **N=8 時 Waksman vs Beneš 的 WIL 是平手**（5.017315 vs 5.016731，Beneš 還略贏）。

### v2（routing upgrade + N=3–12 全戰役）
新增（全部 config-gated，預設關閉以保 golden）：
1. **dB 成本模型**：A* 目標函數 = 損耗模型（解決 router 閃避免費 crossing 的矛盾）
2. **2R 彎距合法性**：A* 狀態加（方向, 直行距離），強制相鄰彎折 ≥ 2R（先前 committed 幾何有 11 對 < 2R，r=5 圓角畫不出來）
3. **Port runway 巨集**：cell 周圍的存取幾何預先合法化
4. **3 條新 DRC 審計規則**
5. **Octave envelope** + **Beneš template**
6. **nsweep campaign runner**：resumable、per-case checkpoint、雙法驗證、witness 憑證
**結果**：40 案（10 N × 2 拓撲 × 2 config），37 完成 + 3 個 route_failed（waksman N=10/11/12 的 C 車道）。

### v3（架構修剪 + 尺寸統一）
- **Phase 1**：死碼清除（含發現 `corridor_guide_mode` 是完全沒接線的 no-op——v2 報告的
  "soft guides" 主張因此是錯的，已修正）、helper 去重、CellGeometry 單一真源、campaign 參數化。
  **hash 中立**（所有 golden 不動）。
- **Phase 2**：離線分析 v2 產物（不重繞）——jog 拉直原型、crossing 可製造性掃描。
- **Phase 4**：尺寸統一（dy ±4 → ±5.5、波導寬度 0.45 µm 參數化），**唯一允許重生 golden 的地方**。
- **Phase 5 mini（12 案，N=4/6/8）結果**：
  - Waksman C 車道大幅改善：n4-C WIL 1.350→**0.720 dB**（crossing 16→4、彎少 36 個）；
    n6-C 2.278→**1.638 dB**（crossing 32→16、彎少 53 個）；同 net 審計 52/9 → 11/4
  - Template 拿下「10 µm crossing 臂長 by construction」硬門全綠，G1 維持 14/60/232
  - 幾何統一代價可忽略（template 案 WIL +0.001~0.05 dB）
  - **新迴歸**：waksman n8-C 從 complete 變 route_failed（放大後的外部 port 保留區堵死最後一跳）

### v4（crossing 機制補完）— **目前狀態：閘門失敗，停滯中**
目標：終結「規避-纏繞螺旋」（C 配置的天價 crossing 罰項 → 猛彎閃避 → 閃不掉還是交叉 →
纏繞自身 → route_failed）。
規劃的四項：
1. 補完 LiDAR crossing 插入（臂長合法性 + **佔位矩形登記進 occupancy map**）
2. 搜尋紀律升級（turn-guard 在 crossing 鄰域不縮放；search-time 同 net 間距成標準）
3. 彎滑移合法化（bend-slide）
4. 放置實驗（cell y-bias、欄內 x-stagger，僅報告不採用）

**實際閘門結果（2026-09-02）**：
```
all_zero_failed: FALSE       audits_strictly_reduced: TRUE (87 → 較少)
waksman N=4 B: 0 failed, WIL 2.586 → 2.586 (drift 0)     ✅ n4-B clearance 2→0
waksman N=4 C: 0 failed, WIL 0.720 → 1.455 (drift +0.735) ❌ C 車道 WIL 退步
waksman N=6 B: 0 failed, WIL 4.044 → 4.316 (drift +0.272) ❌
waksman N=6 C: 0 failed, WIL 1.638 → 2.714 (drift +1.076) ❌ 嚴重退步
waksman N=8 B: 6 failed  (v3 是 complete)                  ❌ 迴歸
waksman N=8 C: 0 failed, WIL 3.712  (v3 是 route_failed)   ✅ 修好了
padded_benes 全部：0 failed、WIL drift 0（template 不受影響）
```
**判讀**：crossing 佔位矩形確實消除了臂長違規（n4-B 2→0、n8-C 從失敗變可繞），
但佔位佔掉的空間讓其他路徑繞更遠（C 車道 WIL +0.7~1.1 dB）甚至繞不出來（n8-B 6 條失敗）。
**這是一個真實的 trade-off，不是 bug。**

### 消融實驗（2026-09-10，N=10）
```
waksman n10 B, explicit_crossings=TRUE : WIL 7.017, 270 s, 26 A* calls, 0 failed
waksman n10 B, explicit_crossings=FALSE: WIL 7.418, 251 s, 28 A* calls, 0 failed
waksman n10 C, explicit_crossings=FALSE: WIL 3.956, 781 s, 75 A* calls, 0 failed  ← C 車道在關閉下可繞！
padded_benes n10 B, explicit=FALSE     : WIL 8.547, 828 s, 79 A* calls, 0 failed
```
**結論**：crossing 顯式插入機制在 **N=10 的 B 車道有益**（WIL 7.418 → 7.017，−0.4 dB），
但在 **C 車道會阻擋繞線**，在 **N=4 有害**。→ 機制本身應該是**依情境啟用**而非全域開關。

---

## 8. 主要結果（v2 完整戰役，40 案）

### 8.1 WIL 對照（八度週期現象）
| N | config B 贏家（差距 dB） | config C 贏家（差距 dB） | Waksman MRR | Beneš MRR | 省 |
|---:|---|---|---:|---:|---:|
| 3 | **Waksman** (0.412) | **Waksman** (0.168) | 3 | 6 | 50% |
| 4 | Beneš (0.005) | Beneš (0.158) | 5 | 6 | 17% |
| 5 | **Waksman** (0.552) | **Waksman** (0.637) | 8 | 20 | 60% |
| 6 | **Waksman** (0.373) | **Waksman** (0.209) | 11 | 20 | 45% |
| 7 | Beneš (0.457) | Beneš (1.279) | 14 | 20 | 30% |
| 8 | Beneš (0.403) | Beneš (1.312) | 17 | 20 | 15% |
| 9 | **Waksman** (1.168) | **Waksman** (0.450) | 21 | 56 | **62.5%** |
| 10 | **Waksman** (0.856) | *route_failed* | 25 | 56 | 55% |
| 11 | **Waksman** (0.824) | *route_failed* | 29 | 56 | 48% |
| 12 | **Waksman** (0.624) | *route_failed* | 33 | 56 | 41% |

**兩個結構性發現**：
1. **WIL 的勝負是八度週期的**：在每個八度的下半段（N 剛超過 2 的冪次）Waksman 贏，
   上半段（N=7,8）輸，2 的冪次幾乎平手。原因：padded Beneš 在一個八度內 WIL 幾乎是常數
   （N=5..8 全部用同一張 benes-8 版圖，C 車道 WIL 恆為 2.486），而 Waksman 隨 N 穩定爬升。
   → 交叉點約在 **N ≈ 1.5 × 2^k**。
2. **MRR 數量是全域嚴格優勢**（15%–62.5%），且是平滑的 W(n) 曲線 vs Beneš 的階梯。

### 8.2 N=9 的三重全贏（case study）
```
                  Waksman      Beneš-16 template
worst path 長度    3268 µm      3857 µm
worst path xings   20 (B) / 32 (C)   33 (B) / 36 (C)
fabric 總 xings    96           232
MRR                21           56
B 配置 WIL 分解：
  Waksman 6.787 = prop 6.536 + MRR 0.251
  Beneš   7.956 = prop 7.714 + MRR 0.241
  → 差距 1.17 dB 幾乎全部來自路徑長度差 589 µm × 0.002
```
Padding 同時懲罰三個維度：元件數 2.7×、總 crossing 2.4×、worst path 被迫在兩倍高的 fabric 裡跑。

### 8.3 論文主敘事（依數據校準後）
> **不能宣稱**「Waksman WIL 更低」（N=4,7,8 會被打臉）。
> **可以宣稱**：Waksman 對確定性佈局的 padded Beneš——MRR 數量在所有 N 嚴格更少
> （W(n) 平滑擴展 vs padding 階梯，N=9 省 62.5%），同八度公平畫布下 WIL 在下半八度更低、
> 上半八度以可量化的小幅代價（≤1.3 dB）換取元件數與控制成本優勢；
> 且系統規模選擇權在設計者手上：挑 N=9–12 這類剛過 2 冪次的規模，兩者兼得。

---

## 9. 已知限制與未驗證假設（誠實清單）

### 9.1 損耗模型的問題
1. **傳播損耗 0.002 dB/µm = 20 dB/cm**，比典型 foundry SOI strip（1–3 dB/cm）高 ~10×，
   硬編碼、無 config 覆寫、無 placeholder 註記。B 配置的 WIL 96.7% 來自這一項。
2. **Bend 弧長是「外加」而非「取代」直角轉角**：真實 fillet 長度 = polyline − 2R + πR/2，
   程式卻 +πR/2 → 每個 bend 多算 2R=10 µm（三組 worst IL 各膨脹 0.48/0.76/0.88 dB）。
3. **Cross 態重複計一次 ring transit**：on 態 S 參數已含 ~0.048 dB ring 損，內部幾何又計 ~0.047 dB。
4. **同一 path 穿越同一 crossing 兩臂時被 set 去重**（n8 worst path 實際 28 次、報 27 次）。
5. **完全沒有 crosstalk（SXR）**：repo 自己的邏輯層模型顯示 waksman 8×8 有 2279/2400 取樣路徑
   違反 20 dB SXR 規格（worst 11.68 dB）——「全 permutation 通過」只保證 IL。

### 9.2 物理缺口（逐一 grep 確認無程式碼）
熱串擾（僅有 24 µm 同 stage 間距的常數 proxy）、heater 功耗、共振漂移與調諧範圍、
製程變異（所有 ring 共用同一個 s_table 物件）、相干干涉與相位（library 有 phase_rad 但 loader 只取 |S|²）、
波長相依（161 點頻譜塌縮成單點，且預設 1550 = 恰好 on-resonance 的最佳情況）、
偏振、多通道同時串擾、光電共設計、封裝/光纖耦合、可測性結構。

另一個重要的 library 假設：**off 態 = detune 半個 FSR（10.164 nm）**——
以 dλ/dT ~0.08–0.1 nm/K 計需要 ΔT > 100 K，實務上不可行（實際 switch 多在 1–2 nm detune 工作，
extinction 會比 −21.8 dB 差很多）。

### 9.3 DRC 的覆蓋範圍
`DRC=0` 只代表 8 條 centerline 規則全過，且 `allow_crossings=true` 讓 crossing 永遠不可能違規。
缺席：線寬控制、實際彎曲半徑/離散化（目前是尖角，radius 只是長度加項）、
**crossing cell 佔位**（v4 正在補）、taper、port 朝向、同 net 間距（v2 後為審計非門檻）、金屬/heater 層。

### 9.4 三類繞線失敗的病因學（重要）
| 類型 | 代表案例 | 計數器簽名 | 根因 | 解法 |
|---|---|---|---|---|
| **壅塞** | pb9 (16 線 A*) | pops 耗盡、soft_repeated_crossing 主導 | 走廊容量 < 需求 | envelope 容量公式（已解） |
| **自我圍困** | waksman n10-C | pops 極低(92)、same_net 主導(63) | 同 net 的 wrap 幾何堵死自己的出口 | search-time 同 net 間距紀律（有 witness 證明可行） |
| **外部保留區阻擋** | waksman n8-C (v3) | port_access_foreign 主導、frontier 窮盡 | dy 放大後的外部 runway 保留區 | 保留區階梯鬆綁 + port stagger |

**關鍵觀察**：三類**沒有一類**需要經典的 global routing——在 PIC 這個尺度（最多 128 條 edge），
病都生在精確幾何層，這獨立驗證了 LiDAR 「不做 global routing」的論點。

---

## 10. 目前卡住的問題（最想討論的）

### Q1. Crossing 佔位的 trade-off 怎麼解？
v4 消融實驗顯示：crossing 顯式插入 + 佔位登記在 **N=10 B 車道有益**（−0.4 dB）、
**C 車道阻擋繞線**、**N=4 有害**。目前的想法是「依情境啟用」，但什麼是正確的啟用準則？
（密度？crossing 罰項與傳播損耗的比值？N？）

### Q2. C 車道（寫實 crossing 損耗）的可繞性崩潰
crossing = 0.1 dB 時等效罰項 = 500 µm，router 拚命閃避 → 大量迂迴 → 纏繞自身 → 繞不出來。
v3 的 search-time 紀律讓小 N（4、6）大幅改善，但 N≥10 仍失敗。
是否應該：(a) 接受「Waksman 在 C 係數下 N≥10 不可繞」作為結論？
(b) 把 Waksman 也做成 template（零搜尋）？(c) 其他？

### Q3. Waksman template 可行嗎？
Beneš 的 template 成功是因為 butterfly 是完全規則的。Waksman 移除了部分 switch 導致結構不規則，
但它仍是遞迴的。有沒有辦法設計一個確定性的 Waksman 佈局（軌道指派可解析算出）？

### Q4. SXR 評估的計算策略
IL 有「路徑分解」這個捷徑（N! → ~500），但 SXR 是排列耦合量，沒有這個性質。
N=12 有 4.79 億排列 × 每排列 N 條干擾源。有沒有聰明的界限論證或取樣策略？

### Q5. 論文定位
Waksman 1968 本來就是交換網路理論（不是新的），但**積體光子交換晶片領域幾乎沒有人用它**
（已流片的都是 Beneš 家族，且清一色 2 的冪次規模）。
目前的定位：「首次在積體 MRR 光子交換 fabric 的**實體層**系統性評估 Waksman reduction」。
這個定位夠強嗎？八度週期性的發現本身有多少價值？

---

## 11. 驗證基礎設施（可信度的來源）

| 閘門 | 內容 |
|---|---|
| G1 | template crossing 數 == 理論預測（benes-4/8/16 = 14/60/232） |
| G2 | template 案 DRC 全綠（legacy + bend + same-net + perpendicular） |
| G3 | 彎折合法性 by construction：最小彎距 ≥ 10.0 µm（實測恰好 10.0） |
| G4 | 確定性：兩次 emission → 相同 geometry hash；`astar_calls == 0` |
| G5 | Envelope 公平性：每個八度單一 envelope_id |
| G6 | Padding 重用：同八度所有 N 的 template geometry hash 相同 |
| G7 | Guides 不傷害（註：已發現 guide 是 no-op，測試已改為記錄現實） |
| 雙法一致 | `method_agreement.csv` 要求 100% |
| Golden hash | 三族 geometry hash（n4/n6/n8）在預設旗標下必須 bit-identical |
| Witness 憑證 | 每案存 worst permutation，`--verify-certificates` 一步重驗 |
| 測試套件 | ~293 tests，全套 ~6 分鐘 |

**方法論紀律**：所有新行為 config-gated 且預設關閉；golden 只在明確的版本化幾何變更點重生；
幾何旋鈕不得逐案調整（公平性憲法）；任何閘門失敗立即停下並原文回報；
失敗現場完整保留（audit 子目錄 + FAILURE_HISTORY.md）。

---

## 附錄 A：理論出處（哪些是前人已證、哪些是我們的）

| 構件 | 狀態 | 出處 |
|---|---|---|
| Beneš RNB + looping 演算法 | 前人已證 | Beneš 1962; Opferman & Tsao-Wu 1971 |
| Waksman 網路、W(n)、深度最優性 | 前人已證 | Waksman 1968 (JACM); AS-Waksman: Beauquier & Darrot |
| Channel crossing 下界 = inversions；swap-sort 達成 | 前人已證 | Condrat, Kalla & Blair, SLIP 2013 |
| 欄式 Beneš 晶片佈局慣例 | 前人已證（但都是 MZI） | Lu et al. OE 2016 (16×16); Qiao et al. Sci Rep 2017 (32×32) |
| Detailed router 機制（port access、orientation A*、crossing insertion、history cost） | 前人已證 | LiDAR, Zhou/Zhu/Gu, ISPD 2025 |
| 平行 max-reduce 窮舉 | 初等 | — |
| **引理：max over perms == max over realizable paths** | **我們的**（trivial 但需明寫） | 需在論文附錄給 2 行證明 |
| **策略相對性**（決定性策略只用路徑子集） | **我們的**（實證 + 易證） | Beneš n8 的 80/256 為證據 |
| **T(N) = inversions + 2·n_MRR**（add-drop wrap 的 crossing 模型） | **我們的**（雙推導 + 機器驗證） | G1 閘門為機器證據 |
| **Octave envelope 公平法** | **我們的**（方法論） | — |
| **八度週期的勝負規律** | **我們的**（實證發現） | 40 案數據 |

## 附錄 B：關鍵檔案索引
```
codex_algorithm_notes.md          演算法細節（雙法、template、憑證）
ARCHITECTURE.md                   模組地圖 + v3 修剪紀錄
GEOMETRY_V3.md                    幾何統一 changelog
claude_waksman_fixed_fabric_review.md   2026-08-02 完整審查報告（51 項發現）
codex_v3_survey_architecture.md   架構盤點（死碼證據、常數清單、golden 關鍵路徑）
codex_v3_survey_anchors.md        設計錨點（pkl 往返、合法性原語、dy 級聯實測）
codex_v4_crossing_task.md         v4 任務書（含三類失敗的證據錨點）
final_results/                    v2 戰役成果（metrics.csv、charts/、REPORT、REVIEW）
outputs/nsweep_fixed_fabric_v2/   40 案完整產物（含 witness 憑證、失敗 audit trail）
outputs/nsweep_fixed_fabric_v3_mini/V2_V3_COMPARISON.md   v3 幾何統一的對照表
outputs/nsweep_fixed_fabric_v4_mini_gate/gate_results.json  v4 閘門失敗數據
outputs/crossing_insert_ablation/ crossing 機制消融實驗
```
