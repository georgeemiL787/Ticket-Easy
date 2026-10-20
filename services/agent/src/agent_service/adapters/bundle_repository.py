"""Storage for tenant bundles: in memory or SQLite (migration 007). Both behave the same: a version number is taken
once, the content of a stored bundle never changes (only its status), and one bundle per business is active."""

import asyncio
import sqlite3
from collections.abc import Sequence
from datetime import datetime
from typing import cast

from agent_service.adapters.sqlite_store import SqliteDatabase
from agent_service.domain.bundle import BundleStatus, TenantBundle
from agent_service.ports import AlreadyExistsError, NotFoundError, StoreError


class BundleStateError(StoreError):
    """The requested status change is not allowed (for example activating a draft)."""


_ORDER = {"draft": 0, "published": 1, "active": 2, "retired": 3}


def _check_activation(bundle: TenantBundle) -> None:
    if bundle.status == "draft":
        raise BundleStateError(f"version {bundle.version} is a draft: publish it first")
    if bundle.status == "retired":
        raise BundleStateError(f"version {bundle.version} is retired")


def _check_status_change(bundle: TenantBundle, status: BundleStatus) -> None:
    if status == "active":
        raise BundleStateError("use activate() to make a version active")
    if bundle.status == "active":
        raise BundleStateError(f"version {bundle.version} is active: activate another version first")
    if status == "draft" or _ORDER[status] < _ORDER[bundle.status]:
        raise BundleStateError(f"a {bundle.status} version cannot become {status}")


class InMemoryBundleStore:
    def __init__(self) -> None:
        self._bundles: dict[tuple[str, int], TenantBundle] = {}
        self._active: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def add(self, bundle: TenantBundle) -> None:
        key = (bundle.tenant_id, bundle.version)
        if key in self._bundles:
            raise AlreadyExistsError(f"{bundle.tenant_id} version {bundle.version} already exists")
        self._bundles[key] = bundle

    async def get(self, tenant_id: str, version: int) -> TenantBundle | None:
        return self._bundles.get((tenant_id, version))

    async def list(self, tenant_id: str) -> Sequence[TenantBundle]:
        found = [b for (t, _), b in self._bundles.items() if t == tenant_id]
        return sorted(found, key=lambda b: b.version, reverse=True)

    async def latest_version(self, tenant_id: str) -> int:
        return max((v for (t, v) in self._bundles if t == tenant_id), default=0)

    async def set_status(self, tenant_id: str, version: int, status: BundleStatus) -> TenantBundle:
        async with self._lock:
            bundle = self._bundles.get((tenant_id, version))
            if bundle is None:
                raise NotFoundError(f"{tenant_id} has no version {version}")
            _check_status_change(bundle, status)
            changed = bundle.with_status(status)
            self._bundles[(tenant_id, version)] = changed
            return changed

    async def activate(self, tenant_id: str, version: int, actor: str, at: datetime) -> TenantBundle:
        async with self._lock:
            bundle = self._bundles.get((tenant_id, version))
            if bundle is None:
                raise NotFoundError(f"{tenant_id} has no version {version}")
            _check_activation(bundle)
            previous = self._active.get(tenant_id)
            if previous is not None and previous != version:
                self._bundles[(tenant_id, previous)] = self._bundles[(tenant_id, previous)].with_status("published")
            changed = bundle.with_status("active")
            self._bundles[(tenant_id, version)] = changed
            self._active[tenant_id] = version
            return changed

    async def active(self, tenant_id: str) -> TenantBundle | None:
        version = self._active.get(tenant_id)
        return self._bundles.get((tenant_id, version)) if version is not None else None

    async def all_active(self) -> Sequence[TenantBundle]:
        return [self._bundles[(t, v)] for t, v in sorted(self._active.items())]

    async def latest(self, tenant_id: str) -> TenantBundle | None:
        version = await self.latest_version(tenant_id)
        return self._bundles.get((tenant_id, version)) if version else None

    async def all_latest(self) -> Sequence[TenantBundle]:
        tenants = sorted({t for t, _ in self._bundles})
        return [b for b in [await self.latest(t) for t in tenants] if b is not None]


