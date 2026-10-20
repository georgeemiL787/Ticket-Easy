"""Noon end to end with the SQLite shop: the whole write path (identity, rule check, confirmation, the shop's rows).

Team A has no approved rules for Noon yet, so a return goes to a person (scenario N06). Here a temporary rules file with
ONE allow rule (the return window in the Noon policy, written only for this test) shows what happens once rules exist:
the customer is asked to confirm, the return becomes a row in the shop's database, the order changes, the audit log has
the rule's answer, a second return is refused by the shop, and everything is still there after a restart.
"""

import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from team_b.config import PROJECT_ROOT
from team_b.container import Container, build_container
from tests.integration.agent_expert import with_expert
from tests.support import make_settings

T = "noon_eg"
WINDOW_RULE = {
    "rule_id": "R-NOON-RETURN-WINDOW", "action": "create_return", "scope": "returns", "status": "approved",
    "applies_if": [], "conditions": [{"field": "days_since_delivery", "op": "<=", "value": 14, "from": "facts"}],
    "effect": "allow", "else_effect": "deny", "citation": "noon_return_policy@v1#s2",
    "quote": "within 14 days of delivery", "effective_date": "2026-10-01",
    "user_message": {"en": "The return window of 14 days has passed.", "ar": "مدة الاسترجاع 14 يوم وعدّت."},
}  # fmt: skip


@pytest.fixture
def fixtures_with_a_rule(tmp_path: Path) -> Path:
    folder = tmp_path / "fixtures"
    shutil.copytree(PROJECT_ROOT / "fixtures", folder)
    rules_file = folder / T / "rules.json"
    rules = json.loads(rules_file.read_text(encoding="utf-8"))
    rules["rules"] = [WINDOW_RULE]
    rules_file.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    return folder


def build(tmp_path: Path, fixtures: Path, *, sqlite: bool = False, agent: bool = False) -> Container:
    options: dict[str, Any] = {"fixtures_dir": fixtures, "fixed_today": date(2026, 10, 8)}
    if sqlite:
        options["store"] = "sqlite"
    container = build_container(make_settings(tmp_path, **options))
    return with_expert(container) if agent else container


async def say(c: Container, conversation: str, *texts: str) -> list[Any]:
    assert c.orchestrator is not None
    return [await c.orchestrator.handle_turn(T, conversation, t) for t in texts]


@pytest.mark.parametrize("agent", [False, True], ids=["usual", "agent"])
async def test_a_return_is_confirmed_then_written_to_the_shops_database(
    tmp_path: Path, fixtures_with_a_rule: Path, agent: bool
) -> None:
    c = build(tmp_path, fixtures_with_a_rule, agent=agent)
    assert c.shop is not None
    replies = await say(c, "n1", "I want to return order ORD-30001, I changed my mind", "01000000001", "yes")
    assert [r.decision.value for r in replies] == ["verify_identity", "confirm", "execute"]
    assert "RET-6001" in replies[-1].text
    assert c.shop.change_count(T) == 1
    assert c.shop.records(T, "return") == [
        {"reference_id": "RET-6001", "order_id": "ORD-30001", "reason": "I changed my mind"}
    ]
    assert c.shop.order(T, "ORD-30001")["order_status"] == "return_in_progress"  # type: ignore[index]
    write = next(e for e in c.shop.audit_log(T) if e.tool == "create_return")
    assert write.applied and write.policy_request_id  # allowed by the rule checker, in this conversation's traces
    traces = await c.traces.for_conversation(T, "n1")
    assert any(p.decision == "allow" and p.reason_code for t in traces for p in t.policy)


async def test_a_second_return_for_the_same_order_is_refused_by_the_shop_and_nothing_changes_twice(
    tmp_path: Path, fixtures_with_a_rule: Path
) -> None:
    c = build(tmp_path, fixtures_with_a_rule)
    assert c.shop is not None
    await say(c, "a", "I want to return order ORD-30001, I changed my mind", "01000000001", "yes")
    again = await say(c, "b", "I want to return order ORD-30001, wrong colour", "01000000001", "yes")
    assert c.shop.change_count(T) == 1  # the same return was not made twice
    assert again[-1].decision.value != "execute" and "RET-6002" not in again[-1].text


async def test_an_order_outside_the_window_is_refused_by_the_rule_and_never_reaches_the_shop(
    tmp_path: Path, fixtures_with_a_rule: Path
) -> None:
    c = build(tmp_path, fixtures_with_a_rule)
    assert c.shop is not None
    detail = json.loads((PROJECT_ROOT / "fixtures" / T / "seed.json").read_text(encoding="utf-8"))
    old = next(o for o in detail["orders"] if o["status"] == "delivered" and o["delivered_at"] < "2026-09-20")
    phone = next(x["phone"] for x in detail["customers"] if x["customer_id"] == old["customer_id"]).replace(" ", "")
    replies = await say(c, "c", f"I want to return order {old['order_id']}, I changed my mind", phone)
    assert replies[-1].decision.value == "refuse" and "14 days" in replies[-1].text and c.shop.change_count(T) == 0


async def test_what_the_shop_did_is_still_there_after_a_restart(tmp_path: Path, fixtures_with_a_rule: Path) -> None:
    first = build(tmp_path, fixtures_with_a_rule, sqlite=True)
    await say(first, "r", "I want to return order ORD-30001, I changed my mind", "01000000001", "yes")
    assert first.shop is not None and first.shop.change_count(T) == 1
    assert (tmp_path / "shops" / "noon_eg_shop.sqlite3").is_file()

    second = build(tmp_path, fixtures_with_a_rule, sqlite=True)  # a new process: the same files
    assert second.shop is not None
    assert (
        second.shop.change_count(T) == 1 and second.shop.order(T, "ORD-30001")["order_status"] == "return_in_progress"
    )  # type: ignore[index]
    replies = await say(second, "r2", "Where is my order ORD-30001?", "01000000001")
    assert replies[-1].decision.value == "answer" and "return" in replies[-1].text.lower()
