"""Asking instead of guessing, with the stand-in shop: "return or exchange?" and "which of your orders?"."""

from typing import Any

import pytest

from team_b.brain.orchestrator import Orchestrator
from team_b.brain.templates import render
from team_b.container import Container
from team_b.contracts.tools import ToolCallRequest, ToolResult
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.reply import AgentReply
from team_b.domain.session import SessionIdentity
from team_b.domain.understanding import IntentCandidate, Language, Locale, NLUResult
from tests.fakes import FakeEvidence, ScriptedNLU

T, C = "shop_001", "conv-1"


def orch(c: Container, **kw: Any) -> Orchestrator:
    options: dict[str, Any] = {"evidence": FakeEvidence(), "tenants": c.tenants, "capabilities": c.capabilities, **kw}
    return Orchestrator(clock=c.clock, sessions=c.sessions, traces=c.traces, cases=c.cases, **options)


async def say(o: Orchestrator, text: str) -> AgentReply:
    return await o.handle_turn(T, C, text)


async def session_of(c: Container):  # type: ignore[no-untyped-def]
    found = await c.sessions.load(T, C)
    assert found is not None
    return found


async def verified_as(c: Container, customer_id: str, language: Language = Language.EN) -> Orchestrator:
    """A conversation whose customer has already proved who they are (the identity step is built later)."""
    o = orch(c)
    await say(o, "Hello")
    session = await session_of(c)
    session.identity = SessionIdentity(verified=True, customer_id=customer_id, method="test")
    session.language = language
    await c.sessions.save(session)
    return o


async def last(c: Container):  # type: ignore[no-untyped-def]
    return (await c.traces.for_conversation(T, C))[-1]


# ---- do you mean A or B? ----


async def test_two_requests_that_cannot_both_be_meant_get_a_question(c: Container) -> None:
    reply = await say(orch(c), "I want to return or exchange order NS-20790")
    assert (reply.decision, reply.awaiting) == (Decision.CLARIFY, "intent_choice")
    assert reply.text == render("disambiguate_intent", Locale.EN, a="return the item", b="exchange it")
    session = await session_of(c)
    assert session.choice_options == ["return_request", "exchange_request"]
    assert session.active_intent is None and session.slots["order_id"] == "NS-20790"  # the order is not lost


@pytest.mark.parametrize("answer", ["the second", "second", "2", "exchange", "I mean exchange"])
async def test_the_answer_picks_the_request_and_the_flow_goes_on(c: Container, answer: str) -> None:
    o = orch(c)
    await say(o, "I want to return or exchange order NS-20790")
    reply = await say(o, answer)
    session = await session_of(c)
    assert session.active_intent == "exchange_request" and session.choice_options == []
    assert (reply.decision, reply.awaiting) == (Decision.VERIFY_IDENTITY, "slot:phone")  # the normal next question


async def test_the_first_option_works_too(c: Container) -> None:
    o = orch(c)
    await say(o, "I want to return or exchange order NS-20790")
    await say(o, "the first")
    assert (await session_of(c)).active_intent == "return_request"


async def test_both_means_one_after_the_other(c: Container) -> None:
    o = orch(c)
    await say(o, "I want to return or exchange order NS-20790")
    await say(o, "both")
    session = await session_of(c)
    assert (session.active_intent, session.intent_queue) == ("return_request", ["exchange_request"])


async def test_an_arabic_customer_is_asked_and_answers_in_arabic(c: Container) -> None:
    o = orch(c)
    asked = await say(o, "عايز ارجع او ابدل الاوردر NS-20790")
    assert asked.locale is Locale.AR and asked.text == render(
        "disambiguate_intent", Locale.AR, a="ترجيع المنتج", b="استبدال المنتج"
    )
    await say(o, "التاني")
    assert (await session_of(c)).active_intent == "exchange_request"


