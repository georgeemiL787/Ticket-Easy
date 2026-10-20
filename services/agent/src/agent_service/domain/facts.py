"""The small, text-free rows the dashboard reads: one per customer turn, one per tool call, one per policy answer.

They are cut from a DecisionTrace when it is stored (see facts_from_trace) and hold no message text or personal value,
so they can be kept and counted long after the trace itself is deleted. metrics.py computes every number from them.
"""

from datetime import datetime

from pydantic import Field

from agent_service.domain.base import FrozenModel
from agent_service.domain.trace import DecisionTrace

AI_MODEL_SERVICE = "ai_model"  # an AI job fell back to the rules or the template (rate budget, outage, nonsense)
# The brain names the part of the system in the first words of an error it records for the trace.
_SERVICE_BY_PREFIX: tuple[tuple[str, str], ...] = (
    ("policy search", "policy_search"),
    ("policy passage", "policy_search"),
    ("safety screen", "safety_screen"),
    ("no safety screen", "safety_screen"),
    ("rule checker", "rule_checker"),
    ("the shop tool list", "shop_tools"),
    ("order list", "shop_tools"),
    ("ai model", AI_MODEL_SERVICE),
)
OTHER_SERVICE = "other"
AI_METHODS = ("llm", "rules_fallback")  # the readings where the AI model was asked


def fallback_category(trace: DecisionTrace) -> str | None:
    """Why the agent loop gave up on this turn, as one of a few categories (the free text may hold a number)."""
    reason = (trace.fallback_reason or "").lower()
    if not reason:
        return None
    if "ratebudget" in reason or "rate_budget" in reason or "RATE_BUDGET" in trace.versions.get("ai_fallback", ""):
        return "rate_limit"
    if "move picker" in reason:
        return "model_error"
    if "refused" in reason:
        return "refused_moves"
    if "moves without" in reason:
        return "too_many_moves"
    if "longer than" in reason:
        return "too_slow"
    return "other"


def agent_worked(turn: "TurnFact") -> bool:
    """Did the agent loop work on this turn (it made moves, or it tried and gave up)?"""
    return turn.agent_mode == "on" and (turn.agent_moves > 0 or turn.agent_fallback)


def ai_asked(turn: "TurnFact") -> bool:
    """Was the AI model asked in this turn (to read the message, or for any other job)?"""
    return turn.nlu_method in AI_METHODS or AI_MODEL_SERVICE in turn.dependency_errors


def ai_fell_back(turn: "TurnFact") -> bool:
    """Did an AI job of this turn fall back to the rules or the template?"""
    return turn.nlu_method == "rules_fallback" or AI_MODEL_SERVICE in turn.dependency_errors


class TurnFact(FrozenModel):
    trace_id: str
    tenant_id: str
    conversation_id: str
    created_at: datetime
    decision: str
    escalation_reason: str | None = None
    language: str | None = None
    intent: str | None = None
    latency_ms: float = Field(ge=0.0)
    evidence_empty: bool = False  # the policy search ran and found nothing
    evidence_count: int = Field(default=0, ge=0)  # passages the answer stood on; with evidence_empty: did a search run?
    nlu_method: str | None = None
    tool_errors: int = Field(default=0, ge=0)  # tool calls that failed in this turn
    dependency_errors: tuple[str, ...] = ()  # the service of every dependency error, one entry per error
    stage_ms: dict[str, float] = Field(default_factory=dict)  # stage -> duration, for stages that ran
    escalation_kind: str | None = None  # a handoff: forced (decided by code) or chosen (by the agent after trying)
    agent_mode: str | None = None  # on | off | shadow
    agent_moves: int = Field(default=0, ge=0)  # moves the agent loop made in this turn
    agent_fallback: bool = False  # the agent loop gave up and the usual stages answered
    agent_fallback_reason: str | None = (
        None  # why, as a category: rate_limit, model_error, refused_moves, too_many_moves, too_slow
    )
    reviewer_blocked_replies: int = Field(default=0, ge=0)  # replies the reviewer blocked or could not check
    reviewer_blocked_actions: int = Field(default=0, ge=0)  # writes the reviewer sent to a person or refused


class ToolCallFact(FrozenModel):
    trace_id: str
    tenant_id: str
    created_at: datetime
    tool: str
    operation_kind: str
    status: str
    error_code: str | None = None
    latency_ms: float = Field(ge=0.0)


class PolicyFact(FrozenModel):
    trace_id: str
    tenant_id: str
    created_at: datetime
    action: str
    decision: str
    reason_code: str


class FactsSummary(FrozenModel):
    """Headline counts of a window, computed by the store without loading every row (the dashboard overview)."""

    turns: int = 0
    conversations: int = 0
    conversations_with_case: int = 0
    unverified_results: int = 0
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None


class Facts(FrozenModel):
    """Everything stored for a tenant in a time window."""

    turns: tuple[TurnFact, ...] = ()
    tool_calls: tuple[ToolCallFact, ...] = ()
    policy: tuple[PolicyFact, ...] = ()


def dependency_service(error: str) -> str:
    """Which service an error text from the trace is about (other when it names none of the known ones)."""
    lowered = error.lower()
    for prefix, service in _SERVICE_BY_PREFIX:
        if lowered.startswith(prefix):
            return service
    return OTHER_SERVICE


def facts_from_trace(
    trace: DecisionTrace, created_at: datetime
) -> tuple[TurnFact | None, list[ToolCallFact], list[PolicyFact]]:
    """The rows for one trace: a turn row for a customer turn only; tool calls and policy answers of any trace."""
    stage_ms = {s.stage: s.duration_ms for s in trace.steps if s.status != "skipped"}
    turn: TurnFact | None = None
    if trace.kind == "customer_turn":
        turn = TurnFact(
            trace_id=trace.trace_id,
            tenant_id=trace.tenant_id,
            conversation_id=trace.conversation_id,
            created_at=created_at,
            decision=trace.decision.value,
            escalation_reason=trace.escalation_reason.value if trace.escalation_reason else None,
            language=trace.language.value if trace.language else None,
            intent=trace.active_intent,
            latency_ms=trace.latency_ms,
            evidence_empty=trace.evidence_empty_reason is not None,
            evidence_count=len(trace.evidence),
            nlu_method=trace.nlu_method,
            tool_errors=sum(1 for c in trace.tool_calls if c.status == "error"),
            dependency_errors=tuple(dependency_service(e) for e in trace.errors),
            stage_ms=stage_ms,
            escalation_kind=trace.escalation_kind,
            agent_mode=trace.agent_mode,
            agent_moves=len(trace.agent_steps),
            agent_fallback=trace.fallback_reason is not None,
            agent_fallback_reason=fallback_category(trace),
            reviewer_blocked_replies=sum(1 for s in trace.steps if s.stage == "review" and s.status != "ok"),
            reviewer_blocked_actions=sum(1 for p in trace.policy if p.reason_code.startswith("POLICY_AGENT_")),
        )
    calls = [
        ToolCallFact(
            trace_id=trace.trace_id, tenant_id=trace.tenant_id, created_at=created_at, tool=c.tool,
            operation_kind=c.operation_kind, status=c.status, error_code=c.error_code, latency_ms=c.latency_ms,
        )
        for c in trace.tool_calls
    ]  # fmt: skip
    policy = [
        PolicyFact(
            trace_id=trace.trace_id, tenant_id=trace.tenant_id, created_at=created_at, action=p.action,
            decision=p.decision, reason_code=p.reason_code,
        )
        for p in trace.policy
    ]  # fmt: skip
    return turn, calls, policy
