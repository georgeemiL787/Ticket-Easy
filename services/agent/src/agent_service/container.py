"""Wires the core dependencies from settings. The one place that knows which implementation is plugged in."""

import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from agent_service.actions.tool_registry import CapabilityRegistry
from agent_service.adapters.alert_repository import InMemoryAlertStore, SqliteAlertStore
from agent_service.adapters.bundle_repository import InMemoryBundleStore, SqliteBundleStore
from agent_service.adapters.knowledge_admin_client import KnowledgeAdminClient
from agent_service.adapters.knowledge_client import HttpEvidenceProvider, HttpPolicyGate, KnowledgeClient
from agent_service.adapters.llm_policy_gate import LLMPolicyGate
from agent_service.adapters.llm_registry import LLMRegistry
from agent_service.adapters.memory_store import (
    FixedClock,
    InMemoryCaseStore,
    InMemorySessionStore,
    InMemoryTraceStore,
    SystemClock,
    TickingClock,
)
from agent_service.adapters.onboarding_repository import InMemoryDraftStore, SqliteDraftStore
from agent_service.adapters.simulated.evidence import SimulatedEvidenceProvider
from agent_service.adapters.simulated.knowledge_admin import SimulatedKnowledgeAdmin
from agent_service.adapters.simulated.policy_search import SimulatedPolicySearch
from agent_service.adapters.simulated.rule_checker import SimulatedRuleChecker
from agent_service.adapters.simulated.safety_screen import SimulatedSafetyScreen
from agent_service.adapters.simulated.shop import SimulatedShop
from agent_service.adapters.sqlite_store import SqliteCaseStore, SqliteDatabase, SqliteSessionStore, SqliteTraceStore
from agent_service.adapters.tools_catalog import ToolCapabilities, ToolsConfig
from agent_service.adapters.user_repository import InMemoryUserStore, SqliteUserStore
from agent_service.agent.planner import LLMPlanner
from agent_service.agent.reviewer import Reviewer
from agent_service.auth import AuthService
from agent_service.bundles import BundleRegistry
from agent_service.config import Settings
from agent_service.contracts.errors import UpstreamError
from agent_service.domain.bundle import TenantBundle
from agent_service.domain.tenant import TenantRegistry
from agent_service.events import EventHub
from agent_service.knowledge.agent import KnowledgeAgent
from agent_service.monitoring.alerts import AlertEngine, WebhookNotifier
from agent_service.ports import (
    AlertStore,
    CapabilityClient,
    CaseStore,
    Clock,
    DraftStore,
    EvidenceProvider,
    KnowledgeAdmin,
    LLMClient,
    PolicyGate,
    SessionStore,
    TraceStore,
    UserStore,
)
from agent_service.preview import PreviewTokens
from agent_service.replies.rewrite import LLMRewriter
from agent_service.replies.smalltalk import SmallTalkWriter
from agent_service.replies.summarizer import HistorySummarizer, LLMHistorySummarizer, TemplateHistorySummarizer
from agent_service.replies.writer import ReplyWriter
from agent_service.turn.handler import Orchestrator
from agent_service.understanding.ai_reader import LLMNLU
from agent_service.understanding.rules import NLU, RuleBasedNLU


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
    llm: LLMClient | None  # the general AI model (AGENT_LLM_*); None: rules only
    sessions: SessionStore
    traces: TraceStore
    cases: CaseStore
    shop: SimulatedShop | None = None  # the fake shop behind `capabilities`, kept so tests can flip its switches
    team_c: ToolCapabilities | None = None  # The tools service's sandbox tools (AGENT_TOOLS_CONFIG), wrapping `shop`
    policy_search: SimulatedPolicySearch | None = None  # the policy search behind `evidence`, same purpose
    rule_checker: SimulatedRuleChecker | None = None  # the rule checker behind `policy`, same purpose
    safety_screen: SimulatedSafetyScreen | None = None  # the safety screen behind `evidence`, same purpose
    registry: CapabilityRegistry | None = None  # the cached list of published shop tools
    orchestrator: Orchestrator | None = None  # handles every customer message and human action
    nlu: NLU | None = None  # rule-based, or AI-assisted when an AI model is configured
    llms: LLMRegistry | None = None  # the AI model of every job (reader, writer, reviewer ...): llms.llm_for(role)
    users: UserStore | None = None  # people who sign in to the inbox and the dashboard
    auth: AuthService | None = None  # checks passwords and the sign-in cookie
    alerts: AlertStore | None = None  # alerts for managers (opened and resolved by alert_engine)
    alert_engine: AlertEngine | None = None
    previews: PreviewTokens | None = None  # signed passes for staff to chat with a business that is not live yet
    drafts: DraftStore | None = None  # where the onboarding wizard stands for each business being set up
    knowledge_admin: KnowledgeAdmin | None = (
        None  # documents and rules for the knowledge pages (None: those pages are off)
    )
    bundles: BundleRegistry | None = None  # versioned business configuration; `tenants` is this registry when enabled
    events: EventHub = field(default_factory=EventHub)  # live delivery of human replies to open chat pages


