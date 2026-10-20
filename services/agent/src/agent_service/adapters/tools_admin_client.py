"""The tools service's REST API (/api/v1), read for the Business tools page and used for two owner actions: disabling a
publication
and checking a connector's connection.

The tools service's own pages stay the place where an owner requests tools, answers questions, approves, builds, tests
and
publishes (ToolsConfig.builder_url links there); nothing of that workflow is repeated in the agent. This client only
reads
publications and proposals and forwards a disable, which blocks the tool's next call in the tools service (it cannot
undo a write
that already happened). The tools service accepts loopback hosts only and no cross-origin mutations; a server-side call
sends no
Origin header. Failures raise UpstreamError("team_c", ...).
"""

from typing import Any

import httpx

from agent_service.contracts.errors import UpstreamError

SERVICE = "team_c"


class ToolsAdminClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        admin_token: str | None = None,
    ):
        self._base = base_url.rstrip("/") + "/api/v1"
        self._timeout = timeout_s
        self._transport = transport
        # The agent's service credential TO the tools service (never a production secret), sent on every call when set.
        self._headers = {"x-team-c-admin-token": admin_token} if admin_token else {}

    async def publications(self, business_id: str) -> list[dict[str, Any]]:
        found = await self._call("GET", "/publications", params={"business_id": business_id})
        return [p for p in found if isinstance(p, dict)] if isinstance(found, list) else []

    async def publication(self, publication_id: str) -> dict[str, Any] | None:
        found = await self._call("GET", f"/publications/{publication_id}", missing_ok=True)
        return found if isinstance(found, dict) else None

    async def disable(self, publication_id: str, note: str) -> dict[str, Any]:
        found = await self._call("POST", f"/publications/{publication_id}/disable", body={"note": note})
        return found if isinstance(found, dict) else {}

    async def connectors(self, business_id: str) -> list[dict[str, Any]]:
        """The tools service's connectors of a business (environment, host, identity names, last check); never
        credentials."""
        found = await self._call("GET", f"/businesses/{business_id}/connectors")
        return [c for c in found if isinstance(c, dict)] if isinstance(found, list) else []

    async def check_connector(self, connector_id: str) -> dict[str, Any]:
        """The tools service's real connection check of one connector (it may send one GET and logins to that API)."""
        found = await self._call("POST", f"/connectors/{connector_id}/check")
        return found if isinstance(found, dict) else {}

    async def proposals(self, business_id: str) -> list[dict[str, Any]]:
        found = await self._call("GET", f"/businesses/{business_id}/proposals")
        return [p for p in found if isinstance(p, dict)] if isinstance(found, list) else []

    async def promotions(self, business_id: str) -> list[dict[str, Any]]:
        found = await self._call("GET", f"/businesses/{business_id}/promotions", missing_ok=True)
        return [p for p in found if isinstance(p, dict)] if isinstance(found, list) else []

    async def promote(
        self, artifact_id: str, *, actor: str, note: str, rollback_contact: str, accept_no_idempotency: bool
    ) -> dict[str, Any]:
        """An admin promotes a tested tool to production; the tools service refuses (409) unless its whole checklist
        passes."""
        body = {
            "actor": actor,
            "note": note,
            "rollback_contact": rollback_contact,
            "accept_no_idempotency": accept_no_idempotency,
        }
        found = await self._call("POST", f"/artifacts/{artifact_id}/promotions", body=body)
        return found if isinstance(found, dict) else {}

    async def rollback(self, promotion_id: str, *, actor: str, note: str) -> dict[str, Any]:
        found = await self._call("POST", f"/promotions/{promotion_id}/rollback", body={"actor": actor, "note": note})
        return found if isinstance(found, dict) else {}

    async def artifacts(self, business_id: str) -> list[dict[str, Any]]:
        """The newest artifact of each proposal with its readiness_state and latest automatic test. An older the tools
        service
        without this endpoint answers 404: no artifacts are shown then."""
        found = await self._call("GET", f"/businesses/{business_id}/artifacts", missing_ok=True)
        return [a for a in found if isinstance(a, dict)] if isinstance(found, list) else []

    async def auto_test(self, artifact_id: str) -> dict[str, Any]:
        """Start the tools service's automatic tests for one artifact (in the background). The agent never asks to
        publish:
        whether a passing tool is published to the sandbox is the owner's setting for the business in the tools
        service."""
        found = await self._call("POST", f"/artifacts/{artifact_id}/auto-test", body={})
        return found if isinstance(found, dict) else {}

    async def auto_test_run(self, run_id: str) -> dict[str, Any] | None:
        found = await self._call("GET", f"/auto-test-runs/{run_id}", missing_ok=True)
        return found if isinstance(found, dict) else None

    # ---- the guided tool setup: import a description, choose operations, batches (tools service api/batches.py) ----

    async def businesses(self) -> list[dict[str, Any]]:
        found = await self._call("GET", "/businesses")
        return [b for b in found if isinstance(b, dict)] if isinstance(found, list) else []

    async def specifications(self, business_id: str) -> list[dict[str, Any]]:
        found = await self._call("GET", f"/businesses/{business_id}/specifications")
        return [x for x in found if isinstance(x, dict)] if isinstance(found, list) else []

    async def batches(self, business_id: str) -> list[dict[str, Any]]:
        found = await self._call("GET", f"/businesses/{business_id}/tool-batches")
        return [x for x in found if isinstance(x, dict)] if isinstance(found, list) else []

    async def create_business(self, name: str, description: str) -> dict[str, Any]:
        found = await self._call("POST", "/businesses", body={"name": name, "description": description})
        return found if isinstance(found, dict) else {}

    async def upload_spec(self, business_id: str, filename: str, content: bytes) -> dict[str, Any]:
        """Hand over an OpenAPI/Swagger file. The tools service reads it offline; nothing it describes is called."""
        found = await self._call(
            "POST", f"/businesses/{business_id}/specifications", files={"file": (filename, content)}, long=True
        )
        return found if isinstance(found, dict) else {}

    async def fetch_spec(self, business_id: str, url: str) -> dict[str, Any]:
        found = await self._call(
            "POST", f"/businesses/{business_id}/specifications/fetch", body={"url": url}, long=True
        )
        return found if isinstance(found, dict) else {}

    async def operations(self, spec_id: str, *, offset: int, limit: int, klass: str | None, q: str) -> dict[str, Any]:
        params = {"offset": str(offset), "limit": str(limit), "q": q}
        if klass:
            params["class"] = klass
        found = await self._call("GET", f"/specifications/{spec_id}/operations", params=params)
        return found if isinstance(found, dict) else {}

    async def connection_check(self, business_id: str, base_url: str) -> dict[str, Any]:
        found = await self._call("POST", f"/businesses/{business_id}/connection-check", body={"base_url": base_url})
        return found if isinstance(found, dict) else {}

    async def create_batch(self, business_id: str, body: dict[str, Any]) -> dict[str, Any]:
        found = await self._call("POST", f"/businesses/{business_id}/tool-batches", body=body, long=True)
        return found if isinstance(found, dict) else {}

    async def batch(self, batch_id: str) -> dict[str, Any] | None:
        found = await self._call("GET", f"/tool-batches/{batch_id}", missing_ok=True)
        return found if isinstance(found, dict) else None

    async def approve_batch(self, batch_id: str, body: dict[str, Any]) -> dict[str, Any]:
        found = await self._call("POST", f"/tool-batches/{batch_id}/approve", body=body)
        return found if isinstance(found, dict) else {}

    async def retry_batch_item(self, batch_id: str, operation_id: str) -> dict[str, Any]:
        found = await self._call("POST", f"/tool-batches/{batch_id}/retry/{operation_id}", body={})
        return found if isinstance(found, dict) else {}

    async def publish_batch(self, batch_id: str, actor: str) -> dict[str, Any]:
        found = await self._call("POST", f"/tool-batches/{batch_id}/publish", body={"actor": actor})
        return found if isinstance(found, dict) else {}

    async def artifact(self, artifact_id: str) -> dict[str, Any] | None:
        found = await self._call("GET", f"/artifacts/{artifact_id}", missing_ok=True)
        return found if isinstance(found, dict) else None

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        files: dict[str, tuple[str, bytes]] | None = None,
        missing_ok: bool = False,
        long: bool = False,  # an upload, a fetch or a model call may take longer than a read
    ) -> Any:
        try:
            timeout = max(self._timeout, 120.0) if long else self._timeout
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport, trust_env=False) as client:
                response = await client.request(
                    method, self._base + path, params=params, json=body, files=files, headers=self._headers
                )
        except httpx.TimeoutException:
            raise UpstreamError(SERVICE, "TIMEOUT", "Team C did not answer in time", retryable=True) from None
        except httpx.TransportError as exc:
            raise UpstreamError(
                SERVICE, "BACKEND_UNAVAILABLE", f"cannot reach Team C ({type(exc).__name__})", retryable=True
            ) from None
        if response.status_code == 404 and missing_ok:
            return None
        if response.status_code >= 400:
            try:
                code = str(response.json().get("code") or response.status_code)
            except (ValueError, AttributeError):
                code = str(response.status_code)
            detail = ""
            if code in ("promotion_refused", "production_disabled", "rate_limited", "promotion_not_active"):
                try:
                    detail = ": " + str(response.json().get("message") or "")[:600]
                except (ValueError, AttributeError):
                    detail = ""
            raise UpstreamError(SERVICE, code.upper(), f"Team C answered {response.status_code} ({code}){detail}")
        try:
            return response.json()
        except ValueError:
            raise UpstreamError(SERVICE, "BAD_RESPONSE", "Team C answered in an unknown format") from None
