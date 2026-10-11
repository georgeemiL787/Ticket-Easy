"""Proposals and the do-it-once key: a proposal runs at most once, only when authorized, and its result is final."""

import asyncio
import hashlib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from team_b.brain.actions import (
    COORDINATOR,
    ActionCoordinator,
    ProposalError,
    approval_id_of,
    idempotency_key_for,
)
from team_b.brain.turn import TurnContext
from team_b.container import Container, build_container, inject
from team_b.contracts.errors import UpstreamError
from team_b.contracts.policy import HumanApproval, PolicyDecision
from team_b.domain.actions import ActionProposal, ActionState, ExecutionNotAuthorizedError
from team_b.domain.decision import Decision
from team_b.domain.session import SessionIdentity, SessionState
from team_b.domain.trace import DecisionTrace
from tests.support import make_settings

T, C = "shop_001", "conv-a6"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
APPROVAL = HumanApproval(approved_by="agent_7", case_id="case_9", approved_at=NOW)


@pytest.fixture
def container(tmp_path: Path) -> Container:
    return build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))


def context(container: Container, session: SessionState | None = None) -> TurnContext:
    assert container.orchestrator is not None
    session = session or SessionState(tenant_id=T, conversation_id=C, created_at=NOW, updated_at=NOW)
    session.identity = SessionIdentity(verified=True, customer_id="C-100", method="test")
    return TurnContext(
        deps=container.orchestrator._deps, tenant=container.tenants.get(T), session=session, text="yes",
        now=NOW, request_id="req-1", trace_id="trace-1",
    )  # fmt: skip


def ticket_slots(session: SessionState) -> None:
    session.slots.update({"order_id": "NS-20877", "description": "late parcel"})


def decision(effect: str = "allow", request_id: str = "pol-1") -> PolicyDecision:
    return PolicyDecision.model_validate(
        {"request_id": request_id, "decision": effect, "reason_code": "TEST", "citations": ["faq@v1#q01"]}
    )


async def approved_ticket(container: Container, effect: str = "allow") -> tuple[TurnContext, ActionProposal]:
    """A ticket proposal, allowed and confirmed by the customer (APPROVED), ready to execute."""
    ctx = context(container)
    ticket_slots(ctx.session)
    proposal = await COORDINATOR.propose(ctx, ctx.tenant.intents["complaint"])
    proposal.policy_decisions.append(decision(effect))
    proposal.transition(ActionState.AWAITING_CONFIRMATION, at=NOW)
    proposal.transition(ActionState.APPROVED, at=NOW)
    return ctx, proposal


def writes(container: Container) -> list[Any]:
    assert container.shop is not None
    return [e for e in container.shop.audit_log(T) if e.tool == "create_ticket"]


# ---- propose ----


async def test_propose_fills_arguments_from_slots_and_constants(container: Container) -> None:
    ctx = context(container)
    ticket_slots(ctx.session)
    proposal = await COORDINATOR.propose(ctx, ctx.tenant.intents["complaint"])
    assert proposal.tool == "create_ticket" and proposal.capability == "create_ticket"
    assert proposal.arguments == {"subject": "Customer complaint", "description": "late parcel", "order_id": "NS-20877"}
    assert proposal.state is ActionState.PROPOSED and proposal.policy_decisions == [] and proposal.result is None
    assert ctx.session.actions == [proposal] and ctx.proposal_ids == [proposal.proposal_id]


async def test_the_amount_comes_from_the_shop_never_from_the_customer(container: Container) -> None:
    ctx = context(container)
    ctx.session.slots.update({"order_id": "NS-20877", "amount": "9999"})  # the customer asks for far too much
    ctx.session.facts = {"order_id": "NS-20877", "order_total": 890}
    proposal = await COORDINATOR.propose(ctx, ctx.tenant.intents["refund_request"])
    assert proposal.arguments == {"order_id": "NS-20877", "amount": 890}


async def test_the_facts_are_copied_into_the_proposal(container: Container) -> None:
    ctx = context(container)
    ticket_slots(ctx.session)
    ctx.session.facts = {"order_id": "NS-20877", "nested": {"a": 1}}
    proposal = await COORDINATOR.propose(ctx, ctx.tenant.intents["complaint"])
    ctx.session.facts["nested"]["a"] = 2
    ctx.session.facts["order_id"] = "changed"
    assert proposal.facts_snapshot == {"order_id": "NS-20877", "nested": {"a": 1}}


