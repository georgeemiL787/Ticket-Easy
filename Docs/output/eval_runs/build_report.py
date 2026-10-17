"""Writes Docs/output/Ticket-Easy_Eval_and_Cost_Report.md. Every table is rendered from a file under Docs/output/eval_runs/,
so a number in the report is a number in a file. Run from the repository root; re-run after any table changes."""
import csv
import json
from pathlib import Path

RUNS = Path("Docs/output/eval_runs")
T = RUNS / "tables"
OUT = Path("Docs/output/Ticket-Easy_Eval_and_Cost_Report.md")


def rows(path):
    p = Path(path)
    return list(csv.DictReader(p.open(encoding="utf-8-sig"))) if p.exists() else []


def md(cols, data, heads=None):
    heads = heads or cols
    out = ["| " + " | ".join(heads) + " |", "|" + "---|" * len(cols)]
    for r in data:
        out.append("| " + " | ".join("" if r.get(c) in (None, "") else str(r[c]).replace("|", "/") for c in cols) + " |")
    return "\n".join(out)


def fnum(x, nd=2):
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return x


summary = json.loads((RUNS / "knowledge/summary.json").read_text(encoding="utf-8"))
ret = summary["retrieval"]
headline = rows(T / "headline.csv")
per_style = rows(T / "per_style.csv")
safety = rows(T / "safety.csv")
tokens = rows(T / "tokens.csv")
allsvc = rows(T / "tokens_all_services.csv")
onboard = rows(T / "onboarding.csv")
onb_cost = rows(T / "onboarding_cost.csv")
cost = rows(T / "cost.csv")
hyp = rows(T / "hypothetical.csv")
local = rows(T / "local_estimates.csv")
prices = rows(T / "prices.csv")
models_t = rows(T / "models.csv")
usage_totals = rows(T / "usage_totals.csv")
cmp_ = rows(RUNS / "models/comparison.csv")
orch_en = rows(RUNS / "models/orchestrator_comparison.csv")
orch_rep = rows(RUNS / "models/orchestrator_repeats.csv")
orch_lang = rows(RUNS / "models/orchestrator_comparison_ar_mixed.csv")
replay = rows(RUNS / "models/replay_comparison.csv")
kpm = rows(T / "knowledge_per_message.csv")

L = []
add = L.append

add("# Ticket-Easy: evaluation and cost report")
add("")
add("16 to 17 October 2026. Written for a sales and technical audience that wants to see what is measured, how, and what is still open. "
    "Every figure comes from a file under `Docs/output/eval_runs/`; the footnotes at the end of each section name the files. "
    "Latency is always \"measured on the Groq free tier\". Nothing in this report was paid for: every model call used a free tier.")
add("")
add("## What the product does, and what we can show")
add("")
add("Ticket-Easy answers customers in English, Egyptian Arabic, mixed Arabic-English and Arabizi. Three services do the work: "
    "the **conversation service** (understands the message and decides what to do), the **knowledge service** (finds the company policy "
    "and enforces its rules) and the **tool service** (connects the shop's own systems as safe tools). The report leads with what a business "
    "must trust: answers that cite the policy, rules that are enforced by code, no unsafe actions, Arabic and English coverage, and search quality that is measured.")
add("")

# ------------------------------------------------------------------ Part 1
add("# Part 1. Evaluation pipeline and results")
add("")
add("## 1.1 How the evaluation works")
add("")
add("```mermaid\nflowchart LR\n  G[Gold data<br/>240 conversations, 155 labelled messages,<br/>83 scenarios, retrieval benchmarks, 44 guardrail cases] --> H[Harness<br/>eval, eval-nlu, eval-retrieval,<br/>eval-guardrails, scenario report]\n"
    "  H --> R[Reports<br/>JSON and Markdown per run]\n  B[Committed baselines<br/>phase0, NLU, conversation] --> C[eval-compare<br/>run against run]\n  R --> C\n"
    "  W[Weekly CI run<br/>Mondays 05:00 UTC] --> R\n  R --> D[Dashboard metrics<br/>and CI artifact]\n```")
