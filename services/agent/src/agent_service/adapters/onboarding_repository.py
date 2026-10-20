"""Storage for onboarding drafts: in memory or SQLite (migration 008). One draft per business that is being set up."""

from collections.abc import Sequence

from agent_service.adapters.sqlite_store import SqliteDatabase
from agent_service.domain.onboarding import OnboardingDraft


class InMemoryDraftStore:
    def __init__(self) -> None:
        self._drafts: dict[str, OnboardingDraft] = {}

    async def get(self, tenant_id: str) -> OnboardingDraft | None:
        return self._drafts.get(tenant_id)

    async def save(self, draft: OnboardingDraft) -> None:
        self._drafts[draft.tenant_id] = draft

    async def list(self) -> Sequence[OnboardingDraft]:
        return sorted(self._drafts.values(), key=lambda d: d.updated_at, reverse=True)

    async def delete(self, tenant_id: str) -> None:
        self._drafts.pop(tenant_id, None)


class SqliteDraftStore:
    def __init__(self, db: SqliteDatabase) -> None:
        self._db = db

    @staticmethod
    def _draft(row: Sequence[object]) -> OnboardingDraft:
        return OnboardingDraft.model_validate_json(str(row[0]))

    async def get(self, tenant_id: str) -> OnboardingDraft | None:
        async with self._db.connect() as db:
            row = await (
                await db.execute("SELECT data_json FROM onboarding_drafts WHERE tenant_id = ?", (tenant_id,))
            ).fetchone()
        return self._draft(row) if row is not None else None

    async def save(self, draft: OnboardingDraft) -> None:
        async with self._db.connect() as db:
            await db.execute(
                "INSERT INTO onboarding_drafts (tenant_id, step, data_json, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (tenant_id) DO UPDATE SET step = excluded.step, data_json = excluded.data_json, "
                "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (draft.tenant_id, draft.step, draft.model_dump_json(), draft.updated_at.isoformat(), draft.updated_by),
            )

    async def list(self) -> Sequence[OnboardingDraft]:
        async with self._db.connect() as db:
            rows = await (
                await db.execute("SELECT data_json FROM onboarding_drafts ORDER BY updated_at DESC")
            ).fetchall()
        return [self._draft(r) for r in rows]

    async def delete(self, tenant_id: str) -> None:
        async with self._db.connect() as db:
            await db.execute("DELETE FROM onboarding_drafts WHERE tenant_id = ?", (tenant_id,))
