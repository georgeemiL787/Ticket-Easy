"""A16: accounts, roles and tenant isolation. SAFETY-CRITICAL (access to customer data).

The central test walks the real route table: a route that is added later and forgotten here is protected by default,
and this test fails if it is not."""

import re
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from team_b.adapters.user_repository import InMemoryUserStore
from team_b.api.app import create_app
from team_b.api.auth import is_public, least_role, parse_chat_keys
from team_b.auth import COOKIE_NAME, MAX_FAILURES, AuthService, TooManyAttemptsError, hash_password, password_matches
from team_b.container import Container, build_container
from team_b.domain.users import User
from tests.conftest import FIXED_TODAY
from tests.support import make_settings

PASSWORD = "correct horse battery"
OTHER = "shop_002"
BASE = "/v1/handoff/cases"


def fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x1", path)


@pytest.fixture
def two_tenants(tmp_path: Path) -> dict[str, Any]:
    """A copy of the real configuration with a second business, so isolation can be tested."""
    config = tmp_path / "tenants"
    shutil.copytree(Path(__file__).parents[3] / "config" / "tenants", config)
    text = (config / "shop_001.json").read_text(encoding="utf-8").replace("shop_001", OTHER)
    (config / f"{OTHER}.json").write_text(text, encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    shutil.copytree(Path(__file__).parents[3] / "fixtures", fixtures)
    shutil.copytree(fixtures / "shop_001", fixtures / OTHER)
    return {"config_dir": config, "fixtures_dir": fixtures}


@pytest.fixture
def secured(tmp_path: Path, two_tenants: dict[str, Any]) -> Container:
    settings = make_settings(
        tmp_path, fixed_today=FIXED_TODAY, rate_limit_per_minute=1000, auth_required=True, secret_key="k" * 32,
        **two_tenants,
    )  # fmt: skip
    return build_container(settings)


@pytest.fixture
async def client(secured: Container) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(container=secured)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


async def person(secured: Container, email: str, role: Any, tenants: tuple[str, ...], name: str | None = None) -> User:
    assert secured.auth is not None
    return await secured.auth.create_user(email, name or email.split("@")[0].title(), role, tenants, PASSWORD)


async def sign_in(secured: Container, app_client: httpx.AsyncClient, email: str) -> dict[str, str]:
    """Log in as the person and return the headers every state-changing call needs."""
    reply = await app_client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert reply.status_code == 200, reply.text
    return {"X-CSRF-Token": reply.json()["csrf_token"]}


async def hand_off(app_client: httpx.AsyncClient, tenant: str = "shop_001", conversation: str = "c1") -> str:
    reply = await app_client.post(
        f"/v1/conversations/{conversation}/messages", json={"tenant_id": tenant, "text": "I want to talk to a human"}
    )
    assert reply.status_code == 200, reply.text
    case_id: str = reply.json()["handoff_case_id"]
    return case_id


# ---- every protected route ----


async def test_every_route_is_closed_without_a_login_unless_it_is_on_the_public_list(client: httpx.AsyncClient) -> None:
    app: FastAPI = client._transport.app  # type: ignore[attr-defined]
    checked = 0
    for path, methods in app.openapi()["paths"].items():
        for method in methods:
            if method.upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            if is_public(method.upper(), fill(path)):
                continue
            reply = await client.request(method.upper(), fill(path), params={"tenant_id": "shop_001"}, json={})
            assert reply.status_code == 401, f"{method.upper()} {path} answered {reply.status_code} without a login"
            assert reply.json()["error"]["code"] == "UNAUTHENTICATED"
            checked += 1
    assert checked >= 20  # inbox, dashboard, traces, capabilities, alerts


async def test_the_public_list_is_exactly_the_customer_chat_the_pages_and_the_login(client: httpx.AsyncClient) -> None:
    app: FastAPI = client._transport.app  # type: ignore[attr-defined]
    public = sorted(
        f"{m.upper()} {p}"
        for p, methods in app.openapi()["paths"].items()
        for m in methods
        if is_public(m.upper(), fill(p))
    )
    assert public == sorted(
        [
            "GET /health",
            "POST /v1/conversations/{conversation_id}/messages",
            "GET /v1/conversations/{conversation_id}/outbox",
            "GET /v1/conversations/{conversation_id}/events",
            "GET /v1/tenants/{tenant_id}/welcome",
            "POST /v1/auth/login",
            "POST /v1/auth/logout",
            "GET /v1/auth/me",
        ]
    )


async def test_an_unknown_path_does_not_reveal_anything_without_a_login(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/handoff/cases/../../etc")).status_code in (401, 404)
    assert (await client.get("/v1/_test/state")).status_code == 401  # the test admin is off and not public


async def test_a_forged_or_tampered_cookie_is_not_a_login(secured: Container, client: httpx.AsyncClient) -> None:
    await person(secured, "m@example.com", "manager", ("shop_001",))
    await sign_in(secured, client, "m@example.com")
    good = client.cookies[COOKIE_NAME]
    client.cookies.clear()
    for bad in ("", "x.y", good[:-3] + "AAA", good.split(".")[0] + ".", "a.b.c"):
        client.cookies.set(COOKIE_NAME, bad)
        assert (await client.get(BASE, params={"tenant_id": "shop_001"})).status_code == 401
    client.cookies.set(COOKIE_NAME, good)
    assert (await client.get(BASE, params={"tenant_id": "shop_001"})).status_code == 200


# ---- tenant isolation ----


async def test_a_manager_of_one_business_cannot_read_another(secured: Container, client: httpx.AsyncClient) -> None:
    await person(secured, "m@example.com", "manager", ("shop_001",))
    await sign_in(secured, client, "m@example.com")
    await hand_off(client, OTHER, "other-1")
    other_case = (await secured.cases.list(OTHER))[0].case_id

    own = await client.get("/v1/dashboard/overview", params={"tenant_id": "shop_001"})
    assert own.status_code == 200
    reads = [
        ("/v1/dashboard/overview", {"tenant_id": OTHER}),
        ("/v1/dashboard/conversations", {"tenant_id": OTHER}),
        ("/v1/dashboard/queue", {"tenant_id": OTHER}),
        ("/v1/dashboard/alerts", {"tenant_id": OTHER}),
        ("/v1/capabilities", {"tenant_id": OTHER}),
        (BASE, {"tenant_id": OTHER}),
        ("/v1/conversations/other-1", {"tenant_id": OTHER}),
        ("/v1/conversations/other-1/traces", {"tenant_id": OTHER}),
    ]
    for path, params in reads:
        reply = await client.get(path, params=params)
        assert reply.status_code == 404, f"{path} showed {OTHER} to a manager of shop_001"
        assert reply.json()["error"]["code"] == "TENANT_NOT_FOUND"
    assert (await client.get(f"{BASE}/{other_case}")).status_code == 404  # the case id alone does not open it
    tenants = await client.get("/v1/dashboard/tenants")
    assert [t["tenant_id"] for t in tenants.json()["tenants"]] == ["shop_001"]


async def test_acting_on_another_business_case_is_refused(secured: Container, client: httpx.AsyncClient) -> None:
    await person(secured, "m@example.com", "manager", ("shop_001",))
    headers = await sign_in(secured, client, "m@example.com")
    await hand_off(client, OTHER, "other-1")
    other_case = (await secured.cases.list(OTHER))[0].case_id
    for action in ("claim", "release", "resolve", "return-to-agent"):
        reply = await client.post(f"{BASE}/{other_case}/{action}", json={}, headers=headers)
        assert reply.status_code == 404, action
    after = (await secured.cases.list(OTHER))[0]
    assert after.claimed_by is None and after.status.value == "open"


async def test_an_admin_sees_every_business(secured: Container, client: httpx.AsyncClient) -> None:
    await person(secured, "a@example.com", "admin", ())
    await sign_in(secured, client, "a@example.com")
    for tenant in ("shop_001", OTHER):
        assert (await client.get("/v1/dashboard/overview", params={"tenant_id": tenant})).status_code == 200
    tenants = await client.get("/v1/dashboard/tenants")
    assert sorted(t["tenant_id"] for t in tenants.json()["tenants"]) == ["shop_001", OTHER]


# ---- roles ----


async def test_an_agent_cannot_reassign_or_decide_or_see_the_dashboard(
    secured: Container, client: httpx.AsyncClient
) -> None:
    await person(secured, "a@example.com", "agent", ("shop_001",), name="Ann")
    await person(secured, "b@example.com", "agent", ("shop_001",), name="Bob")
    headers = await sign_in(secured, client, "a@example.com")
    case_id = await hand_off(client)
    assert (await client.post(f"{BASE}/{case_id}/claim", json={}, headers=headers)).status_code == 200
    assign = await client.post(f"{BASE}/{case_id}/assign", json={"assignee": "Bob"}, headers=headers)
    assert assign.status_code == 403
    decision = await client.post(f"{BASE}/{case_id}/decision", json={"approve": True}, headers=headers)
    assert decision.status_code == 403
    for path in ("/v1/dashboard/overview", "/v1/capabilities", "/v1/dashboard/alerts"):
        assert (await client.get(path, params={"tenant_id": "shop_001"})).status_code == 403
    case = (await secured.cases.list("shop_001"))[0]
    assert case.claimed_by == "Ann"  # nothing moved


async def test_a_manager_can_assign_and_the_actor_is_the_signed_in_person(
    secured: Container, client: httpx.AsyncClient
) -> None:
    await person(secured, "m@example.com", "manager", ("shop_001",), name="Mona")
    await person(secured, "b@example.com", "agent", ("shop_001",), name="Bob")
    headers = await sign_in(secured, client, "m@example.com")
    case_id = await hand_off(client)
    claim = await client.post(f"{BASE}/{case_id}/claim", json={"agent": "someone else"}, headers=headers)
    assert claim.status_code == 200
    assert claim.json()["claimed_by"] == "Mona"  # the name in the body is ignored
    assign = await client.post(f"{BASE}/{case_id}/assign", json={"assignee": "Bob"}, headers=headers)
    assert assign.status_code == 200 and assign.json()["claimed_by"] == "Bob"
    nobody = await client.post(f"{BASE}/{case_id}/assign", json={"assignee": "Nobody"}, headers=headers)
    assert nobody.status_code in (404, 422)


def test_the_role_table_is_deny_by_default_for_unlisted_routes() -> None:
    assert least_role("POST", "/v1/something/new") == "admin"
    assert least_role("GET", "/v1/handoff/cases") == "agent"
    assert least_role("POST", "/v1/handoff/cases/x/assign") == "manager"


# ---- the cookie, the CSRF token, the login ----


async def test_login_sets_a_signed_http_only_same_site_cookie(secured: Container, client: httpx.AsyncClient) -> None:
    await person(secured, "m@example.com", "manager", ("shop_001",))
    reply = await client.post("/v1/auth/login", json={"email": "M@Example.com", "password": PASSWORD})
    assert reply.status_code == 200
    cookie = reply.headers["set-cookie"].lower()
    assert COOKIE_NAME in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "password" not in reply.text.lower()
    me = await client.get("/v1/auth/me")
    assert me.json()["user"]["email"] == "m@example.com" and me.json()["csrf_token"]


async def test_wrong_password_unknown_email_and_switched_off_account_all_look_the_same(
    secured: Container, client: httpx.AsyncClient
) -> None:
    user = await person(secured, "m@example.com", "manager", ("shop_001",))
    await secured.users.set_active(user.user_id, False)  # type: ignore[union-attr]
    answers = []
    for email, password in (("m@example.com", PASSWORD), ("m@example.com", "wrong"), ("nobody@example.com", PASSWORD)):
        reply = await client.post("/v1/auth/login", json={"email": email, "password": password})
        answers.append((reply.status_code, reply.json()["error"]["code"], reply.json()["error"]["message"]))
    assert len(set(answers)) == 1 and answers[0][0] == 401


async def test_a_switched_off_account_is_signed_out_at_once(secured: Container, client: httpx.AsyncClient) -> None:
    user = await person(secured, "m@example.com", "manager", ("shop_001",))
    await sign_in(secured, client, "m@example.com")
    assert (await client.get(BASE, params={"tenant_id": "shop_001"})).status_code == 200
    await secured.users.set_active(user.user_id, False)  # type: ignore[union-attr]
    assert (await client.get(BASE, params={"tenant_id": "shop_001"})).status_code == 401


async def test_logout_clears_the_cookie(secured: Container, client: httpx.AsyncClient) -> None:
    await person(secured, "m@example.com", "manager", ("shop_001",))
    await sign_in(secured, client, "m@example.com")
    assert (await client.post("/v1/auth/logout")).status_code == 200
    client.cookies.clear()
    assert (await client.get(BASE, params={"tenant_id": "shop_001"})).status_code == 401


async def test_a_change_needs_the_csrf_token_and_a_same_site_origin(
    secured: Container, client: httpx.AsyncClient
) -> None:
    await person(secured, "m@example.com", "manager", ("shop_001",))
    headers = await sign_in(secured, client, "m@example.com")
    case_id = await hand_off(client)
    url = f"{BASE}/{case_id}/claim"
    assert (await client.post(url, json={})).status_code == 403  # no token
    assert (await client.post(url, json={}, headers={"X-CSRF-Token": "wrong"})).status_code == 403
    evil = {**headers, "Origin": "https://evil.example"}
    assert (await client.post(url, json={}, headers=evil)).status_code == 403
    assert (await client.post(url, json={}, headers={**headers, "Origin": "null"})).status_code == 403
    ok = {**headers, "Origin": "http://test"}
    assert (await client.post(url, json={}, headers=ok)).status_code == 200


async def test_too_many_wrong_passwords_are_refused_for_a_while(secured: Container, client: httpx.AsyncClient) -> None:
    await person(secured, "m@example.com", "manager", ("shop_001",))
    for _ in range(MAX_FAILURES):
        assert (
            await client.post("/v1/auth/login", json={"email": "m@example.com", "password": "no"})
        ).status_code == 401
    blocked = await client.post("/v1/auth/login", json={"email": "m@example.com", "password": PASSWORD})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) > 0


# ---- the public chat ----


async def test_the_chat_stays_public_but_is_tenant_scoped(client: httpx.AsyncClient) -> None:
    reply = await client.post(
        "/v1/conversations/c9/messages", json={"tenant_id": "shop_001", "text": "What is your return policy?"}
    )
    assert reply.status_code == 200
    assert (await client.get("/v1/conversations/c9/outbox", params={"tenant_id": "shop_001"})).status_code == 200
    unknown = await client.post("/v1/conversations/c9/messages", json={"tenant_id": "nope", "text": "hi"})
    assert unknown.status_code == 404
    assert (await client.get("/v1/conversations/c9", params={"tenant_id": "shop_001"})).status_code == 401  # debug view


async def test_a_conversation_id_of_one_business_is_not_readable_through_another(
    secured: Container, client: httpx.AsyncClient
) -> None:
    await client.post("/v1/conversations/shared/messages", json={"tenant_id": "shop_001", "text": "hello"})
    other = await client.get("/v1/conversations/shared/outbox", params={"tenant_id": OTHER})
    assert other.status_code == 404  # nothing of shop_001's conversation shows through shop_002


async def test_a_business_with_an_api_key_needs_it_in_the_header(tmp_path: Path, two_tenants: dict[str, Any]) -> None:
    settings = make_settings(
        tmp_path, fixed_today=FIXED_TODAY, rate_limit_per_minute=1000, auth_required=True, secret_key="k" * 32,
        chat_api_keys=f"shop_001=sesame, {OTHER}=",
        **two_tenants,
    )  # fmt: skip
    app = create_app(container=build_container(settings))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            body = {"tenant_id": "shop_001", "text": "What is your return policy?"}
            assert (await http.post("/v1/conversations/k1/messages", json=body)).status_code == 401
            wrong = await http.post("/v1/conversations/k1/messages", json=body, headers={"X-Api-Key": "nope"})
            assert wrong.status_code == 401
            right = await http.post("/v1/conversations/k1/messages", json=body, headers={"X-Api-Key": "sesame"})
            assert right.status_code == 200
            free = {"tenant_id": OTHER, "text": "What is your return policy?"}  # no key set: stays open
            assert (await http.post("/v1/conversations/k2/messages", json=free)).status_code == 200


def test_chat_api_keys_are_parsed_leniently() -> None:
    assert parse_chat_keys("a=1, b = 2,,c=,=d, e") == {"a": "1", "b": "2"}
    assert parse_chat_keys("") == {}


# ---- passwords and the user store ----


def test_passwords_are_stored_as_argon2_hashes_and_checked() -> None:
    hashed = hash_password(PASSWORD)
    assert hashed.startswith("$argon2") and PASSWORD not in hashed
    assert password_matches(PASSWORD, hashed) and not password_matches("other password!", hashed)
    assert not password_matches(PASSWORD, "not a hash")
    for bad in ("short", "x" * 201):
        with pytest.raises(ValueError):
            hash_password(bad)


async def test_emails_and_display_names_are_unique_without_regard_to_case() -> None:
    store = InMemoryUserStore()
    auth = AuthService(store, b"k" * 32)
    await auth.create_user("a@example.com", "Ann", "agent", ("shop_001",), PASSWORD)
    from team_b.ports import AlreadyExistsError

    with pytest.raises(AlreadyExistsError):
        await auth.create_user("A@EXAMPLE.COM", "Other", "agent", (), PASSWORD)
    with pytest.raises(AlreadyExistsError):
        await auth.create_user("b@example.com", "ann", "agent", (), PASSWORD)


async def test_a_session_expires_and_the_throttle_recovers() -> None:
    now = [1000.0]
    store = InMemoryUserStore()
    auth = AuthService(store, b"k" * 32, session_hours=1, clock=lambda: now[0])
    user = await auth.create_user("a@example.com", "Ann", "agent", ("shop_001",), PASSWORD)
    cookie, _ = auth.issue(user)
    assert auth.read(cookie) is not None
    now[0] += 3601
    assert auth.read(cookie) is None
    for _ in range(MAX_FAILURES):
        assert await auth.authenticate("a@example.com", "wrong password") is None
    with pytest.raises(TooManyAttemptsError):
        await auth.authenticate("a@example.com", PASSWORD)
    now[0] += 16 * 60
    assert await auth.authenticate("a@example.com", PASSWORD) is not None


def test_users_see_only_their_businesses_and_roles_rank() -> None:
    user = User(
        user_id="u",
        email="a@x.io",
        display_name="A",
        role="manager",
        tenants=("shop_001",),
        created_at=datetime.now(UTC),
    )
    assert user.can_see("shop_001") and not user.can_see("shop_002")
    assert user.at_least("agent") and user.at_least("manager") and not user.at_least("admin")
    admin = user.model_copy(update={"role": "admin", "tenants": ()})
    assert admin.can_see("anything") and admin.at_least("admin")


def test_the_inbox_page_signs_in_and_sends_the_csrf_token() -> None:
    source = (Path(__file__).parents[3] / "web" / "inbox" / "inbox.js").read_text(encoding="utf-8")
    assert "/v1/auth/me" in source and "/v1/auth/login" in source and "X-CSRF-Token" in source
