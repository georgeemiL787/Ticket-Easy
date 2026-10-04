from datetime import date

import pytest
from pydantic import ValidationError

from team_a.evaluation import eval_guardrails
from team_a.policy.check import check_action
from team_a.policy.explain import explain_rule
from team_a.policy.rules_store import RuleStore
from team_a.schemas import CheckActionRequest, Rule

AS_OF = date(2026, 9, 28)


def refund(delivered_at="2026-09-08", amount=800, **overrides) -> CheckActionRequest:
    body = {
        "request_id": "r1",
        "tenant_id": "shop_001",
        "as_of": AS_OF,
        "tool": {"name": "create_refund", "operation_kind": "create", "risk": "high"},
        "facts": {"order_status": "delivered", "delivered_at": delivered_at},
        "arguments": {"amount": amount},
        "identity": {"verified": True, "customer_id": "C-1"},
    }
    body.update(overrides)
    return CheckActionRequest.model_validate(body)


def test_demo_day_20_refund_is_denied_with_citation_and_message():
    decision = check_action(refund(), RuleStore("shop_001"))
    assert decision.decision == "deny"
    assert decision.reason_code == "RULE_BLOCKED"
    assert decision.citations == ["refund_policy@v1#s1"]
    assert "14" in decision.user_message.ar
    assert "R-REFUND-14D" in decision.rationale


def test_guardrail_benchmark_passes_with_zero_unsafe_actions():
    assert eval_guardrails("shop_001")


def test_unapproved_rules_have_no_effect(rule_store):
    rules = rule_store.all()
    draft = rules[0].model_copy(update={
        "rule_id": "P-DRAFT-BLOCK-ALL", "action": "create_ticket", "conditions": [],
        "effect": "deny", "approval_status": "proposed", "approved_by": None, "approved_at": None,
    })
    rule_store.add_proposed([draft])
    req = CheckActionRequest.model_validate({
        "request_id": "r", "tenant_id": "shop_001", "as_of": AS_OF,
        "tool": {"name": "create_ticket", "operation_kind": "create", "risk": "low"},
        "identity": {"verified": True},
    })
    assert check_action(req, rule_store).decision == "allow"
    rule_store.approve("P-DRAFT-BLOCK-ALL", reviewer="tester")
    assert check_action(req, rule_store).decision == "deny"


def test_edit_sends_rule_back_to_review(rule_store):
    edited = rule_store.edit("R-REFUND-LIMIT", {"conditions": [
        {"field": "amount", "op": "<=", "value": 5000, "from": "arguments"}]})
    assert edited.approval_status == "proposed" and edited.approved_by is None
    decision = check_action(refund(delivered_at="2026-09-23", amount=4500), rule_store)
    assert "R-REFUND-LIMIT" not in [o.rule_id for o in decision.rule_outcomes]


def test_extractor_output_can_never_arrive_approved(rule_store):
    approved = rule_store.get("R-RETURN-14D").model_copy(update={"rule_id": "P-SNEAKY"})
    added = rule_store.add_proposed([approved])
    assert added[0].approval_status == "proposed"


def test_approved_rule_must_record_reviewer():
    with pytest.raises(ValidationError):
        Rule.model_validate({**RuleStore("shop_001").get("R-RETURN-14D").model_dump(), "approved_by": None})


def test_future_effective_date_is_not_active(rule_store):
    rules = rule_store.all()
    future = [r.model_copy(update={"effective_date": date(2027, 1, 1)}) if r.rule_id == "R-REFUND-14D" else r
              for r in rules]
    rule_store.save_all(future)
    ids = [o.rule_id for o in check_action(refund(), rule_store).rule_outcomes]
    assert "R-REFUND-14D" not in ids


def test_explain_rule():
    exp = explain_rule(RuleStore("shop_001"), "R-REFUND-14D", AS_OF)
    assert exp.active and exp.source.citation == "refund_policy@v1#s1"
    assert "days_since_delivery <= 14" in exp.summary and "Applies only when" in exp.summary
    assert not explain_rule(RuleStore("shop_001"), "R-DEFECT-48H", AS_OF).active
