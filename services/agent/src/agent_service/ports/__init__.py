"""The plugs: interfaces the core depends on.

The core imports only these Protocols plus agent_service.domain and agent_service.contracts models. Implementations
live in agent_service.adapters and are wired together in agent_service.container. Every method is async. A failing
outside service raises agent_service.contracts.errors.UpstreamError; the core decides what to do about it (usually:
hand off).
"""

from collections.abc import Collection, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from agent_service.contracts.evidence import Passage, PastTicketResult, RetrievalResult, RiskAssessment
from agent_service.contracts.policy import CheckActionRequest, PolicyDecision
from agent_service.contracts.tools import ToolCallRequest, ToolResult, ToolSpec
from agent_service.domain.agent import AgentPlan, AgentView, AnyMove, MoveProposal
from agent_service.domain.alerts import Alert
from agent_service.domain.bundle import BundleStatus, TenantBundle
from agent_service.domain.decision import Decision, EscalationReason
from agent_service.domain.facts import Facts, FactsSummary
from agent_service.domain.handoff import CaseStatus, HandoffCase
from agent_service.domain.onboarding import OnboardingDraft
from agent_service.domain.session import SessionState
from agent_service.domain.trace import DecisionTrace
from agent_service.domain.users import User

__all__ = [
    "AlertStore",
    "AlreadyExistsError",
    "BundleStore",
    "CapabilityClient",
    "CaseStore",
    "Clock",
    "DraftStore",
    "EvidenceProvider",
    "KnowledgeAdmin",
    "LLMClient",
    "Planner",
    "NotFoundError",
    "PolicyGate",
    "SessionConflictError",
    "SessionStore",
    "StaleSession",
    "StoreError",
    "TraceStore",
    "UserStore",
]


class StoreError(Exception):
    """Base class for storage problems that are not an outside-service failure."""


class SessionConflictError(StoreError):
    """The session was saved by someone else since it was loaded (its version is stale)."""


StaleSession = SessionConflictError  # the same error under its other name


class AlreadyExistsError(StoreError):
    """An item with this id is already stored."""


class NotFoundError(StoreError):
    """The item to update does not exist."""


@runtime_checkable
class EvidenceProvider(Protocol):
    """Policy search and the safety screen: what the shop policies say, and whether a message is risky."""

    async def search_knowledge(
        self,
        tenant_id: str,
        query: str,
        *,
        request_id: str,
        conversation_id: str | None = None,
        top_k: int = 5,
    ) -> RetrievalResult: ...

    async def get_passage(self, tenant_id: str, citation: str) -> Passage | None: ...

    async def search_past_tickets(
        self, tenant_id: str, query: str, *, request_id: str, top_k: int = 3
    ) -> PastTicketResult: ...

    async def classify_risk(
        self, tenant_id: str, message: str, *, request_id: str, conversation_id: str | None = None
    ) -> RiskAssessment: ...


@runtime_checkable
class PolicyGate(Protocol):
    """The rule checker: may this action happen? Yes, no, or only with a human."""

    async def check_action(self, request: CheckActionRequest) -> PolicyDecision: ...


@runtime_checkable
class CapabilityClient(Protocol):
    """The shop actions: which tools exist, and calling one."""

    async def list_tools(self, tenant_id: str) -> list[ToolSpec]: ...

    async def call_tool(self, tenant_id: str, request: ToolCallRequest) -> ToolResult: ...


@runtime_checkable
class LLMClient(Protocol):
    """The optional AI model. The core must work fully without it."""

    async def complete_json(
        self, *, system: str, user: str, schema_hint: dict[str, Any], temperature: float = 0.0
    ) -> dict[str, Any]: ...


@runtime_checkable
class Planner(Protocol):
    """The planner: proposes the next move (or a changed plan). It only proposes; code checks and decides."""

    async def next_move(self, view: AgentView) -> AnyMove | AgentPlan | MoveProposal: ...


@runtime_checkable
class SessionStore(Protocol):
    """Conversation memory. save() is optimistic: it raises SessionConflictError when the stored version differs."""

    async def load(self, tenant_id: str, conversation_id: str) -> SessionState | None: ...

    async def save(self, session: SessionState) -> None: ...

    def lock(self, tenant_id: str, conversation_id: str) -> AbstractAsyncContextManager[None]:
        """Async context manager: messages of one conversation are handled one at a time."""
        ...

    async def purge_older_than(self, cutoff: datetime, *, keep: Collection[tuple[str, str]] = ()) -> int:
        """Delete sessions last updated before cutoff, except those in keep."""
        ...


@runtime_checkable
class TraceStore(Protocol):
    """Decision log."""

    async def add(self, trace: DecisionTrace) -> None: ...

    async def get(self, tenant_id: str, trace_id: str) -> DecisionTrace | None: ...

    async def for_conversation(self, tenant_id: str, conversation_id: str) -> list[DecisionTrace]:
        """Oldest first."""
        ...

    async def purge_older_than(self, cutoff: datetime, *, keep: Collection[tuple[str, str]] = ()) -> int:
        """Delete traces stored before cutoff, except those of the (tenant, conversation) pairs in keep."""
        ...

    async def facts(self, tenant_id: str, start: datetime, end: datetime) -> Facts:
        """The summary rows written with the traces stored in [start, end), oldest first. They outlive purged traces."""
        ...

    async def summary(self, tenant_id: str, start: datetime, end: datetime) -> FactsSummary:
        """Headline counts of [start, end) without loading every row: fast on a large database."""
        ...

    async def query(
        self,
        tenant_id: str,
        *,
        conversation_id: str | None = None,
        decision: Decision | None = None,
        escalation_reason: EscalationReason | None = None,
        limit: int = 100,
    ) -> list[DecisionTrace]:
        """Newest first."""
        ...