add("")
add("The gold data are fixed files in the repository, so every run answers the same questions. Reports are saved as JSON and Markdown, "
    "and a run can be compared with a committed baseline or with another run. A weekly CI workflow runs the conversation evaluation and keeps the "
    "report as an artifact; the workflow exists, but this integration branch had not been run in CI before this refresh.[^1]")
add("")
add("## 1.2 Headline results")
add("")
a_ = {r["measure"]: r for r in headline}
add("Trust first. These are the results of the agent-mode run on English, Arabic and mixed conversations (180 conversations, 213 messages) with the real knowledge service.")
add("")
hl = [
    {"item": "Unsafe or unapproved actions in the 240-set run", "result": "0", "how_measured": "wrong_or_unapproved_actions; the run itself saw 0 write requests, so this row alone is not proof (see 1.4)"},
    {"item": "Unsafe actions in the 83 scripted scenarios", "result": "0 failing, 83 of 83 pass", "how_measured": "scenarios include real refunds, cancellations, vouchers and exchanges with confirmation"},
    {"item": "Facts in replies that nothing backs", "result": a_["unbacked facts"]["agent_mode"], "how_measured": "every sent reply re-checked for numbers, dates, ids and links"},
    {"item": "Guardrail cases", "result": "44 of 44, 0 blocked actions executed", "how_measured": "knowledge service guardrail suite"},
    {"item": "Search quality, held-out test split (recall@5)", "result": f"shop_001 {ret['shop_001']['test']['recall_at_5_pct']}%, noon_eg {ret['noon_eg']['test']['recall_at_5_pct']}%, telco_001 {ret['telco_001']['test']['recall_at_5_pct']}%", "how_measured": "70, 129 and 204 labelled questions, split into dev and test"},
    {"item": "Understanding the customer (intent), English / Arabic / mixed", "result": " / ".join(f"{[r for r in per_style if r['style'] == s and r['run'] == 'agent mode'][0]['intent_pct']}%" for s in ("en", "ar", "mixed")), "how_measured": "agent mode, 60 conversations per style"},
    {"item": "Replies that cite the right policy passage", "result": a_["citation accuracy"]["agent_mode"].split(" (")[0] + "%", "how_measured": "policy-question turns only (75 turns); rules only reach " + a_["citation accuracy"]["rules_only_before_fix"].split(" (")[0] + "%"},
    {"item": "Reply in the customer's language", "result": a_["locale match"]["agent_mode"].split(" (")[0] + "%", "how_measured": "all turns"},
]
add(md(["item", "result", "how_measured"], hl, ["Item", "Result", "How it is measured"]))
add("")
add("Arabizi is measured too and reported in the appendix.[^2]")
add("")
add("## 1.3 Knowledge service")
add("")
add("**Search quality.** The knowledge service finds the right passage in hybrid mode (local `bge-m3` embeddings plus keywords). "
    "Test numbers are on questions that were never used to tune it; dev numbers are shown for reference only.")
add("")
kt = []
for tenant in ("shop_001", "noon_eg", "telco_001"):
    for split in ("test", "dev"):
        d = ret[tenant][split]
        kt.append({"tenant": tenant, "split": split, "questions": d["questions"], "recall": d["recall_at_5_pct"], "precision": d["no_answer_precision_pct"], "nrecall": d["no_answer_recall_pct"], "ar": d["per_style_hits"].get("ar", ""), "en": d["per_style_hits"].get("en", ""), "ab": d["per_style_hits"].get("arabizi", "")})
add(md(["tenant", "split", "questions", "recall", "precision", "nrecall", "ar", "en", "ab"], kt,
       ["Business", "Split", "Questions", "Recall@5 (%)", "No-answer precision (%)", "No-answer recall (%)", "Arabic hits", "English hits", "Arabizi hits"]))
add("")
add("The weak spot, stated plainly: for questions the documents cannot answer, the service sometimes still returns passages that share words with the question "
    "(no-answer recall 33 to 78%). The conversation service therefore checks that a passage really answers the question before it quotes it.")
add("")
add(f"**Guardrails.** {summary['guardrails']['passed']} of {summary['guardrails']['total']} guardrail cases pass and {summary['guardrails']['blocked_actions_executed']} blocked actions were executed. "
    f"**Noon scenarios:** expected-section recall@5 {summary['noon_scenarios']['section_recall_at_5_pct']}% ({summary['noon_scenarios']['sections']} sections); "
    f"{summary['noon_scenarios']['no_evidence_returned_empty']} no-evidence cases returned an explicit \"no evidence\".[^3]")
