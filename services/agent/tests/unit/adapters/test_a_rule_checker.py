"""Rule checker stand-in: every step of the evaluation order, and an allow and a fail case for each rules.json rule."""

import json
import shutil
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from team_b.adapters.standins.rule_checker import RuleCheckerStandin, derive_facts
from team_b.config import Settings
from team_b.container import Container, inject
from team_b.contracts.errors import UpstreamError
from team_b.contracts.policy import CheckActionRequest, PolicyDecision
from team_b.ports import PolicyGate

FIXTURES = Settings().fixtures_dir
T = "shop_001"
AS_OF = date(2026, 9, 28)
RULES = json.loads((FIXTURES / T / "rules.json").read_text(encoding="utf-8"))["rules"]
APPROVAL = {"approved_by": "agent_7", "case_id": "case_1", "approved_at": datetime(2026, 9, 28, 12, tzinfo=UTC)}

# action -> (operation kind, risk, facts and arguments under which every rule of the action passes)
BASELINE: dict[str, tuple[str, str, dict[str, Any], dict[str, Any]]] = {
    "create_return": (
        "create", "medium",
        {"days_since_delivery": 3, "product_category": "shirts", "is_clearance": False, "item_condition": "new"}, {},
    ),
    "create_exchange": ("create", "medium", {"days_since_delivery": 3}, {}),
    "create_refund": ("create", "high", {"order_status": "delivered", "days_since_delivery": 3}, {"amount": 500}),
    "update_delivery_address": ("update", "medium", {"order_status": "processing"}, {}),
    "cancel_order": ("update", "medium", {"order_status": "processing"}, {}),
    "apply_voucher": ("create", "medium", {"days_late": 5}, {"amount": 100}),
    "create_ticket": ("create", "low", {}, {}),
}  # fmt: skip


def request(
    action: str,
    facts: dict[str, Any] | None = None,
    arguments: dict[str, Any] | None = None,
    *,
    kind: str | None = None,
    risk: str | None = None,
    verified: bool = True,
    **extra: Any,
) -> CheckActionRequest:
    base_kind, base_risk, _, _ = BASELINE.get(action, ("create", "medium", {}, {}))
    return CheckActionRequest.model_validate(
        {
            "request_id": "req-1", "tenant_id": T, "conversation_id": "c1", "action": action,
            "tool": {"name": action, "operation_kind": kind or base_kind, "risk": risk or base_risk},
            "identity": {"verified": verified, "customer_id": "cust_1" if verified else None},
            "facts": facts or {}, "arguments": arguments or {}, "as_of": AS_OF, **extra,
        }
    )  # fmt: skip


def baseline_request(
    action: str, facts: dict[str, Any] | None = None, args: dict[str, Any] | None = None, **extra: Any
) -> CheckActionRequest:
    _, _, base_facts, base_args = BASELINE[action]
    return request(action, {**base_facts, **(facts or {})}, {**base_args, **(args or {})}, **extra)


@pytest.fixture
def checker() -> RuleCheckerStandin:
    return RuleCheckerStandin(FIXTURES)


def outcome(decision: PolicyDecision, rule_id: str) -> Any:
    found = [o for o in decision.rule_outcomes if o.rule_id == rule_id]
    assert len(found) == 1, f"{rule_id} not evaluated: {[o.rule_id for o in decision.rule_outcomes]}"
    return found[0]


def custom_checker(
    tmp_path: Path, rules: list[dict[str, Any]], risk: dict[str, Any] | None = None
) -> RuleCheckerStandin:
    folder = tmp_path / T
    folder.mkdir()
    (folder / "rules.json").write_text(json.dumps({"tenant_id": T, "rules": rules}), encoding="utf-8")
    if risk is not None:
        (folder / "risk.json").write_text(json.dumps({"categories": risk}), encoding="utf-8")
    return RuleCheckerStandin(tmp_path)


def rule(rule_id: str, action: str, **kw: Any) -> dict[str, Any]:
    return {
        "rule_id": rule_id, "action": action, "status": "approved", "applies_if": [], "conditions": [],
        "effect": "allow", "else_effect": "deny", "citation": f"doc@v1#{rule_id}",
        "user_message": {"en": f"en {rule_id}", "ar": f"ar {rule_id}"}, **kw,
    }  # fmt: skip


def cond(field: str, op: str, value: Any, source: str = "facts") -> dict[str, Any]:
    return {"field": field, "op": op, "value": value, "from": source}


