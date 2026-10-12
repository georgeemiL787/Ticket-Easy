"""Business tools: which shop tools the agent uses, which of them Team C serves (TEST-ONLY sandbox), and their state.

GET  /v1/dashboard/business-tools?tenant_id=                         the page (manager, like every dashboard read)
POST /v1/dashboard/business-tools/refresh?tenant_id=                 ask the shop and Team C for the tool list again
POST /v1/dashboard/business-tools/publications/{id}/disable?tenant_id= {"note"}   stop a Team C tool (manager)

Team C has no list_changed notifications: the agent re-lists every TEAM_B_CAPABILITY_TTL_S and on refresh, and Team C
re-checks publication, approval, enforcement, evidence and hashes on every call anyway. A disable is checked here first:
the publication must belong to this business's Team C business (else 404, as if it did not exist). Disabling blocks the
next call; it cannot undo a write that already happened. Everything else in the tool workflow (request, answers,
approval, build, sandbox tests, publish) happens in Team C's own pages, linked as builder_url.
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from team_b.api.auth import current_user
from team_b.api.chat import TENANT, checked_tenant
from team_b.api.errors import not_found, upstream_unavailable
from team_b.brain import gates
from team_b.container import Container
from team_b.contracts.errors import UpstreamError
from team_b.observability import get_logger

router = APIRouter(prefix="/v1/dashboard/business-tools", tags=["business tools"])
PUBLICATION_ID = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
log = get_logger(__name__)

# What a Team C problem means, in words a business owner can act on.
PROBLEMS = {
    "not_published": "no sandbox publication of this tool is active in Team C",
    "ambiguous": "two artifacts of the same version are published: disable one in Team C",
    "kind_mismatch": "the published tool reads or writes differently from what this business declared",
    "not_sandbox": "the publication is not a TEST-ONLY sandbox publication",
    "no_spec": "this business has no declaration of the tool (spec) to check it against",
    "team_c_unavailable": "Team C's tool server could not be asked",
}


class BusinessTool(BaseModel):
    name: str  # the tool name the agent's intents use
    used_by: list[str]  # intents (or "identity") that rely on it
    source: Literal["team_c", "standin"]  # Team C sandbox publication, or the built-in stand-in shop
    status: Literal["available", "missing", "blocked"]
    reason: str | None = None  # a gate reason (disabled, not_allowed, ...) or a Team C problem (not_published, ...)
    detail: str = ""
    operation_kind: str | None = None
    team_c_tool: str | None = None
    publication_id: str | None = None
    proposal_id: str | None = None
    proposal_version: int | None = None
    artifact_sha256: str | None = None  # the first 12 characters


class TeamCPublication(BaseModel):
    id: str
    tool_name: str
    status: str  # published | disabled
    effective_status: str  # published | blocked (Team C's recheck failed) | disabled
    problem: str | None = None
    environment: str  # always sandbox
    production_ready: bool  # always false
    published_at: str | None = None
    disabled_at: str | None = None
    proposal_id: str
    version: int
    bound_to: str | None = None  # the agent's tool name that uses this proposal, if any


class TeamCProposal(BaseModel):
    id: str
    name: str
    version: int
    state: str  # needs_clarification | needs_reconciliation | ready_for_review | approved_to_build | rejected | ...


class DashboardBusinessTools(BaseModel):
    tenant_id: str
    configured: bool  # is any tool of this business served by Team C?
    test_only: bool = True  # Team C publications are sandbox-only, never production-ready or activated
    business_id: str | None = None
    builder_url: str | None = None  # Team C's owner pages for this business (request, review, build, test, publish)
    builder_base: str | None = None  # Team C's pages root: <base>/proposals/<id>, <base>/artifacts/<id>
    listed_at: datetime | None = None  # when Team C's tools were last listed
    team_c_error: str | None = None
    mapped_customers: int = 0  # verified customers that have a sandbox identity
    tools: list[BusinessTool]
    publications: list[TeamCPublication] = []
    publications_error: str | None = None
    proposals: list[TeamCProposal] = []


class DisableIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=3, max_length=500)


async def _overview(built: Container, tenant_id: str, *, refresh: bool) -> DashboardBusinessTools:
    if built.registry is None:
        raise upstream_unavailable("no shop is connected")
    catalog = await built.registry.catalog(tenant_id, refresh=refresh)
    tenant = built.tenants.get(tenant_id)
    team_c = built.team_c
    binding = team_c.binding(tenant_id) if team_c is not None else None
    state = team_c.status(tenant_id) if team_c is not None and binding is not None else None
    tools: list[BusinessTool] = []
    for name, used_by in gates.configured_capabilities(tenant).items():
        bound = binding is not None and name in binding.tools
        resolved = state.resolved.get(name) if state is not None else None
        spec = catalog.get(name) if catalog is not None else None
        item = BusinessTool(name=name, used_by=used_by, source="team_c" if bound else "standin", status="missing")
        if resolved is not None:
            item.team_c_tool, item.publication_id = resolved.team_c_tool, resolved.publication_id
            item.proposal_id, item.proposal_version = resolved.proposal_id, resolved.proposal_version
            item.artifact_sha256 = resolved.artifact_sha256[:12] if resolved.artifact_sha256 else None
        if spec is None:
            item.reason = (resolved.problem if resolved is not None else None) or "not_published"
            item.detail = PROBLEMS.get(item.reason, f"the shop does not publish {name}")
        else:
            gate = gates.check_tool(tenant, spec)
            item.status = "available" if gate.allowed else "blocked"
            item.reason, item.detail = gate.reason, gate.detail
            item.operation_kind = spec.operation_kind
        tools.append(item)
    out = DashboardBusinessTools(tenant_id=tenant_id, configured=binding is not None, tools=tools)
    if team_c is None or binding is None:
        return out
    out.business_id = binding.business_id
    out.mapped_customers = len(binding.identities)
    if team_c.config.builder_url:
        out.builder_base = team_c.config.builder_url.rstrip("/")
        out.builder_url = f"{out.builder_base}/businesses/{binding.business_id}"
    if state is not None:
        out.listed_at = datetime.fromtimestamp(state.listed_at, UTC) if state.listed_at else None
        out.team_c_error = state.error
    admin = team_c.admin
    if admin is None:
        out.publications_error = "Team C's API address (api_url) is not configured"
        return out
    by_proposal = {b.proposal_id: name for name, b in binding.tools.items() if b.proposal_id}
    try:
        publications = await admin.publications(binding.business_id)
        proposals = await admin.proposals(binding.business_id)
    except UpstreamError as exc:
        out.publications_error = f"Team C's API could not be asked ({exc.code})"
        return out
    out.publications = [_publication(p, by_proposal) for p in reversed(publications)]
    out.proposals = [_proposal(p) for p in proposals if p.get("proposal_id") or p.get("id")]
    return out


def _publication(p: dict[str, Any], by_proposal: dict[str, str]) -> TeamCPublication:
    problem = p.get("problem") if isinstance(p.get("problem"), dict) else None
    return TeamCPublication(
        id=str(p.get("id")),
        tool_name=str(p.get("tool_name") or ""),
        status=str(p.get("status") or ""),
        effective_status=str(p.get("effective_status") or p.get("status") or ""),
        problem=f"{problem.get('code')}: {problem.get('message')}" if problem else None,
        environment=str(p.get("environment") or ""),
        production_ready=p.get("production_ready") is True,
        published_at=p.get("published_at"),
        disabled_at=p.get("disabled_at"),
        proposal_id=str(p.get("proposal_id") or ""),
        version=int(p.get("version") or 0),
        bound_to=by_proposal.get(str(p.get("proposal_id"))),
    )


def _proposal(p: dict[str, Any]) -> TeamCProposal:
    found = p.get("content")
    content: dict[str, Any] = found if isinstance(found, dict) else {}
    return TeamCProposal(
        id=str(p.get("proposal_id") or p.get("id")),  # Team C's proposal view names it proposal_id
        name=str(content.get("name") or p.get("name") or ""),
        version=int(p.get("version") or 0),
        state=str(p.get("state") or ""),
    )


@router.get("", response_model=DashboardBusinessTools)
async def business_tools(tenant_id: TENANT, request: Request) -> DashboardBusinessTools:
    return await _overview(checked_tenant(request, tenant_id), tenant_id, refresh=False)


@router.post("/refresh", response_model=DashboardBusinessTools)
async def refresh(tenant_id: TENANT, request: Request) -> DashboardBusinessTools:
    """List the tools again now (Team C sends no change notifications)."""
    return await _overview(checked_tenant(request, tenant_id), tenant_id, refresh=True)


@router.post("/publications/{publication_id}/disable", response_model=DashboardBusinessTools)
async def disable(
    publication_id: PUBLICATION_ID, tenant_id: TENANT, body: DisableIn, request: Request
) -> DashboardBusinessTools:
    """Stop one Team C tool of this business. Its next call is refused; a write that already happened stays."""
    built = checked_tenant(request, tenant_id)
    binding = built.team_c.binding(tenant_id) if built.team_c is not None else None
    if built.team_c is None or binding is None:
        raise not_found("this business has no Team C tools")
    admin = built.team_c.admin
    if admin is None:
        raise upstream_unavailable("Team C's API address is not configured")
    try:
        found = await admin.publication(publication_id)
        if found is None or found.get("business_id") != binding.business_id:
            raise not_found("publication not found")  # another business's publication is answered as missing
        user = current_user(request)
        who = user.display_name if user is not None else "an operator"
        await admin.disable(publication_id, f"Disabled in Ticket-Easy by {who}: {body.note}")
    except UpstreamError as exc:
        raise upstream_unavailable(f"Team C could not be asked ({exc.code})") from None
    log.info("team_c_publication_disabled", tenant_id=tenant_id, publication_id=publication_id, actor=who)
    return await _overview(built, tenant_id, refresh=True)
