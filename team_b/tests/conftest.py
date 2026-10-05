import itertools
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
from fastapi import FastAPI

from team_b.adapters.standins.shop import StandinShop
from team_b.api.app import create_app
from team_b.config import Settings
from team_b.container import Container, build_container
from team_b.contracts.tools import ToolCallRequest, ToolResult

FIXED_TODAY = date(2026, 9, 28)  # the seeded demo data depends on this date


@pytest.fixture
def tenants_dir(tmp_path: Path) -> Path:
    """An empty tenant directory. Tests that need tenants write JSON files into it before building the container."""
    path = tmp_path / "tenants"
    path.mkdir()
    return path


@pytest.fixture
def settings(tenants_dir: Path) -> Settings:
    return Settings(mode="standin", store="memory", llm="none", fixed_today=FIXED_TODAY, config_dir=tenants_dir)


@pytest.fixture
def container(settings: Settings) -> Container:
    """Stand-in mode, memory stores, a clock fixed at 2026-09-28."""
    return build_container(settings)


@pytest.fixture
def app(container: Container) -> FastAPI:
    return create_app(container=container)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client talking to the app in-process, with the app lifespan running."""
    async with app.router.lifespan_context(app):
        # raise_app_exceptions=False: an unexpected error must come back as a 500 response, not as a test crash.
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


@pytest.fixture
def shop(container: Container) -> StandinShop:
    assert container.shop is not None
    return container.shop


@pytest.fixture
def call(shop: StandinShop) -> Callable[..., Awaitable[ToolResult]]:
    """call("get_order", {"order_id": "NS-20877"}): a call with fresh ids; policy=None omits the policy id."""
    numbers = itertools.count(1)

    async def _call(
        tool: str,
        arguments: dict[str, Any] | None = None,
        *,
        actor: Literal["customer", "human"] = "customer",
        key: str | None = None,
        policy: str | None = "pol-1",
        approval: str | None = None,
    ) -> ToolResult:
        n = next(numbers)
        request = ToolCallRequest(
            request_id=f"req-{n}",
            tool=tool,
            arguments=arguments or {},
            idempotency_key=key or f"idem-{n}",
            policy_request_id=policy,
            approval_id=approval,
            actor=actor,
        )
        return await shop.call_tool("shop_001", request)

    return _call
