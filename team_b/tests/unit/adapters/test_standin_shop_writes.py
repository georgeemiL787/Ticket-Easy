"""Write tools, part 1: reference ids, returns, exchanges and refunds."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from team_b.adapters.standins.json_schema import validate
from team_b.adapters.standins.shop import StandinShop
from team_b.contracts.tools import ToolResult

Call = Callable[..., Awaitable[ToolResult]]
T = "shop_001"

HAPPY = [
    ("create_return", {"order_id": "NS-20745", "reason": "wrong size"}, "RET-"),
    ("create_exchange", {"order_id": "NS-20745", "reason": "too big", "new_size": "38"}, "EXC-"),
    ("create_refund", {"order_id": "NS-20745", "amount": 1250}, "REF-"),
    ("cancel_order", {"order_id": "NS-20960"}, "CAN-"),
    ("update_delivery_address", {"order_id": "NS-20955", "new_address": "5 Nile Corniche"}, "ADR-"),
    ("apply_voucher", {"order_id": "NS-20877", "amount": 100, "reason": "late_delivery"}, "VCH-"),
    ("create_ticket", {"subject": "Late parcel", "description": "It is 4 days late", "order_id": "NS-20877"}, "TKT-"),
    ("delete_customer", {"customer_id": "C-102"}, "DEL-"),
]


@pytest.mark.parametrize(("tool", "arguments", "prefix"), HAPPY, ids=[h[0] for h in HAPPY])
async def test_every_write_returns_a_reference_and_an_audit_id(
    shop: StandinShop, call: Call, tool: str, arguments: dict[str, Any], prefix: str
) -> None:
    spec = next(t for t in await shop.list_tools(T) if t.name == tool)
    result = await call(tool, arguments, actor="human" if spec.human_only else "customer")
    assert result.status == "success", result.error_message
    assert result.reference_id and result.reference_id.startswith(prefix)
    assert result.data["reference_id"] == result.reference_id
    assert result.audit_id and result.audit_id.startswith("AUD-")
    assert validate(spec.output_schema, result.data) == []
    assert shop.change_count(T) == 1


async def test_reference_numbers_count_up_per_kind(call: Call) -> None:
    first = await call("create_ticket", {"subject": "a", "description": "b"})
    second = await call("create_ticket", {"subject": "c", "description": "d"})
    assert (first.reference_id, second.reference_id) == ("TKT-30001", "TKT-30002")


async def test_create_return_only_for_delivered_orders_and_only_once(call: Call, shop: StandinShop) -> None:
    ok = await call("create_return", {"order_id": "NS-20745", "reason": "x"})
    assert ok.data["status"] == "created" and shop.records(T, "return")[0]["order_id"] == "NS-20745"
    again = await call("create_return", {"order_id": "NS-20745", "reason": "y"})
    assert again.error_code == "REJECTED" and "open return" in str(again.error_message)
    exchange = await call("create_exchange", {"order_id": "NS-20745", "reason": "z"})
    assert exchange.error_code == "REJECTED"  # an order cannot be returned and exchanged at once
    shipped = await call("create_return", {"order_id": "NS-20877", "reason": "x"})
    assert shipped.error_code == "REJECTED" and "delivered" in str(shipped.error_message)
    assert (await call("create_return", {"order_id": "NS-00000", "reason": "x"})).error_code == "NOT_FOUND"
    assert shop.change_count(T) == 1


async def test_create_exchange_records_the_new_size(call: Call, shop: StandinShop) -> None:
    await call("create_exchange", {"order_id": "NS-20790", "reason": "small", "new_size": "XL"})
    assert shop.records(T, "exchange")[0]["new_size"] == "XL"
    assert (await call("create_exchange", {"order_id": "NS-20877", "reason": "x"})).error_code == "REJECTED"


async def test_create_refund_marks_the_order_refunded_once(call: Call, shop: StandinShop) -> None:
    ok = await call("create_refund", {"order_id": "NS-20512", "amount": 800})
    assert ok.data["status"] == "refunded" and ok.data["amount"] == 800
    order = shop.order(T, "NS-20512")
    assert order is not None and order["payment_status"] == "refunded"
    assert (await call("create_refund", {"order_id": "NS-20512", "amount": 800})).error_code == "REJECTED"
    assert len(shop.records(T, "refund")) == 1


REFUND_LIMITS = [
    ("NS-20745", 1251, "exceeds"),
    ("NS-20745", 0, "positive"),
    ("NS-20745", -5, "positive"),
    ("NS-20960", 380, "nothing was paid"),
]


@pytest.mark.parametrize(
    ("order_id", "amount", "fragment"), REFUND_LIMITS, ids=["above total", "zero", "negative", "cod"]
)
async def test_create_refund_physical_limits(
    call: Call, shop: StandinShop, order_id: str, amount: float, fragment: str
) -> None:
    result = await call("create_refund", {"order_id": order_id, "amount": amount})
    assert result.error_code == "REJECTED" and fragment in str(result.error_message)
    assert shop.change_count(T) == 0


async def test_the_shop_does_not_enforce_business_policy(call: Call) -> None:
    # Day 20 and above EGP 3,000 are the RULE CHECKER job. If the shop refused these too, a brain that skipped
    # the rule checker would never be caught by the tests.
    assert (await call("create_refund", {"order_id": "NS-20512", "amount": 800})).status == "success"
    assert (await call("create_refund", {"order_id": "NS-20934", "amount": 3450})).status == "success"
    assert (await call("apply_voucher", {"order_id": "NS-20899", "amount": 500, "reason": "x"})).status == "success"
