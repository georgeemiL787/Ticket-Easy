"""The human inbox API: list the cases, read one, and work on it (claim, reply, decide, resolve, hand back).

Rules (enforced here and again inside the orchestrator): only the person who claimed a case may reply, decide,
resolve or return it; a move the case life cycle does not allow answers 409 INVALID_STATE; every change appends a
CaseEvent. "agent" is a plain name for now (Phase 4 replaces it with logins). Approval of a waiting action is built by
Track A (Orchestrator.human_decide): until then the decision endpoint answers 501 NOT_IMPLEMENTED.
"""

import base64
import binascii
import json
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from team_b.api.auth import acting_name, current_user, visible_tenants
from team_b.api.chat import TENANT, checked_tenant, container_of
from team_b.api.errors import forbidden, invalid_request, invalid_state, not_found
from team_b.brain.approval import CaseNotYoursError, NothingToDecideError
from team_b.brain.orchestrator import Orchestrator
from team_b.container import Container
from team_b.domain.decision import EscalationReason
from team_b.domain.handoff import CaseStatus, HandoffCase, IllegalCaseTransitionError, NotManagerError, Priority
from team_b.ports import NotFoundError

DEFAULT_LIMIT, MAX_LIMIT = 50, 200
PRIORITY_RANK: dict[str, int] = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
CASE_ID = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.:-]{1,128}$", description="the case id")]

router = APIRouter(prefix="/v1/handoff", tags=["inbox"])


class CaseSummary(BaseModel):
    """One row of the inbox list."""

    case_id: str
    tenant_id: str
    conversation_id: str
    status: CaseStatus
    claimed_by: str | None
    reason: EscalationReason
    priority: Priority
    language: str | None
    summary: str
    customer_verified: bool
    has_pending_approval: bool
    created_at: datetime
    updated_at: datetime


class CasePage(BaseModel):
    items: list[CaseSummary]
    next_cursor: str | None = None


def summary_of(case: HandoffCase) -> CaseSummary:
    package = case.package
    return CaseSummary(
        case_id=case.case_id,
        tenant_id=case.tenant_id,
        conversation_id=case.conversation_id,
        status=case.status,
        claimed_by=case.claimed_by,
        reason=package.reason,
        priority=package.priority,
        language=package.language.value if package.language else None,
        summary=package.ai_summary if package.summary_source == "ai" and package.ai_summary else package.summary,
        customer_verified=package.customer.verified,
        has_pending_approval=case.pending_approval is not None,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def sort_key(case: HandoffCase) -> tuple[int, datetime, str]:
    """Most urgent first, then the oldest."""
    return (PRIORITY_RANK[case.package.priority], case.created_at, case.case_id)


def encode_cursor(case: HandoffCase) -> str:
    rank, created, case_id = sort_key(case)
    raw = json.dumps([rank, created.isoformat(), case_id])
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[int, datetime, str]:
    try:
        rank, created, case_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return int(rank), datetime.fromisoformat(created), str(case_id)
    except (ValueError, TypeError, binascii.Error):
        raise invalid_request("cursor is not valid") from None


class AgentIn(BaseModel):
    """Who is acting. When people sign in, the signed-in person is the actor and `agent` is ignored; without sign-in
    (TEAM_B_AUTH_REQUIRED=0) it is a plain name."""

    model_config = ConfigDict(extra="forbid")

    agent: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("agent")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or any(ord(ch) < 32 for ch in value):
            raise ValueError("agent must be a plain name")
        return value


class NoteIn(AgentIn):
    note: str | None = Field(default=None, max_length=1000)


class ReplyIn(AgentIn):
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class AssignIn(AgentIn):
    """`agent` is the manager who reassigns; `assignee` is who gets the case."""

    assignee: str = Field(min_length=1, max_length=80)

    @field_validator("assignee")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(ch) < 32 for ch in value):
            raise ValueError("assignee must be a plain name")
        return value


class DecisionIn(NoteIn):
    approve: bool


def orchestrator_of(built: Container) -> Orchestrator:
    assert built.orchestrator is not None
    return built.orchestrator


def who(request: Request, body: AgentIn) -> str:
    """The actor of this request: the signed-in person, else the name in the body."""
    return acting_name(request, body.agent)


async def find_case(built: Container, case_id: str, request: Request | None = None) -> HandoffCase:
    """The case, looking only in the businesses the person may see (another business's case is "not found")."""
    tenant_ids = built.tenants.tenant_ids()
    for tenant_id in tenant_ids if request is None else visible_tenants(request, tenant_ids):
        found = await built.cases.get(tenant_id, case_id)
        if found is not None:
            return found
    raise not_found("case not found")


