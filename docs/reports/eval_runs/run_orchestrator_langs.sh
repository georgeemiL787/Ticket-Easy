#!/usr/bin/env bash
# Orchestrator comparison on Arabic and mixed policy / refund conversations (25 each), qwen and gpt-oss-20b as orchestrator.
# Each run also records its AI calls (diagnosis/rec_<lang>_<slug>.jsonl) for the writer / reviewer / knowledge replays.
# Needs GROQ_KEY_n and the knowledge service on port 8001. Output: diagnosis/orch_<lang>_<slug>.jsonl
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"; RUNS="$ROOT/docs/reports/eval_runs"
cd "$ROOT/team_b" || exit 1
source "$RUNS/agent_env.sh.example"
export TEAM_B_MODE=live TEAM_B_TEAM_A_URL=http://127.0.0.1:8001 TEAM_B_TEAM_A_TIMEOUT_S=30 PYTHONUTF8=1
PY=".venv/Scripts/python"; [ -x "$PY" ] || PY=".venv/bin/python"
run() {  # lang, slug, model, effort, key numbers
  local lang="$1" slug="$2" model="$3" effort="$4" ks="$5"
  (
    o=""; for n in $ks; do v="$(printenv "GROQ_KEY_$n")"; o="${o:+$o,}$v"; done
    export TEAM_B_LLM_API_KEY="$o" TEAM_B_LLM_MODEL_ORCHESTRATOR="$model"
    if [ "$effort" = "-" ]; then unset TEAM_B_LLM_REASONING_EFFORT_ORCHESTRATOR; else export TEAM_B_LLM_REASONING_EFFORT_ORCHESTRATOR="$effort"; fi
    export TEAM_B_USAGE_LOG="$RUNS/usage/orch_${lang}_$slug.jsonl" TEAM_B_USAGE_RUN_LABEL="orch_${lang}_$slug"; rm -f "$TEAM_B_USAGE_LOG"
    "$PY" "$RUNS/diagnose_run.py" --set eval/conversations --llm --agent-mode on --style "$lang" --topics K,T17,T22 --concurrency 1 \
      --record "$RUNS/diagnosis/rec_${lang}_$slug.jsonl" --out "$RUNS/diagnosis/orch_${lang}_$slug.jsonl" > "$RUNS/logs/orch_${lang}_$slug.log" 2>&1
    echo "$lang $slug exit=$?"
  ) &
}
run ar qwen3.8-27b qwen/qwen3.8-27b - "${K1:-3 4 5 7}"
run mixed qwen3.8-27b qwen/qwen3.8-27b - "${K2:-14 15 16 17}"
run ar gpt-oss-20b openai/gpt-oss-20b low "${K3:-18 19 20 21}"
run mixed gpt-oss-20b openai/gpt-oss-20b low "${K4:-23 8 9 10}"
wait
