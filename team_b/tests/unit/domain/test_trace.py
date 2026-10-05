from typing import Any

import pytest
from pydantic import ValidationError

from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.trace import DecisionTrace


def trace(**over: Any) -> DecisionTrace:
    base: dict[str, Any] = {
        "trace_id": "t1",
        "request_id": "req-1",
        "tenant_id": "shop_001",
        "conversation_id": "conv-1",
        "turn_index": 0,
        "decision": Decision.ANSWER,
    }
    return DecisionTrace.model_validate({**base, **over})


def policy(request_id: str = "pol-1", effect: str = "allow") -> dict[str, Any]:
    return {"request_id": request_id, "action": "create_refund", "decision": effect, "reason_code": "X"}


def call(kind: str = "create", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "request_id": "call-1",
        "tool": "refund_tool",
        "operation_kind": kind,
        "status": "success",
        "policy_request_id": "pol-1",
    }
    return {**base, **over}


# --- invariant 1: a handoff always has a reason ---


def test_handoff_without_reason_is_rejected() -> None:
    with pytest.raises(ValidationError, match="escalation_reason"):
        trace(decision=Decision.HANDOFF)


def test_handoff_with_reason_is_accepted() -> None:
    t = trace(decision=Decision.HANDOFF, escalation_reason=EscalationReason.POLICY_DENIED, handoff_case_id="c1")
    assert t.escalation_reason is EscalationReason.POLICY_DENIED


@pytest.mark.parametrize("decision", [d for d in Decision if d is not Decision.HANDOFF])
def test_other_decisions_do_not_need_a_reason(decision: Decision) -> None:
    assert trace(decision=decision).escalation_reason is None


# --- invariant 2: every write is backed by a policy entry ---


def test_read_calls_need_no_policy_entry() -> None:
    t = trace(tool_calls=[call("read", policy_request_id=None)])
    assert len(t.tool_calls) == 1


@pytest.mark.parametrize("kind", ["create", "update", "delete"])
def test_write_with_matching_allow_is_accepted(kind: str) -> None:
    t = trace(tool_calls=[call(kind)], policy=[policy("pol-1", "allow")], decision=Decision.EXECUTE)
    assert t.tool_calls[0].operation_kind == kind


@pytest.mark.parametrize("kind", ["create", "update", "delete"])
def test_write_without_any_policy_entry_is_rejected(kind: str) -> None:
    with pytest.raises(ValidationError, match="no policy entry"):
        trace(tool_calls=[call(kind)])


def test_write_without_policy_request_id_is_rejected() -> None:
    with pytest.raises(ValidationError, match="no policy entry"):
        trace(tool_calls=[call(policy_request_id=None)], policy=[policy("pol-1", "allow")])


def test_write_pointing_at_a_different_policy_entry_is_rejected() -> None:
    with pytest.raises(ValidationError, match="no policy entry"):
        trace(tool_calls=[call(policy_request_id="other")], policy=[policy("pol-1", "allow")])


def test_write_after_deny_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not authorized"):
        trace(tool_calls=[call()], policy=[policy("pol-1", "deny")])


def test_write_after_deny_is_rejected_even_with_an_approval_id() -> None:
    with pytest.raises(ValidationError, match="not authorized"):
        trace(tool_calls=[call(approval_id="appr-1")], policy=[policy("pol-1", "deny")])


def test_write_after_require_human_needs_an_approval_id() -> None:
    with pytest.raises(ValidationError, match="without an approval id"):
        trace(tool_calls=[call()], policy=[policy("pol-1", "require_human")])
    t = trace(
        kind="human_action",
        tool_calls=[call(approval_id="appr-1", actor="human")],
        policy=[policy("pol-1", "require_human")],
    )
    assert t.tool_calls[0].approval_id == "appr-1"


def test_one_unauthorized_write_among_several_calls_is_enough_to_reject() -> None:
    calls = [call(request_id="c1"), call("update", request_id="c2", policy_request_id="missing")]
    with pytest.raises(ValidationError):
        trace(tool_calls=calls, policy=[policy("pol-1", "allow")])


def test_failed_write_is_still_checked() -> None:
    with pytest.raises(ValidationError):
        trace(tool_calls=[call(status="error", error_code="TIMEOUT")])


# --- shape ---


def test_trace_is_frozen_and_forbids_unknown_fields() -> None:
    t = trace()
    with pytest.raises(ValidationError):
        t.decision_reason = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        trace(surprise=1)


def test_trace_round_trips_through_json() -> None:
    t = trace(
        tool_calls=[call()],
        policy=[policy()],
        evidence=[{"citation": "return_policy@v2#s2", "document_id": "return_policy", "version": "v2", "score": 0.7}],
        steps=[{"stage": "nlu", "status": "ok", "duration_ms": 1.5}],
    )
    assert DecisionTrace.model_validate_json(t.model_dump_json()) == t
