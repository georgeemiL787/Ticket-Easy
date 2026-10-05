"""Store fixtures: every store test runs against the in-memory and the SQLite implementation."""

from pathlib import Path

import pytest

from team_b.adapters.memory_store import FixedClock, InMemoryCaseStore, InMemorySessionStore, InMemoryTraceStore
from team_b.adapters.sqlite_store import SqliteCaseStore, SqliteDatabase, SqliteSessionStore, SqliteTraceStore
from team_b.ports import CaseStore, SessionStore, TraceStore
from tests.conftest import FIXED_TODAY


@pytest.fixture(params=["memory", "sqlite"])
def store_kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def sqlite_db(tmp_path: Path) -> SqliteDatabase:
    return SqliteDatabase(tmp_path / "stores.sqlite3")


@pytest.fixture
def session_store(store_kind: str, sqlite_db: SqliteDatabase) -> SessionStore:
    return InMemorySessionStore() if store_kind == "memory" else SqliteSessionStore(sqlite_db)


@pytest.fixture
def trace_store(store_kind: str, sqlite_db: SqliteDatabase) -> TraceStore:
    return InMemoryTraceStore() if store_kind == "memory" else SqliteTraceStore(sqlite_db, FixedClock(FIXED_TODAY))


@pytest.fixture
def case_store(store_kind: str, sqlite_db: SqliteDatabase) -> CaseStore:
    return InMemoryCaseStore() if store_kind == "memory" else SqliteCaseStore(sqlite_db)
