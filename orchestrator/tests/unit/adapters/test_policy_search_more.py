"""Past tickets, the fail_next switch, the container hook, and the text engine underneath."""

import pytest

from team_b.adapters.standins.policy_search import PolicySearchStandin
from team_b.adapters.standins.text_search import build_synonyms, normalize, stem, tokens
from team_b.container import Container, inject
from team_b.contracts.errors import UpstreamError

T = "shop_001"


@pytest.fixture
def search(container: Container) -> PolicySearchStandin:
    assert container.policy_search is not None
    return container.policy_search


async def test_past_tickets_find_a_similar_case(search: PolicySearchStandin) -> None:
    result = await search.search_past_tickets(T, "my order is 5 days late and nobody answers", request_id="r")
    assert result.empty_reason is None and result.tickets[0].category == "late_delivery"
    ticket = result.tickets[0]
    assert ticket.citation == f"ticket:{ticket.ticket_id}" and ticket.created_at.year == 2026
    arabic = await search.search_past_tickets(T, "الاوردر متأخر ومحدش بيرد", request_id="r")
    assert arabic.tickets and arabic.tickets[0].category == "late_delivery"


async def test_past_tickets_no_match_and_top_k(search: PolicySearchStandin) -> None:
    nothing = await search.search_past_tickets(T, "what is the weather in Cairo today", request_id="r")
    assert nothing.tickets == () and nothing.empty_reason == "no_match"
    some = await search.search_past_tickets(T, "refund return order", request_id="r", top_k=2)
    assert len(some.tickets) <= 2


@pytest.mark.parametrize("operation", ["search_knowledge", "get_passage", "search_past_tickets"])
async def test_fail_next_raises_a_retryable_upstream_error_then_recovers(
    search: PolicySearchStandin, operation: str
) -> None:
    async def run() -> object:
        if operation == "search_knowledge":
            return await search.search_knowledge(T, "return", request_id="r")
        if operation == "get_passage":
            return await search.get_passage(T, "return_policy@v2#s2")
        return await search.search_past_tickets(T, "late order", request_id="r")

    search.fail_next(operation, times=2)
    for _ in range(2):
        with pytest.raises(UpstreamError) as info:
            await run()
        assert (info.value.service, info.value.code, info.value.retryable) == (
            "policy_search",
            "BACKEND_UNAVAILABLE",
            True,
        )
    assert await run() is not None  # recovered by itself


async def test_fail_next_only_affects_its_operation_and_validates_input(search: PolicySearchStandin) -> None:
    search.fail_next("get_passage")
    assert (await search.search_knowledge(T, "return", request_id="r")).passages  # another operation is fine
    with pytest.raises(UpstreamError):
        await search.get_passage(T, "return_policy@v2#s2")
    with pytest.raises(ValueError, match="operation"):
        search.fail_next("search_everything")
    with pytest.raises(ValueError, match="times"):
        search.fail_next("get_passage", times=0)


async def test_reset_and_inject(search: PolicySearchStandin) -> None:
    search.inject({"switch": "fail_next", "operation": "search_knowledge", "times": 3})
    search.inject({"switch": "reset"})
    assert (await search.search_knowledge(T, "return", request_id="r")).passages
    with pytest.raises(ValueError, match="unknown policy_search switch"):
        search.inject({"switch": "explode"})


async def test_container_inject_and_evidence_plug_use_the_same_search(container: Container) -> None:
    inject(container, "policy_search", {"switch": "fail_next", "operation": "search_knowledge"})
    with pytest.raises(UpstreamError):
        await container.evidence.search_knowledge(T, "return", request_id="r")  # through the EvidenceProvider plug
    found = await container.evidence.search_knowledge(T, "emta a2dar araga3 el montag?", request_id="r")
    assert found.passages[0].passage_id == "return_policy@v2#s2"
    with pytest.raises(ValueError, match="no failure switches"):
        inject(container, "llm", {"switch": "fail_next"})


def test_normalize_unifies_arabic_forms_digits_and_punctuation() -> None:
    assert normalize("أرجّع المُنتَج!! ٣ أيام؟") == "ارجع المنتج 3 ايام"
    assert normalize("Can't RETURN... flooooos") == "cant return floos"
    assert normalize("مدرسة") == "مدرسه" and normalize("إلى آخر") == "الي اخر"


def test_stem_and_tokens_handle_english_arabic_and_arabizi() -> None:
    assert [stem(w) for w in ("returns", "returned", "returning", "refunded", "shipping", "damaged")] == [
        "return", "return", "return", "refund", "ship", "damag",
    ]  # fmt: skip
    assert stem("address") == "address" and stem("araga3") == "araga3" and stem("3ayez") == "3ayez"
    assert stem("الاوردر") == "اوردر" and stem("والمنتج") == "منتج"
    assert tokens("3ayez araga3 el order") == ["araga3", "order"]  # 3ayez and el are stop words
    assert tokens("Can I return the items?") == ["return", "item"]


def test_synonym_table_maps_variants_to_stemmed_meanings() -> None:
    table = build_synonyms([{"variants": ["Araga3", "morta ga3"], "means": ["ارجع", "Returns"]}])
    assert table["araga3"] == ["ارجع", "return"] and table["morta ga3"] == ["ارجع", "return"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [("return", "return_policy@v2#s2"), ("refund", "refund_policy@v1#s1"), ("استرجاع", "return_policy@v2#s2"),
     ("araga3", "return_policy@v2#s2"), ("voucher", "faq@v1#q07")],
)  # fmt: skip
async def test_one_word_queries_find_the_obvious_passage(
    search: PolicySearchStandin, query: str, expected: str
) -> None:
    result = await search.search_knowledge(T, query, request_id="r")
    assert result.passages and result.passages[0].passage_id == expected


@pytest.mark.parametrize("query", ["weather", "cairo weather", "football"])
async def test_short_unrelated_queries_still_return_no_match(search: PolicySearchStandin, query: str) -> None:
    # "cairo" is in the delivery passage, but "weather" is covered by nothing, so a short query does not get a pass
    assert (await search.search_knowledge(T, query, request_id="r")).empty_reason == "no_match"


def test_arabic_punctuation_is_not_part_of_a_word() -> None:
    assert normalize("بعد كام يوم؟ طيب، تمام؛") == "بعد كام يوم طيب تمام"
    assert tokens("الاستلام؟") == tokens("الاستلام")
