"""Write tools, part 2: cancel, address, voucher, ticket, delete, and invalid arguments."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from team_b.adapters.standins.shop import StandinShop
from team_b.contracts.tools import ToolResult

Call = Callable[..., Awaitable[ToolResult]]
T = "shop_001"


async def test_cancel_order_only_before_shipping(call: Call, shop: StandinShop) -> None:
    ok = await call("cancel_order", {"order_id": "NS-20960"})
    order = shop.order(T, "NS-20960")
    assert ok.data["status"] == "cancelled" and order is not None
    assert (order["order_status"], order["cancelled_at"]) == ("cancelled", "2026-09-28")
    for order_id in ("NS-20877", "NS-20745", "NS-20701", "NS-20960"):  # shipped, delivered, cancelled, cancelled now
        rejected = await call("cancel_order", {"order_id": order_id})
        assert rejected.error_code == "REJECTED" and "has not shipped" in str(rejected.error_message), order_id
    assert shop.change_count(T) == 1


async def test_update_delivery_address_only_before_shipping(call: Call, shop: StandinShop) -> None:
    await call("update_delivery_address", {"order_id": "NS-20955", "new_address": "  5 Nile Corniche  "})
    order = shop.order(T, "NS-20955")
    assert order is not None and order["delivery_address"]["street"] == "5 Nile Corniche"
    shipped = await call("update_delivery_address", {"order_id": "NS-20899", "new_address": "x"})
    assert shipped.error_code == "REJECTED"
    blank = await call("update_delivery_address", {"order_id": "NS-20960", "new_address": "   "})
    assert blank.error_code == "REJECTED"
    missing = await call("update_delivery_address", {"order_id": "NS-00000", "new_address": "x"})
    assert missing.error_code == "NOT_FOUND"


async def test_apply_voucher_once_per_order(call: Call, shop: StandinShop) -> None:
    ok = await call("apply_voucher", {"order_id": "NS-20877", "amount": 100, "reason": "late_delivery"})
    assert ok.data["voucher_code"] == "NILE-30001" and shop.records(T, "voucher")[0]["amount"] == 100
    assert (await call("apply_voucher", {"order_id": "NS-20877", "amount": 50, "reason": "x"})).error_code == "REJECTED"
    assert (await call("apply_voucher", {"order_id": "NS-20899", "amount": -1, "reason": "x"})).error_code == "REJECTED"
    assert (await call("apply_voucher", {"order_id": "NS-00000", "amount": 5, "reason": "x"})).error_code == "NOT_FOUND"


async def test_create_ticket_with_and_without_an_order(call: Call, shop: StandinShop) -> None:
    assert (await call("create_ticket", {"subject": "Hello", "description": "No order"})).data["status"] == "open"
    unknown = await call("create_ticket", {"subject": "s", "description": "d", "order_id": "NS-00000"})
    assert unknown.error_code == "NOT_FOUND"
    assert len(shop.records(T, "ticket")) == 1


async def test_delete_customer_is_human_only_and_needs_no_open_orders(call: Call, shop: StandinShop) -> None:
    by_agent = await call("delete_customer", {"customer_id": "C-102"})
    assert by_agent.error_code == "HUMAN_ONLY" and shop.change_count(T) == 0
    open_orders = await call("delete_customer", {"customer_id": "C-100"}, actor="human")  # has a shipped order
    assert open_orders.error_code == "REJECTED"
    assert (await call("delete_customer", {"customer_id": "C-999"}, actor="human")).error_code == "NOT_FOUND"
    assert (await call("delete_customer", {"customer_id": "C-102"}, actor="human")).data["status"] == "deleted"
    assert (await call("list_customer_orders", {"customer_id": "C-102"})).error_code == "NOT_FOUND"


INVALID = [
    ("create_refund", {"order_id": "NS-20745"}, "missing required field amount"),
    ("create_refund", {"order_id": "NS-20745", "amount": "1250"}, "amount: expected number"),
    ("create_refund", {"order_id": "NS-20745", "amount": True}, "amount: expected number"),
    ("create_return", {"order_id": "NS-20745", "reason": "x", "surprise": 1}, "unknown field surprise"),
    ("cancel_order", {}, "missing required field order_id"),
    ("get_order", {"order_id": 20877}, "order_id: expected string"),
]


@pytest.mark.parametrize(("tool", "arguments", "fragment"), INVALID)
async def test_invalid_arguments_are_refused_and_change_nothing(
    call: Call, shop: StandinShop, tool: str, arguments: dict[str, Any], fragment: str
) -> None:
    result = await call(tool, arguments)
    assert result.error_code == "INVALID_ARGUMENTS" and fragment in str(result.error_message)
    assert shop.change_count(T) == 0
