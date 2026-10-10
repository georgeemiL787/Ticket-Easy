"""The agent loop: one customer message, worked as a few moves proposed one at a time, every move checked by code.

In agent mode (tenant agent_mode / TEAM_B_AGENT_MODE "on", with a model for the orchestrator job) the middle of the
turn (disambiguate, merge, frustration, plan, handler, queue) is one stage, "agent". It asks a MovePicker for a move,
checks the move, works it, and asks again, until a move ends the turn (Reply, AskCustomer, HandOver). The front stages
(handed-off check, understanding, safety screen, wants a person, pending confirmation) run before it exactly as always,
and the handoff stage and the reply composer run after it.

The picker only PROPOSES (CLAUDE.md rule 1). Code decides: a move off the menu, a reply that could say something the
loop cannot back, the same look-up twice, or a hand-over without a valid reason is refused and the picker is told why.
Limits: at most TEAM_B_AGENT_MAX_MOVES asks of the picker per turn (a plan update counts as one), TEAM_B_AGENT_TURN_
TIMEOUT_S seconds for the whole loop. Falling back: a picker error, a time-out, a hit limit, or two refused moves in a
row raise AgentFallback; the pipeline then runs today's middle stages from their start, on the customer's message as if
the loop had not run. Nothing the loop did is kept (the saved plan is put back), and in this step the loop cannot write
anything anyway: look-ups, policy searches and actions are not built yet, they only answer "not built yet".
"""

import asyncio
import json
import time
from typing import Any

from pydantic import ValidationError

from team_b.brain.composer import PLACEHOLDERS, default_composer
from team_b.brain.redaction import redact, redact_mapping
from team_b.brain.turn import Step, TurnContext, locale_of
from team_b.domain.agent import (
    MAX_ARGUMENT_CHARS,
    AgentPlan,
    AgentStepRecord,
    AgentView,
    AnyMove,
    AskCustomer,
    CheckRecord,
    HandOver,
    LookUp,
    PlanStep,
    ProposeAction,
    Reply,
    SearchPolicy,
    parse_move,
)
from team_b.domain.decision import Decision
from team_b.observability import get_logger

log = get_logger(__name__)
SLOT_KEYS = frozenset({"order_id", "phone", "amount", "item", "reason", "new_address", "description"})
REPLY_DECISIONS = frozenset({Decision.ANSWER, Decision.CLARIFY, Decision.REFUSE})
# A reply the model picks may not be a confirmation, a result, a handoff or a policy statement: those come from code.
FORBIDDEN_REPLY_PREFIXES = ("handoff_", "confirm_action", "action_", "policy_", "approval_", "order_status", "status_")
MAX_TRIED = 10
MAX_OPTIONS = 3
NOT_BUILT = "not built yet"


