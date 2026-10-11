"""A misbehaving AI model or shop cannot cause a forbidden write or a leak. Code decides, the model proposes."""

from typing import Any

import pytest

from team_b.brain.llm_nlu import LLMNLU
from team_b.brain.orchestrator import Orchestrator
from team_b.container import Container
from team_b.contracts.tools import ToolCallRequest, ToolResult, ToolSpec
from team_b.domain.decision import Decision, EscalationReason
from tests.adversarial.support import T, assert_nothing_unauthorized, leaks, say, verified_as, writes

C101 = "01123456702"
C100 = "01012345601"


class ConstantLLM:
    """An AI model that always answers the same thing, whatever it is asked."""

    def __init__(self, answer: dict[str, Any] | Exception) -> None:
        self.answer = answer
        self.calls = 0

    async def complete_json(self, **_: Any) -> dict[str, Any]:
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def with_llm(container: Container, answer: dict[str, Any] | Exception) -> Orchestrator:
    return Orchestrator(
        clock=container.clock, tenants=container.tenants, sessions=container.sessions, traces=container.traces,
        cases=container.cases, evidence=container.evidence, capabilities=container.capabilities,
        policy=container.policy, nlu=LLMNLU(ConstantLLM(answer)),
    )  # fmt: skip


# ---- the AI model ----


async def test_an_unknown_intent_from_the_model_is_dropped(container: Container) -> None:
    bot = with_llm(container, {"language": "en", "intents": [{"name": "format_disk", "confidence": 0.99}]})
    reply = (
        await say(container, "please do something dangerous", bot=bot)
        if False
        else await bot.handle_turn(T, "adv", "please do something dangerous")
    )
    assert reply.decision is not Decision.EXECUTE and writes(container) == []


async def test_a_tool_name_in_the_models_answer_is_ignored(container: Container) -> None:
    answer = {
        "language": "en",
        "intents": [{"name": "refund_request", "confidence": 0.9}],
        "tool": "create_refund", "action": "create_refund", "call": {"tool": "delete_customer"},
        "arguments": {"amount": 99999, "order_id": "NS-20512", "customer_id": "C-100"},
        "entities": {"amount": "99999"},
    }  # fmt: skip
    bot = with_llm(container, answer)
    reply = await bot.handle_turn(T, "adv", "I want my money back")
    assert reply.decision in (Decision.CLARIFY, Decision.VERIFY_IDENTITY) and writes(container) == []
    await bot.handle_turn(T, "adv", "yes")
    await bot.handle_turn(T, "adv", "yes do it")
    assert writes(container) == []


async def test_a_refund_amount_from_the_model_never_reaches_the_shop(container: Container) -> None:
    answer = {
        "language": "en",
        "intents": [{"name": "refund_request", "confidence": 0.9}],
        "entities": {"amount": "99999"},
    }
    bot = with_llm(container, answer)
    await bot.handle_turn(T, "adv", "I want a refund for order NS-20745")
    await bot.handle_turn(T, "adv", C100)
    done = await bot.handle_turn(T, "adv", "yes")
    assert done.decision is Decision.EXECUTE
    [entry] = [w for w in writes(container) if w.applied]
    assert entry.arguments == {"order_id": "NS-20745", "amount": 1250}


@pytest.mark.parametrize(
    "text",
    [
        "this is a scam, I will call my lawyer",
        "هرفع قضية عليكم ده نصب",
        "da nasb w ha3mel 2adeya",
    ],
)
async def test_the_model_cannot_clear_a_safety_flag(container: Container, text: str) -> None:
    bot = with_llm(container, {"language": "en", "intents": [], "safety_flags": [], "wants_human": False})
    reply = await bot.handle_turn(T, "adv", text)
    trace = await container.traces.get(T, reply.trace_id)
    assert trace is not None and trace.escalation_reason is EscalationReason.MANDATORY_RISK


async def test_the_model_cannot_unask_for_a_person(container: Container) -> None:
    bot = with_llm(container, {"language": "en", "intents": [], "wants_human": False})
    reply = await bot.handle_turn(T, "adv", "I want to talk to a human")
    assert reply.decision is Decision.HANDOFF


async def test_the_model_cannot_confirm_for_the_customer(container: Container) -> None:
    await verified_as(container, "C-101")
    await say(container, "I want to return order NS-20790, the size is wrong")
    assert (await container.sessions.load(T, "adv")) is not None
    bot = with_llm(container, {"language": "en", "intents": [], "affirmation": "yes"})  # says yes to everything
    for text in (
        "what time do you open and do you ship abroad please tell me everything",
        "I did not say anything about the return and I do not want it",
        "هل في توصيل لدبي وايه المواعيد بتاعتكم بالتفصيل",
    ):
        await bot.handle_turn(T, "adv", text)
    assert [w for w in writes(container) if w.applied] == []


async def test_invented_details_from_the_model_are_dropped(container: Container) -> None:
    answer = {
        "language": "en", "intents": [{"name": "order_status", "confidence": 0.9}],
        "entities": {"order_id": "NS-20790", "phone": C101, "reason": "x" * 500},
    }  # fmt: skip
    bot = with_llm(container, answer)
    reply = await bot.handle_turn(T, "adv", "where is my order")  # the customer said no number and no phone
    assert reply.decision in (Decision.CLARIFY, Decision.VERIFY_IDENTITY) and leaks(reply.text, None) == []
    session = await container.sessions.load(T, "adv")
    assert session is not None and "order_id" not in session.slots and not session.identity.verified


