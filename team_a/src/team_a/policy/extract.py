"""Candidate-rule extraction: a free LLM reads policy passages and proposes rules.

Every candidate is validated against the Rule schema and the shared vocabulary, and its quote must
appear verbatim (after normalization) in the source passage. Survivors are stored as `proposed`;
a human must approve them before they have any effect.
"""

import hashlib
import json
import logging
from functools import lru_cache

from pydantic import ValidationError

from team_a import llm
from team_a.config import settings
from team_a.knowledge.index import build_passages, load_manifest
from team_a.schemas import Rule
from team_a.text import normalize

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def vocabulary() -> dict:
    return json.loads((settings.data_dir / "rules" / "vocabulary.json").read_text(encoding="utf-8"))


def _system_prompt() -> str:
    vocab = vocabulary()
    return f"""You convert e-commerce policy text into machine-checkable rules.
Return ONLY a JSON object: {{"rules": [ ... ]}}. Return {{"rules": []}} if the passage states no
enforceable condition on one of the allowed actions.

Each rule object:
{{
  "action": one of {sorted(vocab["actions"])},
  "applies_if": [<condition>, ...]   (when the rule takes part at all; empty = every request for the action),
  "conditions": [<condition>, ...],
  "effect": "allow" | "deny" | "require_human"      (outcome when the rule applies and ALL conditions hold),
  "else_effect": "allow" | "deny" | "require_human" (outcome when it applies and they do not),
  "quote": exact sentence copied from the passage that states the rule,
  "user_message": {{"ar": "<Egyptian-friendly Arabic sentence>", "en": "<English sentence>"}}
}}

A <condition> is {{"field": <name>, "op": one of ["<=","<",">=",">","==","!=","in","not_in"], "value": <value>, "from": "facts" or "arguments"}}.

applies_if vs conditions: if the text describes a special case ("if the item arrived damaged...",
"for delivered orders..."), that situation goes in applies_if and the limit goes in conditions.
Example: "damaged items must be reported within 48 hours" ->
  applies_if: item_condition in ["damaged_on_arrival","defective"]; conditions: hours_since_delivery <= 48.
Putting the special case in conditions with else_effect "deny" would wrongly deny every ordinary request.
Each rule must govern the action itself: a sentence about fees, vouchers at checkout or other side
details is not a rule on create_refund or apply_voucher.

Allowed facts (from="facts"): {json.dumps(vocab["facts"], ensure_ascii=False)}
Allowed arguments (from="arguments"): {json.dumps(vocab["arguments"], ensure_ascii=False)}
Prefer day counts (days_since_delivery, days_late) over raw dates. Money is in EGP.
Only encode what the text says; do not invent limits."""


def _validate_candidate(raw: dict, passage: dict, effective_date: str) -> Rule | None:
    vocab = vocabulary()
    if raw.get("action") not in vocab["actions"]:
        return None
    for cond in [*raw.get("applies_if", []), *raw.get("conditions", [])]:
        source = cond.get("from", "facts")
        allowed = vocab["facts"] if source == "facts" else vocab["arguments"]
        if cond.get("field") not in allowed:
            return None
    quote = str(raw.get("quote", "")).strip()
    if not quote or normalize(quote) not in normalize(passage["text"]):
        log.info("Dropped candidate with ungrounded quote: %r", quote[:80])
        return None
    identity = [passage["citation"], raw["action"], raw.get("conditions")]
    if raw.get("applies_if"):  # only when present, so IDs of rules without it stay stable across runs
        identity.append(raw["applies_if"])
    digest = hashlib.sha1(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:6].upper()
    try:
        return Rule.model_validate({
            "rule_id": f"P-{passage['document_id'].upper().replace('_', '-')}-{digest}",
            "tenant_id": passage["tenant_id"],
            "source": {
                "document_id": passage["document_id"],
                "version": passage["version"],
                "citation": passage["citation"],
                "quote": quote,
            },
            "scope": passage["document_id"],
            "action": raw["action"],
            "applies_if": raw.get("applies_if", []),
            "conditions": raw.get("conditions", []),
            "effect": raw.get("effect"),
            "else_effect": raw.get("else_effect", "deny"),
            "user_message": raw.get("user_message"),
            "approval_status": "proposed",
            "effective_date": effective_date,
        })
    except ValidationError as exc:
        log.info("Dropped invalid candidate: %s", exc.errors()[:2])
        return None


def extract_candidates(tenant_id: str, document_id: str | None = None) -> list[Rule]:
    manifest = load_manifest(tenant_id)
    effective = {
        (d["document_id"], d["version"]): d.get("effective_date") for d in manifest["documents"]
    }
    passages, _ = build_passages(tenant_id)
    targets = [
        p for p in passages
        if p["current"] and (document_id is None or p["document_id"] == document_id)
    ]
    candidates: list[Rule] = []
    for passage in targets:
        prompt = f"Passage {passage['citation']} ({passage['section']}):\n{passage['text']}"
        out = llm.complete_json(_system_prompt(), prompt)
        for raw in out.get("rules", []):
            rule = _validate_candidate(raw, passage, effective[(passage["document_id"], passage["version"])])
            if rule is not None:
                candidates.append(rule)
    return candidates
