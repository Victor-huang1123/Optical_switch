# MRR 開關合成：演算法導讀

從排列到版圖，六層管線各自解什麼問題、用什麼演算法、程式在哪裡。

本文分兩種內容。正文描述程式碼實際做的事，行號與常數皆取自現行碼。
以 `> **觀點**` 標示的區塊是我的判斷與新穎度評估，不是程式碼事實，請當作可以反駁的意見讀。

---

## 0. 整體形狀

```
排列 π ∈ S_N
   │
   ├─① 拓撲建構      π 無關，只由 N 決定 ──→ stage_pairs
   │
   ├─② 狀態指派      每顆 MRR 該 bar 還是 cross ──→ StateAssignment
   │
   ├─③ 元件映射      2×2 抽象開關 ──→ 實體 add-drop 四埠
   │
   ├─④ 放置          每顆 MRR 的座標 ──→ cells
   │
   ├─⑤ 實體繞線      波導幾何（一次繞線，π 無關）──→ routes + crossings
   │
   └─⑥ 損耗評估      worst-case insertion loss ──→ WIL + 憑證
```

關鍵設計決定在第五層：**fabric 只繞一次**。波導是固定的，換排列只改變 MRR 的狀態，
不改變任何幾何。這讓第六層可以在同一張版圖上窮舉所有排列。

> **觀點**｜這個「fixed fabric」決定是整個專案最重要的架構選擇，而且它是對的。
> 替代方案是每個排列各繞一次，那樣 worst-case 分析會變成對 N! 張不同版圖取最大，
> 既無法製造也無法驗證。固定 fabric 讓「最差情況」有明確物理意義：同一顆晶片，
> 最差的那個組態。這一點在論文裡值得明講，因為它決定了後面所有指標的可解釋性。

---

## ① 拓撲建構

**輸入** N ｜ **輸出** `stage_pairs` ｜ **程式** `core/topology.py`

所有拓撲化約成同一個表示法：一串 stage，每個 stage 是一組互斥的 wire 配對，
一個配對就是一個 2×2 開關。

```python
stage_pairs = (
    ((0,1), (2,3)),     # stage 0：兩個開關
    ((0,2), (1,3)),     # stage 1
    ...
)
```

| 拓撲 | 遞迴／排程 | 級數 |
|---|---|---|
| Padded Beneš | butterfly，前後半分割 | `2·log₂P − 1` |
| Waksman | 遞迴，偶／奇交錯分割，每層少一個開關 | `2·log₂N − 1` |
| Spanke-Beneš（三角） | 三角排程 | `2N − 3` |
| Spanke-Beneš（矩形） | 奇偶磚牆 | `N` |

Waksman 的遞迴與 Beneš 不同，值得看清楚。Beneš 把 wire 切成 `wires[:half]` 與
`wires[half:]`；Waksman 切成 `wires[0::2]` 與 `wires[1::2]`，也就是偶數位與奇數位交錯。

```python
# core/topology.py · _build_waksman_for_wires
outer_pairs  = ((w[0],w[1]), (w[2],w[3]), …)   # 相鄰配對
upper_stages = recurse(wires[0::2])            # 偶數位子網
lower_stages = recurse(wires[1::2])            # 奇數位子網
```

交錯分割意味著子網的 wire 在實體上**不相鄰**。這就是
`WaksmanTopology.has_native_crossings()` 回傳 true 的原因，也是後面繞線困難的根源。

> **觀點**｜這一層完全是教科書內容，新穎度為零，而這沒有問題。Beneš、Waksman
> （正確說是 Beauquier & Darrot 的 AS-Waksman）、Spanke-Beneš 都是既有構造。
> 統一成 `stage_pairs` 是乾淨的工程選擇，讓四個家族共用同一套狀態指派與繞線，
> 但那是可維護性不是研究貢獻。論文裡這一節應該寫得短，當作背景交代過去就好。
> 唯一值得強調的是「交錯分割導致 native crossings」這個因果，因為它是後面
> 可繞線性論證的起點。

---

## ② 狀態指派：looping 演算法

**輸入** π ｜ **輸出** 每顆 MRR 的 0/1 ｜ **程式** `core/state_assignment.py`

問題：給定排列 π，決定每個 2×2 開關要 bar 還是 cross，使每個輸入都到達正確輸出。

