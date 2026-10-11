"""Asking for missing details through the orchestrator (S03's first two turns, clarification limits, unsupported)."""

from typing import Any

import pytest

from team_b.brain.composer import render
from team_b.brain.orchestrator import Orchestrator
from team_b.container import Container
from team_b.contracts.errors import UpstreamError
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.reply import AgentReply
from team_b.domain.session import SessionIdentity
from team_b.domain.tenant import TenantRegistry
from team_b.domain.understanding import Locale
from tests.fakes import FakeEvidence

T, C = "shop_001", "conv-1"


def orch(c: Container, **kw: Any) -> Orchestrator:
    options: dict[str, Any] = {"evidence": FakeEvidence(), "tenants": c.tenants, "capabilities": c.capabilities, **kw}
    return Orchestrator(clock=c.clock, sessions=c.sessions, traces=c.traces, cases=c.cases, **options)


async def say(o: Orchestrator, text: str, conversation: str = C) -> AgentReply:
    return await o.handle_turn(T, conversation, text)


async def session_of(c: Container, conversation: str = C):  # type: ignore[no-untyped-def]
    found = await c.sessions.load(T, conversation)
    assert found is not None
    return found


# ---- S03: the order id, then the phone ----


async def test_an_arabizi_late_order_asks_for_the_order_id_then_the_phone(c: Container) -> None:
    o = orch(c)
    first = await say(o, "el order bta3i et2akhar, 3ayez a3raf feen")
    assert (first.decision, first.awaiting, first.locale) == (Decision.CLARIFY, "slot:order_id", Locale.ARABIZI)
    assert first.text == render("ask_order_id", Locale.ARABIZI)

    second = await say(o, "NS-20877")
    assert (second.decision, second.awaiting, second.locale) == (Decision.VERIFY_IDENTITY, "slot:phone", Locale.ARABIZI)
    assert second.text == render("ask_phone", Locale.ARABIZI)
    assert "Linen" not in second.text and "TRK" not in second.text  # nothing about the order yet

    third = await say(o, "01012345601")
    session = await session_of(c)
    assert session.slots == {"order_id": "NS-20877"}  # verified: the phone is not kept
    assert session.identity.verified and third.decision is Decision.ANSWER and "NS-20877" in third.text


async def test_the_question_is_remembered_as_what_the_agent_waits_for(c: Container) -> None:
    await say(orch(c), "Where is my order?")
    session = await session_of(c)
    assert session.awaiting == "slot:order_id" and session.active_intent == "order_status"
    trace = (await c.traces.for_conversation(T, C))[-1]
    assert trace.decision_reason == "order_status needs order_id"


# ---- the order of the questions ----


async def test_a_return_asks_for_the_phone_before_the_reason_as_the_priority_says(c: Container) -> None:
    o = orch(c)
    first = await say(o, "I want to return my order NS-20512")
    assert (first.decision, first.awaiting) == (Decision.VERIFY_IDENTITY, "slot:phone")
    second = await say(o, "01012345601")
    assert (second.decision, second.awaiting, second.text) == (
        Decision.CLARIFY,
        "slot:reason",
        render("ask_reason", Locale.EN),
    )


async def test_a_verified_customer_is_not_asked_for_the_phone(c: Container) -> None:
    await say(orch(c), "Hello")  # creates the session
    session = await session_of(c)
    session.identity = SessionIdentity(verified=True, customer_id="C-100", method="test")
    await c.sessions.save(session)
    reply = await say(orch(c), "Where is my order NS-20877?")
    assert reply.awaiting != "slot:phone"  # nothing to ask: the order is read and answered
    assert reply.decision is Decision.ANSWER and "NS-20877" in reply.text


async def test_a_detail_given_up_front_is_not_asked_again(c: Container) -> None:
    reply = await say(orch(c), "Where is my order NS-20877? My phone is 01012345601")
    assert (reply.decision, reply.awaiting) == (Decision.ANSWER, None)  # nothing missing: verified and answered


async def test_a_slot_name_without_a_template_uses_the_generic_question(c: Container) -> None:
    tenant = c.tenants.get(T)
    spec = tenant.intents["cancel_order"].model_copy(update={"required_slots": ("order_id", "colour")})
    custom = tenant.model_copy(update={"intents": {**tenant.intents, "cancel_order": spec}})
    o = orch(c, tenants=TenantRegistry({T: custom}))
    await say(o, "cancel my order NS-20960 my phone is 01098765405")
    reply = await say(o, "ok")
    assert reply.awaiting == "slot:colour"
    assert reply.text == render("ask_generic", Locale.EN, slot="colour")


