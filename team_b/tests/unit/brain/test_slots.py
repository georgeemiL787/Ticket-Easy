"""brain/slots.py: which details to ask for, and where each tool argument comes from."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from team_b.brain.slots import (
    argument_sources,
    next_question,
    order_questions,
    parse_source,
    required_slots,
    resolve_arguments,
)
from team_b.config import PROJECT_ROOT, Settings
from team_b.contracts.tools import ToolSpec
from team_b.domain.session import SessionState
from team_b.domain.tenant import IntentSpec, TenantRegistry

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
TOOLS = {
    t["name"]: ToolSpec.model_validate(t)
    for t in json.loads((PROJECT_ROOT / "fixtures" / "shop_001" / "tools.json").read_text(encoding="utf-8"))["tools"]
}
INTENTS = TenantRegistry.from_dir(Settings().config_dir).get("shop_001").intents


def session(slots: dict[str, str] | None = None, **fields: Any) -> SessionState:
    return SessionState.model_validate(
        {
            "tenant_id": "shop_001",
            "conversation_id": "c",
            "created_at": NOW,
            "updated_at": NOW,
            "slots": slots or {},
            **fields,
        }
    )


def intent(**fields: Any) -> IntentSpec:
    return IntentSpec.model_validate({"kind": "action", "action_tool": "create_refund", **fields})


# ---- sources ----


@pytest.mark.parametrize(
    ("source", "parsed"),
    [("slot:order_id", ("slot", "order_id")), ("fact:order_total", ("fact", "order_total")),
     ("identity:customer_id", ("identity", "customer_id")), ("const:late_delivery", ("const", "late_delivery")),
     ("const:a:b", ("const", "a:b"))],
)  # fmt: skip
def test_parse_source(source: str, parsed: tuple[str, str]) -> None:
    assert parse_source(source) == parsed


@pytest.mark.parametrize("source", ["order_id", "slot:", "magic:x", "", ":x"])
def test_a_malformed_source_is_a_configuration_error(source: str) -> None:
    with pytest.raises(ValueError, match="argument source"):
        parse_source(source)


def test_a_required_argument_without_a_map_entry_uses_the_slot_of_the_same_name_when_the_intent_lists_it() -> None:
    sources = argument_sources(INTENTS["order_status"], TOOLS["get_order"])
    assert sources == {"order_id": "slot:order_id"}


# ---- required slots ----


def test_required_slots_are_the_intents_the_tools_and_the_identity_ones_without_repeats() -> None:
    assert required_slots(INTENTS["return_request"], TOOLS["create_return"], needs_identity=True) == [
        "order_id",
        "reason",
        "phone",
    ]
    assert required_slots(INTENTS["return_request"], TOOLS["create_return"], needs_identity=False) == [
        "order_id",
        "reason",
    ]


def test_a_lookup_needs_the_order_and_then_the_phone() -> None:
    assert required_slots(INTENTS["order_status"], TOOLS["get_order"], needs_identity=True) == ["order_id", "phone"]


def test_a_verified_customer_is_not_asked_for_identity_slots() -> None:
    assert required_slots(INTENTS["cancel_order"], TOOLS["cancel_order"], needs_identity=False) == ["order_id"]


def test_slots_come_from_the_tool_schema_through_the_argument_map() -> None:
    # complaint: subject is a constant, description and order_id are slots
    assert required_slots(INTENTS["complaint"], TOOLS["create_ticket"], needs_identity=False) == [
        "order_id",
        "description",
    ]
    # an intent that does not list the slot still needs it when the tool's required argument is taken from it
    bare = intent(
        action_tool="update_delivery_address",
        argument_map={"order_id": "slot:order_id", "new_address": "slot:new_address"},
    )
    assert required_slots(bare, TOOLS["update_delivery_address"], needs_identity=False) == ["order_id", "new_address"]


def test_a_fact_or_constant_argument_is_not_a_slot() -> None:
    assert required_slots(INTENTS["refund_request"], TOOLS["create_refund"], needs_identity=False) == ["order_id"]
    assert required_slots(INTENTS["voucher_request"], TOOLS["apply_voucher"], needs_identity=False) == ["order_id"]


def test_the_identity_slots_can_come_from_the_tenant() -> None:
    slots = required_slots(INTENTS["order_status"], TOOLS["get_order"], True, identity_slots=("order_id", "email"))
    assert slots == ["order_id", "email"]


def test_without_a_known_tool_the_intent_and_identity_slots_still_apply() -> None:
    assert required_slots(INTENTS["return_request"], None, needs_identity=True) == ["order_id", "reason", "phone"]


# ---- the order of questions ----


@pytest.mark.parametrize(
    ("missing", "first"),
    [
        (["amount", "reason", "item", "phone", "order_id"], "order_id"),
        (["amount", "reason", "item", "phone"], "phone"),
        (["amount", "reason", "item"], "item"),
        (["amount", "reason"], "reason"),
        (["amount"], "amount"),
        (["zzz", "phone"], "phone"),  # unknown details come after the known ones
        (["zzz", "aaa"], "zzz"),  # and keep their given order
        ([], None),
    ],
)
def test_next_question_follows_the_priority(missing: list[str], first: str | None) -> None:
    assert next_question(missing) == first


def test_order_questions_sorts_known_first_then_given_order() -> None:
    assert order_questions(["zzz", "reason", "aaa", "order_id", "phone"]) == (
        "order_id",
        "phone",
        "reason",
        "zzz",
        "aaa",
    )


# ---- filling arguments, one source at a time ----


def test_a_slot_fills_its_argument() -> None:
    result = resolve_arguments(INTENTS["cancel_order"], TOOLS["cancel_order"], session({"order_id": "NS-20960"}), {})
    assert result.arguments == {"order_id": "NS-20960"} and result.missing == () and result.unsourced == ()


def test_a_missing_slot_is_reported_not_guessed() -> None:
    result = resolve_arguments(INTENTS["return_request"], TOOLS["create_return"], session({"reason": "too small"}), {})
    assert result.arguments == {"reason": "too small"} and result.missing == ("order_id",)


def test_missing_slots_come_back_in_question_order() -> None:
    result = resolve_arguments(INTENTS["return_request"], TOOLS["create_return"], session(), {})
    assert result.missing == ("order_id", "reason")


def test_a_fact_fills_its_argument_and_the_customers_words_never_do() -> None:
    chatty = session({"order_id": "NS-20745", "amount": "5000"})  # the customer asked for 5000
    result = resolve_arguments(INTENTS["refund_request"], TOOLS["create_refund"], chatty, {"order_total": 1250})
    assert result.arguments == {"order_id": "NS-20745", "amount": 1250}
    assert result.missing == () and result.missing_facts == ()


def test_a_fact_that_is_not_loaded_yet_is_not_asked_of_the_customer() -> None:
    chatty = session({"order_id": "NS-20745", "amount": "5000"})
    result = resolve_arguments(INTENTS["refund_request"], TOOLS["create_refund"], chatty, {})
    assert "amount" not in result.arguments and result.missing == () and result.missing_facts == ("order_total",)


def test_the_verified_identity_fills_identity_arguments() -> None:
    spec = intent(action_tool="delete_customer", argument_map={"customer_id": "identity:customer_id"})
    verified = session(identity={"verified": True, "customer_id": "C-100"})
    assert resolve_arguments(spec, TOOLS["delete_customer"], verified, {}).arguments == {"customer_id": "C-100"}
    anonymous = resolve_arguments(spec, TOOLS["delete_customer"], session(), {})
    assert anonymous.arguments == {} and anonymous.needs_identity is True


def test_a_customer_cannot_supply_their_own_identity_argument() -> None:
    spec = intent(action_tool="delete_customer", argument_map={"customer_id": "identity:customer_id"})
    result = resolve_arguments(spec, TOOLS["delete_customer"], session({"customer_id": "C-999"}), {})
    assert result.arguments == {} and result.needs_identity


def test_a_constant_fills_its_argument_typed_as_the_schema_asks() -> None:
    result = resolve_arguments(
        INTENTS["voucher_request"], TOOLS["apply_voucher"], session({"order_id": "NS-20877"}), {}
    )
    assert result.arguments == {"order_id": "NS-20877", "amount": 100, "reason": "late_delivery"}
    ticket = resolve_arguments(
        INTENTS["complaint"], TOOLS["create_ticket"], session({"order_id": "NS-1", "description": "late"}), {}
    )
    assert ticket.arguments["subject"] == "Customer complaint"


@pytest.mark.parametrize(
    ("typed", "expected"), [("3000", 3000), ("3,000", 3000), ("12.5", 12.5), (" 450 ", 450), ("0", 0)]
)
def test_slot_text_is_typed_as_the_schema_asks(typed: str, expected: float) -> None:
    spec = intent(
        argument_map={"order_id": "slot:order_id", "amount": "slot:amount"}, required_slots=("order_id", "amount")
    )
    result = resolve_arguments(spec, TOOLS["create_refund"], session({"order_id": "NS-1", "amount": typed}), {})
    assert result.arguments["amount"] == expected and isinstance(result.arguments["amount"], int | float)


@pytest.mark.parametrize("typed", ["abc", "ten", "", "  "])
def test_an_unusable_answer_is_asked_again(typed: str) -> None:
    spec = intent(
        argument_map={"order_id": "slot:order_id", "amount": "slot:amount"}, required_slots=("order_id", "amount")
    )
    result = resolve_arguments(spec, TOOLS["create_refund"], session({"order_id": "NS-1", "amount": typed}), {})
    assert "amount" not in result.arguments and result.missing == ("amount",)


def test_arguments_the_tool_does_not_take_are_dropped() -> None:
    spec = intent(action_tool="cancel_order", argument_map={"order_id": "slot:order_id", "coupon": "const:X"})
    result = resolve_arguments(spec, TOOLS["cancel_order"], session({"order_id": "NS-1"}), {})
    assert result.arguments == {"order_id": "NS-1"}


# ---- a required argument nobody can fill ----


def test_a_required_argument_with_no_source_makes_the_request_unsupported() -> None:
    spec = intent(action_tool="create_return", required_slots=("order_id",), argument_map={"order_id": "slot:order_id"})
    result = resolve_arguments(spec, TOOLS["create_return"], session({"order_id": "NS-1"}), {})
    assert result.unsourced == ("reason",)


def test_the_shipped_tenant_config_has_no_unsourced_arguments() -> None:
    for name, spec in INTENTS.items():
        for tool_name in (spec.lookup_tool, spec.action_tool):
            if tool_name:
                assert resolve_arguments(spec, TOOLS[tool_name], session(), {}).unsourced == (), (name, tool_name)


def test_without_a_tool_nothing_can_be_unsourced() -> None:
    result = resolve_arguments(INTENTS["return_request"], None, session({"order_id": "NS-1"}), {})
    assert result.unsourced == () and result.missing == ("reason",)