### 化約成二分著色

遞迴的一層裡，每個輸入開關接兩條 wire，這兩條 wire **必須**去不同的子網路，
否則子網路會超載。輸出側同理。把「必須不同」寫成一條奇偶約束，問題就變成圖著色。

```
constraint = (left, right, parity)
parity = 1  意思是  color[left] XOR color[right] = 1   （必須不同）

_solve_binary_constraints:
    建鄰接表 → 對每個未著色變數指定 0 → BFS 傳播
    傳播規則  expected = color[current] XOR parity
    衝突則 raise（表示這個排列不可路由）
```

著色結果 `colors[wire]` 直接就是該開關的狀態：0 走上子網、1 走下子網。
解完把 π 拆成兩個子排列，遞迴處理。這就是教科書的 looping algorithm，
用奇偶約束傳播寫出來比「來回繞圈」的敘述更好實作也更好驗證。

### Waksman 的差別

Beneš 呼叫時 `fixed` 是空的，所有變數自由。Waksman 會先釘死一部分：

```python
# core/state_assignment.py · _assign_waksman_recursive
if n % 2:
    fixed[wires[-1]] = 0            # 奇數尺寸的落單 wire
else:
    fixed[input_a] = 0              # 最後一組輸出配對沒有開關
    fixed[input_b] = 1              # （Waksman 省掉的那一個）
```

省掉一個開關，等於在著色問題裡多一條已知條件。

> **觀點**｜這裡有一個小但真實的洞見值得寫進論文：**「少一個元件」在演算法層面
> 就等於「少一份自由度」，而且是字面意義上的——它變成約束求解器裡的一個 `fixed` 項。**
>
> 2009 年 Politecnico di Torino 那篇碩論在 N=4 實驗觀察到 Waksman 的受限狀態
> 反而變多（"the constraint states grow because of that less freedom"），但他們是
> 用窮舉統計觀察到的。你的實作等於給了這個現象一個結構性解釋。
> 這不是大貢獻，但它把一個經驗觀察升級成機制說明，而且免費。
>
> 至於 looping 演算法本身：Opferman & Tsao-Wu 1971 的經典成果，二分著色的
> 重述也是已知的。新穎度為零。

### 四種求解策略

| 策略 | 方法 | 適用 |
|---|---|---|
| `BruteForceLUTStrategy` | 枚舉 2^(開關數) 種組合，建反查表 | `n_MRR ≤ 20` |
| `BenesLoopingStrategy` | 遞迴著色，先 bit-reverse 對齊 butterfly | 2 的冪次 |
| `WaksmanStrategy` | 遞迴著色加 `fixed` 釘死 | 不支援 padding |
| `SpankeBenesStrategy` | compare-exchange，本質是氣泡排序 | 任意 N |

Spanke-Beneš 那條特別直觀：`state = 1 if desired_rank[a] > desired_rank[b] else 0`。
每個開關是一個比較器，整個網路是一次奇偶轉置排序，正確性直接來自排序定理。

驗證函式 `realize_state_assignment` 反向跑：拿狀態模擬 wire 怎麼被交換，
看最終排列是否等於目標。任何策略的輸出都能被它獨立檢查。

> **觀點**｜建構法與驗證法完全解耦，這是好工程。更值得注意的是
> `BruteForceLUTStrategy` 的角色：它在小 N 窮舉所有狀態組合建反查表，
> 於是可以用它去驗證遞迴策略在 N≤6 的每一個排列上都給出正確答案。
> 用「笨但顯然對」的方法驗證「聰明但難證」的方法——這個模式在第六層會再出現一次，
> 而且是整個專案最強的可信度來源。建議在論文的 methodology 一節把這個模式明說。

---

## ③ 元件映射：抽象 2×2 變成實體環

**輸入** 配對 + 狀態 ｜ **輸出** 四埠轉移 ｜ **程式** `core/models.py`

到這層為止都還是圖論。這裡第一次碰到真實器件，而且有一個容易被忽略的落差。

```
pair = (upper, lower)

輸入側：  wire == upper → "in"      wire == lower → "add"
輸出側：  wire == upper → "th"      wire == lower → "drop"

state 0（bar，離共振）：  in → th      add → drop
state 1（cross，共振）：  in → drop    add → th
```

四個轉移剛好對應 S 參數檔裡的四條穿透率，所以損耗查表是精確的而非近似。

