"""The guided tool setup API against a scripted tools service (httpx.MockTransport); no real the tools service is
started."""

import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_service.adapters.tools_admin_client import ToolsAdminClient
from agent_service.config import PROJECT_ROOT
from agent_service.container import Container
from tests.unit.adapters.test_tools_catalog import Fake
from tests.unit.api.test_business_tools import ToolsRestClient, build, serve

T = "noon_eg"  # a business with no tools-service binding yet
BOUND = "shop_001"  # already bound to the tools-service business biz-nile by the operator's file
URL = "/v1/dashboard/tool-setup"
PASSWORD = "correct horse battery"
ITEM = {
    "operation_id": "op-1", "method": "POST", "path": "/api/tickets", "title": "Open ticket", "class": "write",
    "state": "ready_to_publish", "proposal_id": "prop-new", "artifact_id": "art-new",
    "summary": {"name": "Open a support ticket", "purpose": "Opens a ticket for the customer"},
}  # fmt: skip
STEPS = [{"id": "s1", "method": "GET"}, {"id": "s2", "method": "POST"}]
ARTIFACT = {
    "id": "art-new",
    "proposal_id": "prop-new",
    "content": {
        "name": "Open a support ticket",
        "purpose": "Opens a ticket",
        "steps": STEPS,
        "input_schema": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}, "subject": {"type": "string", "title": "Subject"}},
            "required": ["order_id", "subject"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"ticket_id": {"type": "string"}, "status": {"type": "string", "nullable": True}},
            "required": ["ticket_id"],
            "additionalProperties": False,
        },
    },
}


class ToolsService(ToolsRestClient):
    """The tools service's new endpoints, kept as data."""

    def __init__(self) -> None:
        super().__init__()
        self.businesses: dict[str, dict[str, Any]] = {}
        self.batches: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.down = False
        self.fetch_code = ""

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        self.calls.append((request.method, path))
        if self.down:
            raise httpx.ConnectError("down", request=request)
        method = request.method
        body = (
            json.loads(request.content)
            if request.content and request.headers.get("content-type", "").startswith("application/json")
            else None
        )
        if path == "/businesses" and method == "GET":
            return httpx.Response(200, json=list(self.businesses.values()))
        if path == "/businesses" and method == "POST":
            made = {"id": f"biz-{len(self.businesses) + 1}", "name": body["name"], "description": body["description"]}
            self.businesses[made["id"]] = made
            return httpx.Response(200, json=made)
        if path.endswith("/specifications") and method == "POST":
            assert b"openapi" in request.content or b"swagger" in request.content
            return httpx.Response(
                200,
                json={
                    "id": "spec-1234",
                    "inventory": {
                        "proposal_generation_ready": True,
                        "eligible_operation_count": 2,
                        "converted_from": "swagger_2.0",
                        "operations": [{}, {}, {}],
                        "diagnostics": [],
                    },
                },
            )
        if path.endswith("/specifications/fetch") and self.fetch_code:
            return httpx.Response(502, json={"code": self.fetch_code, "message": "internal wording"})
        if path.endswith("/specifications/fetch"):
            return httpx.Response(
                200,
                json={
                    "id": "spec-5678",
                    "inventory": {
                        "proposal_generation_ready": False,
                        "operations": [],
                        "diagnostics": [{"severity": "error", "scope": "document", "message": "No operations found"}],
                    },
                },
            )
        if path.startswith("/businesses/") and path.endswith("/specifications") and method == "GET":
            return httpx.Response(200, json=[{"id": "spec-1234", "operations": 3}])
        if path.startswith("/specifications/") and path.endswith("/operations"):
            return httpx.Response(
                200,
                json={"total": 3, "operations": [{"id": "op-1", "class": "write"}], "params": dict(request.url.params)},
            )
        if path.endswith("/connection-check"):
            return httpx.Response(200, json={"reachable": True, "status": 200})
        if path.endswith("/tool-batches") and method == "POST":
            batch = {
                "id": "batch-0001",
                "business_id": path.split("/")[2],
                "spec_id": body["spec_id"],
                "items": [dict(ITEM, state="queued")],
                "busy": True,
                "settings": {"accounts": [{"name": "alice", "scope": "end_user", "context": {"customer_id": "C-100"}}]},
                "request": body,
            }
            self.batches[batch["id"]] = batch
            return httpx.Response(200, json=batch)
        if path.endswith("/tool-batches") and method == "GET":
            return httpx.Response(
                200, json=[b for b in self.batches.values() if b["business_id"] == path.split("/")[2]]
            )
        if path.startswith("/tool-batches/"):
            parts = path.split("/")
            batch = self.batches.get(parts[2])
            if batch is None:
                return httpx.Response(404, json={"code": "not_found"})
            if len(parts) == 3:
                return httpx.Response(200, json=batch)
            if parts[3] == "approve":
                batch["approved"] = body
                batch["items"][0]["state"] = "testing"
                return httpx.Response(200, json=batch)
            if parts[3] == "publish":
                if batch["items"][0]["state"] != "ready_to_publish":
                    return httpx.Response(409, json={"code": "batch_nothing_to_publish", "message": "none passed"})
                batch["items"][0]["state"] = "published"
                return httpx.Response(200, json=batch)
        if path == "/artifacts/art-new":
            return httpx.Response(200, json=ARTIFACT)
        return super().handle(request)


