"""Shared set-up for the adversarial suite: the real demo shop, and helpers that judge by the shop's audit log."""

from datetime import date
from pathlib import Path

import pytest

from team_b.container import Container, build_container
from tests.support import make_settings


@pytest.fixture
def container(tmp_path: Path) -> Container:
    """The demo shop (real tenant config, stand-ins, rules), clock fixed at 2026-09-28."""
    return build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))
