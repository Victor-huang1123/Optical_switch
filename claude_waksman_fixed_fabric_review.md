# Waksman Fixed-Fabric 懷疑式審查報告

日期：2026-08-02
方法：9 個獨立審查維度平行檢查（拓撲正規性 / 一次性繞線 / 邊切割記帳 / 光學模型 / 損耗係數與 DRC / 輸出取證 / Beneš 對照 / legacy 滲漏 / 物理缺口），全部 critical/high 發現再經獨立對抗式驗證（re-read 程式碼 + 重跑 CLI 重現），6/6 CONFIRMED。
審查全程唯讀；對照實驗（padded-Beneš n8）輸出寫在 session scratchpad，repo 未被修改。

---

## 總結一句話

**Fixed-fabric 架構本身是健全的**：拓撲是正規 AS-Waksman（圖同構驗證通過）、實體 waveguide 確實只繞一次（以不同 `--permutation` 重跑得到 byte-identical geometry_sha256 證明）、permutation 只切 MRR 狀態、邊切割記帳精確到位。
**但 headline 數字（2.6419 / 4.1668 / 5.0173 dB）不是實體預測**：96.7% 來自 10 倍偏高的 propagation 常數、bend/crossing loss 歸零、完全沒有 crosstalk —— 而 repo 自己的邏輯層模型顯示 8×8 有 2279/2400 取樣路徑違反 20 dB SXR 規格。另外，**「Waksman 較少 MRR → 較低 worst IL」的敘事在 N=8 被實測反證**（與 padded Beneš 打平，甚至差 0.0006 dB）。

---

## A. 問題表格（依嚴重度排序）

> 無 critical（未發現「會讓 fixed-fabric 架構本身失效」的缺陷）。以下 6 項 HIGH 全部經對抗式驗證 CONFIRMED。

### HIGH

