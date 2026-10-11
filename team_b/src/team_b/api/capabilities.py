"""Which shop actions the agent can use for a business: available, missing (not published) or blocked (and why)."""

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from team_b.api.chat import TENANT, checked_tenant
from team_b.api.errors import upstream_unavailable
from team_b.brain import gates

router = APIRouter(tags=["capabilities"])


class CapabilityInfo(BaseModel):
    name: str
    status: Literal["available", "missing", "blocked"]
    reason: str | None = None  # missing: not_published; blocked: disabled, human_only, not_allowed or risk_too_high
    detail: str = ""
    used_by: list[str] = []  # the intents (or "identity") that rely on it
    operation_kind: str | None = None
    risk: str | None = None


class CapabilitiesOut(BaseModel):
    tenant_id: str
    fetched_at: datetime
    stale: bool  # the shop could not be asked: this is the last list that was received
    capabilities: list[CapabilityInfo]


@router.get("/v1/capabilities", response_model=CapabilitiesOut)
async def list_capabilities(
    tenant_id: TENANT,
    request: Request,
    refresh: Annotated[bool, Query(description="ask the shop again instead of using the remembered list")] = False,
) -> CapabilitiesOut:
    """Each tool this business's configuration relies on, and whether the agent may use it right now."""
    built = checked_tenant(request, tenant_id)
    if built.registry is None:
        raise upstream_unavailable("no shop is connected")
    catalog = await built.registry.catalog(tenant_id, refresh=refresh)
    if catalog is None:
        raise upstream_unavailable("the shop's tool list is not available")
    tenant = built.tenants.get(tenant_id)
    items: list[CapabilityInfo] = []
    for name, used_by in gates.configured_capabilities(tenant).items():
        tool = catalog.get(name)
        if tool is None:
            items.append(
                CapabilityInfo(
                    name=name,
                    status="missing",
                    reason="not_published",
                    used_by=used_by,
                    detail=f"the shop does not publish {name}",
                )  # fmt: skip
            )
            continue
        gate = gates.check_tool(tenant, tool)
        items.append(
            CapabilityInfo(
                name=name,
                status="available" if gate.allowed else "blocked",
                reason=gate.reason,
                detail=gate.detail,
                used_by=used_by,
                operation_kind=tool.operation_kind,
                risk=tool.risk,
            )  # fmt: skip
        )
    return CapabilitiesOut(tenant_id=tenant_id, fetched_at=catalog.fetched_at, stale=catalog.stale, capabilities=items)