add("")
add("## 1.4 Conversation service: rules alone against agent mode")
add("")
add("\"Rules only\" is the conversation service with no AI model (the starting point). \"Agent mode\" adds the AI reader, orchestrator, policy answer, writer and reviewer, "
    "and every move is still checked by code. Both use the real knowledge service; combined English, Arabic and mixed.")
add("")
add(md(["measure", "unit", "rules_only_before_fix", "rules_only_after_fix", "agent_mode"], headline,
       ["Measure", "Unit", "Rules only", "Rules only, after one rule fix", "Agent mode"]))
add("")
add("What this shows, without spin:")
add("")
add("- Agent mode understands the customer far better (intent 70% to 94%) and cites the right policy passage much more often (44% to 71%); decisions improve by about 7 points.")
add("- Agent mode **hands conversations to a person more often than the rules do**: handoff precision is 65% against 82%, with 9 needless handoffs against 4. "
    "The one rule fix (a question about support opening hours was being read as a request for a person) lifted the rules-only precision to 100%; the agent runs were made before that fix.")
add("- The agent is slower and uses AI calls: about 4.9 calls per message, median reply 6 to 7 seconds and 95th percentile about 31 seconds on the Groq free tier, "
    "which waits when the free plan's per-minute limit is reached. The rules answer in under a second.")
add("")
add("**Per language style.**")
add("")
ps = [r for r in per_style if r["appendix"] == "no"]
add(md(["style", "run", "conversations", "intent_pct", "decision_pct", "citation_pct", "locale_pct", "handoff_precision_pct", "handoff_recall_pct", "time_p50_s", "time_p95_s"], ps,
       ["Style", "Run", "Conv.", "Intent %", "Decision %", "Citation %", "Language %", "Handoff precision %", "Handoff recall %", "Median s", "p95 s"]))
add("")
add("**Safety, run by run.** The safety counters must stay at zero; here is every run.")
add("")
add(md(["run", "conversations", "wrong_or_unapproved_actions", "writes_seen", "unbacked_facts", "note"], safety,
       ["Run", "Conversations", "Wrong or unapproved actions", "Writes seen", "Unbacked facts", "Note"]))
add("")
add("\"Keyword stand-in search\" is the simple keyword search the tests used before the real knowledge service was connected.")
add("")
add("In the 240-conversation set almost every conversation is one question and one answer (284 messages in 240 conversations), so no write was attempted and \"0 of 0 writes\" says little by itself. "
    "The evidence for write safety is the 83 scripted scenarios (real confirmations and executions) and a set of 24 longer conversations of 5 to 7 messages: "
    "there, cancellations, vouchers and exchanges were carried out after a confirmation, no order data leaked after a failed identity check, and no return was executed after the customer changed topic.[^4]")
add("")
add("## 1.5 Tool service")
add("")
add("The tool service is judged by what it lets a business publish, not by chat accuracy. Its latest recorded end-to-end run (9 October 2026) went from an 81-operation shop API "
    "to tested, reviewable tools without help, with the search for operations widening automatically (3 rounds in the hardest request), enforcement reviewed by a person, and "
    "sandbox tests gating publication; its suite then had 595 tests and now has 718 passing (1 skipped). In this refresh it also generated a tool from an example e-commerce API on the Groq free tier "
    "in 4 seconds with one model call (5,375 tokens).[^5]")