@pytest.fixture
def service() -> ToolsService:
    return ToolsService()


@pytest.fixture
async def setup(
    tmp_path: Path, service: ToolsService
) -> AsyncIterator[tuple[httpx.AsyncClient, ToolsService, Container]]:
    fake = Fake(tmp_path)
    container = build(tmp_path, fake, service)
    assert container.tools is not None
    container.tools.admin = ToolsAdminClient("http://127.0.0.1:9", transport=httpx.MockTransport(service.handle))
    async for http in serve(container):
        yield http, service, container


async def test_the_first_import_creates_the_tools_business_and_later_ones_reuse_it(setup: Any) -> None:
    http, service, _ = setup
    first = await http.post(
        f"{URL}/import",
        params={"tenant_id": T},
        json={"filename": "shop.json", "content": '{"swagger": "2.0"}'},
    )
    assert first.status_code == 200, first.text
    assert (
        first.json() | {} == first.json() and first.json()["spec_id"] == "spec-1234" and first.json()["valid"] is True
    )
    assert (
        first.json()["operations"] == 3
        and first.json()["usable"] == 2
        and first.json()["converted_from"] == "swagger_2.0"
    )
    assert [b["name"] for b in service.businesses.values()] == ["ticket-easy:noon_eg"]
    second = await http.post(
        f"{URL}/import",
        params={"tenant_id": T},
        json={"filename": "shop.json", "content": '{"openapi": "3.0.3"}'},
    )
    assert second.status_code == 200 and len(service.businesses) == 1


async def test_a_url_import_reports_what_is_wrong_with_the_description(setup: Any) -> None:
    http, _, _ = setup
    reply = await http.post(f"{URL}/import", params={"tenant_id": T}, json={"url": "http://shop.test/openapi.json"})
    assert reply.status_code == 200
    assert reply.json()["valid"] is False and reply.json()["problems"] == ["No operations found"]


@pytest.mark.parametrize("code", ["fetch_failed", "fetch_host", "file_type", "fetch_url"])
async def test_an_address_that_cannot_be_read_says_what_to_do_in_plain_words(setup: Any, code: str) -> None:
    http, service, _ = setup
    service.fetch_code = code
    reply = await http.post(f"{URL}/import", params={"tenant_id": T}, json={"url": "http://shop.test/openapi.json"})
    message = reply.json()["error"]["message"]
    assert reply.status_code == 409 and message != f"this could not be done ({code})"
    assert "The tools service" not in message and code not in message  # no internal names or codes


async def test_an_import_needs_exactly_one_of_file_or_url(setup: Any) -> None:
    http, _, _ = setup
    assert (await http.post(f"{URL}/import", params={"tenant_id": T}, json={})).status_code == 422
    both = await http.post(
        f"{URL}/import",
        params={"tenant_id": T},
        data={"url": "http://x.test/a.json"},
        files={"file": ("a.json", b"{}", "application/json")},
    )
    assert both.status_code == 422