async def test_the_key_is_the_sha256_of_tenant_conversation_and_proposal(container: Container) -> None:
    ctx = context(container)
    ticket_slots(ctx.session)
    proposal = await COORDINATOR.propose(ctx, ctx.tenant.intents["complaint"])
    expected = hashlib.sha256(f"{T}|{C}|{proposal.proposal_id}".encode()).hexdigest()
    assert proposal.idempotency_key == expected == idempotency_key_for(T, C, proposal.proposal_id)


async def test_every_proposal_has_its_own_key(container: Container) -> None:
    ctx = context(container)
    ticket_slots(ctx.session)
    first = await COORDINATOR.propose(ctx, ctx.tenant.intents["complaint"])
    second = await COORDINATOR.propose(ctx, ctx.tenant.intents["complaint"])
    assert first.idempotency_key != second.idempotency_key and first.proposal_id != second.proposal_id
    assert idempotency_key_for(T, "other", first.proposal_id) != first.idempotency_key  # conversation matters
    assert idempotency_key_for("shop_002", C, first.proposal_id) != first.idempotency_key  # tenant matters


async def test_a_proposal_with_a_missing_required_argument_is_refused(container: Container) -> None:
    ctx = context(container)
    ctx.session.slots["order_id"] = "NS-20877"  # no description
    with pytest.raises(ProposalError, match="description"):
        await COORDINATOR.propose(ctx, ctx.tenant.intents["complaint"])
    assert ctx.session.actions == []


async def test_a_proposal_for_an_unpublished_tool_is_refused(container: Container) -> None:
    inject(container, "shop", {"switch": "unpublish", "tool": "create_ticket"})
    ctx = context(container)
    ticket_slots(ctx.session)
    with pytest.raises(ProposalError, match="does not publish"):
        await COORDINATOR.propose(ctx, ctx.tenant.intents["complaint"])


# ---- execute: refusals ----


async def test_nothing_runs_without_a_policy_decision(container: Container) -> None:
    ctx = context(container)
    ticket_slots(ctx.session)
    proposal = await COORDINATOR.propose(ctx, ctx.tenant.intents["complaint"])
    for state in (ActionState.PROPOSED,):
        with pytest.raises(ExecutionNotAuthorizedError):
            await COORDINATOR.execute(ctx, proposal)
        assert proposal.state is state
    assert writes(container) == []


async def test_execution_after_a_deny_is_refused(container: Container) -> None:
    ctx, proposal = await approved_ticket(container, "deny")  # reached APPROVED by mistake, with a deny on record
    with pytest.raises(ExecutionNotAuthorizedError, match="no allow"):
        await COORDINATOR.execute(ctx, proposal)
    assert proposal.state is ActionState.APPROVED and proposal.result is None and writes(container) == []


async def test_a_deny_anywhere_in_the_history_blocks_execution(container: Container) -> None:
    ctx, proposal = await approved_ticket(container)
    proposal.policy_decisions.insert(0, decision("deny", "pol-0"))
    with pytest.raises(ExecutionNotAuthorizedError):
        await COORDINATOR.execute(ctx, proposal)
    assert writes(container) == []


async def test_require_human_needs_an_approval(container: Container) -> None:
    ctx, proposal = await approved_ticket(container, "require_human")
    with pytest.raises(ExecutionNotAuthorizedError):
        await COORDINATOR.execute(ctx, proposal)
    assert writes(container) == []


@pytest.mark.parametrize("state", [ActionState.AWAITING_CONFIRMATION, ActionState.AWAITING_HUMAN, ActionState.PROPOSED])
async def test_only_an_approved_proposal_runs(container: Container, state: ActionState) -> None:
    ctx = context(container)
    ticket_slots(ctx.session)
    proposal = await COORDINATOR.propose(ctx, ctx.tenant.intents["complaint"])
    proposal.policy_decisions.append(decision())
    if state is not ActionState.PROPOSED:
        proposal.transition(state, at=NOW)
    with pytest.raises(ExecutionNotAuthorizedError, match="not executed"):
        await COORDINATOR.execute(ctx, proposal)
    assert writes(container) == []


