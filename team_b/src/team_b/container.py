"""Wires the brain dependencies from settings. The one place that knows which implementation is plugged in."""

import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from team_b.adapters.alert_repository import InMemoryAlertStore, SqliteAlertStore
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
from team_b.adapters.user_repository import InMemoryUserStore, SqliteUserStore
from team_b.auth import AuthService
from team_b.brain.alerts import AlertEngine, WebhookNotifier
from team_b.brain.llm_nlu import LLMNLU
from team_b.brain.nlu import NLU, RuleBasedNLU
from team_b.brain.orchestrator import Orchestrator
from team_b.brain.registry import CapabilityRegistry
from team_b.brain.rewrite import LLMRewriter
from team_b.brain.summarizer import HistorySummarizer, LLMHistorySummarizer, TemplateHistorySummarizer
from team_b.config import Settings
from team_b.domain.tenant import TenantRegistry
from team_b.events import EventHub
from team_b.ports import (
    AlertStore,
    CapabilityClient,
    CaseStore,
    Clock,
    EvidenceProvider,
    LLMClient,
    PolicyGate,
    SessionStore,
    TraceStore,
    UserStore,
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
    rule_checker: RuleCheckerStandin | None = None  # the rule checker behind `policy`, same purpose
    safety_screen: SafetyScreenStandin | None = None  # the safety screen behind `evidence`, same purpose
    registry: CapabilityRegistry | None = None  # the cached list of published shop tools
    orchestrator: Orchestrator | None = None  # handles every customer message and human action
    nlu: NLU | None = None  # rule-based, or AI-assisted when an AI model is configured
    users: UserStore | None = None  # people who sign in to the inbox and the dashboard
    auth: AuthService | None = None  # checks passwords and the sign-in cookie
    alerts: AlertStore | None = None  # alerts for managers (opened and resolved by alert_engine)
    alert_engine: AlertEngine | None = None
    events: EventHub = field(default_factory=EventHub)  # live delivery of human replies to open chat pages


def build_container(settings: Settings, *, clock: Clock | None = None) -> Container:
    if settings.mode == "live":
        raise ContainerError(
            "TEAM_B_MODE=live is not available until Phase 6 (real Team A and Team C services). "
            "Use TEAM_B_MODE=standin."
        )

    if settings.llm_rewrite and settings.llm == "none":
        raise ContainerError("TEAM_B_LLM_REWRITE=1 needs an AI model: set TEAM_B_LLM=ollama or openrouter.")

    clock = clock or (FixedClock(settings.fixed_today) if settings.fixed_today else SystemClock())
    tenants = TenantRegistry.from_dir(settings.config_dir)
    sessions, traces, cases = build_stores(settings, clock)
    shop = StandinShop(settings.fixtures_dir, clock, tenants.tenant_ids())
    policy_search = PolicySearchStandin(settings.fixtures_dir)
    rule_checker = RuleCheckerStandin(settings.fixtures_dir)
    safety_screen = SafetyScreenStandin(settings.fixtures_dir)
    container = Container(
        settings=settings,
        clock=clock,
        tenants=tenants,
        evidence=StandinEvidenceProvider(policy_search, safety_screen),
        policy=rule_checker,
        capabilities=shop,
        llm=build_llm(settings),
        sessions=sessions,
        traces=traces,
        cases=cases,
        shop=shop,
        policy_search=policy_search,
        rule_checker=rule_checker,
        safety_screen=safety_screen,
    )
    registry = CapabilityRegistry(container.capabilities, clock, settings.capability_ttl_s)
    summarizer: HistorySummarizer = (
        LLMHistorySummarizer(container.llm) if container.llm is not None else TemplateHistorySummarizer()
    )
    nlu: NLU = LLMNLU(container.llm) if container.llm is not None else RuleBasedNLU()
    alerts: AlertStore = (
        SqliteAlertStore(SqliteDatabase(settings.db_path)) if settings.store == "sqlite" else InMemoryAlertStore()
    )
    users: UserStore = (
        SqliteUserStore(SqliteDatabase(settings.db_path)) if settings.store == "sqlite" else InMemoryUserStore()
    )
    secret = (
        settings.secret_key.get_secret_value().encode() if settings.secret_key is not None else secrets.token_bytes(32)
    )  # without TEAM_B_SECRET_KEY every start signs everybody out
    alert_engine = AlertEngine(
        traces=traces,
        cases=cases,
        alerts=alerts,
        clock=clock,
        tenants=tenants,
        notifier=WebhookNotifier(settings.alert_webhook) if settings.alert_webhook else None,
    )
    return replace(
        container,
        users=users,
        auth=AuthService(users, secret, session_hours=settings.session_hours),
        alerts=alerts,
        alert_engine=alert_engine,
        nlu=nlu,
        registry=registry,
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
            llm=container.llm,
            registry=registry,
            policy=container.policy,
            max_queued_runs=settings.queue_max_runs,
        ),
    )


OPENROUTER_URL = "https://openrouter.ai/api/v1"
OPENROUTER_DEFAULT_MODEL = "anthropic/claude-haiku-4.5"


def build_llm(settings: Settings) -> LLMClient | None:
    """The AI model TEAM_B_LLM asks for, or None (rules only). OpenRouter needs OPENROUTER_API_KEY, Ollama no key.
    `openai` is any OpenAI-format server named by TEAM_B_LLM_BASE_URL and TEAM_B_LLM_MODEL."""
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
    if settings.llm == "openai":
        assert settings.llm_base_url is not None and settings.llm_model is not None  # checked by Settings
        return OpenAICompatibleLLM(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key.get_secret_value() if settings.llm_api_key else None,
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
    if plug == "rule_checker" and container.rule_checker is not None:
        container.rule_checker.inject(spec)
        return
    if plug == "safety_screen" and container.safety_screen is not None:
        container.safety_screen.inject(spec)
        return
    raise ValueError(
        f"no failure switches for plug {plug!r} (only shop, policy_search, rule_checker and safety_screen have them)"
    )
