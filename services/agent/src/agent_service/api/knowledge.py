"""The knowledge and guardrail pages: a business's documents, the search index, and the rules derived from them.

Every route is under /v1/dashboard with ?tenant_id= (managers). The signed-in person is the reviewer: their name,
not one the request sends, is what Team A records on an approval, a rejection or an edit.
  GET    /knowledge/documents               every upload with its status (uploaded, parsing, indexed, failed, removed)
  POST   /knowledge/documents               {filename, content_base64, document_id?}: stored and checked at once
  DELETE /knowledge/documents/{id}          stop using a document (re-index to take it out of the search)
  POST   /knowledge/reindex                 start the background re-index; poll the job
  GET    /knowledge/jobs/{id}               a job's status and result
  GET    /guardrails                        the rules, each with its quoted source, status, stale flag, history
  POST   /guardrails/extract                ask the model to propose rules (they stay "proposed")
  POST   /guardrails/{rule_id}/approve|reject|edit
A rule derived from a document version that is no longer current is "stale": shown for review, not enforced.
"""

import base64
import binascii
from typing import Annotated, Any

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from agent_service.api.auth import current_user
from agent_service.api.chat import TENANT, checked_tenant
from agent_service.api.errors import ApiError, invalid_state, not_found, upstream_unavailable
from agent_service.container import Container
from agent_service.contracts.errors import UpstreamError
from agent_service.ports import KnowledgeAdmin

router = APIRouter(prefix="/v1/dashboard", tags=["knowledge"])
ID = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.-]{1,80}$")]
MAX_UPLOAD = 10 * 1024 * 1024


class UploadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1, max_length=MAX_UPLOAD * 2)
    document_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")


class ExtractIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_]{0,63}$")


class EditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: dict[str, Any] = Field(min_length=1)


def _admin(request: Request) -> KnowledgeAdmin:
    built: Container = request.app.state.container
    if built.knowledge_admin is None:
        raise upstream_unavailable("the knowledge service is not connected (TEAM_B_TEAM_A_ADMIN_KEY)")
    return built.knowledge_admin


def _failure(exc: UpstreamError) -> ApiError:
    if exc.code in ("TIMEOUT", "BACKEND_UNAVAILABLE", "BAD_RESPONSE"):
        return upstream_unavailable("the knowledge service did not answer: try again in a moment")
    if exc.code in ("NOT_FOUND", "TENANT_NOT_FOUND"):
        return not_found(exc.message)
    if exc.code == "UNAUTHORIZED":
        return upstream_unavailable("the knowledge service refused our key")
    if exc.code == "INVALID_STATE":
        return invalid_state(exc.message)
    return ApiError(
        422, "INVALID_REQUEST", exc.message
    )  # the reason Team A gave (an unreadable file, a failing guardrail case)


def reviewer(request: Request) -> str:
    user = current_user(request)
    return user.display_name if user is not None else "dashboard"


@router.get("/knowledge/documents")
async def documents(tenant_id: TENANT, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    try:
        return await _admin(request).documents(tenant_id)
    except UpstreamError as exc:
        if exc.code in ("NOT_FOUND", "TENANT_NOT_FOUND"):
            # a known business with nothing uploaded yet: the knowledge service learns of it with its first file
            return {"tenant_id": tenant_id, "managed": True, "documents": []}
        raise _failure(exc) from None


@router.post("/knowledge/documents", status_code=201)
async def upload(tenant_id: TENANT, body: UploadIn, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    try:
        content = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise ApiError(422, "INVALID_REQUEST", "content_base64 is not valid base64") from None
    if len(content) > MAX_UPLOAD:
        raise ApiError(413, "INVALID_REQUEST", "the file is larger than 10 MB")
    try:
        return await _admin(request).upload(tenant_id, body.filename, content, body.document_id)
    except UpstreamError as exc:
        raise _failure(exc) from None


@router.delete("/knowledge/documents/{document_id}")
async def remove(document_id: ID, tenant_id: TENANT, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    try:
        return await _admin(request).remove(tenant_id, document_id)
    except UpstreamError as exc:
        raise _failure(exc) from None


@router.post("/knowledge/reindex", status_code=202)
async def reindex(tenant_id: TENANT, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    try:
        return await _admin(request).reindex(tenant_id)
    except UpstreamError as exc:
        raise _failure(exc) from None


@router.get("/knowledge/jobs/{job_id}")
async def job(job_id: ID, tenant_id: TENANT, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    try:
        found = await _admin(request).job(job_id)
    except UpstreamError as exc:
        raise _failure(exc) from None
    if found.get("tenant_id") != tenant_id:
        raise not_found("no such job for this business")
    if str(found.get("error") or "").startswith("LLMUnavailable"):
        # the service's own words name its settings; the owner needs to know what is missing and who can fix it
        found = {
            **found,
            "error": "The AI that reads your documents is not set up here. Ask your administrator to switch it on.",
        }
    return found


@router.get("/guardrails")
async def guardrails(tenant_id: TENANT, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    try:
        return await _admin(request).rules(tenant_id)
    except UpstreamError as exc:
        raise _failure(exc) from None


@router.post("/guardrails/extract", status_code=202)
async def extract(tenant_id: TENANT, body: ExtractIn, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    try:
        return await _admin(request).extract_rules(tenant_id, body.document_id)
    except UpstreamError as exc:
        raise _failure(exc) from None


@router.post("/guardrails/{rule_id}/approve")
async def approve(rule_id: ID, tenant_id: TENANT, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    try:
        return await _admin(request).approve_rule(tenant_id, rule_id, reviewer(request))
    except UpstreamError as exc:
        raise _failure(exc) from None


@router.post("/guardrails/{rule_id}/reject")
async def reject(rule_id: ID, tenant_id: TENANT, request: Request) -> dict[str, Any]:
    checked_tenant(request, tenant_id)
    try:
        return await _admin(request).reject_rule(tenant_id, rule_id, reviewer(request))
    except UpstreamError as exc:
        raise _failure(exc) from None


@router.post("/guardrails/{rule_id}/edit")
async def edit(rule_id: ID, tenant_id: TENANT, body: EditIn, request: Request) -> dict[str, Any]:
    """Change a rule. Any edit sends it back to "proposed": it must be approved again."""
    checked_tenant(request, tenant_id)
    try:
        return await _admin(request).edit_rule(tenant_id, rule_id, body.changes)
    except UpstreamError as exc:
        raise _failure(exc) from None
