"""Test-only endpoints for the load and chaos tests. They exist only when TEAM_B_ENABLE_TEST_ADMIN=1.

OWNER: Track A. Never enable this in production: it switches stand-in services off and shows the shop audit log.

  POST /v1/_test/chaos   {"plug": "shop"|"policy_search"|"rule_checker"|"safety_screen", "seconds": 30}
                         the plug fails every call, then recovers by itself after `seconds`
  POST /v1/_test/heal    {"plug": ...} or {} for every plug: recover now
  GET  /v1/_test/state   which plugs are failing right now
  GET  /v1/_test/report?tenant_id=   what the checker needs: the shop's audit log, the decision traces, the order totals
"""

import asyncio
from typing import Annotated, Any, Literal

from fastapi import APIRouter, FastAPI, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from team_b.api.chat import TENANT, checked_tenant
from team_b.api.errors import invalid_request
from team_b.container import Container
from team_b.observability import get_logger

log = get_logger(__name__)
Plug = Literal["shop", "policy_search", "rule_checker", "safety_screen"]
PLUGS: tuple[Plug, ...] = ("shop", "policy_search", "rule_checker", "safety_screen")
FOREVER = 1_000_000
MAX_TRACES = 20000

router = APIRouter(prefix="/v1/_test", tags=["test-admin"])


class ChaosIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plug: Plug
    seconds: float = Field(default=30.0, gt=0, le=3600)


class HealIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plug: Plug | None = None


def _break(built: Container, plug: Plug) -> None:
    if plug == "shop" and built.shop is not None:
        built.shop.break_everything()
    elif plug == "policy_search" and built.policy_search is not None:
        for operation in ("search_knowledge", "get_passage", "search_past_tickets"):
            built.policy_search.fail_next(operation, FOREVER)
    elif plug == "rule_checker" and built.rule_checker is not None:
        built.rule_checker.fail_next(FOREVER)
    elif plug == "safety_screen" and built.safety_screen is not None:
        built.safety_screen.fail_next(FOREVER)
    else:
        raise invalid_request(f"plug {plug} is not available")


def _heal(built: Container, plug: Plug) -> None:
    if plug == "shop" and built.shop is not None:
        built.shop.heal()
    elif plug == "policy_search" and built.policy_search is not None:
        built.policy_search.reset()
    elif plug == "rule_checker" and built.rule_checker is not None:
        built.rule_checker.reset()
    elif plug == "safety_screen" and built.safety_screen is not None:
        built.safety_screen.reset()


def _state(request: Request) -> dict[str, Any]:
    state: dict[str, Any] = request.app.state.chaos
    return state


@router.post("/chaos")
async def chaos(body: ChaosIn, request: Request) -> dict[str, Any]:
    """Make a plug fail now; it heals itself after the given seconds."""
    built: Container = request.app.state.container
    _break(built, body.plug)
    loop = asyncio.get_running_loop()
    state = _state(request)
    if (old := state.get(body.plug)) is not None:
        old.cancel()  # a second chaos on the same plug restarts its timer

    def recover() -> None:
        _heal(built, body.plug)
        state.pop(body.plug, None)
        log.info("chaos_recovered", plug=body.plug)

    state[body.plug] = loop.call_later(body.seconds, recover)
    log.info("chaos_started", plug=body.plug, seconds=body.seconds)
    return {"plug": body.plug, "failing": True, "recovers_in_s": body.seconds}


@router.post("/heal")
async def heal(body: HealIn, request: Request) -> dict[str, Any]:
    built: Container = request.app.state.container
    state = _state(request)
    for plug in [body.plug] if body.plug else list(PLUGS):
        if (timer := state.pop(plug, None)) is not None:
            timer.cancel()
        _heal(built, plug)
    return {"failing": sorted(state)}


@router.get("/state")
async def state(request: Request) -> dict[str, Any]:
    return {"failing": sorted(_state(request))}


@router.get("/report")
async def report(
    tenant_id: TENANT, request: Request, limit: Annotated[int, Query(ge=1, le=MAX_TRACES)] = MAX_TRACES
) -> dict[str, Any]:
    """The shop's audit log, the traces (oldest first) and every order's owner and total, for the checker."""
    built = checked_tenant(request, tenant_id)
    assert built.shop is not None
    traces = await built.traces.query(tenant_id, limit=limit)
    orders = {
        oid: {"customer_id": o["customer_id"], "order_total": o["order_total"]}
        for oid, o in built.shop._tenants[tenant_id].shop.orders.items()  # type: ignore[attr-defined]
    }
    return {
        "audit": [e.model_dump(mode="json") for e in built.shop.audit_log(tenant_id)],
        "traces": [t.model_dump(mode="json") for t in reversed(traces)],
        "orders": orders,
    }


def install(app: FastAPI) -> None:
    """Add the test endpoints (called only when the setting is on)."""
    app.state.chaos = {}
    app.include_router(router)
