#!/usr/bin/env bash
# Orchestrator comparison: the SAME 25 English policy / refund conversations (27 turns) with a different model as the
# orchestrator job. Everything else is as in the headline run (real knowledge service, reviewer gpt-oss-120b, other jobs qwen).
# The gpt-oss-120b baseline is the saved run diagnosis/live_en_policy.jsonl. Needs GROQ_KEY_n and the knowledge service on 8001.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
RUNS="$ROOT/docs/reports/eval_runs"
cd "$ROOT/team_b" || exit 1
source "$RUNS/agent_env.sh.example"
export TEAM_B_MODE=live TEAM_B_TEAM_A_URL=http://127.0.0.1:8001 TEAM_B_TEAM_A_TIMEOUT_S=30 PYTHONUTF8=1
PY=".venv/Scripts/python"; [ -x "$PY" ] || PY=".venv/bin/python"
keys() { local o="" n; for n in $1; do v="$(printenv "GROQ_KEY_$n")"; [ -n "$v" ] && o="${o:+$o,}$v"; done; printf '%s' "$o"; }
run() {  # slug, model, effort, key numbers
  local slug="$1" model="$2" effort="$3" ks="$4"
  (
    export TEAM_B_LLM_API_KEY="$(keys "$ks")" TEAM_B_LLM_MODEL_ORCHESTRATOR="$model" TEAM_B_LLM_REASONING_EFFORT_ORCHESTRATOR="$effort"
    export TEAM_B_USAGE_LOG="$RUNS/usage/orch_$slug.jsonl" TEAM_B_USAGE_RUN_LABEL="orch_$slug"
    rm -f "$TEAM_B_USAGE_LOG"
    "$PY" "$RUNS/diagnose_run.py" --set eval/conversations --llm --agent-mode on --style en --topics K,T17,T22 --concurrency 1 \
      --out "$RUNS/diagnosis/orch_$slug.jsonl" > "$RUNS/logs/orch_$slug.log" 2>&1
    echo "$slug exit=$?"
  ) &
}
run gpt-oss-20b openai/gpt-oss-20b low "${KEYS_20B:-23 3 4 5 7 14}"
run qwen3.8-27b qwen/qwen3.8-27b "" "${KEYS_QWEN:-15 16 17 18 19 20 21}"
wait
