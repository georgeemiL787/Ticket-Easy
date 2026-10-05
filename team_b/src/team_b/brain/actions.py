"""Actions that change something in the shop: return, refund, cancel, address change.

OWNER: Track A (fills this module).

handle() runs an action request through the gates (details, identity, ownership, permission, risk screen, rules,
confirmation); on_confirmation() continues after the customer says yes (re-check, execute once, verify). Until Track A
fills them, handle() asks for the missing details and on_confirmation() never executes anything.
"""

from team_b.brain.turn import PlannedIntent, Step, TurnContext
from team_b.domain.decision import Decision
from team_b.domain.tenant import IntentSpec
from team_b.domain.understanding import NLUResult


async def handle(ctx: TurnContext, intent_spec: IntentSpec) -> Step:
    """The next step of an action request: a question, a confirmation, a refusal or a handoff."""
    from team_b.brain.stages import slot_handler  # imported here: stages imports this module

    planned = ctx.plan or PlannedIntent(name="action", kind="action")
    return await slot_handler(ctx, planned)


async def on_confirmation(ctx: TurnContext, nlu: NLUResult) -> Step:
    """The customer answered yes to a pending action. Never executes until Track A builds the checked path."""
    return Step(
        Decision.CLARIFY,
        reason="the customer confirmed; executing actions is not built yet",
        reply_key="ask_rephrase",
        awaiting="detail",
    )
