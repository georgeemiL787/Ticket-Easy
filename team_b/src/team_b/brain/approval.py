"""A person's decision on an action that waited for approval. SAFETY-CRITICAL: needs a second reviewer.

OWNER: Track A.

decide_case() is what Orchestrator.human_decide runs. The rules:
  - Approving never skips a check. The order is read again, the arguments must still be the ones the person was shown,
    and the rule checker is asked again WITH the approval: it turns require_human into allow, never a deny. If the rules
    now say deny, nothing runs, the proposal is closed, and the case and the customer are told why.
  - Rejecting cancels the proposal and tells the customer politely. Nothing runs.
  - An approved action runs through the same coordinator as a customer's yes: once, with the do-it-once key, the policy
    request id and the approval id, actor "human", and the result is verified before anyone says "done".
  - A second approval (or rejection) of an already decided action does nothing but leave a note on the case.
  - Whatever happens is recorded in a trace of kind human_action, and on the case.
It returns the message for the customer (to be put in their outbox) or None.
"""

import uuid
from dataclasses import dataclass, field

from team_b.brain import actions, gates
from team_b.brain.composer import default_composer
from team_b.brain.lookup import load_own_order
from team_b.brain.slots import resolve_arguments
from team_b.brain.turn import Deps, Step, TurnContext, locale_of
from team_b.contracts.policy import HumanApproval
from team_b.domain.actions import ActionProposal, ActionState
from team_b.domain.decision import Decision
from team_b.domain.handoff import CaseStatus, HandoffCase
from team_b.domain.tenant import TenantConfig
from team_b.domain.trace import DecisionTrace, ProposalRecord, TraceStep
from team_b.observability import get_logger
from team_b.ports import NotFoundError

log = get_logger(__name__)


DECIDED = frozenset(
    {
        "approved_and_done",
        "approved_but_failed",
        "approved_unverified",
        "approval_denied",
        "approval_blocked",
        "rejected",
    }
)


class NothingToDecideError(ValueError):
    """The case has no action waiting for a person's approval."""


class CaseNotYoursError(ValueError):
    """The case is closed, or another person has claimed it."""


@dataclass
class _Result:
    decision: Decision
    reason: str
    customer_step: Step | None = None  # what to tell the customer (composed in their language)
    event: str = ""  # what the case records
    note: str = ""
    steps: list[TraceStep] = field(default_factory=list)


async def decide_case(
    deps: Deps, tenant: TenantConfig, case: HandoffCase, agent: str, approve: bool, note: str | None
) -> str | None:
    """Approve or reject the action waiting on `case`. Returns the message for the customer, if there is one.

    The case is read again inside the conversation's lock, so simultaneous decisions cannot overwrite each other."""
    async with deps.sessions.lock(case.tenant_id, case.conversation_id):
        fresh = await deps.cases.get(case.tenant_id, case.case_id)
        if fresh is None:
            raise NotFoundError(f"case {case.case_id} does not exist")
        case = fresh
        now = deps.clock.now()
        decided_before = any(e.kind in DECIDED for e in case.events)
        if case.pending_approval is None and not decided_before:
            raise NothingToDecideError(f"case {case.case_id} has no action waiting for approval")
        if case.status in (CaseStatus.RESOLVED, CaseStatus.RETURNED_TO_AGENT) and not decided_before:
            raise CaseNotYoursError(f"case {case.case_id} is {case.status.value}")
        if case.status is CaseStatus.CLAIMED and case.claimed_by != agent:
            raise CaseNotYoursError(f"case {case.case_id} is claimed by {case.claimed_by}")
        if case.pending_approval is None:  # decided already: a repeat is only noted
            case.add_event(actor=agent, kind="decision_ignored", at=now, note="the action was already decided")
            await deps.cases.save(case)
            return None
        if case.status is CaseStatus.OPEN:
            case.transition(CaseStatus.CLAIMED, actor=agent, at=now)

        session = await deps.sessions.load(case.tenant_id, case.conversation_id)
        if session is None:
            raise NotFoundError(f"conversation {case.conversation_id} does not exist")
        proposal = next((a for a in session.actions if a.proposal_id == case.pending_approval.proposal_id), None)
        if proposal is None:
            raise NothingToDecideError("the waiting action is not in the conversation any more")
        ctx = TurnContext(
            deps=deps, tenant=tenant, session=session, text="", now=now,
            request_id=uuid.uuid4().hex, trace_id=uuid.uuid4().hex,
        )  # fmt: skip
        if proposal.state is not ActionState.AWAITING_HUMAN:
            note_ = f"{'approve' if approve else 'reject'} ignored: the action is {proposal.state.value}"
            case.pending_approval = None
            case.add_event(actor=agent, kind="decision_ignored", at=now, note=note_)
            await deps.cases.save(case)
            return None
        if not approve:
            result = _reject(ctx, proposal, agent, note)
        else:
            result = await _approve(ctx, proposal, agent, note, case)

        if proposal.state is not ActionState.AWAITING_HUMAN:
            case.pending_approval = None  # decided: nothing waits any more
        case.add_event(actor=agent, kind=result.event, at=now, note=result.note)
        message = _compose(ctx, result.customer_step)
        await deps.cases.save(case)
        await deps.sessions.save(session)
        await deps.traces.add(_trace(ctx, result, message, case, agent, approve))
    return message


def _compose(ctx: TurnContext, step: Step | None) -> str | None:
    if step is None:
        return None
    return default_composer().t(locale_of(ctx), step.reply_key, **step.values)


