"""Apply the approved rules to the demo orders with a tiny evaluator, to prove the data gives the planned cases.

This evaluator is only for checking the fixtures. The real stand-in rule checker is built in Phase 2.
"""

from datetime import date
from typing import Any

import pytest

from tests.contract.conftest import AS_OF, Condition, Rule, read

STRICTNESS = {"allow": 0, "require_human": 1, "deny": 2}
OPS = {
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "in": lambda a, b: a in b,
    "not_in": lambda a, b: a not in b,
}


def facts_of(order: dict[str, Any]) -> dict[str, Any]:
    facts = dict(order)
    delivered = date.fromisoformat(order["delivered_at"]) if order["delivered_at"] else None
    expected = date.fromisoformat(order["expected_delivery_date"])
    facts["days_since_delivery"] = (AS_OF - delivered).days if delivered else None
    facts["days_late"] = max(0, ((delivered or AS_OF) - expected).days)
    return facts


def holds(cond: Condition, facts: dict[str, Any], arguments: dict[str, Any]) -> bool | None:
    value = (facts if cond.from_ == "facts" else arguments).get(cond.field)
    return None if value is None else bool(OPS[cond.op](value, cond.value))


def decide(action: str, order_id: str, arguments: dict[str, Any], enforced: list[Rule]) -> str:
    order = next(o for o in read("backend.json")["orders"] if o["order_id"] == order_id)
    facts, outcomes = facts_of(order), []
    for rule in enforced:
        if rule.action != action or any(holds(c, facts, arguments) is not True for c in rule.applies_if):
            continue
        results = [holds(c, facts, arguments) for c in rule.conditions]
        if None in results:
            return "deny"  # a needed fact is missing: fail closed
        outcomes.append(rule.effect if all(results) else rule.else_effect)
    return max(outcomes, key=STRICTNESS.__getitem__) if outcomes else "require_human"


def approved() -> list[Rule]:
    return [r for r in (Rule.model_validate(x) for x in read("rules.json")["rules"]) if r.status == "approved"]


CASES = [
    # (order, action, arguments, expected decision, what it shows)
    ("NS-20877", "apply_voucher", {"amount": 100}, "allow", "example A: 4 days late, voucher within EGP 100"),
    ("NS-20877", "apply_voucher", {"amount": 150}, "require_human", "voucher above EGP 100"),
    ("NS-20899", "apply_voucher", {"amount": 100}, "deny", "shipped on time: not late"),
    ("NS-20877", "cancel_order", {}, "require_human", "shipped: cancellation needs a human"),
    ("NS-20877", "create_refund", {"amount": 890}, "deny", "still in transit: no refund"),
    ("NS-20512", "create_refund", {"amount": 800}, "deny", "example B: refund on day 20"),
    ("NS-20512", "create_return", {}, "deny", "return on day 20"),
    ("NS-20512", "create_exchange", {}, "deny", "exchange on day 20"),
    ("NS-20745", "create_refund", {"amount": 1250}, "allow", "delivered 3 days ago"),
    ("NS-20745", "create_return", {}, "allow", "delivered 3 days ago"),
    ("NS-20790", "create_refund", {"amount": 640}, "allow", "delivered 10 days ago"),
    ("NS-20611", "create_refund", {"amount": 520}, "allow", "exactly 14 days: inside the window"),
    ("NS-20588", "create_refund", {"amount": 700}, "deny", "15 days: outside the window"),
    ("NS-20934", "create_refund", {"amount": 3450}, "require_human", "example C: above EGP 3,000"),
    ("NS-20810", "create_return", {}, "deny", "used item"),
    ("NS-20822", "create_return", {}, "deny", "clearance item"),
    ("NS-20399", "create_return", {}, "deny", "underwear is excluded"),
    ("NS-20977", "create_return", {}, "allow", "damaged item: the proposed 48h rule changes nothing"),
    ("NS-20960", "cancel_order", {}, "allow", "pending order"),
    ("NS-20955", "cancel_order", {}, "allow", "processing order"),
    ("NS-20955", "update_delivery_address", {}, "allow", "processing order"),
    ("NS-20899", "update_delivery_address", {}, "deny", "shipped: address cannot change"),
    ("NS-20701", "create_refund", {"amount": 560}, "allow", "cancelled before shipping"),
    ("NS-20466", "create_refund", {"amount": 480}, "allow", "returned inside the window"),
    ("NS-20745", "create_ticket", {}, "allow", "tickets are always allowed"),
]


@pytest.mark.parametrize(
    ("order_id", "action", "arguments", "expected", "why"), CASES, ids=[f"{c[0]}-{c[1]}-{c[3]}" for c in CASES]
)
def test_demo_case_gets_the_planned_decision(
    order_id: str, action: str, arguments: dict[str, Any], expected: str, why: str
) -> None:
    assert decide(action, order_id, arguments, approved()) == expected, why


def test_the_proposed_defect_rule_has_no_effect() -> None:
    enforced = {r.rule_id for r in approved() if r.action == "create_return"}
    assert enforced == {"R-RETURN-14D", "R-RETURN-EXCLUDED", "R-RETURN-UNUSED"}  # R-DEFECT-48H is not enforced
    everything = [Rule.model_validate(r) for r in read("rules.json")["rules"]]
    assert decide("create_return", "NS-20977", {}, approved()) == "allow"
    # if it WERE enforced, the missing hours_since_delivery fact would make this deny: proof that approval matters
    assert decide("create_return", "NS-20977", {}, everything) == "deny"
