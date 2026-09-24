# 監工獨立審查報告(REVIEW)— N=3–12 Waksman vs Padded-Beneš Fixed-Fabric 戰役

審查者:監工 Claude(獨立於實作 agent Codex;實作方自己的驗證記錄見 `CODEX_VALIDATION.md`)。
審查日期:2026-08-21。結論先行:**全部審查項通過,資料可信,論文主敘事成立**;
問題清單見文末(無阻斷級問題)。

## 1. 逐項獨立重驗結果(不採信 Codex 自報,全部自行重跑)

| 審查項 | 方法 | 結果 |
|---|---|---|
| (a) 測試全綠 | 自行執行 `pytest tests/ -q` 全套 | **298 passed**(21m17s),0 失敗 ✓ |
| (b) G1 template crossings | **自寫幾何腳本**:從 emitted 波導 polyline 逐段計算垂直×水平真交點(排除共端點),不用 repo 的 crossing 清單或預測公式 | benes-4/8/16 = **14 / 60 / 232**,與模型 T(N)=inversions+wrap 及 repo 記錄三方一致;交點座標唯一數同 ✓ |
| (c) G4 決定性 | 同案 template 兩次 emission 比對 geometry hash | hash 相同,且 `astar_calls == 0` ✓ |
| (c) G6 padding 共用 | pb n_logical=9 與 n_logical=12 各自 fresh emission | geometry hash 相同(`2fc4460a…`),與 metrics 中 n9–n12 四列 hash 一致 ✓ |
| (d) metrics.csv 完整性 | 自寫驗證腳本 | **40 列**=10N×2拓撲×2config;37 complete + 3 route_failed(waksman C n10/11/12);37 案 method_agreement=True 且 gap=0;coverage_failures 全 0;permutations_checked == N!;legacy/bend DRC 全 0;每八度**單一** envelope_id;MRR 實測==理論(W(n) 與 (P/2)(2log₂P−1));witness 全數在場 ✓ |
| (d) 憑證抽驗 | `--verify-certificates` 抽 waksman n9-C 與 n12-B | 兩案 witness 重驗通過 ✓ |
| (d) 雙法交叉 | 讀 `crosscheck/method_agreement.csv` | **37/37 吻合,max |gap| = 0.0** ✓ |
| (e) 圖表逐張 | 逐張開啟 8 張 PNG | 全數合格,逐張結論見 §2 ✓ |

## 2. 每張圖一句話結論

- `mrr_count_vs_n.png` — 主圖完全達標:W(n) 平滑曲線 vs padding 階梯,每 N 標節省 %(N=9 = −63%),八度邊界虛線在 4→5、8→9。
- `mrr_savings_pct_vs_n.png` — 節省 % 鋸齒圖正確呈現「剛過冪次後峰值」(N=5: 60%、N=9: 62.5%),即 N×N 擴展性論點。
- `worst_il_vs_n_B.png` — y 軸自 0 起、每 N 標 Δ;B 配置下 Waksman 於 N≥9 全勝 0.62–1.17 dB(超越 parity)。
- `worst_il_vs_n_C.png` — 同樣誠實;三個 route_failed 以紅 × 與文字明示,未插值未偽造。
- `win_margin_vs_n.png` — 正負號約定標示清楚,C 線於 N=9 截止,不虛構缺失點。
- `worst_il_bars_B.png` / `_C.png` — 分組長條與折線圖一致;C 圖 N=10–12 Waksman 缺條即為 route_failed。
- `worst_path_crossings_vs_n.png` — 佐證圖;waksman C 線止於 N=9,正確。

## 3. WIL 勝負明細(我自行由 metrics.csv 重算,與 REPORT 一致)

