"""The dashboard API: exact numbers on a small hand-made data set, and consistency on seeded conversations."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from team_b.api.app import create_app
from team_b.api.dashboard import METRICS
from team_b.brain.metrics import MetricsService
from team_b.container import Container, build_container
from team_b.demo_seed import DriftClock, seed
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.trace import DecisionTrace, PolicyRecord, ToolCallRecord
from tests.support import make_settings

T = "shop_001"
START = datetime(2026, 9, 21, tzinfo=UTC)
END = START + timedelta(days=7)
WINDOW = {"tenant_id": T, "from": START.isoformat(), "to": END.isoformat()}
LATE = {**WINDOW, "to": (START + timedelta(days=8)).isoformat()}  # covers everything the seeder does


async def client_for(container: Container) -> tuple[FastAPI, httpx.AsyncClient]:
    app = create_app(container=container)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return app, httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
async def seeded(tmp_path: Path) -> AsyncIterator[tuple[Container, httpx.AsyncClient]]:
    clock = DriftClock(START)
    container = build_container(make_settings(tmp_path), clock=clock)
    await seed(container, clock, conversations=90, days=6, seed=3)
    app, http = await client_for(container)
    async with app.router.lifespan_context(app), http:
        yield container, http


# ---- exact numbers on a small data set ----


class Clock:
    def __init__(self) -> None:
        self.moment = START

    def now(self) -> datetime:
        return self.moment

    def today(self) -> Any:
        return self.moment.date()


def trace(tid: str, conv: str, decision: Decision, **over: Any) -> DecisionTrace:
    base: dict[str, Any] = {
        "trace_id": tid, "request_id": tid, "tenant_id": T, "conversation_id": conv, "turn_index": 0,
        "decision": decision, "language": "en", "active_intent": "policy_question", "latency_ms": 100.0,
        "customer_message": f"question {tid}",
    }  # fmt: skip
    return DecisionTrace.model_validate({**base, **over})


@pytest.fixture
async def small(tmp_path: Path) -> AsyncIterator[tuple[Container, httpx.AsyncClient]]:
    """Previous week: 2 conversations (1 with a case). This week: 4 conversations (1 case), 1 unanswered question."""
    clock = Clock()
    container = build_container(make_settings(tmp_path), clock=clock)  # type: ignore[arg-type]
    rows = [
        (-3, trace("p1", "P1", Decision.ANSWER, latency_ms=50.0)),
        (-2, trace("p2", "P2", Decision.HANDOFF, escalation_reason=EscalationReason.POLICY_DENIED, latency_ms=250.0)),
        (1, trace("a1", "A", Decision.ANSWER, latency_ms=100.0)),
        (1, trace("b1", "B", Decision.ANSWER, latency_ms=200.0)),
        (
            2,
            trace(
                "c1",
                "C",
                Decision.CLARIFY,
                latency_ms=300.0,
                evidence_empty_reason="no_match",
                customer_message="Do you sell furniture?",
            ),
        ),  # fmt: skip
        (
            2,
            trace(
                "c2",
                "C",
                Decision.HANDOFF,
                escalation_reason=EscalationReason.NO_EVIDENCE,
                latency_ms=400.0,
                handoff_case_id="k1",
                customer_message="furniture please",
            ),
        ),  # fmt: skip
        (3, trace("d1", "D", Decision.ANSWER, latency_ms=150.0)),
    ]
    for days, record in rows:
        clock.moment = START + timedelta(days=days)
        await container.traces.add(record)
    app, http = await client_for(container)
    async with app.router.lifespan_context(app), http:
        yield container, http


def move(container: Container, days: float) -> None:
    container.clock.moment = START + timedelta(days=days)  # type: ignore[attr-defined]


async def test_the_overview_has_the_numbers_and_the_change_from_the_previous_period(small: Any) -> None:
    _, http = small
    body = (await http.get("/v1/dashboard/overview", params=WINDOW)).json()
    assert body["previous_start"].startswith("2026-09-14") and body["previous_end"].startswith("2026-09-21")
    assert body["conversations"] == {"value": 4, "previous": 2, "change": 2}
    assert body["automation_rate"] == {"value": 0.75, "previous": 0.5, "change": 0.25}
    assert body["escalation_rate"] == {"value": 0.25, "previous": 0.5, "change": -0.25}
    assert body["unverified_results"] == {"value": 0, "previous": 0, "change": 0}
    # latencies this week 100 200 300 400 150 -> sorted 100 150 200 300 400, p95 = 5th = 400; last week 50 250 -> 250
    assert body["p95_latency_ms"] == {"value": 400.0, "previous": 250.0, "change": 150.0}
    assert body["open_cases"] == 0


async def test_an_empty_period_has_no_rates_and_no_change(small: Any) -> None:
    _, http = small
    empty = {**WINDOW, "from": "2025-01-01T00:00:00Z", "to": "2025-01-08T00:00:00Z"}
    body = (await http.get("/v1/dashboard/overview", params=empty)).json()
    assert body["conversations"] == {"value": 0, "previous": 0, "change": 0}
    assert body["automation_rate"] == {"value": None, "previous": None, "change": None}


async def test_the_default_window_is_the_last_seven_days(small: Any) -> None:
    container, http = small
    move(container, 7)
    body = (await http.get("/v1/dashboard/overview", params={"tenant_id": T})).json()
    assert body["period_end"].startswith("2026-09-28") and body["period_start"].startswith("2026-09-21")
    assert body["conversations"]["value"] == 4


async def test_timeseries_gives_one_point_per_bucket(small: Any) -> None:
    _, http = small
    body = (await http.get("/v1/dashboard/timeseries", params={**WINDOW, "metric": "conversations"})).json()
    assert [(p["bucket_start"][:10], p["value"]) for p in body["points"]] == [
        ("2026-09-21", 0), ("2026-09-22", 2), ("2026-09-23", 1), ("2026-09-24", 1), ("2026-09-25", 0),
        ("2026-09-26", 0), ("2026-09-27", 0),
    ]  # fmt: skip
    weekly = {**WINDOW, "metric": "automation_rate", "bucket": "week"}
    rate = (await http.get("/v1/dashboard/timeseries", params=weekly)).json()
    assert rate["points"][0]["numerator"] == 3 and rate["points"][0]["denominator"] == 4
    arabic = {**WINDOW, "metric": "conversations", "language": "ar"}
    none = (await http.get("/v1/dashboard/timeseries", params=arabic)).json()
    assert sum(p["value"] for p in none["points"]) == 0


async def test_timeseries_checks_its_parameters(small: Any) -> None:
    _, http = small
    assert (await http.get("/v1/dashboard/timeseries", params={**WINDOW, "metric": "nope"})).status_code == 422
    year = {**WINDOW, "metric": "conversations", "bucket": "year"}
    assert (await http.get("/v1/dashboard/timeseries", params=year)).status_code == 422
    too_many = {
        "tenant_id": T, "from": "2020-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z",
        "metric": "conversations", "bucket": "hour",
    }  # fmt: skip
    assert (await http.get("/v1/dashboard/timeseries", params=too_many)).status_code == 422
    backwards = {**WINDOW, "from": END.isoformat(), "to": START.isoformat()}
    assert (await http.get("/v1/dashboard/overview", params=backwards)).status_code == 422
    naive = {**WINDOW, "from": "2026-09-21T00:00:00"}
    assert (await http.get("/v1/dashboard/overview", params=naive)).status_code == 422
    assert (await http.get("/v1/dashboard/overview", params={**WINDOW, "tenant_id": "nope"})).status_code == 404


async def test_the_timeseries_matches_the_metrics_service(small: Any) -> None:
    container, http = small
    expected = await MetricsService(container.traces, container.cases).latency(T, START, END, "day")
    got = (await http.get("/v1/dashboard/timeseries", params={**WINDOW, "metric": "latency"})).json()["points"]
    assert got == [p.model_dump(mode="json") for p in expected]


async def ids_of(http: httpx.AsyncClient, **extra: Any) -> list[str]:
    page = (await http.get("/v1/dashboard/conversations", params={**WINDOW, **extra})).json()
    return [c["conversation_id"] for c in page["items"]]


async def test_conversations_filter_and_search(small: Any) -> None:
    _, http = small
    everything = (await http.get("/v1/dashboard/conversations", params=WINDOW)).json()
    assert [c["conversation_id"] for c in everything["items"]] == ["D", "C", "B", "A"]  # newest activity first
    row = next(c for c in everything["items"] if c["conversation_id"] == "C")
    assert (row["turns"], row["outcome"], row["has_case"], row["escalation_reason"]) == (
        2,
        "handoff",
        True,
        "no_evidence",
    )
    assert row["problems"] == ["handoff: no_evidence", "no policy answer found"] and row["avg_latency_ms"] == 350.0
    assert await ids_of(http, status="escalated") == ["C"]
    assert await ids_of(http, status="automated") == ["D", "B", "A"]
    assert await ids_of(http, reason="no_evidence") == ["C"]
    assert await ids_of(http, language="ar") == []
    assert await ids_of(http, q="FURNITURE") == ["C"]
    assert "D" in await ids_of(http, q="d")
    assert (await http.get("/v1/dashboard/conversations", params={**WINDOW, "status": "weird"})).status_code == 422


async def test_conversations_page_with_a_cursor(small: Any) -> None:
    _, http = small
    seen, cursor = [], None
    for _ in range(5):
        params: dict[str, Any] = {**WINDOW, "limit": 2, **({"cursor": cursor} if cursor else {})}
        page = (await http.get("/v1/dashboard/conversations", params=params)).json()
        seen += [c["conversation_id"] for c in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == ["D", "C", "B", "A"]
    assert (await http.get("/v1/dashboard/conversations", params={**WINDOW, "cursor": "junk"})).status_code == 422


async def test_one_conversation_with_its_transcript_traces_and_case(small: Any) -> None:
    _, http = small
    body = (await http.get("/v1/dashboard/conversations/C", params={"tenant_id": T})).json()
    customer = [line["text"] for line in body["transcript"] if line["role"] == "customer"]
    assert customer == ["Do you sell furniture?", "furniture please"]
    assert [t["trace_id"] for t in body["traces"]] == ["c1", "c2"] and body["case"] is None
    assert (await http.get("/v1/dashboard/conversations/NOPE", params={"tenant_id": T})).status_code == 404


async def test_knowledge_gaps_group_the_unanswered_questions(small: Any) -> None:
    container, http = small
    move(container, 4)
    again = trace(
        "c3", "E", Decision.CLARIFY, evidence_empty_reason="no_match", customer_message="do you sell furniture"
    )
    await container.traces.add(again)
    body = (await http.get("/v1/dashboard/knowledge-gaps", params=WINDOW)).json()
    assert body["questions_without_answer"] == 2
    assert [(g["count"], g["question"]) for g in body["groups"]] == [(2, "Do you sell furniture?")]


def call(kind: str, status: str, code: str | None, ms: float) -> ToolCallRecord:
    return ToolCallRecord(
        request_id=f"{kind}{ms}", tool="create_return" if kind != "read" else "get_order", operation_kind=kind,  # type: ignore[arg-type]
        status=status, error_code=code, latency_ms=ms, policy_request_id="p",  # type: ignore[arg-type]
    )  # fmt: skip


async def test_tools_show_failures_error_codes_and_speed(small: Any) -> None:
    container, http = small
    move(container, 4)
    allow = PolicyRecord(request_id="p", action="create_return", decision="allow", reason_code="R-RETURN-14D")
    calls = [
        call("create", "success", None, 10),
        call("create", "error", "TIMEOUT", 30),
        call("read", "success", None, 5),
    ]
    await container.traces.add(trace("t9", "G", Decision.EXECUTE, tool_calls=calls, policy=[allow]))
    tools = {t["tool"]: t for t in (await http.get("/v1/dashboard/tools", params=WINDOW)).json()["tools"]}
    made = tools["create_return"]
    assert (made["calls"], made["failures"], made["rate"], made["errors"], made["p95_ms"]) == (
        2,
        1,
        0.5,
        {"TIMEOUT": 1},
        30.0,
    )
    assert tools["get_order"]["calls"] == 1 and tools["get_order"]["failures"] == 0


async def test_escalations_show_the_reasons_and_the_rules_behind_them(small: Any) -> None:
    container, http = small
    move(container, 4)
    deny = PolicyRecord(request_id="d", action="create_refund", decision="deny", reason_code="R-REFUND-14D")
    held = trace("h1", "H", Decision.HANDOFF, escalation_reason=EscalationReason.POLICY_DENIED, policy=[deny])
    await container.traces.add(held)
    body = (await http.get("/v1/dashboard/escalations", params=WINDOW)).json()
    assert body["by_reason_total"] == {"no_evidence": 1, "policy_denied": 1} and body["conversations"] == 5
    assert body["top_rules"] == [{"rule": "create_refund:R-REFUND-14D", "decision": "deny", "count": 1}]
    assert len(body["by_reason"]) == 7


# ---- seeded conversations ----


async def test_the_seeded_numbers_agree_with_each_other(seeded: Any) -> None:
    _, http = seeded
    overview = (await http.get("/v1/dashboard/overview", params=LATE)).json()
    total = overview["conversations"]["value"]
    assert total == 90
    pages, cursor = [], None
    while True:
        extra = {"limit": 25, **({"cursor": cursor} if cursor else {})}
        page = (await http.get("/v1/dashboard/conversations", params={**LATE, **extra})).json()
        pages += page["items"]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    ids = [c["conversation_id"] for c in pages]
    assert len(ids) == len(set(ids)) == total
    with_case = sum(1 for c in pages if c["has_case"])
    assert overview["escalation_rate"]["value"] == pytest.approx(with_case / total)
    assert overview["automation_rate"]["value"] == pytest.approx((total - with_case) / total)
    last = [c["last_at"] for c in pages]
    assert last == sorted(last, reverse=True)
    escalations = (await http.get("/v1/dashboard/escalations", params=LATE)).json()
    assert sum(escalations["by_reason_total"].values()) == with_case


async def test_every_metric_answers_on_seeded_data(seeded: Any) -> None:
    _, http = seeded
    for metric in METRICS:
        response = await http.get("/v1/dashboard/timeseries", params={**WINDOW, "metric": metric})
        assert response.status_code == 200, metric
        assert len(response.json()["points"]) == 7, metric


async def test_seeded_staff_work_shows_in_the_case_times_and_the_gaps(seeded: Any) -> None:
    _, http = seeded
    window = {**LATE, "bucket": "month", "metric": "first_reply_time"}
    reply = (await http.get("/v1/dashboard/timeseries", params=window)).json()["points"]
    assert sum(p["count"] for p in reply) > 0 and all(p["p50"] is None or p["p50"] > 0 for p in reply)
    gaps = (await http.get("/v1/dashboard/knowledge-gaps", params=LATE)).json()
    assert gaps["questions_without_answer"] > 0 and gaps["groups"][0]["count"] >= 2
