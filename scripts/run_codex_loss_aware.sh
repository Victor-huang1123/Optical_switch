#!/usr/bin/env bash
# Launch the loss-aware state-assignment measurement via Codex.
set -u
cd /home/jchuang/Optical_switch || exit 1
LOG=/tmp/jchuang-tmp/claude-1237/-home-jchuang/8eb1744e-987f-4556-8c6d-d568aca6a392/scratchpad/codex_loss_aware_run4.log
echo "=== START $(date -Is) ===" | tee "$LOG"
codex exec \
  -m gpt-6-astra \
  -c model_reasoning_effort=high \
  --sandbox workspace-write \
  "Read codex_loss_aware_states_task.md in this repository and carry it out exactly as specified. It is an analysis-only measurement task: do not modify anything under mrr_switch_optimizer/ or tests/, put new code in scripts/, and write deliverables to outputs/loss_aware_states_n8/. Stop and report rather than improvising if the geometry hash check in step 1 fails or if enumeration cost exceeds the stated budget." \
  2>&1 | tee -a "$LOG"
echo "=== DONE rc=$? $(date -Is) ===" | tee -a "$LOG"
