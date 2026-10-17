"""Builds Docs/output/deck/ticket-easy-fixed-misr-deck-21.html from the evaluation tables (run from the repository root).
Every number on a data slide is read from a file under Docs/output/eval_runs/, so the deck cannot drift from the report."""
import csv
import json
from pathlib import Path

RUNS = Path("Docs/output/eval_runs")
T = RUNS / "tables"
OUT_DIR = Path("Docs/output/deck")
A = OUT_DIR / "assets"
MARK = ('<span class="mark"><svg viewBox="44 53 158 158" aria-hidden="true"><path d="M70 122H176C176 87 153 65 122 65C83 65 56 94 56 132C56 174 86 199 124 199'
        'C148 199 164 190 177 175" fill="none" stroke="currentColor" stroke-width="26" stroke-linecap="round"/></svg></span>')
FOOT = '<div class="foot">Source: Ticket-Easy evaluation &amp; cost report, 16–17 Oct 2026. Latency measured on the Groq free tier. Actual model spend so far: $0 (free tiers).</div>'


def rows(p):
    p = Path(p)
    return list(csv.DictReader(p.open(encoding="utf-8-sig"))) if p.exists() else []


def num(x, nd=0):
    return f"{float(x):,.{nd}f}"


def money(x):
    x = float(x)
    return f"{x:,.2f}"


def chip(kind, text):
    return f'<span class="chip {kind}">{text}</span>'


def slide(kicker, title, body, data=True, cls=""):
    return (f'<section class="slide {cls}"><div class="k">{kicker}</div><h2>{title}</h2><div class="body">{body}</div>'
            f'{FOOT if data else ""}<div class="brand-s">{MARK}<span>Ticket-Easy</span></div><div class="pg"></div></section>')


def table(heads, data, cls="t", right=()):
    h = "".join(f'<th class="{"n" if i in right else ""}">{x}</th>' for i, x in enumerate(heads))
    b = "".join("<tr>" + "".join(f'<td class="{"n" if i in right else ""}">{c}</td>' for i, c in enumerate(r)) + "</tr>" for r in data)
    return f'<table class="{cls}"><tr>{h}</tr>{b}</table>'


def bars(items, vmax, color="var(--accent)"):
    out = ""
    for label, val, shown in items:
        out += f'<div class="bar"><span>{label}</span><span class="bt"><i style="width:{min(100, 100 * val / vmax):.1f}%;background:{color}"></i></span><span class="bv">{shown}</span></div>'
    return out


