"""Business tools: which shop tools the agent uses, which of them Team C serves (sandbox or Live Demo), and their state.

GET  /v1/dashboard/business-tools?tenant_id=                         the page (manager, like every dashboard read)
POST /v1/dashboard/business-tools/refresh?tenant_id=                 ask the shop and Team C for the tool list again
POST /v1/dashboard/business-tools/publications/{id}/disable?tenant_id= {"note"}   stop a Team C tool (manager)
POST /v1/dashboard/business-tools/connectors/{id}/check?tenant_id=   Team C's real connection check (manager)
POST /v1/dashboard/business-tools/artifacts/{id}/auto-test?tenant_id=   automatic tests of one built tool (manager)
POST /v1/dashboard/business-tools/artifacts/{id}/promote?tenant_id= {"note","rollback_contact","accept_no_idempotency"}
     promote a tested tool to PRODUCTION (admin, signed in: the promotion records who decided it; Team C refuses
     unless its whole checklist passes)
POST /v1/dashboard/business-tools/promotions/{id}/rollback?tenant_id= {"note"}   switch a production tool off (admin)

Environments are shown as they are: SANDBOX (sandbox-only destinations and identities), LIVE DEMO (calls the
owner-configured real API) or PRODUCTION (a promoted tool that acts on real customers' data with the business's
service credential; Team C scopes every call to the verified customer). A tool is production only if Team C says so
for a promoted, active publication.

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

from agent_service.api.auth import current_user
from agent_service.api.chat import TENANT, checked_tenant
from agent_service.api.errors import ApiError, forbidden, not_found, upstream_unavailable
from agent_service.brain import gates
from agent_service.container import Container
from agent_service.contracts.errors import UpstreamError
from agent_service.observability import get_logger

router = APIRouter(prefix="/v1/dashboard/business-tools", tags=["business tools"])
PUBLICATION_ID = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
log = get_logger(__name__)

# What a Team C problem means, in words a business owner can act on.
PROBLEMS = {
    "not_published": "no sandbox publication of this tool is active in the tool service",
    "ambiguous": "two artifacts of the same version are published: disable one in the tool service",
    "kind_mismatch": "the published tool reads or writes differently from what this business declared",
    "environment_not_allowed": "its environment (e.g. Live Demo) is not allowed for this tool here",
    "no_spec": "this business has no declaration of the tool (spec) to check it against",
    "team_c_unavailable": "the tool service could not be asked",
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
    environment: str | None = None  # Team C tools: sandbox | live_demo
    api_host: str | None = None  # live_demo: the real API this tool calls


class PromotionCheck(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class TeamCPromotion(BaseModel):
    id: str
    source_artifact_id: str
    production_artifact_id: str | None = None
    publication_id: str | None = None
    status: str  # active | rolled_back | refused
    actor: str
    note: str
    rollback_contact: str
    created_at: str | None = None
    rolled_back_at: str | None = None
    rolled_back_by: str | None = None
    rollback_note: str | None = None
    checklist: list[PromotionCheck] = []


class TeamCPublication(BaseModel):
    id: str
    tool_name: str
    status: str  # published | disabled
    effective_status: str  # published | blocked (Team C's recheck failed) | disabled
    problem: str | None = None
    environment: str  # sandbox | live_demo | production
    execution_enabled: bool = False  # Team C would run a call right now (published and every re-check passes)
    production_ready: bool  # true only for a promoted production publication that is active and passes every re-check
    promotion_id: str | None = None
    published_at: str | None = None
    disabled_at: str | None = None
    proposal_id: str
    version: int
    bound_to: str | None = None  # the agent's tool name that uses this proposal, if any


class ConnectorCheck(BaseModel):
    check: str  # configuration | reachable | authentication | permissions | executable
    status: str  # ok | failed | unknown | skipped
    detail: str = ""


class TeamCConnector(BaseModel):
    id: str
    environment: str | None  # sandbox | live_demo
    host: str
    identities: int  # how many identities (credentials) it holds; names and secrets stay in Team C
    checked_at: str | None = None
    ready: bool | None = None  # every step of the last check was ok
    checks: list[ConnectorCheck] = []


class TeamCProposal(BaseModel):
    id: str
    name: str
    version: int
    state: str  # needs_clarification | needs_reconciliation | ready_for_review | approved_to_build | rejected | ...
    # The newest built tool of this proposal (Team C): not_tested | testing | needs_owner | ready_to_publish | published
    artifact_id: str | None = None
    readiness_state: str | None = None
    auto_test_status: str | None = None  # running | succeeded | failed (the latest automatic test run)
    auto_test_question: str | None = None  # what the owner still has to do, in Team C's words
    promotable: bool = False  # tested (ready or published), a production connector exists, not promoted yet
    promoted: bool = False  # an active promotion exists for this tool


class DashboardBusinessTools(BaseModel):
    tenant_id: str
    configured: bool  # is any tool of this business served by Team C?
    sandbox_only: bool = True  # no tool the agent uses calls a real API (every Team C tool in use is a sandbox one)
    production_connector: bool = False  # the business has a production connector in Team C (so a tool can be promoted)
    live_tools: int = 0  # tools the agent uses that call an owner-configured real API (Live Demo)
    production_tools: int = 0  # tools the agent uses that act on real customers' data (promoted to production)
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
    connectors: list[TeamCConnector] = []
    promotions: list[TeamCPromotion] = []


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
            item.environment, item.api_host = resolved.environment, resolved.api_host
        if spec is None:
            item.reason = (resolved.problem if resolved is not None else None) or "not_published"
            item.detail = PROBLEMS.get(item.reason, f"the shop does not publish {name}")
        else:
            gate = gates.check_tool(tenant, spec)
            item.status = "available" if gate.allowed else "blocked"
            item.reason, item.detail = gate.reason, gate.detail
            item.operation_kind = spec.operation_kind
        tools.append(item)
    live = sum(1 for t in tools if t.source == "team_c" and t.status == "available" and t.environment == "live_demo")
    production = sum(
        1 for t in tools if t.source == "team_c" and t.status == "available" and t.environment == "production"
    )
    out = DashboardBusinessTools(
        tenant_id=tenant_id,
        configured=binding is not None,
        tools=tools,
        live_tools=live,
        production_tools=production,
        sandbox_only=live == 0 and production == 0,
    )
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
        out.publications_error = "the tool service's API address (api_url) is not configured"
        return out
    by_proposal = {b.proposal_id: name for name, b in binding.tools.items() if b.proposal_id}
    try:
        publications = await admin.publications(binding.business_id)
        proposals = await admin.proposals(binding.business_id)
        connectors = await admin.connectors(binding.business_id)
    except UpstreamError as exc:
        out.publications_error = f"the tool service's API could not be asked ({exc.code})"
        return out
    out.publications = [_publication(p, by_proposal) for p in reversed(publications)]
    try:
        by_proposal_id = {str(a.get("proposal_id")): a for a in await admin.artifacts(binding.business_id)}
        promotions = await admin.promotions(binding.business_id)
    except UpstreamError:
        by_proposal_id, promotions = {}, []  # the tool list is still useful without the readiness state
    out.promotions = [_promotion(p) for p in promotions if p.get("id")]
    out.production_connector = any(c.get("environment") == "production" for c in connectors)
    active = {str(p.get("source_artifact_id")) for p in promotions if p.get("status") == "active"}
    out.proposals = [_proposal(p, by_proposal_id) for p in proposals if p.get("proposal_id") or p.get("id")]
    for proposal in out.proposals:
        proposal.promoted = proposal.artifact_id in active
        proposal.promotable = (
            out.production_connector
            and not proposal.promoted
            and proposal.readiness_state in ("ready_to_publish", "published")
        )
    out.connectors = [_connector(c) for c in connectors if c.get("id")]
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
        execution_enabled=p.get("execution_enabled") is True,
        production_ready=p.get("production_ready") is True,
        promotion_id=str(p["promotion_id"]) if p.get("promotion_id") else None,
        published_at=p.get("published_at"),
        disabled_at=p.get("disabled_at"),
        proposal_id=str(p.get("proposal_id") or ""),
        version=int(p.get("version") or 0),
        bound_to=by_proposal.get(str(p.get("proposal_id"))),
    )


def _connector(c: dict[str, Any]) -> TeamCConnector:
    found = c.get("last_check")
    last: dict[str, Any] = found if isinstance(found, dict) else {}
    return TeamCConnector(
        id=str(c["id"]),
        environment=c.get("environment"),
        host=str(c.get("host") or ""),
        identities=len(c.get("identities") or []),
        checked_at=last.get("checked_at"),
        ready=last.get("ready") if last else None,
        checks=[
            ConnectorCheck(
                check=str(x.get("check")), status=str(x.get("status")), detail=str(x.get("detail") or "")[:300]
            )
            for x in last.get("checks") or []
            if isinstance(x, dict)
        ],
    )


def _promotion(p: dict[str, Any]) -> TeamCPromotion:
    found = p.get("checklist")
    checklist: dict[str, Any] = found if isinstance(found, dict) else {}
    return TeamCPromotion(
        id=str(p["id"]),
        source_artifact_id=str(p.get("source_artifact_id") or ""),
        production_artifact_id=p.get("production_artifact_id"),
        publication_id=p.get("publication_id"),
        status=str(p.get("status") or ""),
        actor=str(p.get("actor") or ""),
        note=str(p.get("note") or "")[:500],
        rollback_contact=str(p.get("rollback_contact") or ""),
        created_at=p.get("created_at"),
        rolled_back_at=p.get("rolled_back_at"),
        rolled_back_by=p.get("rolled_back_by"),
        rollback_note=p.get("rollback_note"),
        checklist=[
            PromotionCheck(name=str(k), ok=bool(v.get("ok")), detail=str(v.get("detail") or "")[:300])
            for k, v in checklist.items()
            if isinstance(v, dict)
        ],
    )


def _proposal(p: dict[str, Any], built: dict[str, dict[str, Any]]) -> TeamCProposal:
    found = p.get("content")
    content: dict[str, Any] = found if isinstance(found, dict) else {}
    pid = str(p.get("proposal_id") or p.get("id"))  # Team C's proposal view names it proposal_id
    artifact = built.get(pid) or {}
    found_run = artifact.get("auto_test")
    run: dict[str, Any] = found_run if isinstance(found_run, dict) else {}
    asks = run.get("needs_owner") if isinstance(run.get("needs_owner"), list) else []
    first = asks[0] if asks and isinstance(asks[0], dict) else {}
    return TeamCProposal(
        id=pid,
        name=str(content.get("name") or p.get("name") or ""),
        version=int(p.get("version") or 0),
        state=str(p.get("state") or ""),
        artifact_id=str(artifact["id"]) if artifact.get("id") else None,
        readiness_state=str(artifact["readiness_state"]) if artifact.get("readiness_state") else None,
        auto_test_status=str(run["status"]) if run.get("status") else None,
        auto_test_question=str(first["question"])[:400] if first.get("question") else None,
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
        raise not_found("this business has no the tool service tools")
    admin = built.team_c.admin
    if admin is None:
        raise upstream_unavailable("the tool service's API address is not configured")
    try:
        found = await admin.publication(publication_id)
        if found is None or found.get("business_id") != binding.business_id:
            raise not_found("publication not found")  # another business's publication is answered as missing
        user = current_user(request)
        who = user.display_name if user is not None else "an operator"
        await admin.disable(publication_id, f"Disabled in Ticket-Easy by {who}: {body.note}")
    except UpstreamError as exc:
        raise upstream_unavailable(f"The tool service could not be asked ({exc.code})") from None
    log.info("team_c_publication_disabled", tenant_id=tenant_id, publication_id=publication_id, actor=who)
    return await _overview(built, tenant_id, refresh=True)


CONNECTOR_ID = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


@router.post("/connectors/{connector_id}/check", response_model=DashboardBusinessTools)
async def check_connector(connector_id: CONNECTOR_ID, tenant_id: TENANT, request: Request) -> DashboardBusinessTools:
    """Ask Team C to check one of this business's connectors for real (configuration, reachability, login)."""
    built = checked_tenant(request, tenant_id)
    binding = built.team_c.binding(tenant_id) if built.team_c is not None else None
    if built.team_c is None or binding is None or built.team_c.admin is None:
        raise not_found("this business has no the tool service connectors")
    admin = built.team_c.admin
    try:
        mine = {c.get("id") for c in await admin.connectors(binding.business_id)}
        if connector_id not in mine:
            raise not_found("connector not found")  # another business's connector is answered as missing
        await admin.check_connector(connector_id)
    except UpstreamError as exc:
        raise upstream_unavailable(f"The tool service could not be asked ({exc.code})") from None
    log.info("team_c_connector_checked", tenant_id=tenant_id, connector_id=connector_id)
    return await _overview(built, tenant_id, refresh=False)


