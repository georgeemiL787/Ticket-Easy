import asyncio
from pathlib import Path

import pytest

from team_b.bundles import BundleError, BundleRegistry, import_from_files
from team_b.container import Container, build_container
from team_b.domain.bundle import BundleProfile
from team_b.domain.tenant import TenantRegistry, UnknownTenantError
from team_b.ports import NotFoundError
from tests.conftest import FIXED_TODAY
from tests.support import make_settings

T = "shop_001"


@pytest.fixture
def real(tmp_path: Path) -> Container:
    return build_container(make_settings(tmp_path, fixed_today=FIXED_TODAY))


def registry_of(container: Container) -> BundleRegistry:
    assert container.bundles is not None and container.tenants is container.bundles
    return container.bundles


async def go_live(registry: BundleRegistry, version: int = 1) -> None:
    await registry.publish(T, version)
    await registry.activate(T, version, "ana")


async def test_without_a_bundle_the_tenant_file_applies(real: Container) -> None:
    registry = registry_of(real)
    assert registry.active_bundle(T) is None
    assert registry.get(T) is registry.file_config(T)
    assert registry.get(T).bundle_version is None


async def test_import_publish_activate_and_the_next_read_uses_the_bundle(real: Container) -> None:
    registry = registry_of(real)
    draft = await import_from_files(registry, T, "ana")
    assert (draft.version, draft.status, draft.parent_version) == (1, "draft", None)
    await go_live(registry)
    config = registry.get(T)
    assert config.bundle_version == 1 and config.bundle_sha256 == draft.content_sha256
    assert config.model_dump() == registry.file_config(T).model_dump()  # the same behaviour as the file


async def test_a_new_draft_copies_the_active_version_and_may_change_a_part(real: Container) -> None:
    registry = registry_of(real)
    await import_from_files(registry, T, "ana")
    await go_live(registry)
    second = await registry.create_draft(
        T, created_by="ana", profile=BundleProfile(display_name="Nile Style Shop", tone="formal")
    )
    assert (second.version, second.parent_version) == (2, 1)
    assert registry.get(T).bundle_version == 1  # a draft changes nothing
    await go_live(registry, 2)
    assert registry.get(T).display_name == "Nile Style Shop" and registry.get(T).bundle_version == 2


async def test_rollback_goes_to_the_parent_and_a_first_version_has_none(real: Container) -> None:
    registry = registry_of(real)
    with pytest.raises(BundleError, match="no active"):
        await registry.rollback(T, "ana")
    await import_from_files(registry, T, "ana")
    await go_live(registry)
    with pytest.raises(BundleError, match="earlier"):
        await registry.rollback(T, "ana")
    await registry.create_draft(T, created_by="ana")
    await go_live(registry, 2)
    back = await registry.rollback(T, "ana")
    assert back.version == 1 and registry.get(T).bundle_version == 1


async def test_publishing_twice_or_activating_a_draft_is_refused(real: Container) -> None:
    registry = registry_of(real)
    await import_from_files(registry, T, "ana")
    with pytest.raises(BundleError, match="draft"):
        await registry.activate(T, 1, "ana")
    await registry.publish(T, 1)
    with pytest.raises(BundleError, match="already"):
        await registry.publish(T, 1)
    with pytest.raises(NotFoundError):
        await registry.activate(T, 9, "ana")


async def test_a_publish_check_can_stop_a_publication(real: Container) -> None:
    registry = registry_of(real)

    async def deny(_bundle: object) -> list[str]:
        return ["guardrails are not approved"]

    registry.add_publish_check(deny)
    await import_from_files(registry, T, "ana")
    with pytest.raises(BundleError, match="guardrails"):
        await registry.publish(T, 1)


async def test_a_bundle_only_business_is_known_and_a_new_one_needs_a_config(real: Container) -> None:
    registry = registry_of(real)
    with pytest.raises(BundleError, match="no configuration"):
        await registry.create_draft("brand_new", created_by="ana")
    base = registry.file_config(T)
    assert base is not None
    config = base.model_copy(update={"tenant_id": "brand_new"})
    await registry.create_draft("brand_new", created_by="ana", config=config)
    await registry.publish("brand_new", 1)
    assert "brand_new" in registry.drafts and registry.get("brand_new").preview  # known, but only as a draft
    await registry.activate("brand_new", 1, "ana")
    assert "brand_new" in registry and "brand_new" not in registry.drafts and not registry.get("brand_new").preview
    await registry.deactivate("brand_new")
    with pytest.raises(UnknownTenantError):
        registry.get("brand_new")


async def test_a_restart_takes_the_active_bundle_from_the_store(real: Container) -> None:
    registry = registry_of(real)
    await import_from_files(registry, T, "ana")
    await go_live(registry)
    await registry.create_draft(T, created_by="ana", profile=BundleProfile(display_name="Renamed"))
    await go_live(registry, 2)
    files = TenantRegistry.from_dir(real.settings.config_dir)  # a fresh process reads the files again
    fresh = BundleRegistry(files, registry.store, real.clock)
    assert fresh.get(T).bundle_version is None
    await fresh.load()
    assert fresh.get(T).display_name == "Renamed" and fresh.get(T).bundle_version == 2


async def test_concurrent_drafts_get_different_versions(real: Container) -> None:
    registry = registry_of(real)
    await import_from_files(registry, T, "ana")
    drafts = await asyncio.gather(*(registry.create_draft(T, created_by=f"u{i}") for i in range(4)))
    assert sorted(d.version for d in drafts) == [2, 3, 4, 5]
