"""brain/knowledge.py: what is quoted, the 60-second cache, the normalized retry, quote_citations, the trace rule."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from team_b.brain import knowledge
from team_b.brain.turn import PlannedIntent, TurnContext
from team_b.container import Container
from team_b.contracts.evidence import Passage, RetrievalResult
from team_b.domain.decision import Decision
from team_b.domain.session import SessionState
from team_b.domain.trace import DecisionTrace
from team_b.domain.understanding import Language
from tests.unit.brain.test_pipeline import C, T, last_trace, orch, say

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
RETURN_POLICY = "What is your return policy?"


class CountingSearch:
    """Wraps the stand-in search and records the queries it was asked."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.queries: list[str] = []

    async def search_knowledge(self, tenant_id: str, query: str, **kw: Any) -> RetrievalResult:
        self.queries.append(query)
        result: RetrievalResult = await self.inner.search_knowledge(tenant_id, query, **kw)
        return result

    async def get_passage(self, tenant_id: str, citation: str) -> Passage | None:
        passage: Passage | None = await self.inner.get_passage(tenant_id, citation)
        return passage


def context(
    c: Container, evidence: Any, *, text: str = "x", language: Language = Language.EN, now: datetime = NOW
) -> TurnContext:
    deps = orch(c, evidence=evidence)._deps
    session = SessionState(tenant_id=T, conversation_id=C, created_at=NOW, updated_at=NOW, language=language)
    ctx = TurnContext(
        deps=deps, tenant=c.tenants.get(T), session=session, text=text, now=now, request_id="r", trace_id="t"
    )
    ctx.plan = PlannedIntent("policy_question", "knowledge")
    return ctx


async def test_the_answer_quotes_the_passage_verbatim_with_its_citation(c: Container) -> None:
    reply = await say(orch(c, evidence=c.evidence), "How many days do I have to return an item?")
    assert reply.decision is Decision.ANSWER and reply.citations == ("return_policy@v2#s2",)
    assert c.evidence is not None
    passage = await c.evidence.get_passage(T, "return_policy@v2#s2")
    assert passage is not None and f"“{passage.text}” [return_policy@v2#s2]" in reply.text
    trace = await last_trace(c)
    assert trace.knowledge_answer and [e.citation for e in trace.evidence] == ["return_policy@v2#s2"]


async def test_a_superseded_policy_is_never_cited(c: Container) -> None:
    reply = await say(orch(c, evidence=c.evidence), RETURN_POLICY)
    assert reply.citations and all("return_policy@v1" not in x for x in reply.citations)


async def test_the_same_question_is_searched_once_within_a_minute(c: Container) -> None:
    search = CountingSearch(c.policy_search)
    first = await knowledge.answer(context(c, search, text=RETURN_POLICY))
    second = await knowledge.answer(context(c, search, text="  WHAT is your return policy?? "))  # same normalized query
    assert first.citations == second.citations and len(search.queries) == 1


async def test_the_cache_expires_after_sixty_seconds_and_is_per_query(c: Container) -> None:
    search = CountingSearch(c.policy_search)
    await knowledge.answer(context(c, search, text=RETURN_POLICY))
    await knowledge.answer(context(c, search, text=RETURN_POLICY, now=NOW + timedelta(seconds=59)))
    assert len(search.queries) == 1
    await knowledge.answer(context(c, search, text=RETURN_POLICY, now=NOW + timedelta(seconds=61)))
    assert len(search.queries) == 2
    await knowledge.answer(context(c, search, text="how much does shipping cost?"))
    assert len(search.queries) == 3


async def test_an_empty_answer_is_not_cached(c: Container) -> None:
    search = CountingSearch(c.policy_search)
    for _ in range(2):
        await knowledge.answer(context(c, search, text="Do you offer a five year warranty on electronics?"))
    assert len(search.queries) == 2


async def test_arabizi_gets_one_retry_with_normalized_text_when_the_first_search_is_empty(c: Container) -> None:
    arabizi, english = CountingSearch(c.policy_search), CountingSearch(c.policy_search)
    step = await knowledge.answer(context(c, arabizi, text="ZZZ Qqq!!!", language=Language.ARABIZI))
    assert step.decision is Decision.CLARIFY and arabizi.queries == ["ZZZ Qqq!!!", "zzz qqq"]
    await knowledge.answer(context(c, english, text="ZZZ Qqq!!!"))
    assert len(english.queries) == 1


async def test_quote_for_returns_passages_and_records_the_evidence(c: Container) -> None:
    ctx = context(c, c.evidence)
    passages = await knowledge.quote_for(ctx, "late delivery shipping")
    assert passages and [e.citation for e in ctx.evidence] == [p.citation for p in passages]


async def test_quote_citations_gives_the_stored_words_and_skips_unknown_or_superseded(c: Container) -> None:
    ctx = context(c, c.evidence)
    asked = ("return_policy@v2#s2", "return_policy@v1#s2", "nope@v1#s9", "return_policy@v2#s2")
    got = await knowledge.quote_citations(ctx, asked)
    assert [p.citation for p in got] == ["return_policy@v2#s2"]
    assert c.evidence is not None
    stored = await c.evidence.get_passage(T, "return_policy@v2#s2")
    assert stored is not None and got[0].text == stored.text


def test_a_policy_answer_trace_needs_evidence_and_citations() -> None:
    base: dict[str, Any] = {
        "trace_id": "t",
        "request_id": "r",
        "tenant_id": T,
        "conversation_id": C,
        "turn_index": 0,
        "decision": Decision.ANSWER,
        "knowledge_answer": True,
    }
    with pytest.raises(ValidationError, match="evidence"):
        DecisionTrace.model_validate(base)
    ref = {"citation": "a@v1#s1", "document_id": "a", "version": "v1", "score": 1.0}
    with pytest.raises(ValidationError, match="evidence"):
        DecisionTrace.model_validate({**base, "evidence": [ref]})
    with pytest.raises(ValidationError, match="evidence"):
        DecisionTrace.model_validate({**base, "response_citations": ["a@v1#s1"]})
    ok = DecisionTrace.model_validate({**base, "evidence": [ref], "response_citations": ["a@v1#s1"]})
    assert ok.knowledge_answer
    DecisionTrace.model_validate({**base, "knowledge_answer": False})  # other answers are not affected
