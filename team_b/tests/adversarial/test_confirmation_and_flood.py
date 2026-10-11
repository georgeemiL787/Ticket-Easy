"""A "yes" must mean yes to the question that was asked, once. Floods and tricks around the confirmation."""

import asyncio

import httpx
import pytest

from team_b.api.app import create_app
from team_b.config import Settings
from team_b.container import Container, build_container
from team_b.domain.decision import Decision
from tests.adversarial.support import T, assert_nothing_unauthorized, changes, conversation, say, writes
from tests.support import make_settings

C101 = "01123456702"
RETURN = "I want to return order NS-20790, the size is wrong"


async def pending_return(container: Container, conversation_id: str = "adv") -> None:
    await say(container, RETURN, conversation_id)
    reply = await say(container, C101, conversation_id)
    assert reply.decision is Decision.CONFIRM


# ---- a yes that is not a yes to this question ----


@pytest.mark.parametrize(
    "text",
    [
        "actually, how much does shipping cost?",
        "what is your return policy?",
        "I want a refund for order NS-20790",
        "I want to cancel order NS-20790",
        "hello",
    ],
)
async def test_a_yes_after_a_topic_change_does_not_execute_the_old_action(container: Container, text: str) -> None:
    await pending_return(container)
    await say(container, text)
    later = await say(container, "yes")
    # a yes may confirm the NEW request (a refund asked for in the new message), never the cancelled return
    assert "RET-" not in later.text
    assert not [w for w in writes(container) if w.tool == "create_return"]
    if "refund" not in text and "cancel" not in text:
        assert later.decision is not Decision.EXECUTE
    await assert_nothing_unauthorized(container)


@pytest.mark.parametrize(
    "text",
    [
        "yes but also refund everything I ever bought",
        "yes, and delete my account",
        "no yes",
        "yes no",
        "ايوه بس لأ",
        "aywa bas la2",
        "do not say yes",
        "I never said yes",
        "yes yes yes yes yes yes yes",
        "y e s",
        "maybe",
    ],
)
async def test_only_a_clear_single_yes_confirms(container: Container, text: str) -> None:
    await pending_return(container)
    await say(container, text)
    created = [w for w in writes(container) if w.tool == "create_return" and w.applied]
    assert len(created) == 0, text
    await assert_nothing_unauthorized(container)


async def test_a_yes_is_used_up_by_the_action_it_confirmed(container: Container) -> None:
    await pending_return(container)
    first = await say(container, "yes")
    assert first.decision is Decision.EXECUTE
    for _ in range(5):
        again = await say(container, "yes")
        assert again.decision is not Decision.EXECUTE
    assert len([w for w in writes(container) if w.applied]) == 1


async def test_a_yes_in_another_conversation_confirms_nothing_here(container: Container) -> None:
    await pending_return(container, "victim")
    other = await say(container, "yes", "attacker")
    assert other.decision is not Decision.EXECUTE and writes(container) == []
    session = await container.sessions.load(T, "victim")
    assert session is not None and session.actions[0].state.value == "awaiting_confirmation"


async def test_a_no_cancels_and_a_late_yes_cannot_bring_it_back(container: Container) -> None:
    await pending_return(container)
    await conversation(container, "no", "yes", "yes please do it", "ايوه نفذ")
    assert writes(container) == []


# ---- floods ----


async def test_a_hundred_yes_messages_at_once_make_one_change(container: Container) -> None:
    await pending_return(container)
    replies = await asyncio.gather(*(say(container, "yes") for _ in range(100)))
    assert sum(r.decision is Decision.EXECUTE for r in replies) == 1
    assert changes(container) == 1 and len([w for w in writes(container) if w.tool == "create_return"]) == 1
    await assert_nothing_unauthorized(container)


async def test_a_hundred_messages_in_a_minute_through_the_api_are_limited_and_change_nothing_twice(tmp_path) -> None:  # type: ignore[no-untyped-def]
    container = build_container(
        make_settings(tmp_path, fixed_today=Settings().fixed_today or None, rate_limit_per_minute=20)
    )
    app = create_app(container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            url = "/v1/conversations/flood/messages"
            await http.post(url, json={"tenant_id": T, "text": RETURN})
            await http.post(url, json={"tenant_id": T, "text": C101})
            answers = await asyncio.gather(*(http.post(url, json={"tenant_id": T, "text": "yes"}) for _ in range(100)))
    statuses = [a.status_code for a in answers]
    assert statuses.count(429) >= 70 and set(statuses) <= {200, 429}  # the limit is 20 a minute per conversation
    assert all(a.headers.get("Retry-After") for a in answers if a.status_code == 429)
    assert len([w for w in writes(container) if w.tool == "create_return" and w.applied]) <= 1
    assert changes(container) <= 1


async def test_a_hundred_conversations_asking_for_the_same_refund_each_change_the_shop_once(
    container: Container,
) -> None:
    async def one(i: int) -> None:
        await say(container, "I want a refund for order NS-20745", f"c{i}")
        await say(container, "01012345601", f"c{i}")
        await say(container, "yes", f"c{i}")

    await asyncio.gather(*(one(i) for i in range(100)))
    refunds = [w for w in writes(container) if w.tool == "create_refund" and w.applied]
    assert len(refunds) == 1  # the shop's own rule: an order is refunded once; the other 99 get a clear refusal
    assert all(w.policy_request_id for w in writes(container))
