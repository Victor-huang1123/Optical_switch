# Final Results — Waksman vs Deterministic-Layout Padded Beneš (N=3~12)

戰役完成後由監工 Claude 從 outputs/nsweep_fixed_fabric_v2/ 彙整至此。預期內容：

- charts/               全部參數圖（主圖 mrr_count_vs_n.png、mrr_savings_pct_vs_n.png、
                        worst_il_vs_n_{B,C}.png、worst_il_bars_{B,C}.png、win_margin_vs_n.png）
- metrics.csv           40 案完整指標（含 mrr_savings_pct、envelope_id、method agreement）
- REPORT.md             最終報告（win/tie/lose 明列；主張 = MRR 優勢 + 擴展性 + WIL parity）
- crosscheck/method_agreement.csv   雙法交叉驗證紀錄（必須 100%）
- REVIEW.md             監工 Claude 的獨立審查報告（gates 重驗結果）

論文主敘事：Waksman 對確定性佈局 padded Beneš —— MRR 數量在所有 N 嚴格更少
（W(n) 平滑擴展 vs padding 階梯，N=9 省 63%），同八度公平畫布下 WIL 不劣化。
