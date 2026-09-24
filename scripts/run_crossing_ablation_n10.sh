#!/usr/bin/env bash
# N=10 crossing-insertion ablation. Cases run most-informative-first, each in its
# own process with a wall-clock cap so one exhaustion cannot stall the rest.
# Results stream to outputs/crossing_insert_ablation/ as one JSON per case.
set -u
ROOT=/home/jchuang/Optical_switch
export PYTHONPATH=$ROOT
cd "$ROOT" || exit 1
S=scripts/crossing_insert_ablation.py
POPS=100000

run() { # topology config explicit timeout
  echo "=== START $1 $2 explicit=$3 (cap $4s) $(date +%H:%M:%S)"
  timeout "$4" python "$S" --topology "$1" --n 10 --config "$2" --explicit "$3" --pops "$POPS"
  rc=$?
  [ $rc -ne 0 ] && echo "=== $1 $2 explicit=$3 EXIT $rc (124=timeout)"
  return 0
}

run waksman      B_db_placeholder true  2400
run waksman      B_db_placeholder false 2400
run waksman      C_db_realistic   true  4200
run waksman      C_db_realistic   false 4200
run padded_benes B_db_placeholder true  3600
run padded_benes B_db_placeholder false 3600
run padded_benes C_db_realistic   true  3600
run padded_benes C_db_realistic   false 3600
echo "=== ALL DONE $(date +%H:%M:%S)"
