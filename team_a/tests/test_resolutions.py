"""Resolved-escalation precedents. Safety rules first, then retrieval."""

import dataclasses
import inspect
import json

import pytest
from pydantic import ValidationError

from team_a import config
from team_a.knowledge import index as index_module
from team_a.knowledge import resolutions as res
from team_a.knowledge.index import TenantIndex, build_index, load_resolutions
from team_a.policy import check as check_module
from team_a.policy.check import check_action
from team_a.policy.rules_store import RuleStore
from team_a.schemas import (
    MANDATORY_ESCALATION,
    CheckActionRequest,
    RedactionCheck,
    ResolvedEscalation,
    ResolvedEscalationRequest,
    SearchResolutionsRequest,
)

CLEAN = {
    "request_id": "res-1",
    "tenant_id": "shop_001",
    "conversation_id": "conv-secret-77",
    "category": "refund_exception",
    "redacted_summary": "Refund requested on day 16 after delivery for an unused item; outside the 14-day window.",
    "resolution": "Supervisor approved a one-off exception; refund to the original payment method.",
    "cited_rule_id": "R-REFUND-14D",
    "tags": ["refund", "outside_window"],
    "escalation_reason": "policy_deny",
    "risk_categories": [],
}


def request(**overrides) -> ResolvedEscalationRequest:
    return ResolvedEscalationRequest.model_validate({**CLEAN, **overrides})


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Resolutions corpus and index in temp dirs; the policy corpus is still read from data/."""
    patched = dataclasses.replace(config.settings, resolutions_dir=tmp_path / "res", index_dir=tmp_path / "idx")
    monkeypatch.setattr(index_module, "settings", patched)
    monkeypatch.setattr(res, "settings", patched)
    return patched


def stored_rows(settings) -> list[dict]:
    path = settings.resolutions_file("shop_001")
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# ===================================================== rule 1: mandatory risk

@pytest.mark.parametrize("category", sorted(MANDATORY_ESCALATION))
def test_case_with_mandatory_risk_category_is_never_persisted(isolated, category):
    req = request(risk_categories=[category])
    verdict = res.is_safe_to_persist(req)
    assert not verdict.safe and verdict.reasons == [f"mandatory_risk:{category}"]
    result = res.add_resolution(req, embedder=None)
    assert not result.stored and result.case_id is None
    assert stored_rows(isolated) == []


def test_mandatory_escalation_reason_is_never_persisted(isolated):
    result = res.add_resolution(request(escalation_reason="mandatory_category"), embedder=None)
    assert result.rejected_reasons == ["mandatory_risk:escalation_reason"] and stored_rows(isolated) == []


@pytest.mark.parametrize("overrides,category", [
    # The caller passes no risk categories, but what would be stored still describes a risk case.
    ({"redacted_summary": "Customer reported a card payment they say was fraud and not theirs."}, "fraud_suspected"),
    ({"resolution": "Customer threatened to go to consumer protection; agent explained the policy."}, "legal_regulatory"),
    ({"redacted_summary": "العميل بيقول في عملية دفع على البطاقة مش انا اللي عملتها."}, "fraud_suspected"),
    ({"redacted_summary": "Item caused an allergic rash; customer wanted a return."}, "medical_safety"),
    ({"category": "identity_concern"}, "identity_concern"),
    ({"tags": ["refund", "compensation_demand"]}, "compensation_demand"),
])
def test_risk_is_rechecked_on_the_text_being_stored(isolated, overrides, category):
    verdict = res.is_safe_to_persist(request(**overrides))
    assert f"mandatory_risk:{category}" in verdict.reasons
    assert not res.add_resolution(request(**overrides), embedder=None).stored and stored_rows(isolated) == []


def test_risk_categories_cannot_be_omitted():
    body = {k: v for k, v in CLEAN.items() if k != "risk_categories"}
    with pytest.raises(ValidationError):
        ResolvedEscalationRequest.model_validate(body)


def test_mandatory_risk_rejection_reports_only_risk_reasons():
    assert all(r.startswith("mandatory_risk:") for r in res.is_safe_to_persist(
        request(risk_categories=["fraud_suspected", "legal_regulatory"])).reasons)


# ===================================================== rule 2: personal data

@pytest.mark.parametrize("text,kind", [
    ("Customer can be reached at mona.ali@example.com for follow-up.", "email"),
    ("Called the customer back on 01012345678 to confirm.", "phone"),
    ("Called the customer on +20 101 234 5678 to confirm.", "phone"),
    ("كلمنا العميل على ٠١٠١٢٣٤٥٦٧٨ للتأكيد.", "phone"),             # Arabic-Indic digits
    ("Refund for order NS-20877 was approved after review.", "order_id"),
    ("Refund for order 208771 was approved after review.", "order_id"),
    ("Customer C-100 was refunded after the exception.", "reference_id"),
    ("Refund sent to card 4111 1111 1111 1111 after review.", "payment_details"),
    ("Refund sent to the visa ending in 4242 after review.", "payment_details"),
    ("Courier re-sent to 12 Tahrir Street, building 5, floor 3.", "address"),
    ("المندوب راح عنوان شارع التحرير عماره 5 الدور التالت.", "address"),
    ("Mr. Ahmed asked for an exception on day 16.", "customer_name"),
    ("الأستاذ محمود طلب استثناء في اليوم 16.", "customer_name"),
    ("Customer: I want my money back now please.\nAgent: let me check.", "verbatim_transcript"),
    ('Customer wrote "I bought this two weeks ago and it never fit me at all" in chat.', "verbatim_transcript"),
])
def test_personal_data_is_never_persisted(isolated, text, kind):
    req = request(redacted_summary=text)
    verdict = res.is_safe_to_persist(req)
    assert f"personal_data:{kind}" in verdict.reasons
    assert all(r.startswith("personal_data:") for r in verdict.reasons)  # rejected for PII, not risk
    assert not res.add_resolution(req, embedder=None).stored and stored_rows(isolated) == []


@pytest.mark.parametrize("check,kind", [
    (RedactionCheck(customer_names=["Mona"]), "customer_name"),
    (RedactionCheck(phones=["+20 10 1234 5678"]), "phone"),
    (RedactionCheck(addresses=["Nasr City"]), "address"),
    (RedactionCheck(order_ids=["XQ7"]), "order_id"),
    (RedactionCheck(transcript_messages=["the size i got is way too small and i want it gone"]),
     "verbatim_transcript"),
])
def test_known_personal_data_from_the_handoff_package_is_caught(check, kind):
    summary = ("Mona from Nasr City said the size i got is way too small and i want it gone; "
               "order XQ7, callback 0101 234 5678.")
    req = request(redacted_summary=summary, redaction_check=check)
    assert f"personal_data:{kind}" in res.is_safe_to_persist(req).reasons


def test_rejection_reasons_never_echo_the_personal_data():
    req = request(redacted_summary="Reach mona.ali@example.com or 01012345678 about NS-20877.",
                  redaction_check=RedactionCheck(customer_names=["Mona"]))
    blob = json.dumps(res.is_safe_to_persist(req).reasons)
    assert "mona" not in blob.lower() and "0101" not in blob and "20877" not in blob


@pytest.mark.parametrize("text", [
    "Refund of about EGP 4,500 requested on day 16 after delivery (2026-09-08); flat shipping fee kept.",
    "Exchange within 14 days, size unavailable; R-EXCHANGE-14D applied.",
    "Customer asked about the quality of the fabric before returning.",
])
def test_ordinary_redacted_summaries_pass(text):
    req = request(redacted_summary=text, redaction_check=RedactionCheck(customer_names=["Ali"]))
    assert res.is_safe_to_persist(req).safe


def test_only_the_redacted_fields_are_persisted(isolated):
    req = request(redaction_check=RedactionCheck(customer_names=["Mona"], phones=["01099999999"]))
    result = res.add_resolution(req, embedder=None)
    assert result.stored and result.case_id.startswith("PREC-")
    [row] = stored_rows(isolated)
    assert set(row) == set(ResolvedEscalation.model_fields)
    blob = json.dumps(row)
    assert "conv-secret-77" not in blob and "Mona" not in blob and "01099999999" not in blob
    assert "res-1" not in blob  # the request id only seeds the case-id hash


def test_retried_write_is_stored_once(isolated):
    assert res.add_resolution(request(), None).case_id == res.add_resolution(request(), None).case_id
    assert len(stored_rows(isolated)) == 1


def test_seed_corpus_passes_the_gate():
    for row in load_resolutions("shop_001"):
        req = request(**{k: row[k] for k in ("category", "redacted_summary", "resolution",
                                               "cited_rule_id", "tags", "escalation_reason")})
        assert res.is_safe_to_persist(req).safe, row["case_id"]


# ======================================================= rule 3: advisory only

def test_check_action_never_reads_precedents():
    assert "resolution" not in inspect.getsource(check_module).lower()


def test_precedent_for_an_exception_does_not_change_the_policy_decision(keyword_index):
    # The seed corpus holds a day-16 refund that a supervisor approved as an exception...
    hits = search(keyword_index, "refund requested day 16 outside the window")
    assert hits.precedents and hits.precedents[0].category == "refund_exception" and hits.advisory is True
    # ...yet a day-20 refund is still denied by the deterministic gate.
    req = CheckActionRequest.model_validate({
        "request_id": "r", "tenant_id": "shop_001", "as_of": "2026-09-28",
        "tool": {"name": "create_refund", "operation_kind": "create", "risk": "high"},
        "facts": {"order_status": "delivered", "delivered_at": "2026-09-08"}, "arguments": {"amount": 800},
        "identity": {"verified": True, "customer_id": "C-1"},
    })
    assert check_action(req, RuleStore("shop_001")).decision == "deny"


# ================================================================ retrieval

def search(index, query, risk_categories=(), **kw):
    return res.search_resolutions(SearchResolutionsRequest(
        request_id="r", tenant_id="shop_001", query=query, risk_categories=list(risk_categories), **kw), index, None)


@pytest.mark.parametrize("query,category", [
    ("customer wants to cancel but the order already shipped", "cancellation"),
    ("change delivery address after shipping", "address_change"),
    ("المقاس المطلوب للاستبدال مش متوفر", "exchange_unavailable"),
    ("clearance item return final sale", "clearance_return"),
])
def test_similar_precedent_is_found(keyword_index, query, category):
    result = search(keyword_index, query)
    assert result.precedents[0].category == category
    assert result.similar_count == len(result.precedents) and result.retrieval_mode == "keyword_only"
    assert result.precedents[0].citation == f"resolution:{result.precedents[0].case_id}"


def test_unrelated_query_returns_no_precedent(keyword_index):
    result = search(keyword_index, "What is the price of bitcoin today?")
    assert result.precedents == [] and result.empty_reason == "below_threshold"


def test_mandatory_risk_query_gets_no_precedent(keyword_index):
    result = search(keyword_index, "customer says a card payment was fraud, wants a refund")
    assert result.precedents == [] and result.empty_reason == "mandatory_risk"


def test_case_flagged_by_classify_risk_gets_no_precedent_even_if_keywords_miss(keyword_index):
    # Found while recording contract examples: this phrasing is not in the keyword list, and the query
    # alone returned precedents. The open case's classify_risk result must block it.
    query = "refund requested day 16 outside the window, payment on the card they did not make"
    assert search(keyword_index, query).precedents  # keywords alone miss it
    result = search(keyword_index, query, risk_categories=["fraud_suspected"])
    assert result.precedents == [] and result.empty_reason == "mandatory_risk"


def test_search_requires_the_open_cases_risk_categories():
    with pytest.raises(ValidationError):
        SearchResolutionsRequest.model_validate({"request_id": "r", "tenant_id": "shop_001", "query": "refund"})


def test_risky_record_slipped_into_the_corpus_is_never_returned(keyword_index):
    bad = {**keyword_index.resolutions[0], "case_id": "PREC-BAD0000000",
           "redacted_summary": "refund requested day 16 outside the window after fraud on the card"}
    rows = [bad, *keyword_index.resolutions]
    tampered = dataclasses.replace(keyword_index, resolutions=rows, resolution_vectors=None,
                                   resolution_bm25=index_module.BM25(
                                       [index_module.tokenize(index_module.resolution_embed_text(r)) for r in rows]))
    ids = [p.case_id for p in search(tampered, "refund requested day 16 outside the window").precedents]
    assert ids and "PREC-BAD0000000" not in ids


def test_written_precedent_becomes_searchable(isolated):
    # Start from the seed corpus: keyword-only BM25 needs a few documents for its IDF to mean anything.
    seed = config.settings.resolutions_file("shop_001")
    isolated.resolutions_dir.mkdir(parents=True)
    isolated.resolutions_file("shop_001").write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
    build_index("shop_001", embedder=None)
    assert len(TenantIndex.load("shop_001").resolutions) == 7
    res.add_resolution(request(category="gift_wrap_missing", tags=["gift_wrap"],
                               redacted_summary="Gift wrapping was paid for but the parcel arrived unwrapped.",
                               resolution="Refunded the EGP 30 gift-wrap fee."), embedder=None)
    result = search(TenantIndex.load("shop_001"), "paid for gift wrapping but it came unwrapped")
    assert result.precedents[0].category == "gift_wrap_missing"
