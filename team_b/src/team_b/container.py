"""Wires the brain dependencies from settings. The one place that knows which implementation is plugged in."""

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from team_b.adapters.alert_repository import InMemoryAlertStore, SqliteAlertStore
from team_b.adapters.llm_policy_gate import LLMPolicyGate
from team_b.adapters.llm_registry import LLMRegistry
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
from team_b.adapters.team_a_http import HttpEvidenceProvider, HttpPolicyGate, TeamAClient
from team_b.adapters.user_repository import InMemoryUserStore, SqliteUserStore
from team_b.auth import AuthService
from team_b.brain.alerts import AlertEngine, WebhookNotifier
from team_b.brain.knowledge_agent import KnowledgeAgent
from team_b.brain.llm_nlu import LLMNLU
from team_b.brain.move_picker import LLMMovePicker
from team_b.brain.nlu import NLU, RuleBasedNLU
from team_b.brain.orchestrator import Orchestrator
from team_b.brain.registry import CapabilityRegistry
from team_b.brain.reviewer import Reviewer
from team_b.brain.rewrite import LLMRewriter
from team_b.brain.smalltalk import SmallTalkWriter
from team_b.brain.summarizer import HistorySummarizer, LLMHistorySummarizer, TemplateHistorySummarizer
from team_b.brain.writer import ReplyWriter
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
    llm: LLMClient | None  # the general AI model (TEAM_B_LLM_*); None: rules only
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
    llms: LLMRegistry | None = None  # the AI model of every job (reader, writer, reviewer ...): llms.llm_for(role)
    users: UserStore | None = None  # people who sign in to the inbox and the dashboard
    auth: AuthService | None = None  # checks passwords and the sign-in cookie
    alerts: AlertStore | None = None  # alerts for managers (opened and resolved by alert_engine)
    alert_engine: AlertEngine | None = None
    events: EventHub = field(default_factory=EventHub)  # live delivery of human replies to open chat pages


def build_container(
    settings: Settings, *, clock: Clock | None = None, llm_wrapper: Callable[[LLMClient], LLMClient] | None = None
) -> Container:
    """Wire the service. llm_wrapper (the evaluation uses it to count AI calls) wraps the AI model, if there is one."""
    llms = LLMRegistry(settings, wrapper=llm_wrapper)
    if settings.llm_rewrite and llms.llm_for("writer") is None:
        raise ContainerError("TEAM_B_LLM_REWRITE=1 needs an AI model: set TEAM_B_LLM=ollama or openrouter.")

    clock = clock or (FixedClock(settings.fixed_today) if settings.fixed_today else SystemClock())
    tenants = TenantRegistry.from_dir(settings.config_dir)
    sessions, traces, cases = build_stores(settings, clock)
    shop = StandinShop(settings.fixtures_dir, clock, tenants.tenant_ids())
    live = settings.mode == "live"  # Team A is real; the shop stays a stand-in until Team C's tools are connected
    policy_search = None if live else PolicySearchStandin(settings.fixtures_dir)
    rule_checker = None if live else RuleCheckerStandin(settings.fixtures_dir)
    safety_screen = None if live else SafetyScreenStandin(settings.fixtures_dir)
    evidence: EvidenceProvider
    policy: PolicyGate
    if live:
        team_a = TeamAClient(settings.team_a_url, timeout_s=settings.team_a_timeout_s)
        evidence, policy = HttpEvidenceProvider(team_a), HttpPolicyGate(team_a)
    else:
        assert policy_search is not None and rule_checker is not None and safety_screen is not None
        evidence, policy = StandinEvidenceProvider(policy_search, safety_screen), rule_checker
    llm = llms.default()
    if settings.llm_policy and llms.llm_for("reviewer") is None:
        raise ContainerError("TEAM_B_LLM_POLICY needs an AI model: set TEAM_B_LLM.")
    if settings.llm_knowledge and llms.llm_for("knowledge") is None:
        raise ContainerError("TEAM_B_LLM_KNOWLEDGE needs an AI model: set TEAM_B_LLM.")
    if settings.llm_review and llms.llm_for("reviewer") is None:
        raise ContainerError("TEAM_B_LLM_REVIEW needs an AI model: set TEAM_B_LLM.")
    reviewer_llm = llms.llm_for("reviewer")
    reviewer = Reviewer(reviewer_llm) if settings.llm_review and reviewer_llm is not None else None
    if (settings.llm_policy or settings.llm_review) and reviewer_llm is not None:
        # the model may tighten the rule checker, never loosen it
        policy = LLMPolicyGate(policy, evidence, reviewer_llm, reviewer=reviewer or Reviewer(reviewer_llm))
    container = Container(
        settings=settings,
        clock=clock,
        tenants=tenants,
        evidence=evidence,
        policy=policy,
        capabilities=shop,
        llm=llm,
        llms=llms,
        sessions=sessions,
        traces=traces,
        cases=cases,
        shop=shop,
        policy_search=policy_search,
        rule_checker=rule_checker,
        safety_screen=safety_screen,
    )
    registry = CapabilityRegistry(container.capabilities, clock, settings.capability_ttl_s)
    summary_llm, reader_llm, knowledge_llm = llms.llm_for("summary"), llms.llm_for("reader"), llms.llm_for("knowledge")
    writer_llm = llms.llm_for("writer") if settings.llm_rewrite else None
    orchestrator_llm = llms.llm_for("orchestrator")
    summarizer: HistorySummarizer = (
        LLMHistorySummarizer(summary_llm) if summary_llm is not None else TemplateHistorySummarizer()
    )
    knowledge = KnowledgeAgent(knowledge_llm) if settings.llm_knowledge and knowledge_llm is not None else None
    nlu: NLU = LLMNLU(reader_llm) if reader_llm is not None else RuleBasedNLU()
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
            rewriter=LLMRewriter(writer_llm) if writer_llm is not None else None,
            writer=ReplyWriter(writer_llm, reviewer=reviewer) if writer_llm is not None else None,
            reviewer=reviewer,
            smalltalk=SmallTalkWriter(writer_llm) if writer_llm is not None else None,
            events=container.events,
            llm=summary_llm,
            move_picker=LLMMovePicker(orchestrator_llm) if orchestrator_llm is not None else None,
            knowledge_agent=knowledge,
            registry=registry,
            policy=container.policy,
            max_queued_runs=settings.queue_max_runs,
            agent_mode=settings.agent_mode,
            agent_max_moves=settings.agent_max_moves,
            agent_turn_timeout_s=settings.agent_turn_timeout_s,
        ),
    )


def build_llm(settings: Settings) -> LLMClient | None:
    """The general AI model TEAM_B_LLM asks for, or None (rules only). OpenRouter needs OPENROUTER_API_KEY, Ollama no
    key. `openai` is any OpenAI-format server named by TEAM_B_LLM_BASE_URL and TEAM_B_LLM_MODEL. Each job can have its
    own model: see LLMRegistry (adapters/llm_registry.py)."""
    return LLMRegistry(settings).default()


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
