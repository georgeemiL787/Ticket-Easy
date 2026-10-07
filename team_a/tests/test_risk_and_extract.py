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


@pytest.mark.parametrize("message,category", [
    ("في عملية دفع على كارتي ماعملتوش", "fraud_suspected"),     # ما...ش negation; only معملتوش is listed
    ("الفلوس اتخصمت وأنا مادفعتش", "fraud_suspected"),           # only مدفعتش is listed
    ("ده نصصصب واحتيااال", "fraud_suspected"),                   # chat elongation
    ("mesh ana ely 3amalt el 3amaleya di", "fraud_suspected"),   # ely vs listed elly
    ("7ad dakhal 3ala 7esaby", "identity_concern"),              # mixed 7 / kh spelling
    ("3ayez ta3weeeed", "compensation_demand"),                  # Arabizi elongation
])
def test_spelling_variants_missed_by_exact_matching_are_flagged(message, category):
    assert category in classify(message).categories


def test_every_listed_term_still_matches_itself():
    for category, terms in risk._keywords().items():
        for _, term in terms:
            assert category in risk.keyword_scan(term)[0], term


@pytest.mark.parametrize("message", [
    "fein el order bta3y?", "ممكن أرجع المنتج؟", "fe ta8lef hedeya?",
    "ممكن تسمحلي ارجع المنتج؟",  # تسمم must not shrink to تسم, a substring of تسمح
])
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


def test_applies_if_is_kept_and_checked_against_vocabulary(return_window_passage):
    scope = [{"field": "order_status", "op": "in", "value": ["delivered"], "from": "facts"}]
    rule = _validate_candidate(candidate(applies_if=scope), return_window_passage, "2026-09-01")
    assert [c.field for c in rule.applies_if] == ["order_status"]
    unknown = [{"field": "customer_mood", "op": "==", "value": "happy"}]
    assert _validate_candidate(candidate(applies_if=unknown), return_window_passage, "2026-09-01") is None


def test_rule_id_without_applies_if_is_unchanged(return_window_passage):
    # IDs already in the review queue must not change, or re-running extraction would duplicate them.
    without = _validate_candidate(candidate(), return_window_passage, "2026-09-01").rule_id
    empty = _validate_candidate(candidate(applies_if=[]), return_window_passage, "2026-09-01").rule_id
    assert without == empty


def test_only_free_models_are_used(monkeypatch):
    from team_a import config
    import dataclasses

    patched = dataclasses.replace(config.settings, openrouter_api_key="k",
                                  openrouter_models=["openai/gpt-5", "google/gemma-4-31b-it:free"])
    monkeypatch.setattr(llm, "settings", patched)
    assert llm._free_models() == ["google/gemma-4-31b-it:free"]
