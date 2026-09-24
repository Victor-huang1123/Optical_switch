#!/usr/bin/env bash
# N=8 physical-aware Waksman embedding optimization with recursive DP, via Codex.
set -u
ROOT=/home/jchuang/Optical_switch
cd "$ROOT" || exit 1
LOG=/tmp/jchuang-tmp/claude-1237/-home-jchuang/8eb1744e-987f-4556-8c6d-d568aca6a392/scratchpad/codex_embed_dp.log
echo "=== RUN_START $(date -Is) ===" > "$LOG"
codex exec \
  -m gpt-6-astra \
  -c model_reasoning_effort=high \
  --sandbox workspace-write \
  "Read codex_n8_embedding_dp_task.md in this repository and carry it out exactly as specified, section by section. Do not modify mrr_switch_optimizer/ or tests/. New code goes in experiments/, deliverables in results/n8_embedding_dp/. Respect the three-stage routing budget in section 12: complete all proxy work first, then route at most 24 candidates in the stated priority order, writing each summary.csv row immediately as that candidate finishes. The primary metric is GLOBAL_WIL_OPT_ASSIGN against the baseline 5.181320181939005 dB. Verify every embedding transformation on N=4 then N=6 before enabling it at N=8, and discard any that changes logical semantics." \
  2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
echo "=== RUN_EXIT rc=$rc $(date -Is) ===" >> "$LOG"
