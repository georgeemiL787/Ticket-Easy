"""The same scripted conversations must decide the same way whether the shop runs on its tenant file or on the bundle
imported from that file. Proves the old format maps into the new one without a change in behaviour."""

from datetime import UTC, datetime

import pytest

from team_b.bundles import tools_from_config
from team_b.container import Container
from team_b.domain.bundle import BundleProfile, TenantBundle
from tests.integration.scenario_format import discover, load_scenario
from tests.integration.scenario_runner import run_scenario

CASES = [(tenant, path) for tenant, path in discover() if tenant == "shop_001"]


def on_the_bundle(container: Container) -> Container:
    """Replace the file configuration by the bundle imported from it (what activating the bundle does)."""
    registry = container.bundles
    assert registry is not None
    for config in list(registry):
        bundle = TenantBundle(
            tenant_id=config.tenant_id,
            version=1,
            status="active",
            created_at=datetime(2026, 9, 28, tzinfo=UTC),
            created_by="parity",
            config=config,
            profile=BundleProfile(
                display_name=config.display_name, locales=(config.default_locale,), default_locale=config.default_locale
            ),
            tools=tools_from_config(config),
        )
        registry._apply(bundle)  # noqa: SLF001 - the store is not needed to compare behaviour
    return container


@pytest.mark.scenario
@pytest.mark.parametrize(("tenant_id", "path"), CASES, ids=[p.stem for _, p in CASES])
async def test_scenario_decides_the_same_on_the_bundle(tenant_id: str, path: object) -> None:
    scenario = load_scenario(path)  # type: ignore[arg-type]
    result = await run_scenario(scenario, tenant_id, customize=on_the_bundle)
    if result.outcome == "skipped":
        pytest.skip(f"{scenario.id} pending")
    assert not result.failures, f"{scenario.id} fails on the bundle:\n  " + "\n  ".join(result.failures)