class AgentFallback(Exception):
    """The loop gives up on this turn: the usual stages run instead. `reason` goes on the trace."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _fingerprint(move: AnyMove) -> str:
    return json.dumps(move.model_dump(mode="json", exclude={"why"}), sort_keys=True, default=str)


def _check(name: str, passed: bool, reason: str = "") -> CheckRecord:
    return CheckRecord(name=name, passed=passed, reason=reason if not passed else "")


def check_move(ctx: TurnContext, move: AnyMove, seen: set[str]) -> list[CheckRecord]:
    """The checks code makes on a move before it runs; one record each, passed or not."""
    checks = [_check("on_the_menu", True)]
    composer, locale = default_composer(), locale_of(ctx)
    if isinstance(move, LookUp | ProposeAction):
        size = len(json.dumps(move.arguments, default=str))
        checks.append(_check("arguments_fit", size <= MAX_ARGUMENT_CHARS, f"arguments are {size} characters long"))
    if isinstance(move, LookUp | SearchPolicy):
        again = _fingerprint(move) in seen
        checks.append(
            _check("not_asked_before", not again, "the same look-up with the same arguments was already made")
        )
    if isinstance(move, ProposeAction):
        spec = ctx.tenant.intents.get(move.intent)
        ok = spec is not None and spec.kind == "action"
        checks.append(_check("intent_is_an_action", ok, f"{move.intent!r} is not an action of this shop"))
    if isinstance(move, AskCustomer):
        key = move.question_key
        known = key.startswith("ask_") and composer.has(locale, key)
        checks.append(_check("question_exists", known, f"no question template {key!r}"))
        needs_slot = key == "ask_generic"
        checks.append(_check("slot_is_known", (move.slot is None and not needs_slot) or move.slot in SLOT_KEYS,
                             f"slot {move.slot!r} is not a detail this agent collects"))  # fmt: skip
        checks.append(_check("few_options", len(move.options) <= MAX_OPTIONS, "too many options offered"))
    if isinstance(move, HandOver):
        checks.append(_check("tried_is_short", len(move.tried) <= MAX_TRIED, "the list of what was tried is too long"))
    if isinstance(move, Reply):
        plan, key = move.plan, move.plan.reply_key or ""
        checks.append(_check("decision_is_allowed", plan.decision in REPLY_DECISIONS,
                             f"a reply may answer, clarify or refuse, not {plan.decision.value}"))  # fmt: skip
        exists = bool(key) and composer.has(locale, key) and not key.startswith(FORBIDDEN_REPLY_PREFIXES)
        checks.append(_check("template_is_allowed", exists, f"template {key!r} is unknown or reserved for code"))
        checks.append(_check("no_unbacked_facts", not plan.facts and not PLACEHOLDERS.get(key),
                             "a reply may only hold verified facts, and nothing is verified yet"))  # fmt: skip
        checks.append(_check("no_unretrieved_citations", not plan.citations, "no citation was retrieved this turn"))
    return checks


def _record(move: AnyMove | None, why: str, checks: list[CheckRecord], result: str, started: float) -> AgentStepRecord:
    shown: dict[str, Any] = (
        redact_mapping(move.model_dump(mode="json", exclude={"why"})) if move is not None else {"kind": "invalid"}
    )
    return AgentStepRecord(
        move=shown,
        why=redact(why),
        checks=checks,
        result=redact(result),
        duration_ms=max((time.perf_counter() - started) * 1000, 0.001),
    )


def _work(ctx: TurnContext, move: AnyMove) -> tuple[str, Step | None]:
    """Work a move that passed its checks: (what happened, the step that ends the turn or None to go on)."""
    if isinstance(move, LookUp | SearchPolicy | ProposeAction):
        return NOT_BUILT, None  # reading the shop, searching the policy and actions arrive in later steps
    composer, locale = default_composer(), locale_of(ctx)
    if isinstance(move, AskCustomer):
        values = {"slot": move.slot} if move.slot else {}
        awaiting = f"slot:{move.slot}" if move.slot else "detail"
        step = Step(Decision.CLARIFY, reason=move.why or f"asked for {move.slot or 'a detail'}",
                    reply_key=move.question_key, values=values, awaiting=awaiting)  # fmt: skip
        return f"asked {move.question_key}", step
    if isinstance(move, HandOver):
        key = f"handoff_{move.reason.value}"
        why = move.why or "the agent chose to hand over"
        if move.tried:
            why += f" (tried: {'; '.join(move.tried)})"
        step = Step(Decision.HANDOFF, reason=why,
                    reply_key=key if composer.has(locale, key) else "handoff_generic",
                    escalation=move.reason, awaiting="human")  # fmt: skip
        return f"handed over: {move.reason.value}", step
    assert isinstance(move, Reply)
    plan = move.plan
    assert plan.reply_key is not None  # checked: template_is_allowed
    waits = "detail" if plan.decision is Decision.CLARIFY else None
    reply = Step(plan.decision, reason=move.why or "the agent replied", reply_key=plan.reply_key, awaiting=waits)
    return f"replied with {plan.reply_key}", reply


def _new_plan(old: AgentPlan | None, update: AgentPlan) -> AgentPlan:
    steps = [PlanStep(text=redact(s.text), status=s.status) for s in update.steps]
    return AgentPlan(steps=steps, revision=(old.revision if old else 0) + 1)


async def _loop(ctx: TurnContext) -> Step:
    deps, session = ctx.deps, ctx.session
    picker = deps.move_picker
    assert picker is not None
    seen: set[str] = set()
    refused: str | None = None
    refused_in_a_row = 0
    for _ in range(deps.agent_max_moves):
        view = AgentView(
            message=ctx.text, plan=session.agent_plan, moves=list(ctx.agent_steps), refused=refused,
            pending=session.agent_pending,
        )  # fmt: skip
        started = time.perf_counter()
        try:
            proposal = await picker.next_move(view)
        except (TimeoutError, asyncio.CancelledError):
            raise
        except Exception as exc:  # the model or its parsing failed: today's stages take over
            ctx.agent_steps.append(_record(None, "the move picker failed", [], type(exc).__name__, started))
            raise AgentFallback(f"move picker error: {type(exc).__name__}") from exc
        if isinstance(proposal, AgentPlan):  # a changed plan, not a move
            session.agent_plan = _new_plan(session.agent_plan, proposal)
            ctx.agent_steps.append(
                _record(None, "the plan changed", [], f"plan revision {session.agent_plan.revision}", started)
            )
            ctx.agent_steps[-1] = ctx.agent_steps[-1].model_copy(update={"move": {"kind": "plan_update"}})
            refused_in_a_row = 0
            continue
        try:
            move = proposal if not isinstance(proposal, dict) else parse_move(proposal)
        except ValidationError:
            checks = [CheckRecord(name="on_the_menu", passed=False, reason="not a move of the menu")]
            ctx.agent_steps.append(_record(None, "", checks, "refused", started))
            refused, refused_in_a_row = "that is not a move of the menu", refused_in_a_row + 1
        else:
            checks = check_move(ctx, move, seen)
            if all(c.passed for c in checks):
                refused_in_a_row = 0
                if isinstance(move, LookUp | SearchPolicy):
                    seen.add(_fingerprint(move))
                result, step = _work(ctx, move)
                ctx.agent_steps.append(_record(move, move.why, checks, result, started))
                if step is not None:
                    return step
                refused = None
                continue
            refused = "; ".join(c.reason for c in checks if not c.passed)
            refused_in_a_row += 1
            ctx.agent_steps.append(_record(move, move.why, checks, f"refused: {refused}", started))
        if refused_in_a_row >= 2:
            raise AgentFallback("two refused moves in a row")
    raise AgentFallback(f"more than {deps.agent_max_moves} moves without an answer")


async def run_agent(ctx: TurnContext) -> Step:
    """The step this turn ends with, or AgentFallback (the usual stages then run). See the module text."""
    try:
        async with asyncio.timeout(ctx.deps.agent_turn_timeout_s):
            step = await _loop(ctx)
    except TimeoutError as exc:
        raise AgentFallback(f"the turn took longer than {ctx.deps.agent_turn_timeout_s:g} seconds") from exc
    pending = step.awaiting
    ctx.session.agent_pending = (
        pending if pending in ("confirmation", "detail") or (pending or "").startswith(("slot:", "choice")) else None
    )
    return step
