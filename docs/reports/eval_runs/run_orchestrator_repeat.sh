#!/usr/bin/env bash
# Repeat of the orchestrator comparison run (same 25 English policy / refund conversations) to measure run-to-run variation.
# usage: run_orchestrator_repeat.sh <slug> <model> <effort|-> <suffix> "<key numbers>"   (e.g. qwen3.8-27b qwen/qwen3.8-27b - rep2 "8 9 10")
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"; RUNS="$ROOT/docs/reports/eval_runs"
slug="$1"; model="$2"; effort="$3"; suffix="$4"; ks="$5"
cd "$ROOT/services/agent" || exit 1
source "$RUNS/agent_env.sh.example"
export AGENT_BACKENDS=live AGENT_KNOWLEDGE_URL=http://127.0.0.1:8001 AGENT_KNOWLEDGE_TIMEOUT_S=30 PYTHONUTF8=1
o=""; for n in $ks; do v="$(printenv "GROQ_KEY_$n")"; o="${o:+$o,}$v"; done
export AGENT_LLM_API_KEY="$o" AGENT_LLM_MODEL_PLANNER="$model"
if [ "$effort" = "-" ]; then unset AGENT_LLM_REASONING_EFFORT_PLANNER; else export AGENT_LLM_REASONING_EFFORT_PLANNER="$effort"; fi
export AGENT_USAGE_LOG="$RUNS/usage/orch_${slug}_$suffix.jsonl" AGENT_USAGE_RUN_LABEL="orch_${slug}_$suffix"; rm -f "$AGENT_USAGE_LOG"
PY=".venv/Scripts/python"; [ -x "$PY" ] || PY=".venv/bin/python"
"$PY" "$RUNS/diagnose_run.py" --set eval/conversations --llm --agent-mode on --style en --topics K,T17,T22 --concurrency 1 \
  --out "$RUNS/diagnosis/orch_${slug}_$suffix.jsonl" > "$RUNS/logs/orch_${slug}_$suffix.log" 2>&1
echo "$slug $suffix exit=$?"