| # | 問題 | 位置 | 證據 | 後果 | 建議修正 |
|---|------|------|------|------|----------|
| H1 | **多通道 crosstalk（有限 extinction）完全不在宣稱的實體結果內** | `analysis/fabric_loss.py:144-153`、`app/fabric_reports.py:93-129` | fabric_loss.py 只沿 signal path 連乘 `s_table`，無任何 leakage/SXR 項；off 態 per-ring extinction 實測 −21.8 dB（`mrr_r5.00_ch1550.0_off.csv` \|S_DA\|²=0.00657）；repo 自己的邏輯層（`cost.py:493-513` `_mrr_leak_power`，20 dB spec）在 `N*N results/nxn_worst_il_v2p1_seed44/logical_matrix_summary.csv` 顯示 waksman 4/6/8 worst SXR = 15.86 / 12.26 / 11.68 dB，違規 40 / 3684 / 2279（8×8 為 300-perm 取樣）；`docs/waksman_fixed_fabric_results.md` 的 Scope 排除清單未列 crosstalk | 「40320 permutations 全通」只保證 IL，N 通道同時工作時 SXR 可能低於 12 dB —— **最接近「邏輯全通但實體不成立」的一項** | 把 `_mrr_leak_power` 邏輯搬到 routed geometry 上，於 `fabric_loss_summary.json` 增列 worst_sxr_db |
| H2 | **headline IL 把 bend/crossing loss 歸零**（n8 worst path：44 bends、27 crossings × 0.0 dB） | `routing/types.py:133-134`、`analysis/cost.py:16`、`outputs/.../n8/fabric_loss_summary.json:2-3` | 5.0173 = 0.1642 (MRR) + 4.8532 (prop)，bend/crossing 貢獻精確為 0；repo 自己的 `configs/nxn_worst_il_v2p2_hieffort.yaml:44-45`（0.005 dB/bend、0.1 dB/crossing）套在同一路徑 → +2.92 dB ≥ 7.94 dB（下限，worst perm 可能改變）；docs 表格（:9-13）未帶此註記，:44-49 才揭露 | headline 數字被低估 1–3 dB；Waksman 是 crossing-heavy 拓撲，歸零 crossing loss 在跨拓撲比較中系統性偏袒它 | 用 v2p2 係數重跑或雙欄並列呈現 |
| H3 | **0.002 dB/µm = 20 dB/cm，比典型 foundry SOI strip（1–3 dB/cm）高 ~7–20 倍，且無任何文件標示 placeholder** | `analysis/cost.py:14`（`ALPHA_DB_PER_UM`）→ `routing/types.py:132` → `cli.py:709` | configs/ 全部 yaml 都沒有覆寫 prop_loss；輸出確認採用 0.002；n8 worst IL 的 96.7% 是 propagation（4.853/5.017 dB） | 所有已發表 IL 數字被單一未標示常數放大 ~10×；「worst IL 比較」實際上量的是 floorplan 幾何長度 | 改為 config 顯式參數，預設附上來源註記；以 0.0002 dB/µm 重跑對照 |
| H4 | **Waksman 較少 MRR 在 N=8 對 worst IL 沒有任何優勢 —— 實測與 padded Beneš 打平（Waksman 反而差 0.0006 dB）** | `outputs/waksman_fixed_fabric_n8/.../fabric_loss_summary.json:17` vs 重跑 `--topology benes --n-logical 8` | 對抗式驗證重跑 CLI：padded Beneš 8×8 = 5.016731 dB（20 MRR）vs Waksman = 5.017315 dB（17 MRR）；47 個 rules key 逐一比對相同；geometry_sha256 與 repo 內 committed Beneš fabric 一致；兩者 worst path 都過 5 顆 MRR，worst_mrr_loss **byte-identical**（0.1641642673909558）；Waksman worst path crossing 較多（27 vs 17），非零 crossing loss 下會更差 | 「fewer MRRs → lower worst IL」的論文敘事在 2 的冪次 N 不成立；Waksman 真正贏的是 N=6（+0.665 dB，但 90% 來自免 padding 的幾何、僅 0.068 dB 來自 MRR） | 提交 Beneš fixed-fabric loss artifact；把 Waksman 優勢改寫為「元件數 17 vs 20、非 2 冪次 N 免 padding、平均路徑損耗」 |
| H5 | **`physical_fabric_routed_once=true` / `physical_routing_permutation_count=0` 是硬編碼常數，不是量測** | `app/fabric_reports.py:128-129`；測試 `tests/test_fixed_fabric_router.py:82-83` | 兩欄位是 dict literal 直接寫入 JSON，全 repo 無任何計數器；測試斷言常數等於常數（套套邏輯）。routed-once **性質本身為真**（見 B 節），但這兩個欄位不構成證據 | 未來若有人加入 per-permutation 繞線，這兩欄位仍顯示 0/true —— 假儀表 | 實作真計數器（router 呼叫次數）+ 每 permutation 重算 geometry hash 比對 |
| H6 | **在寫實係數下，worst path 的「身分」本身會改變** —— 目前的 worst-case 認定是係數假象 | `analysis/fabric_loss.py:155-161` + 重評分實驗 | 驗證代理重現三個 fabric（worst IL 誤差 <1e-6）並全路徑重評分：保持 0.002 prop 時 worst path 不變；改用 0.0002 dB/µm + 0.01–0.02 dB/bend + 0.02–0.2 dB/crossing 後，N=4 與 N=8 的 worst path 改變（n8: I1→O3 → I0→O3），n8 worst IL 變為 1.65–6.96 dB、由 crossing 主導 | 以目前係數挑出的 worst permutation/path 不能代表寫實條件下的瓶頸 | worst-IL 掃描需在係數網格上做敏感度分析，報告 worst-path 穩定性 |

### MEDIUM（精選，完整 51 項見 scratchpad `audit_findings.json`）

