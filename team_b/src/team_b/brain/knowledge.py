"""Answering policy questions from the shop's own documents.

OWNER: Track B (fills this module).

Signatures are fixed; the orchestrator calls answer() for every intent of kind "knowledge", and other tracks call
quote_for() to add a policy quote to their own replies (for example the late-delivery policy next to an order status).

Never invent policy: a reply is built only from passages the policy search returned, quoted verbatim with their
citation. A question the search cannot match is asked once more in other words, then answered honestly with an offer
of a colleague, and handed to a person (no_evidence) only the third time in a row (see _no_answer). A search that
cannot answer at all is handed over too (dependency_unavailable); nothing is guessed.
"""

import re
import weakref
from dataclasses import dataclass
from datetime import datetime, timedelta

from team_b.brain.text import normalize
from team_b.brain.turn import Step, TurnContext, locale_of
from team_b.contracts.errors import UpstreamError
from team_b.contracts.evidence import Passage, RetrievalResult
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.trace import EvidenceRef
from team_b.domain.understanding import Language
from team_b.observability import get_logger

log = get_logger(__name__)

CACHE_SECONDS = 60
MAX_QUOTED = 2  # passages quoted in one answer
RELATED_SHARE = 0.6  # a second passage is quoted only when it scores at least this share of the best one
SEARCH_ATTEMPTS = 2  # one retry of a failed read; searching has no side effects


@dataclass(frozen=True)
class _Cached:
    expires: datetime
    passages: tuple[Passage, ...]


# One cache per evidence provider (so a test container never sees another's answers): (tenant, query) -> passages.
_CACHES: "weakref.WeakKeyDictionary[object, dict[tuple[str, str], _Cached]]" = weakref.WeakKeyDictionary()


_WORDS = re.compile(r"\w+")


def _plain(text: str) -> str:
    """The words of `text` normalized (digits, Arabic letter forms, case) with punctuation dropped."""
    return " ".join(_WORDS.findall(normalize(text)))


class SearchUnavailable(Exception):
    """The policy search could not answer (or is not configured)."""


def _cache_of(ctx: TurnContext) -> dict[tuple[str, str], _Cached] | None:
    provider = ctx.deps.evidence
    return None if provider is None else _CACHES.setdefault(provider, {})


def search_text(ctx: TurnContext, query_hint: str | None, *, text: str | None = None) -> str:
    """The question sent to the policy search: the customer's words plus the intent's knowledge_query."""
    return " ".join(part for part in ((text if text is not None else ctx.text).strip(), query_hint) if part)


async def _search(ctx: TurnContext, query: str) -> tuple[Passage, ...]:
    """Passages for `query`, best first. Only non-empty answers are cached, so a later fix is seen at once."""
    provider, cache = ctx.deps.evidence, _cache_of(ctx)
    if provider is None or cache is None:
        raise SearchUnavailable("no policy search is configured")
    key = (ctx.tenant.tenant_id, _plain(query))
    hit = cache.get(key)
    if hit is not None and hit.expires > ctx.now:
        return hit.passages
    result: RetrievalResult | None = None
    for attempt in range(SEARCH_ATTEMPTS):
        try:
            result = await provider.search_knowledge(
                ctx.tenant.tenant_id,
                query,
                request_id=ctx.request_id,
                conversation_id=ctx.session.conversation_id,
            )
            break
        except (UpstreamError, NotImplementedError) as exc:
            code = exc.code if isinstance(exc, UpstreamError) else "NOT_IMPLEMENTED"
            ctx.errors.append(f"policy search failed ({code}), attempt {attempt + 1}")
            log.warning("dependency_error", service="policy_search", code=code, attempt=attempt + 1)
    if result is None:
        raise SearchUnavailable("the policy search did not answer")
    ctx.evidence_empty_reason = result.empty_reason
    if result.passages:
        cache[key] = _Cached(ctx.now + timedelta(seconds=CACHE_SECONDS), result.passages)
    return result.passages


def _record(ctx: TurnContext, passages: tuple[Passage, ...]) -> None:
    """Note the passages on the turn so the trace shows what the reply stands on."""
    have = {e.citation for e in ctx.evidence}
    for p in passages:
        if p.citation not in have:
            ctx.evidence.append(
                EvidenceRef(citation=p.citation, document_id=p.document_id, version=p.version, score=p.score)
            )
    if passages:
        ctx.evidence_empty_reason = None


def _pick(passages: tuple[Passage, ...]) -> tuple[Passage, ...]:
    """The best passage, and one more only when it is nearly as relevant."""
    best = passages[0].score
    return tuple(p for p in passages[:MAX_QUOTED] if p is passages[0] or p.score >= best * RELATED_SHARE)


