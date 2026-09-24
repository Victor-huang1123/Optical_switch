# The Wrap Tax: Physical Crossing Counts Overturn Topology Selection in Microring Switch Fabrics

**論文初稿 v0.2** ｜ 2026-09-20

> 狀態說明：本文是結構與論證的初稿。公式與數據為現行實測值，標記
> `[PENDING]` 者尚未取得或需重跑。規劃性內容（場次、阻擋項、審稿風險）
> 收在附錄 A，投稿時刪除。
>
> v0.2 相對 v0.1 的變更：Preliminaries 補足排列網路；新增 §3 合成框架
> （拓撲建構方法）；§4 改為以「單環設計點」開場，明確界定相對前人的設計差異。

---

## Abstract

Permutation networks for microring-resonator (MRR) switch fabrics are commonly
selected by counting switching elements: fewer rings is taken to mean lower
insertion loss, smaller area, and lower power. We show this criterion fails,
and identify what replaces it.

We build a synthesis framework that expresses Beneš, Waksman, and Spanke-Beneš
fabrics in one permutation-free stage representation — wires never change
track, and all topology lives in which wire pairs a stage's switches straddle —
so that a single placement, routing, and evaluation engine serves every family.

Realising such a fabric from *single* four-port add-drop rings, rather than from
pairs of 1×2 rings, halves the ring count but introduces a layout cost that
switching theory does not model: the two bus waveguides of a physical add-drop
ring counter-propagate, forcing one additional *wrap* crossing per cell. The
physical crossing count is therefore `T(P) = I(P) + C(P)` rather than the
textbook `X(N) = (N/2)(N − log₂N − 1)`, giving 14 / 60 / 232 against 2 / 16 / 88
at P = 4 / 8 / 16 — an understatement of 7× falling to 2.6×.

Under the corrected model, element count does not predict worst-case insertion
loss: at N = 8 the two topologies tie exactly, and padded Beneš wins at N = 7
and N = 8 despite using more rings. What does predict the ranking is the
crossing-loss coefficient, so we report the breakeven value at which the
ranking inverts rather than a single verdict.

Every loss figure is produced by two independent engines — an exhaustive `N!`
enumeration and a polynomial path-space search with witness certificates —
required to agree bit-exactly.

---

## 1. Introduction

**選擇拓撲的標準準則是數元件。** 在 MRR 開關 fabric 的設計中，2×2 開關元件
的數量被當成損耗、面積與功耗的代理指標。這個推理在電學交換網路成立，
也被沿用到光子實作。

**照這個準則，Waksman 應該大勝 Beneš。** 在 N = 9 時元件數分別是 56 與 21，
少 62.5%。padded Beneš 因補齊到 2 的冪次而呈 octave 階梯，Waksman 則平滑
成長。任何以元件數為準則的比較都會選 Waksman。

**它沒有贏。** 我們合成並繞出完整實體 fabric、以窮舉法計算 worst-case
insertion loss，結果是：N = 8 時兩者精確平手，最差路徑上的 MRR 損耗逐位元
相同；而 N = 7 與 N = 8 兩個損耗配置下 padded Beneš 實際勝出。

**原因在元件數看不見的地方。** 實體損耗由傳播長度與波導交叉主導，兩者皆由
佈局決定。更關鍵的是：排列網路理論給的交叉數公式對**單環** add-drop 實作
並不成立。理論假設的 2×2 元件兩路訊號同向；實體 add-drop 環的兩條匯流排
逆向傳播，導致每顆 cell 強制一個額外的 wrap crossing。

### 1.1 Contributions

1. **統一的 fabric 合成框架（§3）。** 以無 stage-permutation 的 stage 表示法
   涵蓋 Beneš、Waksman、Spanke-Beneš 三個家族與四種變體：線永不換軌，
   拓撲全部編碼在「某一級的開關跨接哪一對 wire」之中。此表示法使單一套
   放置、繞線與評估引擎可服務所有家族，並且是確定性佈局 template 得以成立
   的前提。狀態指派以奇偶約束的二分著色求解，並由獨立的模擬器逐一驗證。

2. **單環實作的實體交叉數模型（§4）。** 以單一四埠 add-drop 環實作 2×2 元件
   （相對於以兩顆 1×2 環組成）使環數減半，但引入排列網路理論未建模的佈局
   代價。我們證明每顆 cell 恰產生一個 wrap crossing，給出
   `T(P) = I(P) + C(P)`，並以逐一枚舉驗證 P = 4/8/16 為 14/60/232，
   對照教科書值 2/16/88。此稅為單環實作特有，MZI 與雙環實作不需支付。

3. **以 breakeven 取代「哪個拓撲較好」（§7）。** 在修正後的交叉模型下，
   兩拓撲的 worst-case 插入損耗排序隨交叉損耗係數翻轉。我們給出翻轉點，
   使設計者能代入自身製程參數讀出結論，而非依賴我們選定的單一係數。

4. **雙引擎驗證與憑證（§6）。** 每筆損耗數據由兩個獨立演算法計算並要求
   逐位元相等，每案輸出 witness 排列與幾何雜湊，可供第三方重算。

---

## 2. Preliminaries

### 2.1 Rearrangeably non-blocking permutation networks

一個 `N × N` 排列網路由 2×2 開關元件（switching element, SE）組成，每個 SE
有 bar 與 cross 兩個狀態。網路稱為 **rearrangeably non-blocking (RNB)**，
若對任一排列 `π ∈ S_N` 均存在一組 SE 狀態使每個輸入 `i` 抵達輸出 `π(i)`；
與 strictly non-blocking 的差別在於新增連線時允許重新配置既有連線。

**Beneš 網路**以遞迴建構：`P × P` 網路 = 一列 `P/2` 個輸入 SE
+ 兩個 `P/2 × P/2` 子網路 + 一列 `P/2` 個輸出 SE。展開後

