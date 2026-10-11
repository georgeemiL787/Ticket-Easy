from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from team_b.api.app import create_app
from team_b.container import Container, build_container
from tests.conftest import FIXED_TODAY
from tests.support import make_settings

T = "shop_001"


@pytest.fixture
def chat_container(tmp_path: Path) -> Container:
    """The real tenant (shop_001), stand-ins, and a generous rate limit unless a test lowers it."""
    return build_container(make_settings(tmp_path, fixed_today=FIXED_TODAY, rate_limit_per_minute=1000))


@pytest.fixture
def chat_app(chat_container: Container) -> FastAPI:
    return create_app(container=chat_container)


@pytest.fixture
async def chat(chat_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with chat_app.router.lifespan_context(chat_app):
        transport = httpx.ASGITransport(app=chat_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http