### 逆向傳播與 wrap 稅

埠的實體位置是 in 左上、th 右上、add **右**下、drop **左**下。
也就是上匯流排由左往右、下匯流排由右往左，**兩條匯流排逆向傳播**。

排列網路理論假設的 2×2 元件是兩路訊號同向的。單環 add-drop 不是。
走下線的訊號從 cell 左側離開，卻得接到下一級 cell 的右側，必須繞回去，
**每顆 cell 因此強制產生一個 wrap crossing**。

| P | 教科書 `X(N) = (N/2)(N−log₂N−1)` | 實體 `T(P) = inversions + n_MRR` |
|---|---|---|
| 4 | 2 | 14 |
| 8 | 16 | 60 |
| 16 | 88 | 232 |

差額就是 add-drop wrap 的物理代價。這個稅是單環實作特有的，MZI 實作不會付。

> **實測更正（2026-09-20）**｜repo 筆記（`codex_algorithm_notes.md:98`、
> `PROJECT_OVERVIEW_FOR_DISCUSSION.md`）把這條寫成 `inversions + 2·n_MRR`，**那是錯的**。
> 直接枚舉 P=4 的 14 個交叉可見，恰好 6 個（= n_MRR）落在每顆 cell 左緣
> `dx=−24, dy=+5.5`，即 `in` 進線與 `drop` 出線的交叉，**每顆 cell 剛好一個**。
> 這也與 G1 閘門註記的 benes-8 分解一致（channel inversions `[0,6,12,12,6,4]=40` 加 20 wrap = 60）。
> 正確分解為 8+6 / 40+20 / 176+56。那兩處筆記需要更正，且它們已公開在 GitHub 上。

> **觀點**｜**我認為這是整個專案新穎度最高的一點**，理由有三。
>
> 第一，它是修正性的而非增量性的。排列網路的交叉數公式是這個領域拿來估面積與
> 損耗的基礎，而它對單環 add-drop 實作系統性低估，倍率從 7× 收斂到 2.6×。
> 指出一個大家在用的公式在特定實作下不成立，比多做一個拓撲有價值。
>
> 第二，它是結構性的、可證明的，不依賴你的 router 品質。這點很重要，因為你的
> 其他發現（例如 N≥10 路不出來）已經被證實至少部分是 router 的產物——
> 把 `explicit_crossings` 關掉，原本三小時路不出來的 n10-C 就在 781 秒路通了。
> wrap 稅不會有這個問題，它來自埠的幾何定義。
>
> 第三，它會改變結論。你自己的資料顯示拓撲勝負隨交叉損耗係數翻轉，
> 所以交叉數怎麼算不是內部細節，是論文主張的樞紐。
>
> **但有兩個保留。**其一，2009 那篇也遇到了同樣的不匹配（摘要寫
> "severe asymmetric behaviour"），只是用兩顆 1×2 環組成一個 2×2 SE 來迴避。
> 所以「add-drop 不是乾淨的 2×2」這個觀察本身不新，新的是**實體層後果的定價**。
> 這個區別在寫作時必須講清楚，否則會被認為在宣稱別人已經說過的事。
>
> 其二，我沒有做過系統性的先前技術檢索。一篇 2009 碩論是偶然找到的，
> 可能還有別的。在把這條當成主打之前，應該掃過 Waksman + silicon photonics、
> microring、photonic NoC、AS-Waksman 這幾組關鍵字。
>
> 順帶一提，2009 用 2 環/SE、你用 1 環/SE，`C_Waksman(N=4)` 他們是 10、
> 你是 5，剛好一半。**「兩倍的環換掉 wrap 稅」對「一半的環付 wrap 稅」**
> 是一個真實的架構取捨，而且從來沒有人定價過，因為沒有人做過實體層。
> 我認為這個對照本身就可以撐起論文的一節。

---

## ④ 放置

**輸入** 拓撲 ｜ **輸出** cells 座標 ｜ **程式** `placement/`, `routing/envelope.py`

Y 座標不是自由變數。每顆 MRR 耦合哪兩條匯流排是拓撲決定的，
而環到波導的間隙是器件參數（倏逝波耦合對間隙指數敏感），所以：

```
wire_y(k)  = (N_physical − 1 − k) · wire_pitch
cell_y     = ½ · [ wire_y(pair₀) + wire_y(pair₁) ]
```

