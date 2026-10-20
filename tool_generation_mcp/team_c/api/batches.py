"""Spec import for the product screens and tool batches: operations grouped by class, one batch per owner selection.

POST /businesses/{bid}/connection-check   is the API reachable (and does the login work)?
GET  /specifications/{sid}/operations     compact, paginated rows with the class of each operation
POST /businesses/{bid}/tool-batches       choose operations: one tool is prepared for each
POST /tool-batches/{id}/approve           the owner's confirmations; review, build and automatic tests follow
POST /tool-batches/{id}/publish           publish the tools that passed to the sandbox
"""
from typing import Literal

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..classify import operation_row
from ..config import AppError
from ..connectors import base_url as clean_base_url
from .deps import ServiceDep

router = APIRouter(prefix="/api/v1")


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    scope: Literal["end_user", "admin", "service", "public", "guest"] = "end_user"
    token: str | None = Field(default=None, max_length=4000)
    username: str | None = Field(default=None, max_length=300)
    password: str | None = Field(default=None, max_length=1000)
    context: dict[str, str | int] = {}


class Fixtures(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    key_ref: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")  # the NAME of an environment variable


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str | None = None
    fixtures: Fixtures | None = None
    accounts: list[Account] = []  # test accounts; stored encrypted, never returned


class Access(BaseModel):
    model_config = ConfigDict(extra="forbid")
    callers: Literal["customer", "public"] = "customer"
    own_records_only: bool = True


class BatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec_id: str
    operation_ids: list[str] = Field(min_length=1, max_length=25)
    verify_operation_id: str | None = None  # a read that proves a record belongs to the caller (used by tools that change data)
    access: Access = Access()
    connection: Connection = Connection()
    actor: str | None = Field(default=None, max_length=120)


class ApproveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_ids: list[str] = Field(min_length=1)
    confirmations: dict[str, dict[str, StrictBool]] = {}  # proposal id -> the four effect flags
    actor: str | None = Field(default=None, max_length=120)


class PublishInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str | None = Field(default=None, max_length=120)


class ConnectionCheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str


@router.get("/specifications/{sid}/operations")
def operations(sid: str, service: ServiceDep, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
               klass: Literal["read", "write", "risky", "restricted"] | None = Query(None, alias="class"), q: str = ""):
    """Operation rows (no schemas) with their class, `selectable` and, when not, why. Counts cover the whole description."""
    inventory = service.spec(sid)["inventory"]
    rows = [operation_row(o) for o in inventory["operations"]]
    counts = {k: sum(1 for r in rows if r["class"] == k) for k in ("read", "write", "risky", "restricted")}
    needle = q.strip().lower()
    shown = [r for r in rows if (klass is None or r["class"] == klass) and (not needle or needle in f'{r["method"]} {r["path"]} {r["title"]} {r["summary"]}'.lower())]
    return dict(spec_id=sid, total=len(rows), matching=len(shown), offset=offset, limit=limit, counts=counts, unusable=sum(1 for r in rows if not r["selectable"]),
                converted_from=inventory.get("converted_from"), diagnostics=[d for d in inventory.get("diagnostics", []) if d.get("severity") == "error"][:20],
                operations=shown[offset:offset + limit])


@router.post("/businesses/{bid}/connection-check")
def connection_check(bid: str, body: ConnectionCheckInput, service: ServiceDep):
    """Is the API answering at this base URL? No credentials are sent. A real check of the login happens with the first test."""
    service.store.one("SELECT id FROM businesses WHERE id=?", (bid,))
    url = clean_base_url(body.base_url)
    try:
        with httpx.Client(follow_redirects=False, trust_env=False, timeout=service.settings.connection_check_timeout) as client:
            answer = client.get(url + "/", headers={"Accept": "application/json"})
    except httpx.TimeoutException:
        return dict(reachable=False, detail=f"No answer within {service.settings.connection_check_timeout:g} seconds")
    except httpx.HTTPError as exc:
        return dict(reachable=False, detail=f"Could not connect ({type(exc).__name__})")
    return dict(reachable=True, status=answer.status_code, detail=f"The API answered (HTTP {answer.status_code})")


@router.post("/businesses/{bid}/tool-batches")
def create_batch(bid: str, body: BatchInput, service: ServiceDep):
    return service.batches.create(bid, body.spec_id, body.operation_ids, body.verify_operation_id, body.access.model_dump(),
                                  body.connection.model_dump(exclude_none=True), body.actor)


@router.get("/businesses/{bid}/tool-batches")
def list_batches(bid: str, service: ServiceDep):
    return service.batches.batches(bid)


@router.get("/tool-batches/{batch_id}")
def get_batch(batch_id: str, service: ServiceDep):
    return service.batches.view(batch_id)


@router.post("/tool-batches/{batch_id}/retry/{operation_id}")
def retry_item(batch_id: str, operation_id: str, service: ServiceDep):
    return service.batches.retry(batch_id, operation_id)


@router.post("/tool-batches/{batch_id}/approve")
def approve_batch(batch_id: str, body: ApproveInput, service: ServiceDep):
    return service.batches.approve(batch_id, body.proposal_ids, body.confirmations, body.actor)


@router.post("/tool-batches/{batch_id}/publish")
def publish_batch(batch_id: str, body: PublishInput, service: ServiceDep):
    return service.batches.publish(batch_id, body.actor)
