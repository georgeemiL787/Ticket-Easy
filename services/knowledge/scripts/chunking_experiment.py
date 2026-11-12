"""Controlled chunking experiment on the retrieval benchmarks (evaluation only; production indexing is unchanged).

Every strategy gets the same documents, the same retrieval code (`search_knowledge`), the same tokenizer, query
expansion, thresholds, top_k=5 and the same benchmark questions. Only the chunk boundaries change.

Strategies
  structure      production chunking: one passage per heading / FAQ row (index.build_passages, 1500-char paragraph cap)
  fixed-N        fixed windows of N characters with 15% overlap, cut anywhere
  recursive-N    LangChain-style recursive splitter (paragraph > line > sentence > word > character), N chars, 15% overlap
  semantic-lex-P TextTiling-style breakpoints: adjacent-sentence lexical (TF-IDF) distance above the P-th percentile
  semantic-emb-P the same breakpoints on embedding distance (LangChain SemanticChunker method); needs --embed
  structure+recursive-N  structure sections, and a section longer than N is split recursively inside the section

The gold labels are section citations, so a chunk is matched to gold by character overlap with the gold section's
span in the document stream (the parsed sections, each as "title\\ntext", joined by blank lines):
  relevant chunk  = holds >= 50% of a gold section, or >= 50% of the chunk is gold text
  hit@5           = any relevant chunk in the top 5 (equals the existing recall@5 for the structure strategy)
  context recall  = best share of one gold section's characters covered by the top 5 (gold citations are alternatives)
  context precision = rank-aware precision of relevant chunks in the top 5 (the RAGAS formula, reference-based)
  char precision  = share of returned characters that are gold text
Unanswerable questions feed no-answer precision/recall as in evaluation.run_retrieval.

Each strategy is scored three ways on the test split: with the shipped thresholds and top 5; with a keyword threshold
re-picked on dev for that strategy; and with an equal context budget (ranked chunks until BUDGET_CHARS characters), so a
strategy with large chunks cannot win by returning most of a small corpus.

Without --embed retrieval is keyword-only (the service's own fallback mode); with --embed it is hybrid through Ollama.

  PYTHONPATH=src python scripts/chunking_experiment.py [--embed] [--out ../../docs/reports/eval_runs/chunking]
"""

import argparse
import json
import platform
import re
import statistics
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from knowledge_service.config import settings
from knowledge_service.evaluation import SWEEP_BM25, CachedEmbedder, _load_jsonl, run_retrieval
from knowledge_service.schemas import SearchKnowledgeRequest
from knowledge_service.search.bm25 import BM25
from knowledge_service.search.index import TenantIndex, build_passages, passage_embed_text
from knowledge_service.search.retrieval import search_knowledge, tenant_min_top_score, tenant_thresholds
from knowledge_service.text import detect_language, tokenize

TENANTS = ("shop_001", "noon_eg", "telco_001")
SPLITS = ("dev", "test")
TOP_K = 5
BUDGET_CHARS = 1500  # the equal-context comparison: ranked chunks until this many characters (about 400-500 tokens)
BUDGET_TOP_K = 20  # the most search_knowledge returns
OVERLAP = 0.15
SIZES = (256, 512, 1024)
SEPARATORS = ("\n\n", "\n", ". ", "؟ ", "? ", "، ", ", ", " ", "")
_SENTENCE_END = re.compile(r"(?<=[.!?؟])\s+|\n+")


@dataclass
class Doc:
    document_id: str
    version: str
    current: bool
    effective_date: str | None
    text: str
    sections: list[tuple[str, int, int]]  # (citation, start, end) of each production section in `text`


def load_docs(tenant_id: str) -> tuple[list[Doc], list[dict]]:
    """The production passages and, per document, the stream every other strategy chunks."""
    records, _ = build_passages(tenant_id)
    docs: dict[tuple[str, str], Doc] = {}
    for r in records:
        key = (r["document_id"], r["version"])
        doc = docs.setdefault(key, Doc(r["document_id"], r["version"], r["current"], r["effective_date"], "", []))
        block = f"{r['section']}\n{r['text']}" if r["section"] != r["text"] else r["text"]
        if doc.text:
            doc.text += "\n\n"
        start = len(doc.text)
        doc.text += block
        doc.sections.append((r["citation"], start, len(doc.text)))
    return list(docs.values()), records


