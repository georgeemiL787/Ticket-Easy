#!/usr/bin/env bash
# Long-conversation set (orchestrator/eval/long, 24 conversations of 5-7 turns) in agent mode, three processes at once.
# One conversation at a time per process (several at once makes the free plan answer 429 and the agent fall back).
# Needs GROQ_KEY_1..22 in the environment (never in this repository). Output: diagnosis/long_<style>.jsonl
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
RUNS="$ROOT/docs/reports/eval_runs"
cd "$ROOT/team_b" || exit 1
source "$RUNS/agent_env.sh.example"
PY=".venv/Scripts/python"; [ -x "$PY" ] || PY=".venv/bin/python"
keys() { local o="" n; for n in "$@"; do v="$(printenv "GROQ_KEY_$n")"; [ -n "$v" ] && o="${o:+$o,}$v"; done; printf '%s' "$o"; }
run() {  # style, keys...
  local style="$1"; shift
  (
    export TEAM_B_LLM_API_KEY="$(keys "$@")" TEAM_B_USAGE_LOG="$RUNS/usage/long_$style.jsonl" TEAM_B_USAGE_RUN_LABEL="long_$style"
    "$PY" "$RUNS/diagnose_run.py" --set "$ROOT/orchestrator/eval/long" --llm --agent-mode on --style "$style" --concurrency 1 \
      --out "$RUNS/diagnosis/long_$style.jsonl" > "$RUNS/logs/long_$style.log" 2>&1
  ) &
}
run en 16 17 18 19 20 21 22
run ar 1 2 3 4 5 6 7
run mixed 8 9 10 11 12 13 14 15
wait
echo "long runs finished"
