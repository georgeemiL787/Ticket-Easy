"""Reply plans and the reply writer: replies are written from a plan, not reworded from a template.

Every step of a turn has a ReplyPlan (plan_of): the decision, what the reply is for, the facts it may hold (only values
code verified), the question it must ask, and the citations. Code that makes a Step does not change: the plan is read
off its reply key and values, or chosen by the orchestrator (Reply move, including a "free" reply to an off-topic
message). With an AI model for the job "writer" (TEAM_B_LLM_REWRITE=1) the ReplyWriter writes the words freely from the
plan, in the customer's language and tone, reacting to what they said (prompts/writer_v1.md).

Code is the gate. The text is used only if check_grounded finds no number, date, id, amount, link, citation or time
word that the fallback text and the plan's facts do not hold (and none of theirs dropped), it promises no colleague the
plan does not, a confirmation still asks yes or no, and a plan with a question still asks it. A text that fails goes
back to the model once with the problems named (status "retried" if the second one passes); if that fails too, or the
model is down, the fixed template is sent (status "template"). A free reply must also pass the small-talk rules (no
orders, money, policies, promises or colleagues). What the shop executed ("done", the reference number) is never written
by the model, and policy quotes are added after the reply, never shown to the model.
"""

from typing import TYPE_CHECKING, Literal, NamedTuple, Protocol

from team_b.brain.composer import check_grounded, promises_handover
from team_b.brain.llm_nlu import PromptTemplate
from team_b.brain.redaction import redact
from team_b.brain.rewrite import MAX_GROWTH, MAX_PROBLEMS_SHOWN, REGISTER
from team_b.brain.smalltalk import problems_with as small_talk_problems
from team_b.domain.agent import ReplyPlan
from team_b.domain.decision import Decision
from team_b.domain.understanding import Locale
from team_b.observability import get_logger
from team_b.ports import LLMClient

if TYPE_CHECKING:  # brain.turn imports this module (Deps.writer)
    from team_b.brain.turn import Step

log = get_logger(__name__)
PROMPT = "writer_v1"
SCHEMA_HINT = {"text": "the final reply"}
WRITABLE = frozenset(
    {Decision.ANSWER, Decision.CLARIFY, Decision.VERIFY_IDENTITY, Decision.CONFIRM, Decision.REFUSE, Decision.HANDOFF}
)  # never EXECUTE: "done" and the reference number are the shop's word, not the model's
PURPOSES: tuple[tuple[str, str], ...] = (
    ("greeting", "greet the customer and offer help"),
    ("thanks", "say you are welcome and offer more help"),
    ("ask_rephrase", "ask the customer what they need help with"),
    ("ask_", "ask the customer for one missing detail"),
    ("confirm_action", "ask the customer to confirm the action, yes or no"),
    ("confirm_again", "ask again for a clear yes or no"),
    ("order_status", "tell the customer where their order is"),
    ("policy_answer", "answer the policy question (the quoted policy text is added after your reply)"),
    ("policy_refusal", "tell the customer no, and why, as the rules say it"),
    ("action_failed", "tell the customer it could not be done and nothing was changed"),
    ("action_cancelled", "say the action was cancelled and nothing was done"),
    ("disambiguate", "ask the customer which one they mean"),
    ("handoff", "tell the customer a colleague takes over"),
    ("lookup_failed", "say honestly that the information could not be read now"),
    ("order_not_found", "say the order number was not found and ask them to check it"),
    ("out_of_scope", "say this is not something you can do here and offer a colleague"),
)
QUESTION_FOR = {
    Decision.CONFIRM: "ask for a clear yes or no",
    Decision.CLARIFY: "ask the question this reply is for",
    Decision.VERIFY_IDENTITY: "ask for the detail that proves who they are",
}


def purpose_of(step: "Step") -> str:
    for prefix, purpose in PURPOSES:
        if step.reply_key.startswith(prefix):
            return purpose
    return f"{step.decision.value} the customer's request"