async def test_operations_and_the_connection_check_pass_through(setup: Any) -> None:
    http, _, _ = setup
    ops = await http.get(
        f"{URL}/operations",
        params={"tenant_id": T, "spec_id": "spec-1234", "class": "write", "q": "ticket", "limit": 5},
    )
    assert ops.status_code == 200 and ops.json()["params"] == {
        "offset": "0",
        "limit": "5",
        "q": "ticket",
        "class": "write",
    }
    check = await http.post(f"{URL}/connection-check", params={"tenant_id": T}, json={"base_url": "http://shop.test"})
    assert check.json() == {"reachable": True, "status": 200}


async def test_a_batch_is_prepared_approved_published_into_a_draft_and_only_then_activated(setup: Any) -> None:
    http, service, container = setup
    await http.post(
        f"{URL}/import",
        params={"tenant_id": T},
        json={"filename": "shop.json", "content": '{"swagger": "2.0"}'},
    )
    body = {
        "spec_id": "spec-1234",
        "operation_ids": ["op-1"],
        "verify_operation_id": "op-0",
        "connection": {
            "base_url": "http://sandbox.test:8102",
            "accounts": [{"name": "alice", "token": "tok-SECRET-1", "context": {"customer_id": "C-100"}}],
        },
    }
    created = await http.post(f"{URL}/batches", params={"tenant_id": T}, json=body)
    assert created.status_code == 200 and created.json()["id"] == "batch-0001"
    assert service.batches["batch-0001"]["request"]["actor"] == "dashboard"
    approve = await http.post(
        f"{URL}/batches/batch-0001/approve",
        params={"tenant_id": T},
        json={
            "proposal_ids": ["prop-new"],
            "confirmations": {
                "prop-new": {
                    "financial": False,
                    "irreversible": False,
                    "external_side_effects": True,
                    "idempotent": False,
                }
            },
        },
    )
    assert (
        approve.status_code == 200
        and service.batches["batch-0001"]["approved"]["confirmations"]["prop-new"]["external_side_effects"] is True
    )
    assert (
        await http.post(f"{URL}/batches/batch-0001/publish", params={"tenant_id": T}, json={})
    ).status_code == 409  # still testing
    service.batches["batch-0001"]["items"][0]["state"] = "ready_to_publish"
    published = await http.post(f"{URL}/batches/batch-0001/publish", params={"tenant_id": T}, json={})
    assert published.status_code == 200, published.text
    assert published.json()["tools"] == ["open_a_support_ticket"] and published.json()["bundle_version"] == 1
    registry = container.bundles
    assert registry is not None and registry.active_bundle(T) is None  # a draft: nothing is in use yet
    draft = await registry.get_bundle(T, 1)
    tool = next(t for t in draft.tools if t.name == "open_a_support_ticket")
    assert tool.source == "tools" and tool.permission == "confirm" and tool.binding is not None
    assert (
        tool.binding.proposal_id == "prop-new"
        and tool.binding.business_id == "biz-1"
        and tool.binding.reference_field == "ticket_id"
    )
    spec = tool.binding.spec or {}
    assert spec["operation_kind"] == "create" and spec["output_schema"]["properties"]["status"]["type"] == [
        "string",
        "null",
    ]
    assert "title" not in spec["input_schema"]["properties"]["subject"]
    assert "open_a_support_ticket" in draft.config.permissions.allowed_tools and draft.identities == {"C-100": "alice"}
    assert "tok-SECRET-1" not in draft.model_dump_json()

    activated = await http.post(
        f"{URL}/batches/batch-0001/activate", params={"tenant_id": T}, json={"bundle_version": 1}
    )
    assert activated.status_code == 200 and activated.json()["status"] == "active"
    assert registry.active_bundle(T) is not None
    assert container.tools is not None
    binding = container.tools.config.tenants[T]
    assert "open_a_support_ticket" in binding.tools and binding.tools["open_a_support_ticket"].proposal_id == "prop-new"
    assert binding.identities["C-100"] == "alice"  # the account chosen on the connect screen is mapped to its customer
    assert binding.business_id == "biz-1"  # the new business of this tenant


