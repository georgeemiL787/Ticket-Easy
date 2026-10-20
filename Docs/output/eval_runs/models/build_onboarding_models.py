"""Onboarding steps on other models: tool generation (tool service, Groq free tier) and rule extraction (knowledge service,
OpenRouter free models). Reads the saved outputs and usage logs; writes two CSV files in models/."""
import csv
import json
import re
from pathlib import Path

RUNS = Path("Docs/output/eval_runs")
TG = RUNS / "knowledge/tool_generation"


def jsons(path):
    txt, dec, i, out = path.read_text(encoding="utf-8"), json.JSONDecoder(), 0, []
    while (j := txt.find("{", i)) >= 0:
        try:
            o, e = dec.raw_decode(txt[j:])
            out.append(o)
            i = j + e
        except Exception:
            i = j + 1
    return out


def usage(path):
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


attempts = [
    ("gpt-oss-120b", "try 1", RUNS / "knowledge/onboarding_tools_live_smoke.txt", RUNS / "usage/onboarding_tools.jsonl"),
    ("gpt-oss-120b", "try 2", TG / "gpt-oss-120b_run2.txt", RUNS / "usage/onboarding_tools_gpt-oss-120b_run2.jsonl"),
    ("qwen3.8-27b", "try 1", TG / "qwen3.8-27b.txt", RUNS / "usage/onboarding_tools_qwen3.8-27b.jsonl"),
    ("qwen3.8-27b", "try 2", TG / "qwen3.8-27b_try2.txt", RUNS / "usage/onboarding_tools_qwen3.8-27b_try2.jsonl"),
    ("qwen3.8-27b", "try 3", TG / "qwen3.8-27b_try3.txt", RUNS / "usage/onboarding_tools_qwen3.8-27b_try3.jsonl"),
    ("gpt-oss-20b", "try 1", TG / "gpt-oss-20b.txt", RUNS / "usage/onboarding_tools_gpt-oss-20b.jsonl"),
    ("gpt-oss-20b", "try 2", TG / "gpt-oss-20b_try2.txt", RUNS / "usage/onboarding_tools_gpt-oss-20b_try2.jsonl"),
    ("gpt-oss-20b", "try 3", TG / "gpt-oss-20b_try3.txt", RUNS / "usage/onboarding_tools_gpt-oss-20b_try3.jsonl"),
]
rows = []
for model, tries, out, log in attempts:
    objs = jsons(out)
    report = next(o for o in objs if o.get("live"))
    proposals = [o for o in objs if "proposal_id" in o]
    u = usage(log)
    ok = report.get("error") is None
    msg = report.get("message", "")
    reason = "" if ok else ("the model's JSON did not match the required structure" if "does not match" in msg else "Groq could not generate valid JSON for the required structure" if "Failed to generate JSON" in msg else msg[:120])
    rows.append({"model": model, "attempt": tries, "valid_tool_proposals": len(proposals) if ok else 0, "result": "accepted" if ok else "rejected", "why_rejected": reason,
                 "seconds": report.get("seconds"), "input_tokens": sum(x["input_tokens"] or 0 for x in u), "output_tokens": sum(x["output_tokens"] or 0 for x in u),
                 "tool_idea": (proposals[0]["content"].get("name") if proposals else ""), "note": "" if ok else "no tokens are reported for a rejected request"})
summary = []
for model in ("gpt-oss-120b", "qwen3.8-27b", "gpt-oss-20b"):
    mine = [r for r in rows if r["model"] == model]
    summary.append({"model": model, "attempts": len(mine), "accepted": sum(r["result"] == "accepted" for r in mine), "typical_tokens_of_an_accepted_run": next((r["input_tokens"] + r["output_tokens"] for r in mine if r["result"] == "accepted"), ""),
                    "same_tool_idea_when_accepted": "; ".join(sorted({r["tool_idea"] for r in mine if r["tool_idea"]})), "scope": "example e-commerce API with only 2 operations; Groq free tier"})
with (RUNS / "models/onboarding_models_tools.csv").open("w", encoding="utf-8", newline="") as h:
    w = csv.DictWriter(h, fieldnames=list(summary[0]))
    w.writeheader()
    w.writerows(summary)
with (RUNS / "models/onboarding_models_tools_attempts.csv").open("w", encoding="utf-8", newline="") as h:
    w = csv.DictWriter(h, fieldnames=list(rows[0]))
    w.writeheader()
    w.writerows(rows)

# rule extraction
rr = []
for label, result_file, log_file in (
    ("nvidia/nemotron-3-super-120b-a12b:free (measured first)", "knowledge/onboarding_rules_result.json", "usage/onboarding_rules.jsonl"),
    ("nvidia/nemotron-3-ultra-550b-a55b:free", "knowledge/onboarding_rules_result_nemotron-ultra.json", "usage/onboarding_rules_nemotron-ultra.jsonl"),
    ("google/gemma-4-26b-a4b-it:free", "knowledge/onboarding_rules_result_gemma-26b.json", "usage/onboarding_rules_gemma-26b.jsonl"),
):
    u = usage(RUNS / log_file)
    ok = [x for x in u if x["status"] == "ok"]
    res = json.loads((RUNS / result_file).read_text(encoding="utf-8")) if (RUNS / result_file).exists() else None
    lat = sorted(x["latency_ms"] for x in ok)
    reason = {"nemotron-ultra": "every request returned a response without a usable answer ('choices' missing), including all retries of the first passage; stopped after the first passage",
              "gemma-26b": "every request was refused with HTTP 429 (rate-limited upstream), including all retries of the first passage; stopped after the first passage"}
    key = "nemotron-ultra" if "ultra" in label else "gemma-26b" if "gemma" in label else None
    rr.append({"model": label, "passages": res["total_passages"] if res else "", "passages_failed_after_retries": len(res["passages_failed_after_retries"]) if res else "could not complete",
               "candidate_rules": res["total_candidate_rules"] if res else "", "requests": len(u), "ok_requests": len(ok), "failed_or_rate_limited_requests": len(u) - len(ok),
               "input_tokens": sum(x["input_tokens"] or 0 for x in u), "output_tokens": sum(x["output_tokens"] or 0 for x in u), "latency_p50_ms": lat[len(lat) // 2] if lat else "",
               "per_document": ", ".join(f"{k}: {v['candidate_rules']}" for k, v in res["per_document"].items()) if res else "",
               "note": ("candidate rules are proposals only: nothing was stored or approved, and their quality was not judged" if res else reason.get(key, ""))})
with (RUNS / "models/onboarding_models_rules.csv").open("w", encoding="utf-8", newline="") as h:
    w = csv.DictWriter(h, fieldnames=list(rr[0]))
    w.writeheader()
    w.writerows(rr)
for r in summary + rr:
    print({k: v for k, v in r.items() if k not in ("note", "scope")})
