"""The test-only admin endpoint: off by default, and when on it switches stand-ins off, heals them, and reports."""

import asyncio
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import httpx
import pytest

from team_b.api.app import create_app
from team_b.container import Container, build_container
from tests.support import make_settings

T = "shop_001"


async def client(container: Container) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


@pytest.fixture
async def admin(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Container]]:
    container = build_container(
        make_settings(tmp_path, fixed_today=date(2026, 9, 28), enable_test_admin=True, rate_limit_per_minute=10000)
    )
    async for http in client(container):
        yield http, container


async def ask(http: httpx.AsyncClient, text: str, conversation: str = "c") -> dict:  # type: ignore[type-arg]
    response = await http.post(f"/v1/conversations/{conversation}/messages", json={"tenant_id": T, "text": text})
    assert response.status_code == 200
    return response.json()  # type: ignore[no-any-return]


async def test_the_endpoints_do_not_exist_unless_switched_on(tmp_path: Path) -> None:
    container = build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))
    assert container.settings.enable_test_admin is False
    async for http in client(container):
        assert (await http.post("/v1/_test/chaos", json={"plug": "shop", "seconds": 5})).status_code == 404
        assert (await http.get("/v1/_test/report", params={"tenant_id": T})).status_code == 404
        assert (await http.get("/v1/_test/state")).status_code == 404


def test_the_setting_comes_from_the_environment() -> None:
    from team_b.config import Settings

    assert Settings().enable_test_admin is False
    assert Settings.from_env({"TEAM_B_ENABLE_TEST_ADMIN": "1"}).enable_test_admin is True


async def test_a_chaos_on_the_rule_checker_blocks_actions_then_heals_by_itself(
    admin: tuple[httpx.AsyncClient, Container],
) -> None:
    http, container = admin
    started = await http.post("/v1/_test/chaos", json={"plug": "rule_checker", "seconds": 0.4})
    assert started.json() == {"plug": "rule_checker", "failing": True, "recovers_in_s": 0.4}
    assert (await http.get("/v1/_test/state")).json() == {"failing": ["rule_checker"]}
    await ask(http, "I want to return order NS-20790, the size is wrong")
    during = await ask(http, "01123456702")
    assert during["decision"] == "handoff"  # nothing could be checked
    await asyncio.sleep(0.6)  # no call from outside: it recovers by itself
    assert (await http.get("/v1/_test/state")).json() == {"failing": []}
    await ask(http, "I want to return order NS-20790, the size is wrong", "d")
    after = await ask(http, "01123456702", "d")
    assert after["decision"] == "confirm"


@pytest.mark.parametrize("plug", ["shop", "policy_search", "rule_checker", "safety_screen"])
async def test_every_plug_can_be_broken_and_healed(admin: tuple[httpx.AsyncClient, Container], plug: str) -> None:
    http, _ = admin
    await http.post("/v1/_test/chaos", json={"plug": plug, "seconds": 3600})
    assert (await http.get("/v1/_test/state")).json()["failing"] == [plug]
    healed = await http.post("/v1/_test/heal", json={"plug": plug})
    assert healed.json() == {"failing": []}
    assert (await ask(http, "What is your return policy?"))["decision"] == "answer"


async def test_the_shop_outage_keeps_the_audit_log_and_heals_without_a_restart(
    admin: tuple[httpx.AsyncClient, Container],
) -> None:
    http, container = admin
    await ask(http, "Where is my order NS-20877? My phone is 01012345601", "before")
    seen = len(container.shop.audit_log(T))  # type: ignore[union-attr]
    await http.post("/v1/_test/chaos", json={"plug": "shop", "seconds": 3600})
    down = await ask(http, "Where is my order NS-20877? My phone is 01012345601", "during")
    assert down["decision"] != "answer" and "Linen" not in down["text"]
    await http.post("/v1/_test/heal", json={})
    assert len(container.shop.audit_log(T)) >= seen  # type: ignore[union-attr]  # the log was kept
    assert (await ask(http, "Where is my order NS-20877? My phone is 01012345601", "after"))["decision"] == "answer"


async def test_a_second_chaos_on_the_same_plug_restarts_its_timer(admin: tuple[httpx.AsyncClient, Container]) -> None:
    http, _ = admin
    await http.post("/v1/_test/chaos", json={"plug": "safety_screen", "seconds": 0.3})
    await asyncio.sleep(0.15)
    await http.post("/v1/_test/chaos", json={"plug": "safety_screen", "seconds": 0.5})
    await asyncio.sleep(0.3)
    assert (await http.get("/v1/_test/state")).json() == {"failing": ["safety_screen"]}
    await asyncio.sleep(0.4)
    assert (await http.get("/v1/_test/state")).json() == {"failing": []}


async def test_bad_requests_are_refused(admin: tuple[httpx.AsyncClient, Container]) -> None:
    http, _ = admin
    assert (await http.post("/v1/_test/chaos", json={"plug": "database", "seconds": 5})).status_code == 422
    assert (await http.post("/v1/_test/chaos", json={"plug": "shop", "seconds": 0})).status_code == 422
    assert (await http.post("/v1/_test/chaos", json={"plug": "shop", "seconds": 99999})).status_code == 422
    assert (await http.post("/v1/_test/chaos", json={"plug": "shop", "seconds": 5, "x": 1})).status_code == 422
    assert (await http.get("/v1/_test/report")).status_code == 422
    assert (await http.get("/v1/_test/report", params={"tenant_id": "nope"})).status_code == 404


async def test_the_report_has_what_the_checker_needs(admin: tuple[httpx.AsyncClient, Container]) -> None:
    http, _ = admin
    await ask(http, "I want to return order NS-20790, the size is wrong")
    await ask(http, "01123456702")
    await ask(http, "yes")
    report = (await http.get("/v1/_test/report", params={"tenant_id": T})).json()
    assert set(report) == {"audit", "traces", "orders"}
    assert [t["turn_index"] for t in report["traces"]] == [0, 1, 2]  # oldest first
    assert [e["tool"] for e in report["audit"]][-1] == "create_return"
    assert report["orders"]["NS-20790"] == {"customer_id": "C-101", "order_total": 640}
    assert "01123456702" not in str(report["traces"])  # the traces hold no phone number
