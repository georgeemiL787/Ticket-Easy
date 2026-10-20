# Eval and cost report prompts

16 October 2026 · Prompts for the plan [Eval_Cost_Presentation_Plan.md](Eval_Cost_Presentation_Plan.md). Starting point: the `a+b+c-integration` branch as it is today (13 October, never run in CI).

The goal is one report, `Docs/output/Ticket-Easy_Eval_and_Cost_Report.md`, with chart-ready tables for two presentation points:

1. **Evaluation pipeline and results:** fresh, measured results with agent mode as the AI run.
2. **Models and cost:** measured token usage, a small measured model comparison of free models, and cost tables.

Decisions already taken (do not re-open them in a prompt):

- All work on a **new branch made from `a+b+c-integration`**. Nothing is merged.
- **Agent mode is the AI run**, with all AI helpers switched on.
- **Free tiers only for anything that runs:** Groq free plan (several keys from different accounts), local Ollama, and the free OpenRouter models the knowledge service already uses. Popular paid API models and other local models appear only as **hypothetical** cost rows: never run.
- **Arabizi is appendix only.** It is still run so numbers stay comparable, but headlines use English, Arabic and mixed.
- **No new product features.** The only code changes are the token usage log in all three services (E1, E1b) plus small report helpers under `Docs/output/eval_runs/`.
- **Tokens are measured in every component:** conversation service, knowledge service and tool service, in one shared log format.
- **The report says** knowledge service, conversation service and tool service, never team letters.

## How to use this file

1. **Open Claude Code (Sonnet 5.5) at the repository root, on the `eval-cost-report` branch.** Start a fresh session for every prompt.
2. **Paste the whole grey block** of the next prompt, in order. Each prompt assumes the ones before it are done.
3. **Read the report it gives back.** If something fails, send "Continue: fix what failed in the last run" in the same session.
4. **Save a checkpoint** with the commands under each prompt. Push when you like: `git pull --no-rebase origin eval-cost-report && git push origin eval-cost-report`. Never force-push, never merge.
5. Prompts marked **🔑** call real AI models with your keys. All others run without any AI model.

If a prompt breaks things badly, `git reset --hard HEAD` takes you back to your last checkpoint.

## Overview

| # | Prompt | Plan step |
|---|---|---|
| E0 | Branch health check | 0 |
| E1 | Token usage log: conversation service | 1 |
| E1b | Token usage log: knowledge and tool services | 1 |
| E2 | Knowledge service evaluations | 2 |
| E3 | Conversation service rules evaluations | 3 |
| E4 🔑 | Agent-mode 240 run, four parallel processes | 4 |
| E5 🔑 | Model comparison on Groq (three models, 155 messages) | 5 |
| E5b 🔑 | Measured onboarding and knowledge-service per-message tokens | 5b |
| E6 | Merge, rollups and comparisons | 6 |
| E7 | Model and cost tables | 7 |
| E8 | Write the report | 8 |

## Before you start (once)

1. Make the branch and install:
   ```bash
   git fetch origin a+b+c-integration
   git checkout -b eval-cost-report origin/a+b+c-integration
   git push -u origin eval-cost-report
   cd team_b && make install && cd ..
   cd team_a && pip install -r requirements.txt pytest && cd ..
   cd team_c && pip install -e . pytest && cd ..
   cd dashboard && npm ci && cd ..
   mkdir -p Docs/output/eval_runs
   ```
2. Keys go in `.env` files or your shell only, never in a committed file:
   - `GROQ_KEY_1` … `GROQ_KEY_4`: four Groq keys (one per parallel process in E4).
   - For the tool service in E5b: `GROQ_API_KEY` (and optionally `GROQ_API_KEY_2` to `_4`) in `team_c/.env`, with `LLM_PRIMARY=groq`. For the knowledge service in E5b: `OPENROUTER_API_KEY` in `team_a/.env`.
3. For the knowledge service evaluations with embeddings, have Ollama running with `bge-m3` pulled (`ollama pull bge-m3`). Without it, retrieval falls back to keywords and E2 says so.

---

### E0 · Branch health check

```text
<goal>
Make sure the branch works before we measure anything: every test suite of the three services and the dashboard passes. Fix only what blocks the evaluation runs.
</goal>

<context>
This branch (eval-cost-report) was made from a+b+c-integration: 21 commits on top of main that have never been run in CI; some integration scripts were only syntax-checked. The services: conversation service in team_b, knowledge service in team_a, tool service in team_c, dashboard in dashboard/.

Read first: team_b/CLAUDE.md, team_b/Makefile, team_a/README.md, team_a/pytest.ini, team_c/README.md, team_c/pyproject.toml.
</context>

<task>
1. Run every suite and record the results:
   - team_b: make test, make lint, make scenarios
   - team_a: PYTHONPATH=src python -m pytest -q
   - team_c: python -m pytest -q
   - dashboard: npm run typecheck && npm test
2. For each failure, find the cause. Fix it only if it would affect the evaluation commands used later (team_b eval, eval-nlu, make scenarios; team_a eval-retrieval, eval-guardrails, eval-scenarios). List every other failure in the report without fixing it.
3. If a test pins a number that the integration work changed on purpose (for example a data size), say so instead of changing code.
</task>

<constraints>
Never skip, delete or loosen a test to get green. Never change a safety test's expectation. No product changes.
</constraints>

<done_when>Every suite has been run, and nothing that blocks the evaluation commands is failing.</done_when>

<verify>cd team_b && make test && make scenarios; cd ../team_a && PYTHONPATH=src python -m pytest -q; cd ../team_c && python -m pytest -q</verify>

<report>A table per suite (passed / failed), the fixes made with file and cause, and the failures left alone with one line each.</report>
```

