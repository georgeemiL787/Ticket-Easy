"""Wires the brain dependencies from settings. The one place that knows which implementation is plugged in."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from team_b.adapters.llm import OpenAICompatibleLLM
from team_b.adapters.memory_store import (
    FixedClock,
    InMemoryCaseStore,
    InMemorySessionStore,
    InMemoryTraceStore,
    SystemClock,
)
from team_b.adapters.sqlite_store import SqliteCaseStore, SqliteDatabase, SqliteSessionStore, SqliteTraceStore
from team_b.adapters.standins.evidence import StandinEvidenceProvider
from team_b.adapters.standins.policy_search import PolicySearchStandin
from team_b.adapters.standins.rule_checker import RuleCheckerStandin
from team_b.adapters.standins.safety_screen import SafetyScreenStandin
from team_b.adapters.standins.shop import StandinShop
from team_b.brain.llm_nlu import LLMNLU
from team_b.brain.nlu import NLU, RuleBasedNLU
from team_b.brain.orchestrator import Orchestrator
from team_b.brain.rewrite import LLMRewriter
from team_b.brain.summarizer import HistorySummarizer, LLMHistorySummarizer, TemplateHistorySummarizer
from team_b.config import Settings
from team_b.domain.tenant import TenantRegistry
from team_b.events import EventHub
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
    policy_search: PolicySearchStandin | None = None  # the policy search behind `evidence`, same purpose
    orchestrator: Orchestrator | None = None  # handles every customer message and human action
    nlu: NLU | None = None  # rule-based, or AI-assisted when an AI model is configured
    events: EventHub = field(default_factory=EventHub)  # live delivery of human replies to open chat pages


def build_container(settings: Settings) -> Container:
    if settings.mode == "live":
        raise ContainerError(
            "TEAM_B_MODE=live is not available until Phase 6 (real Team A and Team C services). "
            "Use TEAM_B_MODE=standin."
        )

    if settings.llm_rewrite and settings.llm == "none":
        raise ContainerError("TEAM_B_LLM_REWRITE=1 needs an AI model: set TEAM_B_LLM=ollama or openrouter.")

    clock = FixedClock(settings.fixed_today) if settings.fixed_today else SystemClock()
    tenants = TenantRegistry.from_dir(settings.config_dir)
    sessions, traces, cases = build_stores(settings, clock)
    shop = StandinShop(settings.fixtures_dir, clock, tenants.tenant_ids())
    policy_search = PolicySearchStandin(settings.fixtures_dir)
    container = Container(
        settings=settings,
        clock=clock,
        tenants=tenants,
        evidence=StandinEvidenceProvider(policy_search, SafetyScreenStandin()),
        policy=RuleCheckerStandin(),
        capabilities=shop,
        llm=build_llm(settings),
        sessions=sessions,
        traces=traces,
        cases=cases,
        shop=shop,
        policy_search=policy_search,
    )
    summarizer: HistorySummarizer = (
        LLMHistorySummarizer(container.llm) if container.llm is not None else TemplateHistorySummarizer()
    )
    nlu: NLU = LLMNLU(container.llm) if container.llm is not None else RuleBasedNLU()
    return replace(
        container,
        nlu=nlu,
        orchestrator=Orchestrator(
            clock=clock,
            tenants=tenants,
            sessions=container.sessions,
            traces=container.traces,
            cases=container.cases,
            summarizer=summarizer,
            nlu=nlu,
            evidence=container.evidence,
            capabilities=container.capabilities,
            rewriter=LLMRewriter(container.llm) if settings.llm_rewrite and container.llm is not None else None,
            events=container.events,
        ),
    )


OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_MODEL = "anthropic/claude-haiku-4.5"


def build_llm(settings: Settings) -> LLMClient | None:
    """The AI model TEAM_B_LLM asks for, or None (rules only). OpenRouter needs OPENROUTER_API_KEY, Ollama no key."""
    if settings.llm == "ollama":
        return OpenAICompatibleLLM(
            base_url=settings.ollama_url.rstrip("/") + "/v1",
            model=settings.llm_model or settings.ollama_model,
            timeout_s=settings.llm_timeout_s,
        )
    if settings.llm == "openrouter":
        key = settings.openrouter_api_key.get_secret_value() if settings.openrouter_api_key else None
        return OpenAICompatibleLLM(
            base_url=OPENROUTER_URL,
            model=settings.llm_model or OPENROUTER_DEFAULT_MODEL,
            api_key=key,
            timeout_s=settings.llm_timeout_s,
        )
    return None


def build_stores(settings: Settings, clock: Clock) -> tuple[SessionStore, TraceStore, CaseStore]:
    """The stores TEAM_B_STORE asks for: memory (gone on restart) or sqlite (the file at TEAM_B_DB_PATH)."""
    if settings.store == "sqlite":
        db = SqliteDatabase(settings.db_path)
        return SqliteSessionStore(db), SqliteTraceStore(db, clock), SqliteCaseStore(db)
    return InMemorySessionStore(), InMemoryTraceStore(clock), InMemoryCaseStore()


def inject(container: Container, plug: str, spec: Mapping[str, Any]) -> None:
    """Flip a failure switch on a stand-in, for the scenario runner: inject: {plug: shop, switch: fail_next, ...}."""
    if plug == "shop" and container.shop is not None:
        container.shop.inject(spec)
        return
    if plug == "policy_search" and container.policy_search is not None:
        container.policy_search.inject(spec)
        return
    raise ValueError(f"no failure switches for plug {plug!r} (only shop and policy_search have them so far)")