add("")
add("## 1.6 How results are shared")
add("")
add("- Fixed gold data and committed baselines, so any run is comparable with an earlier one (`eval-compare`).")
add("- Every run writes a JSON and a Markdown report; the weekly CI run keeps the report as an artifact.")
add("- The dashboard shows live metrics (automation rate, escalations by reason, time to first reply and others) per business; the definitions are written down.[^6]")
add("")
add("## 1.7 Honest gaps against the targets")
add("")
tg = [
    {"measure": "Request right (intent)", "target": "90%", "now": per_style and a_["intent accuracy"]["agent_mode"].split(" (")[0] + "%", "status": "target met"},
    {"measure": "Outcome right (decision)", "target": "92%", "now": a_["decision accuracy"]["agent_mode"].split(" (")[0] + "%", "status": "below target"},
    {"measure": "Correct source (citation)", "target": "95%", "now": a_["citation accuracy"]["agent_mode"].split(" (")[0] + "%", "status": "below target"},
    {"measure": "Reply language", "target": "100%", "now": a_["locale match"]["agent_mode"].split(" (")[0] + "%", "status": "almost"},
]
add(md(["measure", "target", "now", "status"], tg, ["Measure", "Target", "Agent mode now", "Status"]))
add("")
add("The gap is concentrated in policy questions. On the policy topics the agent often understands the question and then asks a clarifying question or hands over instead of searching and quoting; "
    "the fixed flow often answers these correctly. This is the roadmap: a better orchestrator model (Part 2 shows the first evidence) and a rule that policy questions are searched before anything else is asked. "
    "A second open item: in agent mode, a refund request on an old order can loop on \"what amount?\" instead of explaining the 14-day rule.[^7]")
add("")
add("---")
add("")
add("[^1]: `.github/workflows/eval.yml`, `team_b/eval/baseline_phase0.json`, `team_b/eval/nlu_baseline.json`, `Docs/output/eval_runs/E0_health_check.md`.")
add("[^2]: `Docs/output/eval_runs/tables/per_style.csv`.")
add("[^3]: `Docs/output/eval_runs/knowledge/summary.json` and the command outputs in the same folder; recorded at commit `" + summary["commit"] + "`.")
add("[^4]: `tables/headline.csv`, `tables/per_style.csv`, `tables/safety.csv`, `conversation/compare_agent_vs_rules_live.txt`, `reports/eval_presentation_agent_live_*_2026-10-17.json`, `reports/eval_live_rules_*.json`, `diagnosis/longlive_*.jsonl`, `conversation/scenarios.txt`, `conversation/README_E4_followup.md`.")
add("[^5]: `team_c/E2E_FINAL_REPORT.md`, `knowledge/onboarding_tools_live_smoke.txt`, `usage/onboarding_tools.jsonl`, `conversation/../E0_health_check.md`.")
add("[^6]: `team_b/docs/metrics.md`, `.github/workflows/eval.yml`.")
add("[^7]: `team_b/docs/launch_readiness.md` (targets), `tables/headline.csv`, `diagnosis/live_en_policy.jsonl`, `conversation/README_E4_followup.md`.")
add("")

# ------------------------------------------------------------------ Part 2
add("# Part 2. Models and cost")
add("")
add("## 2.1 The recommended bare-minimum model stack")
add("")
add("The aim: the fewest models that still cover every job. **Two chat model slots (one small for high-volume work, one strong for decisions) plus one local embedding model.** "
    "Today we also keep Qwen 27B for knowledge and writing, because it words Arabic and Egyptian Arabic replies well (a preview model on Groq that can be withdrawn, so gpt-oss-20b is its automatic backup).")
add("")
slots = [
    {"job": "reader (understand the message)", "runs": "every message", "slot": "small"}, {"job": "writer (word the reply)", "runs": "every message", "slot": "small"},
    {"job": "summary (handoff briefing)", "runs": "handoffs only", "slot": "small"}, {"job": "knowledge (answer from policy passages)", "runs": "policy questions", "slot": "strong"},
    {"job": "orchestrator (plan, pick moves)", "runs": "every message in agent mode", "slot": "strong"}, {"job": "reviewer (stricter-only check)", "runs": "every written reply and write", "slot": "strong"},
    {"job": "tool generation, rule extraction", "runs": "once per business", "slot": "strong"}, {"job": "search embeddings", "runs": "every search", "slot": "bge-m3, local ($0 API)"},
    {"job": "Egyptian-Arabic writer (Nile-Chat), judge", "runs": "optional / tests only", "slot": "dropped from the minimum stack"},
]
add(md(["job", "runs", "slot"], slots, ["Job", "How often", "Model slot"]))
add("")
add("**What the minimum stack costs per 1,000 customer messages** (agent mode, the tokens measured in this refresh; paid list prices; the actual spend so far is $0):")
add("")
stack_rows = [{"option": "Today: three Groq models (measured tokens, priced at list)", "usd1k": fnum(next(r for r in hyp if r["stack"].startswith("bare-minimum, today"))["usd_per_1000_messages"]),
               "m10": next(r for r in hyp if r["stack"].startswith("bare-minimum, today"))["usd_per_month_at_10k_messages"], "m100": next(r for r in hyp if r["stack"].startswith("bare-minimum, today"))["usd_per_month_at_100k_messages"], "status": "tokens measured"}]