| # | 問題 | 位置 | 證據 / 後果 | 建議修正 |
|---|------|------|------|----------|
| M1 | **Bend 弧長「外加」而非「取代」直角轉角** —— 每個 bend 多算 2r=10 µm | `routing/fabric.py:278-280`、`fabric_loss.py:141-151` | 真實 fillet 長度 = polyline − 2r + πr/2（每 bend −2.146 µm），程式卻 +πr/2；24/38/44 bends → 三組 worst IL 各膨脹 ~0.48 / 0.76 / 0.88 dB | 改為 corner 取代制 |
| M2 | **Cross（drop）狀態重複計一次 ring transit** | `fabric_loss.py:146-151` | on 態 S 參數已含 ~0.048 dB ring 損（能量和 0.98897），內部 Z 形 39.71 µm 路徑又計 0.079 dB，其中跨 bus 段 ~0.047 dB 重複；n4/n6 worst path 全 bar 不受影響，n8 含 1 次 cross → ~0.05 dB 重複。**結構性脆弱**：若未來換成含 bus 損的實測/FDTD S 參數，bar 態也會全面重複計算 | 明確定義 S 參數 reference plane，內部幾何只補 S 模型未含的部分 |
| M3 | **同一 path 穿越同一 crossing 兩臂時被 set 去重** —— n8 worst path 實體穿越 28 次、報 27 次 | `fabric_loss.py:155-158` | n4/n6/n8 各有 56 / 3768 / 315648 個 path-crossing 去重事件；今日因 crossing loss=0 而不可見，係數轉正後直接低估 | 以 multiset / 每邊獨立計數 |
| M4 | **coverage 是模型自洽檢查，非獨立驗證** | `analysis/fabric_coverage.py:46,59-81` | fabric graph 與 active paths 皆由同一 `stage_pairs`/port 慣例導出 —— 共用慣例的 bug 會雙雙通過；且 Waksman 測試只斷言 `permutations_checked` 計數，沒斷言 `passed`（`tests/test_fixed_fabric_router.py:124`） | 增加以獨立實作交叉比對的測試；斷言 `coverage.passed` |
| M5 | **DRC=0 = 8 條 centerline 規則全過而已；crossing 永遠不可能違規**（allow_crossings=true） | `routing/drc.py:20-113` | 缺席（逐一 grep 確認無程式碼）：線寬、實際彎曲半徑/離散化（目前是尖角，radius 只是長度加項）、crossing cell 佔位、taper、port 朝向、同 net 自間距、局部段 keepout、金屬/heater 層 | 見 D-5；至少把缺席清單寫進 docs Scope |
| M6 | **合併 waveguide 的自間距豁免**：merged pass-through edge 與同 net 其他段間距 < 4 µm 不會被查 | `drc.py:58-59` | DRC 在合併 polyline 上跑（先於 per-edge split，split 只服務 loss 記帳）——合併不會藏跨 net 違規，但同 net 自擠壓永遠 DRC=0 | 對同 net 非相鄰 segment 加自間距檢查 |
| M7 | **legacy_comparison 只比對「單一 permutation 的聚合計數」，且 n6 的 legacy router 有 1/6 path 失敗未在 docs 揭露** | `cli.py:509-518`、`outputs/.../n6/legacy_comparison.json` | n6（perm 2-0-5-1-3-4）legacy routed_paths: 5/6、crossings 20 vs fabric 36；檔案自標 `models_are_not_numerically_interchangeable: true` —— 一致與否皆不證明任何物理性質 | 降級此 artifact 的宣稱力道或補 per-path 數值對比 |
| M8 | **波長塌縮成單點且恰在共振點**；相位資訊被丟棄 | `core/sparams.py:74-77` | library 有 161 點頻譜 + phase_rad，loader 只取最近點的 \|S\|²；預設 1550.0 = 恰好 on-resonance（最佳情況）；無 bandwidth / FSR 對齊檢查（CSV 視窗 8 nm < FSR 20.33 nm） | 加波長掃描與 detuning 敏感度（見 D-4） |
| M9 | **off 態 = detune 半個 FSR（10.164 nm）**，對熱調諧是不現實的最佳情況 | `mrr_sparam_library/mrr_sparam_manifest.csv`（state_mode=off_half_fsr） | dλ/dT ~0.08–0.1 nm/K → 半 FSR 需 ΔT>100 K；實際 switch 常以 1–2 nm detune 工作，off 態 through 損與 leakage 都會比 −21.8 dB 更差；程式碼中 detune/fsr/kappa 零出現（僅 manifest 中繼資料） | library 增列寫實 detune 檔位並重算 |
| M10 | **process variation / thermal / heater 全缺**：全部 ring 共用同一個 s_table 物件；熱僅剩同 stage y 間距 ≥24 µm 的常數 | `placement/layout.py:34`、`placement/sa.py:327-340` | 無 Monte Carlo、無 tuning power、無 heater 佈線 | 見 D 節實驗 |

### LOW（摘列）

