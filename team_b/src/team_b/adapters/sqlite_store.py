"""SQLite storage for sessions, traces and handoff cases: what survives a restart.

Every operation opens its own short connection to the file, so nothing lingers between calls and no connection is shared
between conversations. Models are stored as JSON (model_dump_json / model_validate_json) next to a few indexed columns;
SQL is always parameterised. The schema is created and upgraded from numbered files in adapters/migrations/ the first
time the database is used.

Behaviour matches the in-memory stores (copies, conflicts, duplicate ids), so the same tests run against both.
"""

import asyncio
import re
import sqlite3
from collections import defaultdict
from collections.abc import AsyncIterator, Collection, Iterable, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.handoff import CaseStatus, HandoffCase
from team_b.domain.session import SessionState
from team_b.domain.trace import DecisionTrace
from team_b.ports import AlreadyExistsError, Clock, NotFoundError, SessionConflictError

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_FILE = re.compile(r"^(\d{3})_.+\.sql$")


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[tuple[int, Path]]:
    """(version, file) for every numbered .sql file, lowest first. A gap or a duplicate number is an error."""
    found = sorted((int(m.group(1)), p) for p in directory.glob("*.sql") if (m := _MIGRATION_FILE.match(p.name)))
    versions = [v for v, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError(f"migration files must be numbered 001, 002, ... without gaps, found {versions}")
    return found


async def apply_migrations(db: aiosqlite.Connection, directory: Path = MIGRATIONS_DIR) -> int:
    """Apply the migrations the database has not seen yet; returns the schema version afterwards."""
    await db.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = await (await db.execute("SELECT MAX(version) FROM schema_version")).fetchone()
    current = int(row[0]) if row is not None and row[0] is not None else 0
    for version, path in migration_files(directory):
        if version <= current:
            continue
        await db.executescript(
            f"BEGIN;\n{path.read_text(encoding='utf-8')}\nINSERT INTO schema_version VALUES ({version});\nCOMMIT;"
        )
        current = version
    return current


def _iso(moment: datetime) -> str:
    """Timestamps are stored as UTC ISO strings, which sort the same way as the moments they name."""
    return moment.astimezone(UTC).isoformat()


async def _delete_unless_kept(
    db: aiosqlite.Connection, table: str, rows: Iterable[Sequence[str]], keep: Collection[tuple[str, str]]
) -> int:
    """Delete rows (tenant_id, conversation_id, key) unless their conversation is in keep. table is fixed code."""
    doomed = [(t, k) for t, conversation, k in rows if (t, conversation) not in keep]
    key_column = "conversation_id" if table == "sessions" else "trace_id"
    await db.executemany(f"DELETE FROM {table} WHERE tenant_id = ? AND {key_column} = ?", doomed)
    return len(doomed)


class SqliteDatabase:
    """A database file. connect() gives a short-lived connection after making sure the schema is current."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._ready = False
        self._setup_lock = asyncio.Lock()

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        await self._ensure_ready()
        async with self._open() as db:
            yield db

    @asynccontextmanager
    async def _open(self) -> AsyncIterator[aiosqlite.Connection]:
        async with aiosqlite.connect(
            self.path, timeout=30, isolation_level=None
        ) as db:  # autocommit; BEGIN is explicit
            await db.execute("PRAGMA busy_timeout = 30000")
            yield db

    async def _ensure_ready(self) -> None:
        if self._ready:
            return
        async with self._setup_lock:
            if self._ready:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            async with self._open() as db:
                await db.execute("PRAGMA journal_mode = WAL")
                await apply_migrations(db)
            self._ready = True

    async def schema_version(self) -> int:
        async with self.connect() as db:
            row = await (await db.execute("SELECT MAX(version) FROM schema_version")).fetchone()
        return int(row[0]) if row is not None and row[0] is not None else 0


class SqliteSessionStore:
    """Sessions with optimistic versions, like the in-memory store. lock() serializes one process; the version check
    protects against a second process or a missing lock."""

    def __init__(self, db: SqliteDatabase) -> None:
        self._db = db
        self._locks: defaultdict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    async def load(self, tenant_id: str, conversation_id: str) -> SessionState | None:
        async with self._db.connect() as db:
            row = await (
                await db.execute(
                    "SELECT state_json FROM sessions WHERE tenant_id = ? AND conversation_id = ?",
                    (tenant_id, conversation_id),
                )
            ).fetchone()
        return SessionState.model_validate_json(row[0]) if row is not None else None

    async def save(self, session: SessionState) -> None:
        async with self._db.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (
                    await db.execute(
                        "SELECT version FROM sessions WHERE tenant_id = ? AND conversation_id = ?",
                        (session.tenant_id, session.conversation_id),
                    )
                ).fetchone()
                current = int(row[0]) if row is not None else 0
                if session.version != current:
                    raise SessionConflictError(
                        f"session {session.conversation_id} has version {session.version}, store has {current}"
                    )
                new_version = current + 1
                stored = session.model_copy(update={"version": new_version})
                await db.execute(
                    "INSERT INTO sessions (tenant_id, conversation_id, version, state_json, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (tenant_id, conversation_id) DO UPDATE SET "
                    "version = excluded.version, state_json = excluded.state_json, updated_at = excluded.updated_at",
                    (
                        session.tenant_id,
                        session.conversation_id,
                        new_version,
                        stored.model_dump_json(),
                        _iso(session.updated_at),
                    ),
                )
                await db.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    await db.execute("ROLLBACK")
                raise
        session.version = new_version

    @asynccontextmanager
    async def lock(self, tenant_id: str, conversation_id: str) -> AsyncIterator[None]:
        async with self._locks[(tenant_id, conversation_id)]:
            yield

    async def purge_older_than(self, cutoff: datetime, *, keep: Collection[tuple[str, str]] = ()) -> int:
        async with self._db.connect() as db:
            cursor = await db.execute(
                "SELECT tenant_id, conversation_id, conversation_id FROM sessions WHERE updated_at < ?", (_iso(cutoff),)
            )
            return await _delete_unless_kept(db, "sessions", await cursor.fetchall(), keep)


class SqliteTraceStore:
    def __init__(self, db: SqliteDatabase, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def add(self, trace: DecisionTrace) -> None:
        try:
            async with self._db.connect() as db:
                await db.execute(
                    "INSERT INTO traces (trace_id, tenant_id, conversation_id, turn_index, kind, decision, "
                    "escalation_reason, language, latency_ms, created_at, trace_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        trace.trace_id,
                        trace.tenant_id,
                        trace.conversation_id,
                        trace.turn_index,
                        trace.kind,
                        trace.decision.value,
                        trace.escalation_reason.value if trace.escalation_reason else None,
                        trace.language.value if trace.language else None,
                        trace.latency_ms,
                        _iso(self._clock.now()),
                        trace.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError:
            raise AlreadyExistsError(f"trace {trace.trace_id} already stored") from None

    async def get(self, tenant_id: str, trace_id: str) -> DecisionTrace | None:
        rows = await self._fetch("WHERE tenant_id = ? AND trace_id = ?", (tenant_id, trace_id))
        return rows[0] if rows else None

    async def for_conversation(self, tenant_id: str, conversation_id: str) -> list[DecisionTrace]:
        return await self._fetch(
            "WHERE tenant_id = ? AND conversation_id = ? ORDER BY rowid", (tenant_id, conversation_id)
        )

    async def query(
        self,
        tenant_id: str,
        *,
        conversation_id: str | None = None,
        decision: Decision | None = None,
        escalation_reason: EscalationReason | None = None,
        limit: int = 100,
    ) -> list[DecisionTrace]:
        clauses, params = ["tenant_id = ?"], [tenant_id]
        for column, value in (
            ("conversation_id", conversation_id),
            ("decision", decision.value if decision else None),
            ("escalation_reason", escalation_reason.value if escalation_reason else None),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        return await self._fetch(f"WHERE {' AND '.join(clauses)} ORDER BY rowid DESC LIMIT ?", (*params, limit))

    async def purge_older_than(self, cutoff: datetime, *, keep: Collection[tuple[str, str]] = ()) -> int:
        async with self._db.connect() as db:
            cursor = await db.execute(
                "SELECT tenant_id, conversation_id, trace_id FROM traces WHERE created_at < ?", (_iso(cutoff),)
            )
            return await _delete_unless_kept(db, "traces", await cursor.fetchall(), keep)

    async def _fetch(self, tail: str, params: Sequence[Any]) -> list[DecisionTrace]:
        # `tail` is built only from fixed fragments above; every value travels as a parameter.
        async with self._db.connect() as db:
            rows = await (await db.execute(f"SELECT trace_json FROM traces {tail}", tuple(params))).fetchall()
        return [DecisionTrace.model_validate_json(r[0]) for r in rows]


class SqliteCaseStore:
    def __init__(self, db: SqliteDatabase) -> None:
        self._db = db

    async def add(self, case: HandoffCase) -> None:
        try:
            async with self._db.connect() as db:
                await db.execute(
                    "INSERT INTO cases (case_id, tenant_id, conversation_id, status, reason, priority, claimed_by, "
                    "created_at, updated_at, case_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        case.case_id,
                        case.tenant_id,
                        case.conversation_id,
                        case.status.value,
                        case.package.reason.value,
                        case.package.priority,
                        case.claimed_by,
                        _iso(case.created_at),
                        _iso(case.updated_at),
                        case.model_dump_json(),
                    ),
                )
        except sqlite3.IntegrityError:
            raise AlreadyExistsError(f"case {case.case_id} already stored") from None

    async def get(self, tenant_id: str, case_id: str) -> HandoffCase | None:
        async with self._db.connect() as db:
            row = await (
                await db.execute(
                    "SELECT case_json FROM cases WHERE tenant_id = ? AND case_id = ?", (tenant_id, case_id)
                )
            ).fetchone()
        return HandoffCase.model_validate_json(row[0]) if row is not None else None

    async def save(self, case: HandoffCase) -> None:
        async with self._db.connect() as db:
            cursor = await db.execute(
                "UPDATE cases SET conversation_id = ?, status = ?, reason = ?, priority = ?, claimed_by = ?, "
                "updated_at = ?, case_json = ? WHERE tenant_id = ? AND case_id = ?",
                (
                    case.conversation_id,
                    case.status.value,
                    case.package.reason.value,
                    case.package.priority,
                    case.claimed_by,
                    _iso(case.updated_at),
                    case.model_dump_json(),
                    case.tenant_id,
                    case.case_id,
                ),
            )
            changed = cursor.rowcount
        if changed == 0:
            raise NotFoundError(f"case {case.case_id} does not exist")

    async def list(self, tenant_id: str, *, status: CaseStatus | None = None) -> Sequence[HandoffCase]:
        sql = "SELECT case_json FROM cases WHERE tenant_id = ?"
        params: list[str] = [tenant_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status.value)
        async with self._db.connect() as db:
            rows = await (await db.execute(sql + " ORDER BY created_at, rowid", tuple(params))).fetchall()
        return [HandoffCase.model_validate_json(r[0]) for r in rows]

    async def purge_older_than(self, cutoff: datetime) -> int:
        async with self._db.connect() as db:
            cursor = await db.execute(
                "DELETE FROM cases WHERE status IN (?, ?) AND updated_at < ?",
                (CaseStatus.RESOLVED.value, CaseStatus.RETURNED_TO_AGENT.value, _iso(cutoff)),
            )
            return cursor.rowcount
