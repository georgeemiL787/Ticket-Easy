"""Guardrail cases: expected check_action decisions that any approved rule set must keep.

Used by `python -m team_a eval-guardrails` and as a gate in RuleStore.approve(): a proposed rule
is only approved if every case still passes with it active.
"""

import json
import shutil
import tempfile
from datetime import date
from pathlib import Path

from team_a.config import settings
from team_a.policy.check import check_action
from team_a.policy.risk import keyword_scan
from team_a.schemas import CheckActionRequest


class GuardrailRegression(ValueError):
    def __init__(self, rule_id: str, failures: list[tuple[str, str, list[str]]]):
        self.rule_id, self.failures = rule_id, failures
        detail = "; ".join(f"{cid} {title}: {', '.join(p)}" for cid, title, p in failures[:5])
        super().__init__(
            f"Approving {rule_id} would break {len(failures)} guardrail case(s): {detail}. "
            "Edit the rule (e.g. move the situation it covers into applies_if), or update the "
            "guardrail cases first if the policy change is intended."
        )


def load_cases(tenant_id: str) -> list[dict]:
    path = settings.data_dir / "benchmark" / f"guardrails_{tenant_id}.jsonl"
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def run_cases(store, cases: list[dict], ignore_absent: str | None = None):
    """Returns (failures, unsafe): failing (id, title, problems) and ids wrongly allowed.

    `ignore_absent` skips the "this rule must not be evaluated" check for one rule, since that
    check tests the review workflow and stops being meaningful once the rule is being approved.
    """
    failures, unsafe = [], []
    for case in cases:
        risk_categories = sorted(keyword_scan(case.get("message", ""))[0])
        req = CheckActionRequest.model_validate({
            "request_id": case["id"],
            "tenant_id": store.tenant_id,
            "as_of": case.get("as_of", date.today().isoformat()),
            "risk_categories": risk_categories,
            **case["request"],
        })
        decision = check_action(req, store)
        expect = case["expect"]
        problems = []
        if decision.decision != expect["decision"]:
            problems.append(f"decision {decision.decision} != {expect['decision']}")
            if decision.decision == "allow":
                unsafe.append(case["id"])
        if "reason_code" in expect and decision.reason_code != expect["reason_code"]:
            problems.append(f"reason {decision.reason_code} != {expect['reason_code']}")
        evaluated = [o.rule_id for o in decision.rule_outcomes]
        if "rule_id" in expect and expect["rule_id"] not in evaluated:
            problems.append(f"rule {expect['rule_id']} not evaluated")
        absent = expect.get("absent_rule_id")
        if absent and absent != ignore_absent and absent in evaluated:
            problems.append(f"inactive rule {absent} was evaluated")
        if decision.decision == "deny" and decision.reason_code.startswith("RULE") and not decision.citations:
            problems.append("deny without citation")
        if "mandatory_escalation" in expect and bool(risk_categories) != expect["mandatory_escalation"]:
            problems.append(f"risk {risk_categories} escalation != {expect['mandatory_escalation']}")
        if problems:
            failures.append((case["id"], case["title"], problems))
    return failures, unsafe


def failures_if_approved(store, rule_id: str, approved_rules: list) -> list[tuple[str, str, list[str]]]:
    """Run the tenant's guardrail cases against `approved_rules` in a scratch copy of the store."""
    cases = load_cases(store.tenant_id)
    if not cases:
        return []
    with tempfile.TemporaryDirectory() as tmp:
        scratch = type(store)(store.tenant_id, path=Path(tmp) / "rules.json")
        if store.path.exists():
            shutil.copy(store.path, scratch.path)
        scratch.save_all(approved_rules)
        return run_cases(scratch, cases, ignore_absent=rule_id)[0]
