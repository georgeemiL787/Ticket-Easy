"""Orchestrator comparison on Arabic and mixed policy / refund conversations (25 each, real knowledge service).
qwen3.8-27b and gpt-oss-20b only: the gpt-oss-120b run for these languages is pending (its daily token quota)."""
import csv
import json
import math
from pathlib import Path

RUNS = Path("Docs/output/eval_runs")


def load(path):
    return [json.loads(l) for l in (RUNS / path).read_text(encoding="utf-8").splitlines() if l.strip()]


def pctl(v, q):
    v = sorted(v)
    return v[max(0, math.ceil(q / 100 * len(v)) - 1)] if v else None


rows = []
for lang in ("ar", "mixed"):
    for slug, model in (("qwen3.8-27b", "qwen/qwen3.8-27b"), ("gpt-oss-20b", "openai/gpt-oss-20b"), ("gpt-oss-120b", "openai/gpt-oss-120b")):
        f = RUNS / f"diagnosis/orch_{lang}_{slug}.jsonl"
        if not f.exists():
            rows.append({"language": lang, "orchestrator_model": model, "status": "pending: not run yet (daily token quota of this model)"})
            continue
        turns = [t for t in load(f"diagnosis/orch_{lang}_{slug}.jsonl") if "turn" in t]
        usage = load(f"usage/orch_{lang}_{slug}.jsonl")
        orch_ok = [u for u in usage if u["job"] == "orchestrator" and u["status"] == "ok"]
        cit = [t for t in turns if t["gold"]["citations_any"]]
        ok = lambda t: bool(set(t["gold"]["citations_any"]) & set(t["citations"]))
        agent = [t for t in cit if not t["fallback"]]
        fb = sum(1 for t in turns if t["fallback"])
        rows.append({
            "language": lang, "orchestrator_model": model, "conversations": len({t["conversation"] for t in turns}), "turns": len(turns),
            "citation_right_all_turns": f"{sum(ok(t) for t in cit)}/{len(cit)}", "citation_right_agent_turns_only": f"{sum(ok(t) for t in agent)}/{len(agent)}",
            "turns_with_a_failed_check": sum(1 for t in turns if t["wrong"]), "decision_checks_failed": sum(1 for t in turns if any(w.startswith("decision") for w in t["wrong"])),
            "fallback_turns": fb, "unbacked_fact_turns": sum(1 for t in turns if t["unbacked"]),
            "ai_requests_per_turn_all_jobs": round(len(usage) / len(turns), 1),
            "orchestrator_tokens_per_turn": round(sum((u["input_tokens"] or 0) + (u["output_tokens"] or 0) for u in orch_ok) / len(turns)),
            "turn_time_p50_s": round(pctl([t["latency_ms"] for t in turns], 50) / 1000, 1),
            "status": "complete" if fb == 0 else f"{fb} of {len(turns)} turns fell back to the fixed flow (rate limits): read the agent-turns-only column",
            "note": "latency on the Groq free tier; same 25 conversations, only the orchestrator model changed",
        })
fields = list(dict.fromkeys(k for r in rows for k in r))
with (RUNS / "models/orchestrator_comparison_ar_mixed.csv").open("w", encoding="utf-8", newline="") as h:
    w = csv.DictWriter(h, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
for r in rows:
    print({k: r.get(k) for k in ("language", "orchestrator_model", "citation_right_all_turns", "citation_right_agent_turns_only", "turns_with_a_failed_check", "fallback_turns", "orchestrator_tokens_per_turn", "turn_time_p50_s", "status")})