# ---------------------------------------------------------------- every rule in rules.json

# rule_id -> (facts overrides, argument overrides, expected effect when the rule fails: its else_effect)
RULE_CASES: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    "R-RETURN-14D": ({"days_since_delivery": 20}, {}),
    "R-RETURN-EXCLUDED": ({"product_category": "underwear"}, {}),
    "R-RETURN-UNUSED": ({"item_condition": "used"}, {}),
    "R-EXCHANGE-14D": ({"days_since_delivery": 20}, {}),
    "R-REFUND-14D": ({"days_since_delivery": 20}, {}),
    "R-REFUND-LIMIT": ({}, {"amount": 3001}),
    "R-REFUND-STATUS": ({"order_status": "shipped"}, {}),
    "R-ADDRESS-BEFORE-SHIP": ({"order_status": "shipped"}, {}),
    "R-CANCEL-BEFORE-SHIP": ({"order_status": "shipped"}, {}),
    "R-VOUCHER-LATE-3D": ({"days_late": 3}, {}),
    "R-VOUCHER-MAX-100": ({}, {"amount": 101}),
}
APPROVED = {r["rule_id"]: r for r in RULES if r["status"] == "approved"}


def test_every_approved_rule_has_a_case_and_nothing_else_does() -> None:
    assert set(RULE_CASES) | {"R-TICKET-ALLOWED"} == set(APPROVED)


@pytest.mark.parametrize("rule_id", sorted(RULE_CASES))
async def test_rule_allows_when_it_holds(checker: RuleCheckerStandin, rule_id: str) -> None:
    spec = APPROVED[rule_id]
    d = await checker.check_action(baseline_request(spec["action"]))
    assert (d.decision, d.reason_code, d.user_message) == ("allow", "RULES_PASSED", None)
    got = outcome(d, rule_id)
    assert (got.held, got.effect_applied, got.citation) == (True, spec["effect"], spec["citation"])


@pytest.mark.parametrize("rule_id", sorted(RULE_CASES))
async def test_rule_fails_with_its_else_effect(checker: RuleCheckerStandin, rule_id: str) -> None:
    spec = APPROVED[rule_id]
    facts, args = RULE_CASES[rule_id]
    d = await checker.check_action(baseline_request(spec["action"], facts, args))
    got = outcome(d, rule_id)
    assert (got.held, got.effect_applied) == (False, spec["else_effect"])
    expected_code = {"deny": "RULE_BLOCKED", "require_human": "RULE_REQUIRES_HUMAN"}[spec["else_effect"]]
    assert (d.decision, d.reason_code) == (spec["else_effect"], expected_code)
    assert d.citations == (spec["citation"],)
    assert d.user_message is not None and d.user_message.model_dump() == spec["user_message"]
    assert all(o.effect_applied == "allow" for o in d.rule_outcomes if o.rule_id != rule_id)


