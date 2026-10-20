import pytest

from knowledge_service import llm
from knowledge_service.search.index import build_passages
from knowledge_service.policy import risk
from knowledge_service.policy.extract import _validate_candidate
from knowledge_service.schemas import ClassifyRiskRequest


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
    from knowledge_service import config
    import dataclasses

    patched = dataclasses.replace(config.settings, openrouter_api_key="k",
                                  openrouter_models=["openai/gpt-5", "google/gemma-4-31b-it:free"])
    monkeypatch.setattr(llm, "settings", patched)
    assert llm._free_models() == ["google/gemma-4-31b-it:free"]


# ---- whole-word matching: a danger word inside a longer word is not a risk phrase ----

EVERYDAY_MESSAGES = [
    # English: the term sits inside a longer, harmless word
    "the app crashed when I tried to pay",  # rash
    "my payment page crashes every time",
    "please take out the trash from my cart",
    "a brash colour choice, but I love it",
    "thank you, courtesy of the store",  # court
    "the delivery guy was courteous",
    "great hospitality at your branch",  # hospital
    "my toothbrush arrived with a bent handle",
    "my dog loves to scamper around the box",  # scam
    "we had scampi for dinner while waiting for the parcel",
    "my name is Rashid and I ordered a phone case",  # rash
    "Courtney asked about the delivery date",  # court
    "I got whacked by the door and the box fell",  # hacked
    "the plan is to unlock the screen",  # nothing risky at all
    "where is my order, it was meant to arrive today",
    "can I exchange the shoes for a bigger size",
    "how long do refunds take",
    "I want to change the delivery address",
    "the discourse around this brand is mixed",
    "the sourcing of this coffee is great",
    # Arabic: the term sits inside a longer word
    "أنا اتعينت في منصب جديد وعايز اغير العنوان",  # منصب contains نصب
    "الدكتوراه بتاعتي خلصت وعايز ارجع الشاحن",  # دكتوراه contains دكتور
    "عايز اعرف فين الاوردر بتاعي",
    "ممكن ارجع المنتج؟",
    "قضيت يومين مستني الشحنة",  # قضيت is not قضية
    "محتاج اغير عنوان التوصيل",
    "مخطرش في بالي اني اطلب تاني",  # contains خطر
    "اتأخر الاوردر ومحتاج اعرف الحالة",
    "بكام الشحن لإسكندرية؟",
    "شكرا على المجهود الجميل",
    # Arabizi look-alikes
    "nasbet el application w 3ayez a3raf el status",  # nasbet is not nasb
    "mansab el modeer eh?",
    "fein el order bta3y?",
    "ana 3ayez a8ayar el 3enwan",
    "momken arga3 el montag?",
    "el shehna etakharet ay no3 ta2kheer",
]

REAL_RISK_MESSAGES = [
    # English
    ("my lawyers will call you tomorrow", "legal_regulatory"),
    ("I am going to court over this", "legal_regulatory"),
    ("see you in the courtroom", "legal_regulatory"),
    ("this is a scam", "fraud_suspected"),
    ("you scammed me", "fraud_suspected"),
    ("you are a scammer", "fraud_suspected"),
    ("this looks fraudulent to me", "fraud_suspected"),
    ("someone charged without my permission", "fraud_suspected"),
    ("my card was stolen and used here", "fraud_suspected"),
    ("I was hospitalized after using it", "medical_safety"),
    ("he is in hospital because of your heater", "medical_safety"),
    ("it gave me a rash on my arm", "medical_safety"),
    ("the heater caught fire", "medical_safety"),
    ("I got sunburn from the lamp", "medical_safety"),
    ("it burnt my hand", "medical_safety"),
    ("my child was poisoned by the cleaner", "medical_safety"),
    ("my account has been hacked", "identity_concern"),
    ("someone is impersonating me", "identity_concern"),
    ("I demand compensation now", "compensation_demand"),
    ("you must compensate me", "compensation_demand"),
    # Egyptian Arabic, with attached prefixes and endings
    ("هكلم المحامي بتاعي", "legal_regulatory"),
    ("والمحامي هيتصل بيكم", "legal_regulatory"),
    ("هاخدكم للمحكمة", "legal_regulatory"),
    ("محاميك هيسمع مني", "legal_regulatory"),
    ("وفي المستشفى دلوقتي بسببكم", "medical_safety"),
    ("اتسممت من المنتج ده", "medical_safety"),
    ("الشاحن ولع في ايدي", "medical_safety"),
    ("دي عملية نصب", "fraud_suspected"),
    ("والنصاب ده خد فلوسي", "fraud_suspected"),
    ("كارتي اتسرقت وحد استخدمها", "fraud_suspected"),
    ("عايز تعويض عن اللي حصل", "compensation_demand"),
    ("حسابي اتهكر", "identity_concern"),
    # Arabizi
    ("hakalem el ma7ami bta3y", "legal_regulatory"),
    ("ha3mel 2adeya 3aleko", "legal_regulatory"),
    ("da nasb 3ala el nas", "fraud_suspected"),
    ("el montag wele3 fel 2ouda", "medical_safety"),
    ("ana fel mostashfa dalwa2ty", "medical_safety"),
    ("3ayez ta3weed 3ala el ta2kheer", "compensation_demand"),
    ("7esaby et7aked", "identity_concern"),
]


@pytest.mark.parametrize("message", EVERYDAY_MESSAGES)
def test_everyday_messages_with_a_term_inside_a_longer_word_are_not_flagged(message):
    assert risk.keyword_scan(message) == (set(), []), message
    assert not classify(message).mandatory_escalation


def test_the_everyday_list_is_big_enough():
    assert len(EVERYDAY_MESSAGES) >= 30 and len(REAL_RISK_MESSAGES) >= 30


@pytest.mark.parametrize("message,category", REAL_RISK_MESSAGES)
def test_real_risk_messages_are_still_flagged(message, category):
    result = classify(message)
    assert category in result.categories and result.mandatory_escalation, message


def _in_a_sentence(term: str) -> str:
    if "؀" <= term[0] <= "ۿ":
        return f"عندي مشكلة {term} من فضلكم"
    return f"hello there, {term} please"


def test_every_listed_term_is_found_on_its_own_in_a_short_sentence():
    for category, terms in risk._keywords().items():
        for _, term in terms:
            assert category in risk.keyword_scan(_in_a_sentence(term))[0], term


def test_stem_terms_are_listed_in_keywords_json():
    canonical = {t for terms in risk._keywords().values() for _, t in terms}
    assert {risk.risk_canonical(t) for t in canonical} >= risk.STEM_TERMS
