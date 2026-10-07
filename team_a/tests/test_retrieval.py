"""Keyword-only retrieval tests (the hybrid path is measured by `python -m team_a eval-retrieval`)."""

import pytest
from pydantic import ValidationError

from team_a.knowledge.retrieval import get_passage, search_knowledge, search_past_tickets
from team_a.schemas import RetrievalResult, SearchKnowledgeRequest, SearchPastTicketsRequest


def search(index, query, **kw):
    req = SearchKnowledgeRequest(request_id="t", tenant_id="shop_001", query=query, **kw)
    return search_knowledge(req, index, embedder=None)


@pytest.mark.parametrize("query", [
    "ممكن أرجع المنتج بعد كام يوم؟ مدة الاسترجاع",
    "momken araga3 el 7aga ba3d kam yom?",
])
def test_return_window_question_cites_current_policy(keyword_index, query):
    result = search(keyword_index, query)
    assert result.retrieval_mode == "keyword_only"
    assert "return_policy@v2#s2" in [p.citation for p in result.passages]



def test_english_query_reaches_arabic_unknown_payment_section(keyword_index):
    # Q38: English "payment"/"card" must expand to the Arabic wording (دفع, بطاقته) of refund_policy s6.
    citations = [p.citation for p in search(keyword_index, "There is a payment on my card I did not make").passages]
    assert "refund_policy@v1#s6" in citations

def test_superseded_version_is_excluded_by_default(keyword_index):
    citations = [p.citation for p in search(keyword_index, "مدة الاسترجاع 30 يومًا", top_k=20).passages]
    assert citations and not any("@v1#" in c and c.startswith("return_policy") for c in citations)
    with_old = [p.citation for p in search(keyword_index, "مدة الاسترجاع", top_k=20,
                                           include_superseded=True).passages]
    assert "return_policy@v1#s2" in with_old


def test_unknown_question_returns_explicit_empty_result(keyword_index):
    result = search(keyword_index, "What is the price of bitcoin today?")
    assert result.passages == [] and result.empty_reason == "below_threshold"


def test_every_citation_maps_to_a_real_passage(keyword_index):
    for p in search(keyword_index, "shipping fees Cairo", top_k=10).passages:
        assert get_passage(keyword_index, p.citation).text == p.text
    assert get_passage(keyword_index, "return_policy@v9#s1") is None


def test_past_ticket_search(keyword_index):
    req = SearchPastTicketsRequest(request_id="t", tenant_id="shop_001", query="3ayez a8ayar el 3onwan")
    result = search_past_tickets(req, keyword_index, embedder=None)
    assert result.tickets and result.tickets[0].citation.startswith("ticket:")
    assert result.tickets[0].ticket_id in {"T-1004", "T-1005"}


def test_retrieval_result_requires_empty_reason_when_empty():
    with pytest.raises(ValidationError):
        RetrievalResult(request_id="r", tenant_id="shop_001", query="q", passages=[])
