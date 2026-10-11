"""The SQLite stand-in shop for Noon: the tools' physical rules, persistence, and the same switches as the JSON shop."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from team_b.adapters.standins.shop import StandinShop
from team_b.config import PROJECT_ROOT
from team_b.contracts.errors import UpstreamError
from team_b.contracts.tools import ToolCallRequest, ToolResult

T = "noon_eg"
FIXTURES = PROJECT_ROOT / "fixtures"


class Clock:
    def today(self) -> date:
        return date(2026, 10, 8)

    def now(self) -> datetime:
        return datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def shop(db_dir: Path | None = None) -> StandinShop:
    return StandinShop(FIXTURES, Clock(), [T], db_dir=db_dir)  # type: ignore[arg-type]


async def call(
    s: StandinShop, tool: str, args: dict[str, Any], *, key: str | None = None, policy: bool = False
) -> ToolResult:
    request = ToolCallRequest(
        request_id="r", tool=tool, arguments=args, idempotency_key=key or f"{tool}-{sorted(args.items())}",
        policy_request_id="p1" if policy else None, actor="customer",
    )  # fmt: skip
    return await s.call_tool(T, request)


# ---- the data and the reads ----


async def test_the_seed_is_loaded_from_team_as_noon_data() -> None:
    s = shop()
    tools = {t.name for t in await s.list_tools(T)}
    assert tools == {
        "verify_customer",
        "get_order",
        "list_customer_orders",
        "get_return_status",
        "create_return",
        "create_ticket",
    }
    listing = await call(s, "list_customer_orders", {"customer_id": "C-1001"})
    assert listing.status == "success" and len(listing.data["orders"]) >= 2


async def test_an_order_is_returned_with_the_facts_the_rule_checker_needs() -> None:
    order = (await call(shop(), "get_order", {"order_id": "ORD-30001"})).data
    assert order["order_status"] == "delivered" and order["order_total"] == 8999
    assert (order["product_category"], order["product_subcategory"], order["item_condition"]) == (
        "electronics",
        "smartphone",
        "sealed",
    )
    assert order["payment_method"] == "credit_card" and order["return_eligible_tag"] is True
    assert order["delivered_at"] == "2026-10-03" and order["item_id"] == "ORD-30001-I1"


async def test_the_phone_matches_however_it_is_written_and_a_wrong_phone_looks_like_an_unknown_order() -> None:
    s = shop()
    assert (await call(s, "verify_customer", {"order_id": "ORD-30001", "phone": "01000000001"})).data[
        "verified"
    ] is True
    assert (await call(s, "verify_customer", {"order_id": "ORD-30001", "phone": "0100-000-0001"})).data[
        "verified"
    ] is True
    wrong = await call(s, "verify_customer", {"order_id": "ORD-30001", "phone": "01000000009"})
    unknown = await call(s, "verify_customer", {"order_id": "ORD-99999", "phone": "01000000001"})
    assert wrong.data == unknown.data == {"verified": False, "customer_id": None}  # the shop never reveals which


async def test_return_and_refund_status_is_read_from_the_returns_table() -> None:
    status = (await call(shop(), "get_return_status", {"order_id": "ORD-30027"})).data["returns"]
    assert status[0]["return_id"] == "RET-5001" and status[0]["status"] == "refund_initiated"
    assert status[0]["refund_amount_egp"] == 450 and status[0]["refund_destination"] == "original_card"
    assert (await call(shop(), "get_return_status", {"order_id": "ORD-30001"})).data == {"returns": []}
    assert (await call(shop(), "get_return_status", {"order_id": "ORD-1"})).error_code == "NOT_FOUND"


# ---- the writes ----


async def test_a_return_is_a_row_the_order_changes_and_a_second_return_is_refused() -> None:
    s = shop()
    done = await call(s, "create_return", {"order_id": "ORD-30001", "reason": "changed my mind"}, policy=True)
    assert done.status == "success" and done.reference_id == "RET-6001" and done.data["status"] == "requested"
    assert (await call(s, "get_order", {"order_id": "ORD-30001"})).data["order_status"] == "return_in_progress"
    assert (await call(s, "get_return_status", {"order_id": "ORD-30001"})).data["returns"][0]["status"] == "requested"
    again = await call(s, "create_return", {"order_id": "ORD-30001", "reason": "again"}, key="k2", policy=True)
    assert again.error_code == "REJECTED" and s.change_count(T) == 1
    assert s.records(T, "return") == [
        {"reference_id": "RET-6001", "order_id": "ORD-30001", "reason": "changed my mind"}
    ]


async def test_only_a_delivered_order_can_have_a_return() -> None:
    s = shop()
    assert (await call(s, "get_order", {"order_id": "ORD-30026"})).data["order_status"] == "shipped"
    result = await call(s, "create_return", {"order_id": "ORD-30026", "reason": "x"}, policy=True)
    assert result.status == "error" and result.error_code == "REJECTED" and s.change_count(T) == 0


async def test_a_change_without_the_rule_checkers_id_is_refused_and_arguments_are_checked() -> None:
    s = shop()
    assert (await call(s, "create_return", {"order_id": "ORD-30001", "reason": "x"})).error_code == "POLICY_REQUIRED"
    assert (await call(s, "create_return", {"order_id": "ORD-30001"}, policy=True)).error_code == "INVALID_ARGUMENTS"
    assert s.change_count(T) == 0


async def test_the_same_idempotency_key_replays_the_answer_and_changes_nothing() -> None:
    s = shop()
    args = {"order_id": "ORD-30001", "reason": "changed my mind"}
    first = await call(s, "create_return", args, key="same", policy=True)
    second = await call(s, "create_return", args, key="same", policy=True)
    assert first.reference_id == second.reference_id == "RET-6001" and s.change_count(T) == 1
    assert [e.replayed for e in s.audit_log(T)] == [False, True]
    other = await call(s, "create_return", {"order_id": "ORD-30002", "reason": "x"}, key="same", policy=True)
    assert other.error_code == "IDEMPOTENCY_CONFLICT"


async def test_a_ticket_can_be_opened_with_or_without_an_order() -> None:
    s = shop()
    assert (
        await call(s, "create_ticket", {"subject": "hi", "description": "d"}, policy=True)
    ).reference_id == "TKT-7001"
    with_order = await call(
        s, "create_ticket", {"subject": "late", "description": "d", "order_id": "ORD-30001"}, policy=True
    )
    assert with_order.reference_id == "TKT-7002"
    bad = await call(s, "create_ticket", {"subject": "x", "description": "d", "order_id": "ORD-1"}, policy=True)
    assert bad.error_code == "NOT_FOUND"


# ---- the database keeps what happened ----


async def test_a_change_the_audit_log_and_the_idempotency_memory_survive_a_restart(tmp_path: Path) -> None:
    first = shop(tmp_path)
    args = {"order_id": "ORD-30001", "reason": "changed my mind"}
    made = await call(first, "create_return", args, key="k1", policy=True)
    assert (tmp_path / "noon_eg_shop.sqlite3").is_file()

    second = shop(tmp_path)  # a new process opening the same files
    assert second.change_count(T) == 1 and [e.tool for e in second.audit_log(T)] == ["create_return"]
    assert (await call(second, "get_order", {"order_id": "ORD-30001"})).data["order_status"] == "return_in_progress"
    replay = await call(second, "create_return", args, key="k1", policy=True)  # the same key: the original answer
    assert replay.reference_id == made.reference_id and second.change_count(T) == 1
    later = await call(second, "create_ticket", {"subject": "s", "description": "d"}, policy=True)
    assert later.reference_id == "TKT-7001"  # the counters continue where they were


async def test_a_shop_without_a_folder_starts_from_the_seed_every_time_and_reset_starts_again(tmp_path: Path) -> None:
    a, b = shop(), shop()
    await call(a, "create_return", {"order_id": "ORD-30001", "reason": "x"}, policy=True)
    assert b.change_count(T) == 0  # in memory: nobody else sees it
    persistent = shop(tmp_path)
    await call(persistent, "create_return", {"order_id": "ORD-30001", "reason": "x"}, policy=True)
    persistent.reset()
    assert persistent.change_count(T) == 0
    assert (await call(persistent, "get_order", {"order_id": "ORD-30001"})).data["order_status"] == "delivered"


# ---- the same switches as the JSON shop ----


async def test_the_failure_switches_work_for_the_sqlite_shop_too() -> None:
    s = shop()
    s.fail_next("get_order", "BACKEND_UNAVAILABLE", 1)
    with pytest.raises(UpstreamError):
        await call(s, "get_order", {"order_id": "ORD-30001"})
    assert (await call(s, "get_order", {"order_id": "ORD-30001"})).status == "success"  # the next call works
    s.uncertain("create_return", applied=True)
    unclear = await call(s, "create_return", {"order_id": "ORD-30001", "reason": "x"}, policy=True)
    assert unclear.error_code == "OUTCOME_UNKNOWN" and s.change_count(T) == 1  # it really happened, nobody was told
    s.unpublish("get_return_status")
    assert (await call(s, "get_return_status", {"order_id": "ORD-30027"})).error_code == "TOOL_NOT_PUBLISHED"
