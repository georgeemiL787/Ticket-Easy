"""Alerts end to end: a seeded run, the policy search stand-in switched to failing, then the alerts API."""

import random
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from team_b.api.app import create_app
from team_b.container import Container, build_container
from tests.support import make_settings

T = "shop_001"
QUESTIONS = [  # all answered by the stand-in policy search
    "What is your return policy?",
    "How many days do I have to return an item?",
    "can I pay cash on delivery?",
    "what are your working hours?",
    "what is the refund policy?",
]


class StepClock:
    def __init__(self) -> None:
        self.at = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self.at

    def today(self) -> date:
        return self.at.date()

    def tick(self, minutes: float) -> None:
        self.at += timedelta(minutes=minutes)


@pytest.fixture
async def run(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Container, StepClock]]:
    clock = StepClock()
    container = build_container(
        make_settings(tmp_path, rate_limit_per_minute=10000, alert_interval_s=3600.0), clock=clock
    )
    app = create_app(container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http, container, clock


async def ask(container: Container, text: str, conversation: str) -> None:
    assert container.orchestrator is not None
    await container.orchestrator.handle_turn(T, conversation, text)


async def seeded_traffic(container: Container, clock: StepClock, turns: int, seed: int, tag: str) -> None:
    rng = random.Random(seed)
    for i in range(turns):
        await ask(container, rng.choice(QUESTIONS), f"{tag}-{i}")
        clock.tick(0.2)


async def alerts(http: httpx.AsyncClient, status: str = "all") -> dict:  # type: ignore[type-arg]
    response = await http.get("/v1/dashboard/alerts", params={"tenant_id": T, "status": status})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


async def test_a_failing_policy_search_opens_service_down_and_recovery_resolves_it(
    run: tuple[httpx.AsyncClient, Container, StepClock],
) -> None:
    http, container, clock = run
    assert container.alert_engine is not None and container.policy_search is not None
    await seeded_traffic(container, clock, 40, seed=1, tag="calm")
    await container.alert_engine.evaluate(T)
    assert (await alerts(http))["alerts"] == [] and (await alerts(http))["open_count"] == 0

    container.policy_search.fail_next("search_knowledge", times=100)  # the search goes down
    await seeded_traffic(container, clock, 6, seed=2, tag="down")
    report = await container.alert_engine.evaluate(T)
    assert [a.rule for a in report.opened] == ["service_down"]
    body = await alerts(http, "open")
    assert body["open_count"] == 1
    alert = body["alerts"][0]
    assert (alert["rule"], alert["severity"], alert["details"]["service"]) == (
        "service_down",
        "critical",
        "policy_search",
    )
    assert alert["resolved_at"] is None and alert["acknowledged_by"] is None

    again = await container.alert_engine.evaluate(T)  # still down: not opened a second time
    assert again.opened == [] and (await alerts(http))["open_count"] == 1

    ack = await http.post(
        f"/v1/dashboard/alerts/{alert['alert_id']}/ack", params={"tenant_id": T}, json={"agent": "Sara"}
    )
    assert ack.status_code == 200 and ack.json()["acknowledged_by"] == "Sara" and ack.json()["resolved_at"] is None

    container.policy_search.reset()  # the search comes back
    clock.tick(6)
    await seeded_traffic(container, clock, 5, seed=3, tag="back")
    report = await container.alert_engine.evaluate(T)
    assert [a.rule for a in report.resolved] == ["service_down"]
    body = await alerts(http)
    assert body["open_count"] == 0 and body["alerts"][0]["resolved_at"] is not None
    assert body["alerts"][0]["acknowledged_by"] == "Sara"
    assert (await alerts(http, "open"))["alerts"] == [] and len((await alerts(http, "resolved"))["alerts"]) == 1


async def test_the_engine_runs_in_the_app_and_opens_alerts_by_itself(tmp_path: Path) -> None:
    import asyncio

    clock = StepClock()
    container = build_container(
        make_settings(tmp_path, rate_limit_per_minute=10000, alert_interval_s=0.05), clock=clock
    )
    assert container.policy_search is not None
    container.policy_search.fail_next("search_knowledge", times=100)
    app = create_app(container=container)
    async with app.router.lifespan_context(app):
        await seeded_traffic(container, clock, 5, seed=4, tag="auto")
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            for _ in range(40):  # the task looks every 50 ms
                if (await alerts(http, "open"))["open_count"]:
                    break
                await asyncio.sleep(0.05)
            assert [a["rule"] for a in (await alerts(http, "open"))["alerts"]] == ["service_down"]


async def test_acknowledging_an_unknown_alert_or_of_another_business_is_404(
    run: tuple[httpx.AsyncClient, Container, StepClock],
) -> None:
    http, container, clock = run
    assert container.policy_search is not None and container.alert_engine is not None
    container.policy_search.fail_next("search_knowledge", times=100)
    await seeded_traffic(container, clock, 6, seed=5, tag="x")
    await container.alert_engine.evaluate(T)
    alert_id = (await alerts(http))["alerts"][0]["alert_id"]
    assert (
        await http.post("/v1/dashboard/alerts/nope/ack", params={"tenant_id": T}, json={"agent": "Sara"})
    ).status_code == 404
    wrong = await http.post(
        f"/v1/dashboard/alerts/{alert_id}/ack", params={"tenant_id": "other_shop"}, json={"agent": "Sara"}
    )
    assert wrong.status_code == 404 and wrong.json()["error"]["code"] == "TENANT_NOT_FOUND"


async def test_acknowledgement_validation_and_first_acknowledger_stays(
    run: tuple[httpx.AsyncClient, Container, StepClock],
) -> None:
    http, container, clock = run
    assert container.policy_search is not None and container.alert_engine is not None
    container.policy_search.fail_next("search_knowledge", times=100)
    await seeded_traffic(container, clock, 6, seed=6, tag="y")
    await container.alert_engine.evaluate(T)
    alert_id = (await alerts(http))["alerts"][0]["alert_id"]
    url = f"/v1/dashboard/alerts/{alert_id}/ack"
    assert (await http.post(url, params={"tenant_id": T}, json={"agent": "   "})).status_code == 422
    assert (await http.post(url, params={"tenant_id": T}, json={})).status_code == 422
    assert (await http.post(url, params={"tenant_id": T}, json={"agent": "Sara"})).json()["acknowledged_by"] == "Sara"
    assert (await http.post(url, params={"tenant_id": T}, json={"agent": "Omar"})).json()["acknowledged_by"] == "Sara"


async def test_listing_needs_a_known_tenant_and_a_valid_status(
    run: tuple[httpx.AsyncClient, Container, StepClock],
) -> None:
    http, _, _ = run
    assert (await http.get("/v1/dashboard/alerts")).status_code == 422
    assert (await http.get("/v1/dashboard/alerts", params={"tenant_id": "nope"})).status_code == 404
    assert (await http.get("/v1/dashboard/alerts", params={"tenant_id": T, "status": "weird"})).status_code == 422
