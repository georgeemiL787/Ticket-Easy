"""Speed of the dashboard overview on a big database. Run with `python -m pytest -q -m slow`."""

import json
import random
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from team_b.adapters.sqlite_store import SqliteDatabase
from team_b.api.app import create_app
from team_b.container import build_container
from tests.support import make_settings

ROWS = 100_000
LIMIT_S = 0.3
T = "shop_001"
START = datetime(2026, 9, 21, tzinfo=UTC)


async def fill(db: SqliteDatabase, rows: int) -> None:
    rng = random.Random(7)
    data = []
    for number in range(rows):
        moment = START + timedelta(seconds=rng.random() * 14 * 86400)  # two weeks: this period and the one before
        handoff = rng.random() < 0.12
        data.append(
            (
                f"t{number}", T, f"c{number // 3}", moment.isoformat(), "handoff" if handoff else "answer",
                "no_evidence" if handoff else None, "en", "policy_question", rng.random() * 800, 0, 1, "rules", 0,
                "[]", json.dumps({"understand": 1.5}),
            )
        )  # fmt: skip
    async with db.connect() as conn:
        await conn.execute("BEGIN")
        await conn.executemany("INSERT INTO turn_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", data)
        await conn.execute("COMMIT")


@pytest.mark.slow
async def test_the_overview_of_100k_turns_takes_under_300_ms_on_sqlite(tmp_path: Path) -> None:
    container = build_container(make_settings(tmp_path, store="sqlite"))
    await fill(SqliteDatabase(tmp_path / "team_b.sqlite3"), ROWS)
    app = create_app(container=container)
    params = {
        "tenant_id": T,
        "from": (START + timedelta(days=7)).isoformat(),
        "to": (START + timedelta(days=14)).isoformat(),
    }
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            await http.get("/v1/dashboard/overview", params=params)  # warm up (schema, caches)
            timings = []
            for _ in range(5):
                started = time.perf_counter()
                response = await http.get("/v1/dashboard/overview", params=params)
                timings.append(time.perf_counter() - started)
                assert response.status_code == 200
    print(f"overview median {sorted(timings)[len(timings) // 2] * 1000:.0f} ms over {ROWS} rows")
    body = response.json()
    assert body["conversations"]["value"] > 10_000 and body["conversations"]["previous"] > 10_000
    assert sorted(timings)[len(timings) // 2] < LIMIT_S, f"overview took {timings} seconds"
