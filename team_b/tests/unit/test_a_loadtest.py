"""The load-test checker catches planted violations, passes a real run, and the runner works with chaos."""

import copy
import io
from contextlib import redirect_stdout
from typing import Any

import pytest
from loadtest import demo
from loadtest.checker import chaos_effects, check, decisions, percentile, stage_percentiles
from loadtest.run import main


def trace(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "trace_id": "t1" + "0" * 30, "kind": "customer_turn", "decision": "answer", "escalation_reason": None,
        "identity": {"verified": True, "customer_id": "C-101"}, "tool_calls": [], "policy": [], "errors": [],
        "steps": [{"stage": "understand", "status": "ok", "duration_ms": 3.0}], "latency_ms": 9.0,
    }  # fmt: skip
    return {**base, **over}


def write_call(request_id: str = "w1", status: str = "success", code: str | None = None) -> dict[str, Any]:
    return {"request_id": request_id, "tool": "create_return", "operation_kind": "create", "status": status,
            "error_code": code, "policy_request_id": "p1"}  # fmt: skip


def entry(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "seq": 1, "tool": "create_return", "request_id": "w1", "arguments": {"order_id": "NS-20790"},
        "actor": "customer",
        "idempotency_key": "k1" + "0" * 30, "policy_request_id": "p1", "approval_id": None, "status": "success",
        "error_code": None, "audit_id": "AUD-1", "reference_id": "RET-1", "applied": True, "replayed": False,
    }  # fmt: skip
    return {**base, **over}


def good() -> dict[str, Any]:
    allow = {"request_id": "p1", "decision": "allow", "reason_code": "X", "action": "create_return"}
    return {
        "audit": [entry()],
        "traces": [trace(decision="execute", tool_calls=[write_call()], policy=[allow])],
        "orders": {"NS-20790": {"customer_id": "C-101", "order_total": 640}},
    }


def test_a_clean_report_passes() -> None:
    assert check(good()) == []


def broken(change: Any) -> list[str]:
    report = copy.deepcopy(good())
    change(report)
    return check(report)


def test_a_write_with_no_policy_answer_is_caught() -> None:
    assert any("no policy answer" in p for p in broken(lambda r: r["traces"][0].update(policy=[])))


def test_a_write_after_a_deny_is_caught() -> None:
    assert any("after a deny" in p for p in broken(lambda r: r["traces"][0]["policy"][0].update(decision="deny")))


def test_a_write_that_needed_a_person_is_caught() -> None:
    changed = broken(lambda r: r["traces"][0]["policy"][0].update(decision="require_human"))
    assert any("needed a person's approval" in p for p in changed)


def test_a_write_by_a_person_without_an_approval_is_caught() -> None:
    assert any("without an approval id" in p for p in broken(lambda r: r["audit"][0].update(actor="human")))


def test_a_write_the_traces_never_saw_is_caught() -> None:
    assert any("no trace recorded it" in p for p in broken(lambda r: r["audit"][0].update(request_id="ghost")))


def test_the_same_key_applied_twice_is_caught() -> None:
    def twice(r: dict[str, Any]) -> None:
        r["audit"].append(entry(seq=2, request_id="w2"))
        r["traces"][0]["tool_calls"].append(write_call("w2"))

    assert any("applied 2 times" in p for p in broken(twice))


def test_an_order_refunded_twice_is_caught() -> None:
    def twice(r: dict[str, Any]) -> None:
        for n in (1, 2):
            r["audit"].append(entry(seq=n + 5, tool="create_refund", request_id=f"f{n}", idempotency_key=f"key{n}",
                                    arguments={"order_id": "NS-20790", "amount": 640}))  # fmt: skip
            r["traces"][0]["tool_calls"].append({**write_call(f"f{n}"), "tool": "create_refund"})

    assert any("refunded 2 times" in p for p in broken(twice))


def test_a_write_on_someone_elses_order_is_caught() -> None:
    assert any("is not C-101's" in p for p in broken(lambda r: r["orders"]["NS-20790"].update(customer_id="C-999")))


def test_a_write_by_an_unverified_customer_is_caught() -> None:
    assert any("not verified" in p for p in broken(lambda r: r["traces"][0]["identity"].update(verified=False)))


def test_a_refund_of_the_wrong_amount_is_caught() -> None:
    def refund(r: dict[str, Any]) -> None:
        r["audit"][0].update(tool="create_refund", arguments={"order_id": "NS-20790", "amount": 9999})
        r["traces"][0]["tool_calls"][0]["tool"] = "create_refund"

    assert any("not the order total" in p for p in broken(refund))


def test_saying_done_without_a_proven_write_is_caught() -> None:
    assert any("without a proven write" in p for p in broken(lambda r: r["audit"][0].update(audit_id=None)))


def test_a_write_although_a_check_could_not_run_is_caught() -> None:
    assert any(
        "a check could not run" in p for p in broken(lambda r: r["traces"][0].update(errors=["rule checker failed: X"]))
    )


def test_an_unclear_write_that_was_not_handed_to_a_person_is_caught() -> None:
    def unclear(r: dict[str, Any]) -> None:
        r["audit"][0].update(status="error", applied=False, audit_id=None, reference_id=None)
        r["traces"][0].update(decision="refuse", tool_calls=[write_call(status="error", code="OUTCOME_UNKNOWN")])

    assert any("unclear write was not handed" in p for p in broken(unclear))


def test_percentiles_and_stage_tables() -> None:
    assert percentile([], 0.5) is None and percentile([1, 2, 3, 4], 0.5) == 2 and percentile([1, 2, 3, 4], 0.95) == 4
    report = {"traces": [trace(), trace(steps=[{"stage": "understand", "status": "ok", "duration_ms": 5.0}])]}
    stages = stage_percentiles(report)
    assert stages["understand"] == {"count": 2, "p50_ms": 3.0, "p95_ms": 5.0} and "(whole turn)" in stages
    assert decisions(report)["answer"] == 2 and chaos_effects({"traces": [trace(escalation_reason="x")]})["x"] == 1


def test_the_demo_mix_is_repeatable_and_varied() -> None:
    import random

    first = [demo.pick(random.Random(n)) for n in range(40)]
    assert first == [demo.pick(random.Random(n)) for n in range(40)]
    assert len({c.name for c in first}) >= 6 and all(c.messages for c in first)


# ---- the runner, on the real app ----


@pytest.mark.parametrize(
    "chaos", [[], ["rule_checker:0.5@0.1", "safety_screen:0.5@0.2", "shop:0.5@0.3", "policy_search:0.5@0.4"]]
)
async def test_a_short_run_passes_the_checker_with_and_without_chaos(chaos: list[str]) -> None:
    args = ["--customers", "12", "--conversations", "3", "--seed", "5", "--p95-limit", "3000"]
    for item in chaos:
        args += ["--chaos", item]
    out = io.StringIO()
    with redirect_stdout(out):
        code = await main(args)
    text = out.getvalue()
    assert code == 0, text
    assert "CHECKER: PASS" in text and "per stage" in text and "per endpoint" in text
    if chaos:
        assert "dependency_unavailable" in text  # the failures really bit, and were handled
