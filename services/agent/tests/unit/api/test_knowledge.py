"""The knowledge and guardrail pages over the stand-in knowledge service, and the HTTP client for the real one."""

import base64
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from team_b.adapters.standins.knowledge_admin import StandinKnowledgeAdmin
from team_b.adapters.team_a_admin import TeamAAdmin
from team_b.adapters.team_a_http import TeamAClient
from team_b.api.app import create_app
from team_b.container import Container, build_container
from team_b.contracts.errors import UpstreamError
from tests.conftest import FIXED_TODAY
from tests.support import make_settings

T = "shop_001"
PASSWORD = "correct horse battery"


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


@pytest.fixture
def secured(tmp_path: Path) -> Container:
    return build_container(make_settings(tmp_path, fixed_today=FIXED_TODAY, auth_required=True, secret_key="k" * 32))


@pytest.fixture
async def web(secured: Container) -> AsyncIterator[httpx.AsyncClient]:
    app: FastAPI = create_app(container=secured)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            yield http


async def sign_in(
    secured: Container, web: httpx.AsyncClient, role: str, name: str, tenants: tuple[str, ...] = (T,)
) -> dict[str, str]:
    assert secured.auth is not None
    await secured.auth.create_user(f"{name.lower()}@example.com", name, role, tenants, PASSWORD)  # type: ignore[arg-type]
    reply = await web.post("/v1/auth/login", json={"email": f"{name.lower()}@example.com", "password": PASSWORD})
    assert reply.status_code == 200, reply.text
    return {"X-CSRF-Token": reply.json()["csrf_token"]}


def admin_of(container: Container) -> StandinKnowledgeAdmin:
    assert isinstance(container.knowledge_admin, StandinKnowledgeAdmin)
    return container.knowledge_admin


async def test_documents_go_from_uploaded_to_indexed_and_a_bad_file_is_reported(
    secured: Container, web: httpx.AsyncClient
) -> None:
    headers = await sign_in(secured, web, "manager", "Mona")
    good = await web.post(
        "/v1/dashboard/knowledge/documents",
        params={"tenant_id": T},
        headers=headers,
        json={"filename": "Returns.md", "content_base64": b64("# Returns\n\n## 1. Window\n14 days.")},
    )
    assert good.status_code == 201 and good.json()["status"] == "uploaded"
    bad = await web.post(
        "/v1/dashboard/knowledge/documents",
        params={"tenant_id": T},
        headers=headers,
        json={"filename": "scan.pdf", "content_base64": b64("not a pdf")},
    )
    assert bad.status_code == 201 and bad.json()["status"] == "failed" and bad.json()["error"]
    refused = await web.post(
        "/v1/dashboard/knowledge/documents",
        params={"tenant_id": T},
        headers=headers,
        json={"filename": "run.exe", "content_base64": b64("MZ")},
    )
    assert refused.status_code == 422 and "unsupported" in refused.json()["error"]["message"]
    assert (
        await web.post(
            "/v1/dashboard/knowledge/documents",
            params={"tenant_id": T},
            headers=headers,
            json={"filename": "a.md", "content_base64": "***"},
        )
    ).status_code == 422
    started = await web.post("/v1/dashboard/knowledge/reindex", params={"tenant_id": T}, headers=headers)
    assert started.status_code == 202
    job = await web.get(f"/v1/dashboard/knowledge/jobs/{started.json()['job_id']}", params={"tenant_id": T})
    assert job.json()["status"] == "succeeded" and job.json()["result"]["current_passages"] == 1
    listing = (await web.get("/v1/dashboard/knowledge/documents", params={"tenant_id": T})).json()
    assert {d["filename"]: d["status"] for d in listing["documents"]} == {"Returns.md": "indexed", "scan.pdf": "failed"}
    removed = await web.delete("/v1/dashboard/knowledge/documents/returns", params={"tenant_id": T}, headers=headers)
    assert removed.json()["reindex_needed"] is True
    assert (
        await web.delete("/v1/dashboard/knowledge/documents/ghost", params={"tenant_id": T}, headers=headers)
    ).status_code == 404
    assert (
        await web.post("/v1/dashboard/knowledge/reindex", params={"tenant_id": T}, headers=headers)
    ).status_code == 409


