"""Stand-in for Team A policy search: keyword search over the Nile Style policies, with Arabizi synonym expansion.

Scoring: normalized tokens, synonym expansion (araga3 means return, flousi means refund), passage keywords and
BM25-like weights. Superseded passages (return policy v1) are never searched and never returned.
"""

import copy
import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from team_b.adapters.standins.text_search import Document, Hit, Index, build_synonyms
from team_b.contracts.errors import UpstreamError
from team_b.contracts.evidence import EmptyReason, Passage, PastTicket, PastTicketResult, RetrievalResult

SERVICE = "policy_search"
OPERATIONS = ("search_knowledge", "get_passage", "search_past_tickets")
_RAW: dict[Path, Any] = {}


def _read(path: Path) -> Any:
    if path not in _RAW:
        _RAW[path] = json.loads(path.read_text(encoding="utf-8"))
    return copy.deepcopy(_RAW[path])


def _passage_document(p: dict[str, Any]) -> Document:
    # A word in the keywords counts three times, in the section title one and a half times, in the body once.
    fields = [(p["text"], 1.0), (p["section"], 1.5), (" ".join(p["keywords"]), 3.0)]
    return Document(p["passage_id"], fields, p["keywords"])


def _ticket_document(t: dict[str, Any]) -> Document:
    return Document(t["ticket_id"], [(t["customer_message"], 2.0), (t["resolution"], 1.0), (t["category"], 1.0)])


class _Corpus:
    def __init__(self, folder: Path) -> None:
        policies = _read(folder / "policies.json")
        synonyms = build_synonyms(_read(folder / "synonyms.json")["entries"])
        self.passages: dict[str, dict[str, Any]] = {p["passage_id"]: p for p in policies["passages"]}
        current = [p for p in self.passages.values() if not p["superseded"]]
        self.index = Index([_passage_document(p) for p in current], synonyms)
        tickets = _read(folder / "tickets.json")["tickets"] if (folder / "tickets.json").is_file() else []
        self.tickets: dict[str, dict[str, Any]] = {t["ticket_id"]: t for t in tickets}
        self.ticket_index = Index([_ticket_document(t) for t in tickets], synonyms)


class PolicySearchStandin:
    # Calibrated on the sample questions: the weakest correct hit scores 5.27, unrelated questions stay at or below 5.0.
    MIN_SCORE = 5.1  # below this the question is treated as not covered by the policies
    MIN_TICKET_SCORE = 4.0
    SHORT_QUERY_WORDS, SHORT_QUERY_MIN_SCORE = 2, 2.0  # one or two words can only ever match a little

    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir
        self._corpora: dict[str, _Corpus] = {}
        self._fail: dict[str, int] = {}

    # ---- the search operations (the EvidenceProvider plug) ----

    async def search_knowledge(
        self,
        tenant_id: str,
        query: str,
        *,
        request_id: str,
        conversation_id: str | None = None,
        top_k: int = 5,
    ) -> RetrievalResult:
        self._maybe_fail("search_knowledge")
        corpus = self._corpus(tenant_id)
        hits = [h for h in corpus.index.search(query) if self._enough(h)][: max(1, min(top_k, 20))]
        passages = tuple(self._passage(corpus.passages[h.doc_id], h.score) for h in hits)
        reason: EmptyReason | None = None if passages else "no_match"
        return RetrievalResult(
            request_id=request_id, tenant_id=tenant_id, query=query, passages=passages, empty_reason=reason,
            retrieval_mode="keyword_only",
        )  # fmt: skip

    async def get_passage(self, tenant_id: str, citation: str) -> Passage | None:
        self._maybe_fail("get_passage")
        raw = self._corpus(tenant_id).passages.get(citation)
        # Unlike Team A (which returns any citation), the old return policy is never handed out.
        return None if raw is None or raw["superseded"] else self._passage(raw, 1.0)

    async def search_past_tickets(
        self, tenant_id: str, query: str, *, request_id: str, top_k: int = 3
    ) -> PastTicketResult:
        self._maybe_fail("search_past_tickets")
        corpus = self._corpus(tenant_id)
        hits = [h for h in corpus.ticket_index.search(query) if h.score >= self.MIN_TICKET_SCORE][
            : max(1, min(top_k, 10))
        ]
        tickets = tuple(self._ticket(corpus.tickets[h.doc_id], h.score) for h in hits)
        return PastTicketResult(
            request_id=request_id, tenant_id=tenant_id, query=query, tickets=tickets,
            empty_reason=None if tickets else "no_match",
        )  # fmt: skip

    # ---- failure switch ----

    def fail_next(self, operation: str, times: int = 1) -> None:
        """The next `times` calls of the operation raise UpstreamError(retryable=True)."""
        if operation not in OPERATIONS:
            raise ValueError(f"operation must be one of {OPERATIONS}")
        if times < 1:
            raise ValueError("times must be at least 1")
        self._fail[operation] = self._fail.get(operation, 0) + times

    def reset(self) -> None:
        self._fail.clear()

    def inject(self, spec: Mapping[str, Any]) -> None:
        """Apply a switch from a scenario file, e.g. {"switch": "fail_next", "operation": "search_knowledge"}."""
        switch = spec.get("switch")
        if switch == "fail_next":
            self.fail_next(str(spec["operation"]), int(spec.get("times", 1)))
        elif switch == "reset":
            self.reset()
        else:
            raise ValueError(f"unknown policy_search switch: {switch!r}")

    # ---- internals ----

    def _enough(self, hit: Hit) -> bool:
        """Strong score, or a short question whose every word is covered by the passage."""
        if hit.score >= self.MIN_SCORE:
            return True
        return hit.words <= self.SHORT_QUERY_WORDS and hit.coverage >= 1.0 and hit.score >= self.SHORT_QUERY_MIN_SCORE

    def _maybe_fail(self, operation: str) -> None:
        if self._fail.get(operation, 0) > 0:
            self._fail[operation] -= 1
            raise UpstreamError(SERVICE, "BACKEND_UNAVAILABLE", f"injected failure on {operation}", retryable=True)

    def _corpus(self, tenant_id: str) -> _Corpus:
        if tenant_id not in self._corpora:
            folder = self._dir / tenant_id
            if not (folder / "policies.json").is_file() or not (folder / "synonyms.json").is_file():
                raise UpstreamError(SERVICE, "TENANT_NOT_FOUND", f"no policies for tenant {tenant_id}")
            self._corpora[tenant_id] = _Corpus(folder)
        return self._corpora[tenant_id]

    @staticmethod
    def _passage(raw: dict[str, Any], score: float) -> Passage:
        keys = ("passage_id", "document_id", "version", "section", "language", "text")
        return Passage(**{k: raw[k] for k in keys}, score=score)

    @staticmethod
    def _ticket(raw: dict[str, Any], score: float) -> PastTicket:
        return PastTicket(
            ticket_id=raw["ticket_id"], category=raw["category"], customer_message=raw["customer_message"],
            resolution=raw["resolution"], created_at=date.fromisoformat(raw["created_at"]), score=score,
            citation=f"ticket:{raw['ticket_id']}",
        )  # fmt: skip
