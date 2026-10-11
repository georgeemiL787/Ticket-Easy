"""Policy search stand-in: right passage first in every language style, no_match for the unrelated, never v1."""

import json
from pathlib import Path

import pytest

from team_b.adapters.standins.policy_search import PolicySearchStandin
from team_b.config import PROJECT_ROOT
from team_b.container import Container
from team_b.contracts.errors import UpstreamError
from tests.search_questions import HELD, NEGATIVE, Q

T = "shop_001"
RETURN_WINDOW = "return_policy@v2#s2"


@pytest.fixture
def search(container: Container) -> PolicySearchStandin:
    assert container.policy_search is not None
    return container.policy_search


@pytest.mark.parametrize(
    ("qid", "style", "question"),
    [(q[0], q[1], q[2]) for q in Q if q[0] in {"S01", "S02", "S17"}],
    ids=["S01-english", "S02-egyptian-arabic", "S17-arabizi"],
)
async def test_the_return_window_question_finds_return_policy_v2_s2_first(
    search: PolicySearchStandin, qid: str, style: str, question: str
) -> None:
    result = await search.search_knowledge(T, question, request_id="r1")
    assert result.empty_reason is None
    assert result.passages[0].passage_id == RETURN_WINDOW, (qid, style)
    assert result.passages[0].version == "v2" and result.retrieval_mode == "keyword_only"


@pytest.mark.parametrize(("qid", "style", "question", "accepted"), Q + HELD, ids=[q[0] for q in Q + HELD])
async def test_sample_questions_hit_the_right_passage_first(
    search: PolicySearchStandin, qid: str, style: str, question: str, accepted: list[str]
) -> None:
    result = await search.search_knowledge(T, question, request_id="r")
    assert result.passages, f"{qid} ({style}) found nothing"
    assert result.passages[0].passage_id in accepted, f"{qid} ({style}): {[p.passage_id for p in result.passages[:3]]}"


@pytest.mark.parametrize(("qid", "style", "question"), NEGATIVE[:8], ids=[n[0] for n in NEGATIVE[:8]])
async def test_unrelated_questions_return_no_match(
    search: PolicySearchStandin, qid: str, style: str, question: str
) -> None:
    result = await search.search_knowledge(T, question, request_id="r")
    assert result.passages == () and result.empty_reason == "no_match", (qid, style)


@pytest.mark.parametrize("question", ["", "   ", "???", "the of a"])
async def test_empty_or_meaningless_questions_return_no_match(search: PolicySearchStandin, question: str) -> None:
    result = await search.search_knowledge(T, question, request_id="r")
    assert result.passages == () and result.empty_reason == "no_match"


async def test_the_old_return_policy_is_never_returned(search: PolicySearchStandin) -> None:
    traps = [
        "return window 30 days", "old return policy", "استرجاع خلال 30 يوم", "return policy scope", "شروط الاسترجاع",
        "return conditions unused original condition", "mudet el estergaa3 30 yom", "نطاق السياسة",
    ] + [q[2] for q in Q + HELD]  # fmt: skip
    for question in traps:
        result = await search.search_knowledge(T, question, request_id="r", top_k=20)
        assert not any(p.passage_id.startswith("return_policy@v1") for p in result.passages), question
        assert all(p.version != "v1" or p.document_id != "return_policy" for p in result.passages)


async def test_the_thirty_day_question_gets_the_current_fourteen_day_answer(search: PolicySearchStandin) -> None:
    result = await search.search_knowledge(T, "can I return an item within 30 days?", request_id="r")
    assert result.passages[0].passage_id == RETURN_WINDOW and "14" in result.passages[0].text


async def test_top_k_scores_and_exact_quotes(search: PolicySearchStandin) -> None:
    result = await search.search_knowledge(T, "return an item", request_id="r", top_k=3)
    assert 1 <= len(result.passages) <= 3
    scores = [p.score for p in result.passages]
    assert scores == sorted(scores, reverse=True) and all(s > 0 for s in scores)
    fixtures = json.loads((PROJECT_ROOT / "fixtures" / T / "policies.json").read_text(encoding="utf-8"))
    by_id = {p["passage_id"]: p for p in fixtures["passages"]}
    for passage in result.passages:  # quoted exactly as written, never reworded
        assert passage.text == by_id[passage.passage_id]["text"]
    assert result.request_id == "r" and result.tenant_id == T and result.query == "return an item"


async def test_get_passage(search: PolicySearchStandin) -> None:
    passage = await search.get_passage(T, RETURN_WINDOW)
    assert passage is not None and passage.passage_id == RETURN_WINDOW and "14" in passage.text
    assert await search.get_passage(T, "return_policy@v1#s2") is None  # superseded: never handed out
    assert await search.get_passage(T, "nope@v1#s1") is None


async def test_unknown_tenant_is_an_upstream_error(search: PolicySearchStandin, tmp_path: Path) -> None:
    with pytest.raises(UpstreamError) as info:
        await search.search_knowledge("nope_001", "return", request_id="r")
    assert (info.value.service, info.value.code, info.value.retryable) == ("policy_search", "TENANT_NOT_FOUND", False)
