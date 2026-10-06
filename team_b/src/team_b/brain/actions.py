"""Actions that change something in the shop: return, refund, cancel, address change.

OWNER: Track A (fills this module).

handle() runs an action request through the gates; on_confirmation() continues after the customer says yes (re-check,
execute once, verify). Built so far: the action's tool must be published by the shop and pass the permission gate
(brain/gates.py), the customer must be verified, and the details are collected. The rest of the gate order (facts,
ownership, rules, confirmation) and execution come in later steps; until then on_confirmation() never executes anything.
"""

from team_b.brain import gates, identity
from team_b.brain.turn import PlannedIntent, Step, TurnContext
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.tenant import IntentSpec
from team_b.domain.understanding import NLUResult


def _handoff(reason: EscalationReason, why: str) -> Step:
    return Step(Decision.HANDOFF, reason=why, reply_key=f"handoff_{reason.value}", escalation=reason, awaiting="human")


async def handle(ctx: TurnContext, intent_spec: IntentSpec) -> Step:
    """The next step of an action request: a question, a confirmation, a refusal or a handoff."""
    from team_b.brain.stages import _tools, ask_for_details, placeholder_handler  # imported here: stages imports us

    planned = ctx.plan or PlannedIntent(name="action", kind="action")
    tools = await _tools(ctx)
    tool = tools.get(intent_spec.action_tool or "") if tools is not None else None
    if tool is not None and not (gate := gates.check_tool(ctx.tenant, tool)).allowed:
        return _handoff(EscalationReason.UNSUPPORTED, f"permission gate: {gate.reason}: {gate.detail}")

    if tool is None or tool.requires_identity:
        if (step := await identity.ensure_verified(ctx)) is not None:
            return step
    # Checked after identity: an unverified customer learns nothing about what the shop offers.
    if tools is None:
        return _handoff(EscalationReason.DEPENDENCY_UNAVAILABLE, "the shop's tool list is not available")
    if tool is None:
        return _handoff(EscalationReason.CAPABILITY_MISSING, f"the shop does not publish {intent_spec.action_tool}")
    return await ask_for_details(ctx, planned) or await placeholder_handler(ctx, planned)


async def on_confirmation(ctx: TurnContext, nlu: NLUResult) -> Step:
    """The customer answered yes to a pending action. Never executes until the checked path is built."""
    return Step(
        Decision.CLARIFY,
        reason="the customer confirmed; executing actions is not built yet",
        reply_key="ask_rephrase",
        awaiting="detail",
    )
