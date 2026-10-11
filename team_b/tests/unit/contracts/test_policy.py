import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from team_b.contracts.evidence import RetrievalResult, RiskAssessment
from team_b.contracts.policy import CheckActionRequest, HumanApproval, PolicyDecision, ToolContext


def _request(**over: object) -> CheckActionRequest:
    base: dict[str, object] = {
        "request_id": "req-1",
        "tenant_id": "shop_001",
        "action": "create_refund",
        "tool": {"name": "refund_tool", "operation_kind": "create", "risk": "high"},
    }
    return CheckActionRequest.model_validate({**base, **over})


def test_request_defaults_are_fail_safe() -> None:
    req = _request()
    assert req.identity.verified is False
    assert req.tool.personal_data is True
    assert req.human_approval is None and req.facts == {} and req.risk_categories == ()


def test_tool_context_derives_side_effects_from_kind() -> None:
    assert ToolContext.model_validate({"name": "t", "operation_kind": "read"}).side_effects is False
    for kind in ("create", "update", "delete"):
        assert ToolContext.model_validate({"name": "t", "operation_kind": kind}).side_effects is True


def test_tool_context_rejects_write_without_side_effects() -> None:
    with pytest.raises(ValidationError):
        ToolContext.model_validate({"name": "t", "operation_kind": "update", "side_effects": False})


def test_tool_context_accepts_team_a_personal_data_name() -> None:
    ctx = ToolContext.model_validate({"name": "t", "operation_kind": "read", "exposes_personal_data": False})
    assert ctx.personal_data is False


@pytest.mark.parametrize("tenant", ["Shop 1", "", "UPPER", "a" * 65])
def test_request_rejects_bad_tenant_id(tenant: str) -> None:
    with pytest.raises(ValidationError):
        _request(tenant_id=tenant)


def test_request_rejects_empty_or_oversized_ids_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _request(request_id="")
    with pytest.raises(ValidationError):
        _request(request_id="x" * 129)
    with pytest.raises(ValidationError):
        _request(surprise=1)


def test_request_carries_human_approval_and_dates() -> None:
    approval = HumanApproval(approved_by="agent-7", case_id="case-1", approved_at=datetime(2026, 9, 28, 12, tzinfo=UTC))
    req = _request(human_approval=approval, as_of=date(2026, 9, 28))
    assert req.human_approval is not None and req.human_approval.case_id == "case-1"
    assert req.as_of == date(2026, 9, 28)
    with pytest.raises(ValidationError):
        HumanApproval(approved_by="", case_id="c", approved_at=datetime(2026, 9, 28, tzinfo=UTC))


def test_policy_decision_values() -> None:
    d = PolicyDecision.model_validate(
        {
            "request_id": "r",
            "decision": "require_human",
            "reason_code": "RULE_REQUIRES_HUMAN",
            "user_message": {"en": "a", "ar": "b"},
        }
    )
    assert d.decision == "require_human" and d.user_message is not None and d.user_message.ar == "b"
    with pytest.raises(ValidationError):
        PolicyDecision.model_validate({"request_id": "r", "decision": "maybe", "reason_code": "X"})


# Recorded Team A examples must parse (skipped if the team_a folder is not in this checkout).
EXAMPLES = Path(__file__).resolve().parents[4] / "team_a" / "contracts" / "examples"
needs_team_a = pytest.mark.skipif(not EXAMPLES.is_dir(), reason="team_a examples not available")


def _body(name: str, part: str) -> dict[str, Any]:
    data = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    body: dict[str, Any] = data[part]["body"]
    return body


@needs_team_a
@pytest.mark.parametrize("name", ["allow", "deny", "require_human", "missing_context"])
def test_team_a_check_action_examples_parse(name: str) -> None:
    file = f"check_action.{name}.json"
    request = _body(file, "request")
    # Team A has no `action` field: its rules are keyed by tool.name, so the Phase 6 adapter must send the
    # capability as tool.name. Here we add `action` the way the adapter will derive it.
    CheckActionRequest.model_validate({**request, "action": request["tool"]["name"]})
    decision = PolicyDecision.model_validate(_body(file, "response"))
    assert decision.decision in {"allow", "deny", "require_human"}


@needs_team_a
def test_team_a_search_and_risk_examples_parse() -> None:
    result = RetrievalResult.model_validate(_body("search_knowledge.success.json", "response"))
    assert result.passages and result.passages[0].citation == result.passages[0].passage_id
    assert RetrievalResult.model_validate(_body("search_knowledge.empty.json", "response")).empty_reason
    assert RiskAssessment.model_validate(_body("classify_risk.flagged.json", "response")).flagged is True
    assert RiskAssessment.model_validate(_body("classify_risk.clear.json", "response")).flagged is False
