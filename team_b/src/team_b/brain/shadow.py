"""Shadow mode: the orchestrator watches. Safe to leave on in a demo.

With agent_mode "shadow" the turn runs the usual stages and answers as always. Right after the front stages (where the
agent loop would start) the orchestrator is asked ONCE for its plan and first move, on a deep copy of the turn: its
session, lists and counters are copies, no tool is called, nothing is sent to the customer, and the real session is not
touched (the plan it would keep is remembered only on the trace, and read back from the conversation's last trace so a
follow-up message is seen with its plan). The move is not worked at all; it is only checked the way the loop would check
it (agent_loop.check_move), so the trace shows what the agent would have done and whether code would have allowed it:
trace.shadow_plan, trace.shadow_move (with its checks) and trace.shadow_error (the picker failed or was too slow: never
raised to the customer).

compare() says whether that first move agrees with what the usual flow decided that turn, by the kind of move and the
tool it relies on. The evaluation (`eval --agent-mode shadow`) counts it and lists the disagreements.
"""

import asyncio
import time
from dataclasses import dataclass, replace
from typing import Any

from team_b.brain import agent_reads
from team_b.brain.agent_loop import check_move
from team_b.brain.redaction import redact, redact_mapping
from team_b.brain.turn import TurnContext
from team_b.domain.agent import (
    AgentPlan,
    AgentStepRecord,
    AnyMove,
    MoveProposal,
    PlanStep,
    parse_move,
)
from team_b.domain.decision import Decision
from team_b.domain.trace import DecisionTrace
from team_b.observability import get_logger

log = get_logger(__name__)
IDENTITY_QUESTIONS = frozenset({"ask_phone", "ask_order_id"})
READ_TOOLS_OF_IDENTITY = frozenset({"verify_customer", "get_order", "list_customer_orders"})


def _copy_of(ctx: TurnContext) -> TurnContext:
    """The turn as the picker may see it: every mutable part is a copy, so nothing it (or the view) touches is real."""
    return replace(
        ctx,
        session=ctx.session.model_copy(deep=True),
        steps=[],
        tool_calls=[],
        policy=[],
        proposal_ids=[],
        versions={},
        errors=[],
        evidence=[],
        earlier=[],
        agent_steps=[],
        agent_passages=[],
        agent_outcomes=[],
        agent_listing=[],
        step=None,
    )


async def _last_shadow_plan(ctx: TurnContext) -> AgentPlan | None:
    traces = await ctx.deps.traces.for_conversation(ctx.tenant.tenant_id, ctx.session.conversation_id)
    for trace in reversed(traces):
        if trace.kind == "customer_turn":
            return trace.shadow_plan
    return None


def _redacted(plan: AgentPlan | None) -> AgentPlan | None:
    if plan is None:
        return None
    return AgentPlan(steps=[PlanStep(text=redact(s.text), status=s.status) for s in plan.steps], revision=plan.revision)


async def run_shadow(ctx: TurnContext) -> str:
    """Ask the orchestrator for its plan and first move on a copy; record both on the context. Never raises."""
    picker = ctx.deps.move_picker
    assert picker is not None
    started = time.perf_counter()
    try:
        async with asyncio.timeout(ctx.deps.agent_turn_timeout_s):
            copy = _copy_of(ctx)
            # the last turn's plan matters only while the conversation waits for an answer
            copy.session.agent_plan = await _last_shadow_plan(ctx) if ctx.session.awaiting else None
            copy.session.agent_pending = ctx.session.awaiting
            view = await agent_reads.build_view(copy, None, [])
            proposal: Any = await picker.next_move(view)
            plan: AgentPlan | None = None
            if isinstance(proposal, MoveProposal):
                plan, proposal = proposal.plan, proposal.move
            elif isinstance(proposal, AgentPlan):
                plan, proposal = proposal, None
            move: AnyMove | None = parse_move(proposal) if isinstance(proposal, dict) else proposal
            checks = check_move(copy, move, set()) if move is not None else []
    except Exception as exc:  # shadow must never disturb the real turn
        ctx.shadow_error = type(exc).__name__
        log.warning("shadow_failed", reason=type(exc).__name__)
        return f"the orchestrator failed ({type(exc).__name__}): ignored"
    ctx.shadow_plan = _redacted(plan)
    if move is not None:
        shown = redact_mapping(move.model_dump(mode="json", exclude={"why"}))
        ctx.shadow_move = AgentStepRecord(
            move=shown,
            why=redact(move.why),
            checks=checks,
            result="shadow: proposed, not worked",
            duration_ms=max((time.perf_counter() - started) * 1000, 0.001),
        )
    version = getattr(picker, "prompt_version", None)
    if version:
        ctx.versions["orchestrator_prompt"] = str(version)
    return f"first move: {shown['kind'] if move is not None else 'plan only'}"


