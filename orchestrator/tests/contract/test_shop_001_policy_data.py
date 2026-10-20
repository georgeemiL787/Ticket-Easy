"""Policies, rules, risk keywords and synonyms of Nile Style parse into their models and have the planned shape."""

import re
from collections import Counter
from typing import Any

from team_b.contracts.evidence import Passage
from tests.contract.conftest import Document, PolicyPassage, RiskCategory, Rule, SynonymEntry, read

RULE_IDS = {
    "R-RETURN-14D",
    "R-RETURN-EXCLUDED",
    "R-RETURN-UNUSED",
    "R-EXCHANGE-14D",
    "R-REFUND-14D",
    "R-REFUND-LIMIT",
    "R-REFUND-STATUS",
    "R-ADDRESS-BEFORE-SHIP",
    "R-CANCEL-BEFORE-SHIP",
    "R-VOUCHER-LATE-3D",
    "R-VOUCHER-MAX-100",
    "R-TICKET-ALLOWED",
    "R-DEFECT-48H",
}
ARABIC = re.compile(r"[\u0600-\u06ff]")


def test_policies_parse_and_old_return_policy_is_superseded(policies: dict[str, Any]) -> None:
    docs = [Document.model_validate(d) for d in policies["documents"]]
    passages = [PolicyPassage.model_validate(p) for p in policies["passages"]]
    for p in passages:
        Passage.model_validate(
            {**p.model_dump(), "score": 1.0}
        )  # a valid contract Passage once a search score is added
        assert p.passage_id.startswith(f"{p.document_id}@{p.version}#")
    ids = [p.passage_id for p in passages]
    assert len(ids) == len(set(ids)) == 36
    assert Counter(p.document_id for p in passages) == {
        "return_policy": 11,
        "refund_policy": 6,
        "shipping_policy": 9,
        "faq": 10,
    }
    assert {p.passage_id for p in passages if p.superseded} == {f"return_policy@v1#s{n}" for n in (1, 2, 3)}
    assert {(d.document_id, d.version) for d in docs if not d.current} == {("return_policy", "v1")}
    assert {p.language for p in passages} == {"ar", "en", "mixed"}


def test_policies_cover_the_required_topics(policies: dict[str, Any]) -> None:
    text = {p["passage_id"]: p["text"] for p in policies["passages"] if not p["superseded"]}
    assert "14 يومًا" in text["return_policy@v2#s2"]  # returns: 14 days
    assert "غير مستخدم" in text["return_policy@v2#s3"]  # returns: unused
    assert "التصفية" in text["return_policy@v2#s4"]  # excluded: clearance
    assert "استبدال" in text["return_policy@v2#s5"]  # exchanges
    assert "14 يومًا" in text["refund_policy@v1#s1"] and "3000" in text["refund_policy@v1#s3"]
    assert "يمكن رد المبلغ فقط" in text["refund_policy@v1#s5"]  # refund status rule
    assert "pending or processing" in text["shipping_policy@v1#s5"]  # address change
    assert "3 business days" in text["shipping_policy@v1#s6"] and "EGP 100" in text["shipping_policy@v1#s6"]
    assert "cancelled free of charge before it is shipped" in text["shipping_policy@v1#s8"]
    assert "2-3 business days" in text["shipping_policy@v1#s2"]  # delivery times
    assert all(f"faq@v1#q{n:02d}" in text for n in range(1, 11))


def test_old_return_policy_has_the_wrong_window(policies: dict[str, Any]) -> None:
    old = next(p for p in policies["passages"] if p["passage_id"] == "return_policy@v1#s2")
    assert "30" in old["text"] and old["superseded"] is True  # must never be quoted


def test_rules_parse_and_only_one_is_unapproved() -> None:
    rules = [Rule.model_validate(r) for r in read("rules.json")["rules"]]
    assert {r.rule_id for r in rules} == RULE_IDS and len(rules) == 13
    assert [r.rule_id for r in rules if r.status == "proposed"] == ["R-DEFECT-48H"]
    assert all(r.status == "approved" for r in rules if r.rule_id != "R-DEFECT-48H")
    assert all(ARABIC.search(r.user_message.ar) for r in rules)


def test_risk_keywords_cover_every_category_in_three_styles() -> None:
    data = read("risk.json")["categories"]
    assert set(data) == {
        "fraud_suspected",
        "legal_regulatory",
        "medical_safety",
        "compensation_demand",
        "identity_concern",
    }
    for name, raw in data.items():
        cat = RiskCategory.model_validate(raw)
        assert cat.mandatory_escalation is True, name
        assert all(ARABIC.search(t) for t in cat.ar), name
        assert not any(ARABIC.search(t) for t in cat.en + cat.arabizi), name
    assert any(
        re.search(r"\d", t) for t in RiskCategory.model_validate(data["fraud_suspected"]).arabizi
    )  # digits as letters


def test_synonyms_contain_the_required_expansions() -> None:
    entries = [SynonymEntry.model_validate(e) for e in read("synonyms.json")["entries"]]
    means = {v.lower(): set(e.means) for e in entries for v in e.variants}
    assert "return" in means["araga3"] and "refund" in means["flousi"] and "late" in means["et2akhar"]
    assert {e.origin for e in entries} == {"team_a", "team_b"}
