"""E5b tables from the usage logs (standard library). Every row traces to a usage file named in the 'source' column."""
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

RUNS = Path("docs/reports/eval_runs")


def read(name):
    return [json.loads(l) for l in (RUNS / "usage" / name).read_text(encoding="utf-8").splitlines() if l.strip()]


def pctl(values, q):
    if not values:
        return None
    v = sorted(values)
    return v[max(0, math.ceil(q / 100 * len(v)) - 1)]


def summarise(file, jobs=None, label=None):
    groups = defaultdict(list)
    for r in read(file):
        if jobs and r["job"] not in jobs:
            continue
        groups[(r["service"], r["job"], r["model"])].append(r)
    out = []
    for (service, job, model), items in groups.items():
        ok = [i for i in items if i["status"] == "ok"]
        tin = sum(i["input_tokens"] or 0 for i in items)
        tout = sum(i["output_tokens"] or 0 for i in items)
        out.append({
            "step": label, "service": service, "job": job, "model": model, "source": f"usage/{file}",
            "calls": len(items), "ok_calls": len(ok), "failed_or_rate_limited_calls": len(items) - len(ok),
            "input_tokens": tin, "output_tokens": tout, "total_tokens": tin + tout,
            "avg_input_tokens_per_ok_call": round(sum(i["input_tokens"] or 0 for i in ok) / len(ok), 1) if ok else None,
            "avg_output_tokens_per_ok_call": round(sum(i["output_tokens"] or 0 for i in ok) / len(ok), 1) if ok else None,
            "latency_p50_ms": pctl([i["latency_ms"] for i in ok], 50), "latency_p95_ms": pctl([i["latency_ms"] for i in ok], 95),
            "tokens_reported_or_estimated": "estimated" if any(i.get("estimated") for i in items) else "reported by the server",
        })
    return out


def write(path, rows):
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


onboarding = (
    summarise("onboarding_tools.jsonl", label="tool generation (tool service, Groq free tier)")
    + summarise("onboarding_rules.jsonl", label="rule extraction (knowledge service, OpenRouter free)")
    + summarise("onboarding_embed.jsonl", label="document embeddings (knowledge service, local bge-m3)")
)
write(RUNS / "tables/onboarding_measured.csv", onboarding)

per_message = (
    summarise("knowledge_retrieval.jsonl", jobs={"embed_query"}, label="search query (embedding, local bge-m3), retrieval evaluation runs")
    + summarise("knowledge_risk.jsonl", label="risk second opinion (OpenRouter free), 40 sample messages")
)
write(RUNS / "tables/knowledge_per_message.csv", per_message)
for name, rows in (("onboarding_measured", onboarding), ("knowledge_per_message", per_message)):
    print("==", name)
    for r in rows:
        print(" ", r["step"][:46].ljust(46), r["job"].ljust(20), "calls", r["calls"], "in", r["input_tokens"], "out", r["output_tokens"], "total", r["total_tokens"])
