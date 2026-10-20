#!/usr/bin/env bash
# Long-conversation set (orchestrator/eval/long) in agent mode against the REAL knowledge service (needs it running on port 8001).
# One conversation at a time per process (several at once makes the free plan answer 429 and the agent fall back).
# Needs GROQ_KEY_1..22 in the environment (never in this repository). Output: diagnosis/longlive_<style>.jsonl
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
RUNS="$ROOT/Docs/output/eval_runs"
cd "$ROOT/team_b" || exit 1
source "$RUNS/agent_env.sh.example"; export TEAM_B_MODE=live TEAM_B_TEAM_A_URL=http://127.0.0.1:8001 TEAM_B_TEAM_A_TIMEOUT_S=30
PY=".venv/Scripts/python"; [ -x "$PY" ] || PY=".venv/bin/python"
keys() { local o="" n; for n in "$@"; do v="$(printenv "GROQ_KEY_$n")"; [ -n "$v" ] && o="${o:+$o,}$v"; done; printf '%s' "$o"; }
run() {  # style, keys...
  local style="$1"; shift
  (
    export TEAM_B_LLM_API_KEY="$(keys "$@")" TEAM_B_USAGE_LOG="$RUNS/usage/longlive_$style.jsonl" TEAM_B_USAGE_RUN_LABEL="longlive_$style"
    "$PY" "$RUNS/diagnose_run.py" --set "$ROOT/orchestrator/eval/long" --llm --agent-mode on --style "$style" --concurrency 1 \
      --out "$RUNS/diagnosis/longlive_$style.jsonl" > "$RUNS/logs/longlive_$style.log" 2>&1
  ) &
}
run en 8 9 10 11 12
run ar 18 19 20 21 22
run mixed 13 14 15 16 17
wait
echo "long runs finished"
