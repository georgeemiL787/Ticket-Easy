"""The AI model's part in answering policy questions (TEAM_B_LLM_KNOWLEDGE=1).

Two steps around the policy search, both optional and both checked by code:
- search_query: turns a messy message (Arabizi, a follow-up, mixed languages) into one standalone query. Its text only
  steers the search; it never reaches the customer.
- answer: words the reply to the customer's actual question from the retrieved passages, in the customer's register,
  and names the passages it used. The words are accepted only if check_grounded finds no number, date, id, amount or
  link that the passages (or the question) do not contain, and only passages that were really retrieved can be cited.
  Otherwise the caller falls back to quoting the passages word for word. The passages are always appended verbatim.
"""

from collections.abc import Sequence
from typing import NamedTuple, Protocol

from team_b.brain.composer import check_grounded, promises_handover
from team_b.brain.llm_nlu import PromptTemplate
from team_b.brain.redaction import redact
from team_b.brain.rewrite import REGISTER
from team_b.contracts.evidence import Passage
from team_b.domain.session import SessionState
from team_b.domain.understanding import Locale
from team_b.observability import get_logger
from team_b.ports import LLMClient

log = get_logger(__name__)
QUERY_PROMPT = "knowledge_query_v1"
ANSWER_PROMPT = "knowledge_answer_v1"
HISTORY_MESSAGES = 6
MAX_QUERY_CHARS = 300
MAX_ANSWER_CHARS = 1200


class KnowledgeAnswer(NamedTuple):
    answerable: bool
    text: str  # the model's reply when answerable and grounded, else ""
    citations: tuple[str, ...]  # retrieved passages the reply relies on
    detail: str


class KnowledgeAssistant(Protocol):
    async def search_query(self, message: str, session: SessionState | None) -> str | None: ...

    async def answer(
        self, message: str, session: SessionState | None, passages: Sequence[Passage], locale: Locale
    ) -> KnowledgeAnswer | None: ...


def _history(session: SessionState | None) -> str:
    if session is None:
        return "(no earlier messages)"
    recent = session.history[-HISTORY_MESSAGES:]
    return "\n".join(f"{m.role}: {redact(m.text)}" for m in recent) or "(no earlier messages)"


class LLMKnowledgeAssistant:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm
        self._query = PromptTemplate.load(QUERY_PROMPT)
        self._answer = PromptTemplate.load(ANSWER_PROMPT)

    @property
    def prompt_version(self) -> str:
        return f"{self._query.version}+{self._answer.version}"

    async def search_query(self, message: str, session: SessionState | None) -> str | None:
        user = self._query.render_user(history=_history(session), message=redact(message))
        try:
            data = await self._llm.complete_json(
                system=self._query.system, user=user, schema_hint={"query": "english words arabic words"}
            )
        except Exception as exc:  # the plain search still works
            log.warning("knowledge_query_failed", reason=type(exc).__name__)
            return None
        query = data.get("query")
        if not isinstance(query, str) or not query.strip():
            return None
        return query.strip()[:MAX_QUERY_CHARS]

    async def answer(
        self, message: str, session: SessionState | None, passages: Sequence[Passage], locale: Locale
    ) -> KnowledgeAnswer | None:
        """None when the model failed (the caller quotes the passages); otherwise its verdict on the question."""
        shown = "\n".join(f"[{p.citation}] {p.text}" for p in passages)
        user = self._answer.render_user(history=_history(session), message=redact(message), passages=shown)
        system = self._answer.system.replace("{{register}}", REGISTER[locale])
        try:
            data = await self._llm.complete_json(
                system=system,
                user=user,
                schema_hint={"answerable": True, "answer": "the reply", "citations": ["passage id"]},
            )
        except Exception as exc:
            log.warning("knowledge_answer_failed", reason=type(exc).__name__)
            return None
        if data.get("answerable") is False:
            return KnowledgeAnswer(False, "", (), "the model found no answer in the passages")
        text = data.get("answer")
        raw = data.get("citations")
        if not isinstance(text, str) or not text.strip() or not isinstance(raw, list):
            return None
        known = {p.citation for p in passages}
        cited = tuple(dict.fromkeys(c for c in raw if isinstance(c, str) and c in known))
        if not cited:
            return None
        text = text.strip()
        problems = check_grounded(text, [*(p.text for p in passages), message])
        if promises_handover(text):
            problems.append("promises a colleague, but no case is opened by an answer")
        if problems or len(text) > MAX_ANSWER_CHARS:
            return KnowledgeAnswer(True, "", cited, "; ".join(problems[:4]) or "the reply is too long")
        return KnowledgeAnswer(True, text, cited, "worded by the model")