async def test_an_arabizi_customer_is_asked_and_answers_in_arabizi(c: Container) -> None:
    o = orch(c)
    asked = await say(o, "3ayez araga3 aw abdel el order NS-20790")
    assert asked.locale is Locale.ARABIZI and "return el montag" in asked.text and "estebdal el montag" in asked.text
    await say(o, "el awel")
    assert (await session_of(c)).active_intent == "return_request"


async def test_an_unclear_answer_is_asked_again_then_handed_off(c: Container) -> None:
    o = orch(c)
    await say(o, "I want to return or exchange order NS-20790")
    again = await say(o, "hmm")
    assert again.awaiting == "intent_choice" and again.text.startswith("Do you want")
    final = await say(o, "dunno")
    assert final.decision is Decision.HANDOFF
    assert (await last(c)).escalation_reason is EscalationReason.LOW_CONFIDENCE


async def test_changing_the_subject_drops_the_question(c: Container) -> None:
    o = orch(c)
    await say(o, "I want to return or exchange order NS-20790")
    reply = await say(o, "where is my order NS-20877")
    session = await session_of(c)
    assert session.choice_options == [] and session.active_intent == "order_status"
    assert reply.decision is Decision.VERIFY_IDENTITY  # the new request is handled normally


async def test_the_rest_of_the_message_waits_in_the_queue(c: Container) -> None:
    nlu = ScriptedNLU(
        NLUResult(
            language=Language.EN,
            language_confidence=0.9,
            intents=tuple(
                IntentCandidate(name=n, confidence=0.8) for n in ("order_status", "return_request", "exchange_request")
            ),
        )
    )
    reply = await say(orch(c, nlu=nlu), "status, and return or exchange")
    assert reply.awaiting == "intent_choice" and (await session_of(c)).intent_queue == ["order_status"]


async def test_sensible_pairs_and_a_clear_winner_are_not_questioned(c: Container) -> None:
    reply = await say(orch(c), "cancel my order and give me a refund")
    assert reply.awaiting != "intent_choice"
    strong = NLUResult(
        language=Language.EN,
        language_confidence=0.9,
        intents=(
            IntentCandidate(name="return_request", confidence=0.9),
            IntentCandidate(name="exchange_request", confidence=0.6),
        ),
    )
    other = await orch(c, nlu=ScriptedNLU(strong)).handle_turn(T, "other", "return mostly")
    assert other.awaiting != "intent_choice"


# ---- which of your orders? ----


async def test_a_verified_customer_without_an_order_id_is_shown_their_orders(c: Container) -> None:
    o = await verified_as(c, "C-100")
    reply = await say(o, "I want a refund")
    assert (reply.decision, reply.awaiting) == (Decision.CLARIFY, "order_choice")
    assert "1. NS-**877, 20 Sep\n2. NS-**745, 19 Sep\n3. NS-**512, 1 Sep" in reply.text  # newest first, masked
    assert (await session_of(c)).order_choices == ["NS-20877", "NS-20745", "NS-20512"]
    assert "NS-20877" not in reply.text
    (call,) = (await last(c)).tool_calls
    assert (call.tool, call.operation_kind, call.status) == ("list_customer_orders", "read", "success")
    assert call.arguments == {"customer_id": "C-100"} and call.audit_id


@pytest.mark.parametrize(
    ("answer", "order"),
    [("the first", "NS-20877"), ("the second", "NS-20745"), ("the third", "NS-20512"), ("2", "NS-20745")],
)
async def test_an_ordinal_picks_the_order(c: Container, answer: str, order: str) -> None:
    o = await verified_as(c, "C-100")
    await say(o, "I want a refund")
    reply = await say(o, answer)
    session = await session_of(c)
    assert session.slots["order_id"] == order and session.order_choices == []
    assert "not built yet" in reply.text or reply.awaiting == "detail"  # nothing else is missing: the flow goes on


async def test_the_customer_can_type_the_order_number_instead(c: Container) -> None:
    o = await verified_as(c, "C-100")
    await say(o, "I want a refund")
    await say(o, "NS-20512")
    session = await session_of(c)
    assert session.slots["order_id"] == "NS-20512" and session.order_choices == []