async def test_a_business_that_is_already_bound_keeps_its_tools_service_business(setup: Any) -> None:
    http, service, _ = setup
    reply = await http.post(
        f"{URL}/import", params={"tenant_id": BOUND}, json={"filename": "s.json", "content": '{"openapi": "3.0.3"}'}
    )
    assert reply.status_code == 200 and reply.json()["business_id"] == "biz-nile" and service.businesses == {}


async def test_a_batch_of_another_business_is_not_reachable(setup: Any) -> None:
    http, service, _ = setup
    service.businesses["biz-x"] = {"id": "biz-x", "name": "ticket-easy:other", "description": ""}
    service.batches["batch-9999"] = {"id": "batch-9999", "business_id": "biz-x", "items": []}
    await http.post(
        f"{URL}/import",
        params={"tenant_id": T},
        json={"filename": "shop.json", "content": '{"swagger": "2.0"}'},
    )
    for call in (
        http.get(f"{URL}/batches/batch-9999", params={"tenant_id": T}),
        http.post(f"{URL}/batches/batch-9999/approve", params={"tenant_id": T}, json={"proposal_ids": ["p"]}),
        http.post(f"{URL}/batches/batch-9999/publish", params={"tenant_id": T}, json={}),
    ):
        assert (await call).status_code == 404


async def test_a_business_that_never_imported_has_no_batches(setup: Any) -> None:
    http, _, _ = setup
    assert (await http.get(f"{URL}/batches/batch-0001", params={"tenant_id": T})).status_code == 404
    assert (await http.get(URL, params={"tenant_id": T})).json() == {
        "tenant_id": T,
        "business_id": None,
        "specs": [],
        "batches": [],
    }


async def test_when_the_tools_service_is_down_the_answer_says_so(setup: Any) -> None:
    http, service, _ = setup
    service.down = True
    reply = await http.get(f"{URL}/operations", params={"tenant_id": T, "spec_id": "spec-1234"})
    assert reply.status_code == 503 and reply.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


async def test_without_a_tools_service_the_setup_is_unavailable(tmp_path: Path) -> None:
    from agent_service.container import build_container
    from tests.conftest import FIXED_TODAY
    from tests.support import make_settings

    async for http in serve(build_container(make_settings(tmp_path, fixed_today=FIXED_TODAY))):
        assert (await http.get(URL, params={"tenant_id": T})).status_code == 503


async def test_only_a_signed_in_manager_may_use_it(tmp_path: Path, service: ToolsService) -> None:
    config = tmp_path / "tenants"
    shutil.copytree(PROJECT_ROOT / "config" / "tenants", config)
    container = build(tmp_path, Fake(tmp_path), service, auth_required=True, secret_key="k" * 32, config_dir=config)
    assert container.auth is not None and container.tools is not None
    container.tools.admin = ToolsAdminClient("http://127.0.0.1:9", transport=httpx.MockTransport(service.handle))
    await container.auth.create_user("agent@example.com", "Agent", "agent", (T,), PASSWORD)
    await container.auth.create_user("mona@example.com", "Mona", "manager", (T,), PASSWORD)
    async for http in serve(container):
        assert (await http.get(URL, params={"tenant_id": T})).status_code == 401
        login = await http.post("/v1/auth/login", json={"email": "agent@example.com", "password": PASSWORD})
        csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
        assert (await http.get(URL, params={"tenant_id": T})).status_code == 403
        assert (
            await http.post(
                f"{URL}/batches",
                params={"tenant_id": T},
                json={"spec_id": "spec-1234", "operation_ids": ["a"]},
                headers=csrf,
            )
        ).status_code == 403
        assert service.calls == []
        await http.post("/v1/auth/logout", headers=csrf)
        login = await http.post("/v1/auth/login", json={"email": "mona@example.com", "password": PASSWORD})
        csrf = {"X-CSRF-Token": login.json()["csrf_token"]}
        assert (await http.get(URL, params={"tenant_id": T})).status_code == 200
        made = await http.post(
            f"{URL}/batches",
            params={"tenant_id": T},
            json={"spec_id": "spec-1234", "operation_ids": ["a"]},
            headers=csrf,
        )
        assert made.status_code == 200 and service.batches["batch-0001"]["request"]["actor"] == "Mona"
