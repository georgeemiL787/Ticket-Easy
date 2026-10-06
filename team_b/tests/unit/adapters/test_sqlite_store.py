"""What is specific to SQLite: migrations, persistence across restarts, JSON round trips, safe SQL."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from team_b.adapters.memory_store import FixedClock
from team_b.adapters.sqlite_store import (
    SqliteCaseStore,
    SqliteDatabase,
    SqliteSessionStore,
    SqliteTraceStore,
    migration_files,
)
from team_b.config import Settings
from team_b.container import build_container
from team_b.domain.decision import Decision
from team_b.domain.handoff import CaseStatus
from team_b.domain.trace import DecisionTrace
from tests.unit.adapters.test_trace_case_stores import case, trace

T, C = "shop_001", "conv-1"


def tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as db:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


async def test_first_use_creates_the_schema_and_records_the_version(sqlite_db: SqliteDatabase) -> None:
    assert not sqlite_db.path.exists()
    assert await sqlite_db.schema_version() == 2
    assert {"sessions", "traces", "cases", "schema_version", "turn_facts", "tool_call_facts", "policy_facts"} <= tables(
        sqlite_db.path
    )


async def test_migrations_run_once_and_are_safe_to_repeat(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    await SqliteDatabase(path).schema_version()
    again = SqliteDatabase(path)  # a new process opening the same file
    assert await again.schema_version() == 2
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM schema_version").fetchone() == (2,)


async def test_the_database_folder_is_created(tmp_path: Path) -> None:
    db = SqliteDatabase(tmp_path / "var" / "nested" / "team_b.sqlite3")
    assert await db.schema_version() == 2


def test_migration_files_are_numbered_without_gaps(tmp_path: Path) -> None:
    assert [v for v, _ in migration_files()] == [1, 2]
    (tmp_path / "001_a.sql").write_text("SELECT 1;")
    (tmp_path / "003_c.sql").write_text("SELECT 1;")
    with pytest.raises(RuntimeError, match="without gaps"):
        migration_files(tmp_path)


async def test_a_later_migration_is_applied_on_top(tmp_path: Path) -> None:
    from team_b.adapters import sqlite_store

    folder = tmp_path / "migrations"
    folder.mkdir()
    for name in ("001_init.sql", "002_metric_facts.sql"):
        (folder / name).write_text((sqlite_store.MIGRATIONS_DIR / name).read_text(encoding="utf-8"))
    db_path = tmp_path / "db.sqlite3"
    async with SqliteDatabase(db_path).connect() as db:
        await sqlite_store.apply_migrations(db, folder)
    (folder / "003_extra.sql").write_text("CREATE TABLE extra (x INTEGER);")
    async with SqliteDatabase(db_path).connect() as db:
        assert await sqlite_store.apply_migrations(db, folder) == 3
    assert "extra" in tables(db_path)


async def test_data_survives_a_new_database_object_on_the_same_file(tmp_path: Path) -> None:
    path = tmp_path / "db.sqlite3"
    clock = FixedClock(date(2026, 9, 28))
    first = SqliteDatabase(path)
    await SqliteTraceStore(first, clock).add(trace("t1"))
    await SqliteCaseStore(first).add(case("k1"))
    second = SqliteDatabase(path)
    assert await SqliteTraceStore(second, clock).get(T, "t1") == trace("t1")
    assert (await SqliteCaseStore(second).get(T, "k1")) is not None
    assert await SqliteSessionStore(second).load(T, C) is None


async def test_traces_keep_their_type_after_a_json_round_trip(sqlite_db: SqliteDatabase) -> None:
    store = SqliteTraceStore(sqlite_db, FixedClock(date(2026, 9, 28)))
    original = trace("t1", decision=Decision.ANSWER, response_citations=("return_policy@v2#s2",), language="ar")
    await store.add(original)
    loaded = await store.get(T, "t1")
    assert isinstance(loaded, DecisionTrace) and loaded == original
    assert loaded.response_citations == ("return_policy@v2#s2",)


async def test_case_status_changes_survive_a_save(sqlite_db: SqliteDatabase) -> None:
    from tests.unit.adapters.test_trace_case_stores import NOW

    store = SqliteCaseStore(sqlite_db)
    item = case("k1")
    await store.add(item)
    item.transition(CaseStatus.CLAIMED, actor="agent-1", at=NOW)
    await store.save(item)
    loaded = await store.get(T, "k1")
    assert loaded is not None and loaded.status is CaseStatus.CLAIMED and loaded.claimed_by == "agent-1"
    assert [c.case_id for c in await store.list(T, status=CaseStatus.CLAIMED)] == ["k1"]
    assert await store.list(T, status=CaseStatus.OPEN) == []


async def test_values_are_never_spliced_into_sql(sqlite_db: SqliteDatabase) -> None:
    store = SqliteTraceStore(sqlite_db, FixedClock(date(2026, 9, 28)))
    await store.add(trace("t1"))
    nasty = "x' OR '1'='1"
    assert await store.get(T, nasty) is None
    assert await store.for_conversation(T, nasty) == []
    assert await store.query(T, conversation_id=nasty) == []
    assert await SqliteSessionStore(sqlite_db).load(T, nasty) is None
    assert len(await store.query(T)) == 1  # the table is intact


async def test_a_failed_save_leaves_the_stored_session_untouched(sqlite_db: SqliteDatabase) -> None:
    from tests.unit.adapters.test_stores_and_clocks import session

    store = SqliteSessionStore(sqlite_db)
    s = session()
    await store.save(s)
    stale = session()
    stale.slots["x"] = "1"
    with pytest.raises(Exception, match="version"):
        await store.save(stale)
    loaded = await store.load(T, C)
    assert loaded is not None and loaded.slots == {} and loaded.version == 1


async def test_a_conversation_continues_after_the_container_is_rebuilt(tmp_path: Path) -> None:
    settings = Settings(store="sqlite", db_path=tmp_path / "team_b.sqlite3", fixed_today=date(2026, 9, 28))
    first = build_container(settings)
    assert first.orchestrator is not None
    await first.orchestrator.handle_turn(T, C, "hello")
    await first.orchestrator.handle_turn(T, C, "where is my order")

    restarted = build_container(settings)  # a new process, same file
    assert restarted.orchestrator is not None and restarted.sessions is not first.sessions
    reply = await restarted.orchestrator.handle_turn(T, C, "NS-20877")
    session = await restarted.sessions.load(T, C)
    assert session is not None and session.turn_index == 3 and len(session.history) == 6
    assert [t.turn_index for t in await restarted.traces.for_conversation(T, C)] == [0, 1, 2]
    assert reply.trace_id == (await restarted.traces.for_conversation(T, C))[-1].trace_id


def test_the_container_picks_the_store_from_settings(tmp_path: Path) -> None:
    sqlite = build_container(Settings(store="sqlite", db_path=tmp_path / "a.sqlite3"))
    assert isinstance(sqlite.sessions, SqliteSessionStore) and isinstance(sqlite.traces, SqliteTraceStore)
    assert isinstance(sqlite.cases, SqliteCaseStore)
    memory = build_container(Settings(store="memory"))
    assert not isinstance(memory.sessions, SqliteSessionStore)
