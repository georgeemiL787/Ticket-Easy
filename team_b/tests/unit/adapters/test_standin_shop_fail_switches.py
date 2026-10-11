"""Failure switches, part 1: fail_next and uncertain."""

from collections.abc import Awaitable, Callable

import pytest

from team_b.adapters.standins.shop import StandinShop
from team_b.contracts.errors import UpstreamError
from team_b.contracts.tools import ToolResult

Call = Callable[..., Awaitable[ToolResult]]
T = "shop_001"
REFUND = {"order_id": "NS-20512", "amount": 800}
ORDER = {"order_id": "NS-20877"}


@pytest.mark.parametrize("code", ["BACKEND_UNAVAILABLE", "TIMEOUT"])
async def test_fail_next_unreachable_raises_and_nothing_changes(call: Call, shop: StandinShop, code: str) -> None:
    shop.fail_next("create_refund", code)
    with pytest.raises(UpstreamError) as info:
        await call("create_refund", REFUND)
    assert (info.value.service, info.value.code, info.value.retryable) == ("shop", code, True)
    assert shop.change_count(T) == 0 and shop.records(T, "refund") == []
    assert (await call("create_refund", REFUND)).status == "success"  # the switch was used up


async def test_fail_next_not_found_comes_back_as_an_error_result(call: Call, shop: StandinShop) -> None:
    shop.fail_next("get_order", "NOT_FOUND")
    result = await call("get_order", ORDER)
    assert (result.status, result.error_code, result.retryable) == ("error", "NOT_FOUND", False)
    assert (await call("get_order", ORDER)).status == "success"


async def test_fail_next_counts_calls_and_queues_in_order(call: Call, shop: StandinShop) -> None:
    shop.fail_next("get_order", "TIMEOUT", times=2)
    shop.fail_next("get_order", "NOT_FOUND")
    outcomes = []
    for _ in range(4):
        try:
            outcomes.append((await call("get_order", ORDER)).error_code or "ok")
        except UpstreamError as exc:
            outcomes.append(exc.code)
    assert outcomes == ["TIMEOUT", "TIMEOUT", "NOT_FOUND", "ok"]


async def test_fail_next_only_hits_its_tool_and_is_logged(call: Call, shop: StandinShop) -> None:
    shop.fail_next("get_order", "BACKEND_UNAVAILABLE")
    assert (await call("list_customer_orders", {"customer_id": "C-100"})).status == "success"
    with pytest.raises(UpstreamError):
        await call("get_order", ORDER)
    entry = shop.audit_log(T)[-1]
    assert (entry.tool, entry.status, entry.error_code, entry.applied) == (
        "get_order",
        "error",
        "BACKEND_UNAVAILABLE",
        False,
    )


async def test_fail_next_rejects_bad_input(shop: StandinShop) -> None:
    with pytest.raises(ValueError, match="code"):
        shop.fail_next("get_order", "OOPS")
    with pytest.raises(ValueError, match="times"):
        shop.fail_next("get_order", "TIMEOUT", times=0)
    with pytest.raises(ValueError, match="unknown tool"):
        shop.fail_next("make_coffee", "TIMEOUT")


async def test_uncertain_write_that_did_not_happen(call: Call, shop: StandinShop) -> None:
    shop.uncertain("create_refund")
    result = await call("create_refund", REFUND)
    assert (result.status, result.error_code, result.write_may_have_applied) == ("error", "OUTCOME_UNKNOWN", True)
    assert result.audit_id is None and result.reference_id is None and result.retryable is False
    assert shop.change_count(T) == 0 and shop.records(T, "refund") == []


async def test_uncertain_write_that_really_happened(call: Call, shop: StandinShop) -> None:
    shop.uncertain("create_refund", applied=True)
    result = await call("create_refund", REFUND)
    assert result.error_code == "OUTCOME_UNKNOWN" and result.write_may_have_applied is True
    assert len(shop.records(T, "refund")) == 1 and shop.change_count(T) == 1  # the shop changed, the caller cannot tell
    assert shop.audit_log(T)[-1].applied is True


async def test_uncertain_results_are_not_replayed_as_success(call: Call, shop: StandinShop) -> None:
    shop.uncertain("create_refund", applied=True)
    await call("create_refund", REFUND, key="k")
    again = await call("create_refund", REFUND, key="k")
    assert again.error_code in {"OUTCOME_UNKNOWN", "REJECTED"}  # never quietly turned into a success
    assert again.status == "error"


async def test_uncertain_only_for_write_tools_and_known_tools(shop: StandinShop) -> None:
    with pytest.raises(ValueError, match="write tool"):
        shop.uncertain("get_order")
    with pytest.raises(ValueError, match="unknown tool"):
        shop.uncertain("make_coffee")