@runtime_checkable
class CaseStore(Protocol):
    """Handoff cases."""

    async def add(self, case: HandoffCase) -> None: ...

    async def get(self, tenant_id: str, case_id: str) -> HandoffCase | None: ...

    async def save(self, case: HandoffCase) -> None: ...

    async def list(self, tenant_id: str, *, status: CaseStatus | None = None) -> Sequence[HandoffCase]:
        """Oldest first. The inbox sorts by priority."""
        ...

    async def purge_older_than(self, cutoff: datetime) -> int:
        """Delete resolved or returned cases last updated before cutoff. Open and claimed cases are never deleted."""
        ...


@runtime_checkable
class AlertStore(Protocol):
    """Alerts. At most one alert is open per (tenant, rule, key): add() of a second one raises AlreadyExistsError."""

    async def add(self, alert: Alert) -> None: ...

    async def get(self, tenant_id: str, alert_id: str) -> Alert | None: ...

    async def save(self, alert: Alert) -> None: ...

    async def list(self, tenant_id: str, *, open_only: bool = False, limit: int = 200) -> Sequence[Alert]:
        """Newest first."""
        ...


@runtime_checkable
class UserStore(Protocol):
    """People who sign in. Emails and display names are unique without regard to case (AlreadyExistsError otherwise)."""

    async def create(self, user: User, password_hash: str) -> None: ...

    async def get(self, user_id: str) -> User | None: ...

    async def get_with_hash(self, email: str) -> tuple[User, str] | None: ...

    async def set_password(self, user_id: str, password_hash: str) -> None: ...

    async def set_active(self, user_id: str, active: bool) -> None: ...

    async def list(self) -> Sequence[User]: ...


@runtime_checkable
class BundleStore(Protocol):
    """Versioned tenant bundles. A stored bundle's content never changes: only its status does (draft -> published ->
    active -> retired). At most one bundle per business is active."""

    async def add(self, bundle: TenantBundle) -> None:
        """Store a new version (AlreadyExistsError if tenant_id + version is taken)."""
        ...

    async def get(self, tenant_id: str, version: int) -> TenantBundle | None: ...

    async def list(self, tenant_id: str) -> Sequence[TenantBundle]:
        """Every version of this business, newest first."""
        ...

    async def latest_version(self, tenant_id: str) -> int:
        """The highest stored version number (0 when there is none)."""
        ...

    async def set_status(self, tenant_id: str, version: int, status: BundleStatus) -> TenantBundle: ...

    async def activate(self, tenant_id: str, version: int, actor: str, at: datetime) -> TenantBundle:
        """Make this version the active one; the previously active version becomes published again."""
        ...

    async def active(self, tenant_id: str) -> TenantBundle | None: ...

    async def all_active(self) -> Sequence[TenantBundle]: ...

    async def latest(self, tenant_id: str) -> TenantBundle | None:
        """The highest version, whatever its status."""
        ...

    async def all_latest(self) -> Sequence[TenantBundle]:
        """The highest version of every business."""
        ...


@runtime_checkable
class DraftStore(Protocol):
    """The onboarding wizard's state, one draft per business being set up."""

    async def get(self, tenant_id: str) -> OnboardingDraft | None: ...

    async def save(self, draft: OnboardingDraft) -> None: ...

    async def list(self) -> Sequence[OnboardingDraft]: ...

    async def delete(self, tenant_id: str) -> None: ...


@runtime_checkable
class KnowledgeAdmin(Protocol):
    """Documents, the index and the rules of a business, for the dashboard's knowledge pages (never used by the core).
    A refusal (an unreadable file, a rule that would break a guardrail case) is an UpstreamError with the reason."""

    async def upload(
        self, tenant_id: str, filename: str, content: bytes, document_id: str | None = None
    ) -> dict[str, Any]: ...

    async def documents(self, tenant_id: str) -> dict[str, Any]: ...

    async def remove(self, tenant_id: str, document_id: str) -> dict[str, Any]: ...

    async def reindex(self, tenant_id: str) -> dict[str, Any]: ...

    async def job(self, job_id: str) -> dict[str, Any]: ...

    async def extract_rules(self, tenant_id: str, document_id: str | None = None) -> dict[str, Any]: ...

    async def rules(self, tenant_id: str) -> dict[str, Any]: ...

    async def approve_rule(self, tenant_id: str, rule_id: str, reviewer: str) -> dict[str, Any]: ...

    async def reject_rule(self, tenant_id: str, rule_id: str, reviewer: str) -> dict[str, Any]: ...

    async def edit_rule(self, tenant_id: str, rule_id: str, changes: dict[str, Any]) -> dict[str, Any]: ...

    async def ruleset(self, tenant_id: str) -> dict[str, Any]: ...

    async def snapshot(self, tenant_id: str) -> dict[str, Any]: ...


@runtime_checkable
class Clock(Protocol):
    """Time source. The core never reads the real date directly, so tests can fix it."""

    def today(self) -> date: ...

    def now(self) -> datetime: ...
