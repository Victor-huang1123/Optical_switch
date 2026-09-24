#!/usr/bin/env bash
# N=8 Waksman exact state-assignment optimization study, via Codex.
set -u
ROOT=/home/jchuang/Optical_switch
cd "$ROOT" || exit 1
LOG=/tmp/jchuang-tmp/claude-1237/-home-jchuang/8eb1744e-987f-4556-8c6d-d568aca6a392/scratchpad/codex_n8_study.log
echo "=== CODEX_RUN_START $(date -Is) ===" > "$LOG"
codex exec \
  -m gpt-6-astra \
  -c model_reasoning_effort=high \
  --sandbox workspace-write \
  "Read codex_n8_state_assignment_task.md in this repository and carry it out exactly as specified, section by section. Do not modify anything under mrr_switch_optimizer/ or tests/. New code goes in experiments/, deliverables in results/n8_state_assignment/. Route the fixed fabric exactly once and reuse it for every state evaluation. Follow every correctness assertion in section 15; if one fails, stop and report the diagnostic rather than working around it." \
  2>&1 | tee -a "$LOG"
rc=${PIPESTATUS[0]}
echo "=== CODEX_RUN_EXIT rc=$rc $(date -Is) ===" >> "$LOG"
