"""Opening a handoff case: the stub the turn pipeline uses until the real handoff module exists.

It records what the pipeline knows when it escalates (the reason, a priority, the transcript so far, the details the
customer gave) as a valid HandoffCase, so a human has something to pick up and the case is visible in the case store.
The full briefing (summaries, policy quotes, suggested wording) and the human actions come with the handoff step.
"""

import uuid

from team_b.brain.redaction import redact
from team_b.brain.turn import TurnContext
from team_b.domain.actions import ActionProposal
from team_b.domain.decision import EscalationReason
from team_b.domain.handoff import CustomerSnapshot, HandoffCase, HandoffPackage, PendingApproval, Priority
from team_b.domain.session import SessionState
from team_b.domain.trace import PolicyRecord

R = EscalationReason
# reason -> (priority, what a human should do first)
ESCALATION_DEFAULTS: dict[EscalationReason, tuple[Priority, str]] = {
    R.CUSTOMER_REQUEST: ("normal", "Greet the customer and ask how you can help."),
    R.MANDATORY_RISK: ("urgent", "Read the message first and take over personally; do not let the assistant continue."),
    R.POLICY_DENIED: ("normal", "Review the denied request and explain the policy, or decide on an exception."),
    R.APPROVAL_REQUIRED: ("high", "Review the request and approve or reject it."),
    R.REPEATED_TOOL_FAILURE: ("high", "Check the shop system, then look the information up by hand."),
    R.UNVERIFIED_RESULT: ("high", "Check in the shop system whether the action really happened before replying."),
    R.NO_EVIDENCE: ("normal", "Answer the question from your own knowledge of the shop's policies."),
    R.DEPENDENCY_UNAVAILABLE: ("high", "A system the assistant needs is down; handle the request by hand."),
    R.LOW_CONFIDENCE: ("normal", "The assistant could not understand the request; ask the customer to explain."),
    R.IDENTITY_FAILED: ("high", "The customer could not be verified; verify them another way before sharing anything."),
    R.OWNERSHIP_MISMATCH: (
        "high",
        "The customer asked about an order that is not theirs; check before sharing anything.",
    ),
    R.HIGH_FRUSTRATION: ("high", "The customer is upset; apologise and take over."),
    R.CAPABILITY_MISSING: ("normal", "The assistant cannot do this yet; do it in the shop system."),
    R.UNSUPPORTED: ("normal", "The request is outside what the assistant may do; handle it personally."),
}


def build_package(
    session: SessionState, reason: EscalationReason, summary: str, rule_answers: tuple[PolicyRecord, ...] = ()
) -> HandoffPackage:
    priority, next_step = ESCALATION_DEFAULTS[reason]
    identity = session.identity
    orders = (session.slots["order_id"],) if session.slots.get("order_id") else ()
    return HandoffPackage(
        summary=summary,
        reason=reason,
        priority=priority,
        suggested_next_step=next_step,
        customer=CustomerSnapshot(verified=identity.verified, customer_id=identity.customer_id, orders=orders),
        details={key: redact(value) for key, value in session.slots.items()},
        order_facts=dict(session.facts),
        safety_flags=tuple(session.risk_categories),
        rule_answers=rule_answers,
        transcript=tuple(session.history),
    )


async def open_case(
    ctx: TurnContext,
    reason: EscalationReason,
    detail: str,
    pending_approval: ActionProposal | None = None,
) -> str:
    """Store a new open case for this conversation, mark the session handed off, and return the case id.

    `pending_approval` is the action a human must approve (reason approval_required); it is recorded on the case."""
    session = ctx.session
    waiting = (
        PendingApproval(
            proposal_id=pending_approval.proposal_id,
            tool=pending_approval.tool,
            capability=pending_approval.capability,
            arguments=dict(pending_approval.arguments),
            reason=detail,
        )
        if pending_approval is not None
        else None
    )
    case = HandoffCase(
        case_id=f"case-{uuid.uuid4().hex[:12]}",
        tenant_id=session.tenant_id,
        conversation_id=session.conversation_id,
        package=build_package(session, reason, f"Handed off ({reason.value}): {detail}."),
        pending_approval=waiting,
        created_at=ctx.now,
        updated_at=ctx.now,
    )
    await ctx.deps.cases.add(case)
    session.status = "handed_off"
    session.handoff_case_id = case.case_id
    session.last_escalation = reason
    session.handoff_notice_sent = False
    return case.case_id
