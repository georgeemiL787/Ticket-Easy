"""classify_risk: flag conversations that must reach a human.

Two layers:
  1. Reviewed keyword lists (Arabic, English, Arabizi) - deterministic, always runs.
  2. Optional free LLM second opinion - may ADD categories, never removes a keyword hit.
The result feeds check_action (MANDATORY_RISK) and Team B's escalation trigger.
"""

import json
import logging
from functools import lru_cache

from team_a import llm
from team_a.config import settings
from team_a.schemas import MANDATORY_ESCALATION, ClassifyRiskRequest, RiskAssessment
from team_a.text import normalize

log = logging.getLogger(__name__)

_SYSTEM = """You screen customer-support messages for an Egyptian e-commerce shop.
Messages may be in English, Egyptian Arabic or Arabizi (Arabic in Latin letters with digits like 3, 7, 2).
Return ONLY a JSON object: {"categories": [...], "rationale": "<one short sentence>"}.
Allowed categories (use only when clearly present, else return an empty list):
- fraud_suspected: unauthorized payment, stolen card, scam, transaction the customer did not make
- legal_regulatory: lawyer, lawsuit, court, police, consumer-protection agency, formal regulator complaint
- medical_safety: injury, allergy, poisoning, burns, fire, electric shock, any health or safety danger
- compensation_demand: customer demands compensation or damages
- identity_concern: hacked account, impersonation, someone else using the account or phone number
Ordinary complaints, late orders, returns and refunds within policy are NOT risk categories."""


@lru_cache(maxsize=1)
def _keywords() -> dict[str, list[str]]:
    raw = json.loads((settings.data_dir / "risk" / "keywords.json").read_text(encoding="utf-8"))
    return {cat: [normalize(t) for t in terms] for cat, terms in raw["categories"].items()}


def keyword_scan(message: str) -> tuple[set[str], list[str]]:
    text = normalize(message)
    categories, matched = set(), []
    for category, terms in _keywords().items():
        for term in terms:
            if term in text:
                categories.add(category)
                matched.append(term)
    return categories, matched


def classify_risk(req: ClassifyRiskRequest) -> RiskAssessment:
    categories, matched = keyword_scan(req.message)
    method, rationale = "keywords", None

    if req.use_llm and llm.is_configured():
        try:
            out = llm.complete_json(_SYSTEM, f"Message:\n{req.message}")
            extra = {c for c in out.get("categories", []) if c in MANDATORY_ESCALATION}
            categories |= extra
            rationale = str(out.get("rationale", ""))[:500] or None
            method = "keywords+llm"
        except llm.LLMUnavailable as exc:
            log.warning("Risk LLM unavailable, keyword result only: %s", exc)

    ordered = sorted(categories)
    return RiskAssessment(
        request_id=req.request_id,
        tenant_id=req.tenant_id,
        categories=ordered,
        mandatory_escalation=bool(ordered),
        method=method,
        matched_terms=matched,
        llm_rationale=rationale,
    )
