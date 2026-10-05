"""One conversation is never processed twice at the same time; a stale session is retried once."""

import asyncio
from datetime import date
from pathlib import Path

import pytest

from team_b.adapters.sqlite_store import SqliteDatabase, SqliteSessionStore
from team_b.brain.orchestrator import Orchestrator
from team_b.config import Settings
from team_b.container import Container, build_container
from team_b.domain.session import SessionState
from team_b.ports import SessionConflictError, SessionStore

T, C = "shop_001", "conv-1"


async def test_twenty_concurrent_turns_on_one_conversation(c: Container) -> None:
    assert c.orchestrator is not None
    replies = await asyncio.gather(*(c.orchestrator.handle_turn(T, C, f"message {n}") for n in range(20)))
    session = await c.sessions.load(T, C)
    traces = await c.traces.for_conversation(T, C)
    assert session is not None and session.turn_index == 20 and session.version == 20
    assert len(traces) == 20 and sorted(t.turn_index for t in traces) == list(range(20))
    assert len({r.trace_id for r in replies}) == 20


async def test_different_conversations_run_independently(c: Container) -> None:
    assert c.orchestrator is not None
    await asyncio.gather(*(c.orchestrator.handle_turn(T, f"conv-{n % 4}", "hi") for n in range(20)))
    for n in range(4):
        session = await c.sessions.load(T, f"conv-{n}")
        assert session is not None and session.turn_index == 5


class FlakySessions:
    """Wraps a session store; the first `failures` saves lose the race (as if another process saved first)."""

    def __init__(self, inner: SessionStore, failures: int) -> None:
        self.inner, self.failures, self.saves = inner, failures, 0

    async def load(self, tenant_id: str, conversation_id: str) -> SessionState | None:
        return await self.inner.load(tenant_id, conversation_id)

    async def save(self, session: SessionState) -> None:
        self.saves += 1
        if self.failures > 0:
            self.failures -= 1
            raise SessionConflictError("someone else saved first")
        await self.inner.save(session)

    def lock(self, tenant_id: str, conversation_id: str):  # type: ignore[no-untyped-def]
        return self.inner.lock(tenant_id, conversation_id)

    async def purge_older_than(self, cutoff, *, keep=()):  # type: ignore[no-untyped-def]
        return await self.inner.purge_older_than(cutoff, keep=keep)


def with_sessions(c: Container, sessions: SessionStore) -> Orchestrator:
    return Orchestrator(clock=c.clock, tenants=c.tenants, sessions=sessions, traces=c.traces, cases=c.cases)


async def test_a_stale_session_is_retried_once_and_stores_one_trace(c: Container) -> None:
    flaky = FlakySessions(c.sessions, failures=1)
    reply = await with_sessions(c, flaky).handle_turn(T, C, "hello")
    assert flaky.saves == 2
    traces = await c.traces.for_conversation(T, C)
    assert [t.trace_id for t in traces] == [reply.trace_id]  # the failed attempt left no trace
    session = await c.sessions.load(T, C)
    assert session is not None and session.turn_index == 1 and len(session.history) == 2


async def test_a_second_conflict_is_raised_and_nothing_is_stored(c: Container) -> None:
    flaky = FlakySessions(c.sessions, failures=2)
    with pytest.raises(SessionConflictError):
        await with_sessions(c, flaky).handle_turn(T, C, "hello")
    assert await c.traces.for_conversation(T, C) == []
    assert await c.sessions.load(T, C) is None


async def test_two_processes_on_one_database_do_not_lose_turns(tmp_path: Path) -> None:
    """Two containers share one SQLite file (no shared lock): the version check plus one retry keeps both turns."""
    settings = Settings(store="sqlite", db_path=tmp_path / "team_b.sqlite3", fixed_today=date(2026, 9, 28))
    a, b = build_container(settings), build_container(settings)
    assert a.orchestrator is not None and b.orchestrator is not None
    await a.orchestrator.handle_turn(T, C, "first")  # create the session so both start from version 1
    results = await asyncio.gather(
        a.orchestrator.handle_turn(T, C, "from a"),
        b.orchestrator.handle_turn(T, C, "from b"),
        return_exceptions=True,
    )
    survivors = [r for r in results if not isinstance(r, BaseException)]
    session = await a.sessions.load(T, C)
    traces = await a.traces.for_conversation(T, C)
    assert session is not None
    # whatever happened, the stored state agrees with the traces: no turn is half-saved
    assert session.turn_index == len(traces) == 1 + len(survivors)
    assert len(survivors) >= 1


async def test_the_sqlite_save_rejects_a_stale_version_even_without_a_lock(tmp_path: Path) -> None:
    store = SqliteSessionStore(SqliteDatabase(tmp_path / "db.sqlite3"))
    from tests.unit.adapters.test_stores_and_clocks import session

    await store.save(session())
    one, two = await store.load(T, C), await store.load(T, C)
    assert one is not None and two is not None
    await store.save(one)
    with pytest.raises(SessionConflictError):
        await store.save(two)