- geometry_sha256 只涵蓋 waypoints+edge ids，算一次、從不重算比對（`fabric_reports.py:15-25`）。
- DRC/crossing ownership 靠 regex 從 legacy 整數 net ID 反解（`routing/fabric.py:344-352`），格式脆弱。
- worst permutation 用嚴格 `>` 取第一個達最大值者 —— n4 顯示 identity 只是「並列最差中的第一個」（`fabric_loss.py:100`）。
- 邊界 edge 的 stage span 在 CSV 單獨無法重建（source_stage=None）。
- 輸出 artifact 不含 per-edge 長度/crossing 明細，第三方無法獨立重算 headline 數字。
- crossing 偵測對「恰好落在 collinear hop-junction 頂點」的交點有潛在漏判（`routing/geometry.py:197`，目前被 stub 幾何守住）。
- Polarization / packaging / 光電共設計 / 測試結構全缺（詳 C 節）。

---

## 十四項重點問題的直接回答

1. **是正規 Waksman 嗎？** 是。遞迴建構（`topology.py:309-326`）與獨立建構的 canonical AS-Waksman 在 n=4/6/8 做過**精確圖同構驗證**；Waksman(2^k) 是 Benes(2^k) 減去每個偶數遞迴塊最後一顆輸出 switch 的嚴格子集；W(4)=5、W(6)=11、W(8)=17 與理論一致；奇數 N 用正規非對稱遞迴（未配對線進較大子網）。interconnect 以「非相鄰 pair + identity 級間佈線」編碼而非顯式 shuffle —— 圖層面正規，幾何呈現交給 router。
2. **真的只繞一次？** 是，且有實證：以不同 `--permutation` 參數重跑三個尺寸 → geometry_sha256 全部 byte-identical（dc7b62ec / 0e3bb955 / dbee4371）。`route_fixed_fabric` 單一呼叫點（`cli.py:493`）、輸入不含任何 permutation 衍生物、per-permutation 迴圈不 import 任何繞線/擺置程式碼、全部共享結構是 frozen dataclass。**但** JSON 裡的 routed_once 欄位本身是硬編碼（H5）。
3. **「全 permutations 通過」證明什麼？** 證明：每個 permutation 有完整的 1-bit-per-MRR 狀態向量，其抽象 wire-swap 模擬實現目標排列；且每條 forward path 的每個 hop 都是 fixed fabric 的**有向** link、進出 port（in/add、th/drop）與狀態一致。不證明：任何幾何性質、該 permutation 下的損耗/串擾/DRC；且檢查與被檢對象出自同一慣例（自洽而非獨立）。
4. **合併 FabricEdge 破壞什麼嗎？** 不破壞 —— 數值驗證：每條 waveguide 的完整 polyline 長度 == Σ(edge slices) + Σ(bar 內部段)，bend 同樣精確；split 點必在 MRR port 且兩側有水平 stub，seam 掃描零 bend 落在切點；merged edge 是真實繞線幾何的切片（繞過被跳過 stage 的 keepout，不假裝經過）；ownership 只用於 DRC/crossing 回報歸屬，從不進 loss 計算。stage span 可由 source_stage/target_stage 重建。
5. **Port 方向與 bus continuity？** 實作正確且物理自洽：in(−8,+4)/th(+8,+4) 左→右、add(+8,−4)/drop(−8,−4) 右→左（`models.py:52-55` docstring 明言「勿為了 monotonic 而搬 port」）；bar={in→th, add→drop}、cross={in→drop, add→th} 綁 off/on 共振 CSV。每條 edge 恆為 output-role port → input-role port，方向對所有 permutation 不變 —— 不存在反向傳播組態。代價：下線每級要繞 cell 一圈（U-turn），如實反映在長度/bend。能量上未用 port pair 被當全黑（−21.8 dB leakage 未建模 → H1）。
6. **每 permutation 只切 bar/cross？** 是（程式結構保證）：唯一自由度是 per-MRR 0/1 dict，完整性強制（`state_assignment.py:360-368`）；同 ring 兩線共用同一 bit，衝突**由建構不可能**（非被偵測）；不在 fabric link 集合內的 hop 會記為 fabric_path_failure（三組皆 0）。
7. **長度/bend/crossing 恰好算一次？** 長度與 bend：是（見 4）。Crossing：歸屬幾何正確（每 crossing 恰對映 2 個 edge slice，非 owner 邊也看得到），**但 per-path set 去重會把「同 path 穿兩臂」算成一次**（M3，n8 worst path 27 vs 28）。另 bend 弧長多算 10 µm/bend（M1）。
8. **S 參數重複計算？** 分狀態：bar —— 否（library 近乎無損，0.029 dB 是耦合分流非傳播損，內部 16 µm 幾何長度是在補 S 模型沒有的 bus 損）；cross —— **是**，~0.05 dB/次 ring transit 重複（M2）。若日後換成含 bus 損的實測 S 參數，現行記帳會全面重複。
9. **bend/crossing=0 之下 worst IL 的效力？** 只能做「同係數下的內部幾何比較」，且要注意 H6：係數轉寫實後 worst path 身分會變、n8 可能被 crossing 主導（+0.54 至 +5.40 dB）。不可作為實體預測。
10. **0.002 dB/µm 合理嗎？** = 20 dB/cm，比典型 SOI strip 高 ~10×；硬編碼、無 config 覆寫、無 placeholder 註記（H3）。單位正確、數值不合理。
11. **DRC=0 涵蓋什麼？** 8 條 centerline 規則（manhattan、外部段 MRR keepout+6 µm、跨 net collinear overlap、跨 net 平行間距 4 µm、touching corner、crossing[本次關閉]、同 net touching corner、連續外部 bend ≥10 µm）。缺席：線寬、實際 bend 半徑/離散化、crossing cell、taper、最短直段（部分）、port 朝向、同 net 間距、金屬/heater —— 逐一搜尋確認無程式碼。
12. **Waksman vs padded Beneš？** 同 rules 實測：N=8 打平（Waksman 差 0.0006 dB）—— 3 顆 MRR 的節省從未觸及 worst path（兩者都過 5 顆，MRR 損 byte-identical）；「長線/多 crossing 吃掉優勢」的假說也被反證（Waksman 反而 crossing 較少 70 vs 78、平均較短）；真正差異在非 2 冪次 N 的免 padding（N=6: 0.665 dB，90% 是幾何）。
13. **legacy 滲漏？** 無幾何滲漏（實證：換 permutation 只有 legacy_comparison.json 改變）。fixed-fabric 經 `_fabric_waveguide_paths` adapter 重用 legacy 引擎，但輸入全部 bar-state、來自 topology 而非 permutation；step.state 只影響繞線順序啟發式（fabric paths 恆 0）。legacy active router 仍每次 CLI 跑一次（`cli.py:509-517`）但在 fabric 評估之後、無共享可變幾何。殘留接縫：regex net-ID 反解、繪圖函式簽名仍收 permutation（恆 identity）。
14. **未建模物理**：見 C 節。

