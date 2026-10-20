"""Small helpers shared by the action tests."""

from agent_service.config import Settings
from agent_service.domain.tenant import AlertThresholds, TenantRegistry


def tenant_registry(thresholds: AlertThresholds | None = None) -> TenantRegistry:
    """The real tenants, with the alert thresholds of shop_001 replaced when given."""
    registry = TenantRegistry.from_dir(Settings().config_dir)
    if thresholds is None:
        return registry
    changed = registry.get("shop_001").model_copy(update={"alerts": thresholds})
    return TenantRegistry({t.tenant_id: (changed if t.tenant_id == "shop_001" else t) for t in registry})
