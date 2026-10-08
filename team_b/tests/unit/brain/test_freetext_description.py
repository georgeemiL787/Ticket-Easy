"""A complaint needs a real description: announcing a problem is not one."""

import pytest

from team_b.brain.freetext import capture


@pytest.mark.parametrize(
    "text",
    ["Hi, ازيك؟ انا عندي مشكلة في order بتاعي", "I have a problem with my order", "hi, I have an issue with my order"],
)
def test_a_vague_problem_is_not_a_description(text: str) -> None:
    assert capture(text, {"description"}, None, other_intent=False, entities={}) == {}


def test_a_concrete_cause_is_a_description() -> None:
    found = capture(
        "I have a complaint about order NS-20877, the parcel arrived late",
        {"description"},
        None,
        other_intent=False,
        entities={},
    )
    assert found == {"description": "the parcel arrived late"}


def test_the_understanding_step_may_supply_the_cause() -> None:
    found = capture(
        "order NS-1 is a mess", {"description"}, None, other_intent=False, entities={"reason": "box was empty"}
    )
    assert found == {"description": "box was empty"}
