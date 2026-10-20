import pytest
from pydantic import ValidationError

from team_b.contracts.errors import UpstreamError
from team_b.contracts.tools import ToolCallRequest, ToolResult, ToolSpec


def test_upstream_error_carries_its_fields() -> None:
    err = UpstreamError("shop", "TIMEOUT", "no answer", retryable=True)
    assert (err.service, err.code, err.message, err.retryable) == ("shop", "TIMEOUT", "no answer", True)
    assert "shop" in str(err) and "TIMEOUT" in str(err)
    assert UpstreamError("shop", "X", "y").retryable is False


def test_tool_spec_defaults_are_fail_safe() -> None:
    spec = ToolSpec.model_validate({"name": "t1", "capability": "create_refund", "operation_kind": "create"})
    assert spec.requires_identity and spec.exposes_personal_data
    assert spec.risk == "medium" and not spec.human_only and spec.enabled


def test_tool_spec_ignores_unknown_fields_but_rejects_bad_kind() -> None:
    ToolSpec.model_validate({"name": "t", "capability": "c", "operation_kind": "read", "extra_thing": 1})
    with pytest.raises(ValidationError):
        ToolSpec.model_validate({"name": "t", "capability": "c", "operation_kind": "destroy"})


def test_tool_call_request_needs_idempotency_key_and_forbids_extras() -> None:
    ok = ToolCallRequest(request_id="r1", tool="t", idempotency_key="k1")
    assert ok.actor == "customer" and ok.approval_id is None
    with pytest.raises(ValidationError):
        ToolCallRequest(request_id="r1", tool="t", idempotency_key="")
    with pytest.raises(ValidationError):
        ToolCallRequest.model_validate({"request_id": "r1", "tool": "t", "idempotency_key": "k", "oops": 1})
    with pytest.raises(ValidationError):
        ToolCallRequest.model_validate({"request_id": "r1", "tool": "t", "idempotency_key": "k", "actor": "robot"})


def test_tool_result_success_and_error() -> None:
    ok = ToolResult(status="success", audit_id="a1", reference_id="REF-1", data={"x": 1})
    assert ok.error_code is None
    err = ToolResult(status="error", error_code="TIMEOUT", retryable=True, write_may_have_applied=True)
    assert err.write_may_have_applied


def test_tool_result_error_needs_code() -> None:
    with pytest.raises(ValidationError):
        ToolResult(status="error")


def test_tool_result_success_cannot_be_unclear() -> None:
    with pytest.raises(ValidationError):
        ToolResult(status="success", write_may_have_applied=True)
    with pytest.raises(ValidationError):
        ToolResult(status="success", error_code="X")