class SqliteBundleStore:
    def __init__(self, db: SqliteDatabase) -> None:
        self._db = db

    @staticmethod
    def _bundle(row: Sequence[object]) -> TenantBundle:
        return TenantBundle.model_validate_json(str(row[0])).with_status(cast(BundleStatus, row[1]))

    async def add(self, bundle: TenantBundle) -> None:
        try:
            async with self._db.connect() as db:
                await db.execute(
                    "INSERT INTO tenant_bundles (tenant_id, version, status, content_json, content_sha256, created_at, "
                    "created_by, parent_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        bundle.tenant_id,
                        bundle.version,
                        bundle.status,
                        bundle.model_dump_json(),
                        bundle.content_sha256,
                        bundle.created_at.isoformat(),
                        bundle.created_by,
                        bundle.parent_version,
                    ),
                )
        except sqlite3.IntegrityError:
            raise AlreadyExistsError(f"{bundle.tenant_id} version {bundle.version} already exists") from None

    async def get(self, tenant_id: str, version: int) -> TenantBundle | None:
        async with self._db.connect() as db:
            row = await (
                await db.execute(
                    "SELECT content_json, status FROM tenant_bundles WHERE tenant_id = ? AND version = ?",
                    (tenant_id, version),
                )
            ).fetchone()
        return self._bundle(row) if row is not None else None

    async def list(self, tenant_id: str) -> Sequence[TenantBundle]:
        async with self._db.connect() as db:
            rows = await (
                await db.execute(
                    "SELECT content_json, status FROM tenant_bundles WHERE tenant_id = ? ORDER BY version DESC",
                    (tenant_id,),
                )
            ).fetchall()
        return [self._bundle(r) for r in rows]

    async def latest_version(self, tenant_id: str) -> int:
        async with self._db.connect() as db:
            row = await (
                await db.execute("SELECT MAX(version) FROM tenant_bundles WHERE tenant_id = ?", (tenant_id,))
            ).fetchone()
        return int(row[0]) if row is not None and row[0] is not None else 0

    async def set_status(self, tenant_id: str, version: int, status: BundleStatus) -> TenantBundle:
        async with self._db.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (
                    await db.execute(
                        "SELECT content_json, status FROM tenant_bundles WHERE tenant_id = ? AND version = ?",
                        (tenant_id, version),
                    )
                ).fetchone()
                if row is None:
                    raise NotFoundError(f"{tenant_id} has no version {version}")
                bundle = self._bundle(row)
                _check_status_change(bundle, status)
                await db.execute(
                    "UPDATE tenant_bundles SET status = ? WHERE tenant_id = ? AND version = ?",
                    (status, tenant_id, version),
                )
                await db.execute("COMMIT")
            except BaseException:
                await db.execute("ROLLBACK")
                raise
        return bundle.with_status(status)

    async def activate(self, tenant_id: str, version: int, actor: str, at: datetime) -> TenantBundle:
        async with self._db.connect() as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (
                    await db.execute(
                        "SELECT content_json, status FROM tenant_bundles WHERE tenant_id = ? AND version = ?",
                        (tenant_id, version),
                    )
                ).fetchone()
                if row is None:
                    raise NotFoundError(f"{tenant_id} has no version {version}")
                bundle = self._bundle(row)
                _check_activation(bundle)
                await db.execute(
                    "UPDATE tenant_bundles SET status = 'published' WHERE tenant_id = ? AND status = 'active'",
                    (tenant_id,),
                )
                await db.execute(
                    "UPDATE tenant_bundles SET status = 'active' WHERE tenant_id = ? AND version = ?",
                    (tenant_id, version),
                )
                await db.execute(
                    "INSERT INTO tenant_bundle_active (tenant_id, version, activated_at, activated_by) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT (tenant_id) DO UPDATE SET version = excluded.version, "
                    "activated_at = excluded.activated_at, activated_by = excluded.activated_by",
                    (tenant_id, version, at.isoformat(), actor),
                )
                await db.execute("COMMIT")
            except BaseException:
                await db.execute("ROLLBACK")
                raise
        return bundle.with_status("active")

    async def active(self, tenant_id: str) -> TenantBundle | None:
        async with self._db.connect() as db:
            row = await (
                await db.execute(
                    "SELECT b.content_json, b.status FROM tenant_bundle_active a JOIN tenant_bundles b "
                    "ON b.tenant_id = a.tenant_id AND b.version = a.version WHERE a.tenant_id = ?",
                    (tenant_id,),
                )
            ).fetchone()
        return self._bundle(row) if row is not None else None

    async def all_active(self) -> Sequence[TenantBundle]:
        async with self._db.connect() as db:
            rows = await (
                await db.execute(
                    "SELECT b.content_json, b.status FROM tenant_bundle_active a JOIN tenant_bundles b "
                    "ON b.tenant_id = a.tenant_id AND b.version = a.version ORDER BY a.tenant_id"
                )
            ).fetchall()
        return [self._bundle(r) for r in rows]

    async def latest(self, tenant_id: str) -> TenantBundle | None:
        version = await self.latest_version(tenant_id)
        return await self.get(tenant_id, version) if version else None

    async def all_latest(self) -> Sequence[TenantBundle]:
        async with self._db.connect() as db:
            rows = await (
                await db.execute(
                    "SELECT b.content_json, b.status FROM tenant_bundles b JOIN "
                    "(SELECT tenant_id, MAX(version) AS v FROM tenant_bundles GROUP BY tenant_id) m "
                    "ON m.tenant_id = b.tenant_id AND m.v = b.version ORDER BY b.tenant_id"
                )
            ).fetchall()
        return [self._bundle(r) for r in rows]
