"""Integrity of the retrieval benchmarks' dev/test splits."""

import json

import pytest

from team_a.config import settings
from team_a.knowledge.index import build_passages

STYLES = {"ar", "en", "arabizi"}
TENANTS = ["shop_001", "noon_eg"]


def _load(split, tenant="shop_001"):
    path = settings.data_dir / "benchmark" / f"retrieval_{tenant}.{split}.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


@pytest.mark.parametrize("tenant", TENANTS)
def test_splits_are_disjoint(tenant):
    dev, test = _load("dev", tenant), _load("test", tenant)
    assert not {c["id"] for c in dev} & {c["id"] for c in test}
    assert not {c["question"] for c in dev} & {c["question"] for c in test}


@pytest.mark.parametrize("tenant", TENANTS)
@pytest.mark.parametrize("split", ["dev", "test"])
def test_every_style_is_represented_answerable_and_unanswerable(tenant, split):
    cases = _load(split, tenant)
    assert {c["style"] for c in cases if c["expected"]} == STYLES
    assert {c["style"] for c in cases if not c["expected"]} == STYLES


@pytest.mark.parametrize("split", ["dev", "test"])
def test_gold_citations_exist_in_current_corpus(keyword_index, split):
    for case in _load(split):
        for citation in case["expected"]:
            passage = keyword_index.by_citation.get(citation)
            assert passage and passage["current"], f"{case['id']}: {citation}"


@pytest.mark.parametrize("split", ["dev", "test"])
def test_noon_gold_citations_exist_in_current_corpus(split):
    current = {p["citation"] for p in build_passages("noon_eg")[0] if p["current"]}
    for case in _load(split, "noon_eg"):
        assert set(case["expected"]) <= current, case["id"]
