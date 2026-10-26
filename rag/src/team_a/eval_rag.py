"""DeepEval harness for the retrieval benchmark (expected policy sections per question).

`eval-retrieval` already reports recall@5 and no-answer precision/recall. This command
scores the same jsonl with DeepEval so the new metrics cover the gaps instead of
repeating that suite:

- section_hit: any gold citation in the top-k. Same number as recall@5; kept so a
  DeepEval run sits next to the existing figure.
- abstention: retrieval is empty iff the question is unanswerable. That is the
  no-answer gap (off-topic questions that share words with the corpus).
- contextual relevancy (optional, `--llm`): of the passages we did return, how many
  actually address the question. Diagnoses word-overlap hits on unanswerable queries.

The judge is local Ollama by default, or any OpenAI-compatible free-tier endpoint.
Telemetry is off; nothing is sent to Confident AI.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

from team_a.config import settings
from team_a.evaluation import CachedEmbedder, _load_jsonl
from team_a.knowledge.index import TenantIndex
from team_a.knowledge.retrieval import search_knowledge, tenant_min_top_score, tenant_thresholds
from team_a.schemas import SearchKnowledgeRequest


@dataclass
class Sample:
    id: str
    style: str
    question: str
    expected: list[str]
    retrieved: list[str]
    retrieved_texts: list[str] = field(default_factory=list)
    gold_texts: list[str] = field(default_factory=list)


def section_hit(expected: list[str], retrieved: list[str]) -> float | None:
    """1 if any gold citation is in the retrieved list. Unanswerable questions are skipped."""
    if not expected:
        return None
    return float(any(c in retrieved for c in expected))


def abstention(expected: list[str], retrieved: list[str]) -> float:
    """1 if we retrieved something exactly when the question is answerable."""
    return float(bool(expected) == bool(retrieved))


def collect_samples(tenant_id: str, split: str, index: TenantIndex, embedder,
                    limit: int | None = None) -> list[Sample]:
    cases = _load_jsonl(settings.benchmark_file(tenant_id, f"retrieval.{split}"))
    if limit:
        cases = cases[:limit]
    min_cosine, min_bm25 = tenant_thresholds(tenant_id)
    min_top = tenant_min_top_score(tenant_id)
    samples = []
    for case in cases:
        req = SearchKnowledgeRequest(request_id=case["id"], tenant_id=index.tenant_id,
                                     query=case["question"], top_k=5)
        result = search_knowledge(req, index, embedder, min_cosine=min_cosine, min_bm25=min_bm25,
                                  min_top_score=min_top)
        expected = list(case["expected"])
        gold_texts = []
        for citation in expected:
            passage = index.by_citation.get(citation)
            if passage:
                gold_texts.append(f"{citation}: {passage['text']}")
        samples.append(Sample(
            id=case["id"], style=case["style"], question=case["question"], expected=expected,
            retrieved=[p.citation for p in result.passages],
            retrieved_texts=[f"{p.citation}: {p.text}" for p in result.passages],
            gold_texts=gold_texts,
        ))
    return samples


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def summarize(samples: list[Sample]) -> dict:
    hits, hits_style = [], defaultdict(list)
    abst, abst_unans = [], []
    misses = []
    for s in samples:
        hit = section_hit(s.expected, s.retrieved)
        if hit is not None:
            hits.append(hit)
            hits_style[s.style].append(hit)
            if not hit:
                misses.append((s.id, s.question, s.retrieved[:2]))
        a = abstention(s.expected, s.retrieved)
        abst.append(a)
        if not s.expected:
            abst_unans.append(a)
            if s.retrieved:
                misses.append((s.id, "should be empty", s.retrieved[:2]))
    per_style = {style: f"{int(sum(v))}/{len(v)}" for style, v in sorted(hits_style.items())}
    return {
        "questions": len(samples),
        "unanswerable": sum(1 for s in samples if not s.expected),
        "section_hit": _mean(hits),
        "per_style": per_style,
        "abstention": _mean(abst),
        "abstention_unanswerable": _mean(abst_unans),
        "misses": misses,
    }


def as_test_case(sample: Sample):
    from deepeval.test_case import LLMTestCase

    return LLMTestCase(
        input=sample.question,
        actual_output=", ".join(sample.retrieved) or "NO_EVIDENCE",
        expected_output=", ".join(sample.expected) or "NO_EVIDENCE",
        retrieval_context=sample.retrieved_texts or ["NO_EVIDENCE"],
        context=sample.gold_texts or ["NO_EVIDENCE"],
        metadata={
            "id": sample.id, "style": sample.style,
            "expected": sample.expected, "retrieved": sample.retrieved,
        },
    )


def section_hit_metric():
    from deepeval.metrics import BaseMetric

    class SectionHitMetric(BaseMetric):
        def __init__(self, threshold: float = 1.0):
            self.threshold = threshold
            self.async_mode = False

        def measure(self, test_case) -> float:
            meta = test_case.metadata or {}
            score = section_hit(meta.get("expected") or [], meta.get("retrieved") or [])
            self.score = 1.0 if score is None else score
            self.success = self.score >= self.threshold
            self.reason = f"expected {meta.get('expected')} got {meta.get('retrieved')}"
            return self.score

        async def a_measure(self, test_case) -> float:
            return self.measure(test_case)

        def is_successful(self) -> bool:
            return bool(self.success)

        @property
        def __name__(self):
            return "Section Hit"

    return SectionHitMetric()


def abstention_metric():
    from deepeval.metrics import BaseMetric

    class AbstentionMetric(BaseMetric):
        def __init__(self, threshold: float = 1.0):
            self.threshold = threshold
            self.async_mode = False

        def measure(self, test_case) -> float:
            meta = test_case.metadata or {}
            self.score = abstention(meta.get("expected") or [], meta.get("retrieved") or [])
            self.success = self.score >= self.threshold
            wanted = "evidence" if meta.get("expected") else "empty"
            got = "evidence" if meta.get("retrieved") else "empty"
            self.reason = f"wanted {wanted}, retrieved {got}"
            return self.score

        async def a_measure(self, test_case) -> float:
            return self.measure(test_case)

        def is_successful(self) -> bool:
            return bool(self.success)

        @property
        def __name__(self):
            return "Abstention"

    return AbstentionMetric()


def _judge():
    name = os.getenv("TEAM_A_RAG_EVAL_MODEL", "").strip()
    if not name:
        raise SystemExit("--llm needs TEAM_A_RAG_EVAL_MODEL (an Ollama tag, or a Groq/OpenRouter model id)")
    base = os.getenv("TEAM_A_RAG_EVAL_BASE_URL", "").strip()
    key = os.getenv("TEAM_A_RAG_EVAL_API_KEY", "").strip()
    ollama = (not base) or "11434" in base or "ollama" in base.lower()
    if ollama:
        from deepeval.models import OllamaModel
        return OllamaModel(model=name, base_url=base or settings.ollama_url, temperature=0)
    from deepeval.models import LocalModel
    return LocalModel(model=name, base_url=base, api_key=key or "local", temperature=0, format="json")


def score_relevancy(samples: list[Sample]) -> dict:
    from deepeval.metrics import ContextualRelevancyMetric

    metric_model = _judge()
    scored, unanswerable_hits = [], []
    for sample in samples:
        if not sample.retrieved_texts:
            continue
        metric = ContextualRelevancyMetric(threshold=0.5, model=metric_model, include_reason=True)
        metric.measure(as_test_case(sample))
        row = {"id": sample.id, "style": sample.style, "answerable": bool(sample.expected),
               "score": metric.score, "reason": getattr(metric, "reason", "")}
        scored.append(row)
        if not sample.expected:
            unanswerable_hits.append(metric.score)
        print(f"  relevancy {sample.id:8} {metric.score:.2f}  {sample.question[:60]}")
    return {
        "contextual_relevancy": _mean([r["score"] for r in scored if r["score"] is not None]),
        "contextual_relevancy_unanswerable_hits": _mean(unanswerable_hits),
        "relevancy_n": len(scored),
        "relevancy_rows": scored,
    }


def eval_rag(tenant_id: str, split: str = "test", llm: bool = False,
             keyword: bool = False, limit: int | None = None) -> dict:
    if split not in ("dev", "test"):
        raise ValueError("split must be dev or test")
    if llm:
        try:
            import deepeval  # noqa: F401
        except ImportError:
            raise SystemExit("DeepEval is not installed. From rag/: pip install -r requirements-eval.txt")

    index = TenantIndex.load(tenant_id)
    if keyword:
        embedder = None
    else:
        from team_a.knowledge.embeddings import OllamaEmbedder
        embedder = CachedEmbedder(OllamaEmbedder())
    samples = collect_samples(tenant_id, split, index, embedder, limit=limit)
    report = summarize(samples)
    report.update(tenant=tenant_id, split=split, retrieval_mode="keyword_only" if keyword else "hybrid")
    unans = report["unanswerable"]
    print(f"DeepEval RAG eval  tenant={tenant_id}  split={split}  "
          f"{report['questions']} questions ({unans} unanswerable)  mode={report['retrieval_mode']}")
    hit = report["section_hit"]
    print(f"section_hit (answerable, = recall@5)  {hit:.2%}  per style: {report['per_style']}"
          if hit is not None else "section_hit  n/a")
    abst = report["abstention"]
    abst_u = report["abstention_unanswerable"]
    print(f"abstention (empty iff unanswerable)   {abst:.2%}" if abst is not None else "abstention  n/a")
    print(f"  unanswerable subset (= no-answer recall) {abst_u:.2%}"
          if abst_u is not None else "  unanswerable subset  n/a")
    if llm:
        report.update(score_relevancy(samples))
        rel = report.get("contextual_relevancy")
        rel_u = report.get("contextual_relevancy_unanswerable_hits")
        print(f"contextual relevancy                  {rel:.2%} on {report['relevancy_n']} retrieved"
              if rel is not None else "contextual relevancy  n/a (nothing retrieved)")
        if rel_u is not None:
            print(f"  on unanswerable hits (want low)     {rel_u:.2%}")
    else:
        print("contextual relevancy                  skipped (pass --llm and set TEAM_A_RAG_EVAL_MODEL)")
    for miss in report["misses"]:
        print("  MISS", *miss)
    return report
