"""E7: price and cost tables from MEASURED tokens (tables/tokens.csv, onboarding.csv, models/comparison.csv) and list prices.
Run from the repository root. Prices are dated and sourced in prices.csv; nothing here is a quote from a customer contract.
Every API cost is "what the same tokens would cost at list price"; the actual spend of this work was $0 (free tiers)."""
import csv
import json
from pathlib import Path

RUNS = Path("Docs/output/eval_runs")
OUT = RUNS / "tables"
DATE = "17 Oct 2026"

# (model key) -> input $/M, output $/M, source, date, status
PRICES = {
    "groq/gpt-oss-120b": (0.15, 0.60, "https://console.groq.com/docs/models", DATE, "provider page (read by the fetch tool)"),
    "groq/gpt-oss-20b": (0.075, 0.30, "https://console.groq.com/docs/models", DATE, "provider page (read by the fetch tool)"),
    "groq/qwen3.8-27b": (0.80, 4.00, "https://console.groq.com/docs/models", DATE, "provider page; a preview model, 'for evaluation only'"),
    "openrouter/nemotron-3-super-120b-a12b (paid)": (0.08, 0.45, "https://openrouter.ai/api/v1/models", DATE, "OpenRouter public price list; the :free variant is $0"),
    "openrouter/gemma-4-31b-it (paid)": (0.20, 0.40, "https://openrouter.ai/api/v1/models", DATE, "OpenRouter public price list; the :free variant is $0"),
    "openai/gpt-6-luna": (0.10, 0.50, "https://openrouter.ai/api/v1/models", DATE, "OpenAI's own page returned HTTP 403; OpenRouter public list used (equal to the plan's indicative price)"),
    "openai/gpt-6.1-sol": (2.00, 10.00, "https://openrouter.ai/api/v1/models", DATE, "OpenAI's own page returned HTTP 403; OpenRouter public list used (equal to the plan's indicative price)"),
    "google/gemini-3.5-flash-lite": (0.30, 2.50, "https://ai.google.dev/gemini-api/docs/pricing", DATE, "provider page"),
    "google/gemini-3.8-flash (to 31 Dec 2026)": (0.75, 3.75, "https://ai.google.dev/gemini-api/docs/pricing", DATE, "provider page; introductory price"),
    "google/gemini-3.8-flash (from 1 Jan 2027)": (1.50, 7.50, "https://ai.google.dev/gemini-api/docs/pricing", DATE, "provider page"),
    "anthropic/claude-sonnet-5.5": (2.00, 10.00, "https://platform.claude.com/docs/en/about-claude/pricing", DATE, "provider page"),
    "deepseek/deepseek-flash (peak)": (0.30, 1.20, "https://api-docs.deepseek.com/quick_start/pricing", DATE, "provider page; off-peak is half"),
    "deepseek/deepseek-flash (off-peak)": (0.15, 0.60, "https://api-docs.deepseek.com/quick_start/pricing", DATE, "provider page"),
    "mistral/mistral-small-4": (0.15, 0.60, "plan, Part 2 (vendor survey)", "6 Oct 2026", "indicative, 6 Oct 2026: Mistral's page names no Small 4 price"),
    "mistral/mistral-large-3": (0.50, 1.50, "https://mistral.ai/pricing", DATE, "the page quotes 'Mistral Large $0.5 in / $1.5 out' without naming version 3: indicative"),
}


def w(name, rows):
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with (OUT / name).open("w", encoding="utf-8", newline="") as h:
        d = csv.DictWriter(h, fieldnames=fields)
        d.writeheader()
        d.writerows(rows)


def read(p):
    return list(csv.DictReader(p.open(encoding="utf-8")))


w("prices.csv", [{"model": k, "input_usd_per_1m": v[0], "output_usd_per_1m": v[1], "source": v[2], "date": v[3], "how_obtained": v[4]} for k, v in PRICES.items()])

# measured tokens per message, by job (en+ar+mixed, agent mode, real knowledge service)
tok = {r["job"]: r for r in read(OUT / "tokens.csv") if r["scope"] == "en+ar+mixed"}
JOBS = ("orchestrator", "reviewer", "reader", "knowledge", "writer", "summary")
per_msg = {j: (int(tok[j]["input_tokens"]) / int(tok[j]["messages"]), int(tok[j]["output_tokens"]) / int(tok[j]["messages"])) for j in JOBS}
total_in = sum(v[0] for v in per_msg.values())
total_out = sum(v[1] for v in per_msg.values())


