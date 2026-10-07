"""HTTP service exposing Team A capabilities to Team C's MCP adapter.

Run: uvicorn team_a.service:app --port 8001   (from team_a/ with src on PYTHONPATH)
Every error returns ErrorResponse with a stable code.
"""

import hmac
from datetime import date
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from team_a import config
from team_a.knowledge.embeddings import OllamaEmbedder
from team_a.knowledge.index import IndexNotBuilt, TenantIndex, TenantNotFound, load_manifest
from team_a.knowledge.resolutions import add_resolution, search_resolutions
from team_a.knowledge.retrieval import get_passage, search_knowledge, search_past_tickets
from team_a.policy.check import check_action
from team_a.policy.explain import explain_rule
from team_a.policy.guardrails import GuardrailRegression
from team_a.policy.risk import classify_risk
from team_a.policy.rules_store import RuleNotFound, RuleStore
from team_a.schemas import (
    TENANT_PATTERN,
    CheckActionRequest,
    ClassifyRiskRequest,
    ErrorBody,
    ErrorResponse,
    Passage,
    PastTicketResult,
    PolicyDecision,
    ResolutionSearchResult,
    ResolutionWriteResult,
    ResolvedEscalationRequest,
    RetrievalResult,
    RiskAssessment,
    Rule,
    RuleExplanation,
    SearchKnowledgeRequest,
    SearchPastTicketsRequest,
    SearchResolutionsRequest,
)

app = FastAPI(title="Ticket-Easy Team A: Knowledge + Policy", version="1.0")


class ServiceError(Exception):
    def __init__(self, status: int, code: str, message: str, request_id: str | None = None):
        self.status, self.code, self.message, self.request_id = status, code, message, request_id


def _error(status: int, code: str, message: str, request_id: str | None = None) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, request_id=request_id))
    return JSONResponse(status_code=status, content=body.model_dump())


@app.exception_handler(ServiceError)
async def _service_error(_: Request, exc: ServiceError):
    return _error(exc.status, exc.code, exc.message, exc.request_id)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(p) for p in first.get("loc", []))
    return _error(422, "INVALID_REQUEST", f"{where}: {first.get('msg', 'invalid request')}")


@lru_cache(maxsize=1)
def _embedder() -> OllamaEmbedder:
    return OllamaEmbedder()


@lru_cache(maxsize=32)
def _index(tenant_id: str) -> TenantIndex:
    return TenantIndex.load(tenant_id)


def load_index(tenant_id: str, request_id: str | None = None) -> TenantIndex:
    try:
        return _index(tenant_id)
    except TenantNotFound as exc:
        raise ServiceError(404, "TENANT_NOT_FOUND", str(exc), request_id)
    except IndexNotBuilt as exc:
        raise ServiceError(503, "INDEX_NOT_BUILT", str(exc), request_id)


def _store(tenant_id: str) -> RuleStore:
    return RuleStore(tenant_id)


def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
    """Gate admin/review endpoints on the X-Admin-Key header. Fails closed if ADMIN_API_KEY is unset."""
    expected = config.settings.admin_api_key
    if not expected or not x_admin_key or not hmac.compare_digest(x_admin_key, expected):
        raise ServiceError(401, "UNAUTHORIZED", "Missing or invalid X-Admin-Key")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ---------------------------------------------------------------- knowledge

@app.post("/v1/knowledge/search", response_model=RetrievalResult)
def search_knowledge_endpoint(req: SearchKnowledgeRequest) -> RetrievalResult:
    index = load_index(req.tenant_id, req.request_id)
    return search_knowledge(req, index, _embedder())


@app.get("/v1/knowledge/passages", response_model=Passage)
def get_passage_endpoint(
    tenant_id: str = Query(pattern=TENANT_PATTERN),
    citation: str = Query(min_length=3, max_length=200),
) -> Passage:
    passage = get_passage(load_index(tenant_id), citation)
    if passage is None:
        raise ServiceError(404, "NOT_FOUND", f"No passage with citation '{citation}'")
    return passage


@app.post("/v1/knowledge/past-tickets/search", response_model=PastTicketResult)
def search_past_tickets_endpoint(req: SearchPastTicketsRequest) -> PastTicketResult:
    index = load_index(req.tenant_id, req.request_id)
    return search_past_tickets(req, index, _embedder())