於是每顆 MRR 只剩 X 一個自由度。正式 campaign 走釘死的 octave envelope
（固定 stage 間距、wire 間距、起點），為了讓幾何雜湊可重現。
LP 與 SA 屬於舊 CLI 流程，形式見另一份 LP 文件。

> **觀點**｜這一層目前是專案裡最不重要的一塊，原因不是它不有趣，而是它不在
> 關鍵路徑上。正式結果不走 LP，而且 worst-case 損耗由傳播損耗主導，
> 吸收係數又是偏高十倍的佔位值——修那個係數的影響遠大於任何放置最佳化。
>
> 如果之後要回頭做，我會建議先做最便宜的探路：把 `wire_pitch` 當純量掃描
> （36 → 28 → 24），看縱向壓縮對損耗的實際影響。現有程式已接受這個參數，
> 零開發成本。純量掃描沒動靜的話，per-wire 的 LP 也不會有。

---

## ⑤ 實體繞線

**輸入** cells + fabric graph ｜ **輸出** routes + crossings ｜ **程式** `routing/`

兩條並行實作路線，依拓撲選擇。

### Template 路線（Beneš）

Beneš 遞迴規則，佈局可以直接構造，完全不搜尋。`benes_template_layout.py`
用通道配置產生幾何，交叉數有封閉形式（逆序數加 wrap），
`predicted_template_crossings` 直接把 4/8/16 的答案寫死當閘門。這條路 `astar_calls == 0`。

### A* 路線（Waksman 與其他）

Waksman 沒有已知的確定性構造，只能搜尋。搜尋狀態不只是座標：

```python
# routing/grid_router.py
RouterState(
    x_idx, y_idx,                  # 網格座標
    orientation,                   # 目前行進方向 N/S/E/W
    straight_run_um,               # 已直行距離（彎折間距約束用）
    crossing_arm_remaining_um,     # 插入交叉後還須直行的距離
)
```

把方向放進狀態，才能表達「不能立刻回頭」和「兩個彎折之間要隔 2R」這類製造約束。
`neighbor_moves` 在展開時就濾掉違規移動，而不是事後罰分。

### 步進成本是分層的

```
物理項（不縮放，直接等於損耗模型）
    _step_length_cost          長度，µm 或 dB
    _crossing_step_cost        交叉損耗
    _bend_step_cost            彎弧長度與損耗
    _jog_step_cost             jog 的 dB
    _same_net_physical_cost    同網間距（v4 從引導項升級）

引導項（乘上 db_tie_breaker_scale = 0.05）
    backtrack / hairpin / soft blocker 壅塞
    turn_timing、preferred bend x
    history_cost               協商式壅塞
    reserve-space lookahead
```

`cost_model="db"` 時長度直接乘 `prop_loss_db_per_um`，搜尋目標與評估指標因此一致。

### 兩個非標準手法

- **支配鍵帶交叉簽章。** 一般 A* 用座標做支配判斷，但這裡合法性跟歷史有關
  （已經跟誰交叉過幾次會影響後續合法性），所以鍵是
  `(RouterState, crossing_signature)`，並用 `(cost, crossing_score)` 兩級比較，
  成本相同時偏好交叉較少的路徑。
- **HistoryCost 協商。** 繞線失敗不直接放棄，而是把擁擠位置的成本永久累加再重繞。
  這是 VLSI 的 negotiated congestion。

