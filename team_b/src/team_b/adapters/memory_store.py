"""In-memory stores and clocks: used by tests and by the light demo mode. Nothing survives a restart."""

import asyncio
from collections.abc import AsyncIterator, Collection, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta

from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.handoff import CaseStatus, HandoffCase
from team_b.domain.session import SessionState
from team_b.domain.trace import DecisionTrace
from team_b.ports import AlreadyExistsError, Clock, NotFoundError, SessionConflictError


class SystemClock:
    """The real time, in UTC."""

    def today(self) -> date:
        return self.now().date()

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """A clock that always says the same moment. Give it a date (noon UTC) or a full datetime."""

    def __init__(self, value: date | datetime) -> None:
        if isinstance(value, datetime):
            self._now = value if value.tzinfo else value.replace(tzinfo=UTC)
        else:
            self._now = datetime.combine(value, time(12, 0), tzinfo=UTC)

    def today(self) -> date:
        return self._now.date()

    def now(self) -> datetime:
        return self._now

    def advance(self, days: int) -> None:
        """Move the clock forward (scenarios use this for 'three days later'). Going back is not allowed."""
        if days < 0:
            raise ValueError("a clock only moves forward")
        self._now += timedelta(days=days)


class InMemorySessionStore:
    """Sessions keyed by (tenant, conversation). load() returns a copy, so editing it never changes the store."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], SessionState] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def load(self, tenant_id: str, conversation_id: str) -> SessionState | None:
        stored = self._sessions.get((tenant_id, conversation_id))
        return stored.model_copy(deep=True) if stored is not None else None

    async def save(self, session: SessionState) -> None:
        """Store a copy and bump the version. A stale version means someone else saved first."""
        key = (session.tenant_id, session.conversation_id)
        stored = self._sessions.get(key)
        current = stored.version if stored is not None else 0
        if session.version != current:
            raise SessionConflictError(
                f"session {session.conversation_id} has version {session.version}, store has {current}"
            )
        session.version = current + 1
        self._sessions[key] = session.model_copy(deep=True)

    @asynccontextmanager
    async def lock(self, tenant_id: str, conversation_id: str) -> AsyncIterator[None]:
        lock = self._locks.setdefault((tenant_id, conversation_id), asyncio.Lock())
        async with lock:
            yield

    async def purge_older_than(self, cutoff: datetime, *, keep: Collection[tuple[str, str]] = ()) -> int:
        old = [key for key, s in self._sessions.items() if s.updated_at < cutoff and key not in keep]
        for key in old:
            del self._sessions[key]
        return len(old)


class InMemoryTraceStore:
    def __init__(self, clock: Clock | None = None) -> None:
        self._clock: Clock = clock or SystemClock()
        self._by_id: dict[tuple[str, str], DecisionTrace] = {}
        self._order: list[DecisionTrace] = []  # insertion order
        self._stored_at: dict[tuple[str, str], datetime] = {}

    async def add(self, trace: DecisionTrace) -> None:
        key = (trace.tenant_id, trace.trace_id)
        if key in self._by_id:
            raise AlreadyExistsError(f"trace {trace.trace_id} already stored")
        self._by_id[key] = trace
        self._order.append(trace)
        self._stored_at[key] = self._clock.now()

    async def get(self, tenant_id: str, trace_id: str) -> DecisionTrace | None:
        return self._by_id.get((tenant_id, trace_id))

    async def for_conversation(self, tenant_id: str, conversation_id: str) -> list[DecisionTrace]:
        return [t for t in self._order if t.tenant_id == tenant_id and t.conversation_id == conversation_id]

    async def query(
        self,
        tenant_id: str,
        *,
        conversation_id: str | None = None,
        decision: Decision | None = None,
        escalation_reason: EscalationReason | None = None,
        limit: int = 100,
    ) -> list[DecisionTrace]:
        found = [
            t
            for t in reversed(self._order)
            if t.tenant_id == tenant_id
            and (conversation_id is None or t.conversation_id == conversation_id)
            and (decision is None or t.decision is decision)
            and (escalation_reason is None or t.escalation_reason is escalation_reason)
        ]
        return found[:limit]

    async def purge_older_than(self, cutoff: datetime, *, keep: Collection[tuple[str, str]] = ()) -> int:
        old = {
            key
            for key, stored in self._stored_at.items()
            if stored < cutoff and (self._by_id[key].tenant_id, self._by_id[key].conversation_id) not in keep
        }
        self._order = [t for t in self._order if (t.tenant_id, t.trace_id) not in old]
        for key in old:
            del self._by_id[key]
            del self._stored_at[key]
        return len(old)


class InMemoryCaseStore:
    def __init__(self) -> None:
        self._cases: dict[tuple[str, str], HandoffCase] = {}

    async def add(self, case: HandoffCase) -> None:
        key = (case.tenant_id, case.case_id)
        if key in self._cases:
            raise AlreadyExistsError(f"case {case.case_id} already stored")
        self._cases[key] = case.model_copy(deep=True)

    async def get(self, tenant_id: str, case_id: str) -> HandoffCase | None:
        stored = self._cases.get((tenant_id, case_id))
        return stored.model_copy(deep=True) if stored is not None else None

    async def save(self, case: HandoffCase) -> None:
        key = (case.tenant_id, case.case_id)
        if key not in self._cases:
            raise NotFoundError(f"case {case.case_id} does not exist")
        self._cases[key] = case.model_copy(deep=True)

    async def list(self, tenant_id: str, *, status: CaseStatus | None = None) -> Sequence[HandoffCase]:
        found = [
            c.model_copy(deep=True)
            for c in self._cases.values()
            if c.tenant_id == tenant_id and (status is None or c.status is status)
        ]
        return sorted(found, key=lambda c: c.created_at)

    async def purge_older_than(self, cutoff: datetime) -> int:
        finished = (CaseStatus.RESOLVED, CaseStatus.RETURNED_TO_AGENT)
        old = [key for key, c in self._cases.items() if c.status in finished and c.updated_at < cutoff]
        for key in old:
            del self._cases[key]
        return len(old)