- **B 配置**:Waksman 贏 N=3,5,6,9,10,11,12;輸 N=7 (+0.46),8 (+0.40);N=4 為 **+0.005 dB,應視為平手**(見問題 #2)。最大贏幅 N=9(−1.17 dB)。
- **C 配置**:贏 N=3,5,6,9;輸 N=4 (+0.16)、7 (+1.28)、8 (+1.31);N=10–12 無資料(route_failed)。
- 與先前認知一致:冪次頂端(7,8)Waksman 較差、C 配置放大該劣勢;新事實:**同八度上半段(9–12)Waksman 在 B 配置為實質勝出**,因 padding 需付 16 寬 template 的固定代價。

## 4. MRR 節省 % 走勢

50%(N=3)→ 16.7%(N=4,谷)→ 60%(N=5,峰)→ 遞減至 15%(N=8,谷)→ **62.5%(N=9,峰)**→ 遞減至 41.1%(N=12)。
鋸齒由 padding 階梯造成,峰值永遠出現在剛跨過 2 的冪次之後——這正是論文的擴展性賣點,且對更大 N(17、33…)可外推。

## 5. 問題清單(依嚴重度)

1. **[中] Waksman C 配置 N=10–12 全數 route_failed**:三案皆為同一 I4→O4 wrap 自我干擾簽名
   (`same_net:63, pops:92, window:0`),pop ladder(30k/100k/300k)、三代位移修復機制、
   及擴窗(位移+2 軌,實測至多 +4 軌)全數無效;最終堵點是位移候選需達 y=593 而擴窗上限
   582(差 11 µm ≈ 1.4 軌)。**這是 router 的真實限制,非架構限制**;C 圖 parity 主張
   僅能陳述至 N=9。後續(v4)方向:更大擴窗係數或改變 detour 形狀。所有 24 次嘗試
   逐筆存證於 case config 與 verification/。
2. **[低] REPORT 措辭**:B 配置 N=4 的 Δ=+0.005 dB 被列為「輸」;依戰役自訂的
   「<0.1 dB 不得誇大」原則應改稱平手。不影響任何數據。
3. **[低] corridor_guide_mode 為 no-op**(v2 期中文件曾稱「soft guides 有效」):flag 無任何
   消費點,`_corridor_preferred_x` 恆開,G7 原本比較兩份相同繞線。REPORT 與 G7 測試已更正;
   flag 本體將由 v3 Phase 1 移除。教訓:早期 G7「通過」是恆真式。
4. **[低] Waksman A* 案的 same-net/perpendicular 審計違規隨 N 單調增長**(C 配置 N=12 達
   110/37),template 側全部 0/0。此兩項為量測性審計、非驗收閘門,但屬製造寫實性差距,
   REPORT 已如實列表並轉述為 template 的真實優勢。
5. **[資訊] full_drc=True 會耦合進 routing 評分**:開啟審計旗標會改變繞線結果(n10-C 曾因此
   「假成功」)。未來實驗設計須把它視為干預、不是純觀測。
6. **[資訊] C 配置繞線耗時**:三個失敗案各燒 10–24 小時(含全部重試),v4 預算需計入。

## 6. n10-C 事件審計軌跡(供論文附錄或覆核)

無檔位間狀態洩漏(fresh standalone 逐簽名重現失敗);唯一「成功」跑確認為 full_drc 耦合
實驗、已排除。三代修復機制迭代史與 24 筆嘗試記錄完整保存於
`outputs/nsweep_fixed_fabric_v2/verification/`(四個對照組)與各 case 的
`audit_failed_pre_remediation_three_rung/`。「搜尋窗(可擴、逐案記錄)vs envelope(憲法、
未動)」的區分已在 REPORT 明文;全戰役 envelope/pitch/畫布經我驗證確未變動(每八度單一
envelope_id)。

## 7. 結論

論文主敘事「**Waksman 對確定性佈局 padded Beneš:MRR 數量在所有 N 嚴格更少(平滑 W(n) vs
padding 階梯,N=9 省 63%),同八度公平畫布下 WIL 不劣化**」由本戰役數據支持,且在 B 配置
N≥9 可加強為「WIL 亦較低」。誠實邊界:C 配置(0.1 dB/crossing)下 Waksman 於 N=4,7,8 落敗、
N=10–12 因 router 限制無資料——兩者皆已在圖表與 REPORT 如實呈現。