for key, label in (("bare-minimum, best value (recommended", "Best value: GPT-6 Luna + Gemini 3.8 Flash (introductory price to 31 Dec 2026)"), ("bare-minimum, best value: same, Gemini", "Best value after 1 Jan 2027"), ("bare-minimum, lowest cost", "Lowest cost: Mistral Small 4 + Mistral Large 3 (prices indicative)")):
    r = next(x for x in hyp if x["stack"].startswith(key))
    stack_rows.append({"option": label, "usd1k": fnum(r["usd_per_1000_messages"]), "m10": r["usd_per_month_at_10k_messages"], "m100": r["usd_per_month_at_100k_messages"], "status": "hypothetical, not tested"})
for tier in ("L1 starter", "L2 mirror", "L3 server", "L3 rented"):
    lo = next(r for r in local if r["option"].startswith(tier) and r["case"] == "low")
    hi = next(r for r in local if r["option"].startswith(tier) and r["case"] == "high")
    stack_rows.append({"option": f"Fully local, {lo['option']}", "usd1k": f"{lo['usd_per_1000_messages_at_10k_a_month_estimate']} to {hi['usd_per_1000_messages_at_10k_a_month_estimate']} at 10k a month",
                       "m10": f"{lo['monthly_cost_usd_estimate']} to {hi['monthly_cost_usd_estimate']}", "m100": f"{lo['monthly_cost_usd_estimate']} to {hi['monthly_cost_usd_estimate']} (one box, throughput not measured)", "status": "estimate: hardware and power"})
add(md(["option", "usd1k", "m10", "m100", "status"], stack_rows, ["Option", "USD per 1,000 messages", "USD per month at 10,000 messages", "USD per month at 100,000 messages", "Status"]))
add("")
add("Local rows are **estimates** (hardware price / 36 months + power x 730 hours x $0.10 per kWh, or 730 rented GPU hours); the API cost is $0 but throughput was not measured, so one box is only assumed to keep up at 10,000 messages a month.[^8]")
add("")
add("## 2.2 What was measured about the models")
add("")
add("**Understanding the message (the reader job), 155 labelled messages, same prompt, temperature 0, no backup model.** Only this job's model was changed.")
add("")
cmp_show = [r for r in cmp_]
for r in cmp_show:
    r["lat"] = (f"{r['latency_p50_ms']} / {r['latency_p95_ms']}" if r.get("latency_p50_ms") else "")
    r["tok"] = (f"{r['avg_input_tokens_per_call']} / {r['avg_output_tokens_per_call']}" if r.get("avg_input_tokens_per_call") else "")
add(md(["model", "intent_accuracy_pct", "intent_accuracy_en_pct", "intent_accuracy_ar_pct", "intent_accuracy_mixed_pct", "intent_accuracy_arabizi_pct", "lat", "tok"], cmp_show,
       ["Model", "Intent accuracy %", "English %", "Arabic %", "Mixed %", "Arabizi %", "Latency p50 / p95 ms (Groq free tier)", "Tokens per call in / out"]))
add("")
add("Read this carefully: it tests **one job** (the reader) on **short, hand-written messages**; the three AI readers are within 3 messages of 155 of each other, so they are effectively tied, and all beat the rules by 8 to 10 points. "
    "It says nothing about the other jobs. The gpt-oss-120b figure includes 67 messages that had to be re-run on another free account after its daily token limit was reached (the rate-limited calls in the file are from that first pass).[^9]")
