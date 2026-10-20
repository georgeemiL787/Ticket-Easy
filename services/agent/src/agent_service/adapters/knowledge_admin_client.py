"""The knowledge service's onboarding API: the documents of a business, the index, and the rules the model proposes.

Used only by the dashboard's knowledge and guardrail pages (X-Admin-Key). The person who approves or rejects a rule is
the signed-in reviewer, passed in by the API layer: the knowledge service records that name in the rule and its history.
A refusal by
the knowledge service (an unreadable file, a rule that would break a guardrail case) is returned as an UpstreamError
carrying the knowledge service's
own message, so the page can show it; an unreachable service is a retryable UpstreamError.
"""

import base64
from typing import Any

import httpx

from agent_service.contracts.errors import UpstreamError

SERVICE = "team_a"
REFUSALS = {"INVALID_REQUEST", "INVALID_STATE", "NOT_FOUND", "TENANT_NOT_FOUND", "FORBIDDEN"}


class KnowledgeAdminClient:
    def __init__(
        self,
        base_url: str,
        admin_key: str,
        *,
        service_key: str | None = None,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport
        self._headers = {"X-Admin-Key": admin_key, **({"X-Service-Key": service_key} if service_key else {})}

    # ---- documents and the index ----

    async def upload(
        self, tenant_id: str, filename: str, content: bytes, document_id: str | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"filename": filename, "content_base64": base64.b64encode(content).decode("ascii")}
        if document_id:
            body["document_id"] = document_id
        return await self._call("POST", f"/v1/admin/tenants/{tenant_id}/documents", body=body)

    async def documents(self, tenant_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/v1/admin/tenants/{tenant_id}/documents")

    async def remove(self, tenant_id: str, document_id: str) -> dict[str, Any]:
        return await self._call("DELETE", f"/v1/admin/tenants/{tenant_id}/documents/{document_id}")

    async def reindex(self, tenant_id: str) -> dict[str, Any]:
        return await self._call("POST", f"/v1/admin/tenants/{tenant_id}/reindex")

    async def job(self, job_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/v1/admin/jobs/{job_id}")

    # ---- rules ----

    async def extract_rules(self, tenant_id: str, document_id: str | None = None) -> dict[str, Any]:
        return await self._call(
            "POST", f"/v1/admin/tenants/{tenant_id}/rules/extract", body={"document_id": document_id}
        )

    async def rules(self, tenant_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/v1/admin/tenants/{tenant_id}/rules")

    async def approve_rule(self, tenant_id: str, rule_id: str, reviewer: str) -> dict[str, Any]:
        body = {"tenant_id": tenant_id, "reviewer": reviewer}
        return await self._call("POST", f"/v1/policy/rules/{rule_id}/approve", body=body)

    async def reject_rule(self, tenant_id: str, rule_id: str, reviewer: str) -> dict[str, Any]:
        body = {"tenant_id": tenant_id, "reviewer": reviewer}
        return await self._call("POST", f"/v1/policy/rules/{rule_id}/reject", body=body)

    async def edit_rule(self, tenant_id: str, rule_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        return await self._call(
            "PATCH", f"/v1/policy/rules/{rule_id}", body={"tenant_id": tenant_id, "changes": changes}
        )

    async def ruleset(self, tenant_id: str) -> dict[str, Any]:
        return await self._call("GET", "/v1/policy/rules/ruleset", params={"tenant_id": tenant_id})

    async def snapshot(self, tenant_id: str) -> dict[str, Any]:
        """The bundle's knowledge and guardrails sections as they stand in the knowledge service right now."""
        docs = await self.documents(tenant_id)
        ruleset = await self.ruleset(tenant_id)
        current = [d for d in docs.get("documents", []) if d.get("current")]
        return {
            "knowledge": {
                "provider": "team_a",
                "index_tenant": tenant_id,
                "documents": [
                    {
                        "document_id": d["document_id"],
                        "version": int(str(d.get("version", "v1")).lstrip("v") or 1),
                        "status": d.get("status", "indexed"),
                    }
                    for d in current
                ],
            },
            "guardrails": {
                "ruleset_version": ruleset.get("ruleset_version"),
                "ruleset_sha256": ruleset.get("ruleset_sha256"),
                "approved_rule_ids": ruleset.get("approved_rule_ids", []),
            },
        }

    async def _call(
        self, method: str, path: str, *, body: dict[str, Any] | None = None, params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.request(
                    method, self._base + path, json=body, params=params, headers=self._headers
                )
        except httpx.TimeoutException:
            raise UpstreamError(SERVICE, "TIMEOUT", "Team A did not answer in time", retryable=True) from None
        except httpx.TransportError as exc:
            raise UpstreamError(
                SERVICE, "BACKEND_UNAVAILABLE", f"cannot reach Team A ({type(exc).__name__})", retryable=True
            ) from None
        if response.status_code < 400:
            try:
                found = response.json()
            except ValueError:
                raise UpstreamError(SERVICE, "BAD_RESPONSE", "Team A answered in an unknown format") from None
            return found if isinstance(found, dict) else {"items": found}
        code, message = "", ""
        try:
            error = response.json()["error"]
            code, message = str(error.get("code", "")), str(error.get("message", ""))[:400]
        except (ValueError, KeyError, TypeError, AttributeError):
            message = response.text[:200]
        if response.status_code in (401, 403) and code != "FORBIDDEN":
            raise UpstreamError(SERVICE, "UNAUTHORIZED", "Team A refused the admin key")
        if code in REFUSALS or response.status_code in (404, 409, 413, 422):
            raise UpstreamError(
                SERVICE, code or "INVALID_REQUEST", message or f"Team A answered {response.status_code}"
            )
        raise UpstreamError(
            SERVICE, "BACKEND_UNAVAILABLE", f"Team A answered {code or response.status_code}: {message}", retryable=True
        )
