#!/usr/bin/env bash
# Agent-mode evaluation of the 240 conversations: four processes at once, one per language style, each with its own
# Groq key(s) so each has its own rate budget. Safe to run again: --resume continues a stopped style.
#
#   source <your keys file>          # exports GROQ_KEY_1 ... GROQ_KEY_7 (never put keys in this repository)
#   bash docs/reports/eval_runs/run_agent_parallel.sh
#
# Keys per style: en = 1,2; ar = 3,4; mixed = 5,6; arabizi = 7. Several keys in one process are rotated on rate limits
# (the service multiplies the per-minute budget by the number of keys) and give the style a larger daily allowance.
# The Arabizi process runs a sample of ARABIZI_PER_STYLE conversations (appendix only), the others run all 60.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
RUNS="$ROOT/docs/reports/eval_runs"
ARABIZI_PER_STYLE="${ARABIZI_PER_STYLE:-15}"
cd "$ROOT/team_b" || exit 1
# shellcheck disable=SC1091
source "$RUNS/agent_env.sh.example"
mkdir -p "$RUNS/logs" "$RUNS/usage"
PY=".venv/Scripts/python"; [ -x "$PY" ] || PY=".venv/bin/python"

key_for() {  # the comma list of keys of one style, built from GROQ_KEY_n (never printed)
  local out="" n
  for n in "$@"; do
    local v; v="$(printenv "GROQ_KEY_$n")"
    [ -n "$v" ] && out="${out:+$out,}$v"
  done
  printf '%s' "$out"
}

declare -A KEYS=( [en]="1 2" [ar]="3 4" [mixed]="5 6" [arabizi]="7" )
declare -A PIDS
for style in en ar mixed arabizi; do
  keys="$(key_for ${KEYS[$style]})"
  if [ -z "$keys" ]; then echo "no key for $style: GROQ_KEY_${KEYS[$style]// /, GROQ_KEY_} not set"; continue; fi
  extra=""; [ "$style" = "arabizi" ] && extra="--per-style $ARABIZI_PER_STYLE"
  (
    export TEAM_B_LLM_API_KEY="$keys"
    export TEAM_B_USAGE_LOG="$RUNS/usage/agent_$style.jsonl"
    export TEAM_B_USAGE_RUN_LABEL="presentation_agent_$style"
    "$PY" -m team_b eval --set "$RUNS/sets/$style" --llm --agent-mode on --judge --resume \
      --label "presentation_agent_$style" $extra > "$RUNS/logs/$style.log" 2>&1
  ) &
  PIDS[$style]=$!
  echo "started $style (pid ${PIDS[$style]})"
done

for style in "${!PIDS[@]}"; do
  wait "${PIDS[$style]}"; code=$?
  case $code in
    0) echo "$style: finished" ;;
    3) echo "$style: stopped early (rate limit or daily limit); run this script again to resume" ;;
    *) echo "$style: failed with exit code $code; see $RUNS/logs/$style.log" ;;
  esac
done
