"""Build mock/telco_scenarios.json for Team A eval-scenarios (policy_question + no_evidence)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAQ = ROOT / "data/telecom/telco_001/faq.json"
PASSAGES = ROOT / "var/index/telco_001/passages.jsonl"
OUT = ROOT / "data/telecom/telco_001/mock/telco_scenarios.json"
BENCH_TEST = ROOT / "data/telecom/telco_001/benchmark/retrieval.test.jsonl"


def citation_to_section() -> dict[str, str]:
    mapping = {}
    if not PASSAGES.exists():
        # Fall back to building from corpus via team_a if index missing
        import sys
        sys.path.insert(0, str(ROOT / "src"))
        from team_a.knowledge.index import build_passages
        passages, _ = build_passages("telco_001")
        for p in passages:
            if p["current"]:
                mapping[p["citation"]] = p["section"]
        return mapping
    for line in PASSAGES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        mapping[row["citation"]] = row["section"]
    return mapping


def main() -> None:
    cite_map = citation_to_section()
    faq = json.loads(FAQ.read_text(encoding="utf-8"))
    scenarios = []
    # One policy_question per FAQ in three languages (cap at 60 FAQ → 180 would be huge;
    # take en only for volume parity-ish, plus ar/arabizi every 3rd).
    for i, item in enumerate(faq):
        citation = item["citation"]
        section = cite_map.get(citation)
        if not section:
            raise SystemExit(f"no section title for {citation}")
        for lang, qkey in (("en", "question_en"), ("ar", "question_ar"), ("arabizi", "question_arabizi")):
            if lang != "en" and i % 3 != 0:
                continue
            scenarios.append({
                "scenario_id": f"TS-{item['faq_id']}-{lang}",
                "type": "policy_question",
                "language": lang,
                "customer_id": "TC-D001",
                "customer_message": item[qkey],
                "expected": {
                    "decision": "answer",
                    "policy_sections": [section],
                },
                "test_purpose": f"FAQ {item['faq_id']} policy answer",
            })

    # no_evidence from clean off-topic + unauthorized noise on test
    for line in BENCH_TEST.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("expected"):
            continue
        rid = row["id"]
        if not (rid.startswith("OFF-clean-") or rid.startswith("NOISE-unauth-") or rid.startswith("OFF-")):
            continue
        # Keep a sample: all OFF-clean + all NOISE-unauth + original OFF-* odds only
        if rid.startswith("OFF-") and not rid.startswith("OFF-clean-") and not rid.endswith(("02", "04", "06", "08")):
            # keep even numbered OFF already filtered by endswith
            pass
        scenarios.append({
            "scenario_id": f"TS-{rid}",
            "type": "policy_question",
            "language": row["style"] if row["style"] != "arabizi" else "arabizi",
            "customer_id": "TC-D001",
            "customer_message": row["question"],
            "expected": {"decision": "no_evidence", "policy_sections": []},
            "test_purpose": "unanswerable / unauthorized / off-topic",
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_meta": {
            "tenant_id": "telco_001",
            "as_of": "2026-10-08",
            "currency": "EGP",
            "fictional": True,
            "note": "Team A eval-scenarios for telecom. Orchestrator T*.json scenarios remain pending.",
        },
        "scenarios": scenarios,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(scenarios)} scenarios -> {OUT}")


if __name__ == "__main__":
    main()