$$D_{\text{Beneš}}(P) = 2\log_2 P - 1, \qquad
C_{\text{Beneš}}(P) = \frac{P}{2}\left(2\log_2 P - 1\right)$$

其中 `D` 為深度（級數）、`C` 為 SE 總數。Beneš 網路的 SE 數在 RNB 網路中
接近資訊理論下界 `log₂(N!) ≈ N log₂N − N·log₂e`。

**Waksman 網路**是 Beneš 的最佳化：注意到遞迴中最後一個輸入 SE 的選擇
對結果無影響（可固定為 bar 而不失一般性），每一層遞迴可移除一個 SE。
深度不變，元件數降為

$$C_{\text{Waksman}}(N) = \left\lceil N\log_2 N - N + 1 \right\rceil$$

原始構造僅定義於 2 的冪次；本文採用 Beauquier 與 Darrot 的 **AS-Waksman**
推廣以支援任意 `N`，其遞迴將 wire 依奇偶位置交錯分割成兩個子網路。

**Spanke-Beneš 網路**（亦稱 planar permutation network）僅使用相鄰 wire 的
SE，因而平面可佈線、無需 wire 交換。三角形式深度 `2N − 3`，
矩形（奇偶磚牆）形式深度 `N`，SE 數皆為 `N(N−1)/2`。其路由即為
odd-even transposition sort，正確性由排序定理給出。

**Padding.** Beneš 僅定義於 2 的冪次。對 `N` 非 2 的冪次者，取
`P = 2^{\lceil \log_2 N\rceil}`，將多餘的 `P − N` 個埠標記為封鎖埠。
padding 使元件數在每個 octave 內維持常數，形成階梯，而 Waksman 平滑成長；
兩者的差距因此在 octave 邊界最大、在 octave 末端最小。此現象在 §7 是
勝負交替的主因。

**Routing（狀態指派）.** 給定 `π` 求 SE 狀態的經典解法是 looping algorithm
（Opferman 與 Tsao-Wu, 1971）：遞迴地決定每條連線走上子網或下子網，
限制是共用同一個輸入 SE 的兩條 wire 必須分屬不同子網，輸出側同理。
§3.3 說明本文如何以奇偶約束傳播實作之。

**教科書交叉數.** 以抽象 SE 實作、採用標準通道佈線時，Beneš 的通道交叉數為

$$X(N) = \frac{N}{2}\left(N - \log_2 N - 1\right)$$

在 `N = 4, 8, 16` 分別為 2, 16, 88。此式假設 SE 的兩路訊號同向，
§4 說明該假設對單環 add-drop 實作不成立。

### 2.2 The four-port add-drop microring

#### 2.2.1 Four ports, two states, four transmissions

一顆四埠 add-drop 微環諧振器由一個環與兩條匯流排波導組成。環以倏逝波耦合
於兩條匯流排，耦合係數對環與波導的間隙呈指數敏感，故該間隙是**器件參數**，
不是佈局自由度。四個埠依慣例命名為 `in`、`th`（through）、`add`、`drop`。

器件有兩個工作狀態，由環是否與工作波長共振決定：

$$s = 0 \;(\text{bar, off-resonance}): \quad \text{in}\to\text{th},\quad \text{add}\to\text{drop}$$
$$s = 1 \;(\text{cross, on-resonance}): \quad \text{in}\to\text{drop},\quad \text{add}\to\text{th}$$

離共振時光直接沿各自的匯流排通過（低損耗）；共振時光耦合進環再耦合出
另一條匯流排（高損耗）。這四個轉移窮盡了器件的全部行為，且恰對應
S 參數矩陣的四條穿透率：

| S 參數 | 器件轉移 | 狀態 |
|---|---|---|
| `A → B` | `in → th` | bar |
| `A → D` | `in → drop` | cross |
| `C → B` | `add → th` | cross |
| `C → D` | `add → drop` | bar |

因此本文的單元損耗是**查表而非近似**：給定一條路徑上每顆環的
`(進入埠, 離開埠)`，損耗直接由 S 參數表查得，不需要有效折射率、
耦合係數或品質因子等中間模型。這也意味著埠映射一旦與器件不符，
損耗數值即失去意義——§2.2.3 說明此約束為何不可為了佈局方便而放寬。

#### 2.2.2 Port geometry: side and role are independent

實作採用的埠座標（相對 cell 中心，單位 µm，`d_x = 8.0`、`d_y = 5.5`）為

$$\text{in} = (-d_x, +d_y),\quad \text{th} = (+d_x, +d_y),\quad
\text{add} = (+d_x, -d_y),\quad \text{drop} = (-d_x, -d_y)$$

由此可讀出**兩組彼此獨立的分類**：

| 埠 | 幾何側別 | 光學角色 | 所屬匯流排 |
|---|---|---|---|
| `in` | 左 | 輸入 | 上 |
| `th` | 右 | 輸出 | 上 |
| `add` | 右 | **輸入** | 下 |
| `drop` | 左 | **輸出** | 下 |

側別與角色**不能互相推導**。上匯流排的輸入在左、輸出在右（左→右傳播）；
下匯流排的輸入在右、輸出在左（**右→左傳播**）。若誤將「左側即輸入」
當成通則，下線的方向就會被反推錯誤，而所有級間連線都會接錯。

這個不對稱是本文 §4 的起點：一個抽象的 2×2 開關假設兩路訊號同向，
而這個器件不是。

#### 2.2.3 The convention is fixed by the device, not by convenience

處於 cross 狀態的訊號自 `in`（左上）進入、自 `drop`（左下）離開，
在 cell 內的水平行程為 `2d_x = 16` µm；bar 狀態則自左貫穿至右，
行程同為 `2d_x`。此內部行程計入路徑長度。

