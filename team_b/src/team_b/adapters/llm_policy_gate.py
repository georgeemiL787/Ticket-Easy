"""A policy agent in front of the rule checker: an AI model reads the retrieved policy and picks the route.

LLMPolicyGate wraps the real PolicyGate. For an action that changes something it finds the policy passages that
concern the action, asks the model for allow / require_human / deny with its reasoning, and returns the STRICTER of
the model's route and the rule checker's. So the model can send a case to a person or refuse it where the rules
alone would allow it, but it can never turn a deny or a require_human into an allow. If the model or the search
cannot answer, an allowed write goes to a person (fail closed). A recorded human approval still turns the model's
require_human into allow, never a deny. Reads and denials pass through untouched.
"""

import json
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from team_b.brain.composer import check_grounded
from team_b.brain.llm_nlu import PromptTemplate
from team_b.brain.redaction import redact
from team_b.contracts.evidence import Passage
from team_b.contracts.policy import CheckActionRequest, LocalizedText, PolicyDecision, PolicyEffect
from team_b.observability import get_logger
from team_b.ports import EvidenceProvider, LLMClient, PolicyGate

log = get_logger(__name__)
PROMPT = "policy_agent_v1"
STRICTNESS: Final[dict[str, int]] = {"allow": 0, "require_human": 1, "deny": 2}
SCHEMA_HINT = {
    "decision": "allow|require_human|deny",
    "reasoning": "short reasoning",
    "citations": ["passage id"],
    "message_en": "",
    "message_ar": "",
}
TOP_K = 6
FACT_KEYS_IN_QUERY = ("status", "days_since_delivery", "days_late", "item", "category")


class _Verdict(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decision: PolicyEffect
    reasoning: str = ""
    citations: list[str] = Field(default_factory=list)
    message_en: str = ""
    message_ar: str = ""


def _stricter(a: PolicyEffect, b: PolicyEffect) -> PolicyEffect:
    return a if STRICTNESS[a] >= STRICTNESS[b] else b


def _search_query(request: CheckActionRequest) -> str:
    words = request.action.replace("_", " ")
    text_args = [str(v) for v in request.arguments.values() if isinstance(v, str) and 2 < len(v) < 120]
    facts = [f"{k} {request.facts[k]}" for k in FACT_KEYS_IN_QUERY if k in request.facts]
    return " ".join([f"{words} policy", *text_args, *facts])


class LLMPolicyGate:
    def __init__(
        self, inner: PolicyGate, evidence: EvidenceProvider, llm: LLMClient, prompt: PromptTemplate | None = None
    ) -> None:
        self._inner = inner
        self._evidence = evidence
        self._llm = llm
        self._prompt = prompt or PromptTemplate.load(PROMPT)

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def check_action(self, request: CheckActionRequest) -> PolicyDecision:
        decision = await self._inner.check_action(request)
        if request.tool.operation_kind == "read" or decision.decision == "deny":
            return decision
        try:
            passages = await self._passages(request)
            if not passages:  # nothing in the policy concerns this action: the rule engine's answer stands
                return decision
            verdict = await self._ask(request, decision, passages)
        except Exception as exc:  # model down, search down, nonsense twice: an allowed write waits for a person
            log.warning("policy_agent_failed", reason=type(exc).__name__, action=request.action)
            return self._unavailable(request, decision)
        return self._merge(request, decision, verdict, passages)

    async def _passages(self, request: CheckActionRequest) -> tuple[Passage, ...]:
        result = await self._evidence.search_knowledge(
            request.tenant_id,
            _search_query(request),
            request_id=request.request_id,
            conversation_id=request.conversation_id,
            top_k=TOP_K,
        )
        return result.passages

    async def _ask(
        self, request: CheckActionRequest, decision: PolicyDecision, passages: tuple[Passage, ...]
    ) -> _Verdict:
        shown = "\n".join(f"[{p.passage_id}] {p.text}" for p in passages)
        user = self._prompt.render_user(
            action=request.action,
            operation=request.tool.operation_kind,
            risk=request.tool.risk,
            engine=f"{decision.decision} ({decision.reason_code})",
            arguments=redact(json.dumps(request.arguments, ensure_ascii=False, default=str)),
            facts=redact(json.dumps(request.facts, ensure_ascii=False, default=str)),
            passages=shown,
        )
        last: Exception | None = None
        for _ in range(2):  # one repair attempt for an answer that cannot be used
            try:
                data = await self._llm.complete_json(system=self._prompt.system, user=user, schema_hint=SCHEMA_HINT)
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
                "rationale": verdict.reasoning[:600] or "the policy agent could not allow this on the policy text",
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
    def _unavailable(request: CheckActionRequest, engine: PolicyDecision) -> PolicyDecision:
        if engine.decision != "allow" or request.human_approval is not None:
            return engine
        return engine.model_copy(
            update={
                "decision": "require_human",
                "reason_code": "POLICY_AGENT_UNAVAILABLE",
                "rationale": "the policy agent could not review this action, so a person must",
                "user_message": None,
            }
        )
