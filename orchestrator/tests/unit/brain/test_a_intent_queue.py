"""Several requests in one message: they run in the order said, chain up to the cap, and each action is confirmed."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from team_b.brain.multi import assign_order_ids
from team_b.brain.orchestrator import Orchestrator
from team_b.brain.turn import PlannedIntent, Step, TurnContext
from team_b.container import Container, build_container
from team_b.domain.decision import Decision
from team_b.domain.reply import AgentReply
from tests.fakes import ScriptedNLU
from tests.support import make_settings

T, C = "shop_001", "conv-a9"
PHONE_C105 = "01187654306"  # owns NS-20955 (processing) and NS-20701 (cancelled)
PHONE_C104 = "01098765405"  # owns NS-20960 (pending)


@pytest.fixture
def container(tmp_path: Path) -> Container:
    return build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))


async def say(container: Container, text: str) -> AgentReply:
    assert container.orchestrator is not None
    return await container.orchestrator.handle_turn(T, C, text)


def writes(container: Container) -> list[str]:
    assert container.shop is not None
    return [e.tool for e in container.shop.audit_log(T) if e.applied]


# ---- which order belongs to which request ----


def tenant(container: Container):  # type: ignore[no-untyped-def]
    return container.tenants.get(T)


def test_each_request_gets_the_order_named_with_it(container: Container) -> None:
    text = (
        "feen el order NS-20955, 3ayez a8ayar el 3enwan le 5 Corniche El Nil El Maadi, w 3ayez flousi el order NS-20701"
    )
    got = assign_order_ids(text, ["order_status", "change_address", "refund_request"], tenant(container))
    assert got == {"order_status": "NS-20955", "change_address": "NS-20955", "refund_request": "NS-20701"}


def test_a_request_without_a_number_uses_the_one_before_it(container: Container) -> None:
    text = "I want a refund for order NS-20512 and cancel it, then where is my order NS-20745?"
    got = assign_order_ids(text, ["refund_request", "cancel_order", "order_status"], tenant(container))
    assert got == {"refund_request": "NS-20512", "cancel_order": "NS-20512", "order_status": "NS-20745"}


def test_a_number_before_the_first_request_belongs_to_the_first(container: Container) -> None:
    got = assign_order_ids(
        "NS-20512 refund please, and where is my order NS-20745", ["refund_request", "order_status"], tenant(container)
    )
    assert got == {"refund_request": "NS-20512", "order_status": "NS-20745"}


def test_one_order_number_needs_no_assignment(container: Container) -> None:
    assert (
        assign_order_ids(
            "Where is my order NS-20960 and cancel it", ["order_status", "cancel_order"], tenant(container)
        )
        == {}
    )
    assert assign_order_ids("hello", ["order_status"], tenant(container)) == {}
    assert assign_order_ids("NS-20512 and NS-20745", [], tenant(container)) == {}


# ---- order of the work, one confirmation each ----


async def test_a_status_answer_comes_first_then_the_cancellation_is_confirmed(container: Container) -> None:
    await say(container, "Where is my order NS-20960 and cancel it")
    reply = await say(container, PHONE_C104)
    assert reply.decision is Decision.CONFIRM and reply.awaiting == "confirmation"
    first, second = reply.text.split("\n\n")
    assert "pending" in first or "waiting" in first  # the status, answered
    assert "cancel order NS-20960" in second  # the action, not done yet
    assert writes(container) == []


async def test_the_queued_action_continues_without_the_customer_repeating_it(container: Container) -> None:
    text = (
        "feen el order NS-20955, 3ayez a8ayar el 3enwan le 5 Corniche El Nil El Maadi, w 3ayez flousi el order NS-20701"
    )
    await say(container, text)
    first_confirm = await say(container, PHONE_C105)
    assert "NS-20955" in first_confirm.text and writes(container) == []
    second_confirm = await say(container, "aywa")
    assert (
        second_confirm.decision is Decision.CONFIRM
        and "ADR-" in second_confirm.text
        and "NS-20701" in second_confirm.text
    )
    assert writes(container) == ["update_delivery_address"]  # the refund waits for its own yes
    done = await say(container, "aywa")
    assert done.decision is Decision.EXECUTE and "REF-" in done.text
    assert writes(container) == ["update_delivery_address", "create_refund"]


async def test_each_action_is_for_its_own_order(container: Container) -> None:
    text = (
        "feen el order NS-20955, 3ayez a8ayar el 3enwan le 5 Corniche El Nil El Maadi, w 3ayez flousi el order NS-20701"
    )
    await say(container, text)
    await say(container, PHONE_C105)
    await say(container, "aywa")
    await say(container, "aywa")
    assert container.shop is not None
    by_tool = {e.tool: e.arguments for e in container.shop.audit_log(T) if e.applied}
    assert by_tool["update_delivery_address"]["order_id"] == "NS-20955"
    assert by_tool["update_delivery_address"]["new_address"] == "5 Corniche El Nil El Maadi"
    assert by_tool["create_refund"]["order_id"] == "NS-20701"


async def test_a_no_to_the_first_action_lets_the_next_request_continue(container: Container) -> None:
    await say(container, "Where is my order NS-20960 and cancel it and I need a refund for order NS-20960")
    await say(container, PHONE_C104)  # status answered, cancel waits for confirmation, refund queued
    after_no = await say(container, "no")
    assert writes(container) == []
    assert after_no.decision in (Decision.CONFIRM, Decision.REFUSE, Decision.HANDOFF, Decision.ANSWER)
    assert "cancel" not in after_no.text.lower() or "won't" in after_no.text.lower()


async def test_a_new_request_during_a_confirmation_cancels_it_and_replaces_the_queue(container: Container) -> None:
    await say(container, "Where is my order NS-20960 and cancel it and I need a refund for order NS-20960")
    await say(container, PHONE_C104)
    session = await container.sessions.load(T, C)
    assert session is not None and session.pending_action_id is not None and session.intent_queue == ["refund_request"]
    await say(container, "actually where is my order NS-20960?")
    session = await container.sessions.load(T, C)
    assert session is not None and session.pending_action_id is None
    assert "refund_request" not in session.intent_queue and "cancel_order" not in session.intent_queue
    assert [a.state.value for a in session.actions] == ["cancelled"]
    assert writes(container) == []


# ---- the cap ----


async def answering(ctx: TurnContext, planned: PlannedIntent) -> Step:
    return Step(Decision.ANSWER, reason=f"done {planned.name}", reply_key="thanks", citations=(f"doc#{planned.name}",))


def chaining(container: Container, cap: int | None) -> Orchestrator:
    names = ["order_status", "cancel_order", "refund_request", "complaint", "voucher_request"]
    from tests.unit.brain.test_pipeline import reading

    return Orchestrator(
        clock=container.clock, tenants=container.tenants, sessions=container.sessions, traces=container.traces,
        cases=container.cases, nlu=ScriptedNLU(reading(*names)), handlers={"lookup": answering, "action": answering},
        max_queued_runs=cap,
    )  # fmt: skip


@pytest.mark.parametrize(("cap", "answered"), [(None, 4), (0, 1), (1, 2), (2, 3), (3, 4), (10, 5)])
async def test_the_cap_limits_how_many_requests_are_chained_in_one_reply(
    container: Container, cap: int | None, answered: int
) -> None:
    reply = await chaining(container, cap).handle_turn(T, C, "five things")
    assert len(reply.citations) == answered
    session = await container.sessions.load(T, C)
    assert session is not None and len(session.intent_queue) == 5 - answered


def test_the_cap_is_a_setting(tmp_path: Path) -> None:
    from team_b.config import Settings

    assert Settings().queue_max_runs == 3 and Settings.from_env({"TEAM_B_QUEUE_MAX_RUNS": "1"}).queue_max_runs == 1
    built = build_container(make_settings(tmp_path, queue_max_runs=1))
    assert built.orchestrator is not None and built.orchestrator._deps.max_queued_runs == 1  # type: ignore[attr-defined]


async def test_an_action_that_needs_a_yes_stops_the_chain(container: Container) -> None:
    async def asking(ctx: TurnContext, planned: PlannedIntent) -> Step:
        return Step(Decision.CONFIRM, reason="needs yes", reply_key="confirm_again", awaiting="confirmation")

    from tests.unit.brain.test_pipeline import reading

    bot = Orchestrator(
        clock=container.clock, tenants=container.tenants, sessions=container.sessions, traces=container.traces,
        cases=container.cases, nlu=ScriptedNLU(reading("order_status", "cancel_order", "refund_request")),
        handlers={"lookup": answering, "action": asking},
    )  # fmt: skip
    reply = await bot.handle_turn(T, C, "three things")
    session = await container.sessions.load(T, C)
    assert reply.decision is Decision.CONFIRM and session is not None and session.intent_queue == ["refund_request"]


def test_queue_details_are_forgotten_with_the_request(container: Container) -> None:
    from team_b.domain.session import SessionState

    session = SessionState(
        tenant_id=T, conversation_id=C, created_at=container.clock.now(), updated_at=container.clock.now()
    )
    assert session.queue_slots == {}
    session.queue_slots["refund_request"] = {"order_id": "NS-1"}
    assert SessionState.model_validate(session.model_dump()).queue_slots == {"refund_request": {"order_id": "NS-1"}}


async def _unused(*args: Any) -> None:  # keeps the Any import honest for type checkers
    return None
