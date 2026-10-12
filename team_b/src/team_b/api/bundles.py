"""Tenant configuration bundles: list, read, create a draft, publish, activate, roll back.

Reading is for managers who may see the business; every change is admin only (a route not listed in api/auth.ROLES needs
admin). Activating swaps the configuration the brain uses, so the next customer message already runs on it.
  GET  /v1/admin/tenants/{t}/bundles            versions, newest first
  GET  /v1/admin/tenants/{t}/bundles/active     the active bundle (404 if the business runs on its file)
  GET  /v1/admin/tenants/{t}/bundles/{v}        one version in full
  POST /v1/admin/tenants/{t}/bundles            a new draft (from a version, from the file, or from the parts sent)
  POST /v1/admin/tenants/{t}/bundles/{v}/publish | activate     POST .../bundles/rollback   (back to the parent version)
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from team_b.api.auth import current_user
from team_b.api.errors import ApiError, invalid_state, not_found, tenant_not_found
from team_b.bundles import BundleError, BundleRegistry, import_from_files
from team_b.container import Container
from team_b.contracts.errors import UpstreamError
from team_b.domain.bundle import BundleGuardrails, BundleKnowledge, BundleProfile, BundleTool, TenantBundle
from team_b.domain.tenant import TenantConfig
from team_b.ports import NotFoundError

router = APIRouter(prefix="/v1/admin/tenants/{tenant_id}/bundles", tags=["bundles"])
TENANT_PATH = Annotated[str, Path(pattern=r"^[a-z0-9_]{1,64}$")]
VERSION = Annotated[int, Path(ge=1)]


class BundleSummary(BaseModel):
    tenant_id: str
    version: int
    status: str
    created_at: datetime
    created_by: str
    parent_version: int | None
    content_sha256: str

    @classmethod
    def of(cls, bundle: TenantBundle) -> "BundleSummary":
        return cls(
            tenant_id=bundle.tenant_id,
            version=bundle.version,
            status=bundle.status,
            created_at=bundle.created_at,
            created_by=bundle.created_by,
            parent_version=bundle.parent_version,
            content_sha256=bundle.content_sha256,
        )


class BundleList(BaseModel):
    tenant_id: str
    active_version: int | None
    bundles: list[BundleSummary]


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_version: int | None = Field(default=None, ge=1)  # copy this version (default: the active one)
    from_files: bool = False  # start from config/tenants/<tenant>.json
    config: TenantConfig | None = None
    profile: BundleProfile | None = None
    knowledge: BundleKnowledge | None = None
    guardrails: BundleGuardrails | None = None
    tools: list[BundleTool] | None = None
    refresh_knowledge: bool = (
        False  # take the knowledge and guardrail sections from the knowledge service as they stand now
    )


def registry_of(request: Request) -> BundleRegistry:
    built: Container = request.app.state.container
    if built.bundles is None:
        raise ApiError(503, "UPSTREAM_UNAVAILABLE", "tenant bundles are switched off (TEAM_B_BUNDLES=0)")
    return built.bundles


def checked(request: Request, tenant_id: str) -> BundleRegistry:
    """The registry, after checking the signed-in person may see this business (the guard checks query ids only)."""
    user = current_user(request)
    if user is not None and not user.can_see(tenant_id):
        raise tenant_not_found(tenant_id)
    return registry_of(request)


def actor(request: Request) -> str:
    user = current_user(request)
    return user.display_name if user is not None else "system"


def refused(exc: Exception) -> ApiError:
    if isinstance(exc, NotFoundError):
        return not_found(str(exc))
    return invalid_state(str(exc))


@router.get("", response_model=BundleList)
async def list_bundles(tenant_id: TENANT_PATH, request: Request) -> BundleList:
    registry = checked(request, tenant_id)
    found = await registry.store.list(tenant_id)
    active = registry.active_bundle(tenant_id)
    return BundleList(
        tenant_id=tenant_id,
        active_version=active.version if active else None,
        bundles=[BundleSummary.of(b) for b in found],
    )


@router.get("/active", response_model=TenantBundle)
async def active_bundle(tenant_id: TENANT_PATH, request: Request) -> TenantBundle:
    found = checked(request, tenant_id).active_bundle(tenant_id)
    if found is None:
        raise not_found(f"{tenant_id} runs on its tenant file: no bundle is active")
    return found


@router.get("/{version}", response_model=TenantBundle)
async def get_bundle(tenant_id: TENANT_PATH, version: VERSION, request: Request) -> TenantBundle:
    try:
        return await checked(request, tenant_id).get_bundle(tenant_id, version)
    except NotFoundError as exc:
        raise refused(exc) from None


@router.post("", response_model=TenantBundle, status_code=201)
async def create_draft(tenant_id: TENANT_PATH, body: DraftIn, request: Request) -> TenantBundle:
    registry = checked(request, tenant_id)
    knowledge, guardrails = body.knowledge, body.guardrails
    if body.refresh_knowledge:
        built: Container = request.app.state.container
        if built.knowledge_admin is None:
            raise ApiError(503, "UPSTREAM_UNAVAILABLE", "the knowledge service is not connected")
        try:
            snapshot = await built.knowledge_admin.snapshot(tenant_id)
        except UpstreamError as exc:
            raise ApiError(
                503, "UPSTREAM_UNAVAILABLE", f"the knowledge service did not answer ({exc.code.lower()})"
            ) from None
        knowledge = BundleKnowledge.model_validate(snapshot["knowledge"])
        guardrails = BundleGuardrails.model_validate(snapshot["guardrails"])
    try:
        if body.from_files:
            return await import_from_files(
                registry, tenant_id, actor(request), knowledge=knowledge, guardrails=guardrails
            )
        return await registry.create_draft(
            tenant_id,
            created_by=actor(request),
            config=body.config,
            profile=body.profile,
            knowledge=knowledge,
            guardrails=guardrails,
            tools=tuple(body.tools) if body.tools is not None else None,
            from_version=body.from_version,
        )
    except (BundleError, NotFoundError) as exc:
        raise refused(exc) from None


@router.post("/rollback", response_model=TenantBundle)
async def rollback(tenant_id: TENANT_PATH, request: Request) -> TenantBundle:
    try:
        return await checked(request, tenant_id).rollback(tenant_id, actor(request))
    except (BundleError, NotFoundError) as exc:
        raise refused(exc) from None


@router.post("/{version}/publish", response_model=TenantBundle)
async def publish(tenant_id: TENANT_PATH, version: VERSION, request: Request) -> TenantBundle:
    try:
        return await checked(request, tenant_id).publish(tenant_id, version)
    except (BundleError, NotFoundError) as exc:
        raise refused(exc) from None


@router.post("/{version}/activate", response_model=TenantBundle)
async def activate(tenant_id: TENANT_PATH, version: VERSION, request: Request) -> TenantBundle:
    try:
        return await checked(request, tenant_id).activate(tenant_id, version, actor(request))
    except (BundleError, NotFoundError) as exc:
        raise refused(exc) from None