---

## B. 目前測試/輸出「真正已證明」的事實

1. 拓撲圖層面是精確的 canonical AS-Waksman（n=4/6/8 圖同構驗證 + n=4 逐 port 手驗 + 與 Benes builder 的差集驗證）；MRR 數 5/11/17 = 理論值；edge 數 = 2·n_MRR+N 恆等式由 builder 自身驗證器強制。
2. 實體 fabric 恰繞一次、對 permutation 零依賴 —— 由程式追蹤 + **重跑不同 permutation 得 byte-identical sha256** 雙重證明；重建 `build_fabric_graph` 逐 row 重現三個 committed CSV；重跑 CLI bit-for-bit 重現三個 committed geometry。
3. 每 permutation 唯一自由度是 MRR 狀態 bit；每條 active path 的每個 hop 是 fixed fabric 的有向 link；24/720/40320 = N! 為窮舉（`itertools.permutations`，無取樣、無 break），三組 failure 清單皆空。
4. Claims 表所有數字與原始 JSON 完全一致；IL 分解加總誤差 0.0；length×0.002 == prop loss 誤差 0.0；paths_checked = N!×N 全部成立。
5. 邊切割記帳無漏算/重複（長度、bend 數值恆等式驗證通過）；n4 worst path 1277.4956 µm / 24 bends 逐段重算吻合（含 3×16 µm 內部段；24 bends 中 14 個集中在一條 542 µm 的 detour interstage slice —— 是真實幾何，非記帳洩漏）。
6. Port 幾何為標準 add-drop（上 bus 左→右、下 bus 右→左）、狀態↔共振對映正確；MRR 損耗來自真實 sparam library 而非 MOCK（0.0869 = 3×0.028966 dB through 精確吻合；MOCK 會是 0.223 dB/次）；drop(0.0483) > through(0.0290) 排序物理正確。
7. 三張 layout PNG 與 CSV 結構吻合（每欄 MRR 數、n6 O4/O5 與 n8 O6/O7 的 early exit 皆對應 stage-2 drop 輸出邊），無重疊元件、無斷線。
8. DRC=0 在其 8 條規則的定義域內為真（violations CSV 為 33-byte 純表頭）。

