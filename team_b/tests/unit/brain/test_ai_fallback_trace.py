"""A rate-limited or failing AI job never fails the turn: the rules or the template answer, and the trace says why."""

from datetime import UTC, datetime
from typing import Any

from team_b.adapters.llm_limits import RateBudgetExceeded
from team_b.adapters.llm_registry import RoleLLM
from team_b.brain.llm_nlu import LLMNLU
from team_b.brain.orchestrator import Orchestrator
from team_b.brain.rewrite import LLMRewriter
from team_b.brain.smalltalk import SmallTalkWriter
from team_b.container import Container
from team_b.contracts.errors import UpstreamError
from team_b.domain.facts import AI_MODEL_SERVICE, ai_asked, ai_fell_back, facts_from_trace
from tests.fakes import FakeEvidence, FakeLLM

T, C = "shop_001", "conv-ai"
OVER = RateBudgetExceeded(30.0, "requests per minute")


def orch(c: Container, **options: Any) -> Orchestrator:
    base: dict[str, Any] = {"evidence": FakeEvidence(), "tenants": c.tenants, "capabilities": c.capabilities}
    return Orchestrator(clock=c.clock, sessions=c.sessions, traces=c.traces, cases=c.cases, **{**base, **options})


async def last_trace(c: Container):  # type: ignore[no-untyped-def]
    return (await c.traces.for_conversation(T, C))[-1]


async def test_a_rate_limited_reader_falls_back_to_the_rules_and_the_trace_says_which_job(c: Container) -> None:
    reader = RoleLLM("reader", FakeLLM(OVER), None, "")
    reply = await orch(c, nlu=LLMNLU(reader)).handle_turn(T, C, "Where is my order NS-20877?")
    trace = await last_trace(c)
    assert reply.decision.value == "verify_identity"  # the turn went on, understood by the rules
    assert trace.nlu_method == "rules_fallback"
    assert any("reader: RATE_BUDGET" in e for e in trace.errors)
    assert trace.versions["ai_fallback"] == "reader: RATE_BUDGET"


async def test_a_rate_limited_writer_sends_the_template_and_the_alert_counts_it(c: Container) -> None:
    writer = RoleLLM("writer", FakeLLM(OVER, OVER), None, "")
    reply = await orch(c, rewriter=LLMRewriter(writer), smalltalk=SmallTalkWriter(writer)).handle_turn(T, C, "Hello")
    trace = await last_trace(c)
    assert reply.text == "Hello! How can I help you today?"  # the plain greeting: nothing failed for the customer
    assert trace.versions["ai_fallback"] == "writer: RATE_BUDGET"
    turn, _, _ = facts_from_trace(trace, datetime(2026, 9, 28, tzinfo=UTC))
    assert turn is not None and AI_MODEL_SERVICE in turn.dependency_errors
    assert ai_asked(turn) and ai_fell_back(
        turn
    )  # the ai_fallback alert and metric count this turn, rules reader or not


async def test_a_backup_model_is_noted_but_is_not_a_fallback(c: Container) -> None:
    writer = RoleLLM(
        "writer",
        FakeLLM(UpstreamError("llm", "MODEL_NOT_FOUND", "gone")),
        FakeLLM({"text": "Hi there! How can I help you today?"}),
        "openai/gpt-oss-20b",
    )
    reply = await orch(c, rewriter=LLMRewriter(writer)).handle_turn(T, C, "Hello")
    trace = await last_trace(c)
    assert reply.text == "Hi there! How can I help you today?"
    assert "ai_fallback" not in trace.versions and not any("AI model" in e for e in trace.errors)
    assert trace.versions["ai_backup_model"].startswith("writer: openai/gpt-oss-20b")
    turn, _, _ = facts_from_trace(trace, datetime(2026, 9, 28, tzinfo=UTC))
    assert turn is not None and not ai_fell_back(turn)


async def test_a_turn_with_no_ai_trouble_carries_no_ai_notes(c: Container) -> None:
    await orch(c).handle_turn(T, C, "Hello")
    trace = await last_trace(c)
    assert "ai_fallback" not in trace.versions and "ai_backup_model" not in trace.versions and not trace.errors
