"""A person's decision on a waiting action: it executes once, never overrides a deny, and the customer is told."""

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from team_b.brain.approval import CaseNotYoursError, NothingToDecideError
from team_b.container import Container, build_container, inject
from team_b.domain.actions import ActionState
from team_b.domain.decision import Decision
from team_b.domain.handoff import CaseStatus, HandoffCase
from team_b.domain.trace import DecisionTrace
from tests.support import make_settings

T, C = "shop_001", "conv-a11"
C104 = "01098765405"  # owns NS-20934 (total 3450: a refund needs a person)


@pytest.fixture
def container(tmp_path: Path) -> Container:
    return build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))


def bot(container: Container) -> Any:
    assert container.orchestrator is not None
    return container.orchestrator


def backend(container: Container, order_id: str) -> dict[str, Any]:
    assert container.shop is not None
    return container.shop._tenants[T].shop.orders[order_id]  # type: ignore[attr-defined, no-any-return]


async def waiting(container: Container, conversation: str = C, text: str = "I want a refund for order NS-20934") -> str:
    """The large refund asked for and handed to a person; returns the case id."""
    await bot(container).handle_turn(T, conversation, text)
    reply = await bot(container).handle_turn(T, conversation, C104)
    assert reply.handoff_case_id and reply.decision is Decision.HANDOFF
    return str(reply.handoff_case_id)


def writes(container: Container) -> list[Any]:
    assert container.shop is not None
    return [e for e in container.shop.audit_log(T) if e.tool == "create_refund"]


async def case_of(container: Container, case_id: str) -> HandoffCase:
    case = await container.cases.get(T, case_id)
    assert case is not None
    return case


async def outbox(container: Container, conversation: str = C) -> list[str]:
    session = await container.sessions.load(T, conversation)
    assert session is not None
    return [m.text for m in session.outbox]


async def human_traces(container: Container, conversation: str = C) -> list[DecisionTrace]:
    return [t for t in await container.traces.for_conversation(T, conversation) if t.kind == "human_action"]


# ---- approve ----


async def test_an_approval_executes_once_with_the_approval_recorded(container: Container) -> None:
    case_id = await waiting(container)
    assert writes(container) == []
    await bot(container).human_decide(case_id, "agent-7", True, "checked with the customer")
    [entry] = writes(container)
    assert (entry.actor, entry.approval_id, entry.applied) == ("human", case_id, True)
    assert entry.arguments == {"order_id": "NS-20934", "amount": 3450}
    session = await container.sessions.load(T, C)
    assert session is not None and session.actions[0].state is ActionState.CONFIRMED
    approval = session.actions[0].human_approval
    assert approval is not None and (approval.approved_by, approval.case_id) == ("agent-7", case_id)


async def test_the_customer_is_told_with_the_shops_reference(container: Container) -> None:
    case_id = await waiting(container)
    await bot(container).human_decide(case_id, "agent-7", True)
    [message] = await outbox(container)
    assert "REF-" in message and "Done" in message


async def test_the_trace_is_a_human_action_with_the_policy_answer_and_the_write(container: Container) -> None:
    case_id = await waiting(container)
    await bot(container).human_decide(case_id, "agent-7", True)
    [trace] = await human_traces(container)
    assert trace.decision is Decision.EXECUTE and trace.kind == "human_action"
    [call] = [c for c in trace.tool_calls if c.operation_kind != "read"]
    assert (call.actor, call.approval_id) == ("human", case_id)
    assert [p.reason_code for p in trace.policy] == ["APPROVED_BY_HUMAN"] and trace.policy[0].decision == "allow"
    assert [(p.tool, p.state) for p in trace.proposals] == [("create_refund", "confirmed")]


async def test_the_case_records_the_approval_and_nothing_waits_any_more(container: Container) -> None:
    case_id = await waiting(container)
    await bot(container).human_decide(case_id, "agent-7", True, "ok")
    case = await case_of(container, case_id)
    assert case.pending_approval is None and case.status is CaseStatus.CLAIMED and case.claimed_by == "agent-7"
    assert case.events[-1].kind == "approved_and_done" and case.events[-1].note == "ok"


async def test_a_double_approval_executes_once(container: Container) -> None:
    case_id = await waiting(container)
    await bot(container).human_decide(case_id, "agent-7", True)
    await bot(container).human_decide(case_id, "agent-7", True)
    assert len(writes(container)) == 1 and len(await outbox(container)) == 1
    assert (await case_of(container, case_id)).events[-1].kind == "decision_ignored"


async def test_two_approvals_at_the_same_time_execute_once(container: Container) -> None:
    case_id = await waiting(container)
    await asyncio.gather(
        bot(container).human_decide(case_id, "agent-7", True), bot(container).human_decide(case_id, "agent-7", True)
    )
    assert len(writes(container)) == 1 and container.shop is not None and container.shop.change_count(T) == 1
    assert len(await outbox(container)) == 1 and len(await human_traces(container)) == 1


async def test_an_approval_after_a_rejection_does_nothing(container: Container) -> None:
    case_id = await waiting(container)
    await bot(container).human_decide(case_id, "agent-7", False)
    await bot(container).human_decide(case_id, "agent-7", True)
    assert writes(container) == [] and len(await outbox(container)) == 1


# ---- an approval never overrides a no ----