# ---- asking again, and giving up ----


async def test_asking_the_same_question_twice_is_a_clarification_and_the_third_time_is_a_handoff(c: Container) -> None:
    o = orch(c)
    first = await say(o, "I want a refund")
    assert first.awaiting == "slot:order_id"
    second = await say(o, "hmm")  # no answer
    assert second.decision is Decision.CLARIFY and second.awaiting == "slot:order_id"
    third = await say(o, "ok")  # still no answer: the limit (max_clarifications = 2) is reached
    assert third.decision is Decision.HANDOFF
    trace = (await c.traces.for_conversation(T, C))[-1]
    assert trace.escalation_reason is EscalationReason.LOW_CONFIDENCE and "order_id" in trace.decision_reason
    (case,) = await c.cases.list(T)
    assert case.package.reason is EscalationReason.LOW_CONFIDENCE


async def test_progress_resets_the_count(c: Container) -> None:
    o = orch(c)
    await say(o, "I want a refund")
    await say(o, "hmm")  # one clarification for order_id
    after_answer = await say(o, "NS-20745")  # answered: now the phone is asked, a fresh question
    assert after_answer.awaiting == "slot:phone"
    assert (await session_of(c)).clarifications == 0
    again = await say(o, "hmm")
    assert again.awaiting == "slot:phone" and (await session_of(c)).clarifications == 1


async def test_unintelligible_messages_get_two_clarifications_then_a_handoff(c: Container) -> None:
    o = orch(c)
    one = await say(o, "asdf qwer zxcv")
    two = await say(o, "lkjh poiu")
    three = await say(o, "mnbv")
    assert [(r.decision, r.awaiting) for r in (one, two)] == [(Decision.CLARIFY, "detail")] * 2
    assert three.decision is Decision.HANDOFF
    assert (await c.traces.for_conversation(T, C))[-1].escalation_reason is EscalationReason.LOW_CONFIDENCE


async def test_a_new_intent_starts_its_own_count(c: Container) -> None:
    o = orch(c)
    await say(o, "I want a refund")
    await say(o, "hmm")  # count 1 for the refund
    session = await session_of(c)
    session.active_intent, session.awaiting = None, None  # the refund was dropped
    await c.sessions.save(session)
    await say(o, "please cancel my order")
    assert (await session_of(c)).clarifications == 0


# ---- unsupported ----


async def test_a_required_argument_with_no_possible_source_is_handed_off_as_unsupported(c: Container) -> None:
    tenant = c.tenants.get(T)
    broken = tenant.intents["return_request"].model_copy(
        update={
            "required_slots": ("order_id",),
            "argument_map": {"order_id": "slot:order_id"},
        }  # nothing fills "reason"
    )
    custom = tenant.model_copy(update={"intents": {**tenant.intents, "return_request": broken}})
    reply = await say(orch(c, tenants=TenantRegistry({T: custom})), "I want to return my order NS-20512")
    assert reply.decision is Decision.HANDOFF
    trace = (await c.traces.for_conversation(T, C))[-1]
    assert trace.escalation_reason is EscalationReason.UNSUPPORTED and "reason" in trace.decision_reason


# ---- the shop tools cannot be listed ----


class DownShop:
    async def list_tools(self, tenant_id: str) -> list[Any]:
        raise UpstreamError("shop", "BACKEND_UNAVAILABLE", "down", retryable=True)

    async def call_tool(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover - not reached
        raise AssertionError("no tool may be called")


async def test_when_the_tool_list_is_unavailable_the_safe_assumption_is_that_identity_is_needed(c: Container) -> None:
    o = orch(c, capabilities=DownShop())
    await say(o, "Where is my order NS-20877?")
    reply = await say(o, "ok")
    trace = (await c.traces.for_conversation(T, C))[-1]
    assert reply.awaiting == "slot:phone" and "the shop tool list is unavailable" in trace.errors


async def test_without_a_shop_connection_the_tenant_slots_still_work(c: Container) -> None:
    reply = await say(orch(c, capabilities=None), "Where is my order?")
    assert reply.awaiting == "slot:order_id"


# ---- every slot of the shipped tenant can be asked ----


@pytest.mark.parametrize("slot", ["order_id", "phone", "item", "reason", "amount", "new_address", "description"])
@pytest.mark.parametrize("locale", list(Locale))
def test_there_is_a_question_for_every_common_slot(slot: str, locale: Locale) -> None:
    assert render(f"ask_{slot}", locale)
