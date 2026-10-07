"""Precedents from human-resolved escalations: a separate, redacted corpus.

Safety rules (enforced here, at write time, and re-checked at read time):
  1. A case with any mandatory-escalation risk category is never stored: those cases must always
     reach a human fresh, whatever happened before.
  2. No personal data is stored. Callers send already-redacted text; is_safe_to_persist() refuses
     (never silently scrubs) anything that still looks like a name, phone, email, address, order or
     reference id, payment detail or verbatim transcript.
  3. Precedents are advisory. Search returns them for a human reviewer; nothing here (and nothing in
     check_action) can authorize or execute an action because of a precedent.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date

from team_a.config import settings
from team_a.knowledge.embeddings import Embedder
from team_a.knowledge.index import TenantIndex, index_resolutions, load_resolutions
from team_a.knowledge.retrieval import _cosines, _rank
from team_a.policy.risk import keyword_scan
from team_a.schemas import (
    MANDATORY_ESCALATION,
    Precedent,
    RedactionCheck,
    ResolutionSearchResult,
    ResolutionWriteResult,
    ResolvedEscalation,
    ResolvedEscalationRequest,
    SearchResolutionsRequest,
)
from team_a.text import expand_query, normalize, tokenize


@dataclass
class SafetyVerdict:
    safe: bool
    reasons: list[str] = field(default_factory=list)  # kinds only, never the offending value


# ------------------------------------------------------------ rule 1: risk

def _stored_text(category: str, tags: list[str], summary: str, resolution: str) -> str:
    return " ".join([category.replace("_", " "), *(t.replace("_", " ") for t in tags), summary, resolution])


def mandatory_risk_reasons(req: ResolvedEscalationRequest) -> list[str]:
    reasons = [f"mandatory_risk:{c}" for c in req.risk_categories]
    if req.escalation_reason == "mandatory_category":
        reasons.append("mandatory_risk:escalation_reason")
    reasons += [f"mandatory_risk:{s}" for s in [req.category, *req.tags] if s in MANDATORY_ESCALATION]
    # Re-scan what would be stored: a redacted summary of a fraud case is still a fraud case.
    found, _ = keyword_scan(_stored_text(req.category, req.tags, req.redacted_summary, req.resolution))
    reasons += [f"mandatory_risk:{c}" for c in sorted(found)]
    return list(dict.fromkeys(reasons))


# ---------------------------------------------------- rule 2: personal data

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_DATE = re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b")  # not phone numbers
_DIGIT_RUN = re.compile(r"\+?\d[\d\s\-().]{5,}\d")
_ORDER_ID = re.compile(r"\bns[\s_-]?\d{3,}\b")
_REFERENCE_ID = re.compile(r"\b[a-z]{1,4}-\d{3,}\b")  # C-100, T-1001 (rule ids like R-REFUND-14D don't match)
_LONG_NUMBER = re.compile(r"(?<![\d.])\d{5,}(?![\d.])")  # order/reference numbers; amounts here stay < 10000
_PAYMENT = re.compile(
    r"\b(card|visa|mastercard|iban|cvv|cvc|expiry|كارت|بطاقه|بطاقته|بطاقتي|فيزا)\b\D{0,25}\d{3,}"
    r"|\b(ending( in)?|last 4( digits)?|اخر 4( ارقام)?|ينتهي ب)\D{0,5}\d{4}\b"
)
_ADDRESS = re.compile(
    r"\b(street|st\.|road|rd\.|apartment|apt|building|bldg|floor)\b"  # not "flat": "flat shipping fee"
    r"|(شارع|عماره|شقه|الدور|برج|ش\.)"
)
_NAME = re.compile(
    r"\b(mr|mrs|ms|miss|dr)\.?\s+[a-z]{2,}"
    r"|\b(customer|client)('s)? name\b"
    r"|(الاستاذ|استاذ|الاستاذه|استاذه|مدام|انسه|السيد|السيده)\s+\S{2,}"
    r"|(اسمه|اسمها|اسم العميل)"
)
_SPEAKER = re.compile(r"(^|\n|\s)(customer|agent|bot|العميل|الموظف|المساعد|البوت)\s*:", re.I)
_QUOTED = re.compile(r"[\"“”«»][^\"“”«»]{25,}[\"“”«»]")
_VERBATIM_NGRAM = 6


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _ngrams(text: str, n: int) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", normalize(text))
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _contains_word(text: str, value: str) -> bool:
    value = normalize(value)
    return len(value) >= 2 and re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text) is not None


def _known_value_reasons(text: str, check: RedactionCheck) -> list[str]:
    norm, digits = normalize(text), _digits(normalize(text))
    reasons = []
    for kind, values in (("customer_name", check.customer_names), ("email", check.emails),
                         ("address", check.addresses), ("order_id", check.order_ids)):
        if any(_contains_word(norm, v) for v in values):
            reasons.append(kind)
    if any(len(_digits(normalize(p))) >= 7 and _digits(normalize(p))[-7:] in digits for p in check.phones):
        reasons.append("phone")
    stored = _ngrams(text, _VERBATIM_NGRAM)
    if any(stored & _ngrams(m, _VERBATIM_NGRAM) for m in check.transcript_messages):
        reasons.append("verbatim_transcript")
    return reasons


def personal_data_reasons(req: ResolvedEscalationRequest) -> list[str]:
    raw = f"{req.redacted_summary}\n{req.resolution}"
    text = normalize(raw)  # also maps Arabic-Indic digits to 0-9
    kinds = []
    if _EMAIL.search(text):
        kinds.append("email")
    for run in _DIGIT_RUN.findall(_DATE.sub(" ", text)):
        n = len(_digits(run))
        if n >= 13:
            kinds.append("payment_details")
        elif n >= 8:
            kinds.append("phone")
    if _ORDER_ID.search(text) or _LONG_NUMBER.search(text):
        kinds.append("order_id")
    if _REFERENCE_ID.search(text) or any(re.search(r"\d{4,}", t) for t in [req.category, *req.tags]):
        kinds.append("reference_id")
    if _PAYMENT.search(text):
        kinds.append("payment_details")
    if _ADDRESS.search(text):
        kinds.append("address")
    if _NAME.search(text):
        kinds.append("customer_name")
    if _SPEAKER.search(raw) or _QUOTED.search(raw):
        kinds.append("verbatim_transcript")
    if req.redaction_check:
        kinds += _known_value_reasons(raw, req.redaction_check)
    return [f"personal_data:{k}" for k in dict.fromkeys(kinds)]


def is_safe_to_persist(req: ResolvedEscalationRequest) -> SafetyVerdict:
    """The single write gate. Both checks always run so the caller sees every reason at once."""
    reasons = mandatory_risk_reasons(req) + personal_data_reasons(req)
    return SafetyVerdict(safe=not reasons, reasons=reasons)


# ------------------------------------------------------------------- write

def _case_id(tenant_id: str, request_id: str) -> str:
    # Derived from the write's request_id so a retried write is idempotent; carries no case data.
    return "PREC-" + hashlib.sha256(f"{tenant_id}|{request_id}".encode()).hexdigest()[:10].upper()


def add_resolution(req: ResolvedEscalationRequest, embedder: Embedder | None) -> ResolutionWriteResult:
    """Ingest one resolved case: gate, append to the corpus, re-index. Nothing is written if unsafe."""
    verdict = is_safe_to_persist(req)
    if not verdict.safe:
        return ResolutionWriteResult(request_id=req.request_id, tenant_id=req.tenant_id,
                                     stored=False, rejected_reasons=verdict.reasons)

    case_id = _case_id(req.tenant_id, req.request_id)
    if case_id not in {r["case_id"] for r in load_resolutions(req.tenant_id)}:
        record = ResolvedEscalation(
            case_id=case_id,
            tenant_id=req.tenant_id,
            category=req.category,
            redacted_summary=req.redacted_summary.strip(),
            resolution=req.resolution.strip(),
            cited_rule_id=req.cited_rule_id,
            tags=req.tags,
            escalation_reason=req.escalation_reason,
            created_at=date.today(),
        )
        path = settings.resolutions_file(req.tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")
        index_resolutions(req.tenant_id, embedder, strict=False)
    return ResolutionWriteResult(request_id=req.request_id, tenant_id=req.tenant_id, stored=True, case_id=case_id)


# -------------------------------------------------------------------- read

def _record_is_mandatory(r: dict) -> bool:
    if r["category"] in MANDATORY_ESCALATION or set(r.get("tags", [])) & MANDATORY_ESCALATION:
        return True
    return bool(keyword_scan(_stored_text(r["category"], r.get("tags", []), r["redacted_summary"], r["resolution"]))[0])


def search_resolutions(
    req: SearchResolutionsRequest, index: TenantIndex, embedder: Embedder | None
) -> ResolutionSearchResult:
    """Advisory lookup for a human reviewer. Returns precedents only; never an instruction to act."""
    def result(precedents, empty_reason, mode="hybrid"):
        return ResolutionSearchResult(request_id=req.request_id, tenant_id=req.tenant_id, query=req.query,
                                      precedents=precedents, similar_count=len(precedents),
                                      empty_reason=empty_reason, retrieval_mode=mode)

    # A mandatory-risk case must reach a human fresh: no precedent is offered for it. The caller's
    # classify_risk result (which may include the LLM layer) is authoritative; the keyword re-scan of the
    # query is only a backstop, since keyword lists miss phrasings.
    if req.risk_categories or keyword_scan(req.query)[0]:
        return result([], "mandatory_risk")

    candidates = [
        i for i, r in enumerate(index.resolutions)
        if r["tenant_id"] == req.tenant_id and not _record_is_mandatory(r)  # defence in depth
    ]
    expanded = expand_query(req.query)
    bm25 = index.resolution_bm25.scores(tokenize(expanded))
    cos = _cosines(expanded, index.resolution_vectors, embedder)
    mode = "hybrid" if cos is not None else "keyword_only"
    ranked = _rank(candidates, bm25, cos, settings.min_cosine, settings.min_bm25)

    precedents = [
        Precedent(**index.resolutions[i], score=round(score, 4), citation=f"resolution:{index.resolutions[i]['case_id']}")
        for i, score in ranked[:req.top_k]
    ]
    if not precedents:
        return result([], "no_documents" if not candidates else "below_threshold", mode)
    return result(precedents, None, mode)
