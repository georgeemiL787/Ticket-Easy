import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_service.adapters.bundle_repository import BundleStateError, InMemoryBundleStore, SqliteBundleStore
from agent_service.adapters.sqlite_store import SqliteDatabase
from agent_service.domain.bundle import TenantBundle
from agent_service.ports import AlreadyExistsError, BundleStore, NotFoundError

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "bundles" / "valid_minimal.json"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def bundle_store(store_kind: str, sqlite_db: SqliteDatabase) -> BundleStore:
    return InMemoryBundleStore() if store_kind == "memory" else SqliteBundleStore(sqlite_db)


def make(version: int, status: str = "draft", tenant: str = "demo_min") -> TenantBundle:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["config"]["tenant_id"] = tenant
    return TenantBundle.model_validate({**data, "tenant_id": tenant, "version": version, "status": status})


async def test_versions_are_unique_and_listed_newest_first(bundle_store: BundleStore) -> None:
    await bundle_store.add(make(1))
    await bundle_store.add(make(2))
    with pytest.raises(AlreadyExistsError):
        await bundle_store.add(make(2))
    assert [b.version for b in await bundle_store.list("demo_min")] == [2, 1]
    assert await bundle_store.latest_version("demo_min") == 2 and await bundle_store.latest_version("none") == 0
    assert (await bundle_store.get("demo_min", 1)) == make(1)
    assert await bundle_store.get("demo_min", 9) is None


async def test_a_draft_cannot_go_live_and_a_published_version_never_goes_back(bundle_store: BundleStore) -> None:
    await bundle_store.add(make(1))
    with pytest.raises(BundleStateError, match="draft"):
        await bundle_store.activate("demo_min", 1, "ana", NOW)
    await bundle_store.set_status("demo_min", 1, "published")
    with pytest.raises(BundleStateError):
        await bundle_store.set_status("demo_min", 1, "draft")
    with pytest.raises(BundleStateError, match="activate"):
        await bundle_store.set_status("demo_min", 1, "active")
    with pytest.raises(NotFoundError):
        await bundle_store.set_status("demo_min", 5, "published")


async def test_one_version_is_active_and_the_previous_becomes_published(bundle_store: BundleStore) -> None:
    for version in (1, 2):
        await bundle_store.add(make(version))
        await bundle_store.set_status("demo_min", version, "published")
    assert await bundle_store.active("demo_min") is None
    await bundle_store.activate("demo_min", 1, "ana", NOW)
    second = await bundle_store.activate("demo_min", 2, "ana", NOW)
    assert second.status == "active"
    assert (await bundle_store.active("demo_min")) is not None
    statuses = {b.version: b.status for b in await bundle_store.list("demo_min")}
    assert statuses == {1: "published", 2: "active"}
    assert [b.version for b in await bundle_store.all_active()] == [2]
    with pytest.raises(BundleStateError, match="active"):
        await bundle_store.set_status("demo_min", 2, "retired")


async def test_the_content_of_a_stored_bundle_does_not_change_with_its_status(bundle_store: BundleStore) -> None:
    await bundle_store.add(make(1))
    published = await bundle_store.set_status("demo_min", 1, "published")
    assert published.content_sha256 == make(1).content_sha256
    assert (await bundle_store.get("demo_min", 1)) == published
