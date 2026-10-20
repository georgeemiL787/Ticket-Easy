"""The Business tools page API: Team C's sandbox tools as the agent sees them, refresh, and disable (manager only).

Team C is the scripted fake MCP server plus a mocked REST API (httpx.MockTransport): Team B's tests never start Team C.
"""

import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from agent_service.adapters.team_c_admin import TeamCAdmin
from agent_service.api.app import create_app
from agent_service.config import PROJECT_ROOT
from agent_service.container import Container, build_container
from tests.conftest import FIXED_TODAY
from tests.support import make_settings
from tests.unit.adapters.test_team_c_tools import BUSINESS, GET_ORDER_PROPOSAL, ORDER_OK, Fake, tool_def

PASSWORD = "correct horse battery"
T = "shop_001"
URL = "/v1/dashboard/business-tools"


class TeamCRest:
    """Team C's /api/v1 for publications and proposals, as data the test changes."""

    def __init__(self) -> None:
        self.publications: dict[str, dict[str, Any]] = {}
        self.posts: list[tuple[str, Any]] = []
        self.artifacts: dict[str, list[dict[str, Any]]] = {
            BUSINESS: [
                {
                    "id": "art-1", "proposal_id": "prop-2", "version": 1, "name": "Open a ticket",
                    "environment": "sandbox", "readiness_state": "needs_owner",
                    "auto_test": {"status": "succeeded", "needs_owner": [{"question": "Add one more test account"}]},
                }
            ],
            "biz-someone-else": [
                {"id": "art-other", "proposal_id": "p9", "version": 1, "name": "x",
                 "readiness_state": "not_tested", "auto_test": None}
            ],
        }  # fmt: skip
        self.promotions: dict[str, list[dict[str, Any]]] = {BUSINESS: [], "biz-someone-else": [
            {"id": "promo-other", "status": "active", "source_artifact_id": "art-other", "actor": "x", "note": "n",
             "rollback_contact": "c", "checklist": {}},
        ]}  # fmt: skip
        self.refuse: str | None = None  # Team C's refusal text for the next promotion
        self.connectors: dict[str, list[dict[str, Any]]] = {
            BUSINESS: [{"id": "live-abc", "environment": "live_demo", "host": "api.example.com:443",
                        "identities": [{"name": "alice", "scope": "end_user"}], "last_check": None}],
            "biz-someone-else": [{"id": "live-other", "environment": "live_demo", "host": "x.test", "identities": []}],
        }  # fmt: skip

    def add(self, pub_id: str, business_id: str, tool: str) -> None:
        self.publications[pub_id] = {
            "id": pub_id, "business_id": business_id, "tool_name": tool, "status": "published",
            "effective_status": "published", "problem": None, "environment": "sandbox", "production_ready": False,
            "published_at": "2026-10-12T08:00:00", "disabled_at": None, "proposal_id": GET_ORDER_PROPOSAL, "version": 1,
            "evidence": {"test_ids": ["t1"]},
        }  # fmt: skip

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        if request.method == "GET" and path == "/publications":
            wanted = request.url.params.get("business_id")
            return httpx.Response(200, json=[p for p in self.publications.values() if p["business_id"] == wanted])
        if request.method == "GET" and path.startswith("/publications/"):
            found = self.publications.get(path.split("/")[2])
            return httpx.Response(200, json=found) if found else httpx.Response(404, json={"code": "not_found"})
        if request.method == "POST" and path.endswith("/disable"):
            self.posts.append((path, json.loads(request.content)))
            pub = self.publications[path.split("/")[2]]
            pub.update(status="disabled", effective_status="disabled", disabled_at="2026-10-12T09:00:00")
            return httpx.Response(200, json=pub)
        if request.method == "GET" and path.startswith("/businesses/") and path.endswith("/connectors"):
            return httpx.Response(200, json=self.connectors.get(path.split("/")[2], []))
        if request.method == "POST" and path.startswith("/connectors/") and path.endswith("/check"):
            self.posts.append((path, None))
            cid = path.split("/")[2]
            check = {"checked_at": "2026-10-12T09:00:00+00:00", "ready": False, "checks": [
                {"check": "configuration", "status": "ok", "detail": "live_demo connector to api.example.com:443"},
                {"check": "reachable", "status": "ok", "detail": "The API answered HTTP 200"},
                {"check": "authentication", "status": "unknown", "detail": "run a read test"}]}  # fmt: skip
            for c in self.connectors[BUSINESS]:
                if c["id"] == cid:
                    c["last_check"] = check
            return httpx.Response(200, json=check)
        if request.method == "GET" and path.startswith("/businesses/") and path.endswith("/artifacts"):
            return httpx.Response(200, json=self.artifacts.get(path.split("/")[2], []))
        if request.method == "POST" and path.startswith("/artifacts/") and path.endswith("/auto-test"):
            self.posts.append((path, json.loads(request.content)))
            for a in self.artifacts[BUSINESS]:
                if a["id"] == path.split("/")[2]:
                    a.update(readiness_state="testing", auto_test={"status": "running", "needs_owner": []})
            return httpx.Response(200, json={"run_id": "run-1", "status": "running"})
        if request.method == "GET" and path.startswith("/businesses/") and path.endswith("/promotions"):
            return httpx.Response(200, json=self.promotions.get(path.split("/")[2], []))
        if request.method == "POST" and path.startswith("/artifacts/") and path.endswith("/promotions"):
            self.posts.append((path, json.loads(request.content)))
            if self.refuse:
                return httpx.Response(409, json={"code": "promotion_refused", "message": self.refuse})
            promo = {
                "id": "promo-1", "status": "active", "source_artifact_id": path.split("/")[2],
                "production_artifact_id": "art-prod", "publication_id": "pub-prod",
                "actor": json.loads(request.content)["actor"], "note": "n", "rollback_contact": "c",
                "created_at": "2026-10-12T10:00:00",
                "checklist": {"smoke": {"ok": True, "detail": "ok"}, "idempotency": {"ok": False, "detail": "none"}},
            }  # fmt: skip
            self.promotions[BUSINESS].append(promo)
            return httpx.Response(200, json=promo)
        if request.method == "POST" and path.startswith("/promotions/") and path.endswith("/rollback"):
            self.posts.append((path, json.loads(request.content)))
            for p in self.promotions[BUSINESS]:
                if p["id"] == path.split("/")[2]:
                    p.update(status="rolled_back", rolled_back_by=json.loads(request.content)["actor"])
            return httpx.Response(200, json={})
        if request.method == "GET" and path == f"/businesses/{BUSINESS}/proposals":
            view = {"proposal_id": "prop-2", "id": None, "version": 1, "state": "ready_for_review",
                    "content": {"name": "Open a ticket", "questions": []}}  # fmt: skip
            return httpx.Response(200, json=[view])
        return httpx.Response(404, json={"code": "not_found"})