值得強調的是：把 `add` 與 `drop` 對調（即改為同向傳播）會使級間連線
單調、佈局變得容易，但那描述的是**另一個器件**，與 S 參數表所描述者不符。
本文因此固定此慣例，並在 §4 承擔其佈局代價，而非以修改器件模型迴避之。
該慣例已於全部 `6! = 720` 個排列 × 三個拓撲上驗證：每個 `RouteStep`
皆為合法的 add-drop 轉移，且 S 參數查表與狀態逐一相符。

### 2.3 Worst-case insertion loss on a fixed fabric

本文的 fabric 是**固定**的：波導幾何一次繞出、與排列無關；切換排列只改變
環的共振狀態，不改變任何幾何。worst-case 因此有明確物理意義——同一顆晶片、
所有可能組態下最差的那一條光路。

$$\mathrm{IL}(p) \;=\; \alpha L(p) \;+\; n_b(p)\,\ell_b \;+\; n_\times(p)\,\ell_\times
\;+\; \sum_{i \in p} \ell_{\mathrm{MRR}}\!\left(s_i\right)$$

$$\mathrm{WIL} \;=\; \max_{\pi \in S_N} \;\max_{p \in \mathcal{P}(\pi)} \; \mathrm{IL}(p)$$

其中 `α` 為傳播損耗係數（dB/µm）、`L` 為路徑長度、`n_b` 與 `n_×` 為彎折與
交叉數、`ℓ_MRR(s)` 由 S 參數表依狀態查得、`P(π)` 為排列 `π` 的作用路徑集合。

---

## 3. Fabric Synthesis Framework

### 3.1 A permutation-free stage representation

四個拓撲家族在本文中共用同一個表示法。一個 fabric 是一串 stage，
每個 stage 是一組**互斥**的 wire 配對：

$$\mathcal{S} = \left(\;S_0,\; S_1,\; \dots,\; S_{D-1}\;\right),
\qquad S_k = \left\{(a_1,b_1),\,(a_2,b_2),\,\dots\right\},\quad
a_i < b_i,\;\; \bigl|\{a_i,b_i\}\cap\{a_j,b_j\}\bigr| = 0 \;\;(i \neq j)$$

每個配對 `(a, b)` 放置一個 SE，跨接 wire `a` 與 `b`。未出現在 `S_k` 中的
wire 在該級直接通過。

**關鍵設計決定：wire 永不換軌。** 級間不存在固定置換佈線，拓撲的全部連接
關係編碼在「某一級的開關跨接哪一對 wire」之中。等價地說，第 `k` 級的作用是

$$\sigma_k(w) = \begin{cases}
b & \text{if } (w,b)\in S_k \text{ 且該 SE 為 cross}\\
a & \text{if } (a,w)\in S_k \text{ 且該 SE 為 cross}\\
w & \text{otherwise}
\end{cases}$$

整個 fabric 實現的排列即 `σ_{D-1} ∘ … ∘ σ_1 ∘ σ_0`。此處沒有與狀態無關的
固定置換項，這正是「permutation-free」的意思。

> **為何這個選擇重要。** 因為 wire 不換軌，佈局就是一組固定的水平軌道加上
> 跨接其上的 SE，放置、繞線、DRC 與損耗評估都不需要知道自己在處理哪一個
> 家族。它同時是 §5.1 確定性 template 得以成立的前提：**若級間存在固定置換
> 佈線，通道內的交叉數就不再等於單純的軌道逆序**，§4.3 的 `I(P)` 論證會失效。

#### 3.1.1 Beneš: butterfly encoded as pair distance

Beneš 的第 `k` 級以固定距離 `d_k` 配對。令 `L = log₂P`，距離序列為

$$d = \left(2^0,\, 2^1,\, \dots,\, 2^{L-1},\, 2^{L-2},\, \dots,\, 2^0\right),
\qquad |d| = 2L - 1$$

第 `k` 級在每個大小為 `2d_k` 的區塊內，將 `block + j` 與 `block + j + d_k`
配對（`j = 0 … d_k − 1`）。因此每級恰有 `P/2` 個 SE，總數
`C(P) = (P/2)(2L − 1)`。

| P | 距離序列 | 級數 | SE 數 |
|---|---|---:|---:|
| 4 | `(1, 2, 1)` | 3 | 6 |
| 8 | `(1, 2, 4, 2, 1)` | 5 | 20 |
| 16 | `(1, 2, 4, 8, 4, 2, 1)` | 7 | 56 |

以 `P = 8` 為例，完整的配對集合為

```
stage 0  (d=1):  (0,1) (2,3) (4,5) (6,7)
stage 1  (d=2):  (0,2) (1,3) (4,6) (5,7)
stage 2  (d=4):  (0,4) (1,5) (2,6) (3,7)
stage 3  (d=2):  (0,2) (1,3) (4,6) (5,7)
stage 4  (d=1):  (0,1) (2,3) (4,5) (6,7)
```

距離序列的先增後減即 Beneš 的遞迴結構：`d` 遞增的前半對應「分派到子網路」，
`d = 2^{L-1}` 的中央級對應最粗的分割，遞減的後半對應「自子網路合併」。
把它寫成距離而非置換佈線，是本文表示法與教科書圖示的主要差異。

#### 3.1.2 Waksman: interleaved recursion and the removed switch

Waksman 以遞迴產生配對。對 wire 序列 `w`（長度 `n`）：

1. 外層取**相鄰**配對 `(w_0,w_1), (w_2,w_3), …`，共 `⌊n/2⌋` 個；
2. 兩個子網路分別遞迴於 `w[0::2]`（偶數位）與 `w[1::2]`（奇數位）；
3. 輸出側再取一列配對，但**省略最後一個**（Waksman 最佳化）；
4. `n` 為奇數時最後一條 wire 直接落入上子網路。

遞迴產生的實際結果：

