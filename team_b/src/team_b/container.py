"""Wires the brain dependencies from settings. The one place that knows which implementation is plugged in."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from team_b.adapters.memory_store import (
    FixedClock,
    InMemoryCaseStore,
    InMemorySessionStore,
    InMemoryTraceStore,
    SystemClock,
)
from team_b.adapters.standins.evidence import StandinEvidenceProvider
from team_b.adapters.standins.policy_search import PolicySearchStandin
from team_b.adapters.standins.rule_checker import RuleCheckerStandin
from team_b.adapters.standins.safety_screen import SafetyScreenStandin
from team_b.adapters.standins.shop import StandinShop
from team_b.config import Settings
from team_b.domain.tenant import TenantRegistry
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


class ContainerError(RuntimeError):
    """The requested configuration cannot be built (yet). The message says what to change."""


@dataclass(frozen=True)
class Container:
    settings: Settings
    clock: Clock
    tenants: TenantRegistry
    evidence: EvidenceProvider
    policy: PolicyGate
    capabilities: CapabilityClient
    llm: LLMClient | None  # None: rules only
    sessions: SessionStore
    traces: TraceStore
    cases: CaseStore
    shop: StandinShop | None = None  # the fake shop behind `capabilities`, kept so tests can flip its switches


def build_container(settings: Settings) -> Container:
    if settings.mode == "live":
        raise ContainerError(
            "TEAM_B_MODE=live is not available until Phase 6 (real Team A and Team C services). "
            "Use TEAM_B_MODE=standin."
        )
    if settings.store == "sqlite":
        raise ContainerError("TEAM_B_STORE=sqlite is not available yet. Use TEAM_B_STORE=memory.")
    if settings.llm != "none":
        raise ContainerError(f"TEAM_B_LLM={settings.llm} is not available yet. Use TEAM_B_LLM=none (rules only).")

    clock = FixedClock(settings.fixed_today) if settings.fixed_today else SystemClock()
    tenants = TenantRegistry.from_dir(settings.config_dir)
    shop = StandinShop(settings.fixtures_dir, clock, tenants.tenant_ids())
    return Container(
        settings=settings,
        clock=clock,
        tenants=tenants,
        evidence=StandinEvidenceProvider(PolicySearchStandin(), SafetyScreenStandin()),
        policy=RuleCheckerStandin(),
        capabilities=shop,
        llm=None,
        sessions=InMemorySessionStore(),
        traces=InMemoryTraceStore(),
        cases=InMemoryCaseStore(),
        shop=shop,
    )


def inject(container: Container, plug: str, spec: Mapping[str, Any]) -> None:
    """Flip a failure switch on a stand-in, for the scenario runner: inject: {plug: shop, switch: fail_next, ...}."""
    if plug == "shop" and container.shop is not None:
        container.shop.inject(spec)
        return
    raise ValueError(f"no failure switches for plug {plug!r} (only the shop has them so far)")
