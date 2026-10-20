#!/usr/bin/env bash
# E5: the 155 labelled messages through the AI reader of each of the three Groq models, three processes at once.
# Only the reader job's model changes (TEAM_B_LLM_MODEL_READER); same prompt, temperature 0, no backup model (so a failure
# shows as a failure), reasoning effort low for the gpt-oss models. Needs GROQ_KEY_n in the environment (no keys here).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
RUNS="$ROOT/docs/reports/eval_runs"
cd "$ROOT/team_b" || exit 1
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
PY=".venv/Scripts/python"; [ -x "$PY" ] || PY=".venv/bin/python"
keys() { local o="" n; for n in $1; do v="$(printenv "GROQ_KEY_$n")"; [ -n "$v" ] && o="${o:+$o,}$v"; done; printf '%s' "$o"; }
run() {  # slug, model, effort, key numbers
  local slug="$1" model="$2" effort="$3" ks="$4"
  (
    export TEAM_B_LLM=openai TEAM_B_LLM_BASE_URL=https://api.groq.com/openai/v1 TEAM_B_LLM_API_KEY="$(keys "$ks")"
    export TEAM_B_LLM_MODEL="$model" TEAM_B_LLM_MODEL_READER="$model" TEAM_B_LLM_TIMEOUT_S=60 TEAM_B_LLM_MAX_WAIT_S=120
    export TEAM_B_LLM_RPM_READER=30 TEAM_B_LLM_TPM_READER=8000
    [ -n "$effort" ] && export TEAM_B_LLM_REASONING_EFFORT_READER="$effort"
    export TEAM_B_USAGE_LOG="$RUNS/usage/nlu_$slug.jsonl" TEAM_B_USAGE_RUN_LABEL="nlu_$slug"
    rm -f "$TEAM_B_USAGE_LOG"
    "$PY" "$RUNS/models/run_nlu_model.py" ${DATA:+--data "$DATA"} --out "$RUNS/models/nlu_$slug.jsonl" > "$RUNS/models/nlu_$slug.txt" 2>&1
    echo "$slug exit=$?"
  ) &
}
run gpt-oss-20b openai/gpt-oss-20b low "${KEYS_20B:-3 4 5 7}"
run qwen3.8-27b qwen/qwen3.8-27b "" "${KEYS_QWEN:-8 10 14 15}"
run gpt-oss-120b openai/gpt-oss-120b low "${KEYS_120B:-16 17 18 19 20 21}"
wait
