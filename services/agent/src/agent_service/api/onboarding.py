"""The onboarding wizard: set a business up from nothing to live, step by step, resumable. Admin only (the owner).

  POST /v1/onboarding                        {profile}: create the business (idempotent on its name) -> the draft
  GET  /v1/onboarding                        the businesses being set up (to resume one)
  GET  /v1/onboarding/{t}                    one draft: where the owner stopped
  PUT  /v1/onboarding/{t}/profile            change the profile
  PUT  /v1/onboarding/{t}/step               {step, verify_tool?} remember the step and which tool checks the customer
  GET  /v1/onboarding/{t}/review             what is ready and what blocks "Go live"
  POST /v1/onboarding/{t}/preview            a signed pass and the address of the test chat (draft bundle, test tools
  only)
  POST /v1/onboarding/{t}/go-live            publish and activate: the business answers customers from now on

The documents, guardrails and tools steps use the knowledge and tool-setup pages with the new business id. Until "Go
live"
the public chat answers TENANT_NOT_LIVE. Nothing here stores a secret: the draft holds ids, names and statuses only.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict

from agent_service.api.auth import current_user
from agent_service.api.errors import ApiError, invalid_state, not_found, upstream_unavailable
from agent_service.bundles import BundleError
from agent_service.container import Container
from agent_service.contracts.errors import UpstreamError
from agent_service.domain.bundle import BundleGuardrails, BundleKnowledge, TenantBundle
from agent_service.domain.onboarding import STEPS, BusinessProfile, OnboardingDraft, Step
from agent_service.onboarding import bundle_profile, draft_config, generate_config, unique_id
from agent_service.ports import NotFoundError

router = APIRouter(prefix="/v1/onboarding", tags=["onboarding"])
TENANT_PATH = Annotated[str, Path(pattern=r"^[a-z0-9_]{1,64}$")]


class StepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step: Step
    verify_tool: str | None = None


def _built(request: Request) -> Container:
    built: Container = request.app.state.container
    if built.bundles is None or built.drafts is None:
        raise upstream_unavailable("tenant bundles are switched off (TEAM_B_BUNDLES=0)")
    return built


def _actor(request: Request) -> str:
    user = current_user(request)
    return user.display_name if user is not None else "dashboard"


async def _draft(built: Container, tenant_id: str) -> OnboardingDraft:
    assert built.drafts is not None
    draft = await built.drafts.get(tenant_id)
    if draft is None:
        raise not_found(f"no business is being set up as {tenant_id}")
    return draft


def _live(built: Container, tenant_id: str) -> bool:
    return tenant_id in built.tenants and (built.bundles is None or tenant_id not in built.bundles.drafts)


async def _view(built: Container, draft: OnboardingDraft) -> dict[str, Any]:
    assert built.bundles is not None
    bundle = built.bundles.draft_bundle(draft.tenant_id) or built.bundles.active_bundle(draft.tenant_id)
    return {
        "tenant_id": draft.tenant_id,
        "step": draft.step,
        "steps": list(STEPS),
        "profile": draft.profile.model_dump(mode="json"),
        "verify_tool": draft.verify_tool,
        "live": _live(built, draft.tenant_id),
        "bundle_version": bundle.version if bundle else None,
        "tools": [t.name for t in bundle.tools] if bundle else [],
        "updated_at": draft.updated_at,
        "updated_by": draft.updated_by,
    }


async def _save(built: Container, draft: OnboardingDraft, **changes: Any) -> OnboardingDraft:
    assert built.drafts is not None
    changed = draft.model_copy(update={**changes, "updated_at": built.clock.now()})
    changed = OnboardingDraft.model_validate(changed.model_dump())  # a draft is checked again whenever it changes
    await built.drafts.save(changed)
    return changed


@router.post("", status_code=201)
async def create(profile: BusinessProfile, request: Request) -> dict[str, Any]:
    """Create the business. Sending the same name again returns the draft that exists (nothing is duplicated)."""
    built = _built(request)
    assert built.drafts is not None and built.bundles is not None
    wanted = unique_id(profile.name, set())
    existing = await built.drafts.get(wanted)
    if existing is not None and existing.profile.name.lower() == profile.name.lower():
        return await _view(built, existing)
    if wanted in built.tenants and wanted not in built.bundles.drafts:
        raise invalid_state(f"a business called {profile.name!r} already exists")
    taken = set(built.tenants.tenant_ids()) | {d.tenant_id for d in await built.drafts.list()}
    tenant_id = unique_id(profile.name, taken)
    actor = _actor(request)
    try:
        await built.bundles.create_draft(
            tenant_id, created_by=actor, config=draft_config(tenant_id, profile), profile=bundle_profile(profile)
        )
    except (BundleError, ValueError) as exc:
        raise ApiError(422, "INVALID_REQUEST", str(exc)) from None
    draft = OnboardingDraft(
        tenant_id=tenant_id, step="profile", profile=profile, updated_at=built.clock.now(), updated_by=actor
    )
    await built.drafts.save(draft)
    return await _view(built, draft)


@router.get("")
async def list_drafts(request: Request) -> dict[str, Any]:
    built = _built(request)
    assert built.drafts is not None
    return {"businesses": [await _view(built, d) for d in await built.drafts.list()]}


@router.get("/{tenant_id}")
async def get_draft(tenant_id: TENANT_PATH, request: Request) -> dict[str, Any]:
    built = _built(request)
    return await _view(built, await _draft(built, tenant_id))


@router.put("/{tenant_id}/profile")
async def update_profile(tenant_id: TENANT_PATH, profile: BusinessProfile, request: Request) -> dict[str, Any]:
    built = _built(request)
    assert built.bundles is not None
    draft = await _draft(built, tenant_id)
    if _live(built, tenant_id):
        raise invalid_state("this business is live: change it with a new bundle version")
    base = built.bundles.draft_bundle(tenant_id)
    assert base is not None
    config = base.config.model_copy(
        update={
            "display_name": profile.name,
            "default_locale": profile.default_locale,
            "order_id_pattern": profile.order_id_pattern,
            "order_id_prefix": profile.order_id_prefix,
            "managers": profile.contacts,
        }
    )
    try:
        await built.bundles.create_draft(
            tenant_id, created_by=_actor(request), config=config, profile=bundle_profile(profile)
        )
    except (BundleError, ValueError) as exc:
        raise ApiError(422, "INVALID_REQUEST", str(exc)) from None
    return await _view(built, await _save(built, draft, profile=profile, updated_by=_actor(request)))


@router.put("/{tenant_id}/step")
async def set_step(tenant_id: TENANT_PATH, body: StepIn, request: Request) -> dict[str, Any]:
    built = _built(request)
    assert built.bundles is not None
    draft = await _draft(built, tenant_id)
    verify = body.verify_tool
    if verify is not None:
        bundle = built.bundles.draft_bundle(tenant_id) or built.bundles.active_bundle(tenant_id)
        if bundle is None or verify not in {t.name for t in bundle.tools}:
            raise ApiError(422, "INVALID_REQUEST", "verify_tool must be one of the business's tools")
    changes: dict[str, Any] = {"step": body.step, "updated_by": _actor(request)}
    if verify is not None:
        changes["verify_tool"] = verify
    return await _view(built, await _save(built, draft, **changes))


async def _readiness(built: Container, draft: OnboardingDraft) -> dict[str, Any]:
    assert built.bundles is not None
    tenant_id = draft.tenant_id
    bundle = built.bundles.draft_bundle(tenant_id) or built.bundles.active_bundle(tenant_id)
    tools = [t.name for t in bundle.tools] if bundle else []
    blockers: list[dict[str, str]] = []
    documents: dict[str, int] = {"indexed": 0, "failed": 0, "waiting": 0}
    rules: dict[str, int] = {"approved": 0, "pending": 0, "stale": 0}
    admin = built.knowledge_admin
    if admin is None:
        blockers.append({"code": "knowledge_unavailable", "message": "the knowledge service is not connected"})
    else:
        try:
            for doc in (await admin.documents(tenant_id)).get("documents", []):
                if not doc.get("current") and doc.get("status") != "failed":
                    continue
                key = {"indexed": "indexed", "failed": "failed"}.get(str(doc.get("status")), "waiting")
                documents[key] += 1
            for rule in (await admin.rules(tenant_id)).get("rules", []):
                if rule.get("stale"):
                    rules["stale"] += 1
                elif rule.get("approval_status") == "approved":
                    rules["approved"] += 1
                elif rule.get("approval_status") == "proposed":
                    rules["pending"] += 1
        except UpstreamError as exc:
            blockers.append(
                {
                    "code": "knowledge_unavailable",
                    "message": f"the knowledge service did not answer ({exc.code.lower()})",
                }
            )
    if (
        documents["indexed"] == 0
        and admin is not None
        and not any(b["code"] == "knowledge_unavailable" for b in blockers)
    ):
        blockers.append({"code": "no_documents", "message": "upload and index at least one document"})
    if documents["waiting"]:
        blockers.append({"code": "documents_waiting", "message": "update the search so the new documents can be used"})
    if rules["approved"] == 0 and admin is not None and not any(b["code"] == "knowledge_unavailable" for b in blockers):
        blockers.append({"code": "no_approved_rules", "message": "approve at least one guardrail rule"})
    if not tools:
        blockers.append({"code": "no_tools", "message": "publish at least one tool"})
    elif draft.verify_tool is None or draft.verify_tool not in tools:
        blockers.append({"code": "no_verify_tool", "message": "choose the tool that checks who the customer is"})
    previews = [t for t in await built.traces.query(tenant_id, limit=50) if t.versions.get("preview") == "1"]
    if not previews:
        blockers.append({"code": "no_test_chat", "message": "try the assistant in the test chat first"})
    return {
        "tenant_id": tenant_id,
        "documents": documents,
        "rules": rules,
        "tools": tools,
        "verify_tool": draft.verify_tool,
        "test_messages": len(previews),
        "blockers": blockers,
        "ready": not blockers,
        "live": _live(built, tenant_id),
    }


@router.get("/{tenant_id}/review")
async def review(tenant_id: TENANT_PATH, request: Request) -> dict[str, Any]:
    built = _built(request)
    return await _readiness(built, await _draft(built, tenant_id))


@router.post("/{tenant_id}/preview")
async def preview(tenant_id: TENANT_PATH, request: Request) -> dict[str, Any]:
    """A pass for the test chat. The chat answers with the draft configuration and the test tools only."""
    built = _built(request)
    await _draft(built, tenant_id)
    if built.previews is None:
        raise upstream_unavailable("previews are not available")
    token = built.previews.issue(tenant_id)
    return {"token": token, "chat_url": f"/chat?tenant_id={tenant_id}&preview={token}", "live": _live(built, tenant_id)}


@router.post("/{tenant_id}/go-live")
async def go_live(tenant_id: TENANT_PATH, request: Request) -> dict[str, Any]:
    """Generate the final configuration, publish it and make it the one in use. Refused while anything blocks it."""
    built = _built(request)
    assert built.bundles is not None
    draft = await _draft(built, tenant_id)
    if _live(built, tenant_id):
        raise invalid_state("this business is already live")
    state = await _readiness(built, draft)
    if state["blockers"]:
        raise ApiError(409, "INVALID_STATE", "not ready: " + "; ".join(b["message"] for b in state["blockers"]))
    registry = built.bundles
    current = registry.draft_bundle(tenant_id)
    assert current is not None
    actor = _actor(request)
    knowledge: BundleKnowledge | None = None
    guardrails: BundleGuardrails | None = None
    if built.knowledge_admin is not None:
        try:
            snapshot = await built.knowledge_admin.snapshot(tenant_id)
        except UpstreamError as exc:
            raise upstream_unavailable(f"the knowledge service did not answer ({exc.code.lower()})") from None
        knowledge = BundleKnowledge.model_validate(snapshot["knowledge"])
        guardrails = BundleGuardrails.model_validate(snapshot["guardrails"])
    try:
        final: TenantBundle = await registry.create_draft(
            tenant_id,
            created_by=actor,
            config=generate_config(current.config, draft.profile, current.tools, draft.verify_tool),
            profile=bundle_profile(draft.profile),
            knowledge=knowledge,
            guardrails=guardrails,
            tools=current.tools,
            from_version=current.version,
        )
        await registry.publish(tenant_id, final.version)
        live = await registry.activate(tenant_id, final.version, actor)
    except (BundleError, NotFoundError, ValueError) as exc:
        raise invalid_state(str(exc)) from None
    await _save(built, draft, step="review", updated_by=actor)
    if built.registry is not None:
        built.registry.drop(tenant_id)
    return {"tenant_id": tenant_id, "live": True, "bundle_version": live.version, "tools": [t.name for t in live.tools]}