Checkpoint:
```bash
git add -A && git commit -m "E0: branch health check before evaluation"
```

---

### E1 · Token usage log

```text
<goal>
When an environment variable is set, the conversation service writes one line per AI call to a JSONL file with the exact token counts the provider returned, so the report can state measured token usage instead of estimates. Off by default, no change in behaviour.
</goal>

<context>
team_b/src/team_b/adapters/llm.py (OpenAICompatibleLLM) receives every model answer. In _settle it reads response.json()["usage"]["total_tokens"] only to settle the rate limiter, then drops it. Clients are built per job in adapters/llm_registry.py (llm_for(role), _client, _build); jobs are reader, orchestrator, knowledge, reviewer, writer, writer_egyptian, summary, judge. A backup model may be tried (_ask_backup). The eval command (python -m team_b eval, eval_conversations.py) and eval-nlu use the same registry.

Read first: team_b/CLAUDE.md, adapters/llm.py, adapters/llm_registry.py, adapters/llm_limits.py, __main__.py, eval_conversations.py.
</context>

<task>
1. A new setting TEAM_B_USAGE_LOG (a file path; empty = off) and TEAM_B_USAGE_RUN_LABEL (free text, optional), read the same way other settings are read.
2. When the log is on, after every request the client appends one JSON line: ts (UTC ISO), service ("conversation"), run_label, job, model, provider host (no path, no query), status (ok, rate_limited, error, timeout), http_status, input_tokens (usage.prompt_tokens), output_tokens (usage.completion_tokens), reasoning_tokens and cached_tokens when the provider gives them (else null), latency_ms, key_index (the position of the key in the comma list, never the key), backup_used (true when this call was the backup model). A missing usage object writes null, not 0. Rate-limited and failed calls are written too, with null tokens.
3. Pass the job name to the client from the registry (a constructor argument is fine).
4. Writing is append-only, safe when several processes write different files, and never raises: on any error, log a warning once and carry on.
5. Document the line format in Docs/output/eval_runs/USAGE_LOG_FORMAT.md: every field, its meaning, and the rule that the knowledge and tool services (E1b) write exactly the same fields.
6. A small script team_b/scripts/usage_summary.py: reads one or more JSONL logs and prints totals per service, run_label, job and model (calls, ok calls, input, output, total tokens, average tokens per ok call, latency p50/p95), and can write the same as CSV with --csv.
7. Tests with the existing fake transport or FakeLLM: numbers written correctly; missing usage gives null; a 429 line has status rate_limited and null tokens; the file never contains prompt text, reply text or a key; log off means no file is created; usage_summary totals add up.
</task>

<constraints>
Never write prompt or reply text, customer data or API keys. No behaviour change when the log is off. Touch only team_b.
</constraints>

<done_when>make test and make lint pass; a fake eval run with the log on produces a file that usage_summary reads.</done_when>

<verify>cd team_b && make test && make lint</verify>

<report>The line format with one example line, the files changed, and the usage_summary output for the test run.</report>
```

Checkpoint:
```bash
git add -A && git commit -m "E1: optional token usage log for every AI call, and usage_summary"
```

---

### E1b · Token usage log: knowledge and tool services

