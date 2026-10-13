"""Wires the brain dependencies from settings. The one place that knows which implementation is plugged in."""

import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from team_b.adapters.alert_repository import InMemoryAlertStore, SqliteAlertStore
from team_b.adapters.bundle_repository import InMemoryBundleStore, SqliteBundleStore
from team_b.adapters.llm_policy_gate import LLMPolicyGate
from team_b.adapters.llm_registry import LLMRegistry
from team_b.adapters.memory_store import (
    FixedClock,
    InMemoryCaseStore,
    InMemorySessionStore,
    InMemoryTraceStore,
    SystemClock,
    TickingClock,
)
from team_b.adapters.onboarding_repository import InMemoryDraftStore, SqliteDraftStore
from team_b.adapters.sqlite_store import SqliteCaseStore, SqliteDatabase, SqliteSessionStore, SqliteTraceStore
from team_b.adapters.standins.evidence import StandinEvidenceProvider
from team_b.adapters.standins.knowledge_admin import StandinKnowledgeAdmin
from team_b.adapters.standins.policy_search import PolicySearchStandin
from team_b.adapters.standins.rule_checker import RuleCheckerStandin
from team_b.adapters.standins.safety_screen import SafetyScreenStandin
from team_b.adapters.standins.shop import StandinShop
from team_b.adapters.team_a_admin import TeamAAdmin
from team_b.adapters.team_a_http import HttpEvidenceProvider, HttpPolicyGate, TeamAClient
from team_b.adapters.team_c_tools import TeamCCapabilities, TeamCConfig
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
from team_b.bundles import BundleRegistry
from team_b.config import Settings
from team_b.contracts.errors import UpstreamError
from team_b.domain.bundle import TenantBundle
from team_b.domain.tenant import TenantRegistry
from team_b.events import EventHub
from team_b.ports import (
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
from team_b.preview import PreviewTokens


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
    team_c: TeamCCapabilities | None = None  # Team C's sandbox tools (TEAM_B_TEAM_C_CONFIG), wrapping `shop`
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
        raise ContainerError("TEAM_B_LLM_REWRITE=1 needs an AI model: set TEAM_B_LLM=ollama or openrouter.")

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
    shop = StandinShop(settings.fixtures_dir, clock, tenants.tenant_ids(), db_dir=shops_dir)
    # Team C serves the tools its config binds (sandbox publications over MCP); the stand-in serves the rest.
    team_c = TeamCCapabilities(shop, TeamCConfig.load(settings.team_c_config)) if settings.team_c_config else None
    capabilities: CapabilityClient = team_c if team_c is not None else shop
    if bundles is not None and team_c is not None:
        bundles.add_listener(team_c.on_bundle_change)  # a new active bundle rebinds that business's tools
    live = settings.mode == "live"  # Team A is real (policy search, safety screen, rule checker)
    policy_search = None if live else PolicySearchStandin(settings.fixtures_dir)
    rule_checker = None if live else RuleCheckerStandin(settings.fixtures_dir)
    safety_screen = None if live else SafetyScreenStandin(settings.fixtures_dir)
    evidence: EvidenceProvider
    policy: PolicyGate
    if live:
        service_key = settings.team_a_key.get_secret_value() if settings.team_a_key is not None else None
        team_a = TeamAClient(settings.team_a_url, timeout_s=settings.team_a_timeout_s, service_key=service_key)
        evidence, policy = HttpEvidenceProvider(team_a), HttpPolicyGate(team_a)
        knowledge_admin: KnowledgeAdmin | None = (
            TeamAAdmin(settings.team_a_url, settings.team_a_admin_key.get_secret_value(), service_key=service_key)
            if settings.team_a_admin_key is not None
            else None
        )
    else:
        assert policy_search is not None and rule_checker is not None and safety_screen is not None
        evidence, policy = StandinEvidenceProvider(policy_search, safety_screen), rule_checker
        knowledge_admin = StandinKnowledgeAdmin()
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