def cost_per_1000(assign):
    """assign: job -> price key. USD per 1,000 messages (embeddings are local: $0)."""
    total = 0.0
    for job, (tin, tout) in per_msg.items():
        pin, pout = PRICES[assign[job]][0], PRICES[assign[job]][1]
        total += (tin * pin + tout * pout) / 1e6 * 1000
    return total


def stack_row(name, assign, status, note=""):
    c = cost_per_1000(assign)
    return {"stack": name, "status": status, "usd_per_1000_messages": round(c, 3), "usd_per_month_at_10k_messages": round(c * 10, 2),
            "usd_per_month_at_100k_messages": round(c * 100, 2), "quality": "not tested" if status.startswith("hypothetical") else "see models.csv", "note": note}


def uniform(key):
    return {j: key for j in JOBS}


TODAY = {"orchestrator": "groq/gpt-oss-120b", "reviewer": "groq/gpt-oss-120b", "reader": "groq/qwen3.8-27b", "knowledge": "groq/qwen3.8-27b", "writer": "groq/qwen3.8-27b", "summary": "groq/qwen3.8-27b"}
PLAN_SLOTS = lambda small, strong: {"reader": small, "writer": small, "summary": small, "knowledge": strong, "orchestrator": strong, "reviewer": strong}
BARE = PLAN_SLOTS("groq/gpt-oss-20b", "groq/gpt-oss-120b") | {"knowledge": "groq/qwen3.8-27b", "writer": "groq/qwen3.8-27b"}