@pytest.mark.parametrize(
    ("language", "text", "order"),
    [
        (Language.AR, "الأول", "NS-20877"),
        (Language.AR, "التاني", "NS-20745"),
        (Language.ARABIZI, "el awel", "NS-20877"),
    ],
)
async def test_arabic_and_arabizi_ordinals(c: Container, language: Language, text: str, order: str) -> None:
    o = await verified_as(c, "C-100", language)
    asked = await say(o, "عايز فلوسي" if language is Language.AR else "3ayez flousi")
    assert asked.awaiting == "order_choice" and asked.locale is (
        Locale.AR if language is Language.AR else Locale.ARABIZI
    )
    await say(o, text)
    assert (await session_of(c)).slots["order_id"] == order


async def test_an_unusable_answer_shows_the_list_again_and_then_hands_off(c: Container) -> None:
    o = await verified_as(c, "C-100")
    await say(o, "I want a refund")
    again = await say(o, "hmm")
    assert again.awaiting == "order_choice" and "NS-**877" in again.text
    final = await say(o, "dunno")
    assert final.decision is Decision.HANDOFF
    assert (await last(c)).escalation_reason is EscalationReason.LOW_CONFIDENCE


async def test_changing_the_subject_drops_the_list_for_this_turn(c: Container) -> None:
    o = await verified_as(c, "C-100")
    await say(o, "I want a refund")
    await say(o, "actually, what is your return policy?")
    detail = next(s.detail for s in (await last(c)).steps if s.stage == "disambiguate")
    assert detail == "topic change: the list is dropped"


async def test_cancelled_orders_are_not_offered(c: Container) -> None:
    o = await verified_as(c, "C-105")  # NS-20955 processing, NS-20701 cancelled
    reply = await say(o, "I want a refund")
    assert (await session_of(c)).order_choices == ["NS-20955"] and "NS-**701" not in reply.text


async def test_an_unverified_customer_is_asked_for_the_number_and_no_list_is_read(c: Container) -> None:
    reply = await say(orch(c), "I want a refund")
    assert reply.awaiting == "slot:order_id" and (await last(c)).tool_calls == ()


async def test_when_the_shop_cannot_list_orders_the_customer_is_asked_for_the_number(c: Container) -> None:
    assert c.shop is not None
    c.shop.fail_next("list_customer_orders", "BACKEND_UNAVAILABLE")
    o = await verified_as(c, "C-100")
    reply = await say(o, "I want a refund")
    assert reply.awaiting == "slot:order_id" and "order list unavailable: BACKEND_UNAVAILABLE" in (await last(c)).errors


async def test_a_failed_list_result_falls_back_to_asking_the_number(c: Container) -> None:
    assert c.shop is not None
    o = await verified_as(c, "C-999")  # a customer the shop does not know: NOT_FOUND
    reply = await say(o, "I want a refund")
    trace = await last(c)
    assert reply.awaiting == "slot:order_id" and trace.tool_calls[0].status == "error"
    assert any(e.startswith("order list failed") for e in trace.errors)


class ManyOrders:
    """A shop that lists five orders, to check the limit of three."""

    async def list_tools(self, tenant_id: str) -> list[Any]:
        return []

    async def call_tool(self, tenant_id: str, request: ToolCallRequest) -> ToolResult:
        orders = [
            {"order_id": f"NS-1000{n}", "order_status": "delivered", "placed_at": f"2026-09-0{n}"} for n in range(1, 6)
        ]
        return ToolResult(status="success", data={"orders": orders}, audit_id="AUD-1")


async def test_at_most_three_orders_are_offered_newest_first(c: Container) -> None:
    o = orch(c, capabilities=ManyOrders())
    await say(o, "Hello")
    session = await session_of(c)
    session.identity = SessionIdentity(verified=True, customer_id="C-100", method="test")
    await c.sessions.save(session)
    reply = await say(o, "I want a refund")
    assert (await session_of(c)).order_choices == ["NS-10005", "NS-10004", "NS-10003"]
    assert reply.text.count("\n") == 4  # the question, three lines, the hint
