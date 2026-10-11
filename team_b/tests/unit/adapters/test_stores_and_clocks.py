import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from team_b.adapters.memory_store import (
    FixedClock,
    InMemoryCaseStore,
    InMemorySessionStore,
    InMemoryTraceStore,
    SystemClock,
)
from team_b.domain.session import SessionState
from team_b.ports import CaseStore, Clock, SessionConflictError, SessionStore, TraceStore

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def session(tenant: str = "shop_001", conversation: str = "conv-1") -> SessionState:
    return SessionState(tenant_id=tenant, conversation_id=conversation, created_at=NOW, updated_at=NOW)


def test_stores_and_clocks_satisfy_the_ports() -> None:
    assert isinstance(InMemorySessionStore(), SessionStore)
    assert isinstance(InMemoryTraceStore(), TraceStore)
    assert isinstance(InMemoryCaseStore(), CaseStore)
    assert isinstance(SystemClock(), Clock) and isinstance(FixedClock(date(2026, 9, 28)), Clock)


# --- clocks ---


def test_fixed_clock_from_a_date() -> None:
    clock = FixedClock(date(2026, 9, 28))
    assert clock.today() == date(2026, 9, 28)
    assert clock.now() == datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
    assert clock.now() == clock.now()  # it never moves


def test_fixed_clock_from_a_datetime_adds_utc_when_naive() -> None:
    assert FixedClock(datetime(2026, 9, 28, 8, 30)).now() == datetime(2026, 9, 28, 8, 30, tzinfo=UTC)
    assert FixedClock(NOW).now() == NOW


def test_system_clock_is_timezone_aware_and_current() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert abs(datetime.now(UTC) - now) < timedelta(seconds=5)


# --- session store ---


async def test_session_roundtrip_and_missing(session_store: SessionStore) -> None:
    store = session_store
    assert await store.load("shop_001", "conv-1") is None
    s = session()
    s.slots["order_id"] = "NS-20877"
    await store.save(s)
    loaded = await store.load("shop_001", "conv-1")
    assert loaded is not None and loaded.slots == {"order_id": "NS-20877"}


async def test_save_bumps_the_version(session_store: SessionStore) -> None:
    store = session_store
    s = session()
    assert s.version == 0
    await store.save(s)
    assert s.version == 1
    await store.save(s)
    assert s.version == 2
    loaded = await store.load("shop_001", "conv-1")
    assert loaded is not None and loaded.version == 2


async def test_stale_version_is_a_conflict(session_store: SessionStore) -> None:
    store = session_store
    await store.save(session())
    first = await store.load("shop_001", "conv-1")
    second = await store.load("shop_001", "conv-1")
    assert first is not None and second is not None
    first.turn_index = 1
    await store.save(first)
    second.turn_index = 5
    with pytest.raises(SessionConflictError):
        await store.save(second)
    stored = await store.load("shop_001", "conv-1")
    assert stored is not None and stored.turn_index == 1  # the loser did not overwrite


async def test_a_new_session_with_a_version_is_a_conflict(session_store: SessionStore) -> None:
    s = session()
    s.version = 4
    with pytest.raises(SessionConflictError):
        await session_store.save(s)


async def test_load_returns_a_copy_so_edits_do_not_change_the_store(session_store: SessionStore) -> None:
    store = session_store
    await store.save(session())
    loaded = await store.load("shop_001", "conv-1")
    assert loaded is not None
    loaded.slots["phone"] = "01012345678"
    loaded.history_summary = "edited"
    again = await store.load("shop_001", "conv-1")
    assert again is not None and again.slots == {} and again.history_summary == ""


async def test_saving_stores_a_copy_too(session_store: SessionStore) -> None:
    store = session_store
    s = session()
    await store.save(s)
    s.slots["phone"] = "x"  # edit after saving, without saving again
    again = await store.load("shop_001", "conv-1")
    assert again is not None and again.slots == {}


async def test_sessions_are_separated_by_tenant_and_conversation(session_store: SessionStore) -> None:
    store = session_store
    await store.save(session("shop_001", "conv-1"))
    assert await store.load("shop_002", "conv-1") is None
    assert await store.load("shop_001", "conv-2") is None


async def test_lock_runs_one_message_at_a_time_per_conversation(session_store: SessionStore) -> None:
    store = session_store
    await store.save(session())
    running = 0
    peak = 0
    order: list[int] = []

    async def message(n: int) -> None:
        nonlocal running, peak
        async with store.lock("shop_001", "conv-1"):
            running += 1
            peak = max(peak, running)
            loaded = await store.load("shop_001", "conv-1")
            assert loaded is not None
            await asyncio.sleep(0)  # give the others a chance to barge in
            loaded.turn_index += 1
            await store.save(loaded)  # would raise SessionConflictError if two ran together
            order.append(n)
            running -= 1

    await asyncio.gather(*(message(n) for n in range(20)))
    assert peak == 1
    assert order == list(range(20))  # handled in the order they arrived
    final = await store.load("shop_001", "conv-1")
    assert final is not None and final.turn_index == 20 and final.version == 21


async def test_without_the_lock_concurrent_writers_conflict(session_store: SessionStore) -> None:
    store = session_store
    await store.save(session())

    async def message() -> None:
        loaded = await store.load("shop_001", "conv-1")
        assert loaded is not None
        await asyncio.sleep(0)
        await store.save(loaded)

    results = await asyncio.gather(*(message() for _ in range(5)), return_exceptions=True)
    assert any(isinstance(r, SessionConflictError) for r in results)


async def test_different_conversations_do_not_block_each_other(session_store: SessionStore) -> None:
    store = session_store
    entered = asyncio.Event()
    release = asyncio.Event()

    async def holder() -> None:
        async with store.lock("shop_001", "conv-1"):
            entered.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await entered.wait()
    async with asyncio.timeout(1):  # would time out if the locks were shared
        async with store.lock("shop_001", "conv-2"):
            pass
        async with store.lock("shop_002", "conv-1"):
            pass
    release.set()
    await task


async def test_lock_is_released_after_an_error(session_store: SessionStore) -> None:
    store = session_store
    with pytest.raises(RuntimeError):
        async with store.lock("shop_001", "conv-1"):
            raise RuntimeError("boom")
    async with asyncio.timeout(1):
        async with store.lock("shop_001", "conv-1"):
            pass
