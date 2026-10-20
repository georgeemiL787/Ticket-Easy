from datetime import date
from pathlib import Path

import pytest

from team_b.adapters.llm import OpenAICompatibleLLM
from team_b.adapters.memory_store import (
    FixedClock,
    InMemoryCaseStore,
    InMemorySessionStore,
    InMemoryTraceStore,
    SystemClock,
)
from team_b.adapters.standins.evidence import StandinEvidenceProvider
from team_b.adapters.standins.shop import StandinShop
from team_b.adapters.team_a_http import HttpEvidenceProvider, HttpPolicyGate
from team_b.brain.llm_nlu import LLMNLU
from team_b.brain.nlu import RuleBasedNLU
from team_b.config import Settings
from team_b.container import Container, build_container
from team_b.contracts.policy import CheckActionRequest
from team_b.domain.tenant import TenantConfigError
from team_b.ports import (
    CapabilityClient,
    CaseStore,
    Clock,
    EvidenceProvider,
    LLMClient,
    PolicyGate,
    SessionStore,
    TraceStore,
)


def test_standin_container_wires_every_dependency(container: Container) -> None:
    assert isinstance(container.evidence, EvidenceProvider)
    assert isinstance(container.policy, PolicyGate)
    assert isinstance(container.capabilities, CapabilityClient)
    assert isinstance(container.sessions, SessionStore)
    assert isinstance(container.traces, TraceStore)
    assert isinstance(container.cases, CaseStore)
    if container.settings.store == "memory":  # the suite can also run with TEAM_B_STORE=sqlite
        assert isinstance(container.sessions, InMemorySessionStore)
        assert isinstance(container.traces, InMemoryTraceStore)
        assert isinstance(container.cases, InMemoryCaseStore)
    assert isinstance(container.clock, Clock)
    assert container.llm is None  # rules only
    assert len(container.tenants) == 0


def test_the_conftest_container_uses_the_fixed_date(container: Container) -> None:
    assert isinstance(container.clock, FixedClock)
    assert container.clock.today() == date(2026, 9, 28)


def test_without_a_fixed_date_the_real_clock_is_used(tenants_dir: Path) -> None:
    built = build_container(Settings(config_dir=tenants_dir))
    assert isinstance(built.clock, SystemClock)


def test_live_mode_plugs_in_the_real_team_a_and_keeps_the_shop_standin(tenants_dir: Path) -> None:
    built = build_container(Settings(mode="live", team_a_url="http://team-a.test:8001", config_dir=tenants_dir))
    assert isinstance(built.evidence, HttpEvidenceProvider) and isinstance(built.policy, HttpPolicyGate)
    assert isinstance(built.capabilities, StandinShop)  # Team C's tools are not connected yet
    assert built.policy_search is None and built.rule_checker is None and built.safety_screen is None


def test_standin_mode_uses_the_standin_services(tenants_dir: Path) -> None:
    built = build_container(Settings(config_dir=tenants_dir))
    assert isinstance(built.evidence, StandinEvidenceProvider) and built.rule_checker is built.policy


def test_no_llm_means_rules_only(tenants_dir: Path) -> None:
    built = build_container(Settings(llm="none", config_dir=tenants_dir))
    assert built.llm is None and isinstance(built.nlu, RuleBasedNLU)


def test_ollama_builds_the_openai_compatible_client_without_a_key(tenants_dir: Path) -> None:
    built = build_container(Settings(llm="ollama", ollama_url="http://host:11434/", config_dir=tenants_dir))
    assert isinstance(built.llm, OpenAICompatibleLLM) and isinstance(built.llm, LLMClient)
    assert isinstance(built.nlu, LLMNLU)
    assert built.llm._url == "http://host:11434/v1/chat/completions"  # type: ignore[attr-defined]
    assert built.llm._model == "qwen3:8b" and built.llm._headers == {}  # type: ignore[attr-defined]


def test_openrouter_builds_the_client_with_the_key_and_a_default_model(tenants_dir: Path) -> None:
    settings = Settings(llm="openrouter", openrouter_api_key="sk-secret", config_dir=tenants_dir)  # type: ignore[arg-type]
    built = build_container(settings)
    assert isinstance(built.llm, OpenAICompatibleLLM)
    assert built.llm._url == "https://openrouter.ai/api/v1/chat/completions"  # type: ignore[attr-defined]
    assert built.llm._headers == {"Authorization": "Bearer sk-secret"}  # type: ignore[attr-defined]
    assert built.llm._model == "anthropic/claude-haiku-4.5"  # type: ignore[attr-defined]
    assert Settings(llm="ollama", llm_model="llama3").llm_model == "llama3"


def test_a_missing_tenant_directory_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(TenantConfigError, match="not found"):
        build_container(Settings(config_dir=tmp_path / "nope"))


async def test_evidence_plug_screens_with_the_safety_standin(container: Container) -> None:
    assert not (await container.evidence.classify_risk("shop_001", "hello", request_id="r")).flagged
    assert (await container.evidence.classify_risk("shop_001", "this is fraud", request_id="r")).flagged


async def test_policy_plug_is_the_rule_checker(container: Container) -> None:
    request = CheckActionRequest.model_validate(
        {"request_id": "r", "tenant_id": "shop_001", "action": "a", "tool": {"name": "a", "operation_kind": "read"}}
    )
    assert container.policy is container.rule_checker
    decision = await container.policy.check_action(request)  # personal data by default, identity not verified
    assert (decision.decision, decision.reason_code) == ("deny", "IDENTITY_REQUIRED")