async def test_an_order_that_became_non_refundable_executes_nothing(container: Container) -> None:
    case_id = await waiting(container)
    backend(container, "NS-20934")["delivered_at"] = "2026-08-01"
    await bot(container).human_decide(case_id, "agent-7", True)
    assert writes(container) == []
    session = await container.sessions.load(T, C)
    assert session is not None and session.actions[0].state is ActionState.BLOCKED
    case = await case_of(container, case_id)
    assert case.events[-1].kind == "approval_denied" and "R-REFUND-14D" in case.events[-1].note
    [message] = await outbox(container)
    assert "more than 14 days" in message  # the customer hears the rule's own wording
    [trace] = await human_traces(container)
    assert [p.decision for p in trace.policy] == ["deny"]
    assert not [c for c in trace.tool_calls if c.operation_kind != "read"]


async def test_an_order_that_changed_hands_executes_nothing(container: Container) -> None:
    case_id = await waiting(container)
    backend(container, "NS-20934")["customer_id"] = "C-100"
    await bot(container).human_decide(case_id, "agent-7", True)
    assert writes(container) == [] and (await case_of(container, case_id)).events[-1].kind == "approval_blocked"
    assert "Wool coat" not in " ".join(await outbox(container))


async def test_an_amount_that_changed_is_not_approved_blindly(container: Container) -> None:
    case_id = await waiting(container)
    backend(container, "NS-20934")["order_total"] = 3999
    await bot(container).human_decide(case_id, "agent-7", True)
    assert writes(container) == []
    assert (await case_of(container, case_id)).events[-1].kind == "approval_blocked"


async def test_an_approval_does_not_use_a_rule_checker_that_is_down(container: Container) -> None:
    case_id = await waiting(container)
    inject(container, "rule_checker", {"switch": "fail_next", "times": 2})
    await bot(container).human_decide(case_id, "agent-7", True)
    assert writes(container) == []
    case = await case_of(container, case_id)
    assert case.pending_approval is not None and case.events[-1].kind == "decision_failed"  # still waiting
    await bot(container).human_decide(case_id, "agent-7", True)  # try again: now it works
    assert len(writes(container)) == 1


async def test_an_approval_does_not_use_an_unreadable_order(container: Container) -> None:
    case_id = await waiting(container)
    inject(container, "shop", {"switch": "fail_next", "tool": "get_order", "code": "BACKEND_UNAVAILABLE", "times": 2})
    await bot(container).human_decide(case_id, "agent-7", True)
    assert writes(container) == [] and (await case_of(container, case_id)).pending_approval is not None


# ---- the result is verified like any other ----


async def test_an_unclear_result_after_approval_is_not_reported_as_done(container: Container) -> None:
    case_id = await waiting(container)
    inject(container, "shop", {"switch": "uncertain", "tool": "create_refund", "applied": True})
    await bot(container).human_decide(case_id, "agent-7", True)
    assert len(writes(container)) == 1
    [message] = await outbox(container)
    assert "REF-" not in message and "Done" not in message and "colleague will check" in message
    [trace] = await human_traces(container)
    assert trace.decision is Decision.HANDOFF and trace.escalation_reason is not None
    assert (await case_of(container, case_id)).events[-1].kind == "approved_unverified"


async def test_a_clear_failure_after_approval_is_told_and_not_retried(container: Container) -> None:
    case_id = await waiting(container)
    inject(container, "shop", {"switch": "fail_next", "tool": "create_refund", "code": "NOT_FOUND", "times": 3})
    await bot(container).human_decide(case_id, "agent-7", True)
    assert len(writes(container)) == 1
    assert "Nothing was changed" in (await outbox(container))[0]


# ---- reject ----


async def test_a_rejection_cancels_the_proposal_and_informs_the_customer(container: Container) -> None:
    case_id = await waiting(container)
    await bot(container).human_decide(case_id, "agent-7", False, "not covered")
    assert writes(container) == []
    session = await container.sessions.load(T, C)
    assert session is not None and session.actions[0].state is ActionState.CANCELLED
    [message] = await outbox(container)
    assert "could not approve" in message
    case = await case_of(container, case_id)
    assert (
        case.pending_approval is None and case.events[-1].kind == "rejected" and case.events[-1].note == "not covered"
    )
    [trace] = await human_traces(container)
    assert trace.decision is Decision.REFUSE and not trace.tool_calls


async def test_the_customer_message_is_in_their_language(container: Container) -> None:
    case_id = await waiting(container, "ar-chat", "عايز فلوسي للاوردر NS-20934")
    await bot(container).human_decide(case_id, "agent-7", False)
    [message] = await outbox(container, "ar-chat")
    assert "فريقنا" in message


# ---- who may decide, and what there is to decide ----


async def test_another_person_cannot_decide_a_claimed_case(container: Container) -> None:
    case_id = await waiting(container)
    await bot(container).claim(case_id, "agent-1")
    with pytest.raises(CaseNotYoursError):
        await bot(container).human_decide(case_id, "agent-2", True)
    assert writes(container) == []


async def test_a_closed_case_cannot_be_decided(container: Container) -> None:
    case_id = await waiting(container)
    await bot(container).claim(case_id, "agent-1")
    await bot(container).resolve(case_id, "agent-1")
    with pytest.raises(CaseNotYoursError):
        await bot(container).human_decide(case_id, "agent-1", True)
    assert writes(container) == []


async def test_a_case_without_a_waiting_action_cannot_be_decided(container: Container) -> None:
    reply = await bot(container).handle_turn(T, "plain", "I want to talk to a human")
    with pytest.raises(NothingToDecideError):
        await bot(container).human_decide(reply.handoff_case_id or "", "agent-1", True)
