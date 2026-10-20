"""E6 tables (run from the repository root). Numbers come only from the report files, the usage logs and merge_runs.py.

headline.csv, per_style.csv, safety.csv, tokens.csv, tokens_all_services.csv, onboarding.csv, usage_totals.csv -> tables/
"""
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

RUNS = Path("docs/reports/eval_runs")
sys.path.insert(0, str(RUNS))
import merge_runs as mr  # noqa: E402

REPORTS = RUNS / "reports"
OUT = RUNS / "tables"
STYLES = ("en", "ar", "mixed")
AGENT = [REPORTS / f"eval_presentation_agent_live_{s}_2026-10-17.json" for s in STYLES]
RULES_BEFORE = REPORTS / "eval_live_rules_2026-10-17.json"
RULES_AFTER = REPORTS / "eval_live_rules_after_fix_2026-10-17.json"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def merged(paths):
    per = {}
    for p in paths:
        d = load(p)
        for s in STYLES:
            e = d["styles"].get(s)
            if e and e["conversations"]:
                per[s] = mr.counts(e, s, p.name)
    return per, mr.combine(per)


def write(name, rows):
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with (OUT / name).open("w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


a_per, a = merged(AGENT)
rb_per, rb = merged([RULES_BEFORE])
ra_per, ra = merged([RULES_AFTER])

# ---- headline: EN + AR + mixed combined ----
def n_of(c, key):
    return f"{c[key + '_right']}/{c[key + '_total']}"


def line(measure, unit, fn):
    return {"measure": measure, "unit": unit, "rules_only_before_fix": fn(rb), "rules_only_after_fix": fn(ra), "agent_mode": fn(a)}


p50 = lambda per: f"{min(c['latency_p50_ms'] for c in per.values()) / 1000:.1f} to {max(c['latency_p50_ms'] for c in per.values()) / 1000:.1f}"
p95 = lambda per: f"{min(c['latency_p95_ms'] for c in per.values()) / 1000:.1f} to {max(c['latency_p95_ms'] for c in per.values()) / 1000:.1f}"
rows = [
    line("intent accuracy", "% (right/total)", lambda c: f"{c['intent_pct']} ({n_of(c, 'intent')})"),
    line("decision accuracy", "% (right/total)", lambda c: f"{c['decision_pct']} ({n_of(c, 'decision')})"),
    line("citation accuracy", "% (right/total)", lambda c: f"{c['citation_pct']} ({n_of(c, 'citation')})"),
    line("locale match", "% (right/total)", lambda c: f"{c['locale_pct']} ({n_of(c, 'locale')})"),
    line("handoff precision", "%", lambda c: c["handoff_precision_pct"]),
    line("handoff recall", "%", lambda c: c["handoff_recall_pct"]),
    line("solved without a person", "% (conversations / those needing no person)", lambda c: f"{c['solved_without_person_pct']} ({c['solved_without_person']}/{c['no_person_needed']})"),
    line("needless handoffs", "count", lambda c: c["needless_handoffs"]),
    line("missed escalations", "count of those wanted", lambda c: f"{c['missed_escalations']} of {c['escalations_wanted']}"),
    line("wrong or unapproved actions", "count (writes seen in the 240-set run)", lambda c: f"{c['wrong_or_unapproved_actions']} (of {c['writes']} writes)"),
    line("unbacked facts", "count", lambda c: c["unbacked_facts"]),
    line("agent fallbacks to the fixed flow", "count", lambda c: c["agent_fallbacks"]),
    line("AI calls per message", "calls", lambda c: c["ai_calls_per_message"]),
]
rows.append({"measure": "reply time median, per style (en to mixed range)", "unit": "seconds, Groq free tier (agent); local (rules)", "rules_only_before_fix": p50(rb_per), "rules_only_after_fix": p50(ra_per), "agent_mode": p50(a_per)})
rows.append({"measure": "reply time 95th percentile, per style (range)", "unit": "seconds, Groq free tier (agent); local (rules)", "rules_only_before_fix": p95(rb_per), "rules_only_after_fix": p95(ra_per), "agent_mode": p95(a_per)})
rows.append({"measure": "sample", "unit": "", "rules_only_before_fix": f"{rb['conversations']} conversations, {rb['turns']} turns", "rules_only_after_fix": f"{ra['conversations']} conversations, {ra['turns']} turns", "agent_mode": f"{a['conversations']} conversations, {a['turns']} turns"})
write("headline.csv", rows)

# ---- per style (Arabizi: appendix, sample only) ----
ps = []
for s in STYLES:
    for label, per in (("rules only (before the fix)", rb_per), ("rules only (after the fix)", ra_per), ("agent mode", a_per)):
        c = per[s]
        ps.append({"style": s, "run": label, "appendix": "no", "conversations": c["conversations"], "turns": c["turns"],
                   "intent_pct": mr.pct(c["intent_right"], c["intent_total"]), "decision_pct": mr.pct(c["decision_right"], c["decision_total"]),
                   "citation_pct": mr.pct(c["citation_right"], c["citation_total"]), "locale_pct": mr.pct(c["locale_right"], c["locale_total"]),
                   "handoff_precision_pct": mr.pct(c["handoff_right"], c["handoff_made"]), "handoff_recall_pct": mr.pct(c["handoff_right"], c["handoff_wanted"]),
                   "solved_without_person": f"{c['solved_without_person']}/{c['no_person_needed']}", "wrong_actions": c["wrong_or_unapproved_actions"],
                   "unbacked_facts": c["unbacked_facts"], "agent_fallbacks": c["agent_fallbacks"], "time_p50_s": round(c["latency_p50_ms"] / 1000, 1), "time_p95_s": round(c["latency_p95_ms"] / 1000, 1)})
for label, path, note in (
    ("rules only (before the fix)", RULES_BEFORE, "all 60 conversations"),
    ("agent mode, keyword simulated service, SAMPLE of 15 conversations", REPORTS / "eval_presentation_agent_arabizi_2026-10-16.json", "appendix: a sample, not the full set"),
):
    e = load(path)["styles"]["arabizi"]
    ps.append({"style": "arabizi", "run": label, "appendix": "yes: " + note, "conversations": e["conversations"], "turns": e["turns"],
               "intent_pct": round(100 * e["intent_accuracy"], 1), "decision_pct": round(100 * e["decision_accuracy"], 1), "citation_pct": round(100 * e["citation_accuracy"], 1),
               "locale_pct": round(100 * e["locale_match"], 1), "wrong_actions": e["measures"]["wrong_or_unapproved_actions"], "unbacked_facts": e["measures"]["unbacked_facts"],
               "agent_fallbacks": e["measures"]["agent_fallbacks"], "time_p50_s": round(e["latency_p50_ms"] / 1000, 1), "time_p95_s": round(e["latency_p95_ms"] / 1000, 1)})
write("per_style.csv", ps)

# ---- safety: every run, wrong actions and unbacked facts ----
safety = []
for label, path, note in (
    ("rules only, keyword simulated search", REPORTS / "eval_presentation_rules_2026-10-16.json", ""),
    ("rules only, real knowledge service (before the fix)", RULES_BEFORE, "the 1 unbacked fact was a passage the real service returned and the evaluation did not know; fixed in the check"),
    ("rules only, real knowledge service (after the fix)", RULES_AFTER, "the same single case as before the fix; the evaluation check was corrected afterwards"),
    ("agent mode, keyword simulated service (first run)", None, "the 8 unbacked facts were 'today' in greetings; the evaluation check flagged them wrongly and was fixed"),
    ("agent mode, real knowledge service (headline)", None, ""),
):
    if label.startswith("agent mode, keyword"):
        entries = [load(REPORTS / f"eval_presentation_agent_{s}_2026-10-16.json")["styles"][s] for s in STYLES]
    elif label.startswith("agent mode, real"):
        entries = [load(p)["styles"][s] for p, s in zip(AGENT, STYLES)]
    else:
        d = load(path)
        entries = [d["styles"][s] for s in STYLES]
    safety.append({"run": label, "styles": "en+ar+mixed", "conversations": sum(e["conversations"] for e in entries),
                   "wrong_or_unapproved_actions": sum(e["measures"]["wrong_or_unapproved_actions"] for e in entries),
                   "writes_seen": sum(e["measures"]["writes"] for e in entries), "unbacked_facts": sum(e["measures"]["unbacked_facts"] for e in entries), "note": note})
# the long-conversation runs (24 conversations with real writes, real knowledge service)
long_rows = []
for s in STYLES:
    for line_ in (RUNS / f"diagnosis/longlive_{s}.jsonl").read_text(encoding="utf-8").splitlines():
        if line_.strip():
            long_rows.append(json.loads(line_))
long_turns = [r for r in long_rows if "turn" in r]
leaks = [r for r in long_turns if r["conversation"].endswith("-06") and any(x in r["reply"] for x in ("Linen summer dress", "TRK-20877", "890"))]
returns = [r for r in long_turns if r["conversation"].endswith("-04") and "RET-" in r["reply"]]
safety.append({"run": "long conversations, agent mode, real knowledge service", "styles": "en+ar+mixed", "conversations": len({r['conversation'] for r in long_turns}),
               "wrong_or_unapproved_actions": "not scored by the harness; checked by hand: 0 unexpected writes", "writes_seen": sum(1 for r in long_turns if r["decision"] == "execute"), "unbacked_facts": sum(1 for r in long_turns if r["unbacked"]),
               "note": f"order-data leaks after a failed identity check: {len(leaks)}; returns executed after a topic change: {len(returns)}; writes executed = turns that carried out a refund, cancel, voucher or exchange"})
write("safety.csv", safety)

# ---- tokens: conversation service, agent mode, per style and combined ----
def usage(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


tok = []
by_style = {s: usage(RUNS / f"usage/agent_live_{s}.jsonl") for s in STYLES}
for scope in (*STYLES, "en+ar+mixed"):
    calls = [c for s in STYLES for c in by_style[s]] if scope == "en+ar+mixed" else by_style[scope]
    per = list(a_per.values()) if scope == "en+ar+mixed" else [a_per[scope]]
    messages, conversations = sum(c["turns"] for c in per), sum(c["conversations"] for c in per)
    jobs = defaultdict(lambda: [0, 0, 0])
    failed = defaultdict(int)
    for c in calls:
        j = jobs[c["job"]]
        j[0] += c["status"] == "ok"  # a rate-limited or failed attempt is not a call that was answered
        failed[c["job"]] += c["status"] != "ok"
        j[1] += c["input_tokens"] or 0
        j[2] += c["output_tokens"] or 0
    groups = [(j, v) for j, v in sorted(jobs.items())]
    prod = [v for j, v in jobs.items() if j != "judge"]
    groups.append(("ALL PRODUCTION JOBS (without the judge)", [sum(v[i] for v in prod) for i in range(3)]))
    for job, (n, tin, tout) in groups:
        tok.append({"scope": scope, "job": job, "calls": n, "failed_or_rate_limited_attempts": failed[job] if job in failed else sum(failed[j] for j in jobs if j != "judge"), "calls_per_message": round(n / messages, 2), "input_tokens": tin, "output_tokens": tout, "total_tokens": tin + tout,
                    "input_tokens_per_message": round(tin / messages), "output_tokens_per_message": round(tout / messages), "total_tokens_per_message": round((tin + tout) / messages),
                    "total_tokens_per_conversation": round((tin + tout) / conversations), "messages": messages, "conversations": conversations,
                    "source": f"usage/agent_live_{scope if scope != 'en+ar+mixed' else '<style>'}.jsonl", "note": "evaluation only: not part of the product" if job == "judge" else "measured"})
write("tokens.csv", tok)

# ---- tokens for every component together, per conversation ----
q = [json.loads(l) for l in (RUNS / "usage/knowledge_live_eval.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()] if (RUNS / "usage/knowledge_live_eval.jsonl").exists() else []
start = min(c["ts"] for s in STYLES for c in by_style[s])
end = max(c["ts"] for s in STYLES for c in by_style[s])
window = [x for x in q if x["job"] == "embed_query" and start <= x["ts"] <= end]
conv_total = a["conversations"]
searches = len(window)
per_search_tokens = round(sum(x["input_tokens"] or 0 for x in window) / searches, 1) if searches else None
prod_total = next(r for r in tok if r["scope"] == "en+ar+mixed" and r["job"].startswith("ALL PRODUCTION"))
risk = [json.loads(l) for l in (RUNS / "usage/knowledge_risk.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
risk_tokens = round(sum((x["input_tokens"] or 0) + (x["output_tokens"] or 0) for x in risk) / len(risk), 1)
turns_per_conv = a["turns"] / conv_total
allsvc = [
    {"component": "conversation service (AI jobs, measured)", "tokens_per_conversation": prod_total["total_tokens_per_conversation"], "basis": "usage/agent_live_*.jsonl, 180 conversations", "status": "measured"},
    {"component": "knowledge service: search embeddings (local bge-m3)", "tokens_per_conversation": round(searches * per_search_tokens / conv_total, 1) if searches else None,
     "basis": f"{searches} query embeddings in the time window of the three agent runs / {conv_total} conversations x {per_search_tokens} tokens each; the 15-conversation Arabizi sample ran in the same window", "status": "estimate (window count)"},
    {"component": "knowledge service: risk second opinion (OpenRouter free)", "tokens_per_conversation": round(turns_per_conv * risk_tokens, 1),
     "basis": f"{turns_per_conv:.2f} messages per conversation x {risk_tokens} tokens per check (measured on 40 messages); the evaluation ran with this second opinion switched off, so this is the cost if it is switched on", "status": "estimate"},
]
allsvc.append({"component": "TOTAL per conversation (all components)", "tokens_per_conversation": round(sum(r["tokens_per_conversation"] or 0 for r in allsvc), 1), "basis": "sum of the rows above", "status": "contains estimates"})
write("tokens_all_services.csv", allsvc)

# ---- one onboarding, copied from E5b ----
rows = list(csv.DictReader((OUT / "onboarding_measured.csv").open(encoding="utf-8")))
total = sum(int(r["total_tokens"]) for r in rows)
rows.append({"step": "ONE ONBOARDING, all steps", "total_tokens": total, "input_tokens": sum(int(r["input_tokens"]) for r in rows), "output_tokens": sum(int(r["output_tokens"]) for r in rows),
             "source": "onboarding_measured.csv", "tokens_reported_or_estimated": "reported by the servers"})
write("onboarding.csv", rows)

# ---- all tokens measured across the work, per service and run label ----
totals = defaultdict(lambda: {"calls": 0, "ok": 0, "input": 0, "output": 0, "first": "9", "last": "0"})
for f in sorted((RUNS / "usage").glob("*.jsonl")):
    for line_ in f.read_text(encoding="utf-8").splitlines():
        if not line_.strip():
            continue
        c = json.loads(line_)
        t = totals[(c["service"], c["run_label"] or f.stem)]
        t["calls"] += 1
        t["ok"] += c["status"] == "ok"
        t["input"] += c["input_tokens"] or 0
        t["output"] += c["output_tokens"] or 0
        t["first"], t["last"] = min(t["first"], c["ts"]), max(t["last"], c["ts"])
write("usage_totals.csv", [{"service": s, "run_label": l, "calls": t["calls"], "ok_calls": t["ok"], "input_tokens": t["input"], "output_tokens": t["output"], "total_tokens": t["input"] + t["output"],
                            "first_call_utc": t["first"][:16], "last_call_utc": t["last"][:16]} for (s, l), t in sorted(totals.items())])
print("headline:")
for r in rows if False else []:
    pass
print(open(OUT / "headline.csv", encoding="utf-8").read())
print(open(OUT / "safety.csv", encoding="utf-8").read())
