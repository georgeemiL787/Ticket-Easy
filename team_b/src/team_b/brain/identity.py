"""Proving who the customer is before any order data or action.

OWNER: Track A (fills this module).

ensure_verified(ctx) returns None when the customer is verified (or becomes verified this turn) so the flow may go on,
or a Step that stops the flow: asking for the missing detail, or a handoff (identity_failed) after too many wrong tries.
Until Track A fills it, it returns None and the slot questions in brain/stages.py still ask for the order number and
phone.
"""

from team_b.brain.turn import Step, TurnContext


async def ensure_verified(ctx: TurnContext) -> Step | None:
    """None: verified, carry on. A Step: ask again or hand off; the turn ends with it."""
    return None
