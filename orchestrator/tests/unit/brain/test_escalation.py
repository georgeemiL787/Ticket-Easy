"""Try-first escalation (safety-critical): the orchestrator hands over only after trying, code decides the forced cases.

The moves are scripted (no AI model). The session is seeded with the counts the loop would have kept from earlier
messages, then a hand-over is proposed; the check "handover_after_trying" must refuse it too early and let it through
when the attempts were made. The reasons that only code can state are always refused.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from team_b.brain.escalation import FORCED_ONLY, TRIED_FIRST
from team_b.brain.orchestrator import Orchestrator
from team_b.container import Container
from team_b.domain.agent import HandOver, Reply, ReplyPlan
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.session import SessionState
from team_b.domain.tenant import EscalationConfig
from team_b.domain.trace import DecisionTrace
from tests.fakes import FakeEvidence, ScriptedMovePicker

T, C = "shop_001", "conv-esc"
NOW = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)
GREETING = Reply(why="a greeting", plan=ReplyPlan(decision=Decision.ANSWER, reply_key="greeting"))
ANGRY = "This is ridiculous!!! Worst service ever, I am so angry, stop asking me questions!!!"
R = EscalationReason


def orch(c: Container, picker: Any) -> Orchestrator:
    return Orchestrator(
        clock=c.clock, sessions=c.sessions, traces=c.traces, cases=c.cases, move_picker=picker,
        evidence=FakeEvidence(), tenants=c.tenants, capabilities=c.capabilities,
    )  # fmt: skip


async def seed(c: Container, **fields: Any) -> None:
    await c.sessions.save(SessionState(tenant_id=T, conversation_id=C, created_at=NOW, updated_at=NOW, **fields))


async def hand_over(
    c: Container, reason: EscalationReason, message: str = "hmm", **session: Any
) -> tuple[Any, Any, DecisionTrace]:
    """Seed the session, propose a hand-over (then a greeting if it was refused). Returns (reply, picker, trace)."""
    await seed(c, **session)
    picker = ScriptedMovePicker(HandOver(reason=reason, tried=["did my best"], why="stuck"), GREETING)
    reply = await orch(c, picker).handle_turn(T, C, message)
    trace = (await c.traces.for_conversation(T, C))[-1]
    return reply, picker, trace


def too_early(trace: DecisionTrace) -> bool:
    first = trace.agent_steps[0]
    return first.refused and any(ch.name == "handover_after_trying" and not ch.passed for ch in first.checks)


def allowed(trace: DecisionTrace, reason: EscalationReason) -> bool:
    first = trace.agent_steps[0]
    ok = any(ch.name == "handover_after_trying" and ch.passed for ch in first.checks) and not first.refused
    return ok and trace.decision is Decision.HANDOFF and trace.escalation_reason is reason


# ---- the table of what may be chosen, and when ----


def test_the_forced_and_the_chosen_reasons_do_not_overlap() -> None:
    assert not FORCED_ONLY & TRIED_FIRST
    assert {R.MANDATORY_RISK, R.OWNERSHIP_MISMATCH, R.IDENTITY_FAILED, R.APPROVAL_REQUIRED} <= FORCED_ONLY
    assert {R.POLICY_DENIED, R.UNVERIFIED_RESULT} <= FORCED_ONLY


def test_the_default_limits_are_the_ones_in_the_spec() -> None:
    cfg = EscalationConfig()
    assert (cfg.frustration_attempts, cfg.policy_questions, cfg.outage_retries, cfg.max_clarifications) == (1, 1, 1, 2)
    with pytest.raises(ValueError):
        EscalationConfig(outage_retries=0)


@pytest.mark.parametrize("reason", sorted(FORCED_ONLY, key=lambda r: r.value))
async def test_a_reason_only_code_can_state_is_refused_even_with_every_count_high(c: Container, reason: R) -> None:
    counts = {"frustration_strikes": 5, "no_evidence_count": 5, "agent_questions": 5, "tool_failures": 5}
    reply, picker, trace = await hand_over(c, reason, ANGRY, **counts)
    assert too_early(trace) and "decided by the system" in picker.views[1].refused
    assert reply.decision is Decision.ANSWER and trace.escalation_kind is None


@pytest.mark.parametrize("reason", [R.UNSUPPORTED, R.CAPABILITY_MISSING, R.CUSTOMER_REQUEST])
async def test_a_hand_over_a_person_must_judge_anyway_is_allowed_at_once(c: Container, reason: R) -> None:
    reply, picker, trace = await hand_over(c, reason)
    assert allowed(trace, reason) and trace.escalation_kind == "chosen" and len(picker.views) == 1


async def test_frustration_the_first_angry_message_is_apologised_to_not_handed_over(c: Container) -> None:
    reply, picker, trace = await hand_over(c, R.HIGH_FRUSTRATION, ANGRY)
    assert too_early(trace) and "apologise and solve it first" in picker.views[1].refused
    assert "sorry" in reply.text.lower() and reply.decision is not Decision.HANDOFF  # the apology is told
    session = await c.sessions.load(T, C)
    assert session is not None and session.frustration_strikes == 1


async def test_frustration_still_high_after_an_attempt_may_be_handed_over(c: Container) -> None:
    _, _, trace = await hand_over(c, R.HIGH_FRUSTRATION, ANGRY, frustration_strikes=1)
    assert allowed(trace, R.HIGH_FRUSTRATION) and trace.escalation_kind == "chosen"


async def test_frustration_that_has_calmed_down_cannot_be_handed_over(c: Container) -> None:
    _, _, trace = await hand_over(c, R.HIGH_FRUSTRATION, "ok thanks, where is my order?", frustration_strikes=1)
    assert too_early(trace)
    session = await c.sessions.load(T, C)
    assert session is not None and session.frustration_strikes == 0  # a calm message starts the count again


@pytest.mark.parametrize(
    ("counts", "may"),
    [
        ({"no_evidence_count": 0, "agent_questions": 0}, False),
        ({"no_evidence_count": 1, "agent_questions": 0}, False),  # one miss: ask first
        ({"no_evidence_count": 2, "agent_questions": 0}, False),  # two misses but no clarifying question was asked
        ({"no_evidence_count": 1, "agent_questions": 1}, False),  # asked, but only one search came up empty
        ({"no_evidence_count": 2, "agent_questions": 1}, True),
        ({"no_evidence_count": 3, "agent_questions": 2}, True),
    ],
)
async def test_no_evidence_needs_two_empty_searches_and_a_clarifying_question(
    c: Container, counts: dict[str, int], may: bool
) -> None:
    _, _, trace = await hand_over(c, R.NO_EVIDENCE, "do you ship to the moon?", **counts)
    assert allowed(trace, R.NO_EVIDENCE) if may else too_early(trace), counts


@pytest.mark.parametrize("reason", [R.DEPENDENCY_UNAVAILABLE, R.REPEATED_TOOL_FAILURE])
@pytest.mark.parametrize(("failures", "may"), [(0, False), (1, False), (2, True)])
async def test_an_outage_is_retried_once_before_a_hand_over(c: Container, reason: R, failures: int, may: bool) -> None:
    _, _, trace = await hand_over(c, reason, "where is my order?", tool_failures=failures)
    assert allowed(trace, reason) if may else too_early(trace), failures


@pytest.mark.parametrize(("questions", "may"), [(0, False), (1, False), (2, True), (3, True)])
async def test_an_unclear_request_is_handed_over_only_after_two_questions(
    c: Container, questions: int, may: bool
) -> None:
    _, _, trace = await hand_over(c, R.LOW_CONFIDENCE, "hmm", agent_questions=questions)
    assert allowed(trace, R.LOW_CONFIDENCE) if may else too_early(trace), questions


async def test_a_refused_hand_over_twice_in_a_row_falls_back_to_the_usual_stages(c: Container) -> None:
    await seed(c)
    early = HandOver(reason=R.NO_EVIDENCE, tried=["x"])
    picker = ScriptedMovePicker(early, early)
    reply = await orch(c, picker).handle_turn(T, C, "do you ship to the moon?")
    trace = (await c.traces.for_conversation(T, C))[-1]
    assert trace.fallback_reason and "two refused" in trace.fallback_reason and reply.text  # nobody is left waiting


# ---- the questions are counted, the counts reset ----


async def test_each_question_the_agent_asks_is_counted_and_an_answer_starts_the_count_again(c: Container) -> None:
    from team_b.domain.agent import AskCustomer

    ask = AskCustomer(question_key="ask_rephrase", why="unclear")
    picker = ScriptedMovePicker(ask, ask, GREETING)
    bot = orch(c, picker)
    await bot.handle_turn(T, C, "hmm")
    await bot.handle_turn(T, C, "hmm hmm")
    session = await c.sessions.load(T, C)
    assert session is not None and session.agent_questions == 2
    await bot.handle_turn(T, C, "hello")  # an answered message
    session = await c.sessions.load(T, C)
    assert session is not None and session.agent_questions == 0


# ---- what was tried reaches the briefing ----


async def test_the_briefing_lists_what_was_tried_in_plain_words(c: Container) -> None:
    _, _, trace = await hand_over(
        c, R.NO_EVIDENCE, "do you ship to the moon? my phone is 01012345601", no_evidence_count=2, agent_questions=1
    )
    case = await c.cases.get(T, trace.handoff_case_id or "")
    assert case is not None
    tried = case.package.tried
    assert "did my best" in tried  # what the orchestrator said
    assert any("found nothing 2 time(s)" in t for t in tried) and any(
        "asked the customer 1 question" in t for t in tried
    )
    assert "01012345601" not in " ".join(tried)


async def test_a_forced_handoff_has_a_tried_list_too(c: Container) -> None:
    await seed(c, no_evidence_count=1, tool_failures=1)
    reply = await orch(c, ScriptedMovePicker()).handle_turn(T, C, "I want to talk to a human")
    trace = (await c.traces.for_conversation(T, C))[-1]
    case = await c.cases.get(T, trace.handoff_case_id or "")
    assert reply.decision is Decision.HANDOFF and trace.escalation_kind == "forced" and case is not None
    assert any("read(s) from the shop failed" in t for t in case.package.tried)


async def test_the_inbox_api_returns_the_tried_list_with_the_case(c: Container) -> None:
    _, _, trace = await hand_over(c, R.UNSUPPORTED, "something odd")
    case = await c.cases.get(T, trace.handoff_case_id or "")
    assert case is not None
    again = type(case.package).model_validate_json(case.package.model_dump_json())
    assert again.tried == case.package.tried and "did my best" in again.tried