# ---- chunkers: each returns (start, end) spans into doc.text ----

def _trim(text: str, spans):
    out = []
    for s, e in spans:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e > s:
            out.append((s, e))
    return out


def fixed_spans(text: str, size: int) -> list[tuple[int, int]]:
    step = size - int(size * OVERLAP)
    return _trim(text, [(i, min(i + size, len(text))) for i in range(0, max(len(text) - int(size * OVERLAP), 1), step)])


def _pieces(text: str, start: int, end: int, sep: str) -> list[tuple[int, int]]:
    if sep == "":
        return [(i, i + 1) for i in range(start, end)]
    out, cursor = [], start
    while cursor < end:
        hit = text.find(sep, cursor, end)
        stop = end if hit < 0 else hit + len(sep)
        out.append((cursor, stop))
        cursor = stop
    return out


def _recursive(text: str, start: int, end: int, size: int, overlap: int, seps: tuple[str, ...]) -> list[tuple[int, int]]:
    if end - start <= size:
        return [(start, end)]
    sep_i = next(i for i, s in enumerate(seps) if s == "" or text.find(s, start, end) >= 0)
    pieces = []
    for s, e in _pieces(text, start, end, seps[sep_i]):
        pieces.extend([(s, e)] if e - s <= size else _recursive(text, s, e, size, overlap, seps[sep_i + 1:]))
    chunks, window = [], []
    for piece in pieces:
        if window and piece[1] - window[0][0] > size:
            chunks.append((window[0][0], window[-1][1]))
            while window and (window[-1][1] - window[0][0] > overlap or piece[1] - window[0][0] > size):
                window.pop(0)
        window.append(piece)
    if window:
        chunks.append((window[0][0], window[-1][1]))
    return chunks


def recursive_spans(text: str, size: int, start: int = 0, end: int | None = None) -> list[tuple[int, int]]:
    return _trim(text, _recursive(text, start, len(text) if end is None else end, size, int(size * OVERLAP), SEPARATORS))


def structure_recursive_spans(doc: Doc, size: int) -> list[tuple[int, int]]:
    spans = []
    for _, s, e in doc.sections:
        spans.extend(recursive_spans(doc.text, size, s, e))
    return spans


def sentence_spans(text: str) -> list[tuple[int, int]]:
    spans, cursor = [], 0
    for m in _SENTENCE_END.finditer(text):
        spans.append((cursor, m.start()))
        cursor = m.end()
    spans.append((cursor, len(text)))
    return _trim(text, spans)


def _lexical_vectors(texts: list[str]) -> np.ndarray:
    docs = [Counter(tokenize(t)) for t in texts]
    vocab = sorted({w for d in docs for w in d})
    if not vocab:
        return np.zeros((len(texts), 1), dtype=np.float32)
    col = {w: i for i, w in enumerate(vocab)}
    df = Counter(w for d in docs for w in d)
    idf = np.array([np.log((1 + len(docs)) / (1 + df[w])) + 1 for w in vocab], dtype=np.float32)
    m = np.zeros((len(texts), len(vocab)), dtype=np.float32)
    for r, d in enumerate(docs):
        for w, c in d.items():
            m[r, col[w]] = c
    m *= idf
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return m / norms


def semantic_spans(text: str, percentile: float, embed=None) -> list[tuple[int, int]]:
    """Sentences joined with one neighbour each side (buffer_size=1), break where the distance between consecutive
    windows is above the given percentile: the SemanticChunker percentile method. Lexical TF-IDF vectors unless an
    embedding function is given."""
    sents = sentence_spans(text)
    if len(sents) < 3:
        return sents
    windows = [" ".join(text[s:e] for s, e in sents[max(0, i - 1):i + 2]) for i in range(len(sents))]
    vectors = embed(windows) if embed else _lexical_vectors(windows)
    distances = 1 - np.sum(vectors[:-1] * vectors[1:], axis=1)
    cut = np.percentile(distances, percentile)
    chunks, first = [], 0
    for i, d in enumerate(distances):
        if d > cut:
            chunks.append((sents[first][0], sents[i][1]))
            first = i + 1
    chunks.append((sents[first][0], sents[-1][1]))
    return chunks