def build_container(
    settings: Settings, *, clock: Clock | None = None, llm_wrapper: Callable[[LLMClient], LLMClient] | None = None
) -> Container:
    """Wire the service. llm_wrapper (the evaluation uses it to count AI calls) wraps the AI model, if there is one."""
    llms = LLMRegistry(settings, wrapper=llm_wrapper)
    if settings.llm_rewrite and llms.llm_for("writer") is None:
        raise ContainerError("AGENT_LLM_REWRITE=1 needs an AI model: set AGENT_LLM=ollama or openrouter.")

    if clock is None:
        if not settings.fixed_today:
            clock = SystemClock()
        else:
            clock = TickingClock(settings.fixed_today) if settings.clock_ticks else FixedClock(settings.fixed_today)
    tenants = TenantRegistry.from_dir(settings.config_dir)
    sessions, traces, cases = build_stores(settings, clock)
    bundles: BundleRegistry | None = None
    if settings.bundles:  # an active bundle replaces the tenant file of its business; the registry has the same API
        bundles = BundleRegistry(
            tenants,
            SqliteBundleStore(SqliteDatabase(settings.db_path))
            if settings.store == "sqlite"
            else InMemoryBundleStore(),
            clock,
        )
        tenants = bundles
    shops_dir = settings.db_path.parent / "shops" if settings.store == "sqlite" else None  # SQLite shops keep files
    shop = SimulatedShop(settings.fixtures_dir, clock, tenants.tenant_ids(), db_dir=shops_dir)
    # The tools service serves the tools its config binds (sandbox publications over MCP); the simulated serves the
    # rest.
    team_c = ToolCapabilities(shop, ToolsConfig.load(settings.tools_config)) if settings.tools_config else None
    capabilities: CapabilityClient = team_c if team_c is not None else shop
    if bundles is not None and team_c is not None:
        bundles.add_listener(team_c.on_bundle_change)  # a new active bundle rebinds that business's tools
    live = settings.backends == "live"  # The knowledge service is real (policy search, safety screen, rule checker)
    policy_search = None if live else SimulatedPolicySearch(settings.fixtures_dir)
    rule_checker = None if live else SimulatedRuleChecker(settings.fixtures_dir)
    safety_screen = None if live else SimulatedSafetyScreen(settings.fixtures_dir)
    evidence: EvidenceProvider
    policy: PolicyGate
    if live:
        service_key = settings.knowledge_key.get_secret_value() if settings.knowledge_key is not None else None
        team_a = KnowledgeClient(
            settings.knowledge_url, timeout_s=settings.knowledge_timeout_s, service_key=service_key
        )
        evidence, policy = HttpEvidenceProvider(team_a), HttpPolicyGate(team_a)
        knowledge_admin: KnowledgeAdmin | None = (
            KnowledgeAdminClient(
                settings.knowledge_url, settings.knowledge_admin_key.get_secret_value(), service_key=service_key
            )
            if settings.knowledge_admin_key is not None
            else None
        )
    else:
        assert policy_search is not None and rule_checker is not None and safety_screen is not None
        evidence, policy = SimulatedEvidenceProvider(policy_search, safety_screen), rule_checker
        knowledge_admin = SimulatedKnowledgeAdmin()
    llm = llms.default()
    if settings.llm_policy and llms.llm_for("reviewer") is None:
        raise ContainerError("AGENT_LLM_POLICY needs an AI model: set AGENT_LLM.")
    if settings.llm_knowledge and llms.llm_for("knowledge") is None:
        raise ContainerError("AGENT_LLM_KNOWLEDGE needs an AI model: set AGENT_LLM.")
    if settings.llm_review and llms.llm_for("reviewer") is None:
        raise ContainerError("AGENT_LLM_REVIEW needs an AI model: set AGENT_LLM.")
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
        capabilities=capabilities,
        llm=llm,
        llms=llms,
        sessions=sessions,
        traces=traces,
        cases=cases,
        shop=shop,
        team_c=team_c,
        bundles=bundles,
        knowledge_admin=knowledge_admin,
        policy_search=policy_search,
        rule_checker=rule_checker,
        safety_screen=safety_screen,
    )
    if bundles is not None and knowledge_admin is not None:
        bundles.add_publish_check(approved_rules_are_current(knowledge_admin))
    registry = CapabilityRegistry(container.capabilities, clock, settings.capability_ttl_s)
    summary_llm, reader_llm, knowledge_llm = llms.llm_for("summary"), llms.llm_for("reader"), llms.llm_for("knowledge")
    writer_llm = llms.llm_for("writer") if settings.llm_rewrite else None
    own = settings.role("writer_egyptian")  # only when this job has its own server or model: it never borrows
    egyptian_llm = llms.llm_for("writer_egyptian") if settings.llm_rewrite and (own.base_url or own.model) else None
    planner_llm = llms.llm_for("planner")
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
    )  # without AGENT_SECRET_KEY every start signs everybody out
    alert_engine = AlertEngine(
        traces=traces,
        cases=cases,
        alerts=alerts,
        clock=clock,
        tenants=tenants,
        notifier=WebhookNotifier(settings.alert_webhook) if settings.alert_webhook else None,
    )
    drafts: DraftStore = (
        SqliteDraftStore(SqliteDatabase(settings.db_path)) if settings.store == "sqlite" else InMemoryDraftStore()
    )
    return replace(
        container,
        previews=PreviewTokens(secret),
        drafts=drafts,
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
            writer=ReplyWriter(writer_llm, reviewer=reviewer, egyptian_llm=egyptian_llm)
            if writer_llm is not None
            else None,
            reviewer=reviewer,
            smalltalk=SmallTalkWriter(writer_llm) if writer_llm is not None else None,
            events=container.events,
            llm=summary_llm,
            planner=LLMPlanner(planner_llm) if planner_llm is not None else None,
            knowledge_agent=knowledge,
            registry=registry,
            policy=container.policy,
            max_queued_runs=settings.queue_max_runs,
            agent_mode=settings.agent_mode,
            agent_max_moves=settings.agent_max_moves,
            agent_turn_timeout_s=settings.agent_turn_timeout_s,
        ),
    )


