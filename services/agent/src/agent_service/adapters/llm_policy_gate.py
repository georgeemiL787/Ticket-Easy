"""The reviewer in front of the rule checker: an AI model reads the retrieved policy and picks the route for a write.

LLMPolicyGate wraps the real PolicyGate. For an action that changes something it finds the policy passages that
concern the action and asks the reviewer (agent/reviewer.py, job "reviewer") for allow / require_human / deny; the
reviewer returns the STRICTER of its route and the rule checker's. So the model can send a case to a person or refuse
it where the rules alone would allow it, but it can never turn a deny or a require_human into an allow. If the model or
the search cannot answer, an allowed write goes to a person (fail closed). A recorded human approval still turns the
model's require_human into allow, never a deny. Reads and denials pass through untouched.

The decision logic lives in Reviewer.review_action; this class only finds the passages. (It was the "policy agent";
TEAM_B_LLM_POLICY=1 still switches it on, as does TEAM_B_LLM_REVIEW=1.)
"""

import asyncio

from agent_service.agent.reviewer import Reviewer
from agent_service.contracts.evidence import Passage
from agent_service.contracts.policy import CheckActionRequest, PolicyDecision
from agent_service.observability import get_logger
from agent_service.ports import EvidenceProvider, LLMClient, PolicyGate
from agent_service.understanding.ai_reader import PromptTemplate

log = get_logger(__name__)
TOP_K = 6
RETRY_DELAY_S = 0.5
FACT_KEYS_IN_QUERY = ("status", "days_since_delivery", "days_late", "item", "category")


def _search_query(request: CheckActionRequest) -> str:
    words = request.action.replace("_", " ")
    text_args = [str(v) for v in request.arguments.values() if isinstance(v, str) and 2 < len(v) < 120]
    facts = [f"{k} {request.facts[k]}" for k in FACT_KEYS_IN_QUERY if k in request.facts]
    return " ".join([f"{words} policy", *text_args, *facts])


class LLMPolicyGate:
    def __init__(
        self,
        inner: PolicyGate,
        evidence: EvidenceProvider,
        llm: LLMClient,
        prompt: PromptTemplate | None = None,
        *,
        reviewer: Reviewer | None = None,
    ) -> None:
        self._inner = inner
        self._evidence = evidence
        self._reviewer = reviewer or Reviewer(llm, prompt)

    @property
    def prompt_version(self) -> str:
        return self._reviewer.prompt_version

    async def check_action(self, request: CheckActionRequest) -> PolicyDecision:
        decision = await self._inner.check_action(request)
        if request.tool.operation_kind == "read" or decision.decision == "deny":
            return decision
        try:
            passages = await self._passages_with_retry(request)
        except Exception as exc:  # search down twice: an allowed write waits for a person
            log.warning("reviewer_search_failed", reason=type(exc).__name__, action=request.action)
            return self._reviewer.unavailable(request, decision)
        if not passages:  # nothing in the policy concerns this action: the rule engine's answer stands
            return decision
        return await self._reviewer.review_action(request, decision, passages)

    async def _passages_with_retry(self, request: CheckActionRequest) -> tuple[Passage, ...]:
        """One hiccup in the search (a slow answer, a dropped call) must not send a normal request to a person."""
        try:
            return await self._passages(request)
        except Exception as exc:
            log.warning("reviewer_search_retry", reason=type(exc).__name__, action=request.action)
            await asyncio.sleep(RETRY_DELAY_S)
            return await self._passages(request)

    async def _passages(self, request: CheckActionRequest) -> tuple[Passage, ...]:
        result = await self._evidence.search_knowledge(
            request.tenant_id,
            _search_query(request),
            request_id=request.request_id,
            conversation_id=request.conversation_id,
            top_k=TOP_K,
        )
        return result.passages
