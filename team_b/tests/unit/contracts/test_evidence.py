import pytest
from pydantic import ValidationError

from team_b.contracts.evidence import Passage, PastTicketResult, RetrievalResult, RiskAssessment

PASSAGE = {
    "passage_id": "return_policy@v2#s2",
    "document_id": "return_policy",
    "version": "v2",
    "section": "2. Return window",
    "language": "en",
    "text": "Items can be returned within 14 days of delivery.",
    "score": 0.66,
}


def test_passage_accepts_team_a_shape_and_ignores_extra_fields() -> None:
    passage = Passage.model_validate({**PASSAGE, "citation": "return_policy@v2#s2", "new_field": 1})
    assert passage.citation == "return_policy@v2#s2"
    assert not hasattr(passage, "new_field")


def test_passage_is_frozen() -> None:
    passage = Passage.model_validate(PASSAGE)
    with pytest.raises(ValidationError):
        passage.text = "changed"  # type: ignore[misc]


def test_passage_needs_a_citation_id() -> None:
    with pytest.raises(ValidationError):
        Passage.model_validate({**PASSAGE, "passage_id": ""})


def test_retrieval_result_with_passages_is_valid() -> None:
    result = RetrievalResult.model_validate({"passages": [PASSAGE]})
    assert result.empty_reason is None
    assert result.passages[0].passage_id == "return_policy@v2#s2"


def test_retrieval_result_empty_needs_reason() -> None:
    with pytest.raises(ValidationError):
        RetrievalResult.model_validate({"passages": []})
    assert RetrievalResult.model_validate({"passages": [], "empty_reason": "below_threshold"}).passages == ()


def test_retrieval_result_cannot_have_passages_and_empty_reason() -> None:
    with pytest.raises(ValidationError):
        RetrievalResult.model_validate({"passages": [PASSAGE], "empty_reason": "no_documents"})


def test_retrieval_result_rejects_unknown_empty_reason() -> None:
    with pytest.raises(ValidationError):
        RetrievalResult.model_validate({"passages": [], "empty_reason": "because"})


def test_past_ticket_result_empty_rule() -> None:
    with pytest.raises(ValidationError):
        PastTicketResult.model_validate({"tickets": []})
    ticket = {
        "ticket_id": "T-1",
        "category": "late",
        "customer_message": "where is it",
        "resolution": "voucher",
        "created_at": "2026-09-01",
        "score": 0.4,
        "citation": "ticket:T-1",
    }
    assert PastTicketResult.model_validate({"tickets": [ticket]}).tickets[0].ticket_id == "T-1"
    with pytest.raises(ValidationError):
        PastTicketResult.model_validate({"tickets": [ticket], "empty_reason": "no_documents"})


def test_risk_assessment_accepts_both_names_for_flagged() -> None:
    ours = RiskAssessment.model_validate({"flagged": True, "categories": ["fraud_suspected"]})
    from_team_a = RiskAssessment.model_validate(
        {"mandatory_escalation": True, "categories": ["fraud_suspected"], "matched_terms": ["x"], "method": "keywords"}
    )
    assert ours.flagged and from_team_a.flagged
    assert from_team_a.matched_terms == ("x",)


def test_risk_assessment_flagged_is_required() -> None:
    with pytest.raises(ValidationError):
        RiskAssessment.model_validate({"categories": []})
