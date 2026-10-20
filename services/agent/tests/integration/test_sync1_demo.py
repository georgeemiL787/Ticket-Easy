"""SYNC 1: the large-refund demo over the real HTTP API: /chat -> handoff -> /inbox approve -> confirmation in /chat."""

from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from team_b.api.app import create_app
from team_b.container import Container, build_container
from tests.support import make_settings

T = "shop_001"
INBOX = "/v1/handoff/cases"


@pytest.fixture
async def demo(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Container]]:
    container = build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28), rate_limit_per_minute=1000))
    app = create_app(container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http, container


async def chat(http: httpx.AsyncClient, text: str, conversation: str = "demo") -> dict[str, Any]:
    response = await http.post(f"/v1/conversations/{conversation}/messages", json={"tenant_id": T, "text": text})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def ask_for_the_large_refund(http: httpx.AsyncClient) -> str:
    await chat(http, "I want a refund for order NS-20934")
    handoff = await chat(http, "01098765405")
    assert handoff["decision"] == "handoff" and handoff["handoff_case_id"]
    return str(handoff["handoff_case_id"])


def refunds(container: Container) -> list[Any]:
    assert container.shop is not None
    return [e for e in container.shop.audit_log(T) if e.tool == "create_refund"]


async def test_the_large_refund_demo_approve(demo: tuple[httpx.AsyncClient, Container]) -> None:
    http, container = demo
    case_id = await ask_for_the_large_refund(http)

    listed = (await http.get(INBOX, params={"tenant_id": T})).json()
    row = next(c for c in listed["items"] if c["case_id"] == case_id)
    assert row["reason"] == "approval_required"

    case = (await http.get(f"{INBOX}/{case_id}")).json()
    assert (
        case["pending_approval"]["capability"] == "create_refund"
        and case["pending_approval"]["arguments"]["amount"] == 3450
    )
    assert case["package"]["customer"]["verified"] is True and case["package"]["rule_answers"]
    assert refunds(container) == []  # nothing happened yet

    assert (
        await http.post(f"{INBOX}/{case_id}/decision", json={"agent": "Sara", "approve": True})
    ).status_code == 409  # not claimed
    assert (await http.post(f"{INBOX}/{case_id}/claim", json={"agent": "Sara"})).status_code == 200
    decided = await http.post(f"{INBOX}/{case_id}/decision", json={"agent": "Sara", "approve": True, "note": "ok"})
    assert decided.status_code == 200 and decided.json()["pending_approval"] is None

    outbox = (await http.get("/v1/conversations/demo/outbox", params={"tenant_id": T})).json()
    assert len(outbox) == 1 and "REF-" in outbox[0]["text"]
    [entry] = refunds(container)
    assert (entry.actor, entry.approval_id, entry.applied) == ("human", case_id, True)

    again = await http.post(f"{INBOX}/{case_id}/decision", json={"agent": "Sara", "approve": True})
    assert again.status_code == 409  # nothing waits any more
    assert len(refunds(container)) == 1


async def test_the_large_refund_demo_reject(demo: tuple[httpx.AsyncClient, Container]) -> None:
    http, container = demo
    case_id = await ask_for_the_large_refund(http)
    await http.post(f"{INBOX}/{case_id}/claim", json={"agent": "Sara"})
    assert (await http.post(f"{INBOX}/{case_id}/decision", json={"agent": "Sara", "approve": False})).status_code == 200
    outbox = (await http.get("/v1/conversations/demo/outbox", params={"tenant_id": T})).json()
    assert "could not approve" in outbox[0]["text"] and refunds(container) == []


async def test_approving_cannot_override_a_no(demo: tuple[httpx.AsyncClient, Container]) -> None:
    http, container = demo
    case_id = await ask_for_the_large_refund(http)
    container.shop._tenants[T].shop.orders["NS-20934"]["delivered_at"] = "2026-08-01"  # type: ignore[union-attr, attr-defined]
    await http.post(f"{INBOX}/{case_id}/claim", json={"agent": "Sara"})
    await http.post(f"{INBOX}/{case_id}/decision", json={"agent": "Sara", "approve": True})
    assert refunds(container) == []
    case = (await http.get(f"{INBOX}/{case_id}")).json()
    assert case["events"][-1]["kind"] == "approval_denied"
    outbox = (await http.get("/v1/conversations/demo/outbox", params={"tenant_id": T})).json()
    assert "more than 14 days" in outbox[0]["text"]


async def test_another_person_cannot_decide_the_claimed_case(demo: tuple[httpx.AsyncClient, Container]) -> None:
    http, container = demo
    case_id = await ask_for_the_large_refund(http)
    await http.post(f"{INBOX}/{case_id}/claim", json={"agent": "Sara"})
    response = await http.post(f"{INBOX}/{case_id}/decision", json={"agent": "Omar", "approve": True})
    assert response.status_code == 409 and refunds(container) == []


async def test_the_customer_can_keep_chatting_while_the_case_waits(demo: tuple[httpx.AsyncClient, Container]) -> None:
    http, container = demo
    case_id = await ask_for_the_large_refund(http)
    quiet = await chat(http, "any news?")
    assert quiet["decision"] == "handoff" and quiet["handoff_case_id"] == case_id  # same case, no second one
    assert refunds(container) == []
