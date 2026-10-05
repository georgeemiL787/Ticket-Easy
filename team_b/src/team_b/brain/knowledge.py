"""Answering policy questions from the shop's own documents.

OWNER: Track B (fills this module).

Signatures are fixed; the orchestrator calls answer() for every intent of kind "knowledge", and other tracks call
quote_for() to add a policy quote to their own replies (for example the late-delivery policy next to an order status).
Until Track B fills it, answer() asks the customer for more detail and quote_for() finds nothing.
"""

from team_b.brain.turn import Step, TurnContext
from team_b.contracts.evidence import Passage
from team_b.domain.decision import Decision


async def answer(ctx: TurnContext) -> Step:
    """The reply to a policy question: retrieved passages quoted with citations, or an honest "no evidence"."""
    name = ctx.plan.name if ctx.plan else "knowledge"
    return Step(
        Decision.CLARIFY,
        reason=f"knowledge handling for {name} is not built yet",
        reply_key="ask_rephrase",
        awaiting="detail",
    )


async def quote_for(ctx: TurnContext, query: str) -> list[Passage]:
    """Policy passages relevant to `query`, best first, for quoting verbatim. Empty when nothing reliable is found."""
    return []
