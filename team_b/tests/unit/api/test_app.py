import json
import re
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from team_b.api.app import app as module_app
from team_b.api.app import create_app
from team_b.api.errors import invalid_request, invalid_state, not_found, tenant_not_found, upstream_unavailable
from team_b.config import Settings
from team_b.container import Container, ContainerError, build_container
from team_b.contracts.errors import UpstreamError
from team_b.domain.tenant import UnknownTenantError
from team_b.ports import SessionConflictError


@pytest.fixture
def app(container: Container) -> FastAPI:
    """The real app plus a few routes that raise each kind of error."""
    app = create_app(container=container)

    @app.get("/_t/invalid")
    async def _invalid() -> None:
        raise invalid_request("bad thing")

    @app.get("/_t/not-found")
    async def _not_found() -> None:
        raise not_found("no such thing")

    @app.get("/_t/tenant")
    async def _tenant() -> None:
        raise tenant_not_found("zzz")

    @app.get("/_t/unknown-tenant")
    async def _unknown_tenant() -> None:
        raise UnknownTenantError("qqq")

    @app.get("/_t/state")
    async def _state() -> None:
        raise invalid_state("already closed")

    @app.get("/_t/conflict")
    async def _conflict() -> None:
        raise SessionConflictError("version 3 vs 4")

    @app.get("/_t/down")
    async def _down() -> None:
        raise upstream_unavailable("policy search is down")

    @app.get("/_t/upstream")
    async def _upstream() -> None:
        raise UpstreamError("shop", "TIMEOUT", "secret internal detail", retryable=True)

    @app.get("/_t/boom")
    async def _boom() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/_t/number")
    async def _number(n: int) -> dict[str, int]:
        return {"n": n}

    return app


async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "mode": "standin", "tenants": []}


async def test_health_lists_loaded_tenants(settings: Settings, tenants_dir: Path) -> None:
    tenant = {
        "tenant_id": "shop_001",
        "display_name": "Nile Style",
        "order_id_pattern": r"NS-\d+",
        "identity": {"verify_tool": "verify_customer"},
    }
    (tenants_dir / "shop_001.json").write_text(json.dumps(tenant), encoding="utf-8")
    app = create_app(container=build_container(settings))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            assert (await http.get("/health")).json()["tenants"] == ["shop_001"]


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/_t/invalid", 422, "INVALID_REQUEST"),
        ("/_t/not-found", 404, "NOT_FOUND"),
        ("/_t/tenant", 404, "TENANT_NOT_FOUND"),
        ("/_t/unknown-tenant", 404, "TENANT_NOT_FOUND"),
        ("/_t/state", 409, "INVALID_STATE"),
        ("/_t/conflict", 409, "INVALID_STATE"),
        ("/_t/down", 503, "UPSTREAM_UNAVAILABLE"),
        ("/_t/upstream", 503, "UPSTREAM_UNAVAILABLE"),
        ("/_t/number?n=abc", 422, "INVALID_REQUEST"),
        ("/_t/number", 422, "INVALID_REQUEST"),
        ("/nothing-here", 404, "NOT_FOUND"),
        ("/_t/boom", 500, "INTERNAL_ERROR"),
    ],
)
async def test_every_error_uses_the_same_envelope(client: httpx.AsyncClient, path: str, status: int, code: str) -> None:
    response = await client.get(path, headers={"X-Request-ID": "req-123"})
    assert response.status_code == status
    body = response.json()
    assert set(body) == {"schema_version", "error"}
    assert body["schema_version"] == "1.0"
    assert set(body["error"]) == {"code", "message", "request_id"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    assert body["error"]["request_id"] == "req-123"


async def test_wrong_method_is_an_invalid_request(client: httpx.AsyncClient) -> None:
    response = await client.post("/health")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


async def test_validation_error_names_the_field_but_not_the_value(client: httpx.AsyncClient) -> None:
    response = await client.get("/_t/number", params={"n": "01012345678abc"})
    message = response.json()["error"]["message"]
    assert "n" in message
    assert "01012345678abc" not in message


async def test_unexpected_errors_do_not_leak_internals(client: httpx.AsyncClient) -> None:
    response = await client.get("/_t/boom")
    assert "secret internal detail" not in response.text
    assert response.json()["error"]["message"] == "unexpected error"


async def test_upstream_errors_say_which_service_but_not_why(client: httpx.AsyncClient) -> None:
    response = await client.get("/_t/upstream")
    assert response.json()["error"]["message"] == "shop is unavailable"
    assert "secret internal detail" not in response.text


async def test_request_id_is_echoed_or_generated(client: httpx.AsyncClient) -> None:
    echoed = await client.get("/health", headers={"X-Request-ID": "abc-1"})
    assert echoed.headers["X-Request-ID"] == "abc-1"
    generated = await client.get("/health")
    assert re.fullmatch(r"[0-9a-f]{32}", generated.headers["X-Request-ID"])
    other = await client.get("/health")
    assert other.headers["X-Request-ID"] != generated.headers["X-Request-ID"]


async def test_generated_request_id_appears_in_error_bodies(client: httpx.AsyncClient) -> None:
    response = await client.get("/_t/state")
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


async def test_the_app_builds_its_own_container_at_startup(settings: Settings) -> None:
    app = create_app(settings=settings)
    async with app.router.lifespan_context(app):
        assert app.state.container.settings is settings
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
            assert (await http.get("/health")).json()["status"] == "ok"


async def test_startup_fails_loudly_when_the_configuration_cannot_be_built(tenants_dir: Path) -> None:
    app = create_app(settings=Settings(mode="live", config_dir=tenants_dir))
    with pytest.raises(ContainerError, match="Phase 6"):
        async with app.router.lifespan_context(app):
            pass


def test_the_module_level_app_exists_for_uvicorn() -> None:
    assert any(getattr(route, "path", "") == "/health" for route in module_app.routes)