@pytest.fixture
def rest() -> TeamCRest:
    return TeamCRest()


def build(tmp_path: Path, fake: Fake, rest: TeamCRest, **options: Any) -> Container:
    config = fake.config()
    config.update(api_url="http://127.0.0.1:9", builder_url="http://127.0.0.1:9")
    path = tmp_path / "team_c.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    container = build_container(make_settings(tmp_path, fixed_today=FIXED_TODAY, team_c_config=path, **options))
    assert container.team_c is not None
    container.team_c.admin = TeamCAdmin("http://127.0.0.1:9", transport=httpx.MockTransport(rest.handle))
    return container


async def serve(container: Container) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(container=container)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


@pytest.fixture
async def open_client(tmp_path: Path, rest: TeamCRest) -> AsyncIterator[tuple[httpx.AsyncClient, Fake, TeamCRest]]:
    fake = Fake(tmp_path)
    fake.publish(tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL), get_order_v1_aa=ORDER_OK)
    rest.add("pub-get_order_v1_aa", BUSINESS, "get_order_v1_aa")
    async for http in serve(build(tmp_path, fake, rest)):
        yield http, fake, rest


async def test_the_page_shows_which_tools_team_c_serves_and_its_test_only_publications(
    open_client: tuple[httpx.AsyncClient, Fake, TeamCRest],
) -> None:
    http, _, _ = open_client
    reply = await http.get(URL, params={"tenant_id": T})
    assert reply.status_code == 200, reply.text
    body = reply.json()
    assert body["configured"] and body["sandbox_only"] and body["business_id"] == BUSINESS
    tools = {t["name"]: t for t in body["tools"]}
    assert tools["get_order"]["source"] == "team_c" and tools["get_order"]["status"] == "available"
    assert tools["get_order"]["team_c_tool"] == "get_order_v1_aa"
    assert tools["verify_customer"]["source"] == "standin"
    assert tools["apply_voucher"] == tools["apply_voucher"] | {
        "source": "team_c",
        "status": "missing",
        "reason": "not_published",
    }
    [pub] = body["publications"]
    assert pub["environment"] == "sandbox" and pub["production_ready"] is False and pub["bound_to"] == "get_order"
    assert "evidence" not in pub  # only what the page needs
    [proposal] = body["proposals"]
    assert proposal == proposal | {"id": "prop-2", "name": "Open a ticket", "version": 1, "state": "ready_for_review"}
    assert proposal["artifact_id"] == "art-1" and proposal["readiness_state"] == "needs_owner"
    assert proposal["auto_test_status"] == "succeeded" and proposal["auto_test_question"] == "Add one more test account"
    assert body["builder_url"] == f"http://127.0.0.1:9/businesses/{BUSINESS}"


