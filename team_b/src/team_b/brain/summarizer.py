"""History summaries: what happened in the messages that no longer fit in working memory.

TemplateHistorySummarizer is deterministic and always available. LLMHistorySummarizer writes a more natural summary but
is checked: if its text contains a number, order id or date that is not in the source (the previous summary and the
folded messages), it is rejected and the template summary is used. The model can never put a new fact in memory.
"""

import re
from collections.abc import Sequence
from typing import Literal, NamedTuple, Protocol

from team_b.brain.composer import check_grounded
from team_b.brain.llm_nlu import PromptTemplate
from team_b.domain.handoff import HandoffPackage
from team_b.domain.session import Message, SessionState
from team_b.domain.tenant import TenantConfig
from team_b.domain.understanding import Language
from team_b.ports import LLMClient

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUMBER = re.compile(r"\d+")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

SYSTEM_PROMPT = (
    "You summarize the older part of a customer-service chat in two or three short sentences for the next agent. "
    "Use only facts from the text you are given. Never add a number, order id or date that is not in the text. "
    'Answer with JSON: {"summary": "..."}.'
)


class HistorySummarizer(Protocol):
    async def summarize(self, session: SessionState, folded: Sequence[Message], tenant: TenantConfig) -> str:
        """The new history_summary. The session still holds the old summary; `folded` are the messages being dropped."""
        ...


def _source_text(session: SessionState, folded: Sequence[Message]) -> str:
    return "\n".join([session.history_summary, *(m.text for m in folded)]).translate(_ARABIC_DIGITS)


def order_ids_in(text: str, tenant: TenantConfig) -> list[str]:
    """Order ids in the text, first appearance first, no repeats."""
    found = (m.group(0) for m in re.finditer(tenant.order_id_pattern, text))
    return list(dict.fromkeys(found))


def invented_facts(summary: str, source: str, tenant: TenantConfig) -> list[str]:
    """Numbers, order ids and dates in `summary` that the source does not contain."""
    summary, source = summary.translate(_ARABIC_DIGITS), source.translate(_ARABIC_DIGITS)
    known_numbers = set(_NUMBER.findall(source))
    problems = [n for n in _NUMBER.findall(summary) if n not in known_numbers]
    problems += [d for d in _DATE.findall(summary) if d not in source]
    problems += [o for o in order_ids_in(summary, tenant) if o not in source]
    return list(dict.fromkeys(problems))


class TemplateHistorySummarizer:
    """Facts only: intents seen, order ids, identity status, actions and their final states, last escalation."""

    async def summarize(self, session: SessionState, folded: Sequence[Message], tenant: TenantConfig) -> str:
        orders = order_ids_in(_source_text(session, folded), tenant)
        order_slot = session.slots.get("order_id")
        if order_slot and order_slot not in orders:
            orders.append(order_slot)
        current = [session.active_intent] if session.active_intent else []
        intents = list(dict.fromkeys([*session.intents_seen, *current]))
        identity = session.identity
        parts = [
            f"Intents seen: {', '.join(intents) if intents else 'none yet'}.",
            f"Orders mentioned: {', '.join(orders) if orders else 'none'}.",
            f"Identity: verified as {identity.customer_id}." if identity.verified else "Identity: not verified.",
        ]
        if session.actions:
            parts.append("Actions: " + ", ".join(f"{a.tool} ({a.state.value})" for a in session.actions) + ".")
        if session.last_escalation is not None:
            parts.append(f"Last escalation: {session.last_escalation.value}.")
        return " ".join(parts)


class LLMHistorySummarizer:
    """Asks the model for a summary; falls back to the template on any error or any invented fact."""

    def __init__(self, llm: LLMClient, fallback: HistorySummarizer | None = None) -> None:
        self._llm = llm
        self._fallback = fallback or TemplateHistorySummarizer()

    async def summarize(self, session: SessionState, folded: Sequence[Message], tenant: TenantConfig) -> str:
        template = await self._fallback.summarize(session, folded, tenant)
        source = _source_text(session, folded)
        lines = "\n".join(f"{m.role}: {m.text}" for m in folded)
        user = f"Previous summary: {session.history_summary or '(none)'}\nMessages:\n{lines}"
        try:
            data = await self._llm.complete_json(system=SYSTEM_PROMPT, user=user, schema_hint={"summary": "string"})
            text = str(data.get("summary", "")).strip()
        except Exception:  # any model failure means: use the deterministic summary
            return template
        if not text or invented_facts(text, source, tenant):
            return template
        return text


# ---- the handoff summary written by the AI model, for the human who takes over ----

HANDOFF_SUMMARY_PROMPT = "handoff_summary_v1"
AI_SUGGESTION_LABEL = "AI suggestion, not approved: "
MAX_PROBLEMS_SHOWN = 4
SUMMARY_SCHEMA_HINT = {
    "summary_en": "two or three sentences",
    "summary_customer_language": "the same summary in the customer's language",
    "suggested_next_step": "one sentence",
}
REGISTER = {
    Language.EN: "plain English",
    Language.AR: "Egyptian Arabic, colloquial",
    Language.MIXED: "Egyptian Arabic, colloquial, English terms may stay in English",
    Language.ARABIZI: "Arabizi (Egyptian Arabic in Latin letters and digits)",
}


class SummaryOutcome(NamedTuple):
    package: HandoffPackage
    status: Literal["ai", "template", "failed"]
    detail: str


def _facts_text(package: HandoffPackage) -> str:
    """Everything the briefing states, as one text the summary is checked against (the AI fields are not facts)."""
    return package.model_dump_json(exclude={"ai_summary", "ai_summary_local", "ai_suggestion", "prompt_version"})


async def add_ai_summary(
    llm: LLMClient, package: HandoffPackage, prompt: PromptTemplate | None = None
) -> SummaryOutcome:
    """The package with an AI-written summary and suggestion, if the model's text adds no fact the package lacks.

    Factual fields are never touched. On any model error, bad output or ungrounded text the package comes back
    unchanged with summary_source "template" (the template summary stays the one shown)."""
    template = prompt or PromptTemplate.load(HANDOFF_SUMMARY_PROMPT)
    register = REGISTER.get(package.language or Language.EN, REGISTER[Language.EN])
    facts = _facts_text(package)
    user = template.render_user(register=register, briefing=facts)
    system = template.system.replace("{{register}}", register)
    try:
        data = await llm.complete_json(system=system, user=user, schema_hint=SUMMARY_SCHEMA_HINT)
    except Exception as exc:  # any model failure means: the template summary
        return SummaryOutcome(package, "failed", f"the model failed ({type(exc).__name__})")
    parts = {k: data.get(k) for k in SUMMARY_SCHEMA_HINT}
    if not all(isinstance(v, str) and v.strip() for v in parts.values()):
        return SummaryOutcome(package, "failed", "the model answered without all three texts")
    texts = {k: str(v).strip() for k, v in parts.items()}
    problems = [p for text in texts.values() for p in check_grounded(text, [facts])]
    if problems:
        return SummaryOutcome(package, "template", "rejected: " + "; ".join(dict.fromkeys(problems))[:200])
    updated = package.model_copy(
        update={
            "ai_summary": texts["summary_en"],
            "ai_summary_local": texts["summary_customer_language"],
            "ai_suggestion": AI_SUGGESTION_LABEL + texts["suggested_next_step"],
            "summary_source": "ai",
            "prompt_version": template.version,
        }
    )
    return SummaryOutcome(updated, "ai", "written by the model and grounded in the briefing")