```
waksman N=4  (3 級, 5 SE)        waksman N=6  (5 級, 11 SE)
  stage 0: (0,1) (2,3)             stage 0: (0,1) (2,3) (4,5)
  stage 1: (0,2) (1,3)             stage 1: (0,2) (1,3)
  stage 2: (0,1)          ← 省略    stage 2: (0,4) (1,5)
                                    stage 3: (0,2) (1,3)
waksman N=5  (5 級, 8 SE)          stage 4: (0,1) (2,3)
  stage 0: (0,1) (2,3)
  stage 1: (0,2) (1,3)
  stage 2: (0,4)        ← 奇數落單
  stage 3: (0,2)
  stage 4: (0,1) (2,3)
```

與 Beneš 對照可見兩點差異。其一，**輸出級不是滿的**：`N = 4` 的第 2 級只有
一個 SE 而非兩個，省下的那一個即 Waksman 最佳化，也是 §3.3 中求解器的
固定變數來源。其二，**子網路的 wire 在實體上不相鄰**：因為分割取的是
`w[0::2]` 與 `w[1::2]` 而非前後半，`N = 6` 的第 2 級出現 `(0,4)` 與 `(1,5)`
這種跨距為 4 的配對。這使 Waksman 的 fabric 具有原生交叉，
也是 §5.2 必須改用搜尋繞線的原因。

此外，省略 SE 使**路徑深度不再齊一**。以 `N = 4` 為例，輸入 0 與輸入 2
只經過 2 顆環，輸入 1 與輸入 3 經過 3 顆（見 §3.2 的 worked example）。
Beneš 的每條路徑則一律經過 `2L − 1` 顆。這對 worst-case 分析是有利的
不對稱，但也意味著平均與最差的差距更大。

#### 3.1.3 Spanke-Beneš: adjacent pairs only

兩個變體皆只使用相鄰配對 `(w, w+1)`，故平面可佈線。三角形式的第 `k` 級
跨度為 `min(k, 2N−4−k)`、共 `2N − 3` 級；矩形形式為奇偶磚牆，
偶數級配對 `(0,1),(2,3),…`、奇數級配對 `(1,2),(3,4),…`，共 `N` 級。
兩者 SE 數皆為 `N(N−1)/2`。本文將其作為 §4.3 交叉模型的額外驗證點，
不進入主線比較。

#### 3.1.4 Padding and blocked ports

Beneš 僅定義於 2 的冪次。對非 2 的冪次的 `N`，取
`P = 2^{\lceil\log_2 N\rceil}` 建構 `P × P` 的 fabric，並將埠
`N, N+1, …, P−1` 標記為封鎖埠。求解時目標排列自 `S_N` 延伸至 `S_P`：

$$\tilde\pi = \left(\pi(0),\,\dots,\,\pi(N-1),\; N,\; N+1,\;\dots,\; P-1\right)$$

即封鎖埠恆映至自身。例如 `N = 6` 使用 `P = 8` 的 fabric，封鎖埠為 `{6, 7}`，
SE 數為 20 而非 Waksman 的 11。

**padding 是 octave 階梯的來源**：同一 octave 內所有 `N` 共用同一張 fabric，
元件數、交叉數與版圖完全相同，而 Waksman 隨 `N` 平滑成長。兩者差距因此在
octave 起點最大、在 octave 末端最小，這是 §7.3 勝負交替的直接原因。

### 3.2 From wires to device ports

§3.1 的表示法是純圖論的，§2.2 的器件是實體的。兩者的接合點是一組映射，
本節說明之。這組映射是全流程中唯一將抽象開關對應到實體埠的地方，
其正確性決定所有損耗數值的意義。

#### 3.2.1 The mapping

令某一級的配對為 `(u, ℓ)`，其中 `u < ℓ`。依 §2.2 的軌道約定，
`u` 為上匯流排、`ℓ` 為下匯流排。訊號自 wire `w` 進入該 SE、自 wire `w'`
離開，則

