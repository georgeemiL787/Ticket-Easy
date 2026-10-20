#!/usr/bin/env bash
# Long-conversation set (services/agent/eval/long) in agent mode against the REAL knowledge service (needs it running on port 8001).
# One conversation at a time per process (several at once makes the free plan answer 429 and the agent fall back).
# Needs GROQ_KEY_1..22 in the environment (never in this repository). Output: diagnosis/longlive_<style>.jsonl
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
RUNS="$ROOT/docs/reports/eval_runs"
cd "$ROOT/services/agent" || exit 1
source "$RUNS/agent_env.sh.example"; export AGENT_BACKENDS=live AGENT_KNOWLEDGE_URL=http://127.0.0.1:8001 AGENT_KNOWLEDGE_TIMEOUT_S=30
PY=".venv/Scripts/python"; [ -x "$PY" ] || PY=".venv/bin/python"
keys() { local o="" n; for n in "$@"; do v="$(printenv "GROQ_KEY_$n")"; [ -n "$v" ] && o="${o:+$o,}$v"; done; printf '%s' "$o"; }
run() {  # style, keys...
  local style="$1"; shift
  (
    export AGENT_LLM_API_KEY="$(keys "$@")" AGENT_USAGE_LOG="$RUNS/usage/longlive_$style.jsonl" AGENT_USAGE_RUN_LABEL="longlive_$style"
    "$PY" "$RUNS/diagnose_run.py" --set "$ROOT/services/agent/eval/long" --llm --agent-mode on --style "$style" --concurrency 1 \
      --out "$RUNS/diagnosis/longlive_$style.jsonl" > "$RUNS/logs/longlive_$style.log" 2>&1
  ) &
}
run en 8 9 10 11 12
run ar 18 19 20 21 22
run mixed 13 14 15 16 17
wait
echo "long runs finished"
