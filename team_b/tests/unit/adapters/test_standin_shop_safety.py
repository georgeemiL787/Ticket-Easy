"""The policy safety net, idempotent replay and the audit log."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest

from team_b.adapters.standins.shop import StandinShop
from team_b.contracts.tools import ToolResult

Call = Callable[..., Awaitable[ToolResult]]
T = "shop_001"
REFUND = {"order_id": "NS-20512", "amount": 800}

WRITES = [
    ("create_return", {"order_id": "NS-20745", "reason": "x"}),
    ("create_exchange", {"order_id": "NS-20745", "reason": "x"}),
    ("create_refund", REFUND),
    ("cancel_order", {"order_id": "NS-20960"}),
    ("update_delivery_address", {"order_id": "NS-20955", "new_address": "5 Nile Corniche"}),
    ("apply_voucher", {"order_id": "NS-20877", "amount": 100, "reason": "late"}),
    ("create_ticket", {"subject": "s", "description": "d"}),
    ("delete_customer", {"customer_id": "C-102"}),
]


@pytest.mark.parametrize(("tool", "arguments"), WRITES, ids=[w[0] for w in WRITES])
async def test_a_write_without_a_policy_id_is_refused_and_changes_nothing(
    call: Call, shop: StandinShop, tool: str, arguments: dict
) -> None:
    result = await call(tool, arguments, policy=None, actor="human")
    assert (result.status, result.error_code) == ("error", "POLICY_REQUIRED")
    assert result.audit_id is None and result.reference_id is None
    assert shop.change_count(T) == 0
    assert shop.audit_log(T)[-1].applied is False


async def test_an_empty_policy_id_is_not_accepted_by_the_request_model() -> None:
    from pydantic import ValidationError

    from team_b.contracts.tools import ToolCallRequest

    with pytest.raises(ValidationError):
        ToolCallRequest(request_id="r", tool="create_refund", idempotency_key="")


async def test_the_policy_check_comes_before_argument_validation(call: Call) -> None:
    result = await call("create_refund", {"nonsense": 1}, policy=None)
    assert result.error_code == "POLICY_REQUIRED"


async def test_a_human_approval_id_does_not_replace_the_policy_id(call: Call, shop: StandinShop) -> None:
    result = await call("create_refund", REFUND, policy=None, approval="appr-1", actor="human")
    assert result.error_code == "POLICY_REQUIRED" and shop.change_count(T) == 0


async def test_the_same_key_replays_the_original_result_without_a_second_write(call: Call, shop: StandinShop) -> None:
    first = await call("create_refund", REFUND, key="refund-1")
    second = await call("create_refund", REFUND, key="refund-1")
    assert first.status == "success" and second == first  # same reference id, same audit id
    assert len(shop.records(T, "refund")) == 1 and shop.change_count(T) == 1
    log = shop.audit_log(T)
    assert [(e.applied, e.replayed) for e in log] == [(True, False), (False, True)]
    assert log[1].reference_id == first.reference_id


async def test_a_replay_is_not_blocked_by_a_changed_order(call: Call, shop: StandinShop) -> None:
    first = await call("create_refund", REFUND, key="k")
    third = await call("create_refund", REFUND, key="k")  # a plain second refund would be REJECTED (already refunded)
    assert third.status == "success" and third.reference_id == first.reference_id


async def test_a_key_reused_for_different_arguments_or_another_tool_is_a_conflict(
    call: Call, shop: StandinShop
) -> None:
    await call("create_refund", REFUND, key="k")
    other_args = await call("create_refund", {"order_id": "NS-20745", "amount": 100}, key="k")
    other_tool = await call("create_ticket", {"subject": "s", "description": "d"}, key="k")
    assert other_args.error_code == "IDEMPOTENCY_CONFLICT" and other_tool.error_code == "IDEMPOTENCY_CONFLICT"
    assert shop.change_count(T) == 1


async def test_argument_order_does_not_matter_for_a_replay(call: Call) -> None:
    first = await call("create_refund", {"order_id": "NS-20512", "amount": 800}, key="k")
    again = await call("create_refund", {"amount": 800, "order_id": "NS-20512"}, key="k")
    assert again == first


async def test_failed_writes_are_not_remembered(call: Call, shop: StandinShop) -> None:
    rejected = await call("cancel_order", {"order_id": "NS-20877"}, key="k")  # shipped: REJECTED
    assert rejected.error_code == "REJECTED"
    retry = await call("cancel_order", {"order_id": "NS-20960"}, key="k")  # same key, now a valid request
    assert retry.status == "success" and shop.change_count(T) == 1


async def test_reads_are_never_replayed(call: Call) -> None:
    before = await call("get_order", {"order_id": "NS-20960"}, key="r")
    await call("cancel_order", {"order_id": "NS-20960"})
    after = await call("get_order", {"order_id": "NS-20960"}, key="r")
    assert before.data["order_status"] == "pending" and after.data["order_status"] == "cancelled"


async def test_audit_log_records_every_call(call: Call, shop: StandinShop) -> None:
    await call("get_order", {"order_id": "NS-20877"}, key="k1", policy=None)
    await call("create_refund", REFUND, key="k2", policy="pol-9", approval="appr-1", actor="human")
    await call("create_refund", {"order_id": "NS-20512"}, key="k3")  # invalid arguments
    await call("create_refund", {"order_id": "NS-99999", "amount": 5}, key="k4")  # not found
    log = shop.audit_log(T)
    assert [e.seq for e in log] == [1, 2, 3, 4]
    read, write, invalid, missing = log
    assert (read.tool, read.arguments, read.actor, read.idempotency_key) == (
        "get_order",
        {"order_id": "NS-20877"},
        "customer",
        "k1",
    )
    assert (read.status, read.audit_id, read.applied, read.policy_request_id) == ("success", "AUD-00001", False, None)
    assert (write.actor, write.policy_request_id, write.approval_id, write.applied) == (
        "human",
        "pol-9",
        "appr-1",
        True,
    )
    assert (write.reference_id, write.audit_id) == ("REF-30001", "AUD-00002")
    assert (invalid.status, invalid.error_code, invalid.audit_id) == ("error", "INVALID_ARGUMENTS", None)
    assert (missing.error_code, missing.applied) == ("NOT_FOUND", False)
    assert all(e.at == datetime(2026, 9, 28, 12, 0, tzinfo=UTC) for e in log)  # the fixed clock
    assert [e.request_id for e in log] == ["req-1", "req-2", "req-3", "req-4"]


async def test_audit_entries_are_copies(call: Call, shop: StandinShop) -> None:
    arguments = {"order_id": "NS-20512", "amount": 800}
    await call("create_refund", arguments)
    arguments["amount"] = 1
    assert shop.audit_log(T)[0].arguments["amount"] == 800
    shop.audit_log(T).clear()
    assert len(shop.audit_log(T)) == 1