```text
<goal>
The knowledge service and the tool service write the same token usage log as the conversation service, in exactly the same line format, so one summary covers every AI call in the product.
</goal>

<context>
The format is in Docs/output/eval_runs/USAGE_LOG_FORMAT.md (E1); team_b/scripts/usage_summary.py reads it. AI calls outside the conversation service:
- Knowledge service (team_a): team_a/src/team_a/llm.py is an OpenRouter client restricted to free models (ids ending in :free), used for rule extraction from policy documents (policy/extract.py, command: python -m team_a rules extract --document ...) and as a second opinion in risk classification (policy/risk.py). Embeddings come from bge-m3 through Ollama's /api/embed (knowledge/embeddings.py), for documents at indexing (ingest, db build) and for each search query.
- Tool service (team_c): every model call goes through team_c/team_c/llm/router.py and the providers in llm/ (groq.py, openrouter.py, ollama.py, openai_compat.py). ProviderResponse already carries a usage dict, currently written only into a "model_response" diagnostic. Calls include triage, generation, reconciliation, revision and suggestion. Groq keys rotate GROQ_API_KEY to GROQ_API_KEY_4 on rate limits.

Read first: Docs/output/eval_runs/USAGE_LOG_FORMAT.md, team_b/scripts/usage_summary.py, team_a/README.md, team_a/src/team_a/config.py, llm.py, policy/extract.py, policy/risk.py, knowledge/embeddings.py; team_c/README.md, team_c/team_c/config.py, llm/router.py, llm/types.py, llm/groq.py, llm/openrouter.py, llm/ollama.py.
</context>

<task>
1. In each service, the same two settings: a log path (TEAM_A_USAGE_LOG / TEAM_C_USAGE_LOG; empty = off) and a run label (TEAM_A_USAGE_RUN_LABEL / TEAM_C_USAGE_RUN_LABEL), read the way each service reads its settings.
2. Knowledge service: one line per OpenRouter call (service "knowledge", job "rule_extraction" or "risk_second_opinion") with the tokens from the response's usage object; one line per embedding request (job "embed_document" or "embed_query", kind "embedding") with input tokens from Ollama's prompt_eval_count when the response has it, otherwise a documented estimate with an estimated=true field.
3. Tool service: one line per provider attempt (service "tools", job = the call kind: triage, generation, reconciliation, revision, suggestion, or whatever the router names it), with tokens from the usage dict already there, the provider, the model, status, and the key's position (never the key). A fallback to another provider is its own line.
4. Same rules as E1: append-only, never raises, never text, no customer data, no keys; off by default with no behaviour change.
5. Make usage_summary.py group by service as well (it may already), and check it reads all three services' files together.
6. Tests in each service with its existing fake transports: lines written with the right numbers and fields, missing usage gives null, a failed or rate-limited call is logged with null tokens, nothing written when off, no text or key in the file. One test feeds a line from each service into usage_summary.py.
</task>

<constraints>
Keep the knowledge service's free-models-only rule. Do not change prompts, generation, extraction or retrieval. Never store text or keys.
</constraints>

<done_when>Both services' suites pass and usage_summary.py reads a mixed file from all three services.</done_when>

<verify>cd team_a && PYTHONPATH=src python -m pytest -q; cd ../team_c && python -m pytest -q; cd ../team_b && make test</verify>

<report>For each service: the jobs logged, one example line, and how embedding tokens are counted (reported or estimated).</report>
```

Checkpoint:
```bash
git add -A && git commit -m "E1b: same token usage log in the knowledge and tool services"
```

---

### E2 · Knowledge service evaluations

```text
<goal>
Fresh knowledge service results for the report: retrieval quality on three businesses, guardrail decisions, and the noon scenario check, saved as files the report can cite.
</goal>

<context>
Commands (team_a/src/team_a/__main__.py): python -m team_a eval-retrieval --tenant X [--split dev|test] [--sweep, dev only]; eval-guardrails [--tenant]; eval-scenarios --tenant noon_eg (needs db build); db build [--tenant X]. Recorded results in team_a/README.md: shop_001 recall@5 89.3%, noon_eg 97.5%, guardrails 44/44. No result exists yet for telco_001. Embeddings come from bge-m3 through Ollama; without it retrieval falls back to keywords.

Read first: team_a/README.md, team_a/src/team_a/__main__.py, team_a/src/team_a/evaluation.py.
</context>

<task>
1. Check whether Ollama with bge-m3 is reachable and write which retrieval mode will be used. Turn the knowledge service usage log on for all runs in this prompt: TEAM_A_USAGE_LOG=Docs/output/eval_runs/usage/knowledge_retrieval.jsonl, TEAM_A_USAGE_RUN_LABEL=knowledge_retrieval (E1b). The query embeddings logged here give the measured tokens per search.
2. Run eval-retrieval --split test for shop_001, noon_eg and telco_001, and --split dev for each (dev numbers are for reference only; never run --sweep on test).
3. Run eval-guardrails (for every tenant it supports) and confirm 44/44 for shop_001 with 0 blocked actions executed.
4. Run db build for noon_eg if needed, then eval-scenarios --tenant noon_eg.
5. Save each command's full output to Docs/output/eval_runs/knowledge/<command>_<tenant>_<split>.txt, and write Docs/output/eval_runs/knowledge/summary.json with the key numbers: per tenant and split, recall@5, no-answer precision and recall, per-style breakdown when given; guardrail pass count; noon section_recall@5; the retrieval mode; date and commit.
6. If telco_001 has no benchmark or fails to load, say exactly why and leave it out rather than inventing a number.
</task>

<constraints>No code changes. If a command fails, report the error; do not change the evaluation.</constraints>

<done_when>All outputs and summary.json exist, or each missing one has a stated reason.</done_when>

<verify>cat Docs/output/eval_runs/knowledge/summary.json</verify>

<report>A table per tenant (test split): recall@5, no-answer precision and recall, compared with the README numbers; guardrails; noon scenarios; retrieval mode.</report>
```