def approved_rules_are_current(admin: KnowledgeAdmin) -> Callable[[Any], Awaitable[list[str]]]:
    """A bundle that names a ruleset version may be published only while that is still the knowledge service's approved
    ruleset (a bundle from the knowledge service must name one): rules approved or made stale since then block it."""

    async def check(bundle: TenantBundle) -> list[str]:
        named = bundle.guardrails.ruleset_version
        if named is None and bundle.knowledge.provider != "team_a":
            return []
        try:
            current = (await admin.ruleset(bundle.tenant_id)).get("ruleset_version")
        except UpstreamError:
            return ["the approved rules could not be checked: the knowledge service did not answer"]
        if named is None:
            return [
                "the guardrails were never taken from the knowledge service: refresh them and approve the rules first"
            ]
        if named != current:
            return [
                "the approved rules changed since this draft was made: refresh the draft from the knowledge service"
            ]
        return []

    return check


def build_llm(settings: Settings) -> LLMClient | None:
    """The general AI model AGENT_LLM asks for, or None (rules only). OpenRouter needs OPENROUTER_API_KEY, Ollama no
    key. `openai` is any OpenAI-format server named by AGENT_LLM_BASE_URL and AGENT_LLM_MODEL. Each job can have its
    own model: see LLMRegistry (adapters/llm_registry.py)."""
    return LLMRegistry(settings).default()


def build_stores(settings: Settings, clock: Clock) -> tuple[SessionStore, TraceStore, CaseStore]:
    """The stores AGENT_STORE asks for: memory (gone on restart) or sqlite (the file at AGENT_DB_PATH)."""
    if settings.store == "sqlite":
        db = SqliteDatabase(settings.db_path)
        return SqliteSessionStore(db), SqliteTraceStore(db, clock), SqliteCaseStore(db)
    return InMemorySessionStore(), InMemoryTraceStore(clock), InMemoryCaseStore()


def inject(container: Container, plug: str, spec: Mapping[str, Any]) -> None:
    """Flip a failure switch on a simulated service, for the scenario runner: inject: {plug: shop, switch: fail_next,
    ...}."""
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
