"""Edges of the conversation flow: out of scope, and a new request not inheriting the last request's order."""

from datetime import date
from pathlib import Path

import pytest

from team_b.container import Container, build_container
from team_b.domain.decision import Decision
from tests.support import make_settings

T, C = "shop_001", "conv-a10"
C100 = "01012345601"


@pytest.fixture
def container(tmp_path: Path) -> Container:
    return build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))


async def say(container: Container, text: str):  # type: ignore[no-untyped-def]
    assert container.orchestrator is not None
    return await container.orchestrator.handle_turn(T, C, text)


@pytest.mark.parametrize(
    "text",
    [
        "Please book me a flight to Dubai",
        "I want to buy a new phone from you",
        "عايز احجز تذكرة طيارة لدبي",
        "3ayez a7gez ta2ker tayara",
    ],
)
async def test_a_clear_request_for_something_else_is_refused_with_an_offer_of_a_person(
    container: Container, text: str
) -> None:
    reply = await say(container, text)
    assert reply.decision is Decision.REFUSE and reply.handoff_case_id is None
    assert any(w in reply.text for w in ("person", "حد من الفريق", "7ad", "wa7ed"))


@pytest.mark.parametrize(
    "text", ["asdf qwer zxcv", "please", "yes please", "what is the capital of France?", "hello there friend"]
)
async def test_unclear_or_other_messages_are_not_refused_as_out_of_scope(container: Container, text: str) -> None:
    reply = await say(container, text)
    assert reply.decision is not Decision.REFUSE


async def test_a_refusal_out_of_scope_does_not_end_the_conversation(container: Container) -> None:
    await say(container, "Please book me a flight to Dubai")
    reply = await say(container, "Where is my order NS-20877?")
    assert reply.decision is Decision.VERIFY_IDENTITY


async def test_a_new_request_after_an_answered_lookup_does_not_inherit_its_order(container: Container) -> None:
    await say(container, f"Where is my order NS-20877? My phone is {C100}")
    reply = await say(container, "I want a refund")
    assert reply.decision is Decision.CLARIFY and reply.awaiting == "order_choice"  # asked which order, not NS-20877
    session = await container.sessions.load(T, C)
    assert session is not None and "order_id" not in session.slots and session.active_intent == "refund_request"


async def test_the_same_request_again_keeps_its_order(container: Container) -> None:
    await say(container, f"Where is my order NS-20877? My phone is {C100}")
    reply = await say(container, "where is my order again?")
    assert reply.decision is Decision.ANSWER and "NS-20877" in reply.text


async def test_a_new_request_that_names_its_order_uses_it(container: Container) -> None:
    await say(container, f"Where is my order NS-20877? My phone is {C100}")
    reply = await say(container, "I want a refund for order NS-20745")
    assert reply.decision is Decision.CONFIRM and "NS-20745" in reply.text


async def test_nothing_is_dropped_while_the_agent_waits_for_an_answer(container: Container) -> None:
    await say(container, "I want to return order NS-20790, the size is wrong")
    await say(container, "Where is my order NS-20790?")  # asked in the middle of the return: waits behind it
    session = await container.sessions.load(T, C)
    assert session is not None and session.active_intent == "return_request" and session.slots["order_id"] == "NS-20790"
    assert session.intent_queue == ["order_status"]
