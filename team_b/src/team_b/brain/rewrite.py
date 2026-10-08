"""Optional AI rewording of replies, with a fact check that guarantees the model adds nothing.

The agent's replies come from templates. When TEAM_B_LLM_REWRITE=1 the model may reword a reply so it sounds more
natural, but its text is only used if check_grounded finds no number, date, id, amount, link or citation that is not in
the original (and none of the original's was dropped). Otherwise, and on any model error, the original template text is
sent. Only low-stakes replies are eligible (see REWRITABLE): never a confirmation, an execution result, a refusal or a
handoff, where one changed word could mislead. Policy quotes and citations are not part of the text sent to the model;
the composer appends them after the rewrite.
"""

from collections.abc import Mapping
from typing import Literal, NamedTuple, Protocol

from team_b.brain.composer import check_grounded, promises_handover
from team_b.brain.llm_nlu import PromptTemplate
from team_b.brain.redaction import redact
from team_b.domain.decision import Decision
from team_b.domain.understanding import Locale
from team_b.observability import get_logger
from team_b.ports import LLMClient

log = get_logger(__name__)
REWRITE_PROMPT = "rewrite_v2"
REWRITABLE = frozenset(
    {
        Decision.ANSWER,
        Decision.CLARIFY,
        Decision.VERIFY_IDENTITY,
        Decision.CONFIRM,
        Decision.REFUSE,
        Decision.HANDOFF,
    }
)  # never EXECUTE: "done" and the reference number are the shop's word, not the model's
ROLE: Mapping[Decision, str] = {
    Decision.CONFIRM: "You are asking the customer to confirm: keep it a clear yes/no question about this action.",
    Decision.REFUSE: "You are telling the customer no. Say why with the draft's facts. Do not promise a colleague "
    "unless the draft does; if the draft offers one, offer it.",
    Decision.HANDOFF: "You are telling the customer a colleague takes over. Say that, warmly, and nothing about "
    "outcomes: never say anything is approved, refunded or decided.",
}
MAX_GROWTH = 3  # a rewrite longer than this many times the original (plus slack) is not a rewording
MAX_PROBLEMS_SHOWN = 4
REGISTER: Mapping[Locale, str] = {
    Locale.EN: "plain, warm, polite English",
    Locale.AR: "Egyptian Arabic, colloquial and polite, not formal",
    Locale.ARABIZI: "Arabizi (Egyptian Arabic in Latin letters and digits), colloquial and polite",
}
SCHEMA_HINT = {"text": "the reworded reply"}


def _asks(text: str) -> bool:
    return "?" in text or "؟" in text


class RewriteResult(NamedTuple):
    text: str  # what to send: the rewrite if it passed, else the original
    status: Literal["used", "rejected", "failed"]
    detail: str


class Rewriter(Protocol):
    async def reword(
        self,
        text: str,
        locale: Locale,
        facts: Mapping[str, str],
        *,
        message: str = "",
        history: str = "",
        decision: Decision | None = None,
    ) -> RewriteResult: ...


class LLMRewriter:
    def __init__(self, llm: LLMClient, prompt: PromptTemplate | None = None) -> None:
        self._llm = llm
        self._prompt = prompt or PromptTemplate.load(REWRITE_PROMPT)

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def reword(
        self,
        text: str,
        locale: Locale,
        facts: Mapping[str, str],
        *,
        message: str = "",
        history: str = "",
        decision: Decision | None = None,
    ) -> RewriteResult:
        """The reworded text if it is grounded, else `text` unchanged, with the reason in `detail`."""
        shown = "\n".join(f"- {key}: {redact(str(value))}" for key, value in facts.items()) or "(none)"
        user = self._prompt.render_user(
            register=REGISTER[locale],
            facts=shown,
            text=redact(text),
            message=redact(message) or "(not available)",
            history=redact(history) or "(no earlier messages)",
        )
        role = ROLE.get(decision, "Reword the draft.") if decision is not None else "Reword the draft."
        system = self._prompt.system.replace("{{register}}", REGISTER[locale]).replace("{{role}}", role)
        try:
            data = await self._llm.complete_json(system=system, user=user, schema_hint=SCHEMA_HINT)
        except Exception as exc:  # any model failure means: send the template
            log.warning("rewrite_failed", reason=type(exc).__name__)
            return RewriteResult(text, "failed", f"the model failed ({type(exc).__name__})")
        candidate = data.get("text")
        if not isinstance(candidate, str) or not candidate.strip():
            return RewriteResult(text, "failed", "the model answered without text")
        candidate = candidate.strip()
        if len(candidate) > MAX_GROWTH * len(text) + 80:
            return RewriteResult(text, "rejected", "the rewrite is much longer than the original")
        problems = check_grounded(candidate, [text, *map(str, facts.values())], keep=[text])
        if promises_handover(candidate) and not promises_handover(text) and decision is not Decision.HANDOFF:
            problems.append("promises a colleague the draft does not")  # only a reply that opens a case may say so
        if decision is Decision.CONFIRM and not _asks(candidate):
            problems.append("no longer asks for a yes or no")
        if problems:
            return RewriteResult(text, "rejected", "; ".join(problems[:MAX_PROBLEMS_SHOWN]))
        return RewriteResult(candidate, "used", "reworded")