> **觀點**｜這一層的多數手法是成熟技術的移植：negotiated congestion 是
> PathFinder（Ebeling et al. 1995）、orientation-aware A* 與 crossing-as-move
> 來自 LiDAR（ISPD'25）、rip-up & reroute 是標準繞線流程。移植本身不構成研究貢獻。
>
> 真正有技術含量的是**支配鍵帶交叉簽章**那一項。標準 A* 的支配判斷假設
> 「同一個狀態的兩條歷史等價」，但當合法性依賴歷史時這個假設會壞掉，
> 丟掉可行解。把簽章併進鍵是正確的處理方式。這值得在論文裡寫一小段，
> 但它是實作貢獻，撐不起一篇論文的主張。
>
> **我要提醒一個寫作風險。** 不要把「A* 在 N≥10 的 C 配置路不出來」寫成
> 拓撲性質。ablation 已經證明那是 router 的產物：關掉 `explicit_crossings`
> 就在 781 秒路通。要主張「Waksman 較難繞線」，載重證據必須是結構性的
> （Beneš 遞迴規則因而存在確定性構造且交叉數有封閉形式；Waksman 的
> edge-splitting 破壞規則性因而沒有已知構造），繞線實驗只能當實務佐證。
> 這個區分會決定審稿人是接受還是一句話打掉。
>
> 另外 template 對 A* 的方法不對稱是目前最大的方法論風險。實測落差
> 隨 N 成長：n4-C 是 0.002 dB、n4-B 是 0.084 dB、n10-B 是 0.592 dB。
> 最後那個已經跟你想主張的拓撲差異同一量級。好消息是修正方向對你有利——
> 對稱化之後 Beneš 變差，Waksman 的勝場更穩；但 v2 裡 Beneš 獲勝的
> N=7 與 N=8 兩點就需要重驗。

---

## ⑥ 損耗評估與雙方法驗證

**輸入** routes ｜ **輸出** WIL + witness ｜ **程式** `analysis/nsweep.py`

要回答的是 worst-case insertion loss：所有排列、所有路徑中最差的那條損耗多少。
這裡用兩個獨立演算法算同一個數字，然後要求相等。

### 方法一：排列窮舉

`evaluate_fixed_fabric_parallel` 枚舉全部 N! 個排列，依前兩個元素切成前綴分給
最多 64 個 worker 平行跑，每個排列算出最差路徑，取全域最大。
正確性顯然，代價是階乘。

### 方法二：路徑空間加 witness

`evaluate_fixed_fabric_path_space` 反過來想：不枚舉排列，枚舉**路徑**。
固定 fabric 上的路徑數是多項式的。把所有路徑依損耗由大到小排序，
再由上往下逐條問「有沒有一個排列真的會用到這條路徑」。

```python
paths  = enumerate_fabric_paths(topology, graph)
losses = { p: _evaluate_path_loss(p, …) for p in paths }
ranked = sorted(paths, key=lambda p: -losses[p].insertion_loss_db)

for rank, path in enumerate(ranked, 1):
    witness, proof = _witness_for_path(topology, path)
    # 第一條找得到 witness 的路徑就是 WIL
```

最差的**可實現**路徑就是答案。損耗最高但沒有任何排列會用到的路徑要跳過，
這一步就是 `realizable` 欄位。

> **觀點**｜**這是我認為第二強的一點，而且它的價值被低估了。**
>
> 方法二本質上是把 static timing analysis 的招式搬到光子 fabric：
> 枚舉路徑、依代價排序、找關鍵路徑，然後用可實現性檢查排除 false path。
> STA 社群做了三十年，但據我所知光子開關的損耗分析多半是手算單一條最差路徑，
> 沒有這套紀律。把 false-path 的概念引進來是乾淨的跨領域移植，
> 而且在 EDA 場次會被欣賞。這個框架我建議明說，不要只當成實作細節。
>
> 更重要的是兩個方法的**互補性質**：一個指數但顯然正確，一個多項式但推理細緻，
> 要求兩者逐位元相等，等於用顯然的那個驗證聰明的那個。v2 campaign 的 37 個
> 可繞線案例全部一致、coverage failure 為 0、每案附 witness 排列與幾何雜湊。
>
> 這個領域的論文很少有這種驗證強度。它本身不是「新演算法」，
> 所以不適合當論文的主標題，但它應該是 methodology 一節的核心，
> 而且是你面對「你的數字怎麼來的」這類質疑時最有力的回答。

---

## 新穎度總評

以下純屬我的判斷，排序依「相對於既有文獻的獨特性 × 對結論的影響」。

| 排序 | 主張 | 強度 | 主要風險 |
|---|---|---|---|
| 1 | add-drop wrap 使實體交叉數為 `T(P) = inversions + n_MRR`，教科書 `X(N)` 對單環實作系統性低估 | 結構性、可證明、改變結論 | 先前技術未系統檢索 |
| 2 | 拓撲勝負由交叉損耗係數決定，存在 breakeven | 把不良定義的比較升級成設計準則 | 係數目前是猜的，需 FDTD |
| 3 | 雙方法驗證 + witness 憑證（STA false-path 移植） | 方法論紮實，領域內少見 | 非新演算法，不能當主標題 |
| 4 | 元件數不是損耗的良好預測指標（n8 精確平手） | 推翻領域預設推理 | 2009 已在 N=4 同向觀察 |
| 5 | 拓撲規則性有佈局成本（Beneš 有 template、Waksman 沒有） | 元件數分析看不見的維度 | 必須用結構論證，不能用 router 失敗 |
| 6 | 繞線工程（交叉簽章支配鍵等） | 正確且非平凡 | 增量性，撐不起主張 |

