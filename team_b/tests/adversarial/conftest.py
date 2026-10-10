"""Shared set-up for the adversarial suite: the real demo shop, and helpers that judge by the shop's audit log."""

from datetime import date
from pathlib import Path

import pytest

from team_b.container import Container, build_container
from tests.integration.agent_expert import with_expert
from tests.support import make_settings


@pytest.fixture(params=["usual", "agent"])
def container(request: pytest.FixtureRequest, tmp_path: Path) -> Container:
    """The demo shop (real tenant config, stand-ins, rules), clock fixed at 2026-09-28.

    Every attack runs twice: against the usual step-by-step flow, and against the agent loop with a scripted
    orchestrator (tests/integration/agent_expert.py) that does the requested work through the same gates."""
    built = build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))
    return with_expert(built) if request.param == "agent" else built