async def test_disable_stops_the_tool_and_the_page_shows_the_new_state(
    open_client: tuple[httpx.AsyncClient, Fake, TeamCRest],
) -> None:
    http, fake, rest = open_client
    await http.get(URL, params={"tenant_id": T})
    fake.publish()  # what Team C's MCP server lists once the publication is disabled
    reply = await http.post(
        f"{URL}/publications/pub-get_order_v1_aa/disable", params={"tenant_id": T}, json={"note": "stop it"}
    )
    assert reply.status_code == 200, reply.text
    assert rest.posts == [
        ("/publications/pub-get_order_v1_aa/disable", {"note": "Disabled in Ticket-Easy by an operator: stop it"})
    ]
    body = reply.json()
    assert {t["name"]: t["status"] for t in body["tools"]}["get_order"] == "missing"  # listed again at once
    assert body["publications"][0]["status"] == "disabled"


async def test_another_businesss_publication_cannot_be_disabled_from_here(
    open_client: tuple[httpx.AsyncClient, Fake, TeamCRest],
) -> None:
    http, _, rest = open_client
    rest.add("pub-other", "biz-someone-else", "their_tool_v1_ff")
    for pub in ("pub-other", "pub-does-not-exist"):
        reply = await http.post(f"{URL}/publications/{pub}/disable", params={"tenant_id": T}, json={"note": "nope"})
        assert reply.status_code == 404
    assert rest.posts == [] and rest.publications["pub-other"]["status"] == "published"


async def test_a_business_without_team_c_shows_the_stand_in_tools(tmp_path: Path) -> None:
    async for http in serve(build_container(make_settings(tmp_path, fixed_today=FIXED_TODAY))):
        body = (await http.get(URL, params={"tenant_id": T})).json()
        assert body["configured"] is False and {t["source"] for t in body["tools"]} == {"standin"}
        reply = await http.post(f"{URL}/publications/p1/disable", params={"tenant_id": T}, json={"note": "x" * 5})
        assert reply.status_code == 404


