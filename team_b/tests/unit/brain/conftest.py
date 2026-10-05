import pytest

from team_b.config import Settings
from team_b.container import Container, build_container
from tests.conftest import FIXED_TODAY


@pytest.fixture
def container_with_tenants() -> Container:
    """Stand-in container with the real tenant config (shop_001), clock fixed at 2026-09-28."""
    return build_container(Settings(fixed_today=FIXED_TODAY))


@pytest.fixture
def c(container_with_tenants: Container) -> Container:
    """Short alias used by the orchestrator tests."""
    return container_with_tenants
