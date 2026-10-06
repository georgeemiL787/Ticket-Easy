"""Every metric on synthetic traces with hand-computed answers, on both stores (memory and SQLite).

The data (all times UTC, window 2026-09-28 .. 2026-09-30, one bucket per day):

Day 1, 28 Sep
  conv A  09:00 answer     en      policy_question  100 ms  1 passage        rules
  conv B  10:00 execute    ar      return_request   300 ms  create_return ok (policy allow R-RETURN-14D), AI reading
  conv C  11:00 clarify    en      refund_request   200 ms
          11:05 handoff    en      refund_request   400 ms  policy_denied, deny R-REFUND-14D
  conv D  12:00 handoff    arabizi return_request   500 ms  unverified_result, create_return TIMEOUT (policy allow)
  conv E  13:00 clarify    en      policy_question  150 ms  empty search, AI reading fell back to rules,
                                                           2 policy search errors and 1 safety screen error
Day 2, 29 Sep
  conv F  09:00 answer     en      policy_question  120 ms  2 passages, AI reading
  conv A  09:30 answer     en      policy_question   80 ms  (a second day of conversation A)
Understand stage durations on day 1: 2, 4, 6, 8, 10, 12 ms for the six turns in order.
Cases: X opened 28 Sep 10:00 (reply +300 s, resolved +3600 s), Y opened 28 Sep 12:00 (reply +1200 s, open),
Z opened 29 Sep 08:00 (reply +30 s, resolved +600 s).
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from team_b.adapters.memory_store import InMemoryCaseStore, InMemoryTraceStore
from team_b.adapters.sqlite_store import SqliteCaseStore, SqliteDatabase, SqliteTraceStore
from team_b.brain.metrics import (
    MAX_BUCKETS,
    MetricFilters,
    MetricsService,
    bucket_start,
    bucket_starts,
    next_bucket,
    percentile,
)
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.handoff import HandoffCase, HandoffPackage
from team_b.domain.trace import DecisionTrace, EvidenceRef, PolicyRecord, ToolCallRecord, TraceStep

T = "shop_001"
D1 = datetime(2026, 9, 28, tzinfo=UTC)
D2 = D1 + timedelta(days=1)
END = D1 + timedelta(days=2)


class SteppingClock:
    """A clock the test sets before each store call."""

    def __init__(self) -> None:
        self.moment = D1

    def today(self) -> Any:
        return self.moment.date()

    def now(self) -> datetime:
        return self.moment


def at(day: datetime, hhmm: str) -> datetime:
    hour, minute = hhmm.split(":")
    return day.replace(hour=int(hour), minute=int(minute))


def stage(ms: float) -> list[TraceStep]:
    return [
        TraceStep(stage="understand", status="ok", duration_ms=ms),
        TraceStep(stage="risk_screen", status="skipped"),
    ]


def call(
    tool: str, kind: str, status: str = "success", code: str | None = None, policy: str | None = None
) -> ToolCallRecord:
    return ToolCallRecord(
        request_id=f"{tool}-{status}", tool=tool, operation_kind=kind, status=status, error_code=code,  # type: ignore[arg-type]
        policy_request_id=policy, latency_ms=5.0,
    )  # fmt: skip


def allow(request_id: str) -> PolicyRecord:
    return PolicyRecord(request_id=request_id, action="create_return", decision="allow", reason_code="R-RETURN-14D")


def trace(tid: str, conv: str, decision: Decision, **over: Any) -> DecisionTrace:
    base: dict[str, Any] = {
        "trace_id": tid, "request_id": f"r-{tid}", "tenant_id": T, "conversation_id": conv, "turn_index": 0,
        "decision": decision, "language": "en", "active_intent": "policy_question", "latency_ms": 100.0,
    }  # fmt: skip
    return DecisionTrace.model_validate({**base, **over})


def day_one() -> list[tuple[datetime, DecisionTrace]]:
    ref = [EvidenceRef(citation="a@v1#s1", document_id="a", version="v1", score=1.0)]
    return [
        (at(D1, "09:00"), trace("t1", "A", Decision.ANSWER, evidence=ref, nlu_method="rules", steps=stage(2.0))),
        (
            at(D1, "10:00"),
            trace(
                "t2",
                "B",
                Decision.EXECUTE,
                language="ar",
                active_intent="return_request",
                latency_ms=300.0,
                nlu_method="llm",
                steps=stage(4.0),
                policy=[allow("p1")],
                tool_calls=[call("get_order", "read"), call("create_return", "create", policy="p1")],
            ),
        ),  # fmt: skip
        (
            at(D1, "11:00"),
            trace("t3", "C", Decision.CLARIFY, active_intent="refund_request", latency_ms=200.0, steps=stage(6.0)),
        ),
        (
            at(D1, "11:05"),
            trace(
                "t4",
                "C",
                Decision.HANDOFF,
                active_intent="refund_request",
                latency_ms=400.0,
                steps=stage(8.0),
                escalation_reason=EscalationReason.POLICY_DENIED,
                handoff_case_id="X",
                policy=[
                    PolicyRecord(request_id="p2", action="create_refund", decision="deny", reason_code="R-REFUND-14D")
                ],
            ),
        ),  # fmt: skip
        (
            at(D1, "12:00"),
            trace(
                "t5",
                "D",
                Decision.HANDOFF,
                language="arabizi",
                active_intent="return_request",
                latency_ms=500.0,
                steps=stage(10.0),
                escalation_reason=EscalationReason.UNVERIFIED_RESULT,
                handoff_case_id="Y",
                policy=[allow("p3")],
                tool_calls=[call("create_return", "create", "error", "TIMEOUT", policy="p3")],
            ),
        ),  # fmt: skip
        (
            at(D1, "13:00"),
            trace(
                "t6",
                "E",
                Decision.CLARIFY,
                latency_ms=150.0,
                steps=stage(12.0),
                evidence_empty_reason="no_match",
                nlu_method="rules_fallback",
                errors=(
                    "policy search failed (X), attempt 1",
                    "policy search failed (X), attempt 2",
                    "safety screen unavailable: UpstreamError",
                ),
            ),
        ),  # fmt: skip
    ]


def day_two() -> list[tuple[datetime, DecisionTrace]]:
    ref = [EvidenceRef(citation=f"a@v1#s{i}", document_id="a", version="v1", score=1.0) for i in (1, 2)]
    return [
        (at(D2, "09:00"), trace("t7", "F", Decision.ANSWER, evidence=ref, nlu_method="llm", latency_ms=120.0)),
        (at(D2, "09:30"), trace("t8", "A", Decision.ANSWER, latency_ms=80.0, turn_index=1)),
    ]


def case(
    case_id: str, opened: datetime, reply: int | None, resolved: int | None, reason: str = "policy_denied"
) -> HandoffCase:
    package = HandoffPackage(
        summary="s",
        reason=EscalationReason(reason),
        priority="normal",
        suggested_next_step="n",
        language="en",  # type: ignore[arg-type]
    )
    found = HandoffCase(case_id=case_id, tenant_id=T, conversation_id=f"conv-{case_id}", package=package,
                        created_at=opened, updated_at=opened)  # fmt: skip
    if reply is not None:
        found.add_event(actor="sara", kind="reply", at=opened + timedelta(seconds=reply))
    if resolved is not None:
        found.add_event(actor="sara", kind="resolved", at=opened + timedelta(seconds=resolved))
    return found


@pytest.fixture
async def metrics(store_kind: str, tmp_path: Path) -> MetricsService:
    clock = SteppingClock()
    if store_kind == "memory":
        traces: Any = InMemoryTraceStore(clock)
        cases: Any = InMemoryCaseStore()
    else:
        db = SqliteDatabase(tmp_path / "metrics.sqlite3")
        traces, cases = SqliteTraceStore(db, clock), SqliteCaseStore(db)
    for moment, record in [*day_one(), *day_two()]:
        clock.moment = moment
        await traces.add(record)
    for item in (
        case("X", at(D1, "10:00"), 300, 3600),
        case("Y", at(D1, "12:00"), 1200, None, "unverified_result"),
        case("Z", at(D2, "08:00"), 30, 600),
    ):
        await cases.add(item)
    return MetricsService(traces, cases)


def days(points: list[Any], field: str) -> list[Any]:
    return [getattr(p, field) for p in points]


# ---- conversation metrics ----


async def test_conversations(metrics: MetricsService) -> None:
    assert days(await metrics.conversations(T, D1, END), "value") == [5, 1]  # A counts on day 1 only (first turn)


async def test_automation_rate(metrics: MetricsService) -> None:
    day1, day2 = await metrics.automation_rate(T, D1, END)
    assert (day1.numerator, day1.denominator, day1.rate) == (3, 5, 0.6)  # C and D ended in a case
    assert (day2.numerator, day2.denominator, day2.rate) == (1, 1, 1.0)


async def test_resolved_with_an_action(metrics: MetricsService) -> None:
    day1, day2 = await metrics.resolved_with_action(T, D1, END)
    assert (day1.numerator, day1.denominator, day1.rate) == (1, 5, 0.2)  # only B: D's write failed and it has a case
    assert (day2.numerator, day2.denominator) == (0, 1)


async def test_escalation_rate_by_reason(metrics: MetricsService) -> None:
    day1, day2 = await metrics.escalation_by_reason(T, D1, END)
    assert day1.conversations == 5 and day1.by_reason == {"policy_denied": 1, "unverified_result": 1}
    assert day1.rates == {"policy_denied": 0.2, "unverified_result": 0.2}
    assert (day2.conversations, day2.by_reason, day2.rates) == (1, {}, {})


async def test_time_to_first_human_reply_and_resolution_time(metrics: MetricsService) -> None:
    first = await metrics.time_to_first_human_reply(T, D1, END)
    assert [(p.count, p.p50, p.p95) for p in first] == [(2, 300.0, 1200.0), (1, 30.0, 30.0)]
    resolved = await metrics.case_resolution_time(T, D1, END)
    assert [(p.count, p.p50, p.p95) for p in resolved] == [(1, 3600.0, 3600.0), (1, 600.0, 600.0)]


# ---- action and policy metrics ----


async def test_action_failure_rate_by_tool_and_error_code(metrics: MetricsService) -> None:
    day1, day2 = await metrics.action_failures(T, D1, END)
    assert set(day1.tools) == {"create_return"}  # reads (get_order) are not actions
    stat = day1.tools["create_return"]
    assert (stat.calls, stat.failures, stat.rate, stat.errors) == (2, 1, 0.5, {"TIMEOUT": 1})
    assert day2.tools == {}


async def test_unverified_results(metrics: MetricsService) -> None:
    assert days(await metrics.unverified_results(T, D1, END), "value") == [1, 0]


async def test_unanswered_rate(metrics: MetricsService) -> None:
    day1, day2 = await metrics.unanswered_rate(T, D1, END)
    assert (day1.numerator, day1.denominator, day1.rate) == (1, 2, 0.5)  # t1 found a passage, t6 found nothing
    assert (day2.numerator, day2.denominator, day2.rate) == (0, 1, 0.0)  # t7 found two; t8 did not search


async def test_policy_outcomes_by_rule(metrics: MetricsService) -> None:
    day1, day2 = await metrics.policy_outcomes(T, D1, END)
    assert day1.counts == {"create_return:R-RETURN-14D:allow": 2, "create_refund:R-REFUND-14D:deny": 1}
    assert day2.counts == {}


async def test_dependency_errors_by_service(metrics: MetricsService) -> None:
    day1, day2 = await metrics.dependency_errors(T, D1, END)
    assert day1.counts == {"policy_search": 2, "safety_screen": 1} and day2.counts == {}


# ---- speed, language, AI ----


async def test_latency_overall_and_per_stage(metrics: MetricsService) -> None:
    day1, day2 = await metrics.latency(T, D1, END)
    assert (day1.overall.count, day1.overall.p50, day1.overall.p95) == (6, 200.0, 500.0)  # 100 150 200 300 400 500
    assert (day2.overall.p50, day2.overall.p95) == (80.0, 120.0)
    understand = day1.stages["understand"]
    assert (understand.count, understand.p50, understand.p95) == (6, 6.0, 12.0)
    assert "risk_screen" not in day1.stages  # skipped steps are not timed


async def test_language_mix(metrics: MetricsService) -> None:
    day1, day2 = await metrics.language_mix(T, D1, END)
    assert day1.counts == {"en": 4, "ar": 1, "arabizi": 1} and day2.counts == {"en": 2}


async def test_ai_fallback_rate(metrics: MetricsService) -> None:
    day1, day2 = await metrics.ai_fallback_rate(T, D1, END)
    assert (day1.numerator, day1.denominator, day1.rate) == (1, 2, 0.5)  # t2 used the AI, t6 fell back
    assert (day2.numerator, day2.denominator, day2.rate) == (0, 1, 0.0)


# ---- filters, buckets, other tenants ----


async def test_filters_narrow_every_number(metrics: MetricsService) -> None:
    english = MetricFilters(language="en")
    assert days(await metrics.conversations(T, D1, END, filters=english), "value") == [3, 1]  # A C E | F
    day1 = (await metrics.automation_rate(T, D1, END, filters=english))[0]
    assert (day1.numerator, day1.denominator) == (2, 3)  # C has a case
    assert (await metrics.action_failures(T, D1, END, filters=english))[0].tools == {}  # B and D are not English
    arabic = MetricFilters(language="ar")
    assert (await metrics.action_failures(T, D1, END, filters=arabic))[0].tools["create_return"].calls == 1
    reason = MetricFilters(escalation_reason="policy_denied")
    assert days(await metrics.conversations(T, D1, END, filters=reason), "value") == [1, 0]
    assert [p.count for p in await metrics.time_to_first_human_reply(T, D1, END, filters=reason)] == [
        1,
        1,
    ]  # X and Z (Y is unverified_result)
    unverified = MetricFilters(escalation_reason="unverified_result")
    assert [p.count for p in await metrics.time_to_first_human_reply(T, D1, END, filters=unverified)] == [1, 0]
    assert (await metrics.policy_outcomes(T, D1, END, filters=reason))[0].counts == {
        "create_refund:R-REFUND-14D:deny": 1
    }


async def test_hour_week_and_month_buckets_and_empty_buckets_are_included(metrics: MetricsService) -> None:
    hours = await metrics.conversations(T, at(D1, "09:00"), at(D1, "14:00"), bucket="hour")
    assert days(hours, "value") == [1, 1, 1, 1, 1]  # one conversation starts in each of 09, 10, 11, 12, 13
    empty = await metrics.conversations(T, D1 - timedelta(days=2), D1, bucket="day")
    assert days(empty, "value") == [0, 0]
    week = await metrics.conversations(T, D1, END, bucket="week")  # 28 Sep 2026 is a Monday
    assert [(p.bucket_start, p.value) for p in week] == [(D1, 6)]
    month = await metrics.conversations(T, D1, END, bucket="month")
    assert [(p.bucket_start, p.value) for p in month] == [(datetime(2026, 9, 1, tzinfo=UTC), 6)]


async def test_another_business_sees_nothing(metrics: MetricsService) -> None:
    assert days(await metrics.conversations("tel_001", D1, END), "value") == [0, 0]
    assert (await metrics.latency("tel_001", D1, END))[0].overall.p50 is None


# ---- the small helpers ----


def test_buckets() -> None:
    moment = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert bucket_start(moment, "hour") == datetime(2026, 12, 31, 23, tzinfo=UTC)
    assert bucket_start(moment, "day") == datetime(2026, 12, 31, tzinfo=UTC)
    assert bucket_start(moment, "week") == datetime(2026, 12, 28, tzinfo=UTC)  # Monday
    assert bucket_start(moment, "month") == datetime(2026, 12, 1, tzinfo=UTC)
    assert next_bucket(datetime(2026, 12, 1, tzinfo=UTC), "month") == datetime(2027, 1, 1, tzinfo=UTC)
    assert next_bucket(datetime(2026, 1, 31, tzinfo=UTC).replace(day=1), "month") == datetime(2026, 2, 1, tzinfo=UTC)
    with pytest.raises(ValueError, match="after start"):
        bucket_starts(END, D1, "day")
    with pytest.raises(ValueError, match="buckets"):
        bucket_starts(D1, D1 + timedelta(days=MAX_BUCKETS + 5), "day")


def test_percentile_is_nearest_rank() -> None:
    assert percentile([], 0.5) is None
    assert percentile([7], 0.95) == 7
    assert [percentile([10, 20, 30, 40], p) for p in (0.25, 0.5, 0.75, 0.95, 1.0)] == [10, 20, 30, 40, 40]
    assert percentile([40, 10, 30, 20], 0.5) == 20  # order does not matter
