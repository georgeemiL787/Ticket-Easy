#!/usr/bin/env bash
# Agent-mode diagnosis runs (diagnose_run.py), three processes at once with five Groq keys each.
# Needs GROQ_KEY_1..15 in the environment (never in this repository).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
RUNS="$ROOT/docs/reports/eval_runs"
cd "$ROOT/services/agent" || exit 1
source "$RUNS/agent_env.sh.example"
PY=".venv/Scripts/python"; [ -x "$PY" ] || PY=".venv/bin/python"
mkdir -p "$RUNS/diagnosis" "$RUNS/logs" "$RUNS/usage"
keys() { local o="" n; for n in "$@"; do v="$(printenv "GROQ_KEY_$n")"; [ -n "$v" ] && o="${o:+$o,}$v"; done; printf '%s' "$o"; }
run() {  # name, keys..., then diagnose_run.py arguments after --
  local name="$1"; shift; local ks=(); while [ "$1" != "--" ]; do ks+=("$1"); shift; done; shift
  (
    export TEAM_B_LLM_API_KEY="$(keys "${ks[@]}")" TEAM_B_USAGE_LOG="$RUNS/usage/diag_$name.jsonl" TEAM_B_USAGE_RUN_LABEL="diag_$name"
    "$PY" "$RUNS/diagnose_run.py" --set "$ROOT/orchestrator/eval/conversations" --llm --agent-mode on "$@" \
      --out "$RUNS/diagnosis/agent_$name.jsonl" > "$RUNS/logs/diag_$name.log" 2>&1
  ) &
}
run ar_all 1 2 3 4 5 -- --style ar --concurrency 3
run mixed_all 6 7 8 9 10 -- --style mixed --concurrency 3
run en_policy 11 12 13 14 15 -- --style en --topics K,T17,T22 --concurrency 3
wait
echo "diagnosis runs finished"
