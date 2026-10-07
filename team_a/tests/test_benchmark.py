"""Integrity of the retrieval benchmark's dev/test split."""

import json

import pytest

from team_a.config import settings

STYLES = {"ar", "en", "arabizi"}


def _load(split):
    path = settings.data_dir / "benchmark" / f"retrieval_shop_001.{split}.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_splits_are_disjoint():
    dev, test = _load("dev"), _load("test")
    assert not {c["id"] for c in dev} & {c["id"] for c in test}
    assert not {c["question"] for c in dev} & {c["question"] for c in test}


@pytest.mark.parametrize("split", ["dev", "test"])
def test_every_style_is_represented_answerable_and_unanswerable(split):
    cases = _load(split)
    assert {c["style"] for c in cases if c["expected"]} == STYLES
    assert {c["style"] for c in cases if not c["expected"]} == STYLES


@pytest.mark.parametrize("split", ["dev", "test"])
def test_gold_citations_exist_in_current_corpus(keyword_index, split):
    for case in _load(split):
        for citation in case["expected"]:
            passage = keyword_index.by_citation.get(citation)
            assert passage and passage["current"], f"{case['id']}: {citation}"