async def test_only_a_signed_in_manager_may_see_refresh_or_disable(tmp_path: Path, rest: TeamCRest) -> None:
    config = tmp_path / "tenants"
    shutil.copytree(PROJECT_ROOT / "config" / "tenants", config)
    fake = Fake(tmp_path)
    fake.publish(tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL), get_order_v1_aa=ORDER_OK)
    rest.add("pub-get_order_v1_aa", BUSINESS, "get_order_v1_aa")
    container = build(tmp_path, fake, rest, auth_required=True, secret_key="k" * 32, config_dir=config)
    assert container.auth is not None
    await container.auth.create_user("agent@example.com", "Agent", "agent", (T,), PASSWORD)
    await container.auth.create_user("mona@example.com", "Mona", "manager", (T,), PASSWORD)
    async for http in serve(container):
        assert (await http.get(URL, params={"tenant_id": T})).status_code == 401
        login = await http.post("/v1/auth/login", json={"email": "agent@example.com", "password": PASSWORD})
        csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
        assert (await http.get(URL, params={"tenant_id": T})).status_code == 403
        disable = f"{URL}/publications/pub-get_order_v1_aa/disable"
        reply = await http.post(disable, params={"tenant_id": T}, json={"note": "stop it"}, headers=csrf)
        assert reply.status_code == 403 and rest.posts == []
        await http.post("/v1/auth/logout", headers=csrf)
        login = await http.post("/v1/auth/login", json={"email": "mona@example.com", "password": PASSWORD})
        csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
        assert (await http.get(URL, params={"tenant_id": T})).status_code == 200
        assert (await http.post(disable, params={"tenant_id": T}, json={"note": "stop it"})).status_code == 403  # CSRF
        assert (await http.post(f"{URL}/refresh", params={"tenant_id": T}, headers=csrf)).status_code == 200
        reply = await http.post(disable, params={"tenant_id": T}, json={"note": "stop it"}, headers=csrf)
        assert reply.status_code == 200
        assert rest.posts == [
            ("/publications/pub-get_order_v1_aa/disable", {"note": "Disabled in Ticket-Easy by Mona: stop it"})
        ]
        await http.post("/v1/auth/logout", headers=csrf)
        login = await http.post("/v1/auth/login", json={"email": "agent@example.com", "password": PASSWORD})
        agent_csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
        check = await http.post(f"{URL}/connectors/live-abc/check", params={"tenant_id": T}, headers=agent_csrf)
        assert check.status_code == 403  # an agent may not make Team C call the API


async def test_a_manager_checks_this_businesss_connector_and_sees_each_step(
    open_client: tuple[httpx.AsyncClient, Fake, TeamCRest],
) -> None:
    http, _, rest = open_client
    body = (await http.get(URL, params={"tenant_id": T})).json()
    assert body["connectors"] == [{"id": "live-abc", "environment": "live_demo", "host": "api.example.com:443",
                                   "identities": 1, "checked_at": None, "ready": None, "checks": []}]  # fmt: skip
    reply = await http.post(f"{URL}/connectors/live-abc/check", params={"tenant_id": T})
    assert reply.status_code == 200, reply.text
    [connector] = reply.json()["connectors"]
    assert connector["ready"] is False and [c["status"] for c in connector["checks"]] == ["ok", "ok", "unknown"]
    assert rest.posts == [("/connectors/live-abc/check", None)]
    other = await http.post(f"{URL}/connectors/live-other/check", params={"tenant_id": T})
    assert other.status_code == 404 and len(rest.posts) == 1  # another business's connector is never checked from here


