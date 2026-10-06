"""Proving who the customer is before any order data or action.

OWNER: Track A.

ensure_verified(ctx) returns None when the customer is verified (or becomes verified this turn) so the flow may go on,
or a Step that stops the flow: asking for the missing detail, saying the details did not match, or a handoff
(identity_failed) after too many wrong tries. The check is the tenant's verify tool (order number + phone) and nothing
the customer says counts as verified: only a success answer from the shop that names a customer does.
Wrong tries are counted per conversation (identity.attempts); a failed try drops the phone so the next one is asked for.
"""

from team_b.brain import shopcalls
from team_b.brain.turn import PlannedIntent, Step, TurnContext
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.session import SessionIdentity

METHOD = "order_and_phone"


async def ensure_verified(ctx: TurnContext) -> Step | None:
    """None: verified, carry on. A Step: ask again or hand off; the turn ends with it."""
    from team_b.brain.stages import ask_for_details  # imported here: stages imports the action module

    session = ctx.session
    if session.identity.verified:
        return None
    slots = ctx.tenant.identity.required_slots
    if any(not session.slots.get(s) for s in slots):
        planned = ctx.plan or PlannedIntent(name="identity", kind="lookup")
        asked = await ask_for_details(ctx, planned, force_identity=True)
        if asked is not None:
            return asked
        return _handoff(ctx, EscalationReason.IDENTITY_FAILED, "the identity details could not be collected")

    outcome = await shopcalls.call_read(ctx, ctx.tenant.identity.verify_tool, {s: session.slots[s] for s in slots})
    if not outcome.ok:
        return shopcalls.failed_read(ctx, "the identity check")
    session.tool_failures = 0
    data = outcome.data
    customer_id = data.get("customer_id")
    if data.get("verified") is True and isinstance(customer_id, str) and customer_id:
        session.identity = SessionIdentity(
            verified=True, customer_id=customer_id, method=METHOD, attempts=session.identity.attempts
        )
        session.slots.pop("phone", None)  # no longer needed: keep as little personal data as possible
        session.clarifications = 0
        return None
    return _wrong_details(ctx)


def _wrong_details(ctx: TurnContext) -> Step:
    """The details did not match. Count it; at the limit hand off with no data, else ask for the phone again."""
    session = ctx.session
    session.identity.attempts += 1
    session.slots.pop("phone", None)
    session.clarifications = 0
    if session.identity.attempts >= ctx.tenant.identity.max_attempts:
        return _handoff(
            ctx, EscalationReason.IDENTITY_FAILED, f"identity not verified after {session.identity.attempts} tries"
        )
    return Step(
        Decision.VERIFY_IDENTITY,
        reason=f"the details did not match (try {session.identity.attempts} of {ctx.tenant.identity.max_attempts})",
        reply_key="identity_failed",
        awaiting="slot:phone",
    )


def _handoff(ctx: TurnContext, reason: EscalationReason, why: str) -> Step:
    return Step(Decision.HANDOFF, reason=why, reply_key=f"handoff_{reason.value}", escalation=reason, awaiting="human")
