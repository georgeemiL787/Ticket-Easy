"""A failed read: a one-off failure is told honestly; a setup problem goes to a person at once."""

from types import SimpleNamespace

import pytest

from team_b.brain import shopcalls
from team_b.domain.decision import Decision


def context(errors):
    return SimpleNamespace(
        session=SimpleNamespace(tool_failures=0),
        tenant=SimpleNamespace(escalation=SimpleNamespace(max_tool_failures=2)),
        errors=list(errors),
    )


def test_a_one_off_failure_is_told_to_the_customer_who_may_try_again():
    step = shopcalls.failed_read(context(["get_order failed: BACKEND_UNAVAILABLE"]), "reading the order")
    assert step.decision is Decision.CLARIFY and step.reply_key == "lookup_failed"


@pytest.mark.parametrize("code", shopcalls.PERMANENT_FAILURES)
def test_a_setup_problem_is_not_retried_by_the_customer(code):
    step = shopcalls.failed_read(context([f"get_order failed: {code}"]), "reading the order")
    assert step.decision is Decision.HANDOFF and step.awaiting == "human"