Checkpoint:
```bash
git add -A && git commit -m "E2: knowledge service evaluation outputs"
```

---

### E3 · Conversation service rules evaluations

```text
<goal>
Fresh rules-only results (no AI model) for the conversation service: understanding accuracy, the full 240-conversation test and the scripted scenarios. These are the starting point the agent-mode run is compared with.
</goal>

<context>
Commands: python -m team_b eval-nlu --mode rules (155 labelled messages in eval/nlu_labelled.jsonl; baseline in eval/nlu_baseline.json, 87.42%); python -m team_b eval --set eval/conversations --label presentation_rules (240 conversations, 60 per style: en, ar, mixed, arabizi; reports go to team_b/reports/eval_<label>_<date>.json and .md); make scenarios (runs tests/integration/test_scenarios.py then scripts/scenario_report.py). Baselines: team_b/eval/baseline_phase0.json and team_b/eval/eval_baseline.json. Earlier rules results: intent 62.5%, decision 66.2%, citation 36.0%.

Read first: team_b/CLAUDE.md, __main__.py, eval_conversations.py (docstring), nlu_eval.py, scripts/scenario_report.py.
</context>

<task>
1. Make sure no AI model is configured for this run (TEAM_B_LLM=none, no model settings).
2. Run eval-nlu --mode rules and save the output to Docs/output/eval_runs/conversation/nlu_rules.txt. Check intent accuracy ≥ baseline − 0.02.
3. Run the full 240 rules eval with label presentation_rules (in team_b).
4. Run make scenarios, save the scenario report output to Docs/output/eval_runs/conversation/scenarios.txt, and record how many scenarios exist per business (shop_001, noon_eg), how many passed and how many are pending.
5. Run eval-compare between eval/baseline_phase0.json and the new presentation_rules report; save to Docs/output/eval_runs/conversation/compare_rules_vs_phase0.txt.
6. Confirm in the new report: wrong_or_unapproved_actions = 0 and unbacked_facts = 0.
</task>

<constraints>No code changes. No AI model. If a command fails, report it.</constraints>

<done_when>The NLU output, the presentation_rules report, the scenario output and the comparison exist.</done_when>

<verify>ls team_b/reports/eval_presentation_rules_*; ls Docs/output/eval_runs/conversation/</verify>

<report>NLU accuracy vs baseline; the 240 rules results per style (intent, decision, citation, locale, handoff precision/recall, solved without a person); scenario counts; the two safety numbers; differences vs phase0.</report>
```

Checkpoint:
```bash
git add -A && git commit -m "E3: conversation service rules-only evaluation outputs"
```

---

### E4 🔑 · Agent-mode 240 run, four parallel processes