$$\mathrm{inPort}(u,\ell,w) = \begin{cases}\texttt{in} & w = u\\ \texttt{add} & w = \ell\end{cases}
\qquad
\mathrm{outPort}(u,\ell,w') = \begin{cases}\texttt{th} & w' = u\\ \texttt{drop} & w' = \ell\end{cases}$$

$$\mathrm{state}(w, w') = \begin{cases}
0 \;(\text{bar}) & w' = w\\
1 \;(\text{cross}) & w' \neq w,\; \{w,w'\} = \{u,\ell\}
\end{cases}$$

其餘情形為非法轉移，實作直接拋出例外。注意**進入埠由進入的 wire 決定、
離開埠由離開的 wire 決定**，兩者各自獨立；狀態則由兩者是否相同決定。
這與 §2.2.2 的「側別與角色獨立」是同一件事在拓撲層的表現。

由此得到的四種組合恰好窮盡 §2.2.1 的四條 S 參數穿透率，且一一對應：

| `w → w'` | 埠轉移 | 狀態 | S 參數 |
|---|---|---|---|
| `u → u` | `in → th` | bar | `A→B` |
| `ℓ → ℓ` | `add → drop` | bar | `C→D` |
| `u → ℓ` | `in → drop` | cross | `A→D` |
| `ℓ → u` | `add → th` | cross | `C→B` |

#### 3.2.2 Path construction

給定排列 `π` 與其狀態指派，一條路徑由逐級推進 wire 索引構造：自
`w ← i` 開始，於每一級查找包含 `w` 的配對；若無則該級直接通過，
若有則依狀態更新 `w`，並記錄一筆

$$\texttt{RouteStep} = \bigl(\text{mrr\_id},\; k,\; (u,\ell),\; w,\; w',\;
\mathrm{inPort},\; \mathrm{outPort},\; \mathrm{state}\bigr)$$

推進至最後一級即得輸出埠。路徑的插入損耗為其所有 `RouteStep` 的
S 參數查表值加上級間佈線的實體損耗（§2.3）。

#### 3.2.3 Worked example

以 `π = (2, 0, 3, 1)` 為例。padded Beneš（`N = P = 4`，6 顆環）的狀態解為
`{s0:(0,1)=0, s0:(2,3)=1, s1:(0,2)=1, s1:(1,3)=0, s2:(0,1)=1, s2:(2,3)=0}`，
四條路徑為：

| 輸入 | 級 | 配對 | wire | 埠轉移 | 狀態 |
|---|---|---|---|---|---|
| **0 → 2** | 0 | (0,1) | 0→0 | `in → th` | bar |
| | 1 | (0,2) | 0→2 | `in → drop` | cross |
| | 2 | (2,3) | 2→2 | `in → th` | bar |
| **1 → 0** | 0 | (0,1) | 1→1 | `add → drop` | bar |
| | 1 | (1,3) | 1→1 | `in → th` | bar |
| | 2 | (0,1) | 1→0 | `add → th` | cross |
| **2 → 3** | 0 | (2,3) | 2→3 | `in → drop` | cross |
| | 1 | (1,3) | 3→3 | `add → drop` | bar |
| | 2 | (2,3) | 3→3 | `add → drop` | bar |
| **3 → 1** | 0 | (2,3) | 3→2 | `add → th` | cross |
| | 1 | (0,2) | 2→0 | `add → th` | cross |
| | 2 | (0,1) | 0→1 | `in → drop` | cross |

三點值得注意。第一，**同一條 wire 在不同級可能扮演不同角色**：輸入 1 在
第 0 級是配對 `(0,1)` 的下線（`add`／`drop`），在第 1 級是配對 `(1,3)` 的
上線（`in`／`th`）。角色由該級的配對決定，不是 wire 的固有屬性。
第二，路徑經過的 bar／cross 組合直接決定其損耗，`3 → 1` 連續三個 cross
是本例中最差的一條。第三，每條路徑恰經過 3 顆環，等於 Beneš 的深度。

同一個 `π` 在 Waksman（5 顆環）上的解則呈現深度不齊一：

| 輸入 | 經過環數 | 路徑 |
|---|---|---|
| 0 → 2 | **2** | `in→th` (s0), `in→drop` (s1) |
| 1 → 0 | 3 | `add→drop` (s0), `in→th` (s1), `add→th` (s2) |
| 2 → 3 | **2** | `in→drop` (s0), `add→drop` (s1) |
| 3 → 1 | 3 | `add→th` (s0), `add→th` (s1), `in→drop` (s2) |

省略的 SE 使輸入 0 與 2 少經過一顆環。Waksman 因此不僅環數較少，
部分路徑的環損耗也較低——這正是「元件數少即損耗低」這個直覺的來源，
而 §7 說明它為何仍不成立。

### 3.3 State assignment as parity-constrained two-colouring

looping algorithm 在本文實作為二分著色問題。遞迴的一層裡，共用同一個輸入
SE 的兩條 wire 必須分屬不同子網，寫成奇偶約束

$$\mathrm{color}[a] \oplus \mathrm{color}[b] = 1$$

輸出側對 `π^{-1}` 施加同樣的約束。求解以傳播進行：任取未著色變數指定 0，
沿約束邊以 `expected = color[current] ⊕ parity` 傳播，若與既有著色衝突
即表示該排列不可路由。著色結果直接就是 SE 狀態，再將 `π` 拆成兩個子排列
遞迴處理。

**Waksman 的差異在於初始條件。** Beneš 求解時所有變數自由；Waksman 因每層
移除一個 SE，該位置成為求解器中的一個**固定變數**。換言之，
「少一個元件」在演算法層面即等於「少一份自由度」。這為前人在 `N = 4`
觀察到的「移除 SE 反而增加受限組態數」提供了結構性解釋（§9）。

Spanke-Beneš 的狀態指派則是 compare-exchange：
`s = 1 if rank[a] > rank[b] else 0`，整個網路即一次奇偶轉置排序。

**獨立驗證.** 一個反向模擬器 `realize(·)` 取一組狀態、模擬 wire 交換、
回傳實現的排列。任何策略的輸出皆可由 `realize(states) = π` 獨立檢查。
在小尺寸另有一個窮舉所有 `2^C` 種狀態組合的反查表策略，用以逐一驗證
遞迴策略在 `N ≤ 6` 的每個排列上皆給出正確答案。此「以顯然者驗證精巧者」
的模式在 §6 再次出現。

### 3.4 Placement

wire 的縱向位置由拓撲固定：環耦合哪兩條匯流排是 stage 表示法決定的，
而環與波導的間隙是器件參數（倏逝波耦合對間隙指數敏感）。因此

$$y_{\text{wire}}(k) = (P - 1 - k)\cdot p_w, \qquad
y_{\text{cell}} = \tfrac{1}{2}\left[y_{\text{wire}}(u) + y_{\text{wire}}(\ell)\right]$$

每顆 cell 僅餘 `x` 一個自由度。本文採釘死的 octave envelope：同一 octave 內
所有 `N` 共用一組 canvas、stage pitch 與 wire pitch，使 padding 的比較公平，
且幾何雜湊可重現。

---

## 4. Single-Ring Realisation and the Wrap Tax

### 4.1 The design point: one ring per 2×2 element

一個 2×2 SE 可以有兩種 MRR 實作：

| | 兩顆 1×2 環 | **單顆四埠 add-drop 環（本文）** |
|---|---|---|
| 環數／SE | 2 | 1 |
| `C_Waksman(4)` | 10 | **5** |
| 匯流排方向 | 可安排同向 | 逆向 |
| wrap crossing | 無 | 每顆 cell 一個 |

以單顆 add-drop 環實作 2×2 開關在光子網路文獻中是常見做法，**本文不宣稱
此設計點為新**。前人以 MRR 分析 Beneš 與 Waksman 的工作（§9）採用兩顆
1×2 環組成一個 SE，因而其 SE 數恰為本文的兩倍，並且迴避了以下問題。

本文的立場是：採用單環設計點，並**計價其佈局後果**。這個後果此前未被
量化，因為量化它需要一個實體佈局層。

### 4.2 Counter-propagation forces a wrap

由 §2.2，`in` 與 `drop` 同在 cell 左側、`th` 與 `add` 同在右側。
於是走下線的訊號自 cell 的**左**側（`drop`）離開，卻必須連到下一級 cell 的
**右**側（`add`），而下一級在其右方。該連線必須反向繞回。

**Wrap 佈局慣例（convention A, knot-free）.** 本文採用的解法是：上線自 `in`
直通 `th`，維持在 `y = c + 5.5` 的軌道；下線的 inbound 走南軌
`y = c − h_s` 進入右側的 `add`，outbound 自左側的 `drop` 升至北軌
`y = c + h_n` 越過 cell。offset 由 residue 規則選取，保證所有 riser 的
垂直段長度不小於 `2R`（`R` 為最小彎曲半徑），故佈局無需事後合法化。

**引理 1（wrap lemma）.** 在此慣例下，每顆 cell 恰產生一個 wrap crossing。

*論證.* 上線的直通段橫跨 cell 全寬且位於 `y = c + 5.5`。下線的 inbound 自
右側進入、outbound 自左側離開並升至 `y = c + h_n > c + 5.5`。由於 outbound
的起點在上線直通段之下（`c − 5.5`）而終點在其上（`c + h_n`），且其水平位置
落在 cell 左緣，兩者必相交；又因 inbound 全程位於 `y ≤ c − h_s < c + 5.5`，
不產生額外相交。故恰一次。∎

**實測驗證.** 直接枚舉 `P = 4` 佈局的全部 14 個交叉，其中恰有 6 個
（`= C(4)`）位於 `(dx, dy) = (-24, +5.5)`，即各 cell 左緣的 `in`／`drop`
交叉，每顆 cell 各一，與引理 1 一致。

### 4.3 Channel crossings and the corrected count

級間通道的交叉由軌道指派決定。本文採單 riser 的 swap-sort 特化：
每個通道中每條線配置一個垂直 riser 軌，上行線依出發 `y` 降序排列、
下行線依升序排列。此排序使通道內的交叉恰等於該通道排列的逆序對、
且每對僅交叉一次，達到 Condrat、Kalla 與 Blair (SLIP 2013) 給出的下界。

令 `I(P)` 為全部通道的逆序數總和，`C(P)` 為 SE 數，則

$$\boxed{\;T(P) \;=\; I(P) \;+\; C(P)\;}$$

| P | `I(P)` | `C(P)` | `T(P)` 實測 | 教科書 `X(N)` | 低估倍率 |
|---|---:|---:|---:|---:|---:|
| 4 | 8 | 6 | **14** | 2 | 7.0× |
| 8 | 40 | 20 | **60** | 16 | 3.75× |
| 16 | 176 | 56 | **232** | 88 | 2.6× |

`P = 8` 的逐通道逆序數分解為 `[0, 6, 12, 12, 6, 4]`，總和 40。
三個 `T(P)` 值在實作中作為硬性閘門：任何佈局變更若改動之即視為回歸。

> **內部註記（投稿前刪除）：** repo 既有筆記
> （`codex_algorithm_notes.md:98`、`PROJECT_OVERVIEW_FOR_DISCUSSION.md`）
> 將本式誤記為 `I + 2C`，與其自身給出的 benes-8 分解（40 + 20 = 60）矛盾。
> 已於 2026-09-20 以逐一枚舉確認正確式為 `I + C`。兩處需更正，且已公開於 GitHub。

---

## 5. Physical Routing

### 5.1 Deterministic template for Beneš

由 §3.1 的無置換表示法，Beneš 的佈局可直接構造：軌道固定、通道軌道指派
由 swap-sort 給出、wrap 由 convention A 給出。因此 Beneš 完全不需要搜尋，
其交叉數有封閉形式，且兩次產生的幾何雜湊逐位元相同。

### 5.2 Search-based routing for irregular fabrics

Waksman 的交錯遞迴破壞了上述規則性——子網路的 wire 不相鄰，通道內不存在
單調的軌道指派——故無已知的確定性構造，必須以搜尋繞線。

搜尋狀態除座標外包含行進方向與已直行距離，使「不得立即回頭」與
「相鄰彎折間距 ≥ 2R」等製造約束在展開鄰居時即被過濾，而非事後罰分。
步進成本分為兩層：物理項（長度、交叉、彎弧、同網間距）直接等於損耗模型，
引導項（壅塞、歷史成本、轉向時機）乘以一個小係數僅用於打破平手。

> **此非對稱本身是一項結果，不是實驗瑕疵。** Beneš 之所以有 template
> 而 Waksman 沒有，源於前者的遞迴規則性。§7.4 量化其價值。

---

## 6. Evaluation Methodology

### 6.1 Two independent engines

**引擎一（窮舉）.** 枚舉全部 `N!` 個排列，依前綴分割平行處理，
每個排列取其最差路徑，再取全域最大。正確性顯然，成本為階乘。

**引擎二（路徑空間 + 見證）.** 不枚舉排列而枚舉**路徑**。固定 fabric 上的
路徑數為多項式。將所有路徑依損耗降序排列，自上而下檢查是否存在排列
真正使用該路徑；第一條找得到見證者即為答案。

**引理 2.** $\displaystyle \max_{\pi \in S_N} \max_{p \in \mathcal{P}(\pi)} \mathrm{IL}(p)
\;=\; \max_{p \,\in\, \mathcal{R}} \mathrm{IL}(p)$，
其中 `R` 為可實現路徑集合。

*論證.* 任一作用路徑皆屬於某排列，故左式不小於右式；任一排列的最差路徑
本身即為一條可實現路徑，故右式不小於左式。∎

此結構與靜態時序分析的關鍵路徑分析加 false-path elimination 同構：
枚舉路徑、依代價排序、以可實現性排除偽路徑。據我們所知，光子開關 fabric
的損耗分析尚未採用此紀律。

### 6.2 Bit-exact agreement as the acceptance criterion

兩引擎必須逐位元相等：一者指數但顯然正確，一者多項式但推理細緻，
要求相等即以顯然者驗證精巧者。每個案例另輸出 witness 排列與幾何雜湊。

---

## 7. Experimental Results

### 7.1 Setup

| 項目 | 設定 |
|---|---|
| 拓撲 | padded Beneš、Waksman（Spanke-Beneš 兩變體作為交叉模型的額外資料點） |
| 尺寸 | 實體層 `N = 3…9`；元件數 scaling 以解析式延伸 |
| 器件資料 | 實測 S 參數庫（through 0.993 / 0.029 dB，消光 −45 dB） |
| 配置 B | `ℓ_× = 0`（隔離交叉效應的對照組） |
| 配置 C | `ℓ_× = 0.1` dB/交叉 |
| 傳播損耗 | `α = ` `[PENDING]` — 現值 0.002 dB/µm 為佔位值 |
| 評估 | 雙引擎，要求 100% 一致 |

實體層限縮於 `N ≤ 9`：該範圍 28 個案例全部完成繞線、零失敗、雙引擎
100% 一致。`N ≥ 10` 的部分案例在給定搜尋預算內未收斂，該限制屬繞線器
而非拓撲性質（§8.3）。

### 7.2 Element count is a clean win for Waksman

| N | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---:|---:|---:|---:|---:|---:|---:|
| padded Beneš `C(P)` | 6 | 6 | 20 | 20 | 20 | 20 | 56 |
| Waksman `C(N)` | 3 | 5 | 8 | 11 | 14 | 17 | 21 |
| 節省 | 50.0% | 16.7% | 60.0% | 45.0% | 30.0% | 15.0% | 62.5% |

節省率在每個 octave 邊界達峰後於 octave 內衰減，形成鋸齒。此曲線為純組合
結果，不需繞線，可延伸至任意 `N`。

### 7.3 The loss ranking does not follow

| N | 3 | 4 | 5 | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|
| 配置 B 勝方 | Wak | 平手 | Wak | Wak | **Beneš** | **Beneš** | Wak |
| 配置 C 勝方 | Wak | Beneš | Wak | Wak | **Beneš** | **Beneš** | Wak |

在 `N = 8`，兩拓撲最差路徑皆穿越 5 顆 MRR，MRR 損耗項逐位元相同。
勝負由 `N` 落在 octave 的位置決定：Beneš 在 octave 末端（padding 攤提
最充分處）勝出，Waksman 在跨越 octave 之後勝出。`[PENDING: 對稱化重跑後
確認 N=7,8 兩點]`

### 7.4 The crossing coefficient decides

| `ℓ_×` (dB/交叉) | Waksman WIL | Beneš WIL | 勝方 |
|---|---:|---:|---|
| 0 | 4.576 | 4.750 | Waksman |
| 0.1 | 3.976 | 2.527 | Beneš |

（`N = 8`；breakeven `ℓ_×^*` = `[PENDING: 掃描後填入]`）

> 我們刻意不承諾單一個 `ℓ_×`。文獻報告的 SOI 波導交叉損耗分布甚廣且
> 強烈依賴交叉幾何與製程。正確的輸出因此是一條曲線與一個翻轉點，
> 這同時使結論對係數的不確定性穩健。

### 7.5 The value of layout regularity, in dB

| 案例 | A* | template | 差距 |
|---|---:|---:|---:|
| N=4, 配置 C | 1.1939 | 1.1920 | 0.002 |
| N=4, 配置 B | 2.5668 | 2.4824 | 0.084 |
| N=10, 配置 B | 8.5472 | 7.9557 | **0.592** |

差距隨 `N` 成長。此量即「拓撲具備確定性佈局」的價值。方法受控的比較
（雙方皆用 A*）與各自最佳方法的比較兩者皆報告。

### 7.6 Verification record

| 指標 | 值 |
|---|---|
| 雙引擎一致 | 37 / 37 可繞線案例（100%） |
| coverage failure | 0 |
| legacy DRC / bend-radius 違規 | 0 / 0 |
| 每案憑證 | witness 排列 + 幾何雜湊 |

---

## 8. Limitations

1. **傳播損耗係數。** 現用值為佔位值，約為矽光典型值的十倍，且佔 WIL 絕大
   部分。投稿前須改為文獻或製程實測值。該項主導於側壁粗糙度散射，
   屬製程統計效應，不宜以電磁模擬取得。
2. **無串音模型。** 實體層未建模 crosstalk；邏輯層 8×8 的 SXR 為 11.7 dB，
   低於 20 dB 規格。
3. **繞線器限制而非拓撲限制。** `N ≥ 10` 的部分配置在給定預算內未收斂。
   消融實驗顯示，僅關閉交叉插入合法性檢查即可使其中一例由三小時未收斂
   變為 781 秒收斂，故屬搜尋策略限制。本文據此限縮範圍，並**不以繞線失敗
   支持任何拓撲主張**；§5.2 與 §7.5 的規則性論證為結構性論證。
4. **方法不對稱。** 見 §7.5，已量化並雙向報告。
5. **成本模型校準。** 既有校準的特徵集包含繞線後結果，具循環性，本文不引用。

---

## 9. Related Work

Beneš 與 Waksman 構造、AS-Waksman 推廣、looping algorithm、通道交叉下界
等於逆序數、odd-even transposition sort 的正確性，皆為既有成果。

**與 MRR 實作最接近的前人工作**為 Garrich 的碩士論文（Politecnico di
Torino, 2009），該工作以 MRR 分析包含 crossbar、Beneš 與 Waksman 在內的
多種交換架構，並明確指出其 SE 呈現「severe asymmetric behaviour」。
兩者差異如下：

| 面向 | Garrich 2009 | 本文 |
|---|---|---|
| 每個 2×2 SE 的環數 | 2（兩個 1×2 組成） | **1（單環 add-drop）** |
| 對逆向傳播的處理 | 以雙環迴避 | 承擔並計價（§4） |
| Waksman 的 `N` | 僅 `N = 4` | `N = 3…9` |
| 損耗量化 | high-loss state 計數 × 2.3 dB | 實測 S 參數逐路徑合成 |
| 波導交叉 | 未建模 | 核心變數 |
| 實體佈局 | 無 | 放置 + 繞線 + DRC |

該工作亦觀察到 Waksman 在 `N = 4` 未優於 Beneš，並指出移除 SE 會減少組態
自由度。本文 §3.3 以約束求解的觀點為此現象提供結構性解釋，並將比較延伸
至實體層。

**「兩倍環數換取無 wrap」對「半數環數支付 wrap」構成一個此前未被定價的
架構取捨**，其定價需要實體佈局層，故此前無法進行。

---

## 10. Conclusion

以開關元件數選擇 MRR 開關 fabric 的拓撲並不可靠。元件數少不蘊含損耗低：
在 `N = 8` 兩拓撲精確平手，在 `N = 7` 與 `N = 8` 元件數較多者反而勝出。

決定排序的是實體交叉數，而以單環 add-drop 實作時，該數不等於排列網路
理論給出的值：逆向傳播的雙匯流排使每顆 cell 強制一個 wrap crossing，
`T(P) = I(P) + C(P)`，在 `P = 4/8/16` 為 14/60/232，而非 2/16/88。

在修正後的模型下，拓撲排序隨交叉損耗係數翻轉。本文因此不主張某一拓撲
較優，而給出翻轉點，使設計者得以代入自身製程參數作出選擇。

---
---

# 附錄 A：撰寫規劃（投稿前刪除）

## A.1 投稿場次

**建議走 EDA 場次**（ICCAD / DATE / ASP-DAC / ISPD），不走光子學期刊。
貢獻形狀是設計自動化的（合成框架、成本模型修正、窮舉驗證）；光子學場次
會要求量測或完整元件模擬，補齊耗時，而 EDA 場次接受純模擬但要求驗證強度，
那正是本工作最強處。

## A.2 阻擋投稿的項目

| 項目 | 處理方式 | 估計成本 |
|---|---|---|
| `α` 佔位值 | 引用文獻或製程實測，**不要用 FDTD** | 一行設定 + 一次重跑 |
| N=7、8 的方法對稱重驗 | Beneš 以 A* 重跑該兩點 | 約 15 分鐘 |
| breakeven 掃描 | 掃 `ℓ_×`，填入 §7.4 | 數小時 |
| 三個未收斂案例 | 範圍限縮 `N ≤ 9` | 零 |
| 先前技術檢索 | Waksman × {silicon photonics, microring, photonic NoC}、AS-Waksman | 半天 |
| 筆記 `2C` 誤記 | 更正兩份公開筆記 | 十分鐘 |

**FDTD 不是阻擋項。** 主張若為 breakeven 而非單點排序，則不需自行量測
交叉損耗；掃描係數並引用文獻範圍即可。FDTD 留給期刊延伸版。

## A.3 審稿風險與備妥回答

| 攻擊 | 回答 |
|---|---|
| 損耗係數不物理 | 主張是 breakeven 曲線，結論對係數不確定性穩健 |
| 拿 template 比搜尋 | 兩條 lane 皆報告，差距已量化並本身即為結果（§7.5） |
| Waksman on MRR 2009 已有 | 已引用；該工作為 2 環/SE 的不同設計點、無實體層、僅 N=4 |
| 單環 add-drop 當 2×2 不是新的 | §4.1 已明確不宣稱其為新；新的是其佈局後果的計價 |
| 繞線器在大 N 失敗 | 範圍限縮，且明示為搜尋策略限制（§8.3） |
| 無流片量測 | EDA 場次；貢獻為模型與流程，以雙引擎窮舉與憑證取代量測 |

## A.4 刻意不寫入的內容

- LP 與 SA 放置（不在正式流程上）
- 繞線器內部工程細節（交叉簽章支配鍵、HistoryCost、rip-up）壓成一段
- v4 的 crossing gating 消融實驗（僅在 §8.3 引用其結論）
- Spanke-Beneš 不進主線敘事，僅作為交叉模型的額外資料點

## A.5 待確認的事實

- `I(P)` 是否對 Waksman 亦有封閉形式？目前僅 Beneš template 有驗證值。
  若無，§4.3 的表僅涵蓋 Beneš，Waksman 的交叉數須以實測報告。
- §7.3 的 `N = 8` 精確平手，其 WIL 數值需在 `α` 更新後重新產生。
- §7.4 的兩列數據取自既有 routing upgrade 驗證，需確認繞線設定與主實驗
  一致，否則應重跑。
- §2.1 稱 Beneš SE 數「接近資訊理論下界」，投稿前補上精確的比值或改為定性敘述。
- 引理 1 的論證依賴 `h_n > 5.5` 與 inbound 全程位於 `y ≤ c − h_s`；
  投稿前確認這兩個不等式在所有 `N` 的 residue 規則下皆成立，否則須加條件。
