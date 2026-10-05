from datetime import date
from pathlib import Path

import pytest

from team_b.adapters.memory_store import (
    FixedClock,
    InMemoryCaseStore,
    InMemorySessionStore,
    InMemoryTraceStore,
    SystemClock,
)
from team_b.config import Settings
from team_b.container import Container, ContainerError, build_container
from team_b.contracts.policy import CheckActionRequest
from team_b.domain.tenant import TenantConfigError
from team_b.ports import CapabilityClient, CaseStore, Clock, EvidenceProvider, PolicyGate, SessionStore, TraceStore


def test_standin_container_wires_every_dependency(container: Container) -> None:
    assert isinstance(container.evidence, EvidenceProvider)
    assert isinstance(container.policy, PolicyGate)
    assert isinstance(container.capabilities, CapabilityClient)
    assert isinstance(container.sessions, SessionStore) and isinstance(container.sessions, InMemorySessionStore)
    assert isinstance(container.traces, TraceStore) and isinstance(container.traces, InMemoryTraceStore)
    assert isinstance(container.cases, CaseStore) and isinstance(container.cases, InMemoryCaseStore)
    assert isinstance(container.clock, Clock)
    assert container.llm is None  # rules only
    assert len(container.tenants) == 0


def test_the_conftest_container_uses_the_fixed_date(container: Container) -> None:
    assert isinstance(container.clock, FixedClock)
    assert container.clock.today() == date(2026, 9, 28)


def test_without_a_fixed_date_the_real_clock_is_used(tenants_dir: Path) -> None:
    built = build_container(Settings(config_dir=tenants_dir))
    assert isinstance(built.clock, SystemClock)


def test_live_mode_is_not_available_yet(tenants_dir: Path) -> None:
    with pytest.raises(ContainerError, match="Phase 6"):
        build_container(Settings(mode="live", config_dir=tenants_dir))


def test_sqlite_store_and_llm_providers_are_not_available_yet(tenants_dir: Path) -> None:
    with pytest.raises(ContainerError, match="TEAM_B_STORE=sqlite"):
        build_container(Settings(store="sqlite", config_dir=tenants_dir))
    with pytest.raises(ContainerError, match="TEAM_B_LLM=ollama"):
        build_container(Settings(llm="ollama", config_dir=tenants_dir))


def test_a_missing_tenant_directory_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(TenantConfigError, match="not found"):
        build_container(Settings(config_dir=tmp_path / "nope"))


async def test_standin_services_are_placeholders_until_phase_2(container: Container) -> None:
    with pytest.raises(NotImplementedError, match="Phase 2"):
        await container.evidence.classify_risk("shop_001", "hello", request_id="r")
    request = CheckActionRequest.model_validate(
        {"request_id": "r", "tenant_id": "shop_001", "action": "a", "tool": {"name": "a", "operation_kind": "read"}}
    )
    with pytest.raises(NotImplementedError, match="Phase 2"):
        await container.policy.check_action(request)