## C. 尚未驗證、不能宣稱成立的假設

1. **Worst IL 是實體損耗預測** —— H2/H3/M1/M2/M3 全部指向同一結論：數字是「特定 placeholder 係數下的幾何加權長度」。
2. **「較少 MRR → 較低 worst IL」** —— N=8 已被反證（H4）；只剩非 2 冪次 N 的 padding 論述可用。
3. **N 通道同時工作可行**（SXR ≥ spec）—— repo 自己的邏輯模型顯示大範圍違規（H1），fixed-fabric 層完全未評估。
4. **off 態半 FSR detune 可由實際調諧達成**（M9）—— 對應 ΔT>100 K，未驗證亦不現實；寫實 detune 下 −21.8 dB extinction 是樂觀值。
5. **尖角 Manhattan 幾何可直接製造** —— 無 bend 離散化、無 crossing cell、無線寬/taper/port 朝向規則（M5）；DRC=0 ≠ foundry DRC clean。
6. **合併 waveguide 不自擠壓**（M6）—— 同 net 間距從未檢查。
7. **sparam library 正確代表元件**（解析模型 κ=0.15、近無損、extinction 45 dB 對稱理想）—— 無 golden test 對照實測/FDTD。
8. **routed-once 欄位有監測力**（H5）—— 目前是常數。
9. 相位/相干干涉、偏振、製程變異、熱串擾、heater 功耗、封裝耦合損、可測性 —— 全部缺席（各項為全套件 grep 零命中）。

## D. 最值得執行的五個後續實驗

**D-1. Padded-Beneš 基準 + 係數敏感度矩陣（直接決定論文敘事）**
方法：把審查中重跑的 `--topology benes --n-logical {4,6,8}` fixed-fabric loss artifact 正式提交；在 {α: 0.0002/0.002} × {crossing: 0/0.02/0.1/0.2} × {bend: 0/0.005/0.02} 網格上重算兩拓撲 worst IL 與 worst-path 身分。
預期：2 冪次 N 全網格內兩者打平（差 <0.02 dB）；Waksman 僅在 N=6 保有 padding 優勢。
反證條件：任一寫實係數點上 Waksman 以 >0.1 dB 勝出 → 「fewer MRR」敘事部分復活。
新增輸出：`fabric_loss_matrix.csv`（topology × 係數點 × worst IL × worst path id × 是否換 path）。

**D-2. Fixed-fabric SXR 評估（檢驗 H1，「邏輯全通但實體不成立」的決定性實驗）**
方法：把 `cost.py:_mrr_leak_power` 的一階 leakage 邏輯移植到 routed fabric：對每個 permutation，累加所有同時活躍 path 經共享 MRR 的洩漏（off 態 −21.8 dB、on 態 −45.2 dB，取自同一 library），輸出 per-path SXR。
預期：n8 大多數 permutation worst SXR < 20 dB spec、部分接近邏輯層的 11.68 dB。
反證條件：routed fabric 的 SXR 全面 ≥ 20 dB → 邏輯層違規是其模型 artifact，crosstalk 疑慮解除。
新增輸出：`fabric_loss_summary.json` 增列 worst_sxr_db / sxr_violation_count；新 test 斷言 SXR 欄位由計算產生。

**D-3. routed-once 真儀表化 + 變異測試**
方法：在 `route_physical_design` 加呼叫計數器並傳遞至 report；每個 permutation 評估後重算 `fixed_fabric_geometry_hash` 與初值比對；寫一個故意在 permutation 迴圈內重繞的 mutant，確認測試會抓到。
預期：計數器=1、全部 hash 相等；mutant 使測試失敗。
反證條件：任何 permutation 令 hash 改變 → 存在未發現的幾何滲漏（目前證據顯示不會）。
新增測試：取代 `test_fixed_fabric_router.py:82-83` 的套套邏輯斷言。