add("")
add("**The orchestrator (the job that plans each step), English policy and refund conversations: 25 conversations, 27 messages, real knowledge service.** Only the orchestrator model changed.")
add("")
rep = [dict(r) for r in orch_rep]
add(md(["run", "turns", "fallback_turns", "citation_right_all_turns", "citation_right_agent_turns_only", "turns_with_a_failed_check"], rep,
       ["Run", "Messages", "Fallbacks to the fixed flow", "Citations right, all messages", "Citations right, agent's own messages only", "Messages with a failed check"]))
add("")
add("qwen3.8-27b as orchestrator was repeated three times; the two runs with few fallbacks agree (22 and 21 of 25 citations right) and the run with 18 fallbacks (rate limits) is shown but not relied on. "
    "gpt-oss-120b, the orchestrator used today, scored 17 of 25 in one run, and gpt-oss-20b 19 of 25. That is a hint that qwen is the better orchestrator for English policy questions, "
    "**not yet a verdict**: it is one language, 25 policy messages, and the 120B repeats are still pending its daily token quota. The original choice of 120B was not based on a head-to-head test.")
add("")
add("Arabic and mixed (same design):")
add("")
ol = [dict(r) for r in orch_lang]
add(md(["language", "orchestrator_model", "citation_right_all_turns", "citation_right_agent_turns_only", "fallback_turns", "turns_with_a_failed_check", "status"], ol,
       ["Language", "Orchestrator", "Citations right, all", "Citations right, agent's own messages", "Fallbacks", "Failed checks", "Status"]))
add("")
add("The qwen runs in Arabic and mixed are **not conclusive**: most messages fell back to the fixed flow because qwen's free daily quota ran out. gpt-oss-20b ran cleanly and is weaker in Arabic and mixed than in English. The 120B runs for these languages are pending.[^10]")
add("")
add("**The other jobs, by replaying the same recorded inputs to other models.** Agreement is with the baseline model, not with a ground truth; the writer's wording needs a person (a blinded grading sheet is provided).")
add("")
for lang, title in (("en", "English"), ("ar", "Arabic"), ("mixed", "Mixed")):
    sub = [r for r in replay if r["language"] == lang]
    if not sub:
        add(f"*{title}: pending.*")
        add("")
        continue
    for r in sub:
        r["role"] = f"{r['job']}" + (" (baseline)" if r["is_baseline"] == "True" else "")
        r["agree"] = r.get("agrees_with_baseline_pct") or r.get("answerable_agrees_pct") or ""
        r["loose"] = r.get("looser_than_baseline", "")
    add(f"*{title}*")
    add("")
    add(md(["role", "model", "inputs", "answered", "failed", "grounded_pct", "agree", "loose", "same_citations_pct", "avg_tokens_per_call"], sub,
           ["Job", "Model", "Inputs", "Answered", "Failed (e.g. rate limit)", "Grounded %", "Agrees with baseline %", "Looser than baseline", "Same passages %", "Tokens per call"]))
    add("")
loose = [(r["language"], r["model"], int(r["looser_than_baseline"])) for r in replay if r.get("job") == "reviewer" and r.get("looser_than_baseline") not in (None, "") and int(r["looser_than_baseline"]) > 0]
add("**Reviewer, the safety-relevant job:** " + ("no alternative reviewer was ever more lenient than the baseline reviewer (the safe direction)." if not loose else
    "an alternative reviewer was more lenient than the baseline on " + "; ".join(f"{n} input(s) for {m} in {lg}" for lg, m, n in loose) + ", so a cheaper reviewer is not yet shown to be as strict; it needs a larger test before it is used."))
add("Failed replays are rate limits (qwen's free daily quota ran out), not wrong answers; a row with many failures is not a result.[^11]")
add("")
add("## 2.3 The models, measured against hypothetical")
add("")
mt = [dict(r) for r in models_t if r["status"] != "hypothetical, not tested"]
add(md(["model", "deploy", "role", "status", "latency", "quality", "usd_per_1m_in_out"], mt, ["Model", "Deploy", "Role", "Status", "Latency", "Quality fit", "USD per 1M tokens in / out"]))
add("")
add("Models that were not run in this refresh appear only in the cost tables below, marked **hypothetical, not tested**; their latency and quality are \"not tested\". "
    "We do not recommend a top-tier model as the default volume stack.")