```text
<goal>
Run the full 240-conversation test in agent mode with every AI helper on, on the Groq free tier, as four processes side by side (one per language style, one Groq key each), with the token usage log on. This produces the headline AI results.
</goal>

<context>
Within one process, several keys add no speed: adapters/llm.py only moves to the next key on HTTP 429, and adapters/llm_limits.py keeps one rate budget per (server, model). Separate processes each get their own budget. eval supports --set (a folder of .jsonl files), --llm, --agent-mode on, --judge, --label, --resume, --batch, --per-style. In code the four AI helper switches default to off (config.py: llm_rewrite, llm_policy, llm_knowledge, llm_review = False); only .env.example turns them on, so the run must set them. The token usage log is TEAM_B_USAGE_LOG and TEAM_B_USAGE_RUN_LABEL (E1). Keys are in GROQ_KEY_1 to GROQ_KEY_4 in my shell or a local untracked env file. The earlier small comparison (team_b/docs/agent_comparison.md) needed a raised turn time limit because of free-plan waiting.

Models (current documented free setup, from .env.example): orchestrator and reviewer openai/gpt-oss-120b with reasoning_effort low; reader, knowledge, writer, summary qwen/qwen3.8-27b with openai/gpt-oss-20b as backup; judge openai/gpt-oss-20b; base URL https://api.groq.com/openai/v1; RPM and TPM values under the free plan as in .env.example.

Read first: team_b/CLAUDE.md, .env.example (the AI section), config.py, __main__.py, eval_conversations.py, adapters/llm.py, team_b/docs/agent_comparison.md.
</context>

<task>
1. Make four folders with one style each: Docs/output/eval_runs/sets/{en,ar,mixed,arabizi}/<style>.jsonl, copied from team_b/eval/conversations/.
2. Write Docs/output/eval_runs/agent_env.sh.example (committed, no keys) listing every setting the run needs: TEAM_B_LLM, base URL, model per job, backup models, reasoning effort, RPM/TPM, TEAM_B_LLM_REWRITE=1, TEAM_B_LLM_POLICY=1, TEAM_B_LLM_KNOWLEDGE=1, TEAM_B_LLM_REVIEW=1, TEAM_B_AGENT_MODE=on, the turn time limit used in agent_comparison.md, and TEAM_B_USAGE_LOG / TEAM_B_USAGE_RUN_LABEL placeholders. Keys come from the shell, never from this file.
3. Smoke test with one key: two English conversations, label smoke_agent. Open the trace or report and confirm: agent moves recorded (agent mode really ran, not "unavailable: no model"), the AI writer, knowledge and reviewer steps ran (replies are not templates), and the usage log has lines for orchestrator, reader, knowledge, reviewer and writer. Stop and report if any of these is missing.
4. Write Docs/output/eval_runs/run_agent_parallel.sh: starts four background processes, style N with GROQ_KEY_N, each: python -m team_b eval --set Docs/output/eval_runs/sets/<style> --llm --agent-mode on --judge --resume --label presentation_agent_<style>, with TEAM_B_LLM_API_KEY set to that one key, TEAM_B_USAGE_LOG=Docs/output/eval_runs/usage/agent_<style>.jsonl and TEAM_B_USAGE_RUN_LABEL=presentation_agent_<style>; each process logs to Docs/output/eval_runs/logs/<style>.log; the script waits and prints which styles finished and which stopped. The Arabizi process uses --per-style so it stays small (state the number). Keys are never echoed.
5. Run it. When a process stops on the daily limit, the same command with --resume continues it later; write in the report which styles need resuming.
6. When all four are complete (or a style is stuck), record per style: conversations completed, fallbacks to the fixed flow, wrong_or_unapproved_actions, unbacked_facts, and the date.
</task>

<constraints>
Never print, log or commit a key. Do not lower quality settings or cut conversations to finish faster (except the stated Arabizi sample). Every latency is "measured on the Groq free tier". No code changes in this prompt; if something is broken, stop and report it.
</constraints>

<done_when>presentation_agent_en, _ar, _mixed and _arabizi reports exist with all conversations completed, or the report says exactly which are incomplete and how many conversations they have.</done_when>

<verify>ls team_b/reports/eval_presentation_agent_*; python team_b/scripts/usage_summary.py Docs/output/eval_runs/usage/agent_*.jsonl</verify>

<report>Per style: completed conversations, intent, decision, citation, fallbacks, the two safety numbers, latency p50/p95, total tokens; and anything that needs resuming.</report>
```

Checkpoint (outputs are evidence for the report):
```bash
git add -A && git commit -m "E4: agent-mode evaluation runs per style with usage logs"
```

---

### E5 🔑 · Model comparison on Groq (three models, 155 messages)

```text
<goal>
One measured model comparison for the slides: all 155 labelled messages understood by each of the three Groq models, with intent accuracy, speed and tokens per call. Only Groq models are run; other models appear later as hypothetical cost rows only.
</goal>

<context>
python -m team_b eval-nlu --mode llm [--data FILE] measures intent accuracy on labelled messages (default eval/nlu_labelled.jsonl, 155 messages). Check in __main__.py and nlu_eval.py which job's model build_nlu("llm", settings) uses (expected: the reader job, TEAM_B_LLM_MODEL_READER, or the general model) and override that one. The usage log is TEAM_B_USAGE_LOG / TEAM_B_USAGE_RUN_LABEL (E1). Groq base URL https://api.groq.com/openai/v1; keys GROQ_KEY_1 to GROQ_KEY_4.

Models: openai/gpt-oss-20b, qwen/qwen3.8-27b, openai/gpt-oss-120b (reasoning_effort low for the gpt-oss models, as in .env.example).

Read first: team_b/__main__.py, nlu_eval.py, adapters/llm_registry.py, .env.example.
</context>

<task>
1. Run the three models side by side, one Groq key each (the same trick as E4: separate processes have separate rate budgets), each with eval-nlu --mode llm on the full labelled set, only the understanding job's model changed, the usage log at Docs/output/eval_runs/usage/nlu_<model-slug>.jsonl and run label nlu_<model-slug>. Save each output to Docs/output/eval_runs/models/nlu_<model-slug>.txt. If a daily limit stops a model, finish it later; do not shrink the set.
2. Run the rules mode on the same set for reference.
3. Write Docs/output/eval_runs/models/comparison.csv with one row per model plus rules: provider, model, intent accuracy overall and for en / ar / mixed / arabizi, calls, failed or rate-limited calls, latency p50/p95, average input and output tokens per call (from the usage log), date.
4. Also make Docs/output/eval_runs/models/sample40.jsonl for E5b: 40 messages from the labelled set, spread over intents and languages, at most 5 Arabizi, fixed random seed written in the report.
</task>

<constraints>
Never print or commit a key. Same messages, same prompts, temperature 0 for every model. No code changes. Do not run any model other than the three Groq models. A model that could not finish is listed with "incomplete" and how many messages it did.
</constraints>

<done_when>comparison.csv has a row for each Groq model and the rules reference, and sample40.jsonl exists.</done_when>

<verify>cat Docs/output/eval_runs/models/comparison.csv</verify>

<report>The comparison table sorted by intent accuracy, and the seed used for sample40.</report>
```