def _reject(ctx: TurnContext, proposal: ActionProposal, agent: str, note: str | None) -> _Result:
    proposal.transition(ActionState.CANCELLED, at=ctx.now, note=f"rejected by {agent}")
    ctx.proposal_ids.append(proposal.proposal_id)
    step = Step(Decision.REFUSE, reason="a person rejected the request", reply_key="approval_rejected")
    return _Result(Decision.REFUSE, f"rejected by {agent}", step, "rejected", note or "")


async def _approve(
    ctx: TurnContext, proposal: ActionProposal, agent: str, note: str | None, case: HandoffCase
) -> _Result:
    from team_b.brain.stages import _tools  # imported here: stages imports the action module

    ctx.proposal_ids.append(proposal.proposal_id)
    session = ctx.session
    tools = await _tools(ctx)
    tool = tools.get(proposal.tool) if tools is not None else None
    spec = next((s for s in ctx.tenant.intents.values() if s.action_tool == proposal.tool), None)
    if tools is None or tool is None or spec is None:
        return _stay(f"the shop does not publish {proposal.tool} right now; nothing was done, try again later")
    if not (gate := gates.check_tool(ctx.tenant, tool)).allowed:
        return _close(ctx, proposal, f"permission gate: {gate.reason}: {gate.detail}", "approval_blocked")

    if (read := await load_own_order(ctx, spec, tools)) is not None:
        if read.decision is Decision.HANDOFF:  # not their order any more, or the tool is gone
            return _close(ctx, proposal, read.reason, "approval_blocked")
        return _stay("the order could not be read again; nothing was done, try again")
    resolution = resolve_arguments(spec, tool, session, session.facts)
    if resolution.arguments != proposal.arguments:
        step = Step(Decision.CLARIFY, reason="the order changed", reply_key="action_changed")
        return _close(
            ctx,
            proposal,
            "the order changed since the request was made; the approval was not used",
            "approval_blocked",
            step,
        )

    approval = HumanApproval(approved_by=agent, case_id=case.case_id, approved_at=ctx.now)
    decision = await actions.check_policy(ctx, tool, proposal.arguments, approval=approval)
    if decision is None:
        return _stay("the rule checker did not answer; nothing was done, try again")
    proposal.policy_decisions.append(decision)
    if decision.decision != "allow":  # a deny stays a deny (and require_human with an approval cannot happen)
        locale_step = actions._deny_step(ctx, decision)
        step = Step(
            Decision.REFUSE,
            reason=locale_step.reason,
            reply_key="policy_refusal",
            values=locale_step.values,
            citations=decision.citations,
        )
        return _close(
            ctx,
            proposal,
            f"the rules say no even with your approval ({decision.reason_code}: {decision.rationale})",
            "approval_denied",
            step,
        )

    proposal.human_approval = approval
    proposal.transition(ActionState.APPROVED, at=ctx.now, note=f"approved by {agent}")
    step = await actions._execute_and_verify(ctx, proposal, tool, actor="human")
    session.pending_action_id = None
    outcome = {Decision.EXECUTE: "approved_and_done", Decision.REFUSE: "approved_but_failed"}.get(
        step.decision, "approved_unverified"
    )
    return _Result(step.decision, step.reason, step, outcome, note or step.reason)


def _stay(why: str) -> _Result:
    """Nothing could be done and nothing was decided: the action keeps waiting for a person."""
    return _Result(Decision.REFUSE, why, None, "decision_failed", why)


def _close(ctx: TurnContext, proposal: ActionProposal, why: str, event: str, step: Step | None = None) -> _Result:
    """The action cannot go ahead: close the proposal, record why, and tell the customer."""
    if not proposal.is_terminal:
        proposal.transition(ActionState.BLOCKED, at=ctx.now, note=why)
    step = step or Step(
        Decision.REFUSE, reason=why, reply_key="action_failed"
    )  # the customer is told, never left waiting
    return _Result(Decision.REFUSE, why, step, event, why)


def _trace(
    ctx: TurnContext, result: _Result, message: str | None, case: HandoffCase, agent: str, approve: bool
) -> DecisionTrace:
    session = ctx.session
    step = result.customer_step
    decision = step.decision if step is not None else Decision.REFUSE
    return DecisionTrace(
        trace_id=ctx.trace_id,
        request_id=ctx.request_id,
        tenant_id=ctx.tenant.tenant_id,
        conversation_id=session.conversation_id,
        turn_index=session.turn_index,
        kind="human_action",
        customer_message=f"[{agent}] {'approve' if approve else 'reject'}",
        identity=session.identity.model_copy(),
        risk_categories=tuple(session.risk_categories),
        proposals=tuple(
            ProposalRecord(proposal_id=a.proposal_id, tool=a.tool, state=a.state.value)
            for a in session.actions
            if a.proposal_id in ctx.proposal_ids
        ),
        policy=tuple(ctx.policy),
        tool_calls=tuple(ctx.tool_calls),
        decision=decision,
        decision_reason=f"{result.event}: {result.reason}",
        response_text=message or "",
        response_citations=step.citations if step is not None else (),
        escalation_reason=step.escalation if step is not None and decision is Decision.HANDOFF else None,
        handoff_case_id=case.case_id if decision is Decision.HANDOFF else None,
        errors=tuple(ctx.errors),
        steps=(TraceStep(stage="human_decide", status="ok", detail=result.event),),
    )