@app.post("/v1/knowledge/resolutions", response_model=ResolutionWriteResult, dependencies=[Depends(require_admin)])
def add_resolution_endpoint(req: ResolvedEscalationRequest) -> ResolutionWriteResult:
    """Store a redacted precedent once a case is resolved. Unsafe cases return stored=false with reasons."""
    try:
        load_manifest(req.tenant_id)
    except TenantNotFound as exc:
        raise ServiceError(404, "TENANT_NOT_FOUND", str(exc), req.request_id)
    result = add_resolution(req, _embedder())
    if result.stored:
        _index.cache_clear()
    return result


@app.post("/v1/knowledge/resolutions/search", response_model=ResolutionSearchResult,
          dependencies=[Depends(require_admin)])
def search_resolutions_endpoint(req: SearchResolutionsRequest) -> ResolutionSearchResult:
    """Advisory precedents for a human reviewer. Never consulted by check_action."""
    return search_resolutions(req, load_index(req.tenant_id, req.request_id), _embedder())


@app.post("/v1/admin/reload", dependencies=[Depends(require_admin)])
def reload_indexes() -> dict:
    """Drop cached indexes after re-running ingestion."""
    _index.cache_clear()
    return {"status": "reloaded"}


# ------------------------------------------------------------------- policy

@app.post("/v1/policy/check-action", response_model=PolicyDecision)
def check_action_endpoint(req: CheckActionRequest) -> PolicyDecision:
    return check_action(req, _store(req.tenant_id))


@app.post("/v1/policy/classify-risk", response_model=RiskAssessment)
def classify_risk_endpoint(req: ClassifyRiskRequest) -> RiskAssessment:
    return classify_risk(req)


@app.get("/v1/policy/rules/{rule_id}/explain", response_model=RuleExplanation)
def explain_rule_endpoint(
    rule_id: str, tenant_id: str = Query(pattern=TENANT_PATTERN), as_of: date | None = None
) -> RuleExplanation:
    try:
        return explain_rule(_store(tenant_id), rule_id, as_of)
    except RuleNotFound:
        raise ServiceError(404, "NOT_FOUND", f"No rule '{rule_id}' for tenant '{tenant_id}'")


@app.get("/v1/policy/rules", response_model=list[Rule], dependencies=[Depends(require_admin)])
def list_rules(tenant_id: str = Query(pattern=TENANT_PATTERN), status: str | None = None) -> list[Rule]:
    rules = _store(tenant_id).all()
    return [r for r in rules if status is None or r.approval_status == status]


class ReviewRequest(BaseModel):
    tenant_id: str = Field(pattern=TENANT_PATTERN)
    reviewer: str = Field(min_length=1, max_length=100)


class EditRequest(BaseModel):
    tenant_id: str = Field(pattern=TENANT_PATTERN)
    changes: dict


@app.post("/v1/policy/rules/{rule_id}/approve", response_model=Rule, dependencies=[Depends(require_admin)])
def approve_rule(rule_id: str, req: ReviewRequest) -> Rule:
    try:
        return _store(req.tenant_id).approve(rule_id, req.reviewer)
    except RuleNotFound:
        raise ServiceError(404, "NOT_FOUND", f"No rule '{rule_id}'")
    except GuardrailRegression as exc:
        raise ServiceError(422, "INVALID_REQUEST", str(exc))


@app.post("/v1/policy/rules/{rule_id}/reject", response_model=Rule, dependencies=[Depends(require_admin)])
def reject_rule(rule_id: str, req: ReviewRequest) -> Rule:
    try:
        return _store(req.tenant_id).reject(rule_id, req.reviewer)
    except RuleNotFound:
        raise ServiceError(404, "NOT_FOUND", f"No rule '{rule_id}'")


@app.patch("/v1/policy/rules/{rule_id}", response_model=Rule, dependencies=[Depends(require_admin)])
def edit_rule(rule_id: str, req: EditRequest) -> Rule:
    try:
        return _store(req.tenant_id).edit(rule_id, req.changes)
    except RuleNotFound:
        raise ServiceError(404, "NOT_FOUND", f"No rule '{rule_id}'")
    except ValueError as exc:
        raise ServiceError(422, "INVALID_REQUEST", str(exc))