@pytest.mark.parametrize("end", [ActionState.CANCELLED, ActionState.BLOCKED, ActionState.FAILED])
async def test_a_cancelled_blocked_or_failed_proposal_never_runs(container: Container, end: ActionState) -> None:
    ctx = context(container)
    ticket_slots(ctx.session)
    proposal = await COORDINATOR.propose(ctx, ctx.tenant.intents["complaint"])
    proposal.policy_decisions.append(decision())
    proposal.transition(ActionState.AWAITING_CONFIRMATION, at=NOW)
    proposal.transition(end, at=NOW)
    with pytest.raises(ExecutionNotAuthorizedError):
        await COORDINATOR.execute(ctx, proposal)
    assert writes(container) == []


async def test_a_person_can_only_execute_with_a_recorded_approval(container: Container) -> None:
    ctx, proposal = await approved_ticket(container)  # allowed, but no human approval
    with pytest.raises(ExecutionNotAuthorizedError, match="human approval"):
        await COORDINATOR.execute(ctx, proposal, actor="human")
    assert writes(container) == []


async def test_nothing_runs_without_a_shop(container: Container) -> None:
    ctx, proposal = await approved_ticket(container)
    ctx.deps = ctx.deps.__class__(**{**ctx.deps.__dict__, "capabilities": None})
    with pytest.raises(ExecutionNotAuthorizedError, match="no shop"):
        await COORDINATOR.execute(ctx, proposal)
    assert proposal.state is ActionState.APPROVED


# ---- execute: the one call ----


async def test_execute_calls_the_tool_once_with_key_policy_id_and_actor(container: Container) -> None:
    ctx, proposal = await approved_ticket(container)
    result = await COORDINATOR.execute(ctx, proposal)
    assert result.status == "success" and result.reference_id and result.audit_id
    [entry] = writes(container)
    assert (entry.idempotency_key, entry.policy_request_id, entry.actor, entry.approval_id) == (
        proposal.idempotency_key, "pol-1", "customer", None,
    )  # fmt: skip
    assert entry.arguments == proposal.arguments and entry.applied
    assert proposal.state is ActionState.EXECUTED and proposal.result == result


async def test_the_call_is_in_the_trace_with_its_policy_entry(container: Container) -> None:
    ctx, proposal = await approved_ticket(container)
    await COORDINATOR.execute(ctx, proposal)
    [call] = ctx.tool_calls
    assert (call.tool, call.operation_kind, call.policy_request_id, call.actor) == (
        "create_ticket",
        "create",
        "pol-1",
        "customer",
    )
    assert [p.request_id for p in ctx.policy] == ["pol-1"] and ctx.policy[0].citations == ("faq@v1#q01",)
    trace = DecisionTrace(  # the trace invariant: a write needs its policy entry in the same trace
        trace_id="t", request_id="r", tenant_id=T, conversation_id=C, turn_index=0, decision=Decision.EXECUTE,
        identity=ctx.session.identity, policy=tuple(ctx.policy), tool_calls=tuple(ctx.tool_calls),
    )  # fmt: skip
    assert trace.tool_calls[0].tool == "create_ticket"


async def test_a_second_execute_returns_the_stored_result_without_calling_the_tool(container: Container) -> None:
    ctx, proposal = await approved_ticket(container)
    first = await COORDINATOR.execute(ctx, proposal)
    again = await COORDINATOR.execute(ctx, proposal)
    assert again == first and len(writes(container)) == 1


async def test_a_confirmed_proposal_also_returns_its_result(container: Container) -> None:
    ctx, proposal = await approved_ticket(container)
    first = await COORDINATOR.execute(ctx, proposal)
    proposal.transition(ActionState.CONFIRMED, at=NOW)
    assert await COORDINATOR.execute(ctx, proposal) == first and len(writes(container)) == 1


async def test_a_double_yes_in_parallel_writes_once(container: Container) -> None:
    ctx, proposal = await approved_ticket(container)
    results = await asyncio.gather(*(COORDINATOR.execute(ctx, proposal) for _ in range(5)))
    assert len(writes(container)) == 1 and container.shop is not None and container.shop.change_count(T) == 1
    assert all(r == results[0] for r in results) and len(container.shop.records(T, "ticket")) == 1


async def test_two_copies_of_the_same_proposal_still_write_once(container: Container) -> None:
    """Another process loaded the session before the first yes: the shop recognises the key and does not act twice."""
    ctx, proposal = await approved_ticket(container)
    copy_ctx, copy_proposal = context(container), proposal.model_copy(deep=True)
    results = await asyncio.gather(
        COORDINATOR.execute(ctx, proposal), ActionCoordinator().execute(copy_ctx, copy_proposal)
    )
    assert container.shop is not None and container.shop.change_count(T) == 1
    assert results[0].reference_id == results[1].reference_id and len(container.shop.records(T, "ticket")) == 1
    assert sum(1 for e in writes(container) if e.replayed) == 1


