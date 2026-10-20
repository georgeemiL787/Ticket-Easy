"""Tenant bundles over HTTP: roles, the draft/publish/activate/rollback life cycle, and that activation takes effect on
the next customer turn without a restart."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from agent_service.api.app import create_app
from agent_service.container import Container, build_container
from tests.conftest import FIXED_TODAY
from tests.support import make_settings

T = "shop_001"
BASE = f"/v1/admin/tenants/{T}/bundles"
PASSWORD = "correct horse battery"


@pytest.fixture
def secured(tmp_path: Path) -> Container:
    return build_container(make_settings(tmp_path, fixed_today=FIXED_TODAY, auth_required=True, secret_key="k" * 32))


@pytest.fixture
async def web(secured: Container) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(container=secured)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


async def sign_in(
    secured: Container, web: httpx.AsyncClient, role: Any, tenants: tuple[str, ...] = ()
) -> dict[str, str]:
    assert secured.auth is not None
    email = f"{role}@example.com"
    await secured.auth.create_user(email, role.title(), role, tenants, PASSWORD)
    reply = await web.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert reply.status_code == 200, reply.text
    return {"X-CSRF-Token": reply.json()["csrf_token"]}


async def test_managers_read_but_only_admins_change(secured: Container, web: httpx.AsyncClient) -> None:
    await sign_in(secured, web, "manager", (T,))
    assert (await web.get(BASE)).status_code == 200
    headers = {"X-CSRF-Token": (await web.get("/v1/auth/me")).json()["csrf_token"]}
    assert (await web.post(BASE, json={"from_files": True}, headers=headers)).status_code == 403


async def test_a_manager_of_another_business_does_not_see_this_one(secured: Container, web: httpx.AsyncClient) -> None:
    await sign_in(secured, web, "manager", ("shop_002",))
    reply = await web.get(BASE)
    assert reply.status_code == 404 and reply.json()["error"]["code"] == "TENANT_NOT_FOUND"


async def test_the_whole_life_cycle_and_the_next_turn_uses_the_active_bundle(
    secured: Container, web: httpx.AsyncClient
) -> None:
    headers = await sign_in(secured, web, "admin")
    assert (await web.get(f"{BASE}/active")).status_code == 404  # still on the file

    created = await web.post(BASE, json={"from_files": True}, headers=headers)
    assert created.status_code == 201 and created.json()["version"] == 1 and created.json()["status"] == "draft"
    assert (await web.post(f"{BASE}/1/activate", headers=headers)).status_code == 409  # a draft cannot go live
    assert (await web.post(f"{BASE}/1/publish", headers=headers)).json()["status"] == "published"
    assert (await web.post(f"{BASE}/1/publish", headers=headers)).status_code == 409
    activated = await web.post(f"{BASE}/1/activate", headers=headers)
    assert activated.status_code == 200 and activated.json()["status"] == "active"

    changed = await web.post(
        BASE, json={"profile": {"display_name": "Nile Style Cairo", "tone": "formal"}}, headers=headers
    )
    assert changed.json()["version"] == 2 and changed.json()["parent_version"] == 1
    await web.post(f"{BASE}/2/publish", headers=headers)
    assert (await web.get(f"/v1/tenants/{T}/welcome")).json()["display_name"] == "Nile Style"  # a draft changes nothing
    await web.post(f"{BASE}/2/activate", headers=headers)
    assert (await web.get(f"/v1/tenants/{T}/welcome")).json()["display_name"] == "Nile Style Cairo"  # no restart

    listing = (await web.get(BASE)).json()
    assert listing["active_version"] == 2
    assert [(b["version"], b["status"]) for b in listing["bundles"]] == [(2, "active"), (1, "published")]

    back = await web.post(f"{BASE}/rollback", headers=headers)
    assert back.status_code == 200 and back.json()["version"] == 1
    assert (await web.get(f"/v1/tenants/{T}/welcome")).json()["display_name"] == "Nile Style"
    assert (await web.post(f"{BASE}/rollback", headers=headers)).status_code == 409  # version 1 has no parent


async def test_a_traces_names_the_bundle_that_answered(secured: Container, web: httpx.AsyncClient) -> None:
    headers = await sign_in(secured, web, "admin")
    await web.post(BASE, json={"from_files": True}, headers=headers)
    await web.post(f"{BASE}/1/publish", headers=headers)
    await web.post(f"{BASE}/1/activate", headers=headers)
    reply = await web.post("/v1/conversations/c1/messages", json={"tenant_id": T, "text": "hello"})
    assert reply.status_code == 200
    traces = await secured.traces.query(T, limit=5)
    assert traces and traces[0].versions["bundle_version"] == "1"
    active = (await web.get(f"{BASE}/active")).json()
    assert traces[0].versions["tenant_config_hash"] == active["content_sha256"][:12]


async def test_bad_requests_are_refused_cleanly(secured: Container, web: httpx.AsyncClient) -> None:
    headers = await sign_in(secured, web, "admin")
    assert (await web.get(f"{BASE}/5")).status_code == 404
    assert (await web.get(BASE.replace(T, "Bad-Id"))).status_code == 422
    assert (await web.post(BASE, json={"surprise": 1}, headers=headers)).status_code == 422
    assert (await web.post("/v1/admin/tenants/brand_new/bundles", json={}, headers=headers)).status_code == 409
