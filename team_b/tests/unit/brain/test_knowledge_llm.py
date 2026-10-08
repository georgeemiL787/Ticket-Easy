"""The AI model's part in policy answers: it steers the search and words the reply, but never adds a fact."""

from team_b.brain.knowledge_llm import LLMKnowledgeAssistant
from team_b.contracts.evidence import Passage
from team_b.domain.understanding import Locale
from tests.fakes import FakeLLM

PASSAGE = Passage(
    passage_id="return_policy@v2#s1",
    document_id="return_policy",
    version="v2",
    section="s1",
    language="en",
    text="You can return an item within 14 days of delivery if it is unused.",
    score=0.9,
)


async def test_the_search_query_comes_from_the_model() -> None:
    llm = FakeLLM({"query": "return policy سياسة الاسترجاع"})
    assert (
        await LLMKnowledgeAssistant(llm).search_query("3ayez araga3 el mantag", None) == "return policy سياسة الاسترجاع"
    )


async def test_a_failed_query_step_means_the_plain_search() -> None:
    assert await LLMKnowledgeAssistant(FakeLLM(RuntimeError("down"))).search_query("hi", None) is None


async def test_a_grounded_reply_is_accepted_with_its_citations() -> None:
    llm = FakeLLM(
        {"answerable": True, "answer": "Yes, you have 14 days after delivery.", "citations": [PASSAGE.citation]}
    )
    out = await LLMKnowledgeAssistant(llm).answer("can I return it?", None, [PASSAGE], Locale.EN)
    assert out is not None and out.answerable and out.text and out.citations == (PASSAGE.citation,)


async def test_a_reply_with_an_invented_number_falls_back_to_the_quote() -> None:
    llm = FakeLLM({"answerable": True, "answer": "Yes, you have 30 days.", "citations": [PASSAGE.citation]})
    out = await LLMKnowledgeAssistant(llm).answer("can I return it?", None, [PASSAGE], Locale.EN)
    assert out is not None and out.answerable and out.text == "" and out.citations == (PASSAGE.citation,)


async def test_a_citation_that_was_not_retrieved_is_not_believed() -> None:
    llm = FakeLLM({"answerable": True, "answer": "Yes.", "citations": ["other#1"]})
    assert await LLMKnowledgeAssistant(llm).answer("can I return it?", None, [PASSAGE], Locale.EN) is None


async def test_the_model_can_say_the_passages_do_not_answer() -> None:
    llm = FakeLLM({"answerable": False, "answer": "", "citations": []})
    out = await LLMKnowledgeAssistant(llm).answer("do you sell cars?", None, [PASSAGE], Locale.EN)
    assert out is not None and not out.answerable
