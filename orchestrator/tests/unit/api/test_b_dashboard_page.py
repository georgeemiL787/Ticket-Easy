"""The built dashboard app is served at /dashboard, with every address under it falling back to index.html."""

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from team_b.api.app import mount_dashboard_app


@pytest.fixture
def built(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>dash</title>", encoding="utf-8")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path.parent / "secret.txt").write_text("not for the web", encoding="utf-8")
    return tmp_path


def client(directory: Path) -> httpx.AsyncClient:
    app = FastAPI()
    mount_dashboard_app(app, directory)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_files_are_served_and_other_addresses_get_the_app(built: Path) -> None:
    async with client(built) as http:
        assert "<title>dash</title>" in (await http.get("/dashboard")).text
        assert (await http.get("/dashboard/assets/app.js")).text == "console.log(1)"
        deep = await http.get("/dashboard/conversations/seed-0001")  # a route of the app, opened or reloaded directly
        assert deep.status_code == 200 and "<title>dash</title>" in deep.text


async def test_a_path_cannot_leave_the_dashboard_folder(built: Path) -> None:
    async with client(built) as http:
        for attempt in ("/dashboard/../secret.txt", "/dashboard/%2e%2e/secret.txt", "/dashboard/..%2fsecret.txt"):
            response = await http.get(attempt)
            assert "not for the web" not in response.text, attempt


async def test_without_a_build_the_page_says_how_to_make_one(tmp_path: Path) -> None:
    async with client(tmp_path / "missing") as http:
        response = await http.get("/dashboard")
        assert response.status_code == 503 and "npm ci && npm run build" in response.text
