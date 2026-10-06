"""Reassigning cases (managers only), the SLA settings, and the queue with its timers."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from team_b.container import Container
from team_b.domain.decision import EscalationReason
from team_b.domain.handoff import CaseStatus, HandoffCase, HandoffPackage
from team_b.domain.tenant import DEFAULT_SLA_MINUTES, EscalationConfig

T = "shop_001"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)  # the clock of the test container
BASE = "/v1/handoff/cases"
R = EscalationReason


async def add_case(container: Container, case_id: str, priority: str, waited_minutes: int, **over: Any) -> None:
    package = HandoffPackage(
        summary=f"case {case_id}",
        reason=R.NO_EVIDENCE,
        priority=priority,
        suggested_next_step="look",  # type: ignore[arg-type]
    )
    opened = NOW - timedelta(minutes=waited_minutes)
    await container.cases.add(
        HandoffCase(
            case_id=case_id,
            tenant_id=T,
            conversation_id=f"conv-{case_id}",
            package=package,
            created_at=opened,
            updated_at=opened,
            **over,
        )  # fmt: skip
    )


async def hand_off(chat: httpx.AsyncClient) -> str:
    reply = await chat.post("/v1/conversations/c1/messages", json={"tenant_id": T, "text": "I want to talk to a human"})
    case_id: str = reply.json()["handoff_case_id"]
    return case_id


def error_code(response: httpx.Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


# ---- reassign ----


async def test_a_manager_gives_an_open_case_to_someone(chat: httpx.AsyncClient) -> None:
    case_id = await hand_off(chat)
    response = await chat.post(f"{BASE}/{case_id}/assign", json={"agent": "manager", "assignee": "sara"})
    case = response.json()
    assert response.status_code == 200 and (case["status"], case["claimed_by"]) == ("claimed", "sara")
    assert [(e["actor"], e["kind"]) for e in case["events"]] == [("sara", "claimed"), ("manager", "assigned")]
    assert case["events"][-1]["note"] == "to sara"
    # the person it was given to can now work on it
    reply = await chat.post(f"{BASE}/{case_id}/reply", json={"agent": "sara", "text": "I have it."})
    assert reply.status_code == 200


async def test_a_manager_can_take_a_case_from_the_person_who_has_it(chat: httpx.AsyncClient) -> None:
    case_id = await hand_off(chat)
    await chat.post(f"{BASE}/{case_id}/claim", json={"agent": "omar"})
    moved = (await chat.post(f"{BASE}/{case_id}/assign", json={"agent": "mona", "assignee": "sara"})).json()
    assert (moved["status"], moved["claimed_by"]) == ("claimed", "sara")
    kinds = [(e["actor"], e["kind"], e["note"]) for e in moved["events"]]
    assert ("mona", "released", "reassigned from omar") in kinds and ("sara", "claimed", "assigned by mona") in kinds
    stale = await chat.post(f"{BASE}/{case_id}/reply", json={"agent": "omar", "text": "still me"})
    assert (stale.status_code, error_code(stale)) == (409, "INVALID_STATE")  # omar no longer owns it


async def test_only_a_manager_may_reassign(chat: httpx.AsyncClient) -> None:
    case_id = await hand_off(chat)
    denied = await chat.post(f"{BASE}/{case_id}/assign", json={"agent": "sara", "assignee": "sara"})
    assert (denied.status_code, error_code(denied)) == (403, "FORBIDDEN")
    assert "not a manager" in denied.json()["error"]["message"]
    case = (await chat.get(f"{BASE}/{case_id}")).json()
    assert case["status"] == "open" and case["claimed_by"] is None  # nothing changed


async def test_a_finished_case_or_the_same_person_cannot_be_reassigned(chat: httpx.AsyncClient) -> None:
    case_id = await hand_off(chat)
    await chat.post(f"{BASE}/{case_id}/assign", json={"agent": "manager", "assignee": "sara"})
    same = await chat.post(f"{BASE}/{case_id}/assign", json={"agent": "manager", "assignee": "sara"})
    assert (same.status_code, error_code(same)) == (409, "INVALID_STATE")
    await chat.post(f"{BASE}/{case_id}/resolve", json={"agent": "sara"})
    closed = await chat.post(f"{BASE}/{case_id}/assign", json={"agent": "manager", "assignee": "omar"})
    assert (closed.status_code, error_code(closed)) == (409, "INVALID_STATE")


async def test_assign_checks_its_body_and_the_case(chat: httpx.AsyncClient) -> None:
    case_id = await hand_off(chat)
    assert (await chat.post(f"{BASE}/{case_id}/assign", json={"agent": "manager"})).status_code == 422
    assert (await chat.post(f"{BASE}/{case_id}/assign", json={"agent": "manager", "assignee": "  "})).status_code == 422
    assert (await chat.post(f"{BASE}/nope/assign", json={"agent": "manager", "assignee": "sara"})).status_code == 404


# ---- SLA settings ----


def test_the_default_sla_is_15_minutes_1_hour_and_4_hours() -> None:
    config = EscalationConfig()
    assert [config.sla_for(p) for p in ("urgent", "high", "normal")] == [15, 60, 240]
    assert DEFAULT_SLA_MINUTES["low"] == 1440


def test_a_shop_can_change_an_sla_and_bad_values_are_refused() -> None:
    assert EscalationConfig(sla_minutes={"normal": 30}).sla_for("normal") == 30
    assert EscalationConfig(sla_minutes={"normal": 30}).sla_for("urgent") == 15
    with pytest.raises(ValidationError, match="must be one of"):
        EscalationConfig(sla_minutes={"critical": 5})
    with pytest.raises(ValidationError, match="at least 1 minute"):
        EscalationConfig(sla_minutes={"high": 0})


# ---- the queue ----


async def test_the_queue_shows_the_time_left_and_puts_overdue_cases_first(
    chat: httpx.AsyncClient, chat_container: Container
) -> None:
    await add_case(chat_container, "u-late", "urgent", waited_minutes=20)  # limit 15: 5 minutes overdue
    await add_case(chat_container, "h-ok", "high", waited_minutes=10)  # limit 60: 50 minutes left
    await add_case(chat_container, "n-late", "normal", waited_minutes=300)  # limit 240: 60 minutes overdue
    await add_case(chat_container, "n-new", "normal", waited_minutes=5)  # 235 left
    await add_case(chat_container, "done", "urgent", waited_minutes=999, status=CaseStatus.OPEN)
    done = await chat_container.cases.get(T, "done")
    assert done is not None
    done.transition(CaseStatus.RESOLVED, actor="x", at=NOW)
    await chat_container.cases.save(done)  # resolved cases are not in the queue
    body = (await chat.get("/v1/dashboard/queue", params={"tenant_id": T})).json()
    assert body["overdue"] == 2
    assert [r["case_id"] for r in body["rows"]] == [
        "n-late",
        "u-late",
        "h-ok",
        "n-new",
    ]  # most overdue first, then by priority
    rows = {r["case_id"]: r for r in body["rows"]}
    assert (rows["u-late"]["sla_seconds"], rows["u-late"]["age_seconds"], rows["u-late"]["remaining_seconds"]) == (
        900,
        1200,
        -300,
    )
    assert rows["u-late"]["overdue"] and rows["n-late"]["remaining_seconds"] == -3600
    assert not rows["h-ok"]["overdue"] and rows["h-ok"]["remaining_seconds"] == 3000
    assert rows["n-new"]["remaining_seconds"] == 240 * 60 - 300


async def test_the_queue_includes_claimed_cases_and_flags_waiting_approvals(
    chat: httpx.AsyncClient, chat_container: Container
) -> None:
    await add_case(chat_container, "mine", "high", waited_minutes=5)
    case = await chat_container.cases.get(T, "mine")
    assert case is not None
    case.transition(CaseStatus.CLAIMED, actor="sara", at=NOW)
    await chat_container.cases.save(case)
    rows = (await chat.get("/v1/dashboard/queue", params={"tenant_id": T})).json()["rows"]
    assert [(r["case_id"], r["status"], r["claimed_by"], r["has_pending_approval"]) for r in rows] == [
        ("mine", "claimed", "sara", False)
    ]
    assert (await chat.get("/v1/dashboard/queue", params={"tenant_id": "nope"})).status_code == 404