ARTIFACT_ID = Annotated[str, Path(pattern=r"^[A-Za-z0-9_-]{1,64}$")]


@router.post("/artifacts/{artifact_id}/auto-test", response_model=DashboardBusinessTools)
async def auto_test(artifact_id: ARTIFACT_ID, tenant_id: TENANT, request: Request) -> DashboardBusinessTools:
    """Start Team C's automatic tests for one built tool of this business (accounts, records, scenarios, evidence).

    It only tests: nothing is approved or published from here, and Team C runs it against a sandbox only. The page polls
    the proposal's readiness_state until the run is over.
    """
    built = checked_tenant(request, tenant_id)
    binding = built.team_c.binding(tenant_id) if built.team_c is not None else None
    if built.team_c is None or binding is None or built.team_c.admin is None:
        raise not_found("this business has no the tool service tools")
    admin = built.team_c.admin
    try:
        mine = {a.get("id") for a in await admin.artifacts(binding.business_id)}
        if artifact_id not in mine:
            raise not_found("tool not found")  # another business's tool, or an older version, is answered as missing
        await admin.auto_test(artifact_id)
    except UpstreamError as exc:
        raise upstream_unavailable(f"The tool service could not be asked ({exc.code})") from None
    user = current_user(request)
    log.info(
        "team_c_auto_test_started",
        tenant_id=tenant_id,
        artifact_id=artifact_id,
        actor=user.display_name if user else "an operator",
    )
    return await _overview(built, tenant_id, refresh=False)


class PromoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=3, max_length=500)
    rollback_contact: str = Field(min_length=3, max_length=200)  # who to reach when this has to be switched off
    accept_no_idempotency: bool = False  # the admin accepts that the API cannot make a repeated write safe


class RollbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=3, max_length=500)


def _admin_name(request: Request) -> str:
    """A production decision is made by a named, signed-in person: it is recorded with their name."""
    user = current_user(request)
    if user is None:
        raise forbidden("production decisions need a signed-in admin")
    return str(user.display_name)


def _team_c_failure(exc: UpstreamError) -> ApiError:
    if exc.code in ("PROMOTION_REFUSED", "PRODUCTION_DISABLED", "PROMOTION_NOT_ACTIVE"):
        return ApiError(409, exc.code, exc.message)  # Team C's own words: which checks failed
    return upstream_unavailable(f"The tool service could not be asked ({exc.code})")


@router.post("/artifacts/{artifact_id}/promote", response_model=DashboardBusinessTools)
async def promote(
    artifact_id: ARTIFACT_ID, tenant_id: TENANT, body: PromoteIn, request: Request
) -> DashboardBusinessTools:
    """Promote one tested tool of this business to production (admin). Team C builds the production twin and runs its
    whole checklist (evidence, scoping, identical content, secret, smoke check, idempotency, rollback contact)."""
    built = checked_tenant(request, tenant_id)
    actor = _admin_name(request)
    binding = built.team_c.binding(tenant_id) if built.team_c is not None else None
    if built.team_c is None or binding is None or built.team_c.admin is None:
        raise not_found("this business has no the tool service tools")
    admin = built.team_c.admin
    try:
        if artifact_id not in {a.get("id") for a in await admin.artifacts(binding.business_id)}:
            raise not_found("tool not found")  # another business's tool is answered as missing
        result = await admin.promote(
            artifact_id,
            actor=actor,
            note=body.note,
            rollback_contact=body.rollback_contact,
            accept_no_idempotency=body.accept_no_idempotency,
        )
    except UpstreamError as exc:
        raise _team_c_failure(exc) from None
    log.info(
        "team_c_promoted_to_production",
        tenant_id=tenant_id,
        artifact_id=artifact_id,
        actor=actor,
        promotion_id=result.get("id"),
    )
    return await _overview(built, tenant_id, refresh=True)


@router.post("/promotions/{promotion_id}/rollback", response_model=DashboardBusinessTools)
async def rollback(
    promotion_id: PUBLICATION_ID, tenant_id: TENANT, body: RollbackIn, request: Request
) -> DashboardBusinessTools:
    """Switch a production tool off (admin). Its next call is refused; a write that already happened stays."""
    built = checked_tenant(request, tenant_id)
    actor = _admin_name(request)
    binding = built.team_c.binding(tenant_id) if built.team_c is not None else None
    if built.team_c is None or binding is None or built.team_c.admin is None:
        raise not_found("this business has no the tool service tools")
    admin = built.team_c.admin
    try:
        if promotion_id not in {p.get("id") for p in await admin.promotions(binding.business_id)}:
            raise not_found("promotion not found")
        await admin.rollback(promotion_id, actor=actor, note=body.note)
    except UpstreamError as exc:
        raise _team_c_failure(exc) from None
    log.info("team_c_production_rolled_back", tenant_id=tenant_id, promotion_id=promotion_id, actor=actor)
    return await _overview(built, tenant_id, refresh=True)