async def test_a_live_demo_tool_is_labelled_as_calling_a_real_api(tmp_path: Path, rest: TeamCRest) -> None:
    fake = Fake(tmp_path)
    fake.publish(tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL, live=True), get_order_v1_aa=ORDER_OK)
    rest.add("pub-get_order_v1_aa", BUSINESS, "get_order_v1_aa")
    config = fake.config()
    config["tenants"][T]["tools"]["get_order"]["environments"] = ["sandbox", "live_demo"]
    config.update(api_url="http://127.0.0.1:9")
    path = tmp_path / "team_c.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    container = build_container(make_settings(tmp_path, fixed_today=FIXED_TODAY, team_c_config=path))
    assert container.team_c is not None
    container.team_c.admin = TeamCAdmin("http://127.0.0.1:9", transport=httpx.MockTransport(rest.handle))
    async for http in serve(container):
        body = (await http.get(URL, params={"tenant_id": T})).json()
        tool = {t["name"]: t for t in body["tools"]}["get_order"]
        assert (
            tool["environment"] == "live_demo"
            and tool["api_host"] == "api.example.com"
            and tool["status"] == "available"
        )
        assert body["live_tools"] == 1 and body["sandbox_only"] is False


async def test_a_manager_starts_automatic_tests_for_this_businesss_tool_only(
    open_client: tuple[httpx.AsyncClient, Fake, TeamCRest],
) -> None:
    http, _, rest = open_client
    reply = await http.post(f"{URL}/artifacts/art-1/auto-test", params={"tenant_id": T})
    assert reply.status_code == 200, reply.text
    assert rest.posts == [
        ("/artifacts/art-1/auto-test", {})
    ]  # never asks to publish: Team C's business setting decides
    [proposal] = reply.json()["proposals"]
    assert proposal["readiness_state"] == "testing" and proposal["auto_test_status"] == "running"
    for other in ("art-other", "art-missing"):
        assert (await http.post(f"{URL}/artifacts/{other}/auto-test", params={"tenant_id": T})).status_code == 404
    assert len(rest.posts) == 1


async def test_only_a_manager_may_start_automatic_tests(tmp_path: Path, rest: TeamCRest) -> None:
    config = tmp_path / "tenants"
    shutil.copytree(PROJECT_ROOT / "config" / "tenants", config)
    fake = Fake(tmp_path)
    fake.publish(tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL), get_order_v1_aa=ORDER_OK)
    container = build(tmp_path, fake, rest, auth_required=True, secret_key="k" * 32, config_dir=config)
    assert container.auth is not None
    await container.auth.create_user("agent@example.com", "Agent", "agent", (T,), PASSWORD)
    await container.auth.create_user("mona@example.com", "Mona", "manager", (T,), PASSWORD)
    async for http in serve(container):
        url = f"{URL}/artifacts/art-1/auto-test"
        assert (await http.post(url, params={"tenant_id": T})).status_code == 401
        login = await http.post("/v1/auth/login", json={"email": "agent@example.com", "password": PASSWORD})
        assert (
            await http.post(url, params={"tenant_id": T}, headers={"X-CSRF-Token": login.json()["csrf_token"]})
        ).status_code == 403
        await http.post("/v1/auth/logout", headers={"X-CSRF-Token": login.json()["csrf_token"]})
        login = await http.post("/v1/auth/login", json={"email": "mona@example.com", "password": PASSWORD})
        assert (
            await http.post(url, params={"tenant_id": T}, headers={"X-CSRF-Token": login.json()["csrf_token"]})
        ).status_code == 200
        assert len(rest.posts) == 1