# ---- cost.csv: the three presentation stacks ----
orch_q = [json.loads(l) for l in (RUNS / "usage/orch_qwen3.8-27b.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
q_ok = [u for u in orch_q if u["job"] == "orchestrator" and u["status"] == "ok"]
turns_q = 27
q_in, q_out = sum(u["input_tokens"] or 0 for u in q_ok) / turns_q, sum(u["output_tokens"] or 0 for u in q_ok) / turns_q
g_in, g_out = per_msg["orchestrator"]
adj = cost_per_1000(TODAY) - (g_in * 0.15 + g_out * 0.60) / 1e3 + (q_in * 0.80 + q_out * 4.00) / 1e3
searches = float(next(r for r in read(OUT / "tokens_all_services.csv") if r["component"].startswith("knowledge service: search"))["tokens_per_conversation"])
conv_cost = lambda c: c / 1000 * (213 / 180)  # a conversation holds 1.18 messages
rows = []
for name, c1k, conv_cols, what in (
    ("rules + local embeddings (no AI model)", 0.0, (0.0, 0.0), "no AI tokens; embeddings run locally on Ollama ($0 API; hardware and power are separate, see local_estimates.csv)"),
    ("agent mode, today's free Groq mix (measured)", cost_per_1000(TODAY), None, "orchestrator and reviewer gpt-oss-120b; reader, knowledge, writer, summary qwen3.8-27b; measured over 180 conversations (213 messages)"),
    ("agent mode, qwen3.8-27b as orchestrator too", adj, None, f"orchestrator tokens measured on the 25 English policy conversations only (in {q_in:.0f} / out {q_out:.0f} per message); other jobs as today; estimate"),
):
    rows.append({"stack": name, "actual_spend_usd": 0, "conversation_service_usd_per_1000_messages": round(c1k, 3),
                 "knowledge_service_usd_per_1000_messages": 0.0, "total_usd_per_1000_messages_at_list_price": round(c1k, 3),
                 "usd_per_month_at_10k_messages": round(c1k * 10, 2), "usd_per_month_at_100k_messages": round(c1k * 100, 2),
                 "pilot_50k_messages_plus_2_onboardings_usd": None, "status": "measured tokens x list price" if "measured)" in name or name.startswith("rules") else "estimate", "note": what})
# onboarding priced at list price
onb = []
steps = {r["step"]: r for r in read(OUT / "onboarding_measured.csv")}
tool = next(r for k, r in steps.items() if k.startswith("tool generation"))
rule = next(r for k, r in steps.items() if k.startswith("rule extraction"))
emb = next(r for k, r in steps.items() if k.startswith("document embeddings"))
for label, r, key in (("tool generation (tool service)", tool, "groq/gpt-oss-120b"), ("rule extraction (knowledge service)", rule, "openrouter/nemotron-3-super-120b-a12b (paid)")):
    pin, pout = PRICES[key][0], PRICES[key][1]
    cost = (int(r["input_tokens"]) * pin + int(r["output_tokens"]) * pout) / 1e6
    onb.append({"step": label, "model": key, "input_tokens": int(r["input_tokens"]), "output_tokens": int(r["output_tokens"]), "list_price_usd": round(cost, 5),
                "actual_spend_usd": 0, "status": "measured tokens x list price"})
onb.append({"step": "document embeddings (knowledge service)", "model": "bge-m3 on local Ollama", "input_tokens": int(emb["input_tokens"]), "output_tokens": 0, "list_price_usd": 0.0, "actual_spend_usd": 0, "status": "measured; $0 API"})
onb_total = sum(r["list_price_usd"] for r in onb)
onb.append({"step": "ONE ONBOARDING", "model": "", "input_tokens": sum(r["input_tokens"] for r in onb), "output_tokens": sum(r["output_tokens"] for r in onb), "list_price_usd": round(onb_total, 5), "actual_spend_usd": 0,
            "status": "the example API has only 2 operations, so the tool-generation part is small; a real business costs more"})
w("onboarding_cost.csv", onb)
for r in rows:
    r["pilot_50k_messages_plus_2_onboardings_usd"] = round(r["total_usd_per_1000_messages_at_list_price"] * 50 + (2 * onb_total if "agent" in r["stack"] else 0), 2)
w("cost.csv", rows)

# ---- hypothetical.csv ----
hyp = [
    stack_row("bare-minimum, today: reader+summary gpt-oss-20b, knowledge+writer qwen3.8-27b, orchestrator+reviewer gpt-oss-120b (plan slots)", BARE, "measured tokens x list price", "the three Groq models; reader and summary tokens were measured on qwen and are priced here on gpt-oss-20b"),
    stack_row("bare-minimum, best value (recommended in the plan): small GPT-6 Luna, strong Gemini 3.8 Flash (to 31 Dec 2026)", PLAN_SLOTS("openai/gpt-6-luna", "google/gemini-3.8-flash (to 31 Dec 2026)"), "hypothetical, not tested"),
    stack_row("bare-minimum, best value: same, Gemini 3.8 Flash from 1 Jan 2027", PLAN_SLOTS("openai/gpt-6-luna", "google/gemini-3.8-flash (from 1 Jan 2027)"), "hypothetical, not tested"),
    stack_row("bare-minimum, lowest cost: small Mistral Small 4, strong Mistral Large 3", PLAN_SLOTS("mistral/mistral-small-4", "mistral/mistral-large-3"), "hypothetical, not tested", "prices indicative"),
    stack_row("what-if 1a: GPT-6 Luna for every job", uniform("openai/gpt-6-luna"), "hypothetical, not tested"),
    stack_row("what-if 1b: Gemini 3.5 Flash-Lite for every job", uniform("google/gemini-3.5-flash-lite"), "hypothetical, not tested"),
    stack_row("what-if 2a: GPT-6.1 Sol orchestrator and reviewer, GPT-6 Luna for the rest", PLAN_SLOTS("openai/gpt-6-luna", "openai/gpt-6.1-sol") | {"knowledge": "openai/gpt-6-luna"}, "hypothetical, not tested"),
    stack_row("what-if 2b: Claude Sonnet 5.5 orchestrator and reviewer, GPT-6 Luna for the rest", PLAN_SLOTS("openai/gpt-6-luna", "openai/gpt-6-luna") | {"orchestrator": "anthropic/claude-sonnet-5.5", "reviewer": "anthropic/claude-sonnet-5.5"}, "hypothetical, not tested"),
    stack_row("what-if 3a: DeepSeek Flash for every job (peak price)", uniform("deepseek/deepseek-flash (peak)"), "hypothetical, not tested"),
    stack_row("what-if 3b: DeepSeek Flash for every job (off-peak price)", uniform("deepseek/deepseek-flash (off-peak)"), "hypothetical, not tested"),
    stack_row("what-if 3c: Mistral Small 4 for every job", uniform("mistral/mistral-small-4"), "hypothetical, not tested", "price indicative"),
]
for r in hyp:
    r["tokenizer_caveat"] = "other models count the same text as more or fewer tokens"
    r["speed"] = "not tested" if r["status"].startswith("hypothetical") else "measured on the Groq free tier"
w("hypothetical.csv", hyp)

# ---- local_estimates.csv: every input, so the numbers can be changed ----
KWH = 0.10
LOCAL = [("L1 starter (one 24-32 GB GPU workstation)", 3500, 5000, 0.6, None), ("L2 mirror of today (one 96 GB workstation GPU)", 10000, 12000, 0.8, None),
         ("L3 server (one 80 GB data-centre GPU server)", 30000, 40000, 1.2, None), ("L3 rented (same GPU by the hour)", None, None, None, (2.0, 3.0))]
loc = []
for name, lo, hi, kw, rent in LOCAL:
    if rent:
        lo_m, hi_m = rent[0] * 730, rent[1] * 730
        detail = "GPU rented around the clock: 730 hours x $2-3 per hour"
    else:
        lo_m, hi_m = lo / 36 + kw * 730 * KWH, hi / 36 + kw * 730 * KWH
        detail = f"hardware / 36 months + {kw} kW x 730 h x ${KWH}/kWh"
    for label, m in (("low", lo_m), ("high", hi_m)):
        loc.append({"option": name, "case": label, "hardware_price_usd": lo if label == "low" else hi, "power_kw": kw, "electricity_usd_per_kwh": KWH if kw else None,
                    "hours_per_month": 730, "hourly_rate_usd": (rent[0] if label == "low" else rent[1]) if rent else None, "formula": detail,
                    "api_cost_usd": 0, "monthly_cost_usd_estimate": round(m, 2), "usd_per_1000_messages_at_10k_a_month_estimate": round(m / 10, 2),
                    "usd_per_1000_messages_at_100k_a_month_estimate": round(m / 100, 2), "status": "estimate; throughput not measured"})
w("local_estimates.csv", loc)
print("per message tokens (in/out):", {j: (round(v[0]), round(v[1])) for j, v in per_msg.items()})
for r in rows:
    print(r["stack"][:52].ljust(52), r["total_usd_per_1000_messages_at_list_price"], r["usd_per_month_at_10k_messages"], r["usd_per_month_at_100k_messages"], r["pilot_50k_messages_plus_2_onboardings_usd"])
for r in hyp:
    print(r["stack"][:70].ljust(70), r["usd_per_1000_messages"])
print("onboarding at list price:", round(onb_total, 5))

# ---- models.csv: the plan's model table, measured against hypothetical ----
cmp_ = {r["model"]: r for r in read(RUNS / "models/comparison.csv")}
onbm = {r["job"]: r for r in read(OUT / "onboarding_measured.csv")}
kpm = {r["job"]: r for r in read(OUT / "knowledge_per_message.csv")}


def pr(key):
    return f"{PRICES[key][0]} / {PRICES[key][1]}"


models = [
    {"model": "Groq gpt-oss-20b", "deploy": "cloud (Groq free tier)", "role": "reader candidate, summary, judge, backup of the qwen jobs", "status": "measured in this refresh",
     "latency": f"reader p50 {cmp_['openai/gpt-oss-20b']['latency_p50_ms']} ms, p95 {cmp_['openai/gpt-oss-20b']['latency_p95_ms']} ms (Groq free tier)",
     "quality": f"reader intent accuracy {cmp_['openai/gpt-oss-20b']['intent_accuracy_pct']}% on 155 messages; orchestrator test (English only): 19 of 25 policy citations; reviewer replays agree 93% with the baseline",
     "usd_per_1m_in_out": pr("groq/gpt-oss-20b"), "notes": "Arabic and mixed orchestrator results are partly contaminated by rate limits"},
    {"model": "Groq qwen3.8-27b", "deploy": "cloud (Groq free tier, preview model)", "role": "reader, knowledge, writer, summary (today)", "status": "measured in this refresh",
     "latency": f"reader p50 {cmp_['qwen/qwen3.8-27b']['latency_p50_ms']} ms, p95 {cmp_['qwen/qwen3.8-27b']['latency_p95_ms']} ms (Groq free tier)",
     "quality": f"reader intent accuracy {cmp_['qwen/qwen3.8-27b']['intent_accuracy_pct']}% (best of the three); as orchestrator on English policy conversations: 22 of 25 and 21 of 25 citations (two clean runs)",
     "usd_per_1m_in_out": pr("groq/qwen3.8-27b"), "notes": "a preview model that can be withdrawn; Arabic and mixed orchestrator runs were mostly answered by the fixed flow because its quota ran out, so they are not conclusive"},
    {"model": "Groq gpt-oss-120b", "deploy": "cloud (Groq free tier)", "role": "orchestrator, reviewer (today)", "status": "measured in this refresh",
     "latency": f"reader p50 {cmp_['openai/gpt-oss-120b']['latency_p50_ms']} ms, p95 {cmp_['openai/gpt-oss-120b']['latency_p95_ms']} ms; agent turn median 6 to 7 s (Groq free tier)",
     "quality": f"reader intent accuracy {cmp_['openai/gpt-oss-120b']['intent_accuracy_pct']}%; orchestrator on English policy conversations: 17 of 25 citations (one run; repeats and Arabic/mixed pending its daily token quota)",
     "usd_per_1m_in_out": pr("groq/gpt-oss-120b"), "notes": "its daily token limit is the bottleneck of long evaluation runs"},
    {"model": "Ollama bge-m3", "deploy": "local", "role": "knowledge service search and document embeddings", "status": "in use, local, measured",
     "latency": f"query p50 {kpm['embed_query']['latency_p50_ms']} ms, p95 {kpm['embed_query']['latency_p95_ms']} ms", "quality": "retrieval recall@5 on the held-out test split: shop_001 89.3%, noon_eg 97.5%, telco_001 82.2%",
     "usd_per_1m_in_out": "0 (local)", "notes": f"about {kpm['embed_query']['avg_input_tokens_per_ok_call']} tokens per query, reported by Ollama"},
    {"model": "OpenRouter free: Nemotron 3 Super 120B (and Gemma 4 31B as the second choice)", "deploy": "cloud (OpenRouter free)", "role": "knowledge service: rule extraction and risk second opinion", "status": "in use, measured (not benchmarked)",
     "latency": f"rule extraction p50 {onbm['rule_extraction']['latency_p50_ms']} ms; risk check p50 {kpm['risk_second_opinion']['latency_p50_ms']} ms", "quality": "36 passages produced 20 candidate rules; all 40 risk checks answered; quality of the rules was not judged",
     "usd_per_1m_in_out": f"free; paid version {pr('openrouter/nemotron-3-super-120b-a12b (paid)')}", "notes": "Gemma was rate-limited by its upstream provider during the run"},
]
for key, label in (("openai/gpt-6-luna", "OpenAI GPT-6 Luna"), ("openai/gpt-6.1-sol", "OpenAI GPT-6.1 Sol"), ("google/gemini-3.5-flash-lite", "Google Gemini 3.5 Flash-Lite"),
                   ("google/gemini-3.8-flash (to 31 Dec 2026)", "Google Gemini 3.8 Flash"), ("anthropic/claude-sonnet-5.5", "Anthropic Claude Sonnet 5.5"),
                   ("deepseek/deepseek-flash (peak)", "DeepSeek Flash"), ("mistral/mistral-small-4", "Mistral Small 4"), ("mistral/mistral-large-3", "Mistral Large 3")):
    models.append({"model": label, "deploy": "cloud API", "role": "cost row only", "status": "hypothetical, not tested", "latency": "not tested", "quality": "not tested", "usd_per_1m_in_out": pr(key), "notes": "see hypothetical.csv"})
for label, fit in (("gpt-oss-20b (open weights, local)", "reader, summary, backup; ~16 GB"), ("gpt-oss-120b (open weights, local)", "orchestrator, reviewer, onboarding; ~80 GB"), ("Qwen 27B (local, if weights are published)", "writer, knowledge; ~18-20 GB"),
                   ("Qwen3-30B-A3B (local)", "reader, knowledge, writer; ~20 GB"), ("Qwen3 32B (local)", "knowledge, writer, orchestrator; ~22 GB"), ("Gemma 4 31B (local)", "knowledge, writer; ~22 GB"),
                   ("Mistral Small ~24B (local)", "reader, writer, summary; ~16 GB"), ("Llama 3.3 70B (local)", "orchestrator, reviewer; ~45 GB"), ("Qwen3 8B (local)", "reader, summary; ~6 GB"), ("Nile-Chat 4B (local)", "Egyptian Arabic writer only; ~4 GB")):
    models.append({"model": label, "deploy": "local, own hardware", "role": fit, "status": "hypothetical, not tested", "latency": "not tested", "quality": "not tested", "usd_per_1m_in_out": "0 API; hardware and power estimated in local_estimates.csv", "notes": "throughput not measured"})
w("models.csv", models)
print("models.csv rows:", len(models))