# ---- does the agent's first move agree with what the usual flow decided? ----


@dataclass(frozen=True)
class Agreement:
    agrees: bool
    today: str  # what the usual flow did, in a few words
    agent: str  # what the agent's first move was
    why: str = ""


def _today(trace: DecisionTrace) -> tuple[str, str | None]:
    """(kind, tool) of what the usual flow did this turn."""
    decision = trace.decision
    reads = [c.tool for c in trace.tool_calls if c.operation_kind == "read"]
    if decision is Decision.HANDOFF:
        return "hand_over", None
    if decision is Decision.VERIFY_IDENTITY:
        return "identity", None
    if decision is Decision.CLARIFY:
        return "ask", None
    if decision in (Decision.CONFIRM, Decision.EXECUTE) or trace.proposals:
        return "action", trace.proposals[-1].tool if trace.proposals else None
    if trace.knowledge_answer or trace.evidence:
        return "policy", None
    if reads:
        return "lookup", next((t for t in reads if t != "verify_customer"), reads[0])
    return "reply", None


def _agent(record: AgentStepRecord, trace: DecisionTrace, action_tools: dict[str, str]) -> tuple[str, str | None]:
    move = record.move
    kind = move.get("kind")
    verified = trace.identity.verified
    if kind == "look_up":
        tool = str(move.get("tool"))
        personal = tool in READ_TOOLS_OF_IDENTITY
        return ("identity" if personal and not verified else "lookup"), tool
    if kind == "ask_customer":
        return ("identity" if move.get("question_key") in IDENTITY_QUESTIONS and not verified else "ask"), None
    if kind == "search_policy":
        return "policy", None
    if kind == "propose_action":
        return "action", action_tools.get(str(move.get("intent")))
    if kind == "hand_over":
        return "hand_over", None
    plan = move.get("plan") or {}
    if plan.get("decision") == "clarify":
        return "ask", None
    return ("policy" if plan.get("reply_key") == "policy_answer" else "reply"), None


def compare(trace: DecisionTrace, action_tools: dict[str, str]) -> Agreement | None:
    """None when the agent was not asked in this turn (no picker, or the front stages already decided)."""
    record = trace.shadow_move
    if record is None:
        return None
    today_kind, today_tool = _today(trace)
    agent_kind, agent_tool = _agent(record, trace, action_tools)
    label_today = today_kind + (f" ({today_tool})" if today_tool else "")
    label_agent = agent_kind + (f" ({agent_tool})" if agent_tool else "")
    same_kind = (
        today_kind == agent_kind
        or (today_kind == "identity" and agent_kind in ("ask", "lookup"))  # asking for the phone directly is the same
        or (today_kind == "reply" and agent_kind == "ask" and trace.decision is Decision.ANSWER)  # small talk
    )
    same_tool = today_tool is None or agent_tool is None or today_tool == agent_tool
    refused = any(not c.passed for c in record.checks)
    why = "" if same_kind and same_tool else ("the kind of move differs" if not same_kind else "a different tool")
    if refused and not why:
        why = "same move, but code would have refused it"
    return Agreement(same_kind and same_tool and not refused, label_today, label_agent, why)
