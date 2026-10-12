"""Onboarding endpoints of the knowledge service: documents, re-index, rule extraction and the rule list with its history.

All of them need X-Admin-Key. They are included by service.py (imported at its end, after the names used here exist).
  POST   /v1/admin/tenants/{t}/documents          store one document (JSON: filename + content_base64)
  GET    /v1/admin/tenants/{t}/documents          every upload with its status
  DELETE /v1/admin/tenants/{t}/documents/{id}     stop using a document (re-index to take it out of the search)
  POST   /v1/admin/tenants/{t}/reindex            background job; the new index replaces the old one only when complete
  GET    /v1/admin/jobs/{id}                      a job's status and result
  POST   /v1/admin/tenants/{t}/rules/extract      background job; the model only PROPOSES rules
  GET    /v1/admin/tenants/{t}/rules              rules with status, stale flag, history and the ruleset version
"""

import base64
import binascii
import re
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from team_a import config, jobs
from team_a.knowledge.documents import DocumentError, DocumentLibrary
from team_a.knowledge.index import TenantNotFound, load_manifest
from team_a.schemas import TENANT_PATTERN
from team_a.service import ServiceError, _index, _store, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class UploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1)
    document_id: str | None = Field(default=None, max_length=64)
    effective_date: date | None = None


class ExtractRequest(BaseModel):
    document_id: str | None = Field(default=None, max_length=64)


def _error(exc: DocumentError) -> ServiceError:
    return ServiceError(exc.status, exc.code, str(exc))


def _shipped(tenant_id: str) -> bool:
    return config.settings.category(tenant_id) is not None


@router.post("/v1/admin/tenants/{tenant_id}/documents", status_code=201)
def upload_document(tenant_id: str, req: UploadRequest) -> dict:
    """Store one document of a business (md, txt, html, docx, pdf, xlsx). It is checked at once; it enters the search only
    after the next re-index. A file that can not be read is kept as `failed` with the reason."""
    if not re.fullmatch(TENANT_PATTERN, tenant_id):
        raise ServiceError(422, "INVALID_REQUEST", "tenant_id: invalid")
    try:
        content = base64.b64decode(req.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise ServiceError(422, "INVALID_REQUEST", "content_base64: not valid base64")
    try:
        return DocumentLibrary(tenant_id).add(req.filename, content, req.document_id, req.effective_date)
    except DocumentError as exc:
        raise _error(exc)


@router.get("/v1/admin/tenants/{tenant_id}/documents")
def list_documents(tenant_id: str) -> dict:
    if _shipped(tenant_id):  # a shipped business: its manifest, read-only (indexed with `python -m team_a ingest`)
        manifest = load_manifest(tenant_id)
        return {"tenant_id": tenant_id, "managed": False, "documents": [
            {"document_id": d["document_id"], "version": d["version"], "file": d["file"], "current": bool(d.get("current")),
             "status": "indexed" if d.get("current") else "removed", "error": None} for d in manifest["documents"]]}
    library = DocumentLibrary(tenant_id)
    if not library.exists():
        raise ServiceError(404, "TENANT_NOT_FOUND", f"Unknown tenant '{tenant_id}'")
    try:
        current = {d["file"]: bool(d.get("current")) for d in load_manifest(tenant_id)["documents"]}
    except TenantNotFound:
        current = {}
    return {"tenant_id": tenant_id, "managed": True, "documents": [dict(r, current=current.get(r["file"], False)) for r in library.records()]}


@router.delete("/v1/admin/tenants/{tenant_id}/documents/{document_id}")
def remove_document(tenant_id: str, document_id: str) -> dict:
    """Stop using a document: every version is marked not current. Re-index to take it out of the search."""
    try:
        return {"document_id": document_id, "versions_removed": DocumentLibrary(tenant_id).remove(document_id), "reindex_needed": True}
    except DocumentError as exc:
        raise _error(exc)


@router.post("/v1/admin/tenants/{tenant_id}/reindex", status_code=202)
def reindex(tenant_id: str, embeddings: bool = True) -> dict:
    """Build the tenant's index again in the background and swap it in when it is complete (searches keep using the old
    one until then). Poll GET /v1/admin/jobs/{job_id}."""
    if _shipped(tenant_id):
        raise ServiceError(409, "INVALID_STATE", "this business ships with its own index: use `python -m team_a ingest`")
    library = DocumentLibrary(tenant_id)
    if not library.exists():
        raise ServiceError(404, "TENANT_NOT_FOUND", f"No documents for '{tenant_id}' yet")
    if jobs.running("reindex", tenant_id):
        raise ServiceError(409, "INVALID_STATE", "a re-index of this business is already running")

    def work() -> dict:
        try:
            result = library.reindex(embed=embeddings)
        except DocumentError as exc:
            raise RuntimeError(str(exc))
        _index.cache_clear()
        return result

    return jobs.start("reindex", tenant_id, work)


@router.get("/v1/admin/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = jobs.get(job_id)
    if job is None:
        raise ServiceError(404, "NOT_FOUND", f"No job '{job_id}'")
    return job


@router.post("/v1/admin/tenants/{tenant_id}/rules/extract", status_code=202)
def extract_rules(tenant_id: str, req: ExtractRequest) -> dict:
    """Ask the model to propose rules from the business's current documents. Every result is only `proposed`: a person
    approves it before it has any effect."""
    from team_a.policy.extract import extract_candidates

    try:
        load_manifest(tenant_id)
    except TenantNotFound as exc:
        raise ServiceError(404, "TENANT_NOT_FOUND", str(exc))
    if jobs.running("extract", tenant_id):
        raise ServiceError(409, "INVALID_STATE", "rule extraction for this business is already running")

    def work() -> dict:
        added = _store(tenant_id).add_proposed(extract_candidates(tenant_id, req.document_id))
        return {"added": len(added), "rule_ids": [r.rule_id for r in added]}

    return jobs.start("extract", tenant_id, work)


@router.get("/v1/admin/tenants/{tenant_id}/rules")
def list_tenant_rules(tenant_id: str) -> dict:
    """Every rule with its status, whether its source document is still current (`stale`: not enforced) and its history."""
    store = _store(tenant_id)
    current = store.current_documents()
    history = store.history()
    rules = []
    for rule in store.all():
        row = rule.model_dump(mode="json")
        row["stale"] = store.is_stale(rule, current)
        row["history"] = [h for h in history if h["rule_id"] == rule.rule_id][-5:]
        rules.append(row)
    return {"tenant_id": tenant_id, "ruleset": store.ruleset(), "rules": rules}