**D-4. 波長/detuning 寫實化（檢驗 M8/M9）**
方法：λ 在 1546–1554 nm 掃描（library 現有 161 點）重算 worst IL/SXR；另生成 detune ∈ {0.5, 1, 2, 10.164} nm 的 off 態 library 檔位重算。
預期：off 態 detune 從半 FSR 降到 1–2 nm 時，through 損上升、extinction 顯著劣化（−21.8 dB → −15 dB 級），SXR 惡化數 dB；heater 功耗估算顯示半 FSR 不可行。
反證條件：1–2 nm detune 下結果與半 FSR 差 <0.5 dB → 現行 off 態假設無害。
新增輸出：`sparam_sensitivity.csv`（λ, detune → worst IL, worst SXR）。

**D-5. 記帳修正 + 可獨立重算的 artifact（檢驗 M1/M2/M3）**
方法：修正 bend 長度（corner 取代制）、cross 態內部段扣除、crossing multiset 計數；輸出 per-edge 明細（length_um、bend_count、crossing ids）到 `fabric_edges_geometry.csv`，寫獨立腳本從明細重算 worst IL 比對 summary。
預期：三組 worst IL 各下修 ~0.48/0.76/0.93 dB（bend 修正 + n8 的 0.05 dB cross 修正）；獨立重算與 summary 一致。
反證條件：獨立重算與 summary 不一致 → 存在更深的記帳缺陷。
新增測試：長度恆等式（route == Σ slices + Σ internals）進 CI。

## E. 最終判斷

| 用途 | 判斷 | 理由 |
|------|------|------|
| **內部演算法比較** | **可以，但有兩個前提** | 同係數、同 router 下比較幾何品質是有效的。前提一：認知它比的是 floorplan 幾何（96.7% propagation）；前提二：crossing loss=0 系統性偏袒 crossing-heavy 拓撲，且寫實係數下 worst-path 身分會變（H6）—— 排名結論需在 D-1 係數網格上驗證穩定性後才可信 |
| **論文 / 公開報告** | **現狀不可以** | (1) headline 數字建立在未標示的 20 dB/cm placeholder 與歸零 bend/crossing 上，低估 1–3 dB 且無法對應任何實體平台；(2) 「fewer MRR → lower worst IL」在 N=8 被自家工具鏈反證（H4）；(3) 無 crosstalk —— 自家邏輯層已顯示 SXR 大面積違規。**可發表版本**：明示係數為 normalized/placeholder、主張改為「fixed-fabric 架構 + 一次繞線方法論 + 元件數/padding 優勢」、附 D-1/D-2 結果 |
| **Fabrication decision** | **絕對不可以** | 缺 foundry DRC（線寬/彎曲/crossing cell/taper/port 朝向全缺）、尖角幾何非可製造表示、無 heater/調諧功耗模型（off 態半 FSR 不現實）、無製程變異、無封裝損、S 參數是未經 golden test 的解析模型 |
| **「邏輯全通但實體不成立」風險** | **架構無此問題；系統層有** | 架構層（方向、port 使用、一次繞線、狀態切換）經多重獨立驗證自洽且物理合理 —— 未發現任何會令 4×4/6×6/8×8 需要重新接線或反向傳播的缺陷。真正風險全在**未建模的系統物理**：(1) N 通道同時工作的 SXR（H1，自家數據已亮紅燈）；(2) off 態調諧可達性（M9）；(3) 相干干涉與偏振未評估。任一惡化都可能讓「全 permutation 可用」在實體上不成立 —— 但這是「模型未覆蓋」，不是「模型錯誤」 |

---

### 附錄：審查產物位置

- 完整 51 項發現（JSON）：session scratchpad `audit_findings.json`
- 工作流逐代理紀錄：`~/.claude/projects/-home-jchuang/37bb9dcf-f37d-47fc-8cf9-243de3f31363/subagents/workflows/wf_de02ed38-e87/journal.jsonl`
- 對照 Beneš n8 重跑輸出：session scratchpad（未寫入 repo）
