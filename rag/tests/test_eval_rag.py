"""Deterministic DeepEval scorers for expected policy sections. No Ollama, no API key."""

import pytest

from team_a.eval_rag import Sample, abstention, section_hit, summarize


def test_section_hit_any_gold_citation():
    assert section_hit(["return_policy@v2#s2"], ["faq@v1#q01", "return_policy@v2#s2"]) == 1.0
    assert section_hit(["return_policy@v2#s2"], ["shipping_policy@v1#s3"]) == 0.0


def test_section_hit_skips_unanswerable():
    assert section_hit([], []) is None
    assert section_hit([], ["faq@v1#q01"]) is None


def test_abstention_matches_answerability():
    assert abstention(["return_policy@v2#s2"], ["return_policy@v2#s2"]) == 1.0
    assert abstention(["return_policy@v2#s2"], []) == 0.0
    assert abstention([], []) == 1.0
    assert abstention([], ["faq@v1#q01"]) == 0.0


def test_summarize_splits_recall_from_unanswerable_gap():
    samples = [
        Sample(id="A", style="en", question="return window?", expected=["return_policy@v2#s2"],
               retrieved=["return_policy@v2#s2"]),
        Sample(id="B", style="ar", question="jobs?", expected=[], retrieved=["faq@v1#q01"]),
        Sample(id="C", style="arabizi", question="koshari?", expected=[], retrieved=[]),
    ]
    report = summarize(samples)
    assert report["questions"] == 3
    assert report["unanswerable"] == 2
    assert report["section_hit"] == 1.0
    assert report["per_style"] == {"en": "1/1"}
    assert report["abstention"] == pytest.approx(2 / 3)
    assert report["abstention_unanswerable"] == 0.5
    assert report["misses"][0][0] == "B"


def test_collect_samples_reads_gold_section_ids(keyword_index):
    from team_a.eval_rag import collect_samples

    samples = collect_samples("shop_001", "test", keyword_index, embedder=None, limit=4)
    assert [s.id for s in samples] == ["Q01", "Q03", "Q04", "Q05"]
    assert samples[0].expected == ["return_policy@v2#s2"]
    assert samples[0].gold_texts and samples[0].gold_texts[0].startswith("return_policy@v2#s2:")


def test_deepeval_metrics_consume_expected_section_ids():
    pytest.importorskip("deepeval")
    from team_a.eval_rag import Sample, as_test_case, abstention_metric, section_hit_metric

    hit_ok = Sample(id="Q01", style="en", question="How many days?", expected=["return_policy@v2#s2"],
                    retrieved=["return_policy@v2#s2"], retrieved_texts=["14 days"], gold_texts=["14 days"])
    empty_ok = Sample(id="Q46", style="en", question="laptops?", expected=[], retrieved=[],
                      retrieved_texts=[], gold_texts=[])
    leak = Sample(id="Q47", style="ar", question="وظائف?", expected=[], retrieved=["faq@v1#q01"],
                  retrieved_texts=["hours"], gold_texts=[])

    hit = section_hit_metric()
    assert hit.measure(as_test_case(hit_ok)) == 1.0
    assert hit.measure(as_test_case(empty_ok)) == 1.0

    absn = abstention_metric()
    assert absn.measure(as_test_case(empty_ok)) == 1.0
    assert absn.measure(as_test_case(leak)) == 0.0