Checkpoint:
```bash
git add -A && git commit -m "E5: Groq model comparison on the 155 labelled messages"
```

---

### E5b 🔑 · Measured onboarding and knowledge-service per-message tokens

```text
<goal>
Measure the two costs the conversation tests cannot see: the one-time cost to onboard a business (tool generation in the tool service, rule extraction and document embeddings in the knowledge service), and the knowledge service's per-message AI work (search embedding and risk second opinion).
</goal>

<context>
Usage logs exist in all three services (E1, E1b): TEAM_A_USAGE_LOG / TEAM_C_USAGE_LOG with run labels. Tool service live generation: from team_c, uv run python -m scripts.live_smoke --provider groq --example ecommerce (uses team_c/examples/ecommerce.json and ecommerce-business.txt; Groq keys GROQ_API_KEY to GROQ_API_KEY_4 in team_c's environment, model GROQ_MODEL, default openai/gpt-oss-120b). Knowledge service: python -m team_a rules extract --tenant shop_001 --document <file> (OpenRouter free models, OPENROUTER_API_KEY), python -m team_a ingest --tenant shop_001 (document embeddings), python -m team_a risk "<message>" (risk check, second opinion when OpenRouter is configured). The 40-message sample from E5 is Docs/output/eval_runs/models/sample40.jsonl. Retrieval query tokens were logged in E2.

Read first: team_c/README.md (live smoke section), team_c/scripts/live_smoke.py, team_a/src/team_a/__main__.py, team_a/README.md, Docs/output/eval_runs/USAGE_LOG_FORMAT.md.
</context>

<task>
1. Tool generation: run the live smoke for the ecommerce example on Groq with TEAM_C_USAGE_LOG=Docs/output/eval_runs/usage/onboarding_tools.jsonl and run label onboarding_tools_ecommerce. Record how many tools were proposed and whether the run finished. If the Groq daily limit stops it, finish later; do not switch to a different example.
2. Rule extraction: run rules extract for every shop_001 policy document with TEAM_A_USAGE_LOG=Docs/output/eval_runs/usage/onboarding_rules.jsonl and run label onboarding_rules_shop_001. Do not approve or change any rule; this only measures the extraction. Record how many candidate rules came back.
3. Document embeddings: run ingest for shop_001 with the log at Docs/output/eval_runs/usage/onboarding_embed.jsonl (run label onboarding_embed_shop_001), on a copy of the data folder or database if ingest would change committed files.
4. Risk second opinion: run the risk check on each of the 40 sample messages with the log at Docs/output/eval_runs/usage/knowledge_risk.jsonl (run label knowledge_risk), so we know tokens per risk check.
5. Write Docs/output/eval_runs/tables/onboarding_measured.csv (per service and job: calls, input tokens, output tokens, total, latency, model) and Docs/output/eval_runs/tables/knowledge_per_message.csv (tokens per search query from knowledge_retrieval.jsonl, tokens per risk second opinion from knowledge_risk.jsonl, with call counts).
</task>

<constraints>
Never print or commit a key. No code changes. Do not approve rules, publish tools or change committed data. A step that cannot finish is reported with what it measured so far.
</constraints>

<done_when>Both CSV files exist and every row traces to a usage log.</done_when>

<verify>python team_b/scripts/usage_summary.py Docs/output/eval_runs/usage/onboarding_*.jsonl Docs/output/eval_runs/usage/knowledge_*.jsonl</verify>

<report>Total tokens for one onboarding (tools, rules, embeddings separately), tokens per search query, tokens per risk check, and anything that did not finish.</report>
```

Checkpoint:
```bash
git add -A && git commit -m "E5b: measured onboarding and knowledge-service per-message tokens"
```

---

### E6 · Merge, rollups and comparisons

