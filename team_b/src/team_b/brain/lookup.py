"""Lookup requests: "where is my order?".

OWNER: Track A (fills this module).

The orchestrator calls answer() for every intent of kind "lookup". Track A adds identity, ownership, the real read from
the shop and honest failure handling. Until then it asks for the missing details (slots) like before.
"""

from team_b.brain.turn import PlannedIntent, Step, TurnContext
from team_b.domain.tenant import IntentSpec


async def answer(ctx: TurnContext, intent_spec: IntentSpec) -> Step:
    """The reply to a lookup: facts from the shop (never invented), or the next question, or a handoff."""
    from team_b.brain.stages import slot_handler  # imported here: stages imports the action module

    planned = ctx.plan or PlannedIntent(name="lookup", kind="lookup")
    return await slot_handler(ctx, planned)