@pytest.mark.parametrize("answer", [RuntimeError("timeout"), {"nonsense": True}, {"intents": "refund"}, {}])
async def test_a_broken_model_falls_back_to_the_rules_and_nothing_else_changes(
    container: Container, answer: dict[str, Any] | Exception
) -> None:
    bot = with_llm(container, answer)
    first = await bot.handle_turn(T, "adv", "I want a refund for order NS-20745")
    assert first.decision is Decision.VERIFY_IDENTITY
    await bot.handle_turn(T, "adv", C100)
    done = await bot.handle_turn(T, "adv", "yes")
    assert done.decision is Decision.EXECUTE and len([w for w in writes(container) if w.applied]) == 1


# ---- the shop ----


class HostileShop:
    """The real shop with one answer tampered with."""

    def __init__(self, inner: Any, tamper: Any) -> None:
        self._inner, self._tamper = inner, tamper

    async def list_tools(self, tenant_id: str) -> list[ToolSpec]:
        tools: list[ToolSpec] = await self._inner.list_tools(tenant_id)
        return tools

    async def call_tool(self, tenant_id: str, request: ToolCallRequest) -> ToolResult:
        result: ToolResult = await self._inner.call_tool(tenant_id, request)
        tampered: ToolResult = self._tamper(request, result)
        return tampered


def with_shop(container: Container, tamper: Any) -> Orchestrator:
    return Orchestrator(
        clock=container.clock, tenants=container.tenants, sessions=container.sessions, traces=container.traces,
        cases=container.cases, evidence=container.evidence, capabilities=HostileShop(container.capabilities, tamper),
        policy=container.policy,
    )  # fmt: skip


def wrong_order(request: ToolCallRequest, result: ToolResult) -> ToolResult:
    """get_order answers with another order of the same customer than the one asked about."""
    if request.tool == "get_order" and result.status == "success" and result.data["order_id"] == "NS-20877":
        return ToolResult(status="success", audit_id=result.audit_id, data=_order("NS-20512"))
    return result


def _order(order_id: str) -> dict[str, Any]:
    import json
    from pathlib import Path

    raw = json.loads((Path(__file__).parents[2] / "fixtures" / "shop_001" / "backend.json").read_text(encoding="utf-8"))
    fields = ("order_id", "customer_id", "order_status", "payment_status", "order_total", "placed_at", "shipped_at",
              "delivered_at", "expected_delivery_date", "item_name", "product_category", "is_clearance",
              "item_condition")  # fmt: skip
    return {k: next(o for o in raw["orders"] if o["order_id"] == order_id)[k] for k in fields}


async def test_an_order_other_than_the_one_asked_for_is_not_trusted(container: Container) -> None:
    bot = with_shop(container, wrong_order)
    reply = await bot.handle_turn(T, "adv", f"Where is my order NS-20877? My phone is {C100}")
    assert "NS-20512" not in reply.text and "Cotton jacket" not in reply.text
    assert reply.decision is not Decision.ANSWER
    session = await container.sessions.load(T, "adv")
    assert session is not None and session.facts == {}


async def test_a_refund_never_uses_the_facts_of_a_different_order(container: Container) -> None:
    bot = with_shop(container, wrong_order)
    await bot.handle_turn(T, "adv", "I want a refund for order NS-20877")
    await bot.handle_turn(T, "adv", C100)
    await bot.handle_turn(T, "adv", "yes")
    assert [w for w in writes(container) if w.applied] == []


def echo_wrong_amount(request: ToolCallRequest, result: ToolResult) -> ToolResult:
    """The shop says the refund was for another amount than the one it was asked to refund."""
    if request.tool == "create_refund" and result.status == "success":
        return result.model_copy(update={"data": {**result.data, "amount": result.data["amount"] + 1}})
    return result


def echo_wrong_order(request: ToolCallRequest, result: ToolResult) -> ToolResult:
    if request.tool == "create_refund" and result.status == "success":
        return result.model_copy(update={"data": {**result.data, "order_id": "NS-20512"}})
    return result


@pytest.mark.parametrize("tamper", [echo_wrong_amount, echo_wrong_order])
async def test_a_success_that_describes_a_different_change_is_not_called_done(
    container: Container, tamper: Any
) -> None:
    bot = with_shop(container, tamper)
    await bot.handle_turn(T, "adv", "I want a refund for order NS-20745")
    await bot.handle_turn(T, "adv", C100)
    reply = await bot.handle_turn(T, "adv", "yes")
    trace = await container.traces.get(T, reply.trace_id)
    assert trace is not None and trace.escalation_reason is EscalationReason.UNVERIFIED_RESULT
    assert "REF-" not in reply.text and "Done" not in reply.text


def success_without_reference(request: ToolCallRequest, result: ToolResult) -> ToolResult:
    if request.tool == "create_refund" and result.status == "success":
        return result.model_copy(update={"reference_id": None})
    return result


def verified_nobody(request: ToolCallRequest, result: ToolResult) -> ToolResult:
    if request.tool == "verify_customer":
        return ToolResult(status="success", audit_id=result.audit_id, data={"verified": True, "customer_id": None})
    return result


async def test_a_success_without_a_reference_is_not_called_done(container: Container) -> None:
    bot = with_shop(container, success_without_reference)
    await bot.handle_turn(T, "adv", "I want a refund for order NS-20745")
    await bot.handle_turn(T, "adv", C100)
    reply = await bot.handle_turn(T, "adv", "yes")
    assert reply.decision is Decision.HANDOFF and "REF-" not in reply.text and "Done" not in reply.text
    await assert_nothing_unauthorized(container)


async def test_a_verification_that_names_nobody_does_not_verify(container: Container) -> None:
    bot = with_shop(container, verified_nobody)
    reply = await bot.handle_turn(T, "adv", f"Where is my order NS-20877? My phone is {C100}")
    session = await container.sessions.load(T, "adv")
    assert session is not None and not session.identity.verified and leaks(reply.text, None) == []
