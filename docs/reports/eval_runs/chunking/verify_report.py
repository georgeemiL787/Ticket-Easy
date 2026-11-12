"""Check reports/rag_evaluation_and_chunking_comparison.md against the files its numbers come from.

  python docs/reports/eval_runs/chunking/verify_report.py

1. Every line inside a "BEGIN GENERATED" block must appear in tables_keyword_only.md.
2. The existing-results table (BEGIN VERIFIED: existing_retrieval) must match knowledge/summary.json.
3. Other figures quoted in the text are recomputed from their source files.
Exits non-zero on any mismatch.
"""

import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
REPORT = ROOT / "reports" / "rag_evaluation_and_chunking_comparison.md"
RUNS = ROOT / "docs" / "reports" / "eval_runs"
problems: list[str] = []


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        problems.append(what)


def blocks(text: str, kind: str) -> list[list[str]]:
    return [b.strip().splitlines() for b in re.findall(rf"<!-- BEGIN {kind}[^>]*-->(.*?)<!-- END {kind} -->", text, re.S)]


def main() -> int:
    report = REPORT.read_text(encoding="utf-8")
    generated = set((HERE / "tables_keyword_only.md").read_text(encoding="utf-8").splitlines())
    lines = [l for b in blocks(report, "GENERATED") for l in b if l.strip()]
    missing = [l for l in lines if l not in generated]
    check(bool(lines) and not missing, f"{len(lines)} generated lines copied verbatim" + (f"; missing: {missing[:3]}" if missing else ""))

    summary = json.loads((RUNS / "knowledge" / "summary.json").read_text(encoding="utf-8"))["retrieval"]
    rows = [l for b in blocks(report, "VERIFIED") for l in b if l.startswith("| ") and not l.startswith("| Tenant")]
    for line in rows:
        cells = [c.strip() for c in line.strip("|").split("|")]
        tenant, split = cells[0], cells[1]
        s = summary[tenant][split]
        want = [f"{s['questions']} ({s['unanswerable']})", str(s["recall_at_5_pct"]), str(s["no_answer_precision_pct"]),
                str(s["no_answer_recall_pct"]), s["per_style_hits"]["ar"], s["per_style_hits"]["arabizi"], s["per_style_hits"]["en"]]
        check(cells[2:] == want, f"existing retrieval {tenant} {split}")

    headline = {r["measure"]: r for r in csv.DictReader((RUNS / "tables" / "headline.csv").open(encoding="utf-8"))}
    check(headline["citation accuracy"]["agent_mode"] == "70.7 (53/75)" and "70.7% (53/75" in report, "citation accuracy 70.7% (53/75)")
    check(headline["citation accuracy"]["rules_only_before_fix"] == "44.0 (33/75)", "citation accuracy rules 44.0% (33/75)")
    check(headline["unbacked facts"]["agent_mode"] == "0", "unbacked facts 0")

    replay = list(csv.DictReader((RUNS / "models" / "replay_comparison.csv").open(encoding="utf-8")))
    g = lambda job: sorted(float(r["grounded_pct"]) for r in replay if r["job"] == job and r["grounded_pct"])  # noqa: E731
    check(g("knowledge")[0] == 88.6 and g("knowledge")[-1] == 100.0, "replay grounded knowledge 88.6-100")
    check(g("writer")[0] == 93.3 and g("writer")[-1] == 100.0, "replay grounded writer 93.3-100")

    usage = [json.loads(l) for l in (RUNS / "usage" / "knowledge_retrieval.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    lat = sorted(u["latency_ms"] for u in usage if u.get("job") == "embed_query" and u.get("status") == "ok")
    check((len(lat), lat[len(lat) // 2], lat[int(len(lat) * 0.95)]) == (403, 103, 142), "bge-m3 embed_query n=403 p50 103 p95 142")

    per_q = [json.loads(l) for l in (HERE / "per_question_keyword_only.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    by = defaultdict(dict)
    for r in per_q:
        if r["split"] == "test" and r["thresholds"] == "shipped":
            by[(r["tenant"], r["id"])][r["strategy"]] = r
    misses = [d for d in by.values() if d["structure"]["answerable"] and not d["structure"]["hit"]]
    empty = sum(d["structure"]["returned"] == 0 for d in misses)
    nobody = sum(not any(r["hit"] for r in d.values()) for d in misses)
    false_pos = sum(not d["structure"]["answerable"] and d["structure"]["returned"] > 0 for d in by.values())
    check(len(misses) == 30 and empty == 15, f"structure misses {len(misses)} (empty {empty})")
    check(f"**{nobody} of the 30 are missed by every strategy" in report, f"missed by every strategy: {nobody}")
    check(false_pos == 16 and "(16 of 45 under `structure`)" in report, f"off-topic returning passages: {false_pos}")

    results = json.loads((HERE / "results_keyword_only.json").read_text(encoding="utf-8"))
    for t, v in results["tenants"].items():
        for split in ("dev", "test"):
            off, har = v["production_check"][split], v["strategies"]["structure"][split]
            check(abs(off["recall_at_k"] - har["hit_at_5"]) < 1e-9 and off["per_style"] == har["per_style"],
                  f"harness == run_retrieval {t} {split}")

    print(f"\n{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