@router.get("/cases", response_model=CasePage)
async def list_cases(
    request: Request,
    tenant_id: TENANT,
    status: CaseStatus | None = None,
    reason: EscalationReason | None = None,
    priority: Priority | None = None,
    claimed_by: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
) -> CasePage:
    """Cases of one business, most urgent first and oldest first within a priority. Pass next_cursor to go on."""
    built = checked_tenant(request, tenant_id)
    cases = [
        c
        for c in await built.cases.list(tenant_id, status=status)
        if (reason is None or c.package.reason is reason)
        and (priority is None or c.package.priority == priority)
        and (claimed_by is None or c.claimed_by == claimed_by)
    ]
    cases.sort(key=sort_key)
    if cursor is not None:
        after = decode_cursor(cursor)
        cases = [c for c in cases if sort_key(c) > after]
    page = cases[:limit]
    more = len(cases) > limit
    return CasePage(items=[summary_of(c) for c in page], next_cursor=encode_cursor(page[-1]) if more else None)


@router.get("/cases/{case_id}", response_model=HandoffCase)
async def get_case(case_id: CASE_ID, request: Request) -> HandoffCase:
    """The whole case: briefing, pending approval and every event."""
    return await find_case(container_of(request), case_id, request)


async def act(request: Request, case_id: str, work: Any) -> HandoffCase:
    """Run one human action, turning life-cycle mistakes into the standard error format, and return the new case."""
    built = container_of(request)
    await find_case(built, case_id, request)  # a clear 404 before anything else
    try:
        await work(orchestrator_of(built))
    except NotFoundError:
        raise not_found("case not found") from None
    except NotManagerError as exc:
        raise forbidden(str(exc)) from None
    except (
        IllegalCaseTransitionError,
        CaseNotYoursError,
        NothingToDecideError,
    ) as exc:  # a move the case does not allow
        raise invalid_state(str(exc)) from None
    return await find_case(built, case_id, request)


@router.post("/cases/{case_id}/claim", response_model=HandoffCase)
async def claim(case_id: CASE_ID, body: AgentIn, request: Request) -> HandoffCase:
    return await act(request, case_id, lambda o: o.claim(case_id, who(request, body)))


@router.post("/cases/{case_id}/assign", response_model=HandoffCase)
async def assign(case_id: CASE_ID, body: AssignIn, request: Request) -> HandoffCase:
    """A manager gives the case to someone.

    Signed in: the role decides (manager or admin, checked before this runs) and the assignee must be a person who may
    see this business. Without sign-in: only the names in the tenant's `managers` may do it (403 otherwise)."""
    built = container_of(request)
    signed_in = current_user(request) is not None
    if signed_in:
        case = await find_case(built, case_id, request)
        people = await built.users.list() if built.users is not None else []
        if not any(u.display_name.lower() == body.assignee.lower() and u.can_see(case.tenant_id) for u in people):
            raise invalid_request("the assignee is not a person who works on this business")
    return await act(
        request, case_id, lambda o: o.assign(case_id, who(request, body), body.assignee, verified_manager=signed_in)
    )


@router.post("/cases/{case_id}/release", response_model=HandoffCase)
async def release(case_id: CASE_ID, body: AgentIn, request: Request) -> HandoffCase:
    return await act(request, case_id, lambda o: o.release(case_id, who(request, body)))


@router.post("/cases/{case_id}/reply", response_model=HandoffCase)
async def reply(case_id: CASE_ID, body: ReplyIn, request: Request) -> HandoffCase:
    """Write to the customer: it is recorded on the case and shown in their chat at once."""
    return await act(request, case_id, lambda o: o.human_reply(case_id, who(request, body), body.text))


@router.post("/cases/{case_id}/decision", response_model=HandoffCase)
async def decision(case_id: CASE_ID, body: DecisionIn, request: Request) -> HandoffCase:
    """Approve or reject the action waiting on this case (the person must have claimed it)."""
    built = container_of(request)
    case = await find_case(built, case_id, request)
    try:
        case.require_claimer(who(request, body))
    except IllegalCaseTransitionError as exc:
        raise invalid_state(str(exc)) from None
    if case.pending_approval is None:
        raise invalid_state("this case has no action waiting for approval")
    return await act(request, case_id, lambda o: o.human_decide(case_id, who(request, body), body.approve, body.note))


@router.post("/cases/{case_id}/resolve", response_model=HandoffCase)
async def resolve(case_id: CASE_ID, body: NoteIn, request: Request) -> HandoffCase:
    return await act(request, case_id, lambda o: o.resolve(case_id, who(request, body), body.note))


@router.post("/cases/{case_id}/return-to-agent", response_model=HandoffCase)
async def return_to_agent(case_id: CASE_ID, body: NoteIn, request: Request) -> HandoffCase:
    """Give the conversation back to the assistant; it continues with its memory intact."""
    return await act(request, case_id, lambda o: o.return_to_agent(case_id, who(request, body), body.note))