```text
<goal>
Turn the per-style agent runs and the rules run into the headline tables: one merged agent-mode result for all 240, English + Arabic + mixed rollups for the slides, Arabizi kept apart, and comparisons with the baselines.
</goal>

<context>
Inputs: team_b/reports/eval_presentation_rules_*.json (E3), team_b/reports/eval_presentation_agent_{en,ar,mixed,arabizi}_*.json (E4), team_b/eval/baseline_phase0.json, team_b/eval/eval_baseline.json, usage logs from all three services in Docs/output/eval_runs/usage/, and onboarding_measured.csv and knowledge_per_message.csv (E5b). Each report JSON holds per-turn results as well as per-style percentages.

Read first: team_b/src/team_b/eval_conversations.py (the report structure and how each measure is computed), team_b/src/team_b/eval_compare.py, one of the report JSON files.
</context>

<task>
1. A helper script Docs/output/eval_runs/merge_runs.py (standard library only, run with python -I): reads the four per-style agent reports and writes one merged report for all styles, recomputing every measure from the per-turn results exactly the way eval_conversations.py computes it (not by averaging percentages). Refuse to merge if the model settings or prompt versions differ between files, and say which.
2. Write Docs/output/eval_runs/tables/:
   - headline.csv: rules vs agent for EN+AR+mixed combined (intent, decision, citation, locale match, handoff precision and recall, solved without a person, wrong actions, unbacked facts, latency p50/p95);
   - per_style.csv: the same per style, Arabizi included but marked appendix;
   - safety.csv: wrong_or_unapproved_actions and unbacked_facts for every run (must be 0);
   - tokens.csv: from the usage logs, per style and combined: AI calls per message, input and output tokens per message and per conversation, per job, for the conversation service;
   - tokens_all_services.csv: tokens per conversation for every component together: the conversation service (measured in E4) plus the knowledge service's search embeddings and risk second opinions (tokens per call from knowledge_per_message.csv, E5b, times the number of policy searches and risk checks per conversation counted from the agent run's traces or reports; if a count is not available, label that cell "estimate");
   - onboarding.csv: one business onboarding by service and job, copied from onboarding_measured.csv (E5b);
   - usage_totals.csv: total tokens measured across every run in this work (conversation, knowledge, tools), per service and run label, with dates.
3. Run eval-compare: presentation_rules vs baseline_phase0, and merged agent vs presentation_rules; save the outputs.
4. Use team_b's own eval-compare where it already does the job; the helper only does what eval-compare cannot.
</task>

<constraints>No changes to team_b code. Numbers only from the run files. If a style is incomplete, mark it and its sample size in every table.</constraints>

<done_when>All table files and both comparisons exist; safety.csv shows 0 everywhere or the report flags otherwise.</done_when>

<verify>python -I Docs/output/eval_runs/merge_runs.py --help; ls Docs/output/eval_runs/tables/</verify>

<report>headline.csv and safety.csv pasted, and the biggest differences between rules and agent mode.</report>
```

Checkpoint:
```bash
git add -A && git commit -m "E6: merged agent run, headline rollups and comparisons"
```

---

### E7 · Model and cost tables

```text
<goal>
Chart-ready model and cost tables built from measured tokens and current public list prices: cost per 1,000 messages, monthly cost at 10k and 100k messages, a pilot example, and the one-time onboarding cost, with actual spend shown as $0 on free tiers.
</goal>

<context>
Measured inputs: Docs/output/eval_runs/tables/tokens.csv, tokens_all_services.csv, onboarding.csv and usage_totals.csv (E6), Docs/output/eval_runs/models/comparison.csv (E5), usage logs. The plan's model table and its rules are in Docs/plan/Eval_Cost_Presentation_Plan.md (Part 2): a model not run in this refresh is marked "hypothetical, not tested" and appears only in cost rows; the what-if models and their indicative prices are in Part 2 ("What-if models"). Section 6.4 of the analysis report (if copied into the repo at reports/Ticket-Easy_Technical_and_Business_Analysis.md) lists Groq prices checked on 16 October 2026. The onboarding tokens were measured in E5b.

Read first: the plan's Part 2 and the input files above.
</context>

<task>
1. Fetch current list prices for the measured models from Groq's public pricing page (and OpenRouter's page for the paid version of the free models the knowledge service uses, if one exists). Write Docs/output/eval_runs/tables/prices.csv with model, input and output price per million tokens, source URL and date. If a price cannot be fetched, take it from section 6.4 of the analysis report with its date, or write "not found"; never guess.
2. models.csv: the plan's model table filled in: model, deploy (local / cloud), role, status (measured in this refresh / hypothetical, not tested), latency and quality from E5 or E4 where measured, price.
3. hypothetical.csv: the what-if models from the plan's Part 2 ("What-if models (hypothetical, not tested)": the cloud API table and the local table). Do NOT call any of them. For each cloud model, re-check its price on the provider's own pricing page (date and URL); if the page cannot be reached, use the plan's indicative price and mark it "indicative, 6 Oct 2026". Compute, from the measured tokens per job in tokens_all_services.csv, what the plan's "Bare-minimum model stack" options (today's three Groq models with Qwen 27B on knowledge and writer, Best value, Lowest cost; jobs mapped to slots exactly as the plan's tables say, judge and writer_egyptian excluded) and each of the four what-if stacks would cost per 1,000 messages and per month at 10,000 and 100,000 messages. Every row says "hypothetical, not tested"; quality and speed cells say "not tested". Local options (L1 starter, L2 mirror, L3 rented, from the plan's "Local hardware tiers" table) show $0 API cost plus an ESTIMATED monthly cost using the plan's formula (hardware price / 36 + kW x 730 h x electricity price, or GPU-hours x hourly rate), and the cost per 1,000 messages at 10,000 and 100,000 messages a month. Write a separate local_estimates.csv with every input (hardware price range low/high, power, electricity price, hours) so the numbers can be changed; label every local cell "estimate" and state that throughput was not measured.
4. cost.csv: three stacks: rules + local embeddings; agent mode with today's free Groq mix (measured tokens per message from tokens.csv); agent mode with the Groq mix adjusted by the E5 result if a different Groq model understood messages better. Use tokens_all_services.csv so every component is included, and show the conversation service and the knowledge service as separate columns. For each: actual spend ($0), cost per 1,000 messages at list price, monthly at 10,000 and 100,000 messages, and the pilot example (50,000 messages + 2 onboardings).
5. onboarding_cost.csv: the measured onboarding from onboarding.csv priced at list price, split into tool generation, rule extraction and document embeddings ($0 API for local embeddings).
6. A short notes file Docs/output/eval_runs/tables/cost_notes.md: every formula used, which cells are measured, estimated or hypothetical, the tokenizer caveat (other models may count the same text as more or fewer tokens), and the local-hardware caveat (Ollama has $0 API cost; hardware and electricity are estimated from the plan's tier table and the formula above, throughput not measured).
</task>

<constraints>No invented prices or token counts. Every estimate is labelled. What-if models are never called and are never presented as tested or recommended on quality.</constraints>

<done_when>prices.csv, models.csv, hypothetical.csv, cost.csv, onboarding_cost.csv and cost_notes.md exist.</done_when>

<verify>ls Docs/output/eval_runs/tables/; cat Docs/output/eval_runs/tables/cost.csv</verify>

<report>cost.csv pasted, and the list of estimated (not measured) cells.</report>
```

