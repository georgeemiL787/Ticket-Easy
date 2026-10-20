"""The knowledge agent (job "knowledge"): answers policy and FAQ questions only from passages it found.

research() does three things around the policy search, each a small checked step:
  1. write the search (the model turns Arabizi, follow-ups and mixed messages into a standalone query; on any model
     failure the customer's own words are searched);
  2. search; if nothing came back, or only passages scoring below the business's knowledge.min_score, write a DIFFERENT
     search once (other words, English words for an Arabizi question, the policy topic name) and search again;
  3. answer from the passages, naming the ones used, or say what was missing. It never guesses: no passage, no answer.

It is the runner of the SearchPolicy move (agent mode: NotFound goes back to the orchestrator, which may ask a
clarifying question or offer a person) and of knowledge.answer (usual flow: NotFound keeps today's sequence, ask to
rephrase, offer a colleague, hand over the third time). Every safeguard stays: only retrieved passages can be cited, an
older version of a document is never used next to a newer one, the worded answer must pass check_grounded against the
passages (else the passages are quoted word for word), and the passages are appended after the reply, never sent through
any rewriter.
"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Final

from agent_service.brain.composer import check_grounded, promises_handover
from agent_service.brain.knowledge_llm import MAX_ANSWER_CHARS, MAX_QUERY_CHARS, _history
from agent_service.brain.llm_nlu import PromptTemplate
from agent_service.brain.redaction import redact
from agent_service.brain.rewrite import REGISTER
from agent_service.contracts.evidence import Passage
from agent_service.domain.session import SessionState
from agent_service.domain.understanding import Locale
from agent_service.observability import get_logger
from agent_service.ports import LLMClient

log = get_logger(__name__)
PROMPT: Final = "knowledge_agent_v1"
Search = Callable[[str], Awaitable[Sequence[Passage]]]


@dataclass(frozen=True)
class NotFound:
    """The documents do not answer the question. `what_was_missing` is a few plain words for the orchestrator."""

    what_was_missing: str
    queries: tuple[str, ...] = ()


@dataclass(frozen=True)
class Found:
    """Passages that answer the question, and (when the model worded it and the words passed the check) the answer."""

    passages: tuple[Passage, ...]
    text: str = ""  # the worded answer; empty: quote the passages word for word
    citations: tuple[str, ...] = ()  # the retrieved passages the answer relies on
    queries: tuple[str, ...] = ()
    detail: str = ""
    retried: bool = False  # the first search found nothing useful and a different one was written
    steps: list[str] = field(default_factory=list)


def latest_versions(passages: Sequence[Passage]) -> tuple[Passage, ...]:
    """Drop a passage when a newer version of the same document is among them (superseded text is never cited)."""
    newest: dict[str, str] = {}
    for p in passages:
        if p.document_id not in newest or p.version > newest[p.document_id]:
            newest[p.document_id] = p.version
    return tuple(p for p in passages if p.version == newest[p.document_id])


class KnowledgeAgent:
    def __init__(self, llm: LLMClient, prompt: PromptTemplate | None = None) -> None:
        self._llm = llm
        self._prompt = prompt or PromptTemplate.load(PROMPT)

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def _ask(
        self,
        task: str,
        message: str,
        session: SessionState | None,
        *,
        searches: str = "(none)",
        passages: str = "(none)",
        locale: Locale = Locale.EN,
    ) -> dict[str, object] | None:
        system = self._prompt.system.replace("{{register}}", REGISTER[locale])
        user = self._prompt.render_user(
            task=task, history=_history(session), message=redact(message), searches=searches, passages=passages
        )
        try:
            return await self._llm.complete_json(system=system, user=user, schema_hint={"query": "words"})
        except Exception as exc:  # the plain search still works
            log.warning("knowledge_agent_failed", task=task, reason=type(exc).__name__)
            return None

    async def _query(
        self, task: str, message: str, session: SessionState | None, searches: str = "(none)"
    ) -> str | None:
        data = await self._ask(task, message, session, searches=searches)
        query = data.get("query") if data else None
        return query.strip()[:MAX_QUERY_CHARS] if isinstance(query, str) and query.strip() else None

    async def research(
        self,
        message: str,
        session: SessionState | None,
        locale: Locale,
        search: Search,
        *,
        min_score: float = 0.0,
        first_query: str | None = None,
    ) -> Found | NotFound:
        """Search (and once more, differently, if needed), then answer from the passages or say what is missing.

        `search` raises whatever the policy search raises when it is down: the caller decides what that means."""
        tried: list[str] = []
        steps: list[str] = []

        async def look(query: str) -> tuple[Passage, ...]:
            tried.append(query)
            return latest_versions([p for p in await search(query) if p.score >= min_score])

        first: str | None = first_query  # the orchestrator may have written the search already
        if first:
            steps.append("searched the orchestrator's query")
        else:
            first = await self._query("query", message, session)
            steps.append("wrote a search" if first else "model gave no search: the customer's words are searched")
        passages = await look(first or message)
        retried = False
        if not passages:
            retried = True
            seen = "\n".join(f"- {q}: nothing useful came back" for q in tried)
            again = await self._query("requery", message, session, seen)
            if again and again not in tried:
                steps.append("wrote a different search")
                passages = await look(again)
        if not passages:
            return NotFound("the policy search found nothing for: " + "; ".join(tried), tuple(tried))

        shown = "\n".join(f"[{p.citation}] {p.text}" for p in passages)
        data = await self._ask("answer", message, session, passages=shown, locale=locale)
        found = Found(passages, queries=tuple(tried), retried=retried, steps=steps)
        if data is None:
            return Found(passages, queries=found.queries, retried=retried, steps=steps, detail="model failed: quoted")
        if data.get("answerable") is False:
            missing = data.get("missing")
            return NotFound(
                str(missing).strip()[:200]
                if isinstance(missing, str) and missing.strip()
                else "the passages do not say",
                tuple(tried),
            )
        text, raw = data.get("answer"), data.get("citations")
        known = {p.citation for p in passages}
        cited = (
            tuple(dict.fromkeys(c for c in raw if isinstance(c, str) and c in known)) if isinstance(raw, list) else ()
        )
        if not isinstance(text, str) or not text.strip() or not cited:
            return Found(
                passages, queries=found.queries, retried=retried, steps=steps, detail="unusable answer: quoted"
            )
        text = text.strip()
        problems = check_grounded(text, [*(p.text for p in passages), message])
        if promises_handover(text):
            problems.append("promises a colleague, but no case is opened by an answer")
        if problems or len(text) > MAX_ANSWER_CHARS:
            detail = "; ".join(problems[:4]) or "the reply is too long"
            return Found(passages, "", cited, found.queries, f"not used: {detail}", retried, steps)
        return Found(passages, text, cited, found.queries, "worded by the model", retried, steps)
