"""Storage for users: in memory or SQLite (migration 004). Both behave the same: emails and display names are unique
(compared without regard to case), a password is only ever kept as a hash, and a missing user is None."""

import sqlite3
from collections.abc import Sequence
from datetime import datetime

from team_b.adapters.sqlite_store import SqliteDatabase
from team_b.domain.users import User
from team_b.ports import AlreadyExistsError, NotFoundError


class InMemoryUserStore:
    def __init__(self) -> None:
        self._users: dict[str, tuple[User, str]] = {}

    async def create(self, user: User, password_hash: str) -> None:
        for other, _ in self._users.values():
            if other.user_id == user.user_id or other.email.lower() == user.email.lower():
                raise AlreadyExistsError("a user with this id or email already exists")
            if other.display_name.lower() == user.display_name.lower():
                raise AlreadyExistsError("a user with this display name already exists")
        self._users[user.user_id] = (user, password_hash)

    async def get(self, user_id: str) -> User | None:
        found = self._users.get(user_id)
        return found[0] if found else None

    async def get_with_hash(self, email: str) -> tuple[User, str] | None:
        return next((v for v in self._users.values() if v[0].email.lower() == email.lower()), None)

    async def set_password(self, user_id: str, password_hash: str) -> None:
        if user_id not in self._users:
            raise NotFoundError(f"user {user_id} does not exist")
        self._users[user_id] = (self._users[user_id][0], password_hash)

    async def set_active(self, user_id: str, active: bool) -> None:
        if user_id not in self._users:
            raise NotFoundError(f"user {user_id} does not exist")
        user, password_hash = self._users[user_id]
        self._users[user_id] = (user.model_copy(update={"active": active}), password_hash)

    async def list(self) -> Sequence[User]:
        return sorted((u for u, _ in self._users.values()), key=lambda u: u.created_at)


class SqliteUserStore:
    def __init__(self, db: SqliteDatabase) -> None:
        self._db = db

    async def create(self, user: User, password_hash: str) -> None:
        try:
            async with self._db.connect() as db:
                await db.execute("BEGIN")
                await db.execute(
                    "INSERT INTO users (user_id, email, password_hash, display_name, role, active, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        user.user_id,
                        user.email,
                        password_hash,
                        user.display_name,
                        user.role,
                        int(user.active),
                        user.created_at.isoformat(),
                    ),
                )
                for tenant_id in user.tenants:
                    await db.execute(
                        "INSERT INTO user_tenants (user_id, tenant_id) VALUES (?, ?)", (user.user_id, tenant_id)
                    )
                await db.execute("COMMIT")
        except sqlite3.IntegrityError:
            raise AlreadyExistsError("a user with this id, email or display name already exists") from None

    async def _load(self, where: str, params: tuple[str, ...]) -> tuple[User, str] | None:
        async with self._db.connect() as db:
            row = await (
                await db.execute(
                    "SELECT user_id, email, password_hash, display_name, role, active, created_at FROM users WHERE "
                    + where,
                    params,
                )
            ).fetchone()
            if row is None:
                return None
            tenants = await (
                await db.execute("SELECT tenant_id FROM user_tenants WHERE user_id = ? ORDER BY tenant_id", (row[0],))
            ).fetchall()
        user = User(
            user_id=row[0],
            email=row[1],
            display_name=row[3],
            role=row[4],
            active=bool(row[5]),
            created_at=datetime.fromisoformat(row[6]),
            tenants=tuple(t[0] for t in tenants),
        )
        return user, str(row[2])

    async def get(self, user_id: str) -> User | None:
        found = await self._load("user_id = ?", (user_id,))
        return found[0] if found else None

    async def get_with_hash(self, email: str) -> tuple[User, str] | None:
        return await self._load("lower(email) = lower(?)", (email,))

    async def set_password(self, user_id: str, password_hash: str) -> None:
        async with self._db.connect() as db:
            cursor = await db.execute("UPDATE users SET password_hash = ? WHERE user_id = ?", (password_hash, user_id))
            changed = cursor.rowcount
        if changed == 0:
            raise NotFoundError(f"user {user_id} does not exist")

    async def set_active(self, user_id: str, active: bool) -> None:
        async with self._db.connect() as db:
            cursor = await db.execute("UPDATE users SET active = ? WHERE user_id = ?", (int(active), user_id))
            changed = cursor.rowcount
        if changed == 0:
            raise NotFoundError(f"user {user_id} does not exist")

    async def list(self) -> Sequence[User]:
        async with self._db.connect() as db:
            ids = await (await db.execute("SELECT user_id FROM users ORDER BY created_at, rowid")).fetchall()
        users = [await self.get(r[0]) for r in ids]
        return [u for u in users if u is not None]