async def test_a_signed_in_admin_promotes_a_tested_tool_and_the_decision_carries_their_name(
    tmp_path: Path, rest: TeamCRest
) -> None:
    config = tmp_path / "tenants"
    shutil.copytree(PROJECT_ROOT / "config" / "tenants", config)
    fake = Fake(tmp_path)
    fake.publish(tool_def("get_order_v1_aa", GET_ORDER_PROPOSAL), get_order_v1_aa=ORDER_OK)
    rest.connectors[BUSINESS].append(
        {"id": "prod-1", "environment": "production", "host": "api.example.com:443", "identities": []}
    )
    rest.artifacts[BUSINESS][0]["readiness_state"] = "published"
    container = build(tmp_path, fake, rest, auth_required=True, secret_key="k" * 32, config_dir=config)
    assert container.auth is not None
    await container.auth.create_user("mona@example.com", "Mona", "manager", (T,), PASSWORD)
    await container.auth.create_user("ali@example.com", "Ali Admin", "admin", (T,), PASSWORD)
    body = {"note": "sandbox-only go-live", "rollback_contact": "oncall@example.com", "accept_no_idempotency": True}
    url = f"{URL}/artifacts/art-1/promote"
    async for http in serve(container):
        login = await http.post("/v1/auth/login", json={"email": "mona@example.com", "password": PASSWORD})
        manager = {"X-CSRF-Token": login.json()["csrf_token"]}
        assert (
            await http.post(url, params={"tenant_id": T}, json=body, headers=manager)
        ).status_code == 403  # a manager may not
        assert rest.posts == []
        await http.post("/v1/auth/logout", headers=manager)
        login = await http.post("/v1/auth/login", json={"email": "ali@example.com", "password": PASSWORD})
        admin = {"X-CSRF-Token": login.json()["csrf_token"]}
        page = (await http.get(URL, params={"tenant_id": T})).json()
        assert page["production_connector"] is True and page["proposals"][0]["promotable"] is True
        rest.refuse = "This tool cannot be promoted yet: smoke: PRODUCTION_SMOKE_CONTEXT is not configured"
        refused = await http.post(url, params={"tenant_id": T}, json=body, headers=admin)
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "PROMOTION_REFUSED"
        assert "PRODUCTION_SMOKE_CONTEXT is not configured" in refused.json()["error"]["message"]  # Team C's own words
        rest.refuse = None
        done = await http.post(url, params={"tenant_id": T}, json=body, headers=admin)
        assert done.status_code == 200, done.text
        assert rest.posts[-1] == (
            "/artifacts/art-1/promotions",
            {
                "actor": "Ali Admin",
                "note": "sandbox-only go-live",
                "rollback_contact": "oncall@example.com",
                "accept_no_idempotency": True,
            },
        )
        shown = done.json()
        assert [p["actor"] for p in shown["promotions"]] == ["Ali Admin"] and shown["proposals"][0]["promoted"] is True
        assert {c["name"]: c["ok"] for c in shown["promotions"][0]["checklist"]} == {
            "smoke": True,
            "idempotency": False,
        }
        # another business's tool is answered as missing, and so is rolling back another business's promotion
        assert (
            await http.post(f"{URL}/artifacts/art-other/promote", params={"tenant_id": T}, json=body, headers=admin)
        ).status_code == 404
        assert (
            await http.post(
                f"{URL}/promotions/promo-other/rollback", params={"tenant_id": T}, json={"note": "nope"}, headers=admin
            )
        ).status_code == 404
        back = await http.post(
            f"{URL}/promotions/promo-1/rollback", params={"tenant_id": T}, json={"note": "sandbox-only"}, headers=admin
        )
        assert back.status_code == 200 and rest.posts[-1] == (
            "/promotions/promo-1/rollback",
            {"actor": "Ali Admin", "note": "sandbox-only"},
        )
        assert back.json()["promotions"][0]["status"] == "rolled_back"


async def test_an_open_dashboard_without_sign_in_cannot_promote(
    open_client: tuple[httpx.AsyncClient, Fake, TeamCRest],
) -> None:
    http, _, rest = open_client
    body = {"note": "sandbox-only go-live", "rollback_contact": "oncall@example.com"}
    # sign-in switched off: nobody can be named as the person who decided, so production decisions are refused
    refused = await http.post(f"{URL}/artifacts/art-1/promote", params={"tenant_id": T}, json=body)
    assert refused.status_code == 403 and rest.posts == []