async def test_a_business_that_has_uploaded_nothing_yet_shows_an_empty_list_not_an_error(
    secured: Container, web: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await sign_in(secured, web, "manager", "Mona")

    async def unknown(tenant_id: str) -> dict[str, Any]:
        raise UpstreamError("team_a", "TENANT_NOT_FOUND", f"Unknown tenant '{tenant_id}'")

    monkeypatch.setattr(admin_of(secured), "documents", unknown)
    reply = await web.get("/v1/dashboard/knowledge/documents", params={"tenant_id": T})
    assert reply.status_code == 200 and reply.json()["documents"] == []


async def test_the_reviewer_is_the_signed_in_person_and_an_agent_cannot_review(
    secured: Container, web: httpx.AsyncClient
) -> None:
    admin_of(secured).propose(T, "R-REFUND-14D", "Refunds within 14 days.")
    agent = await sign_in(secured, web, "agent", "Ana")
    assert (await web.get("/v1/dashboard/guardrails", params={"tenant_id": T})).status_code == 403
    assert (
        await web.post("/v1/dashboard/guardrails/R-REFUND-14D/approve", params={"tenant_id": T}, headers=agent)
    ).status_code == 403
    await web.post("/v1/auth/logout", headers=agent)
    manager = await sign_in(secured, web, "manager", "Mona")
    listing = (await web.get("/v1/dashboard/guardrails", params={"tenant_id": T})).json()
    assert [r["approval_status"] for r in listing["rules"]] == ["proposed"] and listing["ruleset"]["count"] == 0
    approved = await web.post("/v1/dashboard/guardrails/R-REFUND-14D/approve", params={"tenant_id": T}, headers=manager)
    assert approved.status_code == 200 and approved.json()["approved_by"] == "Mona"
    after = (await web.get("/v1/dashboard/guardrails", params={"tenant_id": T})).json()
    assert after["ruleset"]["count"] == 1 and [h["by"] for h in after["rules"][0]["history"]] == [None, "Mona"]
    edited = await web.post(
        "/v1/dashboard/guardrails/R-REFUND-14D/edit",
        params={"tenant_id": T},
        headers=manager,
        json={"changes": {"else_effect": "require_human"}},
    )
    assert edited.json()["approval_status"] == "proposed"  # an edit needs a new approval
    assert (
        await web.post(
            "/v1/dashboard/guardrails/R-REFUND-14D/edit",
            params={"tenant_id": T},
            headers=manager,
            json={"changes": {"approval_status": "approved"}},
        )
    ).status_code == 422
    rejected = await web.post("/v1/dashboard/guardrails/R-REFUND-14D/reject", params={"tenant_id": T}, headers=manager)
    assert rejected.json()["approval_status"] == "rejected"
    assert (
        await web.post("/v1/dashboard/guardrails/NOPE/approve", params={"tenant_id": T}, headers=manager)
    ).status_code == 404
    extract = await web.post("/v1/dashboard/guardrails/extract", params={"tenant_id": T}, headers=manager, json={})
    assert extract.status_code == 202 and extract.json()["status"] == "succeeded"


async def test_a_manager_of_another_business_does_not_see_these_pages(
    secured: Container, web: httpx.AsyncClient
) -> None:
    await sign_in(secured, web, "manager", "Mona", ("noon_eg",))
    assert (await web.get("/v1/dashboard/knowledge/documents", params={"tenant_id": T})).status_code == 404
    assert (await web.get("/v1/dashboard/guardrails", params={"tenant_id": T})).status_code == 404


async def test_when_the_knowledge_service_is_down_the_pages_say_so(secured: Container, web: httpx.AsyncClient) -> None:
    await sign_in(secured, web, "manager", "Mona")
    admin_of(secured).fail_next = "BACKEND_UNAVAILABLE"
    reply = await web.get("/v1/dashboard/knowledge/documents", params={"tenant_id": T})
    assert reply.status_code == 503 and reply.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


async def test_a_bundle_cannot_be_published_on_stale_rules(secured: Container, web: httpx.AsyncClient) -> None:
    admin = admin_of(secured)
    admin.propose(T, "R-ONE", "One rule.")
    headers = await sign_in(secured, web, "admin", "Root")
    await admin.approve_rule(T, "R-ONE", "Root")
    base = f"/v1/admin/tenants/{T}/bundles"
    first = await web.post(base, headers=headers, json={"from_files": True, "refresh_knowledge": True})
    assert first.status_code == 201, first.text
    assert (
        first.json()["guardrails"]["approved_rule_ids"] == ["R-ONE"]
        and first.json()["knowledge"]["provider"] == "standin"
    )
    admin.propose(T, "R-TWO", "Another rule.")
    await admin.approve_rule(T, "R-TWO", "Root")  # the approved rules changed after the draft was made
    blocked = await web.post(f"{base}/1/publish", headers=headers)
    assert blocked.status_code == 409 and "approved rules changed" in blocked.json()["error"]["message"]
    fresh = await web.post(base, headers=headers, json={"refresh_knowledge": True})
    assert fresh.status_code == 201 and sorted(fresh.json()["guardrails"]["approved_rule_ids"]) == ["R-ONE", "R-TWO"]
    assert (await web.post(f"{base}/{fresh.json()['version']}/publish", headers=headers)).status_code == 200


# ---- the HTTP clients for the real service ----


def handler(seen: list[httpx.Request], reply: dict[str, Any] | None = None, status: int = 200):
    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=reply if reply is not None else {})

    return respond