def strategies(embed: bool) -> dict[str, callable]:
    out = {"structure": None}
    for n in SIZES:
        out[f"fixed-{n}"] = lambda d, n=n, emb=None: fixed_spans(d.text, n)
    for n in SIZES:
        out[f"recursive-{n}"] = lambda d, n=n, emb=None: recursive_spans(d.text, n)
    for p in (95, 80):
        out[f"semantic-lex-{p}"] = lambda d, p=p, emb=None: semantic_spans(d.text, p)
    if embed:
        for p in (95, 80):
            out[f"semantic-emb-{p}"] = lambda d, p=p, emb=None: semantic_spans(d.text, p, emb)
    out["structure+recursive-512"] = lambda d, emb=None: structure_recursive_spans(d, 512)
    return out


# ---- index and scoring ----

def build(tenant_id: str, name: str, chunker, docs: list[Doc], records: list[dict], embedder) -> tuple[TenantIndex, dict]:
    """passages carry `_span` (doc key, start, end) so results can be matched to gold by overlap."""
    t0 = time.perf_counter()
    if chunker is None:
        span_of = {c: (s, e) for d in docs for c, s, e in d.sections}
        passages = [dict(r, _span=(r["document_id"], r["version"], *span_of[r["citation"]])) for r in records]
    else:
        passages = []
        embed_fn = (lambda texts: embedder.embed(texts)) if embedder else None
        for d in docs:
            for n, (s, e) in enumerate(chunker(d, emb=embed_fn) if "emb" in name else chunker(d)):
                citation = f"{d.document_id}@{d.version}#c{n}"
                text = d.text[s:e]
                passages.append({"passage_id": citation, "tenant_id": tenant_id, "document_id": d.document_id,
                                 "version": d.version, "current": d.current, "effective_date": d.effective_date,
                                 "section": "", "language": detect_language(text), "text": text, "citation": citation,
                                 "_span": (d.document_id, d.version, s, e)})
    chunk_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    bm25 = BM25([tokenize(passage_embed_text(p)) for p in passages])
    bm25_ms = (time.perf_counter() - t1) * 1000
    vectors, embed_ms = None, None
    if embedder is not None:
        t2 = time.perf_counter()
        vectors = embedder.embed([passage_embed_text(p) for p in passages])
        embed_ms = (time.perf_counter() - t2) * 1000
    empty = BM25([])
    index = TenantIndex(tenant_id=tenant_id, meta={}, passages=passages, passage_vectors=vectors, passage_bm25=bm25,
                        tickets=[], ticket_vectors=None, ticket_bm25=empty, resolutions=[], resolution_vectors=None,
                        resolution_bm25=empty, by_citation={p["citation"]: p for p in passages})
    current = [p for p in passages if p["current"]]
    lengths = [len(p["text"]) for p in current]
    stats = {"chunks": len(current), "chars_median": statistics.median(lengths), "chars_mean": round(statistics.mean(lengths), 1),
             "chars_max": max(lengths), "chunk_ms": round(chunk_ms, 2), "bm25_ms": round(bm25_ms, 2),
             "embed_ms": None if embed_ms is None else round(embed_ms, 1)}
    return index, stats


