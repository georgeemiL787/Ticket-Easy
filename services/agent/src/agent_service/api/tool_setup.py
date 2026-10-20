"""The guided tool setup, in three screens: connect and import, review and select, test and publish.

Every route is under /v1/dashboard/tool-setup with ?tenant_id= (managers, like the rest of the dashboard):
  GET  ""                          where this business stands: its tools-service business, API descriptions, batches
  POST /import                     an OpenAPI/Swagger file ({filename, content}) or a URL ({url})
  GET  /operations                 the operations of a description, grouped read / write / risky, paginated
  POST /connection-check           is the API reachable? (no credentials are sent)
  POST /batches                    the owner's selection: one tool is prepared for each operation
  GET  /batches/{id}               its state (the tools service prepares, builds and tests in the background)
  POST /batches/{id}/approve       the owner's confirmations; writes need the four effect flags
  POST /batches/{id}/retry/{op}    prepare one tool again
  POST /batches/{id}/publish       publish what passed to the sandbox and write it into a DRAFT bundle
  POST /batches/{id}/activate      make that bundle the one in use (the owner's step; the next message uses it)

The business in the tools service is found by name (ticket-easy:<tenant>) or created on the first import, so there is
nothing to set up by hand. Credentials entered here go to the tools service once, sealed there; the agent never stores
or
logs them. Everything the tools service decides (access policy, tests, publication gates) stays decided there.
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from agent_service.adapters.tools_admin_client import ToolsAdminClient
from agent_service.api.auth import current_user
from agent_service.api.chat import TENANT, checked_tenant
from agent_service.api.errors import ApiError, invalid_state, not_found, upstream_unavailable
from agent_service.bundles import BundleError, tools_from_config
from agent_service.container import Container
from agent_service.contracts.errors import UpstreamError
from agent_service.domain.bundle import BundleTool
from agent_service.observability import get_logger
from agent_service.ports import NotFoundError
from agent_service.tool_setup import allow, tool_for, unique_name

router = APIRouter(prefix="/v1/dashboard/tool-setup", tags=["tool setup"])
log = get_logger(__name__)
MAX_UPLOAD = 10 * 1024 * 1024
BATCH = Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{8,64}$")]


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
    base_url: str = Field(max_length=500)
    key_ref: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class Connection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str | None = Field(default=None, max_length=500)
    fixtures: Fixtures | None = None
    accounts: list[Account] = Field(default=[], max_length=10)


class Access(BaseModel):
    model_config = ConfigDict(extra="forbid")
    callers: Literal["customer", "public"] = "customer"
    own_records_only: bool = True


class BatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec_id: str = Field(min_length=8, max_length=64)
    operation_ids: list[str] = Field(min_length=1, max_length=25)
    verify_operation_id: str | None = None
    access: Access = Access()
    connection: Connection = Connection()


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_ids: list[str] = Field(min_length=1, max_length=25)
    confirmations: dict[str, dict[str, bool]] = {}


class PublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    names: dict[str, str] = {}  # proposal id -> the tool name the agent uses (default: from the tool's name)


class ConnectionCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(min_length=8, max_length=500)


def _admin(request: Request) -> tuple[Container, ToolsAdminClient]:
    built: Container = request.app.state.container
    admin = built.team_c.admin if built.team_c is not None else None
    if admin is None:
        raise upstream_unavailable("the tools service is not connected (TEAM_B_TEAM_C_CONFIG with api_url)")
    return built, admin


def _actor(request: Request) -> str:
    user = current_user(request)
    return user.display_name if user is not None else "dashboard"


KNOWN_REFUSALS = {
    "fetch_failed": (
        "We could not reach that address. Check that your system is running and the address is right, "
        "or upload the file instead."
    ),
    "fetch_url": "Enter a full web address that starts with http:// or https://, without a password in it.",
    "fetch_status": "That address answered with an error. Check the address, or upload the file instead.",
    "fetch_host": (
        "We are not allowed to read from that address. Upload the file instead, or ask your administrator to allow it."
    ),
    "fetch_disabled": "Reading from a web address is switched off here. Upload the file instead.",
    "file_type": (
        "That address did not return an API description. Use the address of the OpenAPI file (JSON or YAML), "
        "or upload it."
    ),
    "upload_limit": "That file is too large (the limit is 10 MB).",
}


def _failure(exc: UpstreamError) -> ApiError:
    """The tools service is down (try again) or refused this (say so)."""
    if exc.code in ("TIMEOUT", "BACKEND_UNAVAILABLE", "BAD_RESPONSE"):
        return upstream_unavailable("the tools service did not answer: try again in a moment")
    said = KNOWN_REFUSALS.get(exc.code.lower())
    return invalid_state(said or f"this could not be done ({exc.code.lower()})")


async def _business(built: Container, admin: ToolsAdminClient, tenant_id: str, *, create: bool) -> str | None:
    """The tools-service business of this tenant: the one its tools are already bound to, else the one named
    ticket-easy:<tenant>, else (when `create`) a new one."""
    bound = built.team_c.binding(tenant_id) if built.team_c is not None else None
    if bound is not None and bound.business_id:
        return bound.business_id
    active = (
        (built.bundles.active_bundle(tenant_id) or built.bundles.draft_bundle(tenant_id))
        if built.bundles is not None
        else None
    )
    for tool in active.tools if active is not None else ():
        if tool.binding is not None:
            return tool.binding.business_id
    wanted = f"ticket-easy:{tenant_id}"
    for found in await admin.businesses():
        if found.get("name") == wanted and found.get("id"):
            return str(found["id"])
    if not create:
        return None
    name = built.tenants.get(tenant_id).display_name
    made = await admin.create_business(wanted, f"{name}: tools for the support assistant, set up from the dashboard.")
    return str(made["id"])


async def _owned(request: Request, tenant_id: str, batch_id: str) -> tuple[Container, ToolsAdminClient, dict[str, Any]]:
    """The batch, only if it belongs to this tenant's business (else 404, as if it did not exist)."""
    built, admin = _admin(request)
    business = await _business(built, admin, tenant_id, create=False)
    batch = await admin.batch(batch_id)
    if batch is None or business is None or batch.get("business_id") != business:
        raise not_found("no such tool batch for this business")
    return built, admin, batch


