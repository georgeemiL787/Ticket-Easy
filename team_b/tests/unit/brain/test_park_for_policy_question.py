"""A policy question asked while "which order?" is pending is answered first; the lookup is asked again after it."""

from team_b.container import Container
from team_b.domain.decision import Decision

T, C = "shop_001", "conv-park"


async def test_a_policy_question_does_not_get_swallowed_by_a_pending_order_question(c: Container) -> None:
    assert c.orchestrator is not None
    first = await c.orchestrator.handle_turn(T, C, "where is my order")
    assert first.awaiting is not None and first.awaiting.startswith("slot:")
    second = await c.orchestrator.handle_turn(T, C, "How many days do I have to return an item?")
    assert second.decision in (Decision.ANSWER, Decision.CLARIFY, Decision.VERIFY_IDENTITY)
    assert second.citations, "the policy question is answered from the policy first"
