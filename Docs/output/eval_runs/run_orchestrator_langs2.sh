#!/usr/bin/env bash
# Second pass of the Arabic / mixed orchestrator comparison: qwen3.8-27b again (its first pass ran out of quota) and
# gpt-oss-120b (reasoning effort low, as in the product). Same 25 policy / refund conversations, real knowledge service.
# Needs GROQ_KEY_n and the knowledge service on port 8001. One conversation at a time per process; key sets do not overlap.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"; RUNS="$ROOT/Docs/output/eval_runs"
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
      --out "$RUNS/diagnosis/orch_${lang}_$slug.jsonl" > "$RUNS/logs/orch_${lang}_$slug.log" 2>&1
    echo "$lang $slug exit=$?"
  ) &
}
case "${1:-first}" in
  first)  # three processes now
    run ar qwen3.8-27b qwen/qwen3.8-27b - "1 2 3 4 5"
    run mixed qwen3.8-27b qwen/qwen3.8-27b - "6 7 8 9 10"
    run ar gpt-oss-120b openai/gpt-oss-120b low "14 15 16 17 18 19 20"
    ;;
  mixed120)  # later, when the 120B quota has refilled
    run mixed gpt-oss-120b openai/gpt-oss-120b low "${K120:-14 15 16 17 18 19 20 21 23}"
    ;;
esac
wait
