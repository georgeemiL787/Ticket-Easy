"""Failure matrix for policy answers: what the customer and the case see when the policy search has nothing or is down.

Never guess policy: an empty search asks once to rephrase and then hands off (no_evidence); a search that fails twice
hands off at once (dependency_unavailable); in both cases nothing from the policies is quoted.
"""

from pathlib import Path

import pytest

from team_b.brain.orchestrator import Orchestrator
from team_b.container import Container, build_container
from team_b.domain.decision import Decision, EscalationReason
from tests.conftest import FIXED_TODAY
from tests.fakes import FakeEvidence
from tests.support import make_settings

T, C = "shop_001", "conv-km"
QUESTION = "What is your return policy?"
UNKNOWN = "Do you offer a five year warranty on electronics?"


@pytest.fixture
def container(tmp_path: Path) -> Container:
    return build_container(make_settings(tmp_path, fixed_today=FIXED_TODAY))


def turns(c: Container) -> Orchestrator:
    assert c.orchestrator is not None
    return c.orchestrator


async def trace_of(c: Container):  # type: ignore[no-untyped-def]
    return (await c.traces.for_conversation(T, C))[-1]


async def test_an_empty_search_asks_to_rephrase_once_then_hands_off(container: Container) -> None:
    o = turns(container)
    first = await o.handle_turn(T, C, UNKNOWN)
    assert (first.decision, first.awaiting, first.citations) == (Decision.CLARIFY, "detail", ())
    assert (await trace_of(container)).evidence_empty_reason == "no_match"
    second = await o.handle_turn(T, C, "I mean the warranty for electronic devices")
    assert (second.decision, second.awaiting) == (Decision.HANDOFF, "human")
    last = await trace_of(container)
    assert last.escalation_reason is EscalationReason.NO_EVIDENCE and not last.response_citations


async def test_a_search_that_is_down_hands_off_without_quoting(container: Container) -> None:
    assert container.policy_search is not None
    container.policy_search.fail_next("search_knowledge", 2)
    reply = await turns(container).handle_turn(T, C, QUESTION)
    assert (reply.decision, reply.citations) == (Decision.HANDOFF, ())
    trace = await trace_of(container)
    assert trace.escalation_reason is EscalationReason.DEPENDENCY_UNAVAILABLE
    assert len([e for e in trace.errors if "policy search failed" in e]) == 2  # one retry of the read, then give up


async def test_one_failed_attempt_is_retried_and_the_customer_still_gets_the_policy(container: Container) -> None:
    assert container.policy_search is not None
    container.policy_search.fail_next("search_knowledge", 1)
    reply = await turns(container).handle_turn(T, C, QUESTION)
    assert reply.decision is Decision.ANSWER and "return_policy@v2#s2" in reply.citations


async def test_no_working_policy_search_fails_closed(container: Container) -> None:
    o = Orchestrator(
        clock=container.clock,
        tenants=container.tenants,
        sessions=container.sessions,
        traces=container.traces,
        cases=container.cases,
        evidence=FakeEvidence(),  # its search_knowledge is not implemented
    )
    reply = await o.handle_turn(T, C, QUESTION)
    assert reply.decision is Decision.HANDOFF
    assert (await trace_of(container)).escalation_reason is EscalationReason.DEPENDENCY_UNAVAILABLE


async def test_a_rephrase_that_finds_the_policy_is_answered(container: Container) -> None:
    o = turns(container)
    assert (await o.handle_turn(T, C, UNKNOWN)).decision is Decision.CLARIFY
    reply = await o.handle_turn(T, C, "ok, how many days do I have to return an item?")
    assert reply.decision is Decision.ANSWER and "return_policy@v2#s2" in reply.citations
