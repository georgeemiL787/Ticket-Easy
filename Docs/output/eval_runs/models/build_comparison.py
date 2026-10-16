"""Builds models/comparison.csv from the per-message files (nlu_<slug>.jsonl), the usage logs and the rules output."""
import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

RUNS = Path("Docs/output/eval_runs")
MODELS = [("gpt-oss-20b", "openai/gpt-oss-20b", "Groq"), ("qwen3.8-27b", "qwen/qwen3.8-27b", "Groq"), ("gpt-oss-120b", "openai/gpt-oss-120b", "Groq")]
STYLES = ("en", "ar", "mixed", "arabizi")


def pct(num, den):
    return round(100 * num / den, 1) if den else None


def nearest_rank(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]


rows = []
for slug, model, provider in MODELS:
    per_file = RUNS / f"models/nlu_{slug}.jsonl"
    usage_file = RUNS / f"usage/nlu_{slug}.jsonl"
    if not per_file.exists():
        if provider == "Groq":
            rows.append({"provider": provider, "model": model, "status": "incomplete: no result file"})
        continue
    msgs = [json.loads(l) for l in per_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_id = {m["id"]: m for m in msgs}
    for retry in sorted((RUNS / "models").glob(f"nlu_{slug}_retry*.jsonl")):  # messages the model could not answer the first time
        for line in retry.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line)["method"] == "llm":
                by_id[json.loads(line)["id"]] = json.loads(line)
    msgs = list(by_id.values())
    by_style = defaultdict(lambda: [0, 0])
    for m in msgs:
        by_style[m["language"]][1] += 1
        by_style[m["language"]][0] += bool(m["correct"])
    calls = []
    for uf in [usage_file, *sorted((RUNS / "usage").glob(f"nlu_{slug}_retry*.jsonl"))]:
        calls += [json.loads(l) for l in uf.read_text(encoding="utf-8").splitlines() if l.strip()] if uf.exists() else []
    ok = [c for c in calls if c["status"] == "ok"]
    answered = [m for m in msgs if m["method"] == "llm"]
    row = {
        "provider": provider, "model": model, "mode": "AI reader (eval-nlu --mode llm)", "messages": len(msgs),
        "intent_accuracy_pct": pct(sum(m["correct"] for m in msgs), len(msgs)),
        **{f"intent_accuracy_{s}_pct": pct(*by_style[s]) if by_style[s][1] else None for s in STYLES},
        "ai_answered_messages": len(answered),
        "rules_fallback_messages": sum(m["method"] == "rules_fallback" for m in msgs),
        "intent_accuracy_when_ai_answered_pct": pct(sum(m["correct"] for m in answered), len(answered)),
        "calls": len(calls), "ok_calls": len(ok), "failed_or_rate_limited_calls": len(calls) - len(ok),
        "latency_p50_ms": nearest_rank([c["latency_ms"] for c in ok], 50), "latency_p95_ms": nearest_rank([c["latency_ms"] for c in ok], 95),
        "avg_input_tokens_per_call": round(sum(c["input_tokens"] or 0 for c in ok) / len(ok), 1) if ok else None,
        "avg_output_tokens_per_call": round(sum(c["output_tokens"] or 0 for c in ok) / len(ok), 1) if ok else None,
        "date": date.today().isoformat(), "status": "complete" if len(answered) == 155 else f"incomplete: the AI answered {len(answered)} of 155 messages (the rest fell back to the rules, so the overall accuracy is not the model's)",
        "note": ("latency measured on the Groq free tier" if provider == "Groq" else "latency measured on the Google free tier") + ("; the failed or rate-limited calls are from a first pass that ran into the daily token limit: 67 messages were re-run on another free account and merged" if any((RUNS / "models").glob(f"nlu_{slug}_retry*.jsonl")) else ""),
    }
    rows.append(row)

text = (RUNS / "conversation/nlu_rules.txt").read_text(encoding="utf-8")
overall = re.search(r"intent accuracy \(first intent\)\s+([\d.]+)%\s+\((\d+)/(\d+)\)", text)
style_acc = {s: float(re.search(rf"^\s*{s}\s+([\d.]+)%", text, re.M).group(1)) for s in STYLES}
rows.append({
    "provider": "none (local rules)", "model": "rule-based reader", "mode": "rules (eval-nlu --mode rules)", "messages": int(overall.group(3)),
    "intent_accuracy_pct": float(overall.group(1)), **{f"intent_accuracy_{s}_pct": style_acc[s] for s in STYLES},
    "calls": 0, "date": "2026-10-16", "status": "complete", "note": "reference; no AI model, no tokens",
})
rows.sort(key=lambda r: -(r.get("intent_accuracy_pct") or 0))
fields = list(dict.fromkeys(k for r in rows for k in r))
with (RUNS / "models/comparison.csv").open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
for r in rows:
    print({k: r.get(k) for k in ("model", "intent_accuracy_pct", "intent_accuracy_en_pct", "intent_accuracy_ar_pct", "intent_accuracy_mixed_pct", "intent_accuracy_arabizi_pct", "rules_fallback_messages", "failed_or_rate_limited_calls", "latency_p50_ms", "latency_p95_ms", "avg_input_tokens_per_call", "avg_output_tokens_per_call", "status")})
