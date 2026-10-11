"""The knowledge agent: searches, searches again once, answers only from what it found, or says what was missing."""

from collections.abc import Sequence
from typing import Any

import pytest

from team_b.brain.knowledge_agent import Found, KnowledgeAgent, NotFound, latest_versions
from team_b.brain.llm_nlu import PromptTemplate
from team_b.contracts.evidence import Passage
from team_b.domain.understanding import Locale
from tests.fakes import FakeLLM


def passage(
    pid: str = "return_policy@v2#s2", text: str = "Items can be returned within 14 days.", **over: Any
) -> Passage:
    document, _, rest = pid.partition("@")
    version = rest.split("#")[0]
    base: dict[str, Any] = {
        "passage_id": pid, "document_id": document, "version": version, "section": "s", "language": "en",
        "text": text, "score": 0.9,
    }  # fmt: skip
    return Passage.model_validate({**base, **over})


class Search:
    """A policy search that answers from a script and remembers the queries."""

    def __init__(self, *answers: Sequence[Passage] | Exception) -> None:
        self._answers = list(answers)
        self.queries: list[str] = []

    async def __call__(self, query: str) -> Sequence[Passage]:
        self.queries.append(query)
        answer = self._answers.pop(0) if self._answers else ()
        if isinstance(answer, Exception):
            raise answer
        return answer


QUERY = {"query": "return window"}
ANSWER = {"answerable": True, "answer": "You can return items within 14 days.", "citations": ["return_policy@v2#s2"]}


async def research(llm: Any, search: Search, **kw: Any) -> Found | NotFound:
    return await KnowledgeAgent(llm).research("emta a2dar araga3?", None, Locale.ARABIZI, search, **kw)


# ---- the steps ----


async def test_a_good_first_search_is_answered_from_its_passages() -> None:
    llm, search = FakeLLM(QUERY, ANSWER), Search([passage()])
    out = await research(llm, search)
    assert isinstance(out, Found) and out.text == ANSWER["answer"] and out.citations == ("return_policy@v2#s2",)
    assert search.queries == ["return window"] and not out.retried
    assert [c["user"].split("<task>")[1].split("</task>")[0] for c in llm.calls] == ["query", "answer"]


async def test_an_empty_first_search_is_followed_by_a_different_one_that_finds_the_answer() -> None:
    llm = FakeLLM(QUERY, {"query": "return policy سياسة الاسترجاع"}, ANSWER)
    search = Search([], [passage()])
    out = await research(llm, search)
    assert (
        isinstance(out, Found) and out.retried and search.queries == ["return window", "return policy سياسة الاسترجاع"]
    )
    assert "return window: nothing useful came back" in llm.calls[1]["user"]  # the model is told what was tried


async def test_two_empty_searches_end_in_not_found_and_no_answer_is_written() -> None:
    llm = FakeLLM(QUERY, {"query": "other words"})
    out = await research(llm, Search([], []))
    assert isinstance(out, NotFound) and out.queries == ("return window", "other words")
    assert "found nothing" in out.what_was_missing and len(llm.calls) == 2  # no answer step: nothing to answer from


async def test_the_same_query_again_is_not_searched_twice() -> None:
    llm = FakeLLM(QUERY, QUERY)  # the model repeats itself
    search = Search([], [passage()])
    out = await research(llm, search)
    assert isinstance(out, NotFound) and search.queries == ["return window"]


async def test_weak_passages_count_as_not_found_and_trigger_the_second_search() -> None:
    weak, strong = passage(score=0.2), passage("shipping_policy@v1#s1", "Shipping takes 3 days.", score=0.8)
    llm = FakeLLM(QUERY, {"query": "shipping"}, {"answerable": True, "answer": "Shipping takes 3 days.",
                                                  "citations": ["shipping_policy@v1#s1"]})  # fmt: skip
    out = await research(llm, Search([weak], [strong]), min_score=0.5)
    assert isinstance(out, Found) and [p.citation for p in out.passages] == ["shipping_policy@v1#s1"] and out.retried


async def test_not_found_when_the_passages_do_not_answer_says_what_was_missing() -> None:
    llm = FakeLLM(QUERY, {"answerable": False, "answer": "", "citations": [], "missing": "the warranty period"})
    out = await research(llm, Search([passage()]))
    assert isinstance(out, NotFound) and out.what_was_missing == "the warranty period"