add("")
add("## 2.4 What it would cost with other models (hypothetical, not tested)")
add("")
add("The same measured tokens per job, priced for other vendors' models. **None of these models was run.** Two caveats: other models count the same text as more or fewer tokens, "
    "and their quality and speed are unknown until they are actually run.")
add("")
add(md(["stack", "usd_per_1000_messages", "usd_per_month_at_10k_messages", "usd_per_month_at_100k_messages", "status", "quality"], hyp,
       ["Stack", "USD per 1,000 messages", "USD per month at 10k", "USD per month at 100k", "Status", "Quality / speed"]))
add("")
add("## 2.5 Tokens measured")
add("")
add("Every figure below is a count the provider returned, written to a usage log by the three services in one shared format. Usage before the log existed was not recorded; the totals are for the runs of this refresh, with their dates (UTC).")
add("")
svc = {}
for r in usage_totals:
    s = svc.setdefault(r["service"], {"service": r["service"], "calls": 0, "tokens": 0, "first": "9", "last": "0"})
    s["calls"] += int(r["calls"]); s["tokens"] += int(r["total_tokens"]); s["first"] = min(s["first"], r["first_call_utc"]); s["last"] = max(s["last"], r["last_call_utc"])
names = {"conversation": "conversation service", "knowledge": "knowledge service", "tools": "tool service"}
tot = [{"service": names.get(k, k), "calls": f"{v['calls']:,}", "tokens": f"{v['tokens']:,}", "period": f"{v['first']} to {v['last']}"} for k, v in svc.items()]
tot.append({"service": "all services", "calls": f"{sum(v['calls'] for v in svc.values()):,}", "tokens": f"{sum(v['tokens'] for v in svc.values()):,}", "period": ""})
add(md(["service", "calls", "tokens", "period"], tot, ["Service", "Logged calls (all attempts)", "Tokens measured", "First to last call (UTC)"]))
add("")
add("**Tokens per conversation, by job (agent mode, English + Arabic + mixed, 180 conversations, 213 messages).**")
add("")
tj = [dict(r) for r in tokens if r["scope"] == "en+ar+mixed" and r["job"] != "judge"]
add(md(["job", "calls_per_message", "total_tokens_per_message", "total_tokens_per_conversation"], tj, ["Job", "AI calls per message", "Tokens per message", "Tokens per conversation"]))
add("")
add("**All components together, per conversation.**")
add("")
add(md(["component", "tokens_per_conversation", "status", "basis"], allsvc, ["Component", "Tokens per conversation", "Status", "Basis"]))
add("")
add("The conversation tests use the real knowledge service for search, but its AI risk second opinion was switched off during them; that row is therefore an estimate of the cost if it is switched on.[^12]")
add("")
add("## 2.6 Cost tables")
add("")
add("Actual spend so far: **$0**. Every other number is what the same measured tokens would cost at paid list prices.")
add("")
add(md(["stack", "total_usd_per_1000_messages_at_list_price", "usd_per_month_at_10k_messages", "usd_per_month_at_100k_messages", "pilot_50k_messages_plus_2_onboardings_usd", "status"], cost,
       ["Stack", "USD per 1,000 messages", "USD per month at 10k", "USD per month at 100k", "Pilot: 50,000 messages + 2 onboardings (USD)", "Status"]))
add("")
add("The conversation service accounts for all of the AI cost in these rows; the knowledge service's search runs on a local model ($0 API) and its risk second opinion runs on a free model. "
    "Qwen as the orchestrator looks expensive at Groq's list price for qwen ($0.80 in / $4.00 out per million tokens, a preview model): about 2.6 times today's mix, because it also uses more tokens per message. "
    "At OpenRouter's lower price for the same model family the picture would differ, so the choice of orchestrator should be made on quality first and then priced on the provider actually used.")
add("")
add("**One business onboarding, measured.**")
add("")
add(md(["step", "total_tokens", "tokens_reported_or_estimated"], onboard, ["Step", "Tokens", "Counts"]))
add("")
add(md(["step", "model", "input_tokens", "output_tokens", "list_price_usd", "status"], onb_cost, ["Step", "Model", "Input tokens", "Output tokens", "List price (USD)", "Status"]))
add("")
add("Onboarding costs cents at list price and was measured on one example API with only 2 operations; a real business with many operations uses more tool-generation tokens. "
    "The quality of the generated tool and of the 20 candidate rules was not judged in this refresh.[^13]")