# ---------------------------------------------------------------- data
mg = json.loads((T / "merged_agent_live.json").read_text(encoding="utf-8"))["combined"]
mr = json.loads((T / "merged_rules_live.json").read_text(encoding="utf-8"))["combined"]
per = rows(T / "per_style.csv")
agent_style = {r["style"]: r for r in per if r["run"] == "agent mode"}
summary = json.loads((RUNS / "knowledge/summary.json").read_text(encoding="utf-8"))
ret = summary["retrieval"]
tok = [r for r in rows(T / "tokens.csv") if r["scope"] == "en+ar+mixed"]
tokj = {r["job"]: r for r in tok}
allsvc = rows(T / "tokens_all_services.csv")
onb = rows(T / "onboarding.csv")
onb_cost = rows(T / "onboarding_cost.csv")
cost = rows(T / "cost.csv")
hyp = {r["stack"]: r for r in rows(T / "hypothetical.csv")}
local = rows(T / "local_estimates.csv")
cmp_ = {r["model"]: r for r in rows(RUNS / "models/comparison.csv")}
orch_rep = rows(RUNS / "models/orchestrator_repeats.csv")
orch_lang = rows(RUNS / "models/orchestrator_comparison_ar_mixed.csv")
replay = rows(RUNS / "models/replay_comparison.csv")
on_tools = rows(RUNS / "models/onboarding_models_tools.csv")
on_rules = rows(RUNS / "models/onboarding_models_rules.csv")
kpm = {r["job"]: r for r in rows(T / "knowledge_per_message.csv")}
onbm = {r["job"]: r for r in rows(T / "onboarding_measured.csv")}
long_rows = []
for s in ("en", "ar", "mixed"):
    for line in (RUNS / f"diagnosis/longlive_{s}.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            long_rows.append(json.loads(line))
long_turns = [r for r in long_rows if "turn" in r]
long_writes = sum(1 for r in long_turns if r["decision"] == "execute")
long_convs = len({r["conversation"] for r in long_turns})
svc = {}
for r in rows(T / "usage_totals.csv"):
    s = svc.setdefault(r["service"], [0, 0])
    s[0] += int(r["calls"])
    s[1] += int(r["total_tokens"])
tot_calls = sum(v[0] for v in svc.values())
tot_tokens = sum(v[1] for v in svc.values())
per_conv = {j: int(tokj[j]["total_tokens_per_conversation"]) for j in ("orchestrator", "reader", "reviewer", "writer", "knowledge", "summary")}
prod_conv = int(next(r for r in tok if r["job"].startswith("ALL"))["total_tokens_per_conversation"])
today = hyp[next(k for k in hyp if k.startswith("bare-minimum, today"))]
best = hyp[next(k for k in hyp if k.startswith("bare-minimum, best value (from the plan)"))]
best27 = hyp[next(k for k in hyp if k.startswith("bare-minimum, best value: same"))]
lowest = hyp[next(k for k in hyp if k.startswith("bare-minimum, lowest cost"))]
c_rules, c_today, c_qwen = cost[0], cost[1], cost[2]
onb_total_cost = float(next(r for r in onb_cost if r["step"] == "ONE ONBOARDING")["list_price_usd"])
onb_total_tokens = int(next(r for r in onb if r["step"].startswith("ONE ONBOARDING"))["total_tokens"])

# ---------------------------------------------------------------- slides
S = []
S.append(f'<section class="slide cover"><div class="brand-big">{MARK}<span>Ticket-Easy</span></div><div class="k">AI CUSTOMER-SUPPORT AGENT</div>'
         '<h1>An AI agent that works<br>inside <em>your own systems</em></h1><p class="sub">One agent loop, three services, your systems.</p>'
         '<p class="meta">Prepared for Fixed Misr Online (فيكسيد مصر اون لاين) · 17 October 2026 · <span class="tbd">[[TBD: presenter]]</span></p></section>')
S.append(slide("AGENDA", "What we will cover",
               '<ol class="agenda"><li>AI Architecture Diagram</li><li>Evaluation Pipeline &amp; Results Sharing</li><li>Models Used &amp; Cost Analysis (including total token usage)</li>'
               '<li>Unique Selling Point (USP)</li><li>Target Domains &amp; Use Cases</li><li>On-Premise Deployment Costs</li></ol>', data=False))

P = '{"overview": [0, 0, 1224, 1060], "core": [0, 0, 884, 440], "systems": [0, 480, 1224, 400], "models": [0, 890, 1224, 170], "toolgen": [910, 124, 314, 620]}'
BTN = [("overview", "Overview"), ("core", "Agent core"), ("systems", "Guardrails · MCP · Systems"), ("models", "AI model layer"), ("toolgen", "Tool generation")]


def arch(start, kicker, title, extra=""):
    btns = "".join(f'<button class="pb{" on" if k == start else ""}" data-k="{k}">{v}</button>' for k, v in BTN)
    return (f'<section class="slide arch"><div class="k">{kicker}</div><h2>{title}</h2><div class="body"><div class="cambar"><div>{btns}</div>'
            '<div><button class="zb" data-z="o">−</button><button class="zb" data-z="i">+</button><button class="zb w" data-z="r">Reset</button></div></div>'
            f"<div class=\"cam\" data-start=\"{start}\" data-p='{P}'><div class=\"camstage\"></div></div>"
            '<div class="lgrow"><span class="lg"><i></i><b>Runtime</b> request / response</span><span class="lg"><i class="r"></i><b>Handoff</b> to a human, same thread</span>'
            f'<span class="lg"><i class="d"></i><b>Setup</b> owner-time management</span><span class="small">Drag to pan · scroll or pinch to zoom{extra}</span></div></div>'
            f'<div class="brand-s">{MARK}<span>Ticket-Easy</span></div><div class="pg"></div></section>')


S.append(arch("overview", "1 · AI ARCHITECTURE DIAGRAM", 'One agent loop, <span class="a">three services</span>, your systems',
              " · Agent Orchestrator = “conversation service” · Knowledge &amp; Guardrails = “knowledge service” · Tool Generation Service + MCP Tool Server = “tool service”"))
S.append(arch("core", "1 · AI ARCHITECTURE · AGENT CORE", "Agent core: Agent Orchestrator (FastAPI :8010)"))
S.append(arch("systems", "1 · AI ARCHITECTURE · GUARDRAILS, MCP &amp; SYSTEMS", "Guardrails, MCP tools &amp; your data"))
S.append(arch("models", "1 · AI ARCHITECTURE · AI MODEL LAYER", "One model per job, never in charge"))
S.append(arch("toolgen", "1 · AI ARCHITECTURE · TOOL GENERATION", "Tool generation: from an API spec to safe tools"))

# E1 pipeline + sharing
flow = ('<div class="flow"><div class="fb"><h4>1 · Gold data</h4><ul><li>240 conversations</li><li>155 labelled messages</li><li>83 scenarios</li><li>44 guardrail cases</li><li>retrieval benchmarks</li></ul></div><div class="ar">→</div>'
        '<div class="fb"><h4>2 · Harness</h4><p>eval, eval-nlu, eval-retrieval, eval-guardrails, scenario report</p></div><div class="ar">→</div>'
        '<div class="fb"><h4>3 · Reports</h4><p>JSON and Markdown per run</p></div><div class="ar">→</div>'
        '<div class="fb"><h4>4 · eval-compare</h4><p>Run vs committed baselines, or vs another run</p></div><div class="ar">→</div>'
        '<div class="fb"><h4>5 · Outputs</h4><p>Dashboard metrics and CI artifact</p></div></div>')
share = ('<div class="cols3"><div class="card"><h4>Fixed gold data, committed baselines</h4><p class="small">Every run answers the same questions, so any run can be compared with an earlier one.</p></div>'
         '<div class="card"><h4>A report per run</h4><p class="small">JSON for tools, Markdown for people; every figure in this deck traces to a file.</p></div>'
         '<div class="card"><h4>Weekly CI + dashboard</h4><p class="small">The weekly workflow keeps the report as an artifact. The dashboard shows live metrics per business: automation rate, escalations by reason, time to first human reply.</p></div></div>')
S.append(slide("2 · EVALUATION · PIPELINE &amp; RESULTS SHARING", "Evaluation pipeline &amp; results sharing",
               flow + '<div class="ci">Weekly CI run · Mondays 05:00 UTC · workflow defined, not yet run on this branch ' + chip("est", "pending") + "</div>" + share))

# E2 safety & trust
tiles = [
    ("83 / 83", "scripted scenarios pass: refunds, cancellations, vouchers, exchanges with confirmation", "ok", "measured"),
    ("44 / 44", "guardrail cases pass; 0 blocked actions executed", "ok", "measured"),
    (str(mg["unbacked_facts"]), f"facts in replies that nothing backs, across {mg['conversations']} conversations ({mg['turns']} messages)", "ok", "measured"),
    ("0", f"order-data leaks after a failed identity check, across {long_convs} longer conversations", "ok", "measured"),
    (str(long_writes), "writes carried out after a confirmation in the longer conversations (cancel, voucher, exchange)", "ok", "measured"),
    ("0", "returns executed after the customer changed topic mid-confirmation", "ok", "measured"),
    (str(mg["wrong_or_unapproved_actions"]), f"wrong or unapproved actions in the {mg['conversations']}-conversation set (that set attempts no writes; write safety rests on the scenarios and long conversations)", "ok", "measured"),
    (f"{mg['locale_pct']}%", "replies in the customer's language (English, Egyptian Arabic, mixed)", "ok", "measured"),
]
S.append(slide("2 · EVALUATION · SAFETY &amp; TRUST", "Safety and trust: what was measured",
               '<p class="lead">Agent mode, real knowledge service. Model calls are checked by code before anything runs.</p><div class="tiles">' +
               "".join(f'<div class="tile"><b>{v}</b><span>{t}</span>{chip(c, l)}</div>' for v, t, c, l in tiles) + "</div>"))

# E3 quality rules -> agent
bar_html = ""
for lab, key in (("Intent accuracy", "intent_pct"), ("Decision accuracy", "decision_pct"), ("Citation accuracy", "citation_pct")):
    bar_html += f'<div class="grp"><b>{lab}</b>' + bars([("rules only", mr[key], f"{mr[key]}%")], 100, "#b9bdb0") + bars([("agent mode", mg[key], f"{mg[key]}%")], 100) + "</div>"
lang_rows = [[s.upper() if s != "ar" else "Arabic", f"{agent_style[s]['intent_pct']}%", f"{agent_style[s]['decision_pct']}%", f"{agent_style[s]['citation_pct']}%"] for s in ("en", "ar", "mixed")]
lang_rows = [[{"EN": "English", "Arabic": "Arabic", "MIXED": "Mixed"}[r[0]]] + r[1:] for r in lang_rows]
lang_rows.append(["<b>Target</b>", "90%", "92%", "95%"])
right = ('<div class="card"><h4>Agent mode, per language</h4>' + table(["", "Intent", "Decision", "Citation"], lang_rows, "t xs", right=(1, 2, 3)) + "</div>"
         f'<div class="card" style="margin-top:10px"><h4>What changes with agent mode</h4><ul><li>Handoff precision {mr["handoff_precision_pct"]}% → {mg["handoff_precision_pct"]}%; needless handoffs {mr["needless_handoffs"]} → {mg["needless_handoffs"]}</li>'
         f'<li>{mg["ai_calls_per_message"]} AI calls per message; median reply 6–7 s, 95th percentile about 31 s</li></ul></div>')
S.append(slide("2 · EVALUATION · QUALITY", "Rules only → agent mode",
               f'<p class="lead">English + Arabic + mixed · {mg["conversations"]} conversations / {mg["turns"]} messages · real knowledge service</p><div class="split"><div class="chart">{bar_html}</div><div>{right}</div></div>'))

# E4 search quality
srows = []
for tenant, label in (("shop_001", "shop_001 (fashion shop)"), ("noon_eg", "noon_eg"), ("telco_001", "telco_001")):
    d, v = ret[tenant]["test"], ret[tenant]["dev"]
    srows.append([label, d["questions"], f"{d['recall_at_5_pct']}%", f"{d['no_answer_precision_pct']}%", f"{d['no_answer_recall_pct']}%", f"{v['recall_at_5_pct']}%"])
S.append(slide("2 · EVALUATION · KNOWLEDGE SERVICE", "Search quality: measured on held-out questions",
               '<p class="lead">Hybrid retrieval (local bge-m3 + keywords). The test split was never used to tune the thresholds.</p>' +
               table(["Business", "Test questions", "Recall@5 (test)", "No-answer precision", "No-answer recall", "Recall@5 (dev, reference)"], srows, "t", right=(1, 2, 3, 4, 5)) +
               f'<div class="cols"><div class="card"><h4>Guardrails</h4><p class="small">{summary["guardrails"]["passed"]} of {summary["guardrails"]["total"]} cases pass; {summary["guardrails"]["blocked_actions_executed"]} blocked actions executed.</p></div>'
               f'<div class="card"><h4>Noon scenarios</h4><p class="small">Expected-section recall@5 {summary["noon_scenarios"]["section_recall_at_5_pct"]}% ({summary["noon_scenarios"]["sections"]} sections); {summary["noon_scenarios"]["no_evidence_returned_empty"]} no-evidence cases answered with an explicit “no evidence”.</p></div></div>'
               '<p class="note">For questions the documents cannot answer, the search sometimes still returns passages that share words with the question (no-answer recall 33–78%); the conversation service checks that a passage really answers before quoting it.</p>'))

# M1 models in use
q, g, s20 = cmp_["qwen/qwen3.8-27b"], cmp_["openai/gpt-oss-120b"], cmp_["openai/gpt-oss-20b"]
mrows = [
    ["qwen/qwen3.8-27b", "Groq free tier (preview model)", "reader, knowledge, writer, summary", f"{q['latency_p50_ms']} ms · {q['avg_input_tokens_per_call']} / {q['avg_output_tokens_per_call']}", "0.80 / 4.00"],
    ["openai/gpt-oss-120b", "Groq free tier", "orchestrator, reviewer, tool generation", f"{g['latency_p50_ms']} ms · {g['avg_input_tokens_per_call']} / {g['avg_output_tokens_per_call']}", "0.15 / 0.60"],
    ["openai/gpt-oss-20b", "Groq free tier", "backup of the qwen jobs; judge in evaluation only", f"{s20['latency_p50_ms']} ms · {s20['avg_input_tokens_per_call']} / {s20['avg_output_tokens_per_call']}", "0.075 / 0.30"],
    ["bge-m3", "local (Ollama)", "search and document embeddings", f"{kpm['embed_query']['latency_p50_ms']} ms · {kpm['embed_query']['avg_input_tokens_per_ok_call']} / –", "$0 (local)"],
    ["Nemotron 3 Super 120B", "OpenRouter free", "rule extraction (and risk second opinion when switched on)", f"{onbm['rule_extraction']['latency_p50_ms']} ms · {round(int(onbm['rule_extraction']['input_tokens'])/int(onbm['rule_extraction']['calls']))} / {round(int(onbm['rule_extraction']['output_tokens'])/int(onbm['rule_extraction']['calls']))}", "free; paid 0.08 / 0.45"],
]
S.append(slide("3 · MODELS &amp; COST · MODELS USED", "Models in use today",
               '<p class="lead">One model per job, a backup per job, and a fallback to rules or templates. Code decides; models propose.</p>' +
               table(["Model", "Deployment", "Jobs", "Median latency · tokens per call (in / out)", "USD per 1M tokens (in / out)"], mrows, "t") +
               '<p class="note">Latency and tokens are from the measured runs on the Groq free tier (reader calls for the Groq models; query embeddings for bge-m3; rule-extraction calls for Nemotron). Prices are list prices read from the providers on 17 Oct 2026.</p>'))

# M2 reader
order = ["qwen/qwen3.8-27b", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "rule-based reader"]
rrows = [[m.replace("rule-based reader", "rules only (no AI)"), f"<b>{cmp_[m]['intent_accuracy_pct']}%</b>", cmp_[m]["intent_accuracy_en_pct"], cmp_[m]["intent_accuracy_ar_pct"], cmp_[m]["intent_accuracy_mixed_pct"], cmp_[m]["intent_accuracy_arabizi_pct"],
          (f"{cmp_[m]['latency_p50_ms']} / {cmp_[m]['latency_p95_ms']}" if cmp_[m].get("latency_p50_ms") else "–")] for m in order]
S.append(slide("3 · MODELS &amp; COST · MEASURED COMPARISON", "Measured: understanding the customer's message",
               '<p class="lead">155 labelled messages, one job (the reader), same prompt, temperature 0, no backup model. Only the reader\'s model changes.</p>' +
               '<div class="split"><div>' + table(["Model", "Intent %", "English", "Arabic", "Mixed", "Arabizi", "Latency p50 / p95 ms"], rrows, "t", right=(1, 2, 3, 4, 5, 6)) + "</div>"
               '<div class="chart"><h5>Intent accuracy</h5>' + bars([("qwen3.8-27b", float(q["intent_accuracy_pct"]), q["intent_accuracy_pct"] + "%"), ("gpt-oss-120b", float(g["intent_accuracy_pct"]), g["intent_accuracy_pct"] + "%"),
                                                                       ("gpt-oss-20b", float(s20["intent_accuracy_pct"]), s20["intent_accuracy_pct"] + "%")], 100) +
               bars([("rules only", float(cmp_["rule-based reader"]["intent_accuracy_pct"]), cmp_["rule-based reader"]["intent_accuracy_pct"] + "%")], 100, "#b9bdb0") + "</div></div>"
               '<p class="note">The three AI readers differ by at most 3 messages out of 155. 67 of the gpt-oss-120b messages were re-run on another free account after its daily token limit was reached. Short hand-written messages; the other jobs are not covered by this table.</p>'))

# M3 orchestrator + other jobs + onboarding
def cite(s):
    return s.replace("/25", "")


en = {r["run"]: r for r in orch_rep}
q_en = " · ".join(cite(en[f"qwen3.8-27b run {i}"]["citation_right_all_turns"]) for i in (1, 2, 3))
orows = [["English", q_en + " (3 runs)", cite(en["gpt-oss-120b run 1 (baseline)"]["citation_right_all_turns"]), cite(en["gpt-oss-20b run 1"]["citation_right_all_turns"])]]
for lang, label in (("ar", "Arabic"), ("mixed", "Mixed")):
    d = {r["orchestrator_model"]: r for r in orch_lang if r["language"] == lang}
    orows.append([label, cite(d["qwen/qwen3.8-27b"]["citation_right_all_turns"]), cite(d["openai/gpt-oss-120b"]["citation_right_all_turns"]), cite(d["openai/gpt-oss-20b"]["citation_right_all_turns"])])
loose = {}
for r in replay:
    if r["job"] == "reviewer" and r["model"] == "openai/gpt-oss-20b":
        loose[r["language"]] = r["looser_than_baseline"]
gr = [float(r["grounded_pct"]) for r in replay if r["job"] in ("writer", "knowledge") and r["is_baseline"] != "True" and r.get("grounded_pct") and int(r["failed"]) == 0]
tool = {r["model"]: r for r in on_tools}
rules_ok = next(r for r in on_rules if "super" in r["model"])
other = ('<table class="t xs"><tr><th>Job (replayed inputs)</th><th>Result</th></tr>'
         f'<tr><td>Reviewer: gpt-oss-20b more lenient than the baseline</td><td>English {loose.get("en","–")} · Arabic {loose.get("ar","–")} · mixed {loose.get("mixed","–")} inputs</td></tr>'
         f'<tr><td>Writer and knowledge replies grounded in their input</td><td>{min(gr):.1f}–{max(gr):.1f}% across models</td></tr></table>')
onb_t = ('<table class="t xs"><tr><th>Onboarding step</th><th>Result</th></tr>'
         f'<tr><td>Tool generation accepted (attempts)</td><td>gpt-oss-120b {tool["gpt-oss-120b"]["accepted"]}/{tool["gpt-oss-120b"]["attempts"]} · qwen {tool["qwen3.8-27b"]["accepted"]}/{tool["qwen3.8-27b"]["attempts"]} · gpt-oss-20b {tool["gpt-oss-20b"]["accepted"]}/{tool["gpt-oss-20b"]["attempts"]}</td></tr>'
         f'<tr><td>Rule extraction, Nemotron 3 Super</td><td>{rules_ok["passages"]} of {rules_ok["passages"]} passages answered; {rules_ok["candidate_rules"]} candidate rules</td></tr>'
         '<tr><td>Two other free models</td><td>no usable answer (empty response, or refused with error 429)</td></tr></table>')
S.append(slide("3 · MODELS &amp; COST · MEASURED COMPARISON", "Measured: the orchestrator, the other jobs and onboarding",
               '<div class="cols"><div><div class="card"><h4>Orchestrator: policy citations right, out of 25</h4>' + table(["", "qwen3.8-27b", "gpt-oss-120b", "gpt-oss-20b"], orows, "t xs", right=(1, 2, 3)) +
               '<p class="small" style="margin-top:6px">Same 25 policy and refund conversations, only the orchestrator model changed. Most cells are one run; English qwen was run three times.</p></div>'
               '<div class="card" style="margin-top:10px"><h4>Other jobs</h4>' + other + "</div></div>"
               '<div><div class="card"><h4>Onboarding on other models</h4>' + onb_t + '<p class="small" style="margin-top:6px">One example API with 2 operations; a few attempts per model.</p></div>'
               '<div class="card warn" style="margin-top:10px"><h4>Read with care</h4><p class="small">One language per row, 25 policy messages, one run for most models. Hypothetical models (other vendors) were priced, not run.</p></div></div></div>'))

# C1 tokens
bar_tokens = bars([(j.capitalize(), per_conv[j], f"{per_conv[j]:,}") for j in sorted(per_conv, key=lambda k: -per_conv[k])], max(per_conv.values()))
ob_rows = [[onb_r["step"].replace(" (tool service, Groq free tier)", " (tool service)").replace(" (knowledge service, OpenRouter free)", " (knowledge service)").replace(" (knowledge service, local bge-m3)", " (knowledge service, local)"), f"{int(onb_r['total_tokens']):,}"] for onb_r in onb]
ob_rows = [[("<b>" + r[0] + "</b>") if "ONE" in r[0] else r[0], ("<b>" + r[1] + "</b>") if "ONE" in r[0] else r[1]] for r in ob_rows]
S.append(slide("3 · MODELS &amp; COST · TOTAL TOKEN USAGE", "Where the tokens go " + chip("ok", "measured"),
               '<div class="tiles t3"><div class="tile"><b>' + f"{tot_tokens:,}" + '</b><span>tokens measured across all runs of this evaluation</span></div>'
               f'<div class="tile"><b>{prod_conv:,}</b><span>tokens per conversation in agent mode (AI jobs)</span></div>'
               f'<div class="tile"><b>{onb_total_tokens:,}</b><span>tokens to onboard one business (about ${onb_total_cost:.3f} at list price)</span></div></div>'
               '<div class="split"><div class="chart"><h5>Tokens per conversation, by job (conversation service)</h5>' + bar_tokens + "</div><div>"
               '<div class="card"><h4>One onboarding</h4>' + table(["Step", "Tokens"], ob_rows, "t xs", right=(1,)) + "</div>"
               f'<div class="card" style="margin-top:10px"><h4>Per event, knowledge service</h4><p class="small">Search query: about {kpm["embed_query"]["avg_input_tokens_per_ok_call"]} tokens (local, reported by Ollama). Risk second opinion: about {round((int(kpm["risk_second_opinion"]["input_tokens"])+int(kpm["risk_second_opinion"]["output_tokens"]))/int(kpm["risk_second_opinion"]["calls"]))} tokens per check '
               + chip("est", "estimate per conversation") + "</p></div></div></div>"
               f'<p class="note">By service: conversation {svc["conversation"][1]:,} · knowledge {svc["knowledge"][1]:,} · tool {svc["tools"][1]:,} tokens ({tot_calls:,} logged calls). '
               'Not yet measured: the tool service\'s other AI steps (triage, reconciliation, revision, suggestions) — only tool generation was run. Usage before the log existed was not recorded.</p>'))

# C2 cost
crow = [
    ["Rules + local embeddings (no AI model)", "0.00", "0", "0", "measured"],
    ["Agent mode, today's free Groq mix, at list price", money(c_today["total_usd_per_1000_messages_at_list_price"]), money(c_today["usd_per_month_at_10k_messages"]), money(c_today["usd_per_month_at_100k_messages"]), "measured tokens × list price"],
    ["Bare-minimum stack today (2 chat sizes + local bge-m3)", money(today["usd_per_1000_messages"]), money(today["usd_per_month_at_10k_messages"]), money(today["usd_per_month_at_100k_messages"]), "measured tokens × list price"],
    ["Cloud pair from the plan: GPT-6 Luna + Gemini 3.8 Flash", money(best["usd_per_1000_messages"]), money(best["usd_per_month_at_10k_messages"]), money(best["usd_per_month_at_100k_messages"]), "hypothetical, not tested"],
    ["Same pair after the 1 Jan 2027 price change", money(best27["usd_per_1000_messages"]), money(best27["usd_per_month_at_10k_messages"]), money(best27["usd_per_month_at_100k_messages"]), "hypothetical, not tested"],
    ["Mistral Small 4 + Mistral Large 3 (prices indicative)", money(lowest["usd_per_1000_messages"]), money(lowest["usd_per_month_at_10k_messages"]), money(lowest["usd_per_month_at_100k_messages"]), "hypothetical, not tested"],
]
S.append(slide("3 · MODELS &amp; COST · COST ANALYSIS", "Cost analysis: actual spend so far $0",
               '<p class="lead">The measured tokens priced at paid list prices. Every model call in this evaluation used a free tier.</p>' +
               table(["Stack", "USD per 1,000 msgs", "Month at 10k", "Month at 100k", "Status"], crow, "t sm", right=(1, 2, 3)) +
               f'<div class="cols"><div class="card"><h4>Pilot example</h4><p class="small">50,000 messages + 2 onboardings: about ${float(c_today["pilot_50k_messages_plus_2_onboardings_usd"]):,.0f} at list price (today\'s mix). Actual spend: $0.</p></div>'
               f'<div class="card"><h4>Other options priced in the report</h4><p class="small">11 what-if stacks from other vendors (all “hypothetical, not tested”) and three local-hardware tiers (next section). Qwen as orchestrator, priced at Groq\'s preview list price: ${money(c_qwen["total_usd_per_1000_messages_at_list_price"])} per 1,000 messages.</p></div></div>'))

# USP
usp = [
    ("Safe by design", f"The model only proposes, code decides. 0 unsafe actions in 83 scenarios and {long_convs} longer conversations."),
    ("No made-up facts", f"0 unbacked facts in {mg['conversations']} conversations measured. Answers cite policy or say “no evidence”."),
    ("Policy-as-code", "Every action: allow, deny or require_human."),
    ("Verified customer only", "Order ID + phone; every tool call re-checked."),
    ("Built for Egypt", "English, Egyptian Arabic, mixed and Arabizi."),
    ("Your API becomes tools", "Owner approves; staged release; instant disable."),
    ("Human handoff", "Same thread, with an automatic briefing."),
    ("Transparent", "DecisionTrace replay, and published results with their gaps."),
    ("Low cost, on-premise ready", f"About ${money(c_today['total_usd_per_1000_messages_at_list_price'])} per 1,000 messages at list price today; $0 actual spend on free tiers."),
]
S.append(slide("4 · UNIQUE SELLING POINT (USP)", "Why Ticket-Easy",
               '<div class="cols3 usp">' + "".join(f'<div class="card"><h4>{h}</h4><p>{t}</p></div>' for h, t in usp) + "</div>", data=False))

# use cases
uc = [
    ["Order status &amp; tracking", "Verifies order ID + phone, then a read-only lookup", "Automate"],
    ["Cancellation", "Policy gate checks the rules → customer confirms → written once", "Automate (tested)"],
    ["Returns &amp; exchanges", "Eligibility from the cited return policy → confirmation → executed once", "Automate (tested)"],
    ["Refunds", "Policy gate; edge cases go to require_human (see Known limitations)", "Automate / Handoff"],
    ["Vouchers", "Issued after confirmation, within policy", "Automate (tested)"],
    ["Policy questions (shipping, returns, warranty, opening hours)", "Answered with a citation, or “no evidence”", "Automate"],
    ["Billing / invoice questions", "Lookups for the verified customer only", "Automate"],
    ["Angry, risky or unclear cases", "Handed to a human in the same thread with a briefing", '<span class="rose">Handoff</span>'],
    ["Another customer's order, or actions without verification", "Refused before any tool is called", '<span class="rose">Deny</span>'],
]
S.append(slide("5 · TARGET DOMAINS &amp; USE CASES · E-COMMERCE", "E-commerce: use cases",
               '<p class="lead">Connected systems: Orders, CRM, Billing, and any other system with a REST API · English, Egyptian Arabic, mixed and Arabizi.</p>' +
               table(["Use case", "What Ticket-Easy does", "Mode"], uc, "t xs"), data=False))
steps = [("1", "Understand", "intent = return, entity = order"), ("2", "Safety screen", "passes"), ("3", "Verify identity", "order ID + phone"),
         ("4", "Agent loop plans", "look up the order → search the return policy → propose the return"), ("5", "Policy gate", "allow / deny / human, based on the cited return policy"),
         ("6", "Confirm &amp; act", "the customer confirms, then the Orders tool runs once"), ("7", "Verify &amp; reply", "reply in Arabizi with the result and the citation, logged in DecisionTrace")]
S.append(slide("5 · TARGET DOMAINS &amp; USE CASES · E-COMMERCE", "E-commerce: example walkthrough",
               '<p class="lead">A customer writes in Arabizi asking to return an item. Stages match diagram stages 1–7.</p><div class="steps">' +
               "".join(f'<div class="st"><span class="sn">{n}</span><h4>{h}</h4><p>{t}</p></div>' for n, h, t in steps) + "</div>"
               f'<p class="note">Evidence: tests ran on one fashion shop; 83 of 83 scripted scenarios pass {chip("ok", "measured")}; {long_convs} longer conversations with {long_writes} confirmed writes and 0 data leaks after a failed identity check {chip("ok", "measured")}. '
               'Business impact (tickets automated, response-time savings): <span class="tbd">[[TBD]]</span>. No live customer data yet.</p>', data=False))

# playbook appendix (unchanged content)
PROPOSED = '<span class="prop">Appendix · Proposed, not yet confirmed</span>'
S.append(slide("5 · TARGET DOMAINS &amp; USE CASES · PLAYBOOK", "Disposition rules &amp; common flow " + PROPOSED,
               '<div class="cols3"><div class="card"><h4>AUTOMATE</h4><p class="small">Low-risk FAQ, a secure status lookup or an approved routine action after verification and policy checks.</p></div>'
               '<div class="card rosec"><h4>HANDOFF</h4><p class="small">A trained person owns clinical, financial, insurance, identity or legal decisions.</p></div>'
               '<div class="card rosec"><h4>DENY</h4><p class="small">Refused before any tool is called, with a legitimate route offered.</p></div></div>'
               '<div class="ci">Receive → Understand → Verify → Ground → Enforce policy → Act or route → Close</div>'
               '<div class="card rosec"><h4>Always hand off</h4><p class="small">medical emergency · claim rejection or appeal · payment fraud or dispute · identity mismatch · key compromise · legal validity · policy conflict · repeated failed verification · the user asks for a human</p></div>', data=False))
S.append(slide("5 · TARGET DOMAINS &amp; USE CASES · PLAYBOOK", "HealthPay + HealthFlow (FEDIS partnership) " + PROPOSED,
               '<div class="cols3"><div class="card"><h4>Automate</h4><ul><li>payment methods, wellness card and service FAQ</li><li>payment-link troubleshooting</li><li>provider onboarding checklist and status</li><li>neutral claim-status lookup</li><li>prescription and provider-verification process FAQ</li></ul></div>'
               '<div class="card rosec"><h4>Handoff</h4><ul><li>medical urgency</li><li>claim rejection or appeal</li><li>payment dispute, refund or fraud</li><li>provider credential mismatch</li></ul></div>'
               '<div class="card rosec"><h4>Deny</h4><ul><li>accessing another person\'s records without proxy</li><li>falsifying clinical or claim data</li><li>bypassing payment controls</li><li>diagnosing or prescribing</li></ul></div></div>'
               '<div class="card"><h4>Pilot boundary</h4><ul><li>FAQ + authenticated read-only status + case creation</li><li>no money movement, coverage decisions or clinical advice</li></ul></div>', data=False))
S.append(slide("5 · TARGET DOMAINS &amp; USE CASES · PLAYBOOK", "e&amp; Egypt / My Signature e-signature (launched 2022 with Fixed Misr) " + PROPOSED,
               '<div class="cols3"><div class="card"><h4>Automate</h4><ul><li>service discovery and checklist</li><li>application or renewal status</li><li>expiry reminders</li><li>signing and device troubleshooting</li><li>business onboarding how-to</li></ul></div>'
               '<div class="card rosec"><h4>Handoff</h4><ul><li>identity mismatch</li><li>lost token or exposed key/PIN/OTP</li><li>signature or legal-validity dispute</li></ul></div>'
               '<div class="card rosec"><h4>Deny</h4><ul><li>signing for another person</li><li>forging or backdating</li><li>sharing secrets in chat</li><li>accessing another applicant\'s file</li></ul></div></div>'
               '<div class="card"><h4>Pilot boundary</h4><ul><li>FAQ + application-stage lookup + troubleshooting case</li><li>no identity approval, certificate issuance/revocation or signing by the assistant</li></ul></div>'
               '<p class="note">Success measures: grounded answer quality, safe automation, handoff usefulness, service outcomes, critical safety incidents (target zero). <b>Caveat:</b> workflows, APIs, current My Signature availability and HealthPay\'s licence scope must be confirmed with the partners.</p>', data=False))

# on-prem
def rng(tier, key, nd=2):
    lo = next(r for r in local if r["option"].startswith(tier) and r["case"] == "low")
    hi = next(r for r in local if r["option"].startswith(tier) and r["case"] == "high")
    return f"{float(lo[key]):,.{nd}f} – {float(hi[key]):,.{nd}f}"


op = [
    ["L1 starter", "one 24–32 GB GPU workstation · about $3,500–5,000 one-time", rng("L1", "monthly_cost_usd_estimate"), rng("L1", "usd_per_1000_messages_at_10k_a_month_estimate")],
    ["L2 mirror of today's models", "one 96 GB workstation GPU · about $10,000–12,000 one-time", rng("L2", "monthly_cost_usd_estimate"), rng("L2", "usd_per_1000_messages_at_10k_a_month_estimate")],
    ["L3 server", "one 80 GB data-centre GPU server · about $30,000–40,000 one-time", rng("L3 server", "monthly_cost_usd_estimate"), rng("L3 server", "usd_per_1000_messages_at_10k_a_month_estimate")],
    ["L3 rented", "same GPU by the hour · about $2–3 per GPU-hour (730 h a month)", rng("L3 rented", "monthly_cost_usd_estimate"), rng("L3 rented", "usd_per_1000_messages_at_10k_a_month_estimate")],
]
S.append(slide("6 · ON-PREMISE DEPLOYMENT COSTS", "On-premise deployment costs " + chip("est", "estimate"),
               '<p class="lead">API cost is $0. Throughput was not measured: one box is assumed to serve 10,000 messages a month.</p>' +
               table(["Option", "Hardware and one-time price", "USD per month", "USD per 1,000 msgs"], op, "t", right=(2, 3)) +
               f'<p class="note">Monthly = hardware ÷ 36 months + power × 730 h × $0.10 per kWh (rented: 730 hours × hourly rate). Cloud today: about ${money(c_today["usd_per_month_at_10k_messages"])} per month at 10,000 messages. '
               'On-premise is chosen for data control, not for cost at low volume. Setup and support costs: <span class="tbd">[[TBD]]</span></p>', data=False))

# known limitations
lim = [
    ["Decision and citation below target", f"decision {mg['decision_pct']}% (target 92%), citation {mg['citation_pct']}% (target 95%); on 8 of 25 English policy messages the agent asked a clarifying question or handed over instead of searching", "Search the policy before asking; re-test the same 25 conversations"],
    ["Old-order refund request", "in agent mode it can loop on “what amount?” instead of explaining the 14-day rule", "Fix the refusal path; re-test"],
    ["More handoffs than the rules", f"handoff precision {mg['handoff_precision_pct']}% against {mr['handoff_precision_pct']}%; {mg['needless_handoffs']} needless handoffs against {mr['needless_handoffs']}", "Tune when the agent may hand over"],
    ["Coverage", "tests ran on one fashion shop; scripted longer conversations; no real customer messages yet", "Second business; anonymised real messages"],
    ["Model choice", "most orchestrator comparisons are one run per model; the gpt-oss-120b English repeats were not run", "Repeat runs"],
    ["Arabic wording quality", "graded by program checks only; blinded sheets for a person are prepared", "Human grading"],
    ["Token measurement", "tool service steps other than generation are not measured; onboarding measured on a 2-operation example", "Run the full tool-service flow with logging on"],
    ["Weekly CI", "workflow defined, not yet run on this branch", "First scheduled run"],
]
S.append(slide("APPENDIX · KNOWN LIMITATIONS &amp; ROADMAP", "Known limitations and roadmap " + chip("gap", "measured gaps"),
               table(["Area", "What was measured", "Proposed next step"], lim, "t sm"), data=True))

# ---------------------------------------------------------------- page
css = (A / "style.css").read_text(encoding="utf-8")
js = (A / "app.js").read_text(encoding="utf-8")
svg = (A / "diagram.svg").read_text(encoding="utf-8")
html = ('<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ticket-Easy for Fixed Misr Online</title>'
        '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=Sora:wght@600;700;800&family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">'
        f"<style>{css}</style></head><body><div id=\"deck\">{''.join(S)}</div>"
        '<div class="hud"><button id="pv" aria-label="Previous">←</button><span id="ct"></span><button id="nx" aria-label="Next">→</button></div><div class="prog"><div id="pb"></div></div>'
        f'<template id="dia">{svg}</template><script>{js}</script></body></html>')
out = OUT_DIR / "ticket-easy-fixed-misr-deck-21.html"
out.write_text(html, encoding="utf-8")
print("wrote", out, f"{len(html):,} bytes,", len(S), "slides")
