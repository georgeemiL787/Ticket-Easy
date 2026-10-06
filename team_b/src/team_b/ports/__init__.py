"""The plugs: interfaces the brain depends on.

The brain imports only these Protocols plus team_b.domain and team_b.contracts models. Implementations live in
team_b.adapters and are wired together in team_b.container. Every method is async. A failing outside service
raises team_b.contracts.errors.UpstreamError; the brain decides what to do about it (usually: hand off).
"""

from collections.abc import Collection, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import date, datetime
from typing import Any, Protocol, runtime_checkable

from team_b.contracts.evidence import Passage, PastTicketResult, RetrievalResult, RiskAssessment
from team_b.contracts.policy import CheckActionRequest, PolicyDecision
from team_b.contracts.tools import ToolCallRequest, ToolResult, ToolSpec
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.facts import Facts, FactsSummary
from team_b.domain.handoff import CaseStatus, HandoffCase
from team_b.domain.session import SessionState
from team_b.domain.trace import DecisionTrace

__all__ = [
    "AlreadyExistsError",
    "CapabilityClient",
    "CaseStore",
    "Clock",
    "EvidenceProvider",
    "LLMClient",
    "NotFoundError",
    "PolicyGate",
    "SessionConflictError",
    "SessionStore",
    "StaleSession",
    "StoreError",
    "TraceStore",
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
    """The optional AI model. The brain must work fully without it."""

    async def complete_json(
        self, *, system: str, user: str, schema_hint: dict[str, Any], temperature: float = 0.0
    ) -> dict[str, Any]: ...


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
class Clock(Protocol):
    """Time source. The brain never reads the real date directly, so tests can fix it."""

    def today(self) -> date: ...

    def now(self) -> datetime: ...
