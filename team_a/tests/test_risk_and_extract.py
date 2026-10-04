import pytest

from team_a import llm
from team_a.knowledge.index import build_passages
from team_a.policy import risk
from team_a.policy.extract import _validate_candidate
from team_a.schemas import ClassifyRiskRequest


def classify(message: str, use_llm: bool = False):
    return risk.classify_risk(ClassifyRiskRequest(request_id="r", tenant_id="shop_001", message=message,
                                                  use_llm=use_llm))


@pytest.mark.parametrize("message,category", [
    ("في عملية دفع على بطاقتي معملتهاش", "fraud_suspected"),
    ("I will contact my lawyer", "legal_regulatory"),
    ("law ma rag3toush el flos ha3mel 2adeya", "legal_regulatory"),
    ("الشاحن ولع في ايدي", "medical_safety"),
    ("3ayez ta3weed 3shan el ta2kheer", "compensation_demand"),
    ("my account was hacked", "identity_concern"),
])
def test_keyword_layer_flags_mandatory_escalation(message, category):
    result = classify(message)
    assert category in result.categories and result.mandatory_escalation


@pytest.mark.parametrize("message", ["fein el order bta3y?", "ممكن أرجع المنتج؟", "fe ta8lef hedeya?"])
def test_ordinary_requests_are_not_flagged(message):
    assert not classify(message).mandatory_escalation


def test_llm_can_add_but_never_remove_categories(monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(llm, "complete_json", lambda *_: {"categories": [], "rationale": "looks fine"})
    assert classify("I will contact my lawyer", use_llm=True).categories == ["legal_regulatory"]

    monkeypatch.setattr(llm, "complete_json",
                        lambda *_: {"categories": ["medical_safety", "not_a_category"], "rationale": "burn"})
    result = classify("the kettle hurt my hand", use_llm=True)
    assert result.categories == ["medical_safety"] and result.method == "keywords+llm"


def test_llm_outage_falls_back_to_keywords(monkeypatch):
    monkeypatch.setattr(llm, "is_configured", lambda: True)

    def down(*_):
        raise llm.LLMUnavailable("rate limited")

    monkeypatch.setattr(llm, "complete_json", down)
    result = classify("my account was hacked", use_llm=True)
    assert result.method == "keywords" and result.mandatory_escalation


@pytest.fixture(scope="module")
def return_window_passage():
    records, _ = build_passages("shop_001")
    return next(r for r in records if r["citation"] == "return_policy@v2#s2")


def candidate(**overrides):
    base = {
        "action": "create_return",
        "conditions": [{"field": "days_since_delivery", "op": "<=", "value": 14, "from": "facts"}],
        "effect": "allow",
        "else_effect": "deny",
        "quote": "يحق للعميل استرجاع المنتج خلال 14 يومًا من تاريخ الاستلام.",
        "user_message": {"ar": "مدة الاسترجاع 14 يوم.", "en": "Returns within 14 days."},
    }
    return {**base, **overrides}


def test_grounded_candidate_becomes_proposed_rule(return_window_passage):
    rule = _validate_candidate(candidate(), return_window_passage, "2026-09-01")
    assert rule.approval_status == "proposed"
    assert rule.source.citation == "return_policy@v2#s2"
    assert rule.rule_id.startswith("P-RETURN-POLICY-")


@pytest.mark.parametrize("bad", [
    {"quote": "يحق للعميل استرجاع المنتج خلال 60 يومًا"},                     # not in the passage
    {"action": "delete_database"},                                              # unknown action
    {"conditions": [{"field": "customer_mood", "op": "==", "value": "happy"}]},  # unknown fact
    {"effect": "maybe"},                                                        # invalid effect
])
def test_ungrounded_or_invalid_candidates_are_dropped(return_window_passage, bad):
    assert _validate_candidate(candidate(**bad), return_window_passage, "2026-09-01") is None


def test_only_free_models_are_used(monkeypatch):
    from team_a import config
    import dataclasses

    patched = dataclasses.replace(config.settings, openrouter_api_key="k",
                                  openrouter_models=["openai/gpt-5", "google/gemma-4-31b-it:free"])
    monkeypatch.setattr(llm, "settings", patched)
    assert llm._free_models() == ["google/gemma-4-31b-it:free"]
