"""Read tools and the shape of every tool result."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from team_b.adapters.standins.json_schema import validate
from team_b.adapters.standins.shop import StandinShop
from team_b.contracts.errors import UpstreamError
from team_b.contracts.tools import ToolResult

Call = Callable[..., Awaitable[ToolResult]]


async def test_list_tools_returns_the_eleven_published_tools(shop: StandinShop) -> None:
    tools = await shop.list_tools("shop_001")
    assert [t.name for t in tools][:3] == ["verify_customer", "get_order", "list_customer_orders"]
    assert len(tools) == 11 and next(t for t in tools if t.name == "delete_customer").human_only
    tools[0].input_schema["junk"] = 1  # a returned copy: changing it must not change the shop
    assert "junk" not in (await shop.list_tools("shop_001"))[0].input_schema


async def test_unknown_tenant_is_an_upstream_error(shop: StandinShop) -> None:
    with pytest.raises(UpstreamError) as info:
        await shop.list_tools("nope_001")
    assert info.value.code == "TENANT_NOT_FOUND" and info.value.retryable is False


async def test_verify_customer_with_the_right_phone(call: Call) -> None:
    result = await call("verify_customer", {"order_id": "NS-20877", "phone": "01012345601"})
    assert result.status == "success" and result.data == {"verified": True, "customer_id": "C-100"}


@pytest.mark.parametrize(
    ("order_id", "phone"),
    [("NS-20877", "01112345602"), ("NS-20877", "01012345699"), ("NS-99999", "01012345601")],
    ids=["another customers phone", "wrong phone", "unknown order"],
)
async def test_verify_customer_fails_the_same_way_for_wrong_phone_and_unknown_order(
    call: Call, order_id: str, phone: str
) -> None:
    result = await call("verify_customer", {"order_id": order_id, "phone": phone})
    assert result.status == "success" and result.data == {"verified": False, "customer_id": None}


async def test_verify_customer_ignores_spaces_and_dashes_but_nothing_else(call: Call) -> None:
    assert (await call("verify_customer", {"order_id": "NS-20877", "phone": "010 1234-5601"})).data["verified"] is True
    # normalising +20 or Arabic digits is the brain job: the shop compares what it is given
    assert (await call("verify_customer", {"order_id": "NS-20877", "phone": "+201012345601"})).data["verified"] is False


async def test_get_order_returns_the_facts_the_rules_need(call: Call) -> None:
    result = await call("get_order", {"order_id": "NS-20877"})
    assert result.status == "success"
    assert result.data["order_status"] == "shipped" and result.data["order_total"] == 890
    assert result.data["expected_delivery_date"] == "2026-09-24" and result.data["delivered_at"] is None
    assert result.data["is_clearance"] is False and result.data["customer_id"] == "C-100"
    assert result.audit_id and result.reference_id is None  # reads have an audit id but no reference id


async def test_get_order_not_found(call: Call) -> None:
    result = await call("get_order", {"order_id": "NS-99999"})
    assert (result.status, result.error_code, result.retryable) == ("error", "NOT_FOUND", False)
    assert result.audit_id is None


async def test_list_customer_orders_is_ordered_by_date(call: Call) -> None:
    result = await call("list_customer_orders", {"customer_id": "C-100"})
    orders = result.data["orders"]
    assert [o["order_id"] for o in orders] == ["NS-20512", "NS-20745", "NS-20877"]
    assert set(orders[0]) == {"order_id", "order_status", "order_total", "placed_at"}
    assert (await call("list_customer_orders", {"customer_id": "C-999"})).error_code == "NOT_FOUND"


async def test_reads_do_not_need_a_policy_id_and_change_nothing(call: Call, shop: StandinShop) -> None:
    result = await call("get_order", {"order_id": "NS-20877"}, policy=None)
    assert result.status == "success" and shop.change_count("shop_001") == 0


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("verify_customer", {"order_id": "NS-20877", "phone": "01012345601"}),
        ("get_order", {"order_id": "NS-20877"}),
        ("list_customer_orders", {"customer_id": "C-100"}),
    ],
)
async def test_read_results_fit_their_output_schema(
    shop: StandinShop, call: Call, tool: str, arguments: dict[str, Any]
) -> None:
    spec = next(t for t in await shop.list_tools("shop_001") if t.name == tool)
    result = await call(tool, arguments)
    assert validate(spec.output_schema, result.data) == []