### 我認為該修正的框架

**不要說「第一個把 Waksman 用在 MRR 上」。** 2009 那篇已經做了，而且這種
「已知拓撲 × 新平台」的框架審稿人會直接歸類成 A+B。

有一個簡單的自我檢驗：**試著不提 Waksman 這個字，把貢獻說完。**
如果說得出來就是真貢獻。例如：

> 我們證明開關元件數在 MRR fabric 裡是插入損耗的劣質預測指標，真正主導的是
> 交叉損耗與佈局規則性，並給出交叉損耗的 breakeven 準則；其中交叉數本身
> 必須用 `T(P) = inversions + n_MRR` 而非教科書公式，因為單環 add-drop
> 的逆向傳播每顆 cell 強制一個 wrap crossing。

這句話裡沒有 Waksman。Waksman 是製造對照的工具，不是結果。

### 誠實的弱點清單

這些在投稿前需要處理，或至少在 limitation 明說：

- 吸收係數 `ALPHA_DB_PER_UM = 0.002`（= 20 dB/cm）約為矽光典型值十倍，
  而它佔 headline WIL 的 96.7%。這個數字不該用 FDTD 算（主導的是側壁粗糙度
  散射，是製程統計效應），應引用文獻或製程實測。**最便宜、對可信度提升最大的修正。**
- 交叉損耗係數是猜的。這是論文的樞紐變數，值得用 FDTD 跑單一個 crossing cell
  取得插入損耗、串音與 cell 尺寸。`min_crossing_clearance_um = 10.0` 旁邊本來
  就寫著 "until FDTD picks the cell"。
- 物理層完全沒有 crosstalk 模型；邏輯層 8×8 的 SXR 是 11.7 dB 對規格 20 dB。
- 比較的方法不對稱（Beneš 用 template、Waksman 用 A*），落差隨 N 成長到
  n10-B 的 0.592 dB。
- C4 校準有循環性（特徵含繞線後結果），未重做前不要引用。
- v2 有三個 route_failed（Waksman C 配置 N=10/11/12）。建議把實體層範圍
  限縮在 N≤9（該範圍 28 個案例全部 complete、零失敗、雙方法 100% 一致），
  MRR 數與級數的 scaling 則用解析式畫到任意 N——那條曲線是純組合數學，
  不需要 router。

---

## 建議的閱讀順序

前三個檔案加起來不到 900 行，讀完就掌握整個邏輯層。

| 順序 | 檔案 | 行數 | 先看什麼 |
|---|---|---|---|
| 1 | `core/models.py` | 78 | `port_for_wire`、`state_for_transition` |
| 2 | `core/topology.py` | 378 | `get_active_paths`、`build_waksman_stage_pairs` |
| 3 | `core/state_assignment.py` | 387 | `_solve_binary_constraints` 再往外看兩個遞迴 |
| 4 | `routing/grid_router.py` | 171 | `RouterState`、`neighbor_moves` |
| 5 | `routing/grid.py` | 1424 | `_astar_route` 的成本累加段 |
| 6 | `analysis/nsweep.py` | 501 | 兩個 evaluate 函式對照著看 |

`routing/physical.py` 有 5796 行，是繞線協調層（net 排序、候選產生、rip-up、
來源協商）。建議最後再碰，而且先看 `codex_v3_survey_architecture.md` 標出的區段界線。

**讀碼時要知道的坑：** `grid.py` 會把失敗原因格式化成字串，而 `physical.py`
用正規表示式把它*解析回來*當作重繞決策的依據。這個字串協議是已知技術債，
改動錯誤訊息會改變繞線行為。

---

*本文演算法描述取自 `mrr_switch_optimizer` 現行程式碼。放置層的 LP 形式另見
`codex_crossing_gating_task.md` 與 LP 專文。*
