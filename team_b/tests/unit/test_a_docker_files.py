"""The Docker demo files are consistent with the code: every setting documented, both profiles, a safe image."""

import re
from pathlib import Path

import pytest

from team_b.config import ENV_VARS

ROOT = Path(__file__).resolve().parents[3]
DOCKERFILE = (ROOT / "team_b" / "Dockerfile").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
MAKEFILE = (ROOT / "Makefile").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")


def test_the_root_env_example_documents_every_setting() -> None:
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", ENV_EXAMPLE, flags=re.MULTILINE))
    missing = {name for name in ENV_VARS.values() if name not in documented}
    assert not missing, f"not in the root .env.example: {sorted(missing)}"


def test_the_image_is_built_in_two_stages_and_runs_without_privileges() -> None:
    assert re.search(r"^FROM python:3\.12-slim", DOCKERFILE, flags=re.MULTILINE)
    assert re.search(r"^FROM node:\S+ AS dashboard", DOCKERFILE, flags=re.MULTILINE)
    assert "COPY --from=dashboard" in DOCKERFILE and "npm run build" in DOCKERFILE
    assert re.search(r"^USER app$", DOCKERFILE, flags=re.MULTILINE) and "useradd" in DOCKERFILE
    assert "HEALTHCHECK" in DOCKERFILE and "/health" in DOCKERFILE and "8010" in DOCKERFILE
    assert "TEAM_B_ENABLE_TEST_ADMIN" not in DOCKERFILE  # the test endpoints are never in the image by default


def test_the_image_installs_the_service_in_place_so_it_finds_its_data_folders() -> None:
    assert "pip install --no-deps -e ." in DOCKERFILE
    for folder in ("config", "data", "fixtures", "prompts", "contracts"):
        assert f"COPY team_b/{folder} {folder}" in DOCKERFILE


def test_compose_has_the_two_profiles_and_health_checks() -> None:
    assert 'profiles: ["light"]' in COMPOSE and COMPOSE.count('profiles: ["full"]') == 2
    assert "ollama/ollama" in COMPOSE and "TEAM_B_LLM: ollama" in COMPOSE and "qwen3:8b" in COMPOSE
    assert COMPOSE.count("healthcheck:") >= 2 and "service_healthy" in COMPOSE
    assert "team_b_data:/data" in COMPOSE and "TEAM_B_STORE: sqlite" in COMPOSE
    assert "TEAM_B_ENABLE_TEST_ADMIN" not in COMPOSE


def test_the_root_makefile_has_the_four_commands_and_prints_the_urls() -> None:
    for target in ("demo:", "demo-full:", "down:", "logs:"):
        assert re.search(rf"^{re.escape(target)}", MAKEFILE, flags=re.MULTILINE), target
    for url in ("/chat?tenant_id=shop_001", "/inbox", "/dashboard"):
        assert url in MAKEFILE
    assert "seed-demo" in MAKEFILE and "--wait" in MAKEFILE


def test_the_demo_command_exists() -> None:
    from team_b.__main__ import main

    assert callable(main)


async def test_seed_demo_fills_the_database_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from team_b.__main__ import seed_demo

    monkeypatch.setenv("TEAM_B_DB_PATH", str(tmp_path / "demo.sqlite3"))
    assert await seed_demo(6, 2, force=False) == 0
    assert "seeded 6 conversations" in capsys.readouterr().out
    assert await seed_demo(6, 2, force=False) == 0
    assert "nothing seeded" in capsys.readouterr().out
    assert await seed_demo(3, 2, force=True) == 0
    assert "seeded 3 conversations" in capsys.readouterr().out


async def test_the_demo_seed_ends_at_the_services_own_now_and_leaves_alerts_to_show(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The compose demo pins the clock: the history must end there, and the Alerts page needs a resolved outage."""
    from datetime import UTC, date, datetime

    from team_b.__main__ import seed_demo
    from team_b.config import Settings
    from team_b.container import build_container

    monkeypatch.setenv("TEAM_B_DB_PATH", str(tmp_path / "demo.sqlite3"))
    monkeypatch.setenv("TEAM_B_FIXED_TODAY", "2026-09-28")
    assert await seed_demo(40, 6, force=False) == 0
    container = build_container(Settings.from_env().model_copy(update={"store": "sqlite"}))  # the clock the service has
    assert container.alerts is not None
    alerts = await container.alerts.list("shop_001")
    assert any(a.rule == "service_down" and a.resolved_at is not None for a in alerts)
    end = datetime(2026, 9, 28, 12, tzinfo=UTC)
    facts = await container.traces.facts("shop_001", end.replace(year=2026, month=9, day=20), end.replace(hour=23))
    assert len(facts.turns) >= 40  # inside the window the dashboard looks at (it ends at the service's "now")
    assert date(2026, 9, 28) == container.clock.today()
