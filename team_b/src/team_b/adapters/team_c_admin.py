"""Team C's REST API (/api/v1), read for the Business tools page and used for one owner action: disabling a publication.

Team C's own pages stay the place where an owner requests tools, answers questions, approves, builds, tests and
publishes (TeamCConfig.builder_url links there); nothing of that workflow is repeated in Team B. This client only reads
publications and proposals and forwards a disable, which blocks the tool's next call in Team C (it cannot undo a write
that already happened). Team C accepts loopback hosts only and no cross-origin mutations; a server-side call sends no
Origin header. Failures raise UpstreamError("team_c", ...).
"""

from typing import Any

import httpx

from team_b.contracts.errors import UpstreamError

SERVICE = "team_c"


class TeamCAdmin:
    def __init__(self, base_url: str, *, timeout_s: float = 10.0, transport: httpx.AsyncBaseTransport | None = None):
        self._base = base_url.rstrip("/") + "/api/v1"
        self._timeout = timeout_s
        self._transport = transport

    async def publications(self, business_id: str) -> list[dict[str, Any]]:
        found = await self._call("GET", "/publications", params={"business_id": business_id})
        return [p for p in found if isinstance(p, dict)] if isinstance(found, list) else []

    async def publication(self, publication_id: str) -> dict[str, Any] | None:
        found = await self._call("GET", f"/publications/{publication_id}", missing_ok=True)
        return found if isinstance(found, dict) else None

    async def disable(self, publication_id: str, note: str) -> dict[str, Any]:
        found = await self._call("POST", f"/publications/{publication_id}/disable", body={"note": note})
        return found if isinstance(found, dict) else {}

    async def proposals(self, business_id: str) -> list[dict[str, Any]]:
        found = await self._call("GET", f"/businesses/{business_id}/proposals")
        return [p for p in found if isinstance(p, dict)] if isinstance(found, list) else []

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        missing_ok: bool = False,
    ) -> Any:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport, trust_env=False) as client:
                response = await client.request(method, self._base + path, params=params, json=body)
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
            raise UpstreamError(SERVICE, code.upper(), f"Team C answered {response.status_code} ({code})")
        try:
            return response.json()
        except ValueError:
            raise UpstreamError(SERVICE, "BAD_RESPONSE", "Team C answered in an unknown format") from None