async def test_return_excluded_also_blocks_clearance_items(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(baseline_request("create_return", {"is_clearance": True}))
    assert (d.decision, outcome(d, "R-RETURN-EXCLUDED").held) == ("deny", False)


async def test_ticket_rule_allows_and_has_no_failing_case(checker: RuleCheckerStandin) -> None:
    # R-TICKET-ALLOWED has no conditions, so only the earlier steps can stop a ticket (see the next tests).
    d = await checker.check_action(baseline_request("create_ticket"))
    assert (d.decision, d.reason_code, d.citations) == ("allow", "RULES_PASSED", ("faq@v1#q01",))
    assert outcome(d, "R-TICKET-ALLOWED").predicate == "always"
    blocked = await checker.check_action(baseline_request("create_ticket", verified=False))
    assert (blocked.decision, blocked.reason_code, blocked.rule_outcomes) == ("deny", "IDENTITY_REQUIRED", ())


@pytest.mark.parametrize("days,decision", [(0, "allow"), (14, "allow"), (15, "deny")])
async def test_fourteen_day_boundary(checker: RuleCheckerStandin, days: int, decision: str) -> None:
    assert (
        await checker.check_action(baseline_request("create_return", {"days_since_delivery": days}))
    ).decision == decision


@pytest.mark.parametrize("amount,decision", [(3000, "allow"), (3000.5, "require_human")])
async def test_refund_limit_boundary(checker: RuleCheckerStandin, amount: float, decision: str) -> None:
    assert (await checker.check_action(baseline_request("create_refund", args={"amount": amount}))).decision == decision


# ---------------------------------------------------------------- the unapproved rule has no effect


async def test_proposed_rule_has_no_effect(checker: RuleCheckerStandin) -> None:
    assert APPROVED.get("R-DEFECT-48H") is None
    facts = {
        "item_condition": "defective",
        "hours_since_delivery": 500,
    }  # would trigger R-DEFECT-48H's else: require_human
    d = await checker.check_action(baseline_request("create_return", facts))
    assert (d.decision, d.reason_code) == ("allow", "RULES_PASSED")
    assert "R-DEFECT-48H" not in {o.rule_id for o in d.rule_outcomes}
    assert "return_policy@v2#s6" not in d.citations


async def test_the_same_rule_does_count_once_approved(tmp_path: Path) -> None:
    folder = tmp_path / T
    folder.mkdir()
    flipped = [{**r, "status": "approved"} if r["rule_id"] == "R-DEFECT-48H" else r for r in RULES]
    (folder / "rules.json").write_text(json.dumps({"tenant_id": T, "rules": flipped}), encoding="utf-8")
    shutil.copy(FIXTURES / T / "risk.json", folder / "risk.json")
    facts = {"item_condition": "defective", "hours_since_delivery": 500}
    d = await RuleCheckerStandin(tmp_path).check_action(baseline_request("create_return", facts))
    assert (d.decision, d.reason_code) == ("require_human", "RULE_REQUIRES_HUMAN")
    assert outcome(d, "R-DEFECT-48H").effect_applied == "require_human"


async def test_proposed_rule_with_a_missing_fact_cannot_block_either(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(baseline_request("create_return", {"item_condition": "defective"}))  # no hours fact
    assert (d.decision, d.missing_fields) == ("allow", ())


# ---------------------------------------------------------------- step 1: tenant


async def test_step1_tenant_mismatch_denies_and_comes_first(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(baseline_request("create_refund", verified=False, resource_tenant_id="shop_002"))
    assert (d.decision, d.reason_code, d.rule_outcomes) == ("deny", "TENANT_MISMATCH", ())
    assert d.user_message is not None and d.user_message.en and d.user_message.ar


async def test_step1_same_resource_tenant_is_fine(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(baseline_request("create_ticket", resource_tenant_id=T))
    assert d.decision == "allow"


# ---------------------------------------------------------------- step 2: identity


async def test_step2_side_effect_without_identity_is_denied(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(baseline_request("create_refund", verified=False))
    assert (d.decision, d.reason_code) == ("deny", "IDENTITY_REQUIRED")


async def test_step2_personal_data_read_without_identity_is_denied(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(request("get_order", kind="read", verified=False))  # personal_data defaults to true
    assert (d.decision, d.reason_code) == ("deny", "IDENTITY_REQUIRED")


async def test_step2_harmless_read_needs_no_identity(checker: RuleCheckerStandin) -> None:
    req = CheckActionRequest.model_validate(
        {
            "request_id": "r", "tenant_id": T, "action": "get_store_hours",
            "tool": {"name": "get_store_hours", "operation_kind": "read", "personal_data": False},
        }
    )  # fmt: skip
    d = await checker.check_action(req)
    assert (d.decision, d.reason_code) == ("allow", "NO_RULE_READ_ONLY")


async def test_step2_comes_before_the_mandatory_risk_step(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(
        baseline_request("create_refund", verified=False, risk_categories=("fraud_suspected",))
    )
    assert d.reason_code == "IDENTITY_REQUIRED"


# ---------------------------------------------------------------- step 3: mandatory risk


async def test_step3_mandatory_risk_on_a_write_needs_a_human(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(baseline_request("create_ticket", risk_categories=("fraud_suspected",)))
    assert (d.decision, d.reason_code) == ("require_human", "MANDATORY_RISK")
    assert "fraud_suspected" in d.rationale and d.user_message is not None


async def test_step3_comes_before_the_rules(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(
        baseline_request("create_return", {"days_since_delivery": 90}, risk_categories=("legal_regulatory",))
    )  # the 14-day rule would deny
    assert (d.decision, d.reason_code, d.rule_outcomes) == ("require_human", "MANDATORY_RISK", ())


async def test_step3_does_not_stop_a_read(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(request("get_order", kind="read", risk_categories=("fraud_suspected",)))
    assert d.reason_code == "NO_RULE_READ_ONLY"


async def test_step3_unknown_category_counts_as_mandatory(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(baseline_request("create_ticket", risk_categories=("brand_new_category",)))
    assert d.reason_code == "MANDATORY_RISK"


async def test_step3_a_category_the_tenant_marks_not_mandatory_is_ignored(tmp_path: Path) -> None:
    checker = custom_checker(
        tmp_path, [rule("R-T", "create_ticket")], risk={"annoyed": {"mandatory_escalation": False}, "fraud": {}}
    )
    assert (
        await checker.check_action(baseline_request("create_ticket", risk_categories=("annoyed",)))
    ).decision == "allow"
    assert (
        await checker.check_action(baseline_request("create_ticket", risk_categories=("fraud",)))
    ).reason_code == "MANDATORY_RISK"


async def test_step3_unreadable_risk_file_means_every_category_is_mandatory(tmp_path: Path) -> None:
    checker = custom_checker(tmp_path, [rule("R-T", "create_ticket")])  # no risk.json
    d = await checker.check_action(baseline_request("create_ticket", risk_categories=("annoyed",)))
    assert d.reason_code == "MANDATORY_RISK"


# ---------------------------------------------------------------- step 4: rules


async def test_step4_deny_beats_require_human_beats_allow(checker: RuleCheckerStandin) -> None:
    both = await checker.check_action(baseline_request("create_refund", {"days_since_delivery": 20}, {"amount": 5000}))
    assert (both.decision, both.reason_code) == ("deny", "RULE_BLOCKED")
    assert {o.rule_id: o.effect_applied for o in both.rule_outcomes} == {
        "R-REFUND-14D": "deny", "R-REFUND-LIMIT": "require_human", "R-REFUND-STATUS": "allow",
    }  # fmt: skip
    assert both.citations == ("refund_policy@v1#s1",)  # only the deciding rules are cited
    human = await checker.check_action(baseline_request("create_refund", args={"amount": 5000}))
    assert (human.decision, human.reason_code) == ("require_human", "RULE_REQUIRES_HUMAN")


async def test_step4_rule_whose_applies_if_is_false_is_skipped(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(
        baseline_request("create_refund", {"order_status": "cancelled", "days_since_delivery": 99})
    )
    assert d.decision == "allow" and "R-REFUND-14D" not in {o.rule_id for o in d.rule_outcomes}


async def test_step4_missing_fact_denies_with_missing_context(checker: RuleCheckerStandin) -> None:
    req = request("create_return", {"days_since_delivery": 3}, {})  # category, clearance and condition facts are absent
    d = await checker.check_action(req)
    assert (d.decision, d.reason_code) == ("deny", "MISSING_CONTEXT")
    assert d.missing_fields == ("product_category", "is_clearance", "item_condition")
    assert outcome(d, "R-RETURN-UNUSED").held is None and d.user_message is not None
    assert set(d.citations) == {"return_policy@v2#s4", "return_policy@v2#s3"}


@pytest.mark.parametrize("bad", [None, "ten", True, [3], {"n": 3}, float("nan"), "3"])
async def test_step4_malformed_numeric_fact_fails_closed(checker: RuleCheckerStandin, bad: Any) -> None:
    d = await checker.check_action(baseline_request("create_exchange", {"days_since_delivery": bad}))
    assert (d.decision, d.reason_code, d.missing_fields) == ("deny", "MISSING_CONTEXT", ("days_since_delivery",))


@pytest.mark.parametrize("bad", ["no", 0, "false", None])
async def test_step4_malformed_boolean_fact_fails_closed(checker: RuleCheckerStandin, bad: Any) -> None:
    d = await checker.check_action(baseline_request("create_return", {"is_clearance": bad}))
    assert (d.decision, d.reason_code) == ("deny", "MISSING_CONTEXT")


async def test_step4_missing_applies_if_fact_fails_closed(checker: RuleCheckerStandin) -> None:
    req = request("create_refund", {"days_since_delivery": 3}, {"amount": 100})  # no order_status
    d = await checker.check_action(req)
    assert (d.decision, d.reason_code) == ("deny", "MISSING_CONTEXT")
    assert "order_status" in d.missing_fields


async def test_step4_another_definite_deny_wins_over_missing_context(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(
        request("create_return", {"days_since_delivery": 90}, {})
    )  # 14d denies, rest missing
    assert (d.decision, d.reason_code) == ("deny", "RULE_BLOCKED")
    assert d.missing_fields  # the gaps are still reported


async def test_step4_missing_fact_with_only_require_human_elsewhere_is_still_a_deny(
    checker: RuleCheckerStandin,
) -> None:
    d = await checker.check_action(baseline_request("create_refund", {"order_status": None}, {"amount": 5000}))
    assert (d.decision, d.reason_code) == ("deny", "MISSING_CONTEXT")


async def test_step4_an_argument_never_stands_in_for_a_fact(checker: RuleCheckerStandin) -> None:
    req = request("create_exchange", {}, {"days_since_delivery": 1})  # the customer says "yesterday"
    d = await checker.check_action(req)
    assert (d.decision, d.reason_code, d.missing_fields) == ("deny", "MISSING_CONTEXT", ("days_since_delivery",))


async def test_step4_a_fact_does_not_stand_in_for_an_argument(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(request("apply_voucher", {"days_late": 5, "amount": 10}, {}))
    assert (d.decision, d.missing_fields) == ("deny", ("arguments.amount",))


async def test_step4_an_argument_cannot_override_a_fact(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(
        baseline_request("create_exchange", {"days_since_delivery": 40}, {"days_since_delivery": 1})
    )
    assert (d.decision, d.reason_code) == ("deny", "RULE_BLOCKED")


async def test_step4_rules_of_other_actions_do_not_apply(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(baseline_request("cancel_order"))
    assert {o.rule_id for o in d.rule_outcomes} == {"R-CANCEL-BEFORE-SHIP"}


async def test_step4_wrong_type_condition_in_rule_file_is_unreadable(tmp_path: Path) -> None:
    folder = tmp_path / T
    folder.mkdir()
    bad = rule("R-X", "create_ticket", conditions=[cond("a", "~=", 1)])
    (folder / "rules.json").write_text(json.dumps({"tenant_id": T, "rules": [bad]}), encoding="utf-8")
    with pytest.raises(UpstreamError) as caught:
        await RuleCheckerStandin(tmp_path).check_action(baseline_request("create_ticket"))
    assert caught.value.code == "RULES_INVALID"


# ---------------------------------------------------------------- derived facts


@pytest.mark.parametrize(
    "delivered,decision",
    [("2026-09-18", "allow"), ("2026-09-14", "allow"), ("2026-09-13", "deny"), ("2026-09-18T10:00:00Z", "allow")],
)
async def test_days_since_delivery_is_derived_from_the_date(
    checker: RuleCheckerStandin, delivered: str, decision: str
) -> None:
    d = await checker.check_action(request("create_exchange", {"delivered_at": delivered}))
    assert d.decision == decision


async def test_derived_days_replace_a_number_the_backend_sent(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(request("create_exchange", {"delivered_at": "2026-08-01", "days_since_delivery": 2}))
    assert (d.decision, d.reason_code) == ("deny", "RULE_BLOCKED")


async def test_a_supplied_day_count_is_used_when_there_is_no_date(checker: RuleCheckerStandin) -> None:
    assert (await checker.check_action(request("create_exchange", {"days_since_delivery": 2}))).decision == "allow"


@pytest.mark.parametrize("delivered", ["yesterday", "2026-10-05", 20260918, ""])
async def test_unusable_delivery_date_fails_closed(checker: RuleCheckerStandin, delivered: Any) -> None:
    d = await checker.check_action(request("create_exchange", {"delivered_at": delivered, "days_since_delivery": 1}))
    assert (d.decision, d.reason_code) == ("deny", "MISSING_CONTEXT")


async def test_without_as_of_nothing_is_derived(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(request("create_exchange", {"delivered_at": "2026-09-27"}, as_of=None))
    assert (d.decision, d.missing_fields) == ("deny", ("days_since_delivery",))


def test_derive_facts_days_late() -> None:
    assert derive_facts({"delivered_at": "2026-09-25", "expected_delivery_date": "2026-09-20"}, AS_OF)["days_late"] == 5
    assert derive_facts({"delivered_at": "2026-09-19", "expected_delivery_date": "2026-09-20"}, AS_OF)["days_late"] == 0
    assert derive_facts({"expected_delivery_date": "2026-09-20"}, AS_OF)["days_late"] == 8  # not delivered yet: as_of
    assert derive_facts({"expected_delivery_date": "2026-10-20"}, AS_OF)["days_late"] == 0
    assert "days_late" not in derive_facts({"delivered_at": "nope", "expected_delivery_date": "2026-09-20"}, AS_OF)
    assert "days_late" not in derive_facts({"expected_delivery_date": "soon"}, AS_OF)
    assert derive_facts({"days_late": 9}, AS_OF) == {"days_late": 9}
    facts = {"delivered_at": "2026-09-18"}
    assert derive_facts(facts, AS_OF)["days_since_delivery"] == 10 and facts == {"delivered_at": "2026-09-18"}


@pytest.mark.parametrize(
    "delivered,expected,decision", [("2026-09-25", "2026-09-20", "allow"), ("2026-09-22", "2026-09-20", "deny")]
)
async def test_late_voucher_uses_derived_days_late(
    checker: RuleCheckerStandin, delivered: str, expected: str, decision: str
) -> None:
    facts = {"delivered_at": delivered, "expected_delivery_date": expected}
    d = await checker.check_action(request("apply_voucher", facts, {"amount": 100}))
    assert d.decision == decision and outcome(d, "R-VOUCHER-LATE-3D").predicate == "days_late > 3"


# ---------------------------------------------------------------- step 5: no rule


async def test_step5_read_without_rule_is_allowed(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(request("get_order", kind="read"))
    assert (d.decision, d.reason_code, d.user_message) == ("allow", "NO_RULE_READ_ONLY", None)


async def test_step5_high_risk_write_without_rule_needs_a_human(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(request("close_account", kind="delete", risk="high"))
    assert (d.decision, d.reason_code) == ("require_human", "HIGH_RISK_DEFAULT") and d.user_message is not None


async def test_step5_other_write_without_rule_needs_a_human(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(request("rename_wishlist", kind="update", risk="low"))
    assert (d.decision, d.reason_code) == ("require_human", "NO_RULE_SIDE_EFFECT")


async def test_step5_applies_when_every_rule_is_skipped_by_applies_if(tmp_path: Path) -> None:
    checker = custom_checker(tmp_path, [rule("R-A", "create_ticket", applies_if=[cond("vip", "==", True)])])
    d = await checker.check_action(baseline_request("create_ticket", {"vip": False}))
    assert (d.decision, d.reason_code, d.rule_outcomes) == ("require_human", "NO_RULE_SIDE_EFFECT", ())


async def test_step5_a_proposed_rule_alone_leaves_the_action_without_a_rule(tmp_path: Path) -> None:
    checker = custom_checker(tmp_path, [rule("R-P", "create_ticket", status="proposed")])
    d = await checker.check_action(baseline_request("create_ticket"))
    assert d.reason_code == "NO_RULE_SIDE_EFFECT"


# ---------------------------------------------------------------- step 6: human approval


@pytest.mark.parametrize(
    "build,was",
    [
        (lambda: baseline_request("create_refund", args={"amount": 5000}), "RULE_REQUIRES_HUMAN"),
        (lambda: baseline_request("create_ticket", risk_categories=("fraud_suspected",)), "MANDATORY_RISK"),
        (lambda: request("close_account", kind="delete", risk="high"), "HIGH_RISK_DEFAULT"),
        (lambda: request("rename_wishlist", kind="update", risk="low"), "NO_RULE_SIDE_EFFECT"),
    ],
)
async def test_step6_approval_turns_require_human_into_allow(checker: RuleCheckerStandin, build: Any, was: str) -> None:
    before = await checker.check_action(build())
    assert (before.decision, before.reason_code) == ("require_human", was)
    after = await checker.check_action(build().model_copy(update={"human_approval": _approval()}))
    assert (after.decision, after.reason_code, after.user_message) == ("allow", "APPROVED_BY_HUMAN", None)
    assert "agent_7" in after.rationale and was in after.rationale and after.rule_outcomes == before.rule_outcomes


def _approval() -> Any:
    return CheckActionRequest.model_validate({**request("x").model_dump(), "human_approval": APPROVAL}).human_approval


@pytest.mark.parametrize(
    "build,code",
    [
        (lambda: baseline_request("create_return", {"days_since_delivery": 90}), "RULE_BLOCKED"),
        (lambda: baseline_request("create_refund", {"days_since_delivery": 20}, {"amount": 5000}), "RULE_BLOCKED"),
        (lambda: request("create_exchange", {}), "MISSING_CONTEXT"),
        (lambda: baseline_request("create_ticket", verified=False), "IDENTITY_REQUIRED"),
        (lambda: baseline_request("create_ticket", resource_tenant_id="shop_002"), "TENANT_MISMATCH"),
    ],
)
async def test_step6_approval_never_turns_a_deny_into_an_allow(
    checker: RuleCheckerStandin, build: Any, code: str
) -> None:
    req = build().model_copy(update={"human_approval": _approval()})
    d = await checker.check_action(req)
    assert (d.decision, d.reason_code) == ("deny", code)


async def test_step6_an_allow_stays_as_it_was(checker: RuleCheckerStandin) -> None:
    req = baseline_request("create_ticket").model_copy(update={"human_approval": _approval()})
    assert (await checker.check_action(req)).reason_code == "RULES_PASSED"


# ---------------------------------------------------------------- the answer, the plug, the failure switch


async def test_every_non_allow_answer_has_both_languages(checker: RuleCheckerStandin) -> None:
    requests = [
        baseline_request("create_ticket", resource_tenant_id="x"), baseline_request("create_ticket", verified=False),
        baseline_request("create_ticket", risk_categories=("fraud_suspected",)),
        request("create_exchange", {}), request("close_account", kind="delete", risk="high"),
        request("rename_wishlist", kind="update", risk="low"),
        baseline_request("create_return", {"days_since_delivery": 90}),
        baseline_request("create_refund", args={"amount": 5000}),
    ]  # fmt: skip
    for req in requests:
        d = await checker.check_action(req)
        assert d.decision != "allow" and d.user_message is not None, d.reason_code
        assert d.user_message.en.strip() and d.user_message.ar.strip(), d.reason_code


async def test_answer_echoes_the_request(checker: RuleCheckerStandin) -> None:
    d = await checker.check_action(baseline_request("create_ticket"))
    assert (d.request_id, d.tenant_id, d.conversation_id, d.action) == ("req-1", T, "c1", "create_ticket")


async def test_checker_does_not_change_the_request(checker: RuleCheckerStandin) -> None:
    req = baseline_request("create_exchange", {"delivered_at": "2026-09-18"})
    before = req.model_dump()
    await checker.check_action(req)
    assert req.model_dump() == before


async def test_unknown_tenant_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(UpstreamError) as caught:
        await RuleCheckerStandin(tmp_path).check_action(baseline_request("create_ticket"))
    assert caught.value.code == "TENANT_NOT_FOUND"


async def test_rules_file_naming_another_tenant_is_refused(tmp_path: Path) -> None:
    (tmp_path / T).mkdir()
    (tmp_path / T / "rules.json").write_text(json.dumps({"tenant_id": "other", "rules": []}), encoding="utf-8")
    with pytest.raises(UpstreamError, match="another tenant"):
        await RuleCheckerStandin(tmp_path).check_action(baseline_request("create_ticket"))


async def test_failure_switch(checker: RuleCheckerStandin) -> None:
    checker.fail_next(2)
    for _ in range(2):
        with pytest.raises(UpstreamError) as caught:
            await checker.check_action(baseline_request("create_ticket"))
        assert caught.value.retryable
    assert (await checker.check_action(baseline_request("create_ticket"))).decision == "allow"
    with pytest.raises(ValueError, match="at least 1"):
        checker.fail_next(0)


async def test_switch_applies_even_to_checks_that_would_deny(checker: RuleCheckerStandin) -> None:
    checker.fail_next()
    with pytest.raises(UpstreamError):
        await checker.check_action(baseline_request("create_ticket", verified=False))


async def test_inject_and_container_plug(container: Container) -> None:
    assert isinstance(container.policy, PolicyGate) and container.policy is container.rule_checker
    inject(container, "rule_checker", {"switch": "fail_next", "times": 2})
    for _ in range(2):
        with pytest.raises(UpstreamError):
            await container.policy.check_action(baseline_request("create_ticket"))
    inject(container, "rule_checker", {"switch": "fail_next"})
    inject(container, "rule_checker", {"switch": "reset"})
    assert (await container.policy.check_action(baseline_request("create_ticket"))).decision == "allow"
    with pytest.raises(ValueError, match="unknown rule_checker switch"):
        inject(container, "rule_checker", {"switch": "explode"})
    with pytest.raises(ValueError, match="only the operation"):
        inject(container, "rule_checker", {"switch": "fail_next", "operation": "other"})
