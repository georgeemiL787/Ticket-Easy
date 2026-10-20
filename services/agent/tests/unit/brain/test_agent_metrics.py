"""The agent's metrics (15 to 19 in docs/metrics.md) over seeded facts, and the rows they are cut from."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_service.adapters.memory_store import InMemoryCaseStore, InMemoryTraceStore
from agent_service.adapters.sqlite_store import SqliteDatabase, SqliteTraceStore
from agent_service.api.app import create_app
from agent_service.brain.metrics import MetricsService, agent_summary_of
from agent_service.container import build_container
from agent_service.domain.agent import AgentStepRecord
from agent_service.domain.decision import Decision, EscalationReason
from agent_service.domain.facts import TurnFact, agent_worked, facts_from_trace, fallback_category
from agent_service.domain.trace import DecisionTrace, PolicyRecord, TraceStep
from tests.support import make_settings

T = "shop_001"
START = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)
MOVE = (AgentStepRecord(move={"kind": "reply"}),)


class Clock:
    def __init__(self) -> None:
        self.at = START

    def now(self) -> datetime:
        return self.at

    def today(self) -> Any:
        return self.at.date()


def turn_fact(**over: Any) -> TurnFact:
    base: dict[str, Any] = {
        "trace_id": "t", "tenant_id": T, "conversation_id": "c", "created_at": START, "decision": "answer",
        "latency_ms": 10.0, "agent_mode": "on", "agent_moves": 2,
    }  # fmt: skip
    return TurnFact(**{**base, **over})


def trace(n: int, **over: Any) -> DecisionTrace:
    base: dict[str, Any] = {
        "trace_id": f"t{n}", "request_id": f"r{n}", "tenant_id": T, "conversation_id": f"c{n}", "turn_index": 0,
        "decision": Decision.ANSWER, "agent_mode": "on", "agent_steps": MOVE,
    }  # fmt: skip
    if over.get("escalation_reason") is not None:
        over = {"decision": Decision.HANDOFF, **over}
    return DecisionTrace(**{**base, **over})


# ---- the pure summary ----


def test_the_fallback_rate_counts_only_turns_the_agent_worked() -> None:
    turns = [turn_fact() for _ in range(6)]
    turns += [turn_fact(agent_fallback=True, agent_moves=0, agent_fallback_reason="rate_limit")]
    turns += [turn_fact(agent_fallback=True, agent_moves=1, agent_fallback_reason="model_error")] * 2
    turns += [turn_fact(agent_mode="off", agent_moves=0) for _ in range(20)]  # the usual flow: not counted
    s = agent_summary_of(turns)
    assert (s.agent_turns, s.fallbacks) == (9, 3) and s.fallback_rate == pytest.approx(3 / 9)
    assert s.fallback_reasons == {"rate_limit": 1, "model_error": 2}


def test_moves_per_message_is_the_mean_over_agent_turns() -> None:
    s = agent_summary_of(
        [turn_fact(agent_moves=1), turn_fact(agent_moves=3), turn_fact(agent_mode="off", agent_moves=0)]
    )
    assert s.moves_per_message == 2.0
    assert agent_summary_of([]).moves_per_message is None and agent_summary_of([]).fallback_rate is None


def test_reviewer_blocks_are_summed_for_replies_and_actions() -> None:
    s = agent_summary_of([turn_fact(reviewer_blocked_replies=2), turn_fact(reviewer_blocked_actions=1), turn_fact()])
    assert (s.reviewer_blocked_replies, s.reviewer_blocked_actions) == (2, 1)


def test_handoffs_are_split_into_forced_and_chosen_by_reason() -> None:
    def handoff(reason: str, kind: str | None) -> TurnFact:
        return turn_fact(decision="handoff", escalation_reason=reason, escalation_kind=kind)

    s = agent_summary_of(
        [handoff("mandatory_risk", "forced"), handoff("customer_request", "forced"), handoff("no_evidence", "chosen"),
         handoff("no_evidence", "chosen"), handoff("high_frustration", "chosen"), handoff("policy_denied", None)]
    )  # fmt: skip
    assert (s.handoffs_forced, s.handoffs_chosen) == (3, 3)  # a handoff with no kind (the usual flow) is forced by code
    assert s.by_reason["no_evidence"] == {"forced": 0, "chosen": 2} and s.by_reason["mandatory_risk"]["forced"] == 1


def test_automate_next_ranks_request_types_by_handoffs() -> None:
    turns = [turn_fact(intent="refund_request", decision="handoff", escalation_reason="approval_required")] * 3
    turns += [turn_fact(intent="refund_request")] * 3
    turns += [turn_fact(intent="cancel_order", decision="handoff", escalation_reason="approval_required")]
    turns += [turn_fact(intent="order_status") for _ in range(10)]
    ranked = agent_summary_of(turns).automate_next
    assert [(r.intent, r.handoffs, r.turns) for r in ranked] == [("refund_request", 3, 6), ("cancel_order", 1, 1)]
    assert ranked[0].rate == 0.5


# ---- the rows they are cut from ----


@pytest.mark.parametrize(
    ("reason", "versions", "category"),
    [
        (None, {}, None),
        ("move picker error: UpstreamError", {}, "model_error"),
        ("move picker error: RateBudgetExceeded", {}, "rate_limit"),
        ("move picker error: UpstreamError", {"ai_fallback": "orchestrator: RATE_BUDGET"}, "rate_limit"),
        ("two refused moves in a row", {}, "refused_moves"),
        ("more than 5 moves without an answer", {}, "too_many_moves"),
        ("the turn took longer than 30 seconds", {}, "too_slow"),
        ("something new", {}, "other"),
    ],
)
def test_the_fallback_reason_becomes_a_category(
    reason: str | None, versions: dict[str, str], category: str | None
) -> None:
    assert fallback_category(trace(1, fallback_reason=reason, versions=versions)) == category


def test_a_trace_becomes_the_agent_columns_of_its_turn_row() -> None:
    blocked = (
        TraceStep(stage="review", status="blocked", duration_ms=1.0),
        TraceStep(stage="review", status="ok", duration_ms=1.0),
    )
    policy = (PolicyRecord(request_id="p", action="create_refund", decision="require_human",
                           reason_code="POLICY_AGENT_REQUIRE_HUMAN", citations=()),)  # fmt: skip
    row, _, _ = facts_from_trace(
        trace(1, steps=blocked, policy=policy, fallback_reason="two refused moves in a row",
              escalation_reason=EscalationReason.NO_EVIDENCE, escalation_kind="chosen"),
        START,
    )  # fmt: skip
    assert row is not None and agent_worked(row)
    assert (row.agent_fallback_reason, row.reviewer_blocked_replies, row.reviewer_blocked_actions) == (
        "refused_moves",
        1,
        1,
    )
    assert (row.escalation_kind, row.agent_mode, row.agent_moves, row.agent_fallback) == ("chosen", "on", 1, True)


async def test_the_new_columns_survive_the_sqlite_round_trip(tmp_path: Path) -> None:
    clock = Clock()
    store = SqliteTraceStore(SqliteDatabase(tmp_path / "m.sqlite3"), clock)  # type: ignore[arg-type]
    await store.add(trace(1, fallback_reason="move picker error: x", escalation_reason=EscalationReason.NO_EVIDENCE,
                          escalation_kind="chosen"))  # fmt: skip
    facts = await store.facts(T, START - timedelta(hours=1), START + timedelta(hours=1))
    [row] = facts.turns
    assert (row.agent_fallback_reason, row.escalation_kind, row.agent_fallback) == ("model_error", "chosen", True)


# ---- through the service and the API ----


async def test_the_service_and_the_dashboard_endpoints_agree(tmp_path: Path) -> None:
    clock = Clock()
    traces = InMemoryTraceStore(clock)  # type: ignore[arg-type]
    for n in range(4):
        await traces.add(trace(n))
    await traces.add(trace(10, fallback_reason="move picker error: x"))
    await traces.add(
        trace(
            11,
            escalation_reason=EscalationReason.NO_EVIDENCE,
            escalation_kind="chosen",
            active_intent="policy_question",
        )
    )
    await traces.add(trace(12, escalation_reason=EscalationReason.MANDATORY_RISK, escalation_kind="forced"))
    start, end = START - timedelta(days=1), START + timedelta(days=1)
    summary = await MetricsService(traces, InMemoryCaseStore()).agent_summary(T, start, end)
    assert (
        summary.agent_turns == 7
        and summary.fallbacks == 1
        and (summary.handoffs_forced, summary.handoffs_chosen) == (1, 1)
    )

    container = build_container(make_settings(tmp_path), clock=clock)  # type: ignore[arg-type]
    for n in range(4):
        await container.traces.add(trace(n))
    await container.traces.add(
        trace(
            11,
            escalation_reason=EscalationReason.NO_EVIDENCE,
            escalation_kind="chosen",
            active_intent="policy_question",
        )
    )
    await container.traces.add(trace(12, escalation_reason=EscalationReason.MANDATORY_RISK, escalation_kind="forced"))
    app = create_app(container=container)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test")
    params = {"tenant_id": T, "from": start.isoformat(), "to": end.isoformat()}
    async with app.router.lifespan_context(app), http:
        agent = (await http.get("/v1/dashboard/agent", params=params)).json()
        esc = (await http.get("/v1/dashboard/escalations", params=params)).json()
    assert agent["now"]["agent_turns"] == 6 and agent["now"]["moves_per_message"] == 1.0
    assert agent["previous"]["agent_turns"] == 0 and agent["previous"]["fallback_rate"] is None
    assert (esc["forced"], esc["chosen"]) == (1, 1)
    assert esc["by_reason_kind"]["no_evidence"] == {"forced": 0, "chosen": 1}
    assert [(r["intent"], r["handoffs"]) for r in esc["automate_next"]] == [("policy_question", 1), ("unknown", 1)]