add("")
add("---")
add("")
add("[^8]: `tables/hypothetical.csv`, `tables/local_estimates.csv`, `tables/cost_notes.md`, `tables/prices.csv`.")
add("[^9]: `models/comparison.csv`, `models/nlu_*.jsonl`, `usage/nlu_*.jsonl`, `models/sample40.jsonl` (seed 20261017, used for the risk checks).")
add("[^10]: `models/orchestrator_comparison.csv`, `models/orchestrator_repeats.csv`, `models/orchestrator_comparison_ar_mixed.csv`, `diagnosis/orch_*.jsonl`, `usage/orch_*.jsonl`.")
add("[^11]: `models/replay_comparison.csv`, `models/writer_replies_to_grade_*.csv`, `usage/replay_*.jsonl`.")
add("[^12]: `tables/usage_totals.csv`, `tables/tokens.csv`, `tables/tokens_all_services.csv`, `tables/knowledge_per_message.csv`, `usage/*.jsonl`, `USAGE_LOG_FORMAT.md`.")
add("[^13]: `tables/onboarding.csv`, `tables/onboarding_measured.csv`, `tables/onboarding_cost.csv`, `knowledge/onboarding_rules_result.json`.")
add("")

# ------------------------------------------------------------------ Appendix
add("# Appendix")
add("")
add("## A. Arabizi")
add("")
ab = [r for r in per_style if r["style"] == "arabizi"]
add("Arabizi (Egyptian Arabic written in Latin letters) is measured so numbers stay comparable, but it is not part of the headline figures. "
    "The rules alone reach 40% intent on all 60 Arabizi conversations. The agent-mode figure below is a **sample of 15 conversations** run on the earlier keyword stand-in; "
    "the same sample on the real knowledge service did not complete cleanly (most turns fell back after rate limits), so it is not reported. Dialect wording by a dedicated writer is optional.")
add("")
add(md(["run", "conversations", "intent_pct", "decision_pct", "citation_pct", "locale_pct", "time_p50_s", "appendix"], ab, ["Run", "Conversations", "Intent %", "Decision %", "Citation %", "Language %", "Median s", "Note"]))
add("")
add("## B. Formulas")
add("")
add("- Cost per 1,000 messages = sum over jobs of (input tokens per message x input price + output tokens per message x output price) / 1,000,000 x 1,000; embeddings run locally ($0 API).")
add("- Monthly = cost per 1,000 messages x 10 (10,000 messages) or x 100 (100,000). Pilot = 50 x cost per 1,000 + 2 onboardings.")
add("- Local monthly cost (estimate) = hardware price / 36 + kW x 730 x $0.10 per kWh; rented = 730 hours x hourly rate. Inputs are in `tables/local_estimates.csv`.")
add("- Merged results are rebuilt from exact counts (rate x the fixed denominators of the gold files), never by averaging percentages (`merge_runs.py`).")
add("")
add("## C. Prices, with sources and dates")
add("")
add(md(["model", "input_usd_per_1m", "output_usd_per_1m", "source", "date", "how_obtained"], prices, ["Model", "Input USD per 1M", "Output USD per 1M", "Source", "Date", "How obtained"]))
add("")
add("## D. Method note and what is still open")
add("")
add("- The agent-mode runs were made before a rule fix (support-hours questions) and three corrections to the evaluation's own checks; the rules-only table is shown both before and after the rule fix. The corrections only removed false alarms (a greeting saying \"today\", a reference number the shop really issued, a passage the real service really returned).")
add("- The conversation tests ran on one business (a fashion shop). A second business, real customer messages and a person-graded sample of replies are not yet included.")
add("- Pending because of the free daily token limit of gpt-oss-120b: its orchestrator repeats and its Arabic and mixed runs; the qwen Arabic and mixed orchestrator runs need a clean re-run.")
add("- Not yet compared across models: tool generation and rule extraction (only one model each was measured).")
add("- The recorded Arabizi sample on the real knowledge service is incomplete (rate limits).")
OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
print("wrote", OUT, len("\n".join(L)), "characters")
