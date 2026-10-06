"""The reason table (priority and next step per reason) and the per-shop overrides."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from team_b.brain.handoff import DEFAULT_PRIORITY, NEXT_STEP, next_step_for, priority_for
from team_b.config import PROJECT_ROOT
from team_b.domain.decision import EscalationReason
from team_b.domain.tenant import EscalationConfig, TenantConfig, TenantConfigError, TenantRegistry
from team_b.domain.understanding import Language

R = EscalationReason
SHOP = TenantConfig.model_validate_json((PROJECT_ROOT / "config/tenants/shop_001.json").read_text(encoding="utf-8-sig"))


def shop_with(**escalation: Any) -> TenantConfig:
    return SHOP.model_copy(update={"escalation": EscalationConfig(**escalation)})


def test_every_reason_has_a_priority_and_a_next_step_in_english_and_arabic() -> None:
    assert set(DEFAULT_PRIORITY) == set(NEXT_STEP) == set(R)
    for reason in R:
        assert NEXT_STEP[reason]["en"].strip() and NEXT_STEP[reason]["ar"].strip()


def test_default_priorities_follow_the_agreed_table() -> None:
    urgent = {R.MANDATORY_RISK}
    high = {
        R.REPEATED_TOOL_FAILURE, R.UNVERIFIED_RESULT, R.DEPENDENCY_UNAVAILABLE,
        R.IDENTITY_FAILED, R.OWNERSHIP_MISMATCH, R.HIGH_FRUSTRATION,
    }  # fmt: skip
    for reason in R:
        expected = "urgent" if reason in urgent else "high" if reason in high else "normal"
        assert priority_for(SHOP, reason) == expected, reason


def test_a_shop_can_change_a_priority_and_a_next_step() -> None:
    shop = shop_with(
        priorities={"no_evidence": "low"}, next_steps={"no_evidence": {"en": "Check the FAQ.", "ar": "راجع الأسئلة."}}
    )
    assert priority_for(shop, R.NO_EVIDENCE) == "low" and priority_for(shop, R.MANDATORY_RISK) == "urgent"
    assert next_step_for(shop, R.NO_EVIDENCE, Language.EN) == "Check the FAQ."
    assert next_step_for(shop, R.NO_EVIDENCE, Language.MIXED) == "راجع الأسئلة."
    assert next_step_for(shop, R.POLICY_DENIED, Language.ARABIZI) == NEXT_STEP[R.POLICY_DENIED]["en"]
    assert next_step_for(shop, R.POLICY_DENIED, Language.AR) == NEXT_STEP[R.POLICY_DENIED]["ar"]


def test_an_override_with_only_english_serves_it_to_arabic_customers_too() -> None:
    shop = shop_with(next_steps={"unsupported": {"en": "Call them."}})
    assert next_step_for(shop, R.UNSUPPORTED, Language.AR) == "Call them."


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ({"priorities": {"made_up": "high"}}, "not an escalation reason"),
        ({"priorities": {"no_evidence": "critical"}}, "must be one of"),
        ({"next_steps": {"made_up": {"en": "x"}}}, "not an escalation reason"),
        ({"next_steps": {"no_evidence": {"fr": "x"}}}, "locale 'fr'"),
        ({"next_steps": {"no_evidence": {"en": "  "}}}, "the text is empty"),
    ],
)
def test_bad_escalation_settings_are_rejected_with_a_clear_message(bad: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        EscalationConfig(**bad)


def test_a_bad_tenant_file_fails_on_load_naming_the_file(tmp_path: Any) -> None:
    data = json.loads((PROJECT_ROOT / "config/tenants/shop_001.json").read_text(encoding="utf-8-sig"))
    data["escalation"]["priorities"] = {"no_evidence": "critical"}
    (tmp_path / "shop_001.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(TenantConfigError, match=r"(?s)shop_001\.json.*must be one of"):
        TenantRegistry.from_dir(tmp_path)
