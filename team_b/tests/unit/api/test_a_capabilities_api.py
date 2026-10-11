"""GET /v1/capabilities: each configured tool as available, missing or blocked, with the reason."""

import httpx
import pytest

from team_b.container import Container, inject

T = "shop_001"


def by_name(body: dict) -> dict:  # type: ignore[type-arg]
    return {c["name"]: c for c in body["capabilities"]}


async def test_every_configured_tool_is_listed_with_its_state(chat: httpx.AsyncClient) -> None:
    response = await chat.get("/v1/capabilities", params={"tenant_id": T})
    body = response.json()
    assert response.status_code == 200 and body["tenant_id"] == T and body["stale"] is False
    tools = by_name(body)
    assert tools["get_order"]["status"] == "available" and "order_status" in tools["get_order"]["used_by"]
    assert tools["verify_customer"]["used_by"] == ["identity"] and tools["create_refund"]["risk"] == "high"
    assert tools["delete_customer"]["status"] == "blocked" and tools["delete_customer"]["reason"] == "human_only"
    assert [c["status"] for c in body["capabilities"]].count("blocked") == 1 and list(tools) == sorted(tools)


async def test_an_unpublished_tool_is_missing(chat: httpx.AsyncClient, chat_container: Container) -> None:
    inject(chat_container, "shop", {"switch": "unpublish", "tool": "create_return"})
    body = (await chat.get("/v1/capabilities", params={"tenant_id": T})).json()
    missing = by_name(body)["create_return"]
    assert (missing["status"], missing["reason"]) == ("missing", "not_published")
    assert missing["used_by"] == ["return_request"] and missing["operation_kind"] is None


async def test_the_list_is_remembered_until_refresh(chat: httpx.AsyncClient, chat_container: Container) -> None:
    await chat.get("/v1/capabilities", params={"tenant_id": T})  # remembered
    inject(chat_container, "shop", {"switch": "unpublish", "tool": "create_return"})
    cached = by_name((await chat.get("/v1/capabilities", params={"tenant_id": T})).json())
    assert cached["create_return"]["status"] == "available"
    fresh = by_name((await chat.get("/v1/capabilities", params={"tenant_id": T, "refresh": "true"})).json())
    assert fresh["create_return"]["status"] == "missing"


async def test_a_shop_that_is_down_serves_the_last_list_marked_stale(
    chat: httpx.AsyncClient, chat_container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    await chat.get("/v1/capabilities", params={"tenant_id": T})
    from team_b.contracts.errors import UpstreamError

    async def down(tenant_id: str) -> list:  # type: ignore[type-arg]
        raise UpstreamError("shop", "BACKEND_UNAVAILABLE", "down", retryable=True)

    monkeypatch.setattr(chat_container.capabilities, "list_tools", down)
    body = (await chat.get("/v1/capabilities", params={"tenant_id": T, "refresh": "true"})).json()
    assert body["stale"] is True and by_name(body)["get_order"]["status"] == "available"


async def test_with_no_list_at_all_the_answer_is_503(
    chat: httpx.AsyncClient, chat_container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    from team_b.contracts.errors import UpstreamError

    async def down(tenant_id: str) -> list:  # type: ignore[type-arg]
        raise UpstreamError("shop", "BACKEND_UNAVAILABLE", "down", retryable=True)

    monkeypatch.setattr(chat_container.capabilities, "list_tools", down)
    response = await chat.get("/v1/capabilities", params={"tenant_id": T})
    assert response.status_code == 503 and response.json()["error"]["code"] == "UPSTREAM_UNAVAILABLE"


async def test_the_tenant_is_required_and_must_exist(chat: httpx.AsyncClient) -> None:
    assert (await chat.get("/v1/capabilities")).status_code == 422
    unknown = await chat.get("/v1/capabilities", params={"tenant_id": "nope"})
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "TENANT_NOT_FOUND"