async def test_a_human_execution_carries_the_approval_id(container: Container) -> None:
    ctx, proposal = await approved_ticket(container, "require_human")
    proposal.human_approval = APPROVAL
    result = await COORDINATOR.execute(ctx, proposal, actor="human")
    [entry] = writes(container)
    assert result.status == "success" and (entry.actor, entry.approval_id) == ("human", approval_id_of(APPROVAL))
    assert ctx.tool_calls[0].approval_id == "case_9"


# ---- execute: failures are final, never retried ----


async def test_an_unclear_outcome_is_kept_and_never_retried(container: Container) -> None:
    inject(container, "shop", {"switch": "uncertain", "tool": "create_ticket", "applied": True})
    ctx, proposal = await approved_ticket(container)
    result = await COORDINATOR.execute(ctx, proposal)
    assert result.status == "error" and result.write_may_have_applied
    again = await COORDINATOR.execute(ctx, proposal)
    assert again == result and len(writes(container)) == 1 and container.shop is not None
    assert container.shop.change_count(T) == 1  # it really happened once, and was not repeated


async def test_a_clear_refusal_by_the_shop_is_kept_and_never_retried(container: Container) -> None:
    ctx = context(container)
    ctx.session.slots.update({"order_id": "NS-00000", "description": "x"})  # an order that does not exist
    proposal = await COORDINATOR.propose(ctx, ctx.tenant.intents["complaint"])
    proposal.policy_decisions.append(decision())
    proposal.transition(ActionState.AWAITING_CONFIRMATION, at=NOW)
    proposal.transition(ActionState.APPROVED, at=NOW)
    result = await COORDINATOR.execute(ctx, proposal)
    assert result.status == "error" and result.error_code == "NOT_FOUND" and not result.write_may_have_applied
    await COORDINATOR.execute(ctx, proposal)
    assert len(writes(container)) == 1


@pytest.mark.parametrize(("code", "uncertain"), [("BACKEND_UNAVAILABLE", False), ("TIMEOUT", True)])
async def test_a_shop_that_cannot_be_reached_is_one_attempt(container: Container, code: str, uncertain: bool) -> None:
    inject(container, "shop", {"switch": "fail_next", "tool": "create_ticket", "code": code, "times": 3})
    ctx, proposal = await approved_ticket(container)
    result = await COORDINATOR.execute(ctx, proposal)
    assert (result.status, result.error_code, result.write_may_have_applied) == ("error", code, uncertain)
    assert not result.retryable and len(writes(container)) == 1  # one attempt, no retry in the same turn
    await COORDINATOR.execute(ctx, proposal)
    assert len(writes(container)) == 1  # and none later either


async def test_an_unexpected_crash_in_the_call_leaves_the_proposal_claimed(container: Container) -> None:
    class Crashing:
        async def list_tools(self, tenant_id: str) -> list[Any]:
            return await container.capabilities.list_tools(tenant_id)

        async def call_tool(self, tenant_id: str, request: Any) -> Any:
            raise RuntimeError("bug")

    ctx, proposal = await approved_ticket(container)
    ctx.deps = ctx.deps.__class__(**{**ctx.deps.__dict__, "capabilities": Crashing(), "registry": None})
    with pytest.raises(RuntimeError):
        await COORDINATOR.execute(ctx, proposal)
    assert (
        proposal.state is ActionState.EXECUTED and proposal.result is None
    )  # claimed, so a second yes cannot re-run it
    again = await COORDINATOR.execute(ctx, proposal)
    assert again.error_code == "OUTCOME_UNKNOWN" and again.write_may_have_applied


async def test_the_guard_forgets_finished_keys(container: Container) -> None:
    coordinator = ActionCoordinator()
    ctx, proposal = await approved_ticket(container)
    await coordinator.execute(ctx, proposal)
    assert coordinator._guard._locks == {}  # type: ignore[attr-defined]


def test_a_lost_upstream_error_type_is_not_swallowed() -> None:
    assert issubclass(UpstreamError, Exception)  # the coordinator catches only UpstreamError, a bug elsewhere surfaces
