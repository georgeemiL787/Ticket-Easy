"""The onboarding wizard from nothing to live, in process: stand-in knowledge service and a scripted tools service."""

import base64
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_service.adapters.standins.knowledge_admin import StandinKnowledgeAdmin
from agent_service.adapters.team_c_admin import TeamCAdmin
from agent_service.container import Container, build_container
from agent_service.domain.bundle import BundleTool, ToolBindingRef
from agent_service.domain.onboarding import BusinessProfile, OnboardingDraft
from agent_service.onboarding import generate_config, slug, unique_id
from tests.conftest import FIXED_TODAY
from tests.support import make_settings
from tests.unit.adapters.test_team_c_tools import Fake
from tests.unit.api.test_business_tools import build, serve
from tests.unit.api.test_tool_setup import ARTIFACT, ITEM, ToolsService

PROFILE = {"name": "Cairo Books", "locales": ["en", "ar"], "default_locale": "en", "tone": "friendly",
           "order_id_prefix": "CB-", "order_id_digits": 5, "contacts": ["Mona", "Sami"]}  # fmt: skip
T = "cairo_books"


@pytest.fixture
async def wizard(tmp_path: Path) -> AsyncIterator[tuple[httpx.AsyncClient, ToolsService, Container]]:
    service = ToolsService()
    container = build(tmp_path, Fake(tmp_path), service)
    assert container.team_c is not None
    container.team_c.admin = TeamCAdmin("http://127.0.0.1:9", transport=httpx.MockTransport(service.handle))
    async for http in serve(container):
        yield http, service, container


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