def plan_of(step: "Step") -> ReplyPlan:
    """The ReplyPlan of a step: the plan the orchestrator chose, else one read off the step's key and values."""
    if step.plan is not None:
        return step.plan
    must = QUESTION_FOR.get(step.decision)
    return ReplyPlan(
        decision=step.decision,
        intent=purpose_of(step),
        reply_key=step.reply_key,
        facts={k: str(v) for k, v in step.values.items()},
        must_ask=must,
        citations=list(step.citations),
    )


class WriteResult(NamedTuple):
    text: str  # what to send: the written reply if it passed, else the template
    status: Literal["used", "retried", "template"]
    detail: str


class Writer(Protocol):
    @property
    def prompt_version(self) -> str: ...

    async def write(
        self,
        plan: ReplyPlan,
        template: str,
        locale: Locale,
        *,
        message: str = "",
        history: str = "",
        shop: str = "",
    ) -> WriteResult: ...


def _asks(text: str) -> bool:
    return "?" in text or "؟" in text


def problems_in(plan: ReplyPlan, template: str, candidate: str, shop: str) -> list[str]:
    """Why `candidate` must not be sent; empty means it may."""
    if len(candidate) > MAX_GROWTH * len(template) + 80 and not plan.free:
        return ["the reply is much longer than the plan needs"]
    problems = check_grounded(candidate, [template, *plan.facts.values()], keep=[template])
    if promises_handover(candidate) and not promises_handover(template) and plan.decision is not Decision.HANDOFF:
        problems.append("promises a colleague the plan does not")
    if plan.must_ask is not None and not _asks(candidate):
        problems.append("no longer asks the question the plan needs answered")
    if plan.free:
        problems += small_talk_problems(candidate, template, shop)
    return problems


class ReplyWriter:
    def __init__(self, llm: LLMClient, prompt: PromptTemplate | None = None) -> None:
        self._llm = llm
        self._prompt = prompt or PromptTemplate.load(PROMPT)

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def _ask(
        self, plan: ReplyPlan, template: str, locale: Locale, message: str, history: str, problems: list[str]
    ) -> str | None:
        register = REGISTER[locale]
        facts = "\n".join(f"- {k}: {redact(str(v))}" for k, v in plan.facts.items()) or "(none)"
        user = self._prompt.render_user(
            register=register,
            history=redact(history) or "(no earlier messages)",
            message=redact(message) or "(not available)",
            decision=plan.decision.value,
            intent=plan.intent or "(none)",
            facts=facts,
            must_ask=plan.must_ask or "(none)",
            free="yes" if plan.free else "no",
            text=redact(template),
        )
        if problems:
            user += (
                f"\n\nYour last reply had these problems: {'; '.join(problems[:MAX_PROBLEMS_SHOWN])}. Write it again."
            )
        system = self._prompt.system.replace("{{register}}", register)
        try:
            data = await self._llm.complete_json(system=system, user=user, schema_hint=SCHEMA_HINT)
        except Exception as exc:  # any model failure means: send the template
            log.warning("writer_failed", reason=type(exc).__name__)
            return None
        text = data.get("text")
        return text.strip() if isinstance(text, str) and text.strip() else None

    async def write(
        self,
        plan: ReplyPlan,
        template: str,
        locale: Locale,
        *,
        message: str = "",
        history: str = "",
        shop: str = "",
    ) -> WriteResult:
        """The written reply if it passes the checks (after one retry with the problems), else the template."""
        problems: list[str] = []
        for attempt in (1, 2):
            candidate = await self._ask(plan, template, locale, message, history, problems)
            if candidate is None:
                return WriteResult(template, "template", "the model failed or answered without text")
            problems = problems_in(plan, template, candidate, shop)
            if not problems:
                return WriteResult(candidate, "used" if attempt == 1 else "retried", "written")
        return WriteResult(template, "template", "; ".join(problems[:MAX_PROBLEMS_SHOWN]))
