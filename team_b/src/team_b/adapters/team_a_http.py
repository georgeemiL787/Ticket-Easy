"""The real Team A service over HTTP: policy search, safety screen and rule checker (TEAM_B_MODE=live).

HttpEvidenceProvider is the EvidenceProvider plug and HttpPolicyGate is the PolicyGate plug. Both talk to the service in
team_a/ (POST /v1/knowledge/search, GET /v1/knowledge/passages, POST /v1/knowledge/past-tickets/search,
POST /v1/policy/classify-risk, POST /v1/policy/check-action). Failures follow the plug rules: a service that cannot be
reached, is too slow, refuses us or answers nonsense raises UpstreamError, and the brain then hands the case to a human
(nothing is executed on a failed rule check).

Team A does not know human approvals. HttpPolicyGate applies them here, exactly as the stand-in does: a recorded
approval turns require_human into allow, and never a deny.
"""

from typing import Any, TypeVar

import httpx
from pydantic import ValidationError

from team_b.contracts.base import PlugModel
from team_b.contracts.errors import UpstreamError
from team_b.contracts.evidence import Passage, PastTicketResult, RetrievalResult, RiskAssessment
from team_b.contracts.policy import CheckActionRequest, PolicyDecision
from team_b.observability import get_logger

SERVICE = "team_a"
log = get_logger(__name__)
_T = TypeVar("_T", bound=PlugModel)


class TeamAClient:
    """The HTTP settings both plugs share. Every call opens a short-lived client, like the AI model client."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    async def request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None, params: dict[str, str] | None = None
    ) -> httpx.Response:
        """The response, or UpstreamError. A plain NOT_FOUND 404 is returned so a lookup can answer None."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.request(method, self._base + path, json=body, params=params)
        except httpx.TimeoutException:
            raise UpstreamError(SERVICE, "TIMEOUT", "Team A did not answer in time", retryable=True) from None
        except httpx.TransportError as exc:
            raise UpstreamError(
                SERVICE, "BACKEND_UNAVAILABLE", f"cannot reach Team A ({type(exc).__name__})", retryable=True
            ) from None
        if response.status_code < 400:
            return response
        code, message = _error_of(response)
        if response.status_code == 404 and code == "NOT_FOUND":
            return response
        if response.status_code in (401, 403):
            raise UpstreamError(SERVICE, "UNAUTHORIZED", "Team A refused the request")
        if response.status_code == 404:
            raise UpstreamError(SERVICE, code or "TENANT_NOT_FOUND", message)
        if response.status_code == 429 or response.status_code >= 500:
            raise UpstreamError(
                SERVICE,
                "BACKEND_UNAVAILABLE",
                f"Team A answered {code or response.status_code}: {message}",
                retryable=True,
            )
        raise UpstreamError(SERVICE, "BAD_REQUEST", f"Team A answered {code or response.status_code}: {message}")

    async def call(
        self,
        model: type[_T],
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> _T:
        response = await self.request(method, path, body=body, params=params)
        return _parse(model, response)


def _error_of(response: httpx.Response) -> tuple[str, str]:
    """Team A's {"error": {"code", "message"}} body, or empty strings when the answer is something else."""
    try:
        error = response.json()["error"]
        return str(error.get("code", "")), str(error.get("message", ""))[:200]
    except (ValueError, KeyError, TypeError, AttributeError):
        return "", response.text[:200]


def _parse(model: type[_T], response: httpx.Response) -> _T:
    try:
        return model.model_validate(response.json())
    except (ValueError, ValidationError):
        raise UpstreamError(
            SERVICE, "BAD_RESPONSE", f"Team A answered in an unknown format ({model.__name__})"
        ) from None


class HttpEvidenceProvider:
    def __init__(self, client: TeamAClient) -> None:
        self._client = client

    async def search_knowledge(
        self,
        tenant_id: str,
        query: str,
        *,
        request_id: str,
        conversation_id: str | None = None,
        top_k: int = 5,
    ) -> RetrievalResult:
        return await self._client.call(
            RetrievalResult,
            "POST",
            "/v1/knowledge/search",
            body={
                "request_id": request_id,
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "query": query,
                "top_k": top_k,
            },
        )

    async def get_passage(self, tenant_id: str, citation: str) -> Passage | None:
        response = await self._client.request(
            "GET", "/v1/knowledge/passages", params={"tenant_id": tenant_id, "citation": citation}
        )
        if response.status_code == 404:
            return None
        return _parse(Passage, response)

    async def search_past_tickets(
        self, tenant_id: str, query: str, *, request_id: str, top_k: int = 3
    ) -> PastTicketResult:
        return await self._client.call(
            PastTicketResult,
            "POST",
            "/v1/knowledge/past-tickets/search",
            body={"request_id": request_id, "tenant_id": tenant_id, "query": query, "top_k": top_k},
        )

    async def classify_risk(
        self, tenant_id: str, message: str, *, request_id: str, conversation_id: str | None = None
    ) -> RiskAssessment:
        return await self._client.call(
            RiskAssessment,
            "POST",
            "/v1/policy/classify-risk",
            body={
                "request_id": request_id,
                "tenant_id": tenant_id,
                "conversation_id": conversation_id,
                "message": message,
            },
        )


class HttpPolicyGate:
    def __init__(self, client: TeamAClient) -> None:
        self._client = client

    async def check_action(self, request: CheckActionRequest) -> PolicyDecision:
        decision = await self._client.call(
            PolicyDecision, "POST", "/v1/policy/check-action", body=_check_action_body(request)
        )
        approval = request.human_approval
        if decision.decision == "require_human" and approval is not None:
            return decision.model_copy(
                update={
                    "decision": "allow",
                    "reason_code": "APPROVED_BY_HUMAN",
                    "rationale": f"{decision.reason_code} was approved by {approval.approved_by} "
                    f"(case {approval.case_id}). {decision.rationale}",
                    "user_message": None,
                }
            )
        return decision


def _check_action_body(request: CheckActionRequest) -> dict[str, Any]:
    """Team A's CheckActionRequest: it has no action, side_effects or human_approval; the tool name is the action."""
    return {
        "request_id": request.request_id,
        "tenant_id": request.tenant_id,
        "conversation_id": request.conversation_id,
        "tool": {
            "name": request.action,
            "operation_kind": request.tool.operation_kind,
            "risk": request.tool.risk,
            "exposes_personal_data": request.tool.personal_data,
        },
        "arguments": request.arguments,
        "facts": request.facts,
        "identity": request.identity.model_dump(mode="json"),
        "resource_tenant_id": request.resource_tenant_id,
        "risk_categories": list(request.risk_categories),
        "as_of": request.as_of.isoformat() if request.as_of else None,
    }