async def create(http: httpx.AsyncClient, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    reply = await http.post("/v1/onboarding", json=profile or PROFILE)
    assert reply.status_code in (200, 201), reply.text
    return reply.json()


async def chat(
    http: httpx.AsyncClient, text: str = "I want to talk to a person", token: str | None = None
) -> httpx.Response:
    headers = {"X-Preview-Token": token} if token else {}
    return await http.post(
        f"/v1/conversations/c-{abs(hash(text))}/messages", json={"tenant_id": T, "text": text}, headers=headers
    )


async def test_creating_a_business_makes_a_draft_that_customers_cannot_reach(wizard: Any) -> None:
    http, _, container = wizard
    made = await create(http)
    assert (
        made["tenant_id"] == T and made["step"] == "profile" and made["live"] is False and made["bundle_version"] == 1
    )
    assert made["profile"]["contacts"] == ["Mona", "Sami"] and made["steps"][0] == "profile"
    assert container.bundles is not None and T in container.bundles.drafts and T in container.tenants
    assert (
        container.tenants.get(T).managers == ("Mona", "Sami")
        and container.tenants.get(T).order_id_pattern == "CB\\-\\d{5}"
    )
    refused = await chat(http)
    assert refused.status_code == 404 and refused.json()["error"]["code"] == "TENANT_NOT_LIVE"
    assert (await http.get(f"/v1/tenants/{T}/welcome")).status_code == 404


async def test_the_test_chat_uses_the_draft_and_marks_its_traces(wizard: Any) -> None:
    http, _, container = wizard
    await create(http)
    preview = (await http.post(f"/v1/onboarding/{T}/preview")).json()
    assert preview["chat_url"].startswith(f"/chat?tenant_id={T}&preview=") and preview["live"] is False
    token = preview["token"]
    welcome = await http.get(f"/v1/tenants/{T}/welcome", headers={"X-Preview-Token": token})
    assert welcome.status_code == 200 and welcome.json()["display_name"] == "Cairo Books"
    other = await http.get("/v1/tenants/shop_001/welcome", headers={"X-Preview-Token": token})
    assert other.status_code == 200  # a live business never needed a pass
    reply = await chat(http, token=token)
    assert reply.status_code == 200  # (the stand-in safety screen has no data for a new business and fails closed)
    traces = await container.traces.query(T, limit=5)
    assert traces and all(t.versions.get("preview") == "1" and t.versions.get("bundle_version") == "1" for t in traces)
    assert (await chat(http, "hello", token="1.deadbeef")).status_code == 404  # a forged or expired pass opens nothing
    assert (await chat(http, "hello again", token=None)).json()["error"]["code"] == "TENANT_NOT_LIVE"


async def test_sending_the_same_name_again_changes_nothing_and_a_live_name_is_refused(wizard: Any) -> None:
    http, _, container = wizard
    first = await create(http)
    again = await create(http, {**PROFILE, "tone": "formal"})
    assert again["tenant_id"] == first["tenant_id"] and again["bundle_version"] == 1
    assert (await http.get("/v1/onboarding")).json()["businesses"][0]["tenant_id"] == T
    clash = await http.post("/v1/onboarding", json={**PROFILE, "name": "Shop 001"})
    assert clash.status_code == 409
    other = await create(http, {**PROFILE, "name": "Cairo  Books!"})
    assert other["tenant_id"] == "cairo_books_2"
    assert container.bundles is not None and {T, "cairo_books_2"} <= container.bundles.drafts


async def test_a_bad_profile_is_refused(wizard: Any) -> None:
    http, _, _ = wizard
    for bad in (
        {**PROFILE, "name": "x"},
        {**PROFILE, "default_locale": "ar", "locales": ["en"]},
        {**PROFILE, "order_id_prefix": "a b"},
        {**PROFILE, "contacts": ["m" * 90]},
        {**PROFILE, "order_id_digits": 1},
    ):
        assert (await http.post("/v1/onboarding", json=bad)).status_code == 422


async def test_the_profile_can_be_changed_while_the_business_is_a_draft(wizard: Any) -> None:
    http, _, container = wizard
    await create(http)
    changed = await http.put(
        f"/v1/onboarding/{T}/profile", json={**PROFILE, "name": "Cairo Books & Co", "contacts": ["Mona"]}
    )
    assert changed.status_code == 200 and changed.json()["bundle_version"] == 2
    assert container.tenants.get(T).display_name == "Cairo Books & Co" and container.tenants.get(T).managers == (
        "Mona",
    )
    assert (await http.get("/v1/onboarding/ghost")).status_code == 404


async def test_the_knowledge_steps_work_for_a_business_that_is_not_live(wizard: Any) -> None:
    http, _, container = wizard
    await create(http)
    params = {"tenant_id": T}
    up = await http.post(
        "/v1/dashboard/knowledge/documents",
        params=params,
        json={"filename": "Returns.md", "content_base64": b64("# R\n\n## 1. W\n14 days.")},
    )
    assert up.status_code == 201 and up.json()["status"] == "uploaded"
    failed = await http.post(
        "/v1/dashboard/knowledge/documents", params=params, json={"filename": "scan.pdf", "content_base64": b64("junk")}
    )
    assert failed.json()["status"] == "failed"
    assert (
        await http.post(
            "/v1/dashboard/knowledge/documents", params=params, json={"filename": "x.exe", "content_base64": b64("MZ")}
        )
    ).status_code == 422
    review = (await http.get(f"/v1/onboarding/{T}/review")).json()
    assert review["documents"] == {"indexed": 0, "failed": 1, "waiting": 1} and not review["ready"]
    assert {b["code"] for b in review["blockers"]} >= {
        "documents_waiting",
        "no_approved_rules",
        "no_tools",
        "no_test_chat",
    }
    await http.post("/v1/dashboard/knowledge/reindex", params=params)
    admin = container.knowledge_admin
    assert isinstance(admin, StandinKnowledgeAdmin)
    admin.propose(T, "R-ONE", "Returns within 14 days.")
    approved = await http.post("/v1/dashboard/guardrails/R-ONE/approve", params=params)
    assert approved.status_code == 200
    review = (await http.get(f"/v1/onboarding/{T}/review")).json()
    assert review["documents"]["indexed"] == 1 and review["rules"] == {"approved": 1, "pending": 0, "stale": 0}
    assert "no_approved_rules" not in {b["code"] for b in review["blockers"]}


async def test_a_resume_after_a_restart_finds_the_draft_and_its_bundle(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, fixed_today=FIXED_TODAY, store="sqlite")
    async for http in serve(build_container(settings)):
        await create(http)
        await http.put(f"/v1/onboarding/{T}/step", json={"step": "knowledge"})
    async for http in serve(build_container(settings)):  # a new process on the same database
        again = (await http.get(f"/v1/onboarding/{T}")).json()
        assert again["step"] == "knowledge" and again["bundle_version"] == 1 and again["live"] is False
        assert (await chat(http)).json()["error"]["code"] == "TENANT_NOT_LIVE"
        assert (await http.get("/v1/onboarding")).json()["businesses"][0]["profile"]["name"] == "Cairo Books"


async def test_go_live_is_blocked_until_everything_is_ready_and_then_the_business_answers(wizard: Any) -> None:
    http, service, container = wizard
    admin = container.knowledge_admin
    assert isinstance(admin, StandinKnowledgeAdmin) and container.bundles is not None
    await create(http)
    params = {"tenant_id": T}
    blocked = await http.post(f"/v1/onboarding/{T}/go-live")
    assert blocked.status_code == 409 and "not ready" in blocked.json()["error"]["message"]

    await http.post(
        "/v1/dashboard/knowledge/documents",
        params=params,
        json={"filename": "Returns.md", "content_base64": b64("# R\n\n## 1. W\n14 days.")},
    )
    await http.post("/v1/dashboard/knowledge/reindex", params=params)
    admin.propose(T, "R-ONE", "Returns within 14 days.")
    await http.post("/v1/dashboard/guardrails/R-ONE/approve", params=params)

    imported = await http.post(
        "/v1/dashboard/tool-setup/import", params=params, json={"filename": "s.json", "content": '{"openapi": "3.0.3"}'}
    )
    assert imported.status_code == 200 and imported.json()["business_id"] == "biz-1"
    made = await http.post(
        "/v1/dashboard/tool-setup/batches", params=params, json={"spec_id": "spec-1234", "operation_ids": ["op-1"]}
    )
    service.batches[made.json()["id"]]["items"][0]["state"] = "ready_to_publish"
    published = await http.post(f"/v1/dashboard/tool-setup/batches/{made.json()['id']}/publish", params=params, json={})
    assert published.status_code == 200 and published.json()["tools"] == ["open_a_support_ticket"]
    assert container.bundles.active_bundle(T) is None and container.bundles.draft_bundle(T) is not None

    review = (await http.get(f"/v1/onboarding/{T}/review")).json()
    assert {b["code"] for b in review["blockers"]} == {"no_verify_tool", "no_test_chat"}
    assert (
        await http.put(f"/v1/onboarding/{T}/step", json={"step": "review", "verify_tool": "not_a_tool"})
    ).status_code == 422
    assert (
        await http.put(f"/v1/onboarding/{T}/step", json={"step": "review", "verify_tool": "open_a_support_ticket"})
    ).status_code == 200
    review = (await http.get(f"/v1/onboarding/{T}/review")).json()
    assert [b["code"] for b in review["blockers"]] == ["no_test_chat"] and review["tools"] == ["open_a_support_ticket"]
    assert (await http.post(f"/v1/onboarding/{T}/go-live")).status_code == 409  # the test chat has not been tried

    token = (await http.post(f"/v1/onboarding/{T}/preview")).json()["token"]
    assert (await chat(http, token=token)).status_code == 200
    review = (await http.get(f"/v1/onboarding/{T}/review")).json()
    assert review["ready"] is True and review["test_messages"] == 1 and review["blockers"] == []

    live = await http.post(f"/v1/onboarding/{T}/go-live")
    assert live.status_code == 200, live.text
    assert live.json() == {
        "tenant_id": T,
        "live": True,
        "bundle_version": live.json()["bundle_version"],
        "tools": ["open_a_support_ticket"],
    }
    active = container.bundles.active_bundle(T)
    assert active is not None and active.status == "active" and active.guardrails.approved_rule_ids == ("R-ONE",)
    assert active.knowledge.provider == "standin" and [d.document_id for d in active.knowledge.documents] == ["returns"]
    config = container.tenants.get(T)
    assert config.agent_mode == "on" and config.identity.verify_tool == "open_a_support_ticket"
    assert T not in container.bundles.drafts and config.preview is False
    customer = await chat(http, "I want to talk to a person")  # no pass needed any more
    assert customer.status_code == 200
    assert (await http.post(f"/v1/onboarding/{T}/go-live")).status_code == 409  # already live
    assert (await http.get(f"/v1/onboarding/{T}")).json()["live"] is True
    last = (await container.traces.query(T, limit=1))[0]
    assert "preview" not in last.versions


async def test_when_the_knowledge_service_is_down_the_review_says_so_and_blocks(wizard: Any) -> None:
    http, _, container = wizard
    await create(http)
    assert isinstance(container.knowledge_admin, StandinKnowledgeAdmin)
    container.knowledge_admin.fail_next = "BACKEND_UNAVAILABLE"
    review = (await http.get(f"/v1/onboarding/{T}/review")).json()
    assert "knowledge_unavailable" in {b["code"] for b in review["blockers"]} and review["ready"] is False


# ---- generation and the draft record, without HTTP ----


def tool(name: str, kind: str, properties: dict[str, Any], required: list[str]) -> BundleTool:
    schema = {"type": "object", "properties": properties, "required": required}
    spec = {
        "name": name,
        "capability": name,
        "title": name.replace("_", " ").title(),
        "description": "does it",
        "operation_kind": kind,
        "input_schema": schema,
    }
    return BundleTool(
        name=name,
        source="team_c",
        risk="high" if kind == "delete" else "low",
        binding=ToolBindingRef(business_id="b", proposal_id=f"p-{name}", spec=spec),
    )


def test_the_final_configuration_has_an_intent_for_every_tool_but_the_one_that_checks_the_customer() -> None:
    from agent_service.onboarding import draft_config

    profile = BusinessProfile.model_validate(PROFILE)
    base = draft_config(T, profile)
    tools = (
        tool("check_customer", "read", {"phone": {"type": "string"}}, ["phone"]),
        tool(
            "look_up_order",
            "read",
            {"order_id": {"type": "string"}, "customer_id": {"type": "string"}},
            ["order_id", "customer_id"],
        ),
        tool("cancel_order", "delete", {"order_id": {"type": "string"}, "reason": {"type": "string"}}, ["order_id"]),
    )
    config = generate_config(base, profile, tools, "check_customer")
    assert set(config.intents) == {"policy_question", "greeting", "look_up_order", "cancel_order"}
    lookup, action = config.intents["look_up_order"], config.intents["cancel_order"]
    assert lookup.kind == "lookup" and lookup.lookup_tool == "look_up_order" and lookup.required_slots == ("order_id",)
    assert lookup.argument_map == {"order_id": "slot:order_id", "customer_id": "identity:customer_id"}
    assert (
        action.kind == "action"
        and action.action_tool == "cancel_order"
        and set(action.argument_map) == {"order_id", "reason"}
    )
    assert config.identity.verify_tool == "check_customer" and config.permissions.max_risk == "high"
    assert (
        config.permissions.allowed_tools == ("check_customer", "look_up_order", "cancel_order")
        and config.agent_mode == "on"
    )
    safe = generate_config(base, profile, tools[:2], None)
    assert safe.permissions.max_risk == "medium" and safe.identity.verify_tool == "verify_customer"


def test_names_become_tenant_ids() -> None:
    assert slug("Cairo Books!") == "cairo_books" and slug("123 Shop") == "shop_123_shop" and slug("***") == "shop"
    assert unique_id("Cairo Books", {"cairo_books", "cairo_books_2"}) == "cairo_books_3"
    assert len(slug("x" * 100)) <= 56


def test_a_draft_never_holds_a_secret() -> None:
    profile = BusinessProfile.model_validate(PROFILE)
    ok = OnboardingDraft(
        tenant_id=T,
        profile=profile,
        updated_at=FIXED_TODAY and __import__("datetime").datetime(2026, 9, 28),
        updated_by="ana",
    )
    assert ok.step == "profile"
    with pytest.raises(ValueError, match="secrets"):
        OnboardingDraft(
            tenant_id=T, profile=profile, verify_tool="api_key_tool", updated_at=ok.updated_at, updated_by="ana"
        )
    assert "token" not in ok.model_dump_json().lower() and "password" not in ok.model_dump_json().lower()
    assert ARTIFACT and ITEM  # the scripted tools service's data is shared with the tool setup tests
