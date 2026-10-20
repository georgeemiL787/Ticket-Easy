"""The reviewer (job "reviewer", TEAM_B_LLM_REVIEW=1): a second pair of eyes that can only make things stricter.

SAFETY-CRITICAL. Two checks, both after code has done its own:

review_reply(plan, reply, message, passages): the writer's text already passed check_grounded. Does it match the plan's
decision (nothing "done" before it is done, no promise nobody decided), answer what the customer asked, and is every
policy statement backed by a passage? Returns ok or the problems. A reply that is blocked goes back to the writer once
with those problems; if it is blocked again, or the reviewer cannot answer (fail closed), the fixed template is sent.

review_action(request, rule_decision, passages): for a write, before the customer is asked to confirm. The model reads
the shop's own policy passages and picks allow / require_human / deny; the STRICTER of its route and the rule checker's
wins, so it can send a case to a person or refuse it where the rules would allow it, but it can never turn a deny or a
require_human into an allow. An allow or deny that cites no retrieved passage becomes require_human (a low-risk allow
needs none); if the reviewer cannot answer, an allowed write becomes require_human POLICY_AGENT_UNAVAILABLE. A recorded
human approval still turns the model's require_human into allow, never a deny. (This is the former policy agent of
adapters/llm_policy_gate.py, moved here; the gate now calls it.)

blocks_handover: a hand-over that lists nothing that was tried is sent back to the planner once per turn; the
second time it goes through (the forced cases never wait for this: they are decided by the front stages).
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent_service.contracts.evidence import Passage
from agent_service.contracts.policy import CheckActionRequest, LocalizedText, PolicyDecision, PolicyEffect
from agent_service.domain.agent import HandOver, ReplyPlan
from agent_service.handoff.redaction import redact
from agent_service.observability import get_logger
from agent_service.ports import LLMClient
from agent_service.replies.composer import check_grounded
from agent_service.understanding.ai_reader import PromptTemplate

log = get_logger(__name__)
PROMPT: Final = "reviewer_v1"
STRICTNESS: Final[dict[str, int]] = {"allow": 0, "require_human": 1, "deny": 2}
ACTION_HINT = {
    "decision": "allow|require_human|deny",
    "reasoning": "short reasoning",
    "citations": ["passage id"],
    "message_en": "",
    "message_ar": "",
}
REPLY_HINT = {"ok": True, "problems": ["what is wrong"]}
MAX_PROBLEMS = 4


class _Verdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decision: PolicyEffect
    reasoning: str = ""
    citations: list[str] = Field(default_factory=list)
    message_en: str = ""
    message_ar: str = ""


class _ReplyVerdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool
    problems: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ReplyVerdict:
    ok: bool
    problems: tuple[str, ...] = ()
    failed: bool = False  # the reviewer could not answer: the caller treats the reply as blocked (fail closed)

    @property
    def detail(self) -> str:
        if self.failed:
            return "the reviewer could not answer"
        return "ok" if self.ok else "; ".join(self.problems[:MAX_PROBLEMS])


@dataclass
class HandoverWatch:
    """Per turn: has a hand-over with an empty list of what was tried been sent back already?"""

    sent_back: bool = field(default=False)


def _stricter(a: PolicyEffect, b: PolicyEffect) -> PolicyEffect:
    return a if STRICTNESS[a] >= STRICTNESS[b] else b


class Reviewer:
    def __init__(self, llm: LLMClient, prompt: PromptTemplate | None = None) -> None:
        self._llm = llm
        self._prompt = prompt or PromptTemplate.load(PROMPT)

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    def _user(self, task: str, **values: str) -> str:
        blank = dict.fromkeys(
            (
                "message", "action", "operation", "risk", "engine", "arguments", "facts", "decision", "intent",
                "plan_facts", "reply", "passages",
            ),
            "(none)",
        )  # fmt: skip
        return self._prompt.render_user(task=task, **{**blank, **values})

    # ---- the reply ----

    async def review_reply(
        self, plan: ReplyPlan, reply: str, customer_message: str, passages: Sequence[Passage] = ()
    ) -> ReplyVerdict:
        facts = "\n".join(f"- {k}: {redact(str(v))}" for k, v in plan.facts.items()) or "(none)"
        user = self._user(
            "reply",
            message=redact(customer_message) or "(not available)",
            decision=plan.decision.value,
            intent=plan.intent or "(none)",
            plan_facts=facts,
            reply=redact(reply),
            passages="\n".join(f"[{p.citation}] {p.text}" for p in passages) or "(none)",
        )
        try:
            data = await self._llm.complete_json(system=self._prompt.system, user=user, schema_hint=REPLY_HINT)
            verdict = _ReplyVerdict.model_validate(data)
        except Exception as exc:  # no answer is no approval
            log.warning("review_reply_failed", reason=type(exc).__name__)
            return ReplyVerdict(False, (), failed=True)
        problems = tuple(p.strip() for p in verdict.problems if p.strip())
        if not verdict.ok and not problems:
            problems = ("the reviewer found a problem",)
        return ReplyVerdict(verdict.ok, problems)

    # ---- the action ----

    async def review_action(
        self, request: CheckActionRequest, rule_decision: PolicyDecision, passages: Sequence[Passage]
    ) -> PolicyDecision:
        """The route for a write: the stricter of the reviewer's and the rule checker's. Never loosens."""
        try:
            verdict = await self._ask_action(request, rule_decision, passages)
        except Exception as exc:  # model down, nonsense twice: an allowed write waits for a person
            log.warning("reviewer_failed", reason=type(exc).__name__, action=request.action)
            return self.unavailable(request, rule_decision)
        return self._merge(request, rule_decision, verdict, tuple(passages))

    async def _ask_action(
        self, request: CheckActionRequest, decision: PolicyDecision, passages: Sequence[Passage]
    ) -> _Verdict:
        user = self._user(
            "action",
            action=request.action,
            operation=request.tool.operation_kind,
            risk=request.tool.risk,
            engine=f"{decision.decision} ({decision.reason_code})",
            arguments=redact(json.dumps(request.arguments, ensure_ascii=False, default=str)),
            facts=redact(json.dumps(request.facts, ensure_ascii=False, default=str)),
            passages="\n".join(f"[{p.passage_id}] {p.text}" for p in passages),
        )
        last: Exception | None = None
        for _ in range(2):  # one repair attempt for an answer that cannot be used
            try:
                data = await self._llm.complete_json(system=self._prompt.system, user=user, schema_hint=ACTION_HINT)
                return _Verdict.model_validate(data)
            except (ValidationError, ValueError) as exc:
                last = exc
        raise last if last is not None else RuntimeError("no verdict")

    def _merge(
        self, request: CheckActionRequest, engine: PolicyDecision, verdict: _Verdict, passages: tuple[Passage, ...]
    ) -> PolicyDecision:
        known = {p.passage_id for p in passages}
        cited = tuple(c for c in dict.fromkeys(verdict.citations) if c in known)
        agent: PolicyEffect = verdict.decision
        uncited_low_risk_allow = agent == "allow" and request.tool.risk == "low"  # e.g. opening a support ticket
        if agent != "require_human" and not cited and not uncited_low_risk_allow:
            agent = "require_human"  # an allow or deny that cites nothing is not trusted
        if agent == "require_human" and request.human_approval is not None:
            agent = "allow"  # a person already reviewed it; a deny would have stayed a deny
        final = _stricter(engine.decision, agent)
        if final == engine.decision:  # the model was equal or more permissive: the rule engine's route stands
            return engine.model_copy(update={"citations": tuple(dict.fromkeys([*engine.citations, *cited]))})
        sources = [p.text for p in passages]
        sources += [json.dumps(request.facts, default=str), json.dumps(request.arguments, default=str)]
        return engine.model_copy(
            update={
                "decision": final,
                "reason_code": "POLICY_AGENT_" + final.upper(),
                "rationale": verdict.reasoning[:600] or "the reviewer could not allow this on the policy text",
                "citations": tuple(dict.fromkeys([*engine.citations, *cited])),
                "user_message": self._message(verdict, sources),
            }
        )

    @staticmethod
    def _message(verdict: _Verdict, sources: list[str]) -> LocalizedText | None:
        en, ar = verdict.message_en.strip(), verdict.message_ar.strip()
        if not en or not ar:
            return None
        if check_grounded(en, sources) or check_grounded(ar, sources):
            return None  # it named a number or date that the policy and the facts do not contain
        return LocalizedText(en=en, ar=ar)

    @staticmethod
    def unavailable(request: CheckActionRequest, engine: PolicyDecision) -> PolicyDecision:
        if engine.decision != "allow" or request.human_approval is not None:
            return engine
        return engine.model_copy(
            update={
                "decision": "require_human",
                "reason_code": "POLICY_AGENT_UNAVAILABLE",
                "rationale": "the reviewer could not check this action, so a person must",
                "user_message": None,
            }
        )


def blocks_handover(move: HandOver, watch: HandoverWatch) -> bool:
    """A hand-over that lists nothing tried goes back to the planner once per turn, then it goes through."""
    if move.tried or watch.sent_back:
        return False
    watch.sent_back = True
    return True
