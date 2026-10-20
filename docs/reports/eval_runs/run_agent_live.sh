#!/usr/bin/env bash
# The agent-mode evaluation against the REAL knowledge service (AGENT_BACKENDS=live) instead of the keyword simulated service.
# Start the knowledge service first:  cd team_a && PYTHONPATH=src .venv/Scripts/python -m uvicorn knowledge_service.api:app --app-dir src --port 8001
# Needs GROQ_KEY_n in the environment (never in this repository). Reports: orchestrator/reports/eval_presentation_agent_live_<style>_<date>.*
# Keys per style: en = 8-12, ar = 18-22, mixed = 13-17 (override with KEYS_EN / KEYS_AR / KEYS_MIXED / KEYS_ARABIZI).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
RUNS="$ROOT/docs/reports/eval_runs"
cd "$ROOT/services/agent" || exit 1
source "$RUNS/agent_env.sh.example"
export AGENT_BACKENDS=live AGENT_KNOWLEDGE_URL="${AGENT_KNOWLEDGE_URL:-http://127.0.0.1:8001}" AGENT_KNOWLEDGE_TIMEOUT_S=30
PY=".venv/Scripts/python"; [ -x "$PY" ] || PY=".venv/bin/python"
keys() { local o="" n; for n in $1; do v="$(printenv "GROQ_KEY_$n")"; [ -n "$v" ] && o="${o:+$o,}$v"; done; printf '%s' "$o"; }
run() {  # style, key numbers, extra args
  local style="$1" ks="$2"; shift 2
  (
    export AGENT_LLM_API_KEY="$(keys "$ks")" AGENT_USAGE_LOG="$RUNS/usage/agent_live_$style.jsonl" AGENT_USAGE_RUN_LABEL="presentation_agent_live_$style"
    "$PY" -m agent_service eval --set "$RUNS/sets/$style" --llm --agent-mode on --judge --resume --label "presentation_agent_live_$style" "$@" \
      > "$RUNS/logs/live_$style.log" 2>&1
  ) &
}
run en "${KEYS_EN:-8 9 10 11 12}"
run mixed "${KEYS_MIXED:-13 14 15 16 17}"
run ar "${KEYS_AR:-18 19 20 21 22}"
[ -n "${KEYS_ARABIZI:-}" ] && run arabizi "$KEYS_ARABIZI" --per-style "${ARABIZI_PER_STYLE:-15}"
wait
echo "live agent runs finished (a style that stopped early continues when this script is run again)"