def _overlap(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def returned_spans(result, index: TenantIndex, budget: int | None) -> list[tuple]:
    """The spans a caller would read: the top results, or with a budget the ranked results until `budget` characters
    are used (the last one cut to fit), so every strategy hands the same amount of text to the answer model."""
    spans, used = [], 0
    for p in result.passages:
        doc, ver, s, e = index.by_citation[p.citation]["_span"]
        if budget is not None:
            if used >= budget:
                break
            e = min(e, s + budget - used)
        spans.append((doc, ver, s, e))
        used += e - s
    return spans


def score_question(case: dict, result, index: TenantIndex, gold_spans: dict, budget: int | None = None) -> dict:
    retrieved = returned_spans(result, index, budget)
    gold = [gold_spans[c] for c in case["expected"]]
    row = {"id": case["id"], "style": case["style"], "answerable": bool(gold), "returned": len(retrieved),
           "citations": [p.citation for p in result.passages][:len(retrieved)],
           "returned_chars": sum(e - s for _, _, s, e in retrieved)}
    if not gold:
        return row
    rel = []
    for doc, ver, s, e in retrieved:
        ok = False
        for gdoc, gver, gs, ge in gold:
            if (doc, ver) == (gdoc, gver):
                ov = _overlap((s, e), (gs, ge))
                ok = ok or ov >= 0.5 * (ge - gs) or ov >= 0.5 * (e - s)
        rel.append(ok)
    precisions, hits = [], 0
    for k, r in enumerate(rel, 1):
        hits += r
        if r:
            precisions.append(hits / k)
    covered = []
    for gdoc, gver, gs, ge in gold:
        marks = np.zeros(ge - gs, dtype=bool)
        for doc, ver, s, e in retrieved:
            if (doc, ver) == (gdoc, gver) and _overlap((s, e), (gs, ge)):
                marks[max(s, gs) - gs:min(e, ge) - gs] = True
        covered.append(marks.mean())
    gold_chars = 0
    for doc, ver, s, e in retrieved:
        gold_chars += max((_overlap((s, e), (gs, ge)) for gdoc, gver, gs, ge in gold if (doc, ver) == (gdoc, gver)), default=0)
    row.update({"hit": any(rel), "first_relevant_rank": (rel.index(True) + 1) if any(rel) else None,
                "context_precision": sum(precisions) / sum(rel) if any(rel) else 0.0,
                "context_recall": float(max(covered)),
                "char_precision": gold_chars / row["returned_chars"] if row["returned_chars"] else 0.0})
    return row


def evaluate(cases, index, embedder, gold_spans, min_cosine, min_bm25, min_top, budget=None) -> tuple[dict, list[dict]]:
    rows, latencies = [], []
    for case in cases:
        req = SearchKnowledgeRequest(request_id=case["id"], tenant_id=index.tenant_id, query=case["question"],
                                     top_k=TOP_K if budget is None else BUDGET_TOP_K)
        t = time.perf_counter()
        result = search_knowledge(req, index, embedder, min_cosine=min_cosine, min_bm25=min_bm25, min_top_score=min_top)
        latencies.append((time.perf_counter() - t) * 1000)
        rows.append(score_question(case, result, index, gold_spans, budget))
    ans = [r for r in rows if r["answerable"]]
    unans = [r for r in rows if not r["answerable"]]
    empties = [r for r in rows if r["returned"] == 0]
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0  # noqa: E731
    per_style = {}
    for r in ans:
        h, n = per_style.get(r["style"], (0, 0))
        per_style[r["style"]] = (h + r["hit"], n + 1)
    lat = sorted(latencies)
    summary = {
        "questions": len(rows), "answerable": len(ans), "unanswerable": len(unans),
        "hit_at_5": mean([r["hit"] for r in ans]),
        "mrr": mean([1 / r["first_relevant_rank"] if r["first_relevant_rank"] else 0 for r in ans]),
        "context_precision": mean([r["context_precision"] for r in ans]),
        "context_recall": mean([r["context_recall"] for r in ans]),
        "char_precision": mean([r["char_precision"] for r in ans if r["returned"]]),
        "no_answer_precision": (sum(not r["answerable"] for r in empties) / len(empties)) if empties else 1.0,
        "no_answer_recall": (sum(r["returned"] == 0 for r in unans) / len(unans)) if unans else 1.0,
        "correct": sum(r["hit"] for r in ans) + sum(r["returned"] == 0 for r in unans),
        "per_style": {s: f"{h}/{n}" for s, (h, n) in sorted(per_style.items())},
        "avg_returned_chars": mean([r["returned_chars"] for r in rows if r["returned"]]),
        "latency_ms_p50": round(lat[len(lat) // 2], 3), "latency_ms_p95": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 3),
        "min_bm25": min_bm25, "min_cosine": min_cosine,
    }
    return summary, rows


def tune_bm25(cases, index, embedder, gold_spans, min_cosine, min_top) -> float:
    """Per-strategy keyword threshold on dev: most questions correct, ties to the shipped grid's stricter value."""
    scores = {b: evaluate(cases, index, embedder, gold_spans, min_cosine, b, min_top)[0]["correct"] for b in SWEEP_BM25}
    best = max(scores.values())
    return max(b for b, s in scores.items() if s == best)


def check_against_production(tenant_id: str, records: list[dict], embedder, min_cosine, min_bm25, min_top) -> dict:
    """The structure strategy through this harness must give the same recall@5 as evaluation.run_retrieval."""
    bm25 = BM25([tokenize(passage_embed_text(p)) for p in records])
    vectors = embedder.embed([passage_embed_text(p) for p in records]) if embedder else None
    empty = BM25([])
    index = TenantIndex(tenant_id, {}, records, vectors, bm25, [], None, empty, [], None, empty, {p["citation"]: p for p in records})
    out = {}
    for split in SPLITS:
        cases = _load_jsonl(settings.benchmark_file(tenant_id, f"retrieval.{split}"))
        r = run_retrieval(cases, index, embedder, min_cosine, min_bm25, top_k=TOP_K, min_top_score=min_top)
        out[split] = {k: r[k] for k in ("recall_at_k", "no_answer_precision", "no_answer_recall", "correct", "per_style")}
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--embed", action="store_true", help="hybrid retrieval and embedding semantic chunks (needs Ollama)")
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[3] / "docs" / "reports" / "eval_runs" / "chunking")
    args = parser.parse_args(argv)
    embedder = None
    if args.embed:
        from knowledge_service.search.embeddings import OllamaEmbedder
        embedder = CachedEmbedder(OllamaEmbedder())
        embedder.embed(["warm-up"])  # fails fast with EmbeddingUnavailable when Ollama is not running
    mode = "hybrid" if embedder else "keyword_only"
    args.out.mkdir(parents=True, exist_ok=True)
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=Path(__file__).parent).stdout.strip()
    except OSError:
        commit = None
    results = {"run_at": datetime.now(timezone.utc).isoformat(), "commit": commit, "retrieval_mode": mode, "top_k": TOP_K,
               "embed_model": embedder.model if embedder else None, "python": sys.version.split()[0],
               "machine": platform.platform(), "tenants": {}}
    per_question = []
    for tenant_id in TENANTS:
        docs, records = load_docs(tenant_id)
        gold_spans = {c: (d.document_id, d.version, s, e) for d in docs for c, s, e in d.sections}
        min_cosine, min_bm25 = tenant_thresholds(tenant_id)
        min_top = tenant_min_top_score(tenant_id)
        cases = {s: _load_jsonl(settings.benchmark_file(tenant_id, f"retrieval.{s}")) for s in SPLITS}
        tenant = {"thresholds": {"min_cosine": min_cosine, "min_bm25": min_bm25, "min_top_score": min_top},
                  "corpus_chars": sum(len(d.text) for d in docs if d.current),
                  "production_check": check_against_production(tenant_id, records, embedder, min_cosine, min_bm25, min_top),
                  "strategies": {}}
        for name, chunker in strategies(bool(embedder)).items():
            index, stats = build(tenant_id, name, chunker, docs, records, embedder)
            entry = {"index": stats}
            for split in SPLITS:
                summary, rows = evaluate(cases[split], index, embedder, gold_spans, min_cosine, min_bm25, min_top)
                entry[split] = summary
                per_question.extend({"tenant": tenant_id, "strategy": name, "split": split, "thresholds": "shipped", **r} for r in rows)
            tuned = tune_bm25(cases["dev"], index, embedder, gold_spans, min_cosine, min_top)
            summary, rows = evaluate(cases["test"], index, embedder, gold_spans, min_cosine, tuned, min_top)
            entry["test_dev_tuned"] = summary
            per_question.extend({"tenant": tenant_id, "strategy": name, "split": "test", "thresholds": "dev_tuned", **r} for r in rows)
            summary, rows = evaluate(cases["test"], index, embedder, gold_spans, min_cosine, min_bm25, min_top, BUDGET_CHARS)
            entry["test_budget"] = summary
            per_question.extend({"tenant": tenant_id, "strategy": name, "split": "test", "thresholds": f"shipped_budget{BUDGET_CHARS}", **r}
                                for r in rows)
            tenant["strategies"][name] = entry
            t = entry["test"]
            print(f"{tenant_id:10} {name:24} chunks={stats['chunks']:4} hit@5={t['hit_at_5']:.3f} CP={t['context_precision']:.3f} "
                  f"CR={t['context_recall']:.3f} na_rec={t['no_answer_recall']:.3f} p50={t['latency_ms_p50']:.2f}ms")
        results["tenants"][tenant_id] = tenant
    (args.out / f"results_{mode}.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.out / f"per_question_{mode}.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in per_question), encoding="utf-8")
    print(f"Wrote {args.out / f'results_{mode}.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
