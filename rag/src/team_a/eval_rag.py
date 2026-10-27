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
- faithfulness (`--replies` / `--labels`): whether a logged (or synthetic) reply
  is supported by the cited passage texts, not by gold section IDs alone.

The judge is local Ollama by default, or Groq. OpenRouter is rejected.
Telemetry is off; nothing is sent to Confident AI.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")

from team_a.config import ROOT, settings
from team_a.evaluation import CachedEmbedder, _load_jsonl
from team_a.knowledge.index import TenantIndex
from team_a.knowledge.retrieval import search_knowledge, tenant_min_top_score, tenant_thresholds
from team_a.schemas import SearchKnowledgeRequest


GROQ_URL = "https://api.groq.com/openai/v1"
DIAGNOSIS_DIR = ROOT.parent / "Docs" / "output" / "eval_runs" / "diagnosis"
LABELS_FILE = ROOT / "eval" / "faithfulness_labels.jsonl"
STYLE_NOTE = (
    "The customer question may be English, Egyptian Arabic (العامية المصرية), "
    "or Arabizi (Egyptian Arabic in Latin letters and digits, e.g. 3ayez araga3). "
    "Judge the meaning, not the script."
)


@dataclass
class Sample:
    id: str
    style: str
    question: str
    expected: list[str]
    retrieved: list[str]
    retrieved_texts: list[str] = field(default_factory=list)
    gold_texts: list[str] = field(default_factory=list)
    reply: str = ""
    label: float | None = None
    label_reason: str = ""


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

    output = sample.reply or (", ".join(sample.retrieved) or "NO_EVIDENCE")
    return LLMTestCase(
        input=sample.question,
        actual_output=output,
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


def _passage_texts(index: TenantIndex, citations: list[str]) -> list[str]:
    texts = []
    for citation in citations:
        passage = index.by_citation.get(citation)
        if passage:
            texts.append(f"{citation}: {passage['text']}")
    return texts


def relevancy_template():
    from deepeval.metrics.contextual_relevancy import ContextualRelevancyTemplate

    class TicketEasyRelevancy(ContextualRelevancyTemplate):
        @staticmethod
        def generate_verdicts(input: str, context):
            if isinstance(context, (list, tuple)):
                context = "\n\n".join(str(c) for c in context)
            return f"""{STYLE_NOTE}

For each statement in the context, say whether it is relevant to answering the question.
Return JSON: {{"verdicts": [{{"verdict": "yes"|"no", "statement": "..."}}]}}

Question:
{input}

Context:
{context}

JSON:
"""

    return TicketEasyRelevancy


def faithfulness_template():
    from deepeval.metrics.faithfulness import FaithfulnessTemplate

    class TicketEasyFaithfulness(FaithfulnessTemplate):
        @staticmethod
        def generate_verdicts(**kwargs):
            claims = kwargs.get("claims", "")
            if isinstance(claims, (list, tuple)):
                claims = "\n".join(f"- {c}" for c in claims)
            retrieval_context = kwargs.get("retrieval_context", "")
            if isinstance(retrieval_context, (list, tuple)):
                retrieval_context = "\n\n".join(str(c) for c in retrieval_context)
            return f"""{STYLE_NOTE}

For each claim taken from the assistant reply, say whether it is supported by the retrieval context.
A paraphrase in Egyptian Arabic or Arabizi of a fact in the context is supported.
A number, deadline, fee, or cap that is not in the context is a contradiction.
Return JSON: {{"verdicts": [{{"verdict": "yes"|"no"|"borderline", "reason": "..."}}]}}

Retrieval context:
{retrieval_context}

Claims:
{claims}

JSON:
"""

    return TicketEasyFaithfulness


def _judge(kind: str = "ollama"):
    """Judge is Groq or local Ollama only — no OpenRouter."""
    kind = (kind or os.getenv("TEAM_A_RAG_EVAL_JUDGE") or "ollama").strip().lower()
    if kind not in ("ollama", "groq"):
        raise SystemExit("judge must be ollama or groq")
    name = os.getenv("TEAM_A_RAG_EVAL_MODEL", "").strip()
    base = os.getenv("TEAM_A_RAG_EVAL_BASE_URL", "").strip()
    key = (os.getenv("TEAM_A_RAG_EVAL_API_KEY") or os.getenv("GROQ_API_KEY")
           or os.getenv("GROQ_KEY_1") or "").strip()
    if "openrouter" in base.lower() or "openrouter" in name.lower():
        raise SystemExit("judges are Groq or local Ollama only")
    if kind == "groq":
        if not key:
            raise SystemExit("Groq judge needs TEAM_A_RAG_EVAL_API_KEY, GROQ_API_KEY, or GROQ_KEY_1")
        from deepeval.models import LocalModel
        from deepeval.models.llms.utils import trim_and_load_json

        class TicketEasyGroq(LocalModel):
            """gpt-oss often parks JSON in reasoning; require json_object and read both."""

            def generate(self, prompt, schema=None, audio=None):
                from deepeval.models.llms.multimodal import to_openai_content
                content = to_openai_content(prompt, audio, text_only_as_string=True,
                                            inline_remote_images=True)
                client = self.load_model(async_mode=False)
                kwargs = {
                    "model": self.name,
                    "messages": [{"role": "user", "content": content}],
                    "temperature": self.temperature,
                    "response_format": {"type": "json_object"},
                    **self.generation_kwargs,
                }
                if "gpt-oss" in (self.name or "") or "qwen" in (self.name or "").lower():
                    kwargs["extra_body"] = {"reasoning_effort": "low" if "gpt-oss" in self.name else "none"}
                import time
                response = None
                for attempt in range(6):
                    try:
                        response = client.chat.completions.create(**kwargs)
                        break
                    except Exception as exc:
                        if "429" not in str(exc) or attempt == 5:
                            raise
                        time.sleep(20 * (attempt + 1))
                if response is None:
                    raise RuntimeError("Groq returned no response")
                message = response.choices[0].message
                text = (message.content or "").strip()
                if not text:
                    text = (getattr(message, "reasoning", None) or "").strip()
                if schema is not None:
                    return schema.model_validate(trim_and_load_json(text)), 0.0
                return text, 0.0

            async def a_generate(self, prompt, schema=None, audio=None):
                return self.generate(prompt, schema=schema, audio=audio)

        return TicketEasyGroq(model=name or "qwen/qwen3.8-27b", base_url=base or GROQ_URL,
                              api_key=key, temperature=0, format="json")
    from deepeval.models import OllamaModel

    class TicketEasyOllama(OllamaModel):
        """qwen3 thinking must be off or DeepEval cannot parse the JSON verdicts."""

        def generate(self, prompt, schema=None, audio=None):
            chat_model = self.load_model()
            options = {"temperature": self.temperature, **self.generation_kwargs}
            think = options.pop("think", False)
            response = chat_model.chat(
                model=self.name,
                messages=self._prompt_messages(prompt, audio),
                format=schema.model_json_schema() if schema else None,
                options=options,
                think=think,
            )
            content = response.message.content or ""
            if schema is not None:
                return schema.model_validate_json(content), 0.0
            return content, 0.0

        async def a_generate(self, prompt, schema=None, audio=None):
            return self.generate(prompt, schema=schema, audio=audio)

    return TicketEasyOllama(model=name or "qwen3:8b", base_url=base or settings.ollama_url,
                            temperature=0, generation_kwargs={"think": False})


def _score_metric(name: str, samples: list[Sample], judge: str) -> dict:
    from deepeval.metrics import ContextualRelevancyMetric, FaithfulnessMetric

    model = _judge(judge)
    reasons = any(s.label is not None for s in samples)
    if name == "relevancy":
        def make():
            return ContextualRelevancyMetric(threshold=0.5, model=model, include_reason=reasons,
                                             async_mode=False, evaluation_template=relevancy_template())
        need = lambda s: bool(s.retrieved_texts)  # noqa: E731
    elif name == "faithfulness":
        def make():
            return FaithfulnessMetric(threshold=0.5, model=model, include_reason=reasons,
                                     async_mode=False, evaluation_template=faithfulness_template())
        need = lambda s: bool(s.reply and s.retrieved_texts)  # noqa: E731
    else:
        raise ValueError(name)

    scored, unanswerable_hits = [], []
    for sample in samples:
        if not need(sample):
            continue
        metric = make()
        try:
            metric.measure(as_test_case(sample))
            score = metric.score
            reason = getattr(metric, "reason", "") or ""
        except Exception as exc:
            score, reason = None, str(exc)
        row = {"id": sample.id, "style": sample.style, "answerable": bool(sample.expected),
               "score": score, "reason": reason, "label": sample.label}
        if sample.label is not None and score is not None:
            pred = 1.0 if score >= 0.5 else 0.0
            row["agree"] = pred == sample.label
        scored.append(row)
        if not sample.expected and score is not None:
            unanswerable_hits.append(score)
        mark = "" if sample.label is None else f"  gold={sample.label:g}"
        shown = "n/a" if score is None else f"{score:.2f}"
        extra = f"  {reason[:80]}" if score is None and reason else ""
        print(f"  {name:12} {sample.id:12} {shown}{mark}  {sample.question[:50]}{extra}")
    labelled = [r for r in scored if r.get("label") is not None]
    per_style: dict[str, list[bool]] = defaultdict(list)
    for r in labelled:
        if "agree" in r:
            per_style[r["style"]].append(r["agree"])
    return {
        name: _mean([r["score"] for r in scored if r["score"] is not None]),
        f"{name}_unanswerable_hits": _mean(unanswerable_hits),
        f"{name}_n": len(scored),
        f"{name}_rows": scored,
        "agreement": _mean([float(r["agree"]) for r in labelled if "agree" in r]),
        "agreement_n": len(labelled),
        "agreement_per_style": {s: f"{sum(v)}/{len(v)}" for s, v in sorted(per_style.items())},
        "judge": judge,
    }


def load_diagnosis(path: Path) -> dict[str, dict]:
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("conversation"):
            rows[row["conversation"]] = row
    return rows


def load_reply_file(path: Path, index: TenantIndex, limit: int | None = None) -> list[Sample]:
    samples = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        citations = list(row.get("citations") or [])
        if not row.get("reply") or not citations:
            continue
        samples.append(Sample(
            id=row.get("conversation") or row.get("id"),
            style=row.get("style") or "en",
            question=row.get("said") or row.get("question") or "",
            expected=list((row.get("gold") or {}).get("citations_any") or citations),
            retrieved=citations,
            retrieved_texts=_passage_texts(index, citations),
            reply=row["reply"],
        ))
        if limit and len(samples) >= limit:
            break
    return samples


def load_labels(path: Path, index: TenantIndex) -> list[Sample]:
    samples = []
    cache: dict[Path, dict[str, dict]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        row = json.loads(line)
        if row.get("source") and row.get("conversation"):
            src = DIAGNOSIS_DIR / f"{row['source']}.jsonl"
            if src not in cache:
                cache[src] = load_diagnosis(src)
            log = cache[src][row["conversation"]]
            citations = list(log.get("citations") or row.get("citations") or [])
            samples.append(Sample(
                id=row["id"], style=log.get("style") or row.get("style") or "en",
                question=log["said"], expected=list((log.get("gold") or {}).get("citations_any") or citations),
                retrieved=citations, retrieved_texts=_passage_texts(index, citations),
                reply=log["reply"], label=float(row["label"]), label_reason=row.get("reason") or "",
            ))
            continue
        citations = list(row["citations"])
        samples.append(Sample(
            id=row["id"], style=row["style"], question=row["question"], expected=citations,
            retrieved=citations, retrieved_texts=_passage_texts(index, citations),
            reply=row["reply"], label=float(row["label"]), label_reason=row.get("reason") or "",
        ))
    return samples


def _write_report(report: dict, out: Path | None) -> None:
    if not out:
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    dump = {k: v for k, v in report.items() if k != "misses"}
    dump["misses"] = [list(m) for m in report.get("misses") or []]
    out.write_text(json.dumps(dump, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")


def eval_rag(tenant_id: str, split: str = "test", llm: bool = False,
             keyword: bool = False, limit: int | None = None,
             metric: str = "relevancy", replies: Path | None = None,
             labels: Path | None = None, judge: str = "ollama",
             out: Path | None = None) -> dict:
    if split not in ("dev", "test"):
        raise ValueError("split must be dev or test")
    judged = llm or metric in ("relevancy", "faithfulness") and (replies or labels or llm)
    if judged or labels or replies:
        try:
            import deepeval  # noqa: F401
        except ImportError:
            raise SystemExit("DeepEval is not installed. From rag/: pip install -r requirements-eval.txt")

    index = TenantIndex.load(tenant_id)
    if labels:
        samples = load_labels(labels, index)
        report = {"questions": len(samples), "tenant": tenant_id, "split": "labels"}
        print(f"DeepEval labels  {len(samples)} rows from {labels.name}  judge={judge}")
        report.update(_score_metric("faithfulness", samples, judge))
        agr = report.get("agreement")
        print(f"faithfulness agreement vs labels     {agr:.2%} ({report['agreement_n']})"
              if agr is not None else "agreement  n/a")
        print(f"  per style: {report.get('agreement_per_style')}")
        _write_report(report, out)
        return report

    if replies:
        samples = load_reply_file(replies, index, limit=limit)
        report = {"questions": len(samples), "tenant": tenant_id, "replies": str(replies)}
        print(f"DeepEval faithfulness  {len(samples)} logged replies  judge={judge}")
        report.update(_score_metric("faithfulness", samples, judge))
        val = report.get("faithfulness")
        print(f"faithfulness                          {val:.2%} on {report['faithfulness_n']}"
              if val is not None else "faithfulness  n/a")
        _write_report(report, out)
        return report

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
    if llm and metric in ("relevancy", "both"):
        report.update(_score_metric("relevancy", samples, judge))
        rel = report.get("relevancy")
        rel_u = report.get("relevancy_unanswerable_hits")
        print(f"contextual relevancy                  {rel:.2%} on {report['relevancy_n']} retrieved"
              if rel is not None else "contextual relevancy  n/a (nothing retrieved)")
        if rel_u is not None:
            print(f"  on unanswerable hits (want low)     {rel_u:.2%}")
    elif not llm:
        print("contextual relevancy                  skipped (pass --llm)")
    for miss in report["misses"]:
        print("  MISS", *miss)
    _write_report(report, out)
    return report