async def _find(ctx: TurnContext, query_hint: str | None) -> tuple[Passage, ...]:
    """Search with the customer's words; mixed and Arabizi messages get one more try with the normalized text."""
    assistant = ctx.deps.knowledge
    if assistant is not None:  # the AI model writes the query: it understands Arabizi, follow-ups and mixed messages
        query = await assistant.search_query(ctx.text, ctx.session)
        if query:
            found = await _search(ctx, search_text(ctx, query_hint, text=query))
            if found:
                return found
    passages = await _search(ctx, search_text(ctx, query_hint))
    if not passages and ctx.session.language in (Language.MIXED, Language.ARABIZI):
        retry = search_text(ctx, query_hint, text=_plain(ctx.text))
        if retry != search_text(ctx, query_hint):
            passages = await _search(ctx, retry)
    return passages


async def answer(ctx: TurnContext) -> Step:
    """The reply to a policy question: retrieved passages quoted with citations, or an honest "no evidence"."""
    name = ctx.plan.name if ctx.plan else "knowledge"
    spec = ctx.tenant.intents.get(name)
    try:
        passages = await _find(ctx, spec.knowledge_query if spec else None)
    except SearchUnavailable as exc:
        return Step(
            Decision.HANDOFF,
            reason=f"the policy search is unavailable: {exc}",
            reply_key=f"handoff_{EscalationReason.DEPENDENCY_UNAVAILABLE.value}",
            escalation=EscalationReason.DEPENDENCY_UNAVAILABLE,
            awaiting="human",
        )
    if not passages:
        return _no_answer(ctx, name)
    ctx.session.no_evidence_count = 0
    quoted, text = _pick(passages), None
    if ctx.deps.knowledge is not None:
        verdict = await ctx.deps.knowledge.answer(ctx.text, ctx.session, passages, locale_of(ctx))
        if verdict is not None and not verdict.answerable:
            return _no_answer(ctx, name)
        if verdict is not None and verdict.citations:
            quoted = tuple(p for p in passages if p.citation in verdict.citations)[: MAX_QUOTED + 1]
            text = verdict.text or None
    _record(ctx, quoted)
    return Step(
        Decision.ANSWER,
        reason=f"answered {name} from {', '.join(p.citation for p in quoted)}",
        reply_key="policy_answer",
        citations=tuple(p.citation for p in quoted),
        passages=quoted,
        knowledge=True,
        text=text,
    )


def _no_answer(ctx: TurnContext, name: str) -> Step:
    """Nothing in the documents answers the question. The first time the customer is asked to say it another way, the
    second time told honestly that it is not in the policies (a colleague is offered), and only the third time in a row
    is the case handed to a person."""
    session = ctx.session
    session.no_evidence_count += 1
    if session.no_evidence_count == 1:
        return Step(
            Decision.CLARIFY,
            reason=f"no policy passage answers {name}: asked the customer to rephrase",
            reply_key="ask_rephrase",
            awaiting="detail",
        )
    if session.no_evidence_count == 2:
        return Step(
            Decision.ANSWER,
            reason=f"no policy passage answers {name} after a rephrase: said so and offered a colleague",
            reply_key="no_answer_offer",
        )
    return Step(
        Decision.HANDOFF,
        reason=f"no policy passage answers {name} three times in a row",
        reply_key=f"handoff_{EscalationReason.NO_EVIDENCE.value}",
        escalation=EscalationReason.NO_EVIDENCE,
        awaiting="human",
    )


async def quote_for(ctx: TurnContext, query: str) -> list[Passage]:
    """Policy passages relevant to `query`, best first, for quoting verbatim. Empty when nothing reliable is found."""
    try:
        passages = await _search(ctx, query)
    except SearchUnavailable:
        return []
    if not passages:
        return []
    quoted = _pick(passages)  # the best passage, and one more only when it is nearly as relevant
    _record(ctx, quoted)
    return list(quoted)


async def quote_citations(ctx: TurnContext, citations: tuple[str, ...], *, record: bool = True) -> list[Passage]:
    """The passages behind citations already given (a rule's policy sentence, a handoff briefing), word for word.

    A citation the policy store no longer holds (or one that is superseded) is left out, never replaced by a guess."""
    provider = ctx.deps.evidence
    if provider is None:
        return []
    found: list[Passage] = []
    for citation in dict.fromkeys(citations):
        try:
            passage = await provider.get_passage(ctx.tenant.tenant_id, citation)
        except (UpstreamError, NotImplementedError) as exc:
            ctx.errors.append(f"policy passage {citation} unavailable ({type(exc).__name__})")
            continue
        if passage is not None:
            found.append(passage)
    if record:
        _record(ctx, tuple(found))
    return found
