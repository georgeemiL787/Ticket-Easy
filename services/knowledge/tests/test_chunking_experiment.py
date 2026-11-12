"""The chunking experiment script (scripts/chunking_experiment.py): chunkers stay in bounds and the structure strategy
scores exactly what evaluation.run_retrieval reports."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import chunking_experiment as ce  # noqa: E402

TEXT = ("Title\nIntro line.\n\n1. Returns\nYou can return an item within 14 days. It must be unused.\n\n"
        "2. Refunds\nRefunds go back to the card in 7 days. Cash on delivery refunds go to the wallet.\n\n"
        "3. Shipping\nOrders ship in 2 days. Remote areas take longer, up to 5 days.")


def _covers_all(text, spans):
    marks = [False] * len(text)
    for s, e in spans:
        for i in range(s, e):
            marks[i] = True
    return all(m or text[i].isspace() for i, m in enumerate(marks))


@pytest.mark.parametrize("size", [40, 80, 160])
def test_fixed_and_recursive_spans_fit_and_cover_the_text(size):
    for spans in (ce.fixed_spans(TEXT, size), ce.recursive_spans(TEXT, size)):
        assert spans and all(0 <= s < e <= len(TEXT) and e - s <= size for s, e in spans)
        assert _covers_all(TEXT, spans)


def test_recursive_prefers_paragraph_boundaries():
    spans = ce.recursive_spans(TEXT, 120)
    assert any(TEXT[s:e].startswith("2. Refunds") for s, e in spans)


def test_semantic_spans_are_contiguous_sentences():
    spans = ce.semantic_spans(TEXT, 80)
    assert spans == sorted(spans) and _covers_all(TEXT, spans)
    assert all(e1 <= s2 for (_, e1), (s2, _) in zip(spans, spans[1:]))


def test_score_question_metrics():
    index = SimpleNamespace(by_citation={
        "d@v1#c0": {"_span": ("d", "v1", 0, 100)},     # unrelated
        "d@v1#c1": {"_span": ("d", "v1", 100, 150)},   # half of the gold section
        "d@v1#c2": {"_span": ("d", "v1", 150, 200)},   # the other half
    })
    result = SimpleNamespace(passages=[SimpleNamespace(citation=c) for c in ("d@v1#c0", "d@v1#c1", "d@v1#c2")])
    case = {"id": "q", "style": "en", "expected": ["d@v1#s1"]}
    row = ce.score_question(case, result, index, {"d@v1#s1": ("d", "v1", 100, 200)})
    assert row["hit"] and row["first_relevant_rank"] == 2
    assert row["context_recall"] == pytest.approx(1.0)
    assert row["context_precision"] == pytest.approx((1 / 2 + 2 / 3) / 2)
    assert row["char_precision"] == pytest.approx(100 / 200)
    budget = ce.score_question(case, result, index, {"d@v1#s1": ("d", "v1", 100, 200)}, budget=125)
    assert budget["returned_chars"] == 125 and budget["context_recall"] == pytest.approx(0.25)


def test_structure_strategy_matches_run_retrieval():
    docs, records = ce.load_docs("shop_001")
    gold = {c: (d.document_id, d.version, s, e) for d in docs for c, s, e in d.sections}
    index, _ = ce.build("shop_001", "structure", None, docs, records, None)
    min_cos, min_bm25 = ce.tenant_thresholds("shop_001")
    official = ce.check_against_production("shop_001", records, None, min_cos, min_bm25, 0.0)
    for split in ce.SPLITS:
        cases = ce._load_jsonl(ce.settings.benchmark_file("shop_001", f"retrieval.{split}"))
        summary, _ = ce.evaluate(cases, index, None, gold, min_cos, min_bm25, 0.0)
        assert summary["hit_at_5"] == pytest.approx(official[split]["recall_at_k"])
        assert summary["no_answer_recall"] == pytest.approx(official[split]["no_answer_recall"])
        assert summary["per_style"] == official[split]["per_style"]