@router.get("")
async def state(tenant_id: TENANT, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    built, admin = _admin(request)
    try:
        business = await _business(built, admin, tenant_id, create=False)
        if business is None:
            return {"tenant_id": tenant_id, "business_id": None, "specs": [], "batches": []}
        specs = await admin.specifications(business)
        batches = await admin.batches(business)
    except UpstreamError as exc:
        raise _failure(exc) from None
    return {"tenant_id": tenant_id, "business_id": business, "specs": specs, "batches": batches}


class ImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str | None = Field(
        default=None, max_length=1000
    )  # fetched by the tools service from an address its operator allows
    filename: str | None = Field(default=None, max_length=200)  # or the file's name and text, read in the browser
    content: str | None = Field(default=None, max_length=MAX_UPLOAD)


@router.post("/import")
async def import_spec(tenant_id: TENANT, body: ImportIn, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    built, admin = _admin(request)
    if (body.url is None) == (body.content is None):
        raise ApiError(422, "INVALID_REQUEST", "send either a URL or a file (filename and content)")
    try:
        business = await _business(built, admin, tenant_id, create=True)
        assert business is not None
        if body.content is not None:
            found = await admin.upload_spec(business, body.filename or "api.json", body.content.encode("utf-8"))
        else:
            found = await admin.fetch_spec(business, str(body.url))
    except UpstreamError as exc:
        raise _failure(exc) from None
    inventory = found.get("inventory") or {}
    return {
        "business_id": business,
        "spec_id": found.get("id"),
        "valid": bool(inventory.get("proposal_generation_ready")),
        "operations": len(inventory.get("operations") or []),
        "usable": inventory.get("eligible_operation_count", 0),
        "converted_from": inventory.get("converted_from"),
        "problems": [
            d.get("message")
            for d in inventory.get("diagnostics") or []
            if d.get("severity") == "error" and d.get("scope") == "document"
        ][:5],
    }


@router.get("/operations")
async def operations(
    tenant_id: TENANT,
    request: Request,
    spec_id: Annotated[str, Query(min_length=8, max_length=64)],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    klass: Annotated[Literal["read", "write", "risky", "restricted"] | None, Query(alias="class")] = None,
    q: Annotated[str, Query(max_length=100)] = "",
) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    _built, admin = _admin(request)
    try:
        return await admin.operations(spec_id, offset=offset, limit=limit, klass=klass, q=q)
    except UpstreamError as exc:
        raise _failure(exc) from None


@router.post("/connection-check")
async def connection_check(tenant_id: TENANT, body: ConnectionCheckIn, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    built, admin = _admin(request)
    try:
        business = await _business(built, admin, tenant_id, create=True)
        assert business is not None
        return await admin.connection_check(business, body.base_url)
    except UpstreamError as exc:
        raise _failure(exc) from None


@router.post("/batches")
async def create_batch(tenant_id: TENANT, body: BatchIn, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    built, admin = _admin(request)
    try:
        business = await _business(built, admin, tenant_id, create=True)
        assert business is not None
        payload = body.model_dump(exclude_none=True)
        payload["actor"] = _actor(request)
        return await admin.create_batch(business, payload)
    except UpstreamError as exc:
        raise _failure(exc) from None


@router.get("/batches/{batch_id}")
async def get_batch(batch_id: str, tenant_id: TENANT, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    _built, _admin_client, batch = await _owned(request, tenant_id, batch_id)
    return batch


@router.post("/batches/{batch_id}/approve")
async def approve(batch_id: str, tenant_id: TENANT, body: ApproveIn, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    _built, admin, _batch = await _owned(request, tenant_id, batch_id)
    try:
        return await admin.approve_batch(batch_id, {**body.model_dump(), "actor": _actor(request)})
    except UpstreamError as exc:
        if exc.code == "EFFECTS_UNCONFIRMED":
            raise ApiError(
                422, "INVALID_REQUEST", "confirm what each tool that changes data does before approving it"
            ) from None
        raise _failure(exc) from None


@router.post("/batches/{batch_id}/retry/{operation_id}")
async def retry(batch_id: str, operation_id: str, tenant_id: TENANT, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    _built, admin, _batch = await _owned(request, tenant_id, batch_id)
    try:
        return await admin.retry_batch_item(batch_id, operation_id)
    except UpstreamError as exc:
        raise _failure(exc) from None


@router.post("/batches/{batch_id}/publish")
async def publish(batch_id: str, tenant_id: TENANT, body: PublishIn, request: Request) -> dict[str, Any]:
    """Publish what passed, then record those tools in a draft bundle. Nothing is in use until /activate."""
    checked_tenant(request, tenant_id)
    built, admin, _batch = await _owned(request, tenant_id, batch_id)
    if built.bundles is None:
        raise upstream_unavailable("tenant bundles are switched off (TEAM_B_BUNDLES=0)")
    actor = _actor(request)
    try:
        batch = await admin.publish_batch(batch_id, actor)
        published = [i for i in batch.get("items", []) if i.get("state") == "published"]
        registry = built.bundles
        base = registry.active_bundle(tenant_id) or registry.draft_bundle(
            tenant_id
        )  # a business still being set up has a draft
        config = base.config if base else registry.file_config(tenant_id)
        if config is None:
            raise invalid_state("set up the business profile first: this business has no configuration to add tools to")
        binding = built.team_c.binding(tenant_id) if built.team_c is not None else None
        existing = list(base.tools) if base else list(tools_from_config(config, binding))
        taken = {t.name for t in existing}
        tools: list[BundleTool] = []
        for item in published:
            artifact = await admin.artifact(str(item.get("artifact_id")))
            if artifact is None:
                continue
            wanted = body.names.get(str(item.get("proposal_id"))) or str(
                (item.get("summary") or {}).get("name") or item.get("title") or item.get("path")
            )
            name = unique_name(wanted, taken)
            taken.add(name)
            tool, _spec = tool_for(name=name, item=item, artifact=artifact, business_id=str(batch["business_id"]))
            tools.append(tool)
        identities = {
            str(next(iter(a["context"].values()))): a["name"]
            for a in batch.get("settings", {}).get("accounts", [])
            if a.get("context")
        }
        draft = await registry.create_draft(
            tenant_id,
            created_by=actor,
            config=allow(config, [t.name for t in tools]),
            tools=(*existing, *tools),
            identities={**(dict(base.identities) if base else {}), **identities},
        )
    except UpstreamError as exc:
        raise _failure(exc) from None
    except (BundleError, NotFoundError) as exc:
        raise invalid_state(str(exc)) from None
    return {"batch": batch, "bundle_version": draft.version, "tools": [t.name for t in tools]}


@router.post("/batches/{batch_id}/activate")
async def activate(batch_id: str, tenant_id: TENANT, body: dict[str, int], request: Request) -> dict[str, Any]:
    """Make the draft bundle /publish wrote the one in use: `{"bundle_version": N}`. The next message uses it."""
    checked_tenant(request, tenant_id)
    built, _admin_client, _batch = await _owned(request, tenant_id, batch_id)
    if built.bundles is None:
        raise upstream_unavailable("tenant bundles are switched off (TEAM_B_BUNDLES=0)")
    version = body.get("bundle_version")
    if not isinstance(version, int):
        raise ApiError(422, "INVALID_REQUEST", "bundle_version is required")
    try:
        await built.bundles.publish(tenant_id, version)
        bundle = await built.bundles.activate(tenant_id, version, _actor(request))
    except (BundleError, NotFoundError) as exc:
        raise invalid_state(str(exc)) from None
    if built.registry is not None:
        built.registry.drop(tenant_id)
    log.info(
        "tools_activated", tenant_id=tenant_id, bundle_version=bundle.version, tools=[t.name for t in bundle.tools]
    )
    return {"bundle_version": bundle.version, "status": bundle.status, "tools": [t.name for t in bundle.tools]}