async def test_the_admin_client_sends_its_keys_and_reads_a_snapshot() -> None:
    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/documents"):
            return httpx.Response(
                200,
                json={
                    "documents": [
                        {"document_id": "returns", "version": "v2", "current": True, "status": "indexed"},
                        {"document_id": "returns", "version": "v1", "current": False, "status": "indexed"},
                    ]
                },
            )
        return httpx.Response(
            200, json={"ruleset_version": "abc123", "ruleset_sha256": "f" * 64, "approved_rule_ids": ["R-1"]}
        )

    admin = TeamAAdmin("http://a.test", "admin-key", service_key="svc-key", transport=httpx.MockTransport(respond))
    snapshot = await admin.snapshot(T)
    assert snapshot["knowledge"]["documents"] == [{"document_id": "returns", "version": 2, "status": "indexed"}]
    assert (
        snapshot["guardrails"]["approved_rule_ids"] == ["R-1"] and snapshot["guardrails"]["ruleset_version"] == "abc123"
    )
    assert all(r.headers["x-admin-key"] == "admin-key" and r.headers["x-service-key"] == "svc-key" for r in seen)
    sent = TeamAAdmin("http://a.test", "k", transport=httpx.MockTransport(handler(seen)))
    await sent.upload(T, "a.md", b"hello", "doc_a")
    body = json.loads(seen[-1].content)
    assert body == {"filename": "a.md", "content_base64": b64("hello"), "document_id": "doc_a"}
    await sent.approve_rule(T, "R-1", "Mona")
    assert (
        json.loads(seen[-1].content) == {"tenant_id": T, "reviewer": "Mona"}
        and seen[-1].url.path == "/v1/policy/rules/R-1/approve"
    )


@pytest.mark.parametrize(
    ("status", "body", "code", "retryable"),
    [
        (
            422,
            {"error": {"code": "INVALID_REQUEST", "message": "unsupported file type .exe"}},
            "INVALID_REQUEST",
            False,
        ),
        (409, {"error": {"code": "INVALID_STATE", "message": "already running"}}, "INVALID_STATE", False),
        (404, {"error": {"code": "NOT_FOUND", "message": "No rule"}}, "NOT_FOUND", False),
        (401, {"error": {"code": "UNAUTHORIZED", "message": "bad key"}}, "UNAUTHORIZED", False),
        (500, {"error": {"code": "INTERNAL_ERROR", "message": "boom"}}, "BACKEND_UNAVAILABLE", True),
    ],
)
async def test_the_admin_client_keeps_the_reason_team_a_gave(
    status: int, body: dict[str, Any], code: str, retryable: bool
) -> None:
    admin = TeamAAdmin("http://a.test", "k", transport=httpx.MockTransport(handler([], body, status)))
    with pytest.raises(UpstreamError) as error:
        await admin.documents(T)
    assert error.value.code == code and error.value.retryable is retryable
    if code in ("INVALID_REQUEST", "INVALID_STATE", "NOT_FOUND"):
        assert body["error"]["message"] in error.value.message


async def test_an_unreachable_team_a_is_a_retryable_failure() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(UpstreamError) as error:
        await TeamAAdmin("http://a.test", "k", transport=httpx.MockTransport(refuse)).documents(T)
    assert error.value.code == "BACKEND_UNAVAILABLE" and error.value.retryable


async def test_the_search_client_sends_the_service_key_when_there_is_one() -> None:
    seen: list[httpx.Request] = []
    keyed = TeamAClient("http://a.test", transport=httpx.MockTransport(handler(seen)), service_key="svc-key")
    await keyed.request("GET", "/v1/knowledge/passages", params={"tenant_id": T, "citation": "x@v1#s1"})
    assert seen[0].headers["x-service-key"] == "svc-key"
    plain = TeamAClient("http://a.test", transport=httpx.MockTransport(handler(seen)))
    await plain.request("GET", "/health")
    assert "x-service-key" not in seen[1].headers
    refused = TeamAClient(
        "http://a.test",
        transport=httpx.MockTransport(handler([], {"error": {"code": "FORBIDDEN", "message": "no"}}, 403)),
    )
    with pytest.raises(UpstreamError) as error:
        await refused.request("GET", "/health")
    assert error.value.code == "UNAUTHORIZED"  # fails closed: the brain hands over
