"""Storage for alerts: in memory (gone on restart) or SQLite (the table from migration 003).

OWNER: Track A. Both behave the same: copies in and out, a second open alert for the same (tenant, rule, key) is refused
with AlreadyExistsError (the engine opens a condition once), saving an unknown alert is NotFoundError, and one business
can never read or change another's alerts.
"""

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from team_b.adapters.sqlite_store import SqliteDatabase
from team_b.domain.alerts import Alert
from team_b.ports import AlreadyExistsError, NotFoundError


def alert_key(alert: Alert) -> str:
    """Which one of a rule's alerts this is (the service of service_down); empty when the rule has only one."""
    return str(alert.details.get("key", ""))


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


class InMemoryAlertStore:
    def __init__(self) -> None:
        self._alerts: dict[tuple[str, str], Alert] = {}

    async def add(self, alert: Alert) -> None:
        if (alert.tenant_id, alert.alert_id) in self._alerts:
            raise AlreadyExistsError(f"alert {alert.alert_id} already stored")
        if alert.is_open and any(
            a.tenant_id == alert.tenant_id and a.rule == alert.rule and alert_key(a) == alert_key(alert) and a.is_open
            for a in self._alerts.values()
        ):
            raise AlreadyExistsError(f"an alert for {alert.rule} is already open")
        self._alerts[(alert.tenant_id, alert.alert_id)] = alert.model_copy(deep=True)

    async def get(self, tenant_id: str, alert_id: str) -> Alert | None:
        found = self._alerts.get((tenant_id, alert_id))
        return found.model_copy(deep=True) if found is not None else None

    async def save(self, alert: Alert) -> None:
        if (alert.tenant_id, alert.alert_id) not in self._alerts:
            raise NotFoundError(f"alert {alert.alert_id} does not exist")
        self._alerts[(alert.tenant_id, alert.alert_id)] = alert.model_copy(deep=True)

    async def list(self, tenant_id: str, *, open_only: bool = False, limit: int = 200) -> Sequence[Alert]:
        found = [a for (t, _), a in self._alerts.items() if t == tenant_id and (a.is_open or not open_only)]
        found.sort(key=lambda a: a.opened_at, reverse=True)
        return [a.model_copy(deep=True) for a in found[:limit]]


class SqliteAlertStore:
    def __init__(self, db: SqliteDatabase) -> None:
        self._db = db

    async def add(self, alert: Alert) -> None:
        try:
            async with self._db.connect() as db:
                await db.execute(
                    "INSERT INTO alerts (alert_id, tenant_id, rule, alert_key, severity, opened_at, resolved_at, "
                    "acknowledged_by, alert_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        alert.alert_id, alert.tenant_id, alert.rule, alert_key(alert), alert.severity,
                        _iso(alert.opened_at), _iso(alert.resolved_at), alert.acknowledged_by, alert.model_dump_json(),
                    ),
                )  # fmt: skip
        except sqlite3.IntegrityError:
            raise AlreadyExistsError(
                f"alert {alert.alert_id} (or an open alert for {alert.rule}) already stored"
            ) from None

    async def get(self, tenant_id: str, alert_id: str) -> Alert | None:
        async with self._db.connect() as db:
            row = await (
                await db.execute(
                    "SELECT alert_json FROM alerts WHERE tenant_id = ? AND alert_id = ?", (tenant_id, alert_id)
                )
            ).fetchone()
        return Alert.model_validate_json(row[0]) if row is not None else None

    async def save(self, alert: Alert) -> None:
        async with self._db.connect() as db:
            cursor = await db.execute(
                "UPDATE alerts SET severity = ?, resolved_at = ?, acknowledged_by = ?, alert_json = ? "
                "WHERE tenant_id = ? AND alert_id = ?",
                (
                    alert.severity,
                    _iso(alert.resolved_at),
                    alert.acknowledged_by,
                    alert.model_dump_json(),
                    alert.tenant_id,
                    alert.alert_id,
                ),
            )
            changed = cursor.rowcount
        if changed == 0:
            raise NotFoundError(f"alert {alert.alert_id} does not exist")

    async def list(self, tenant_id: str, *, open_only: bool = False, limit: int = 200) -> Sequence[Alert]:
        sql = "SELECT alert_json FROM alerts WHERE tenant_id = ?"
        if open_only:
            sql += " AND resolved_at IS NULL"
        async with self._db.connect() as db:
            rows = await (
                await db.execute(sql + " ORDER BY opened_at DESC, rowid DESC LIMIT ?", (tenant_id, limit))
            ).fetchall()
        return [Alert.model_validate_json(r[0]) for r in rows]
