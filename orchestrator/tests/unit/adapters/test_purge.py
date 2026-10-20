"""purge_older_than on every store (memory and sqlite)."""

from datetime import timedelta

from team_b.adapters.memory_store import FixedClock, InMemoryTraceStore
from team_b.adapters.sqlite_store import SqliteDatabase, SqliteTraceStore
from team_b.domain.handoff import CaseStatus
from team_b.ports import CaseStore, SessionStore, TraceStore
from tests.unit.adapters.test_stores_and_clocks import NOW, session
from tests.unit.adapters.test_trace_case_stores import case, trace


async def test_sessions_older_than_the_cutoff_are_deleted(session_store: SessionStore) -> None:
    await session_store.save(session(conversation="old"))
    fresh = session(conversation="fresh")
    fresh.updated_at = NOW + timedelta(days=10)
    await session_store.save(fresh)
    assert await session_store.purge_older_than(NOW + timedelta(days=5)) == 1
    assert await session_store.load("shop_001", "old") is None
    assert await session_store.load("shop_001", "fresh") is not None


async def test_kept_conversations_survive_a_session_purge(session_store: SessionStore) -> None:
    await session_store.save(session(conversation="a"))
    await session_store.save(session(conversation="b"))
    removed = await session_store.purge_older_than(NOW + timedelta(days=1), keep={("shop_001", "a")})
    assert removed == 1
    assert await session_store.load("shop_001", "a") is not None
    assert await session_store.load("shop_001", "b") is None


async def test_purge_with_nothing_old_deletes_nothing(session_store: SessionStore) -> None:
    await session_store.save(session())
    assert await session_store.purge_older_than(NOW - timedelta(days=1)) == 0
    assert await session_store.load("shop_001", "conv-1") is not None


async def test_traces_are_purged_by_the_time_they_were_stored(store_kind: str, sqlite_db: SqliteDatabase) -> None:
    clock = FixedClock(NOW)
    store: TraceStore = InMemoryTraceStore(clock) if store_kind == "memory" else SqliteTraceStore(sqlite_db, clock)
    await store.add(trace("old", conversation="a"))
    await store.add(trace("old-kept", conversation="kept"))
    clock.advance(30)
    await store.add(trace("new", conversation="a"))
    cutoff = NOW + timedelta(days=10)
    assert await store.purge_older_than(cutoff, keep={("shop_001", "kept")}) == 1
    assert await store.get("shop_001", "old") is None
    assert await store.get("shop_001", "old-kept") is not None
    assert [t.trace_id for t in await store.for_conversation("shop_001", "a")] == ["new"]
    assert [t.trace_id for t in await store.query("shop_001")] == ["new", "old-kept"]


async def test_only_finished_cases_are_purged(case_store: CaseStore) -> None:
    resolved, returned, claimed, open_case = case("k1"), case("k2"), case("k3"), case("k4")
    for c in (resolved, returned, claimed, open_case):
        await case_store.add(c)
    resolved.transition(CaseStatus.RESOLVED, actor="a", at=NOW)
    claimed.transition(CaseStatus.CLAIMED, actor="a", at=NOW)
    returned.transition(CaseStatus.CLAIMED, actor="a", at=NOW)
    returned.transition(CaseStatus.RETURNED_TO_AGENT, actor="a", at=NOW)
    for c in (resolved, returned, claimed):
        await case_store.save(c)

    assert await case_store.purge_older_than(NOW + timedelta(days=365)) == 2  # resolved and returned
    left = sorted(c.case_id for c in await case_store.list("shop_001"))
    assert left == ["k3", "k4"]  # claimed and open stay, however old


async def test_a_recent_finished_case_is_kept(case_store: CaseStore) -> None:
    done = case("k1")
    await case_store.add(done)
    done.transition(CaseStatus.RESOLVED, actor="a", at=NOW)
    await case_store.save(done)
    assert await case_store.purge_older_than(NOW - timedelta(days=1)) == 0