Checkpoint:
```bash
git add -A && git commit -m "E7: model and cost tables from measured tokens and list prices"
```

---

### E8 · Write the report

```text
<goal>
Write Docs/output/Ticket-Easy_Eval_and_Cost_Report.md: a sales-oriented but honest report with chart-ready Markdown tables for the evaluation pipeline and results, and for the models and cost analysis, where every number traces to a file.
</goal>

<context>
Inputs: everything under Docs/output/eval_runs/ (knowledge/, conversation/, models/, tables/, usage/), team_b/reports/eval_presentation_*, team_c/E2E_FINAL_REPORT.md, .github/workflows/eval.yml, team_b/docs/metrics.md. Structure and framing are in Docs/plan/Eval_Cost_Presentation_Plan.md (report outline in Part 1, Part 2 tables, "Report narrative").

Read first: the plan, then every input file.
</context>

<task>
1. Follow the plan's outline:
   - Evaluation: pipeline diagram (Mermaid: gold data, harness, baselines, CI weekly run); headline results (safety 0/0, guardrails 44/44, retrieval recall, scenarios, EN/AR/mixed); knowledge service tables; conversation service tables with rules vs agent side by side, rules shown as the starting point; tool service credibility; how results are shared; honest gaps vs the 0.90 / 0.92 / 0.95 targets, framed as the roadmap and tied to the model comparison.
   - Models and cost: the recommended bare-minimum stack first (two chat model slots plus local embeddings, Qwen 27B kept today for Arabic wording, with its cost per 1,000 messages for today, for each cloud production option, and for the three local tiers with their estimated hardware price and monthly cost); the measured Groq comparison chart table; the model table with measured vs hypothetical status; a "what it would cost with other models" table from hypothetical.csv, clearly titled as hypothetical with the tokenizer caveat; total token usage measured over these runs for every component (usage_totals.csv) with dates, and a plain note that usage before the log existed was not recorded; tokens per conversation by component; cost tables; measured onboarding cost; actual spend $0 on free tiers.
   - Appendix: Arabizi results (one short paragraph and one table row), formulas, prices with sources and dates.
2. Each table is chart-ready: one header row, plain numbers with units in the header, no merged cells.
3. Every number cites its source file in a footnote list at the end of each section.
4. Lead with trust: citations, enforced policy, zero unsafe actions, Arabic and English coverage, measurable search quality.
5. Say "knowledge service", "conversation service" and "tool service"; no team letters anywhere in the text or tables.
</task>

<constraints>
Only numbers from the files. Missing numbers read "not measured". Estimates are labelled. Every latency says "Groq free tier". No paid model recommended.
</constraints>

<done_when>The report exists, every headline number has a footnote, safety cells are 0, Arabizi is not in the headline tables, and the words "Team A", "Team B", "Team C" do not appear.</done_when>

<verify>grep -n -i "team a\|team b\|team c" Docs/output/Ticket-Easy_Eval_and_Cost_Report.md; grep -c "not measured\|estimate" Docs/output/Ticket-Easy_Eval_and_Cost_Report.md</verify>

<report>The section list, the headline table, and any number you could not source.</report>
```

Checkpoint:
```bash
git add -A && git commit -m "E8: evaluation and cost report"
git pull --no-rebase origin eval-cost-report && git push origin eval-cost-report
```
