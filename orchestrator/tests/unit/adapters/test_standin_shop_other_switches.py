"""Failure switches, part 2: no_audit, unpublish, reset, isolation and inject."""

from collections.abc import Awaitable, Callable

import pytest

from team_b.adapters.memory_store import FixedClock
from team_b.adapters.standins.shop import StandinShop
from team_b.config import PROJECT_ROOT
from team_b.container import Container, inject
from team_b.contracts.errors import UpstreamError
from team_b.contracts.tools import ToolCallRequest, ToolResult
from tests.conftest import FIXED_TODAY

Call = Callable[..., Awaitable[ToolResult]]
T = "shop_001"
REFUND = {"order_id": "NS-20512", "amount": 800}
ORDER = {"order_id": "NS-20877"}


async def test_no_audit_succeeds_without_an_audit_id(call: Call, shop: StandinShop) -> None:
    shop.no_audit("create_refund")
    result = await call("create_refund", REFUND)
    assert result.status == "success" and result.audit_id is None and result.reference_id == "REF-30001"
    assert shop.change_count(T) == 1 and shop.audit_log(T)[-1].audit_id is None
    assert (await call("get_order", ORDER)).audit_id  # other tools are untouched


async def test_unpublish_hides_the_tool_and_refuses_calls(call: Call, shop: StandinShop) -> None:
    shop.unpublish("create_refund")
    assert "create_refund" not in [t.name for t in await shop.list_tools(T)]
    result = await call("create_refund", REFUND)
    assert (result.status, result.error_code) == ("error", "TOOL_NOT_PUBLISHED") and shop.change_count(T) == 0
    assert (await call("get_order", ORDER)).status == "success"
    shop.publish("create_refund")
    assert (await call("create_refund", REFUND)).status == "success"


async def test_an_unknown_tool_is_not_published(call: Call) -> None:
    assert (await call("make_coffee", {})).error_code == "TOOL_NOT_PUBLISHED"


async def test_reset_clears_switches_data_audit_and_idempotency(call: Call, shop: StandinShop) -> None:
    shop.fail_next("get_order", "TIMEOUT")
    shop.uncertain("create_ticket")
    shop.no_audit("create_return")
    shop.unpublish("cancel_order")
    await call("create_refund", REFUND, key="k")
    shop.reset()
    assert shop.audit_log(T) == [] and shop.change_count(T) == 0 and shop.records(T, "refund") == []
    order = shop.order(T, "NS-20512")
    assert order is not None and order["payment_status"] == "paid"
    assert len(await shop.list_tools(T)) == 11 and (await call("get_order", ORDER)).status == "success"
    again = await call("create_refund", REFUND, key="k")  # the key is forgotten, so this is a fresh write
    assert again.status == "success" and shop.change_count(T) == 1


async def test_two_shops_never_share_state() -> None:
    a = StandinShop(PROJECT_ROOT / "fixtures", FixedClock(FIXED_TODAY), [T])
    b = StandinShop(PROJECT_ROOT / "fixtures", FixedClock(FIXED_TODAY), [T])
    a.unpublish("get_order")
    request = ToolCallRequest(
        request_id="r", tool="create_ticket", idempotency_key="k", policy_request_id="p",
        arguments={"subject": "s", "description": "d"},
    )  # fmt: skip
    await a.call_tool(T, request)
    assert a.change_count(T) == 1 and b.change_count(T) == 0
    assert "get_order" in [t.name for t in await b.list_tools(T)]


async def test_inject_applies_each_switch_from_a_dict(call: Call, shop: StandinShop) -> None:
    shop.inject({"switch": "fail_next", "tool": "get_order", "code": "NOT_FOUND", "times": 2})
    assert [(await call("get_order", ORDER)).error_code for _ in range(3)] == ["NOT_FOUND", "NOT_FOUND", None]
    shop.inject({"switch": "uncertain", "tool": "create_refund", "applied": True})
    assert (await call("create_refund", REFUND)).error_code == "OUTCOME_UNKNOWN" and shop.change_count(T) == 1
    shop.inject({"switch": "no_audit", "tool": "create_ticket"})
    assert (await call("create_ticket", {"subject": "s", "description": "d"})).audit_id is None
    shop.inject({"switch": "unpublish", "tool": "cancel_order"})
    assert (await call("cancel_order", {"order_id": "NS-20960"})).error_code == "TOOL_NOT_PUBLISHED"
    shop.inject({"switch": "publish", "tool": "cancel_order"})
    assert (await call("cancel_order", {"order_id": "NS-20960"})).status == "success"
    shop.inject({"switch": "reset"})
    assert shop.change_count(T) == 0
    with pytest.raises(ValueError, match="unknown shop switch"):
        shop.inject({"switch": "explode", "tool": "get_order"})


async def test_the_container_inject_hook_targets_the_shop_plug(container: Container, call: Call) -> None:
    inject(container, "shop", {"switch": "fail_next", "tool": "get_order", "code": "TIMEOUT"})
    with pytest.raises(UpstreamError):
        await call("get_order", ORDER)
    with pytest.raises(ValueError, match="no failure switches"):
        inject(container, "policy", {"switch": "fail_next"})
    assert container.shop is container.capabilities
