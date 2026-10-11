"""Alerts for managers: list them and acknowledge them. The engine (brain/alerts.py) opens and resolves them.

OWNER: Track A. GET /v1/dashboard/alerts?tenant_id=&status=open|resolved|all and
POST /v1/dashboard/alerts/{alert_id}/ack {"agent": "Sara"}. Acknowledging only records who saw it: the alert stays open
until its condition clears.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from team_b.api.chat import TENANT, checked_tenant
from team_b.api.errors import not_found, upstream_unavailable
from team_b.domain.alerts import Alert

router = APIRouter(prefix="/v1/dashboard", tags=["alerts"])
ALERT_ID = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")]


class AlertList(BaseModel):
    tenant_id: str
    open_count: int  # for the badge in the top bar
    alerts: list[Alert]


class AckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: str = Field(min_length=1, max_length=80)

    @field_validator("agent")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(ch) < 32 for ch in value):
            raise ValueError("agent must be a plain name")
        return value


@router.get("/alerts", response_model=AlertList)
async def list_alerts(
    tenant_id: TENANT,
    request: Request,
    status: Annotated[Literal["open", "resolved", "all"], Query()] = "all",
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> AlertList:
    """The alerts of one business, newest first, with the number still open."""
    built = checked_tenant(request, tenant_id)
    if built.alerts is None:
        raise upstream_unavailable("alerts are not available")
    everything = await built.alerts.list(tenant_id, limit=500)
    shown = [a for a in everything if status == "all" or (a.is_open if status == "open" else not a.is_open)]
    return AlertList(tenant_id=tenant_id, open_count=sum(1 for a in everything if a.is_open), alerts=shown[:limit])


@router.post("/alerts/{alert_id}/ack", response_model=Alert)
async def acknowledge(alert_id: ALERT_ID, tenant_id: TENANT, body: AckIn, request: Request) -> Alert:
    """Record that this person has seen the alert. The first acknowledgement stays; the alert is not closed by it."""
    built = checked_tenant(request, tenant_id)
    if built.alerts is None:
        raise upstream_unavailable("alerts are not available")
    alert = await built.alerts.get(tenant_id, alert_id)
    if alert is None:
        raise not_found("alert not found")
    if alert.acknowledged_by is None:
        alert.acknowledged_by = body.agent
        await built.alerts.save(alert)
    return alert
