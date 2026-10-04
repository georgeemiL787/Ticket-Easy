"""Hybrid (BM25 + embedding) retrieval with an explicit no-evidence threshold.

Two stages:
  1. Query gate: the corpus has evidence for the query when the best cosine similarity reaches
     MIN_COSINE or the best BM25 score reaches MIN_BM25. Otherwise the result is empty with
     empty_reason="below_threshold"; callers must treat that as "no evidence", never guess.
  2. Passage floor: once the gate passes, passages are ranked by a hybrid score and kept if their
     cosine is within PASSAGE_COS_MARGIN of MIN_COSINE or they have any keyword match.
"""

import hashlib
import logging

import numpy as np

from team_a.config import settings
from team_a.knowledge.embeddings import Embedder, EmbeddingUnavailable
from team_a.knowledge.index import TenantIndex
from team_a.schemas import (
    Passage,
    PastTicket,
    PastTicketResult,
    RetrievalResult,
    SearchKnowledgeRequest,
    SearchPastTicketsRequest,
)
from team_a.text import expand_query, normalize, tokenize

log = logging.getLogger(__name__)

COSINE_WEIGHT = 0.65
PASSAGE_COS_MARGIN = 0.08


def _bm25_norm(scores: np.ndarray) -> np.ndarray:
    # Saturating map to [0, 1) so the hybrid score stays comparable across queries.
    return scores / (scores + settings.min_bm25)


def _cosines(query: str, vectors: np.ndarray | None, embedder: Embedder | None) -> np.ndarray | None:
    if vectors is None or embedder is None:
        return None
    try:
        return vectors @ embedder.embed([query])[0]
    except EmbeddingUnavailable as exc:
        log.warning("Falling back to keyword-only retrieval: %s", exc)
        return None


def _rank(
    candidates: list[int],
    bm25_all: list[float],
    cos_all: np.ndarray | None,
    min_cosine: float,
    min_bm25: float,
) -> list[tuple[int, float]]:
    if not candidates:
        return []
    idx = np.asarray(candidates)
    bm25 = np.asarray(bm25_all, dtype=np.float32)[idx]
    if cos_all is not None:
        cos = cos_all[idx]
        if cos.max() < min_cosine and bm25.max() < min_bm25:
            return []
        keep = (cos >= min_cosine - PASSAGE_COS_MARGIN) | (bm25 > 0)
        score = COSINE_WEIGHT * cos + (1 - COSINE_WEIGHT) * _bm25_norm(bm25)
    else:
        if bm25.max() < min_bm25:
            return []
        keep = bm25 > 0
        score = _bm25_norm(bm25)
    ranked = sorted(
        ((int(i), float(s)) for i, s, ok in zip(idx, score, keep) if ok),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return ranked


def search_knowledge(
    req: SearchKnowledgeRequest,
    index: TenantIndex,
    embedder: Embedder | None,
    min_cosine: float | None = None,
    min_bm25: float | None = None,
) -> RetrievalResult:
    min_cosine = settings.min_cosine if min_cosine is None else min_cosine
    min_bm25 = settings.min_bm25 if min_bm25 is None else min_bm25

    candidates = [
        i for i, p in enumerate(index.passages)
        if p["tenant_id"] == req.tenant_id and (p["current"] or req.include_superseded)
    ]
    expanded = expand_query(req.query)
    bm25 = index.passage_bm25.scores(tokenize(expanded))
    cos = _cosines(expanded, index.passage_vectors, embedder)
    ranked = _rank(candidates, bm25, cos, min_cosine, min_bm25)

    passages, seen_text = [], set()
    for i, score in ranked:
        p = index.passages[i]
        fingerprint = hashlib.sha1(normalize(p["text"]).encode()).hexdigest()
        if fingerprint in seen_text:
            continue
        seen_text.add(fingerprint)
        passages.append(Passage(
            passage_id=p["passage_id"],
            document_id=p["document_id"],
            version=p["version"],
            section=p["section"],
            language=p["language"],
            text=p["text"],
            score=round(score, 4),
            citation=p["citation"],
        ))
        if len(passages) == req.top_k:
            break

    empty_reason = None
    if not passages:
        empty_reason = "no_documents" if not candidates else "below_threshold"
    return RetrievalResult(
        request_id=req.request_id,
        tenant_id=req.tenant_id,
        query=req.query,
        passages=passages,
        empty_reason=empty_reason,
        retrieval_mode="hybrid" if cos is not None else "keyword_only",
    )


def get_passage(index: TenantIndex, citation: str) -> Passage | None:
    p = index.by_citation.get(citation)
    if p is None:
        return None
    return Passage(
        passage_id=p["passage_id"],
        document_id=p["document_id"],
        version=p["version"],
        section=p["section"],
        language=p["language"],
        text=p["text"],
        score=1.0,
        citation=p["citation"],
    )


def search_past_tickets(
    req: SearchPastTicketsRequest, index: TenantIndex, embedder: Embedder | None
) -> PastTicketResult:
    candidates = [i for i, t in enumerate(index.tickets) if t["tenant_id"] == req.tenant_id]
    expanded = expand_query(req.query)
    bm25 = index.ticket_bm25.scores(tokenize(expanded))
    cos = _cosines(expanded, index.ticket_vectors, embedder)
    ranked = _rank(candidates, bm25, cos, settings.min_cosine, settings.min_bm25)

    tickets, seen = [], set()
    for i, score in ranked:
        t = index.tickets[i]
        fingerprint = normalize(t["customer_message"])
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        tickets.append(PastTicket(
            ticket_id=t["ticket_id"],
            category=t["category"],
            customer_message=t["customer_message"],
            resolution=t["resolution"],
            created_at=t["created_at"],
            score=round(score, 4),
            citation=f"ticket:{t['ticket_id']}",
        ))
        if len(tickets) == req.top_k:
            break

    empty_reason = None
    if not tickets:
        empty_reason = "no_documents" if not candidates else "below_threshold"
    return PastTicketResult(
        request_id=req.request_id,
        tenant_id=req.tenant_id,
        query=req.query,
        tickets=tickets,
        empty_reason=empty_reason,
    )