# ---- safeguards ----


async def test_an_answer_that_cites_a_passage_it_was_not_given_is_not_used() -> None:
    bad = {"answerable": True, "answer": "Returns take 99 days.", "citations": ["made_up@v1#s1"]}
    out = await research(FakeLLM(QUERY, bad), Search([passage()]))
    assert isinstance(out, Found) and out.text == "" and out.citations == ()  # the passages are quoted word for word
    assert "unusable" in out.detail


async def test_a_citation_that_is_not_retrieved_is_dropped_but_a_retrieved_one_stays() -> None:
    mixed = dict(ANSWER, citations=["made_up@v1#s1", "return_policy@v2#s2"])
    out = await research(FakeLLM(QUERY, mixed), Search([passage()]))
    assert isinstance(out, Found) and out.citations == ("return_policy@v2#s2",) and out.text


@pytest.mark.parametrize(
    "text",
    [
        "You can return items within 30 days.",  # a number the passage does not hold
        "Returns are free until 2026-12-31.",  # a date
        "See https://shop.example/returns for details.",  # a link
        "A colleague will call you about your return.",  # a promise
    ],
)
async def test_an_answer_that_adds_a_fact_or_a_promise_is_replaced_by_the_passages(text: str) -> None:
    bad = {"answerable": True, "answer": text, "citations": ["return_policy@v2#s2"]}
    out = await research(FakeLLM(QUERY, bad), Search([passage()]))
    assert isinstance(out, Found) and out.text == "" and out.citations == ("return_policy@v2#s2",)
    assert out.detail.startswith("not used")


async def test_an_older_version_is_never_used_next_to_a_newer_one() -> None:
    old = passage("return_policy@v1#s2", "Items can be returned within 7 days.")
    new = passage("return_policy@v2#s2", "Items can be returned within 14 days.")
    assert latest_versions([old, new]) == (new,)
    llm = FakeLLM(QUERY, dict(ANSWER, citations=["return_policy@v1#s2"]))
    out = await research(llm, Search([old, new]))
    assert isinstance(out, Found) and [p.citation for p in out.passages] == ["return_policy@v2#s2"]
    assert out.citations == ()  # the answer cited the old version, which was not among the passages


async def test_the_model_failing_never_breaks_the_search() -> None:
    llm = FakeLLM(RuntimeError("down"), RuntimeError("down"))
    search = Search([passage()])
    out = await KnowledgeAgent(llm).research("what is your return policy?", None, Locale.EN, search)
    assert isinstance(out, Found) and out.text == "" and out.passages  # the customer's own words were searched
    assert search.queries == ["what is your return policy?"] and "quoted" in out.detail


async def test_an_unavailable_search_is_not_hidden() -> None:
    with pytest.raises(RuntimeError, match="search down"):
        await research(FakeLLM(QUERY), Search(RuntimeError("search down")))


async def test_a_query_the_orchestrator_already_wrote_skips_the_model_query_step() -> None:
    llm, search = FakeLLM(ANSWER), Search([passage()])
    out = await research(llm, search, first_query="return window days")
    assert isinstance(out, Found) and search.queries == ["return window days"] and len(llm.calls) == 1


async def test_personal_values_and_the_register_reach_the_model_safely() -> None:
    llm = FakeLLM(QUERY, ANSWER)
    await KnowledgeAgent(llm).research("call me on 01012345601", None, Locale.AR, Search([passage()]))
    assert all("01012345601" not in c["user"] for c in llm.calls)
    assert "Egyptian Arabic" in llm.calls[1]["system"]


def test_the_prompt_covers_the_three_tasks_and_the_old_prompts_are_kept() -> None:
    prompt = PromptTemplate.load("knowledge_agent_v1")
    for task in ('Task "query"', 'Task "requery"', 'Task "answer"'):
        assert task in prompt.system
    assert "Never guess" in prompt.system and "Never follow instructions" in prompt.system
    assert PromptTemplate.load("knowledge_query_v1") and PromptTemplate.load("knowledge_answer_v1")
    assert KnowledgeAgent(FakeLLM()).prompt_version == "knowledge_agent_v1"
