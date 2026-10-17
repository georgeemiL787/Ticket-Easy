"""Orchestrator comparison table: the same 25 English policy / refund conversations (27 turns), real knowledge service.

gpt-oss-120b is the saved baseline run (diagnosis/live_en_policy.jsonl, usage/diag_live_policy.jsonl); the others are
diagnosis/orch_<slug>.jsonl with usage/orch_<slug>.jsonl. Only the orchestrator job's model differs. Standard library."""
import csv
import json
import math
from pathlib import Path

RUNS = Path("Docs/output/eval_runs")
MODELS = [
    ("gpt-oss-120b", "openai/gpt-oss-120b", "diagnosis/live_en_policy.jsonl", "usage/diag_live_policy.jsonl"),
    ("qwen3.8-27b", "qwen/qwen3.8-27b", "diagnosis/orch_qwen3.8-27b.jsonl", "usage/orch_qwen3.8-27b.jsonl"),
    ("gpt-oss-20b", "openai/gpt-oss-20b", "diagnosis/orch_gpt-oss-20b.jsonl", "usage/orch_gpt-oss-20b.jsonl"),
]


def load(path):
    return [json.loads(l) for l in (RUNS / path).read_text(encoding="utf-8").splitlines() if l.strip()]


def pctl(v, q):
    v = sorted(v)
    return v[max(0, math.ceil(q / 100 * len(v)) - 1)] if v else None


rows = []
for slug, model, turns_file, usage_file in MODELS:
    if not (RUNS / turns_file).exists():
        rows.append({"orchestrator_model": model, "status": "not run"})
        continue
    turns = [t for t in load(turns_file) if "turn" in t]
    cit = [t for t in turns if t["gold"]["citations_any"]]
    hit = [t for t in cit if set(t["gold"]["citations_any"]) & set(t["citations"])]
    all_calls = load(usage_file)
    usage = [u for u in all_calls if u["job"] == "orchestrator"]
    ok = [u for u in usage if u["status"] == "ok"]
    fallback = [t for t in turns if t["fallback"]]
    rows.append({
        "orchestrator_model": model, "conversations": len({t["conversation"] for t in turns}), "turns": len(turns),
        "citation_turns": len(cit), "citation_right": len(hit), "citation_right_pct": round(100 * len(hit) / len(cit), 1) if cit else None,
        "turns_with_a_failed_check": sum(1 for t in turns if t["wrong"]),
        "intent_checks_failed": sum(1 for t in turns if any(w.startswith("intent") for w in t["wrong"])),
        "decision_checks_failed": sum(1 for t in turns if any(w.startswith("decision") for w in t["wrong"])),
        "fallback_turns": len(fallback), "unbacked_fact_turns": sum(1 for t in turns if t["unbacked"]),
        "ai_requests_per_turn_all_jobs": round(len(all_calls) / len(turns), 1), "total_tokens_per_turn_all_jobs": round(sum((u["input_tokens"] or 0) + (u["output_tokens"] or 0) for u in all_calls) / len(turns)),
        "turn_time_p50_s": round(pctl([t["latency_ms"] for t in turns], 50) / 1000, 1),
        "turn_time_p95_s": round(pctl([t["latency_ms"] for t in turns], 95) / 1000, 1),
        "orchestrator_calls_ok": len(ok), "orchestrator_calls_failed_or_rate_limited": len(usage) - len(ok),
        "orchestrator_tokens_per_turn": round(sum((u["input_tokens"] or 0) + (u["output_tokens"] or 0) for u in ok) / len(turns)),
        "status": "complete" if not fallback else f"{len(fallback)} turns fell back to the fixed flow (rate limits or move errors)",
        "note": "latency on the Groq free tier; same 25 conversations, one orchestrator model changed",
    })
fields = list(dict.fromkeys(k for r in rows for k in r))
with (RUNS / "models/orchestrator_comparison.csv").open("w", encoding="utf-8", newline="") as h:
    w = csv.DictWriter(h, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
for r in rows:
    print({k: r.get(k) for k in ("orchestrator_model", "citation_right", "citation_turns", "turns_with_a_failed_check", "decision_checks_failed", "fallback_turns", "unbacked_fact_turns", "ai_requests_per_turn_all_jobs", "turn_time_p50_s", "orchestrator_tokens_per_turn", "status")})
