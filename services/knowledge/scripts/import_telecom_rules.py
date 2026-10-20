"""Convert the telecom business rules (Team B fixture format) into Team A rules, all as `proposed`.

Source: data/telecom/telco_001/fixtures/telco_001/rules.json. Output: data/telecom/telco_001/rules.json.
The source marks them "approved", but in Team A only an explicit `rules approve` (which runs the guardrail
gate) makes a rule active, so every imported rule starts as proposed and has no effect until reviewed.
Each quote must appear in the cited passage, the same grounding check the rule extractor applies.

Run from services/knowledge/:  python scripts/import_telecom_rules.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from team_a.config import settings  # noqa: E402
from team_a.knowledge.index import build_passages  # noqa: E402
from team_a.policy.rules_store import RuleStore  # noqa: E402
from team_a.schemas import Rule  # noqa: E402
from team_a.text import normalize  # noqa: E402

TENANT = "telco_001"


def main() -> None:
    src = settings.tenant_dir(TENANT) / "fixtures" / TENANT / "rules.json"
    raw = json.loads(src.read_text(encoding="utf-8"))
    passages = {p["citation"]: p for p in build_passages(TENANT)[0]}
    rules, problems = [], []
    for r in raw["rules"]:
        citation = r["citation"]
        document_id, rest = citation.split("@", 1)
        passage = passages.get(citation)
        if passage is None:
            problems.append(f"{r['rule_id']}: citation {citation} is not a passage")
            continue
        if normalize(r["quote"]) not in normalize(passage["text"]):
            problems.append(f"{r['rule_id']}: quote not found in {citation}")
        rules.append(Rule.model_validate({
            "rule_id": r["rule_id"], "tenant_id": TENANT,
            "source": {"document_id": document_id, "version": rest.split("#", 1)[0], "citation": citation,
                       "quote": r["quote"]},
            "scope": r["scope"], "action": r["action"], "applies_if": r["applies_if"],
            "conditions": r["conditions"], "effect": r["effect"], "else_effect": r["else_effect"],
            "user_message": r["user_message"], "approval_status": "proposed",
            "effective_date": r["effective_date"], "approved_by": None, "approved_at": None,
        }))
    if problems:
        sys.exit("Not imported:\n  " + "\n  ".join(problems))
    RuleStore(TENANT).save_all(rules)
    print(f"{len(rules)} telecom rules written to {settings.rules_file(TENANT)} as proposed "
          f"({len({r.action for r in rules})} actions); every quote is grounded in its passage")


if __name__ == "__main__":
    main()
