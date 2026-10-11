"""The agent loop: one customer message, worked as a few moves proposed one at a time, every move checked by code.

In agent mode (tenant agent_mode / TEAM_B_AGENT_MODE "on", with a model for the orchestrator job) the middle of the
turn (disambiguate, merge, frustration, plan, handler, queue) is one stage, "agent". It asks a MovePicker for a move,
checks the move, works it, and asks again, until a move ends the turn (a Reply that finishes the plan, AskCustomer,
HandOver). The front stages (handed-off check, understanding, safety screen, wants a person, pending confirmation) run
before it exactly as always, and the handoff stage and the reply composer run after it.

The picker only PROPOSES (CLAUDE.md rule 1). Code decides: a move off the menu, a reply that could say something the
loop cannot back, the same look-up twice, a read of someone else's order or a hand-over without a valid reason is
refused and the picker is told why. What the read moves check is in agent_reads.py.

Plan, act, replan: the picker may answer with a changed plan (1 to 5 steps, saved on the session with a revision number
so the next message continues it) together with its move. A Reply that answers one request while the plan still has an
open step does not end the turn: its text is kept in front of the final one (the way queued requests are today) and the
picker is asked again.

Limits: at most TEAM_B_AGENT_MAX_MOVES asks of the picker per turn (a plan update counts as one), TEAM_B_AGENT_TURN_
TIMEOUT_S seconds for the whole loop. Falling back: a picker error, a time-out, a hit limit, or two refused moves in a
row raise AgentFallback; the pipeline then runs today's middle stages from their start, on the customer's message as if
the loop had not run. The saved plan is put back. In this step the loop cannot write: ProposeAction only answers "not
built yet", and a write tool named in a LookUp is refused.
"""

import asyncio
import json
import time
from typing import Any

from pydantic import ValidationError

from team_b.brain import agent_actions, agent_reads, escalation
from team_b.brain.choices import describe_orders, label_of
from team_b.brain.composer import PLACEHOLDERS, default_composer
from team_b.brain.redaction import redact, redact_mapping
from team_b.brain.reviewer import HandoverWatch, blocks_handover
from team_b.brain.turn import COMPLETED, Step, TurnContext, locale_of
from team_b.domain.agent import (
    MAX_ARGUMENT_CHARS,
    AgentPlan,
    AgentStepRecord,
    AnyMove,
    AskCustomer,
    CheckRecord,
    HandOver,
    LookUp,
    MoveProposal,
    PlanStep,
    ProposeAction,
    Reply,
    ReplyPlan,
    SearchPolicy,
    parse_move,
)
from team_b.domain.decision import Decision
from team_b.observability import get_logger

log = get_logger(__name__)
SLOT_KEYS = frozenset({"order_id", "phone", "amount", "item", "reason", "new_address", "description"})
REPLY_DECISIONS = frozenset({Decision.ANSWER, Decision.CLARIFY, Decision.REFUSE})
# A reply the model picks may not be a confirmation, a result, a handoff or a policy statement: those come from code.
FORBIDDEN_REPLY_PREFIXES = ("handoff_", "confirm_action", "action_", "policy_refusal", "approval_", "status_")
ORDER_STATUS = "order_status"  # built by code from the verified order, never from the model's words
FREE_REPLY = "free_reply"  # an off-topic message: the writer answers the person and steers back (no facts)
ACTION_OUTCOME = "action_outcome"  # the refusal / done / failed text of an action, as the rules and the shop gave it
POLICY_ANSWER = "policy_answer"  # the retrieved passages, quoted word for word
MAX_TRIED = 10
MAX_OPTIONS = 3


class AgentFallback(Exception):
    """The loop gives up on this turn: the usual stages run instead. `reason` goes on the trace."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _fingerprint(move: AnyMove) -> str:
    return json.dumps(move.model_dump(mode="json", exclude={"why"}), sort_keys=True, default=str)


def _check(name: str, passed: bool, reason: str = "") -> CheckRecord:
    return CheckRecord(name=name, passed=passed, reason="" if passed else reason)


def _reply_checks(ctx: TurnContext, move: Reply) -> list[CheckRecord]:
    plan, key = move.plan, move.plan.reply_key or ""
    composer, locale, session = default_composer(), locale_of(ctx), ctx.session
    allowed = plan.decision in REPLY_DECISIONS
    checks = [
        _check("decision_is_allowed", allowed, f"a reply may answer, clarify or refuse, not {plan.decision.value}")
    ]
    if key == ORDER_STATUS:  # the words come from the verified order, so it must be there and the customer's own
        facts = session.facts
        verified = session.identity.verified and facts.get("customer_id") == session.identity.customer_id
        checks.append(
            _check(
                "order_is_verified",
                bool(verified and facts.get("order_id")),
                "no order of the verified customer was read",
            )
        )
        checks.append(_check("answer_decision", plan.decision is Decision.ANSWER, "an order status is an answer"))
    elif key == FREE_REPLY:
        checks.append(
            _check("free_reply_has_no_facts", not plan.facts and not plan.citations, "a free reply holds no facts")
        )
        checks.append(_check("free_reply_is_an_answer", plan.decision is Decision.ANSWER, "a free reply is an answer"))
    elif key == ACTION_OUTCOME:
        checks.append(_check("an_outcome_is_waiting", bool(ctx.agent_outcomes), "no action has an outcome to tell yet"))
    elif key == POLICY_ANSWER:
        found = {p.citation for p in ctx.agent_passages}
        checks.append(_check("passages_were_retrieved", bool(found), "no policy passage was retrieved this turn"))
        stray = [c for c in plan.citations if c not in found]
        checks.append(_check("citations_were_retrieved", not stray, f"{', '.join(stray)} was not retrieved this turn"))
        checks.append(_check("answer_decision", plan.decision is Decision.ANSWER, "a policy answer is an answer"))
    else:
        exists = bool(key) and composer.has(locale, key) and not key.startswith(FORBIDDEN_REPLY_PREFIXES)
        checks.append(_check("template_is_allowed", exists, f"template {key!r} is unknown or reserved for code"))
        backed = not plan.facts and not PLACEHOLDERS.get(key)
        checks.append(_check("no_unbacked_facts", backed, "a reply may only hold facts code can back"))
        checks.append(_check("no_unretrieved_citations", not plan.citations, "no citation was retrieved this turn"))
    return checks


def check_move(ctx: TurnContext, move: AnyMove, seen: set[str]) -> list[CheckRecord]:
    """The checks code makes on a move before it runs, one record each. (The read moves make more while they run.)"""
    checks = [_check("on_the_menu", True)]
    composer, locale = default_composer(), locale_of(ctx)
    if isinstance(move, LookUp | ProposeAction):
        size = len(json.dumps(move.arguments, default=str))
        checks.append(_check("arguments_fit", size <= MAX_ARGUMENT_CHARS, f"arguments are {size} characters long"))
    if isinstance(move, LookUp | SearchPolicy | Reply):
        again = _fingerprint(move) in seen
        checks.append(_check("not_done_before", not again, "the same move with the same content was already made"))
    if isinstance(move, ProposeAction):
        spec = ctx.tenant.intents.get(move.intent)
        ok = spec is not None and spec.kind == "action"
        checks.append(_check("intent_is_an_action", ok, f"{move.intent!r} is not an action of this shop"))
        asked = {i.name for i in (ctx.understanding.intents if ctx.understanding else [])}
        asked |= {*ctx.session.intents_seen, *ctx.session.intent_queue, ctx.session.active_intent or ""}
        checks.append(  # a model (or a text hidden in the data it reads) must not start what nobody asked for
            _check("action_was_asked_for", move.intent in asked, f"the customer did not ask for {move.intent!r}")
        )
    if isinstance(move, AskCustomer):
        key = move.question_key
        if key == "disambiguate_intent":  # "return or exchange?": two requests that cannot both be meant
            pair = len(move.options) == 2 and all(
                o in ctx.tenant.intents and ctx.tenant.intents[o].kind == "action" for o in move.options
            )
            checks.append(_check("options_are_two_actions", pair, "a request choice offers exactly two actions"))
        elif move.options:
            listed = {o.get("order_id") for o in ctx.agent_listing}
            ok = key == "disambiguate_order" and set(move.options) <= listed and len(move.options) <= MAX_OPTIONS
            checks.append(
                _check("options_are_the_customers_orders", ok, "a choice may only offer the customer's listed orders")
            )
        else:
            known = key.startswith("ask_") and composer.has(locale, key)
            checks.append(_check("question_exists", known, f"no question template {key!r}"))
            ok = (move.slot is None and key != "ask_generic") or move.slot in SLOT_KEYS
            checks.append(_check("slot_is_known", ok, f"slot {move.slot!r} is not a detail this agent collects"))
            session = ctx.session
            have = (
                bool(move.slot)
                and move.slot != "order_id"
                and (  # a new order number may be wanted: that one is asked
                    bool(session.slots.get(move.slot or "")) or (move.slot == "phone" and session.identity.verified)
                )
            )
            checks.append(  # the customer already gave it: asking again is what makes the agent seem to forget
                _check(
                    "slot_not_already_known", not have, f"you already have the {move.slot}: use it, do not ask for it"
                )
            )
            lost = move.slot is None and session.active_intent is not None and session.awaiting != "detail"
            checks.append(  # the shop's record says what the customer wants: asking "what do you need?" is forgetting
                _check(
                    "not_forgetting_the_request",
                    not lost,
                    f"the customer asked for {session.active_intent}: go on with it",
                )
            )
            repeats = move.slot is None and session.awaiting == "detail"
            tired = repeats and session.agent_questions >= ctx.tenant.escalation.max_clarifications
            checks.append(
                _check(
                    "not_asking_the_same_again", not tired, "you already asked twice for more detail: hand over instead"
                )
            )
    if isinstance(move, HandOver):
        checks.append(_check("tried_is_short", len(move.tried) <= MAX_TRIED, "the list of what was tried is too long"))
        problem = escalation.handover_problem(ctx, move)
        checks.append(_check("handover_after_trying", problem is None, problem or ""))
    if isinstance(move, Reply):
        checks += _reply_checks(ctx, move)
    return checks


MAX_SHOWN_CHARS = 300  # a longer text in a move is clipped in the trace: a huge argument must not fill the store


def _clipped(value: Any) -> Any:
    """The same value with every long text cut short (the trace keeps what the move was, not a megabyte of it)."""
    if isinstance(value, str):
        return value if len(value) <= MAX_SHOWN_CHARS else f"{value[:MAX_SHOWN_CHARS]}... ({len(value)} characters)"
    if isinstance(value, dict):
        return {k: _clipped(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clipped(v) for v in value[:MAX_TRIED]]
    return value


def _record(move: AnyMove | None, why: str, checks: list[CheckRecord], result: str, started: float) -> AgentStepRecord:
    shown: dict[str, Any] = (
        _clipped(redact_mapping(move.model_dump(mode="json", exclude={"why"})))
        if move is not None
        else {"kind": "invalid"}
    )
    return AgentStepRecord(
        move=shown,
        why=redact(why),
        checks=checks,
        result=redact(result),
        duration_ms=max((time.perf_counter() - started) * 1000, 0.001),
    )


async def _reply_step(ctx: TurnContext, move: Reply) -> Step:
    """The step of a reply move. An order status and a policy answer are built by code from what was verified."""
    from team_b.brain.lookup import _policy_quote, _status_reply

    plan = move.plan
    if plan.reply_key == FREE_REPLY:
        purpose = plan.intent or "answer the person kindly and steer back to how you can help"
        free = ReplyPlan(decision=Decision.ANSWER, intent=purpose, free=True, reply_key="ask_rephrase")
        return Step(Decision.ANSWER, reason=move.why or "an unexpected message", reply_key="ask_rephrase", plan=free)
    if plan.reply_key == ACTION_OUTCOME:
        return ctx.agent_outcomes.pop(0)  # the words are the rules' and the shop's, not the model's
    if plan.reply_key == ORDER_STATUS:
        spec = next((s for s in ctx.tenant.intents.values() if s.kind == "lookup" and s.lookup_tool), None)
        passages = await _policy_quote(ctx, spec) if spec is not None else []
        return _status_reply(ctx, ctx.session.facts, passages)
    if plan.reply_key == POLICY_ANSWER:
        wanted = set(plan.citations)
        quoted = tuple(p for p in ctx.agent_passages if not wanted or p.citation in wanted)
        cited = tuple(p.citation for p in quoted)
        reason = f"answered from {', '.join(cited)}"
        # the knowledge agent's own words, when it worded the answer and every passage it relied on is being quoted
        own = ctx.agent_policy_text if ctx.agent_policy_text and set(ctx.agent_policy_citations) <= set(cited) else None
        return Step(
            Decision.ANSWER,
            reason=reason,
            reply_key=POLICY_ANSWER,
            citations=cited,
            passages=quoted,
            knowledge=True,
            text=own,
        )
    assert plan.reply_key is not None  # checked: template_is_allowed
    waits = "detail" if plan.decision is Decision.CLARIFY else None
    return Step(plan.decision, reason=move.why or "the agent replied", reply_key=plan.reply_key, awaiting=waits)


async def _work(ctx: TurnContext, move: AnyMove) -> agent_reads.ReadResult:
    """Work a move that passed its first checks: what happened, extra checks, the step that ends the turn (or None)."""
    if isinstance(move, LookUp):
        return await agent_reads.look_up(ctx, move)
    if isinstance(move, SearchPolicy):
        return await agent_reads.search_policy(ctx, move)
    if isinstance(move, ProposeAction):
        return await agent_actions.propose_action(ctx, move)  # the whole gate order of actions.py decides
    composer, locale = default_composer(), locale_of(ctx)
    if isinstance(move, AskCustomer):
        if move.question_key == "disambiguate_intent":  # "return or exchange?"
            first, second = move.options
            ctx.session.choice_options = [first, second]
            names = {"a": label_of(ctx.tenant, first, locale), "b": label_of(ctx.tenant, second, locale)}
            choice = Step(Decision.CLARIFY, reason=move.why or f"cannot tell {first} from {second}",
                          reply_key="disambiguate_intent", values=names, awaiting="intent_choice")  # fmt: skip
            return agent_reads.ReadResult("asked which request", choice)
        if move.options:  # "which order?": the customer's own orders, masked, to pick from
            chosen = [o for o in ctx.agent_listing if o.get("order_id") in move.options]
            ctx.session.order_choices = [o["order_id"] for o in chosen]
            values = {"orders": describe_orders(chosen, locale)}
            asked = Step(Decision.CLARIFY, reason=move.why or "asked which order", reply_key="disambiguate_order",
                         values=values, awaiting="order_choice")  # fmt: skip
            return agent_reads.ReadResult("asked which order", asked)
        ctx.session.agent_questions += 1
        _remember_the_request(ctx)
        values = {"slot": move.slot} if move.slot else {}
        awaiting = f"slot:{move.slot}" if move.slot else "detail"
        proves_identity = move.slot == "phone" and not ctx.session.identity.verified  # as the usual flow labels it
        step = Step(Decision.VERIFY_IDENTITY if proves_identity else Decision.CLARIFY,
                    reason=move.why or f"asked for {move.slot or 'a detail'}",
                    reply_key=move.question_key, values=values, awaiting=awaiting)  # fmt: skip
        return agent_reads.ReadResult(f"asked {move.question_key}", step)
    if isinstance(move, HandOver):
        key = f"handoff_{move.reason.value}"
        why = move.why or "the agent chose to hand over"
        if move.tried:
            why += f" (tried: {'; '.join(move.tried)})"
        step = Step(Decision.HANDOFF, reason=why, reply_key=key if composer.has(locale, key) else "handoff_generic",
                    escalation=move.reason, awaiting="human", escalation_kind="chosen",
                    tried=tuple(redact(t) for t in move.tried))  # fmt: skip
        return agent_reads.ReadResult(f"handed over: {move.reason.value}", step)
    assert isinstance(move, Reply)
    return agent_reads.ReadResult(f"replied with {move.plan.reply_key}", await _reply_step(ctx, move))


def _remember_the_request(ctx: TurnContext) -> None:
    """The agent asks for a detail of a request: that request is now the active one. Without it, a turn the AI cannot
    take (rate limit, timeout) falls to the usual stages, which would not know what the answer belongs to."""
    session = ctx.session
    if session.active_intent is not None or ctx.understanding is None:
        return
    for found in ctx.understanding.intents:
        spec = ctx.tenant.intents.get(found.name)
        if spec is not None and spec.kind in ("action", "lookup"):
            session.active_intent = found.name
            return


def _give_up(ctx: TurnContext, reason: str) -> Step:
    """The loop cannot go on. Normally the usual stages take over (AgentFallback), but not once the shop was asked to
    change something this turn: running the whole message again could do it twice. Then the turn ends with what was
    already settled (the outcome of the action), and the trace says the loop stopped."""
    if not ctx.agent_wrote:
        raise AgentFallback(reason)
    ctx.errors.append(f"agent stopped early: {reason}")
    ctx.earlier.extend(ctx.agent_outcomes)
    ctx.agent_outcomes.clear()
    return ctx.earlier.pop()


def _plan_changed(old: AgentPlan | None, new: AgentPlan) -> bool:
    return old is None or [(s.text, s.status) for s in old.steps] != [(s.text, s.status) for s in new.steps]


def _apply_plan(ctx: TurnContext, update: AgentPlan, started: float) -> None:
    session = ctx.session
    if not _plan_changed(session.agent_plan, update):
        return
    steps = [PlanStep(text=redact(s.text), status=s.status) for s in update.steps]
    session.agent_plan = AgentPlan(steps=steps, revision=(session.agent_plan.revision if session.agent_plan else 0) + 1)
    ctx.agent_steps.append(
        AgentStepRecord(
            move={"kind": "plan_update"},
            why="the plan changed",
            duration_ms=max((time.perf_counter() - started) * 1000, 0.001),
            result=f"plan revision {session.agent_plan.revision}",
        )
    )


async def _loop(ctx: TurnContext) -> Step:
    deps, session = ctx.deps, ctx.session
    picker = deps.move_picker
    assert picker is not None
    seen: set[str] = set()
    refused: str | None = None
    refused_in_a_row = 0
    calls = 0
    watch = HandoverWatch()
    while calls < deps.agent_max_moves:
        calls += 1
        view = await agent_reads.build_view(ctx, refused, ctx.agent_steps)
        started = time.perf_counter()
        try:
            proposal = await picker.next_move(view)
        except (TimeoutError, asyncio.CancelledError):
            raise
        except Exception as exc:  # the model or its parsing failed: today's stages take over
            ctx.agent_steps.append(_record(None, "the move picker failed", [], type(exc).__name__, started))
            if not ctx.agent_wrote:
                raise AgentFallback(f"move picker error: {type(exc).__name__}") from exc
            return _give_up(ctx, f"move picker error: {type(exc).__name__}")
        if isinstance(proposal, MoveProposal):  # a plan and a move in one answer
            if proposal.plan is not None:
                _apply_plan(ctx, proposal.plan, started)
            proposal = proposal.move
        elif isinstance(proposal, AgentPlan):  # a changed plan, no move this ask
            _apply_plan(ctx, proposal, started)
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
            if isinstance(move, HandOver) and deps.reviewer is not None and blocks_handover(move, watch):
                reason = "the reviewer: say what was tried before handing over"
                checks.append(CheckRecord(name="handover_lists_what_was_tried", passed=False, reason=reason))
            worked = agent_reads.ReadResult("refused") if any(not c.passed for c in checks) else await _work(ctx, move)
            checks = checks + worked.checks
            if all(c.passed for c in checks):
                refused_in_a_row = 0
                seen.add(_fingerprint(move))
                ctx.agent_steps.append(_record(move, move.why, checks, worked.result, started))
                refused = None
                step = worked.step
                if step is None:
                    continue
                more = session.agent_plan is not None and any(s.status == "open" for s in session.agent_plan.steps)
                if isinstance(move, Reply) and step.decision in COMPLETED and more and calls < deps.agent_max_moves:
                    ctx.earlier.append(step)  # one request answered, others still open: its text goes in front
                    continue
                ctx.earlier.extend(ctx.agent_outcomes)  # a refusal or result nobody delivered is still told, in front
                ctx.agent_outcomes.clear()
                return step
            refused = "; ".join(c.reason for c in checks if not c.passed)
            refused_in_a_row += 1
            ctx.agent_steps.append(_record(move, move.why, checks, f"refused: {refused}", started))
        if refused_in_a_row >= 2:
            return _give_up(ctx, "two refused moves in a row")
    return _give_up(ctx, f"more than {deps.agent_max_moves} moves without an answer")


async def run_agent(ctx: TurnContext) -> Step:
    """The step this turn ends with, or AgentFallback (the usual stages then run). See the module text."""
    try:
        async with asyncio.timeout(ctx.deps.agent_turn_timeout_s):
            step = await _loop(ctx)
    except TimeoutError as exc:
        reason = f"the turn took longer than {ctx.deps.agent_turn_timeout_s:g} seconds"
        if not ctx.agent_wrote:
            raise AgentFallback(reason) from exc
        step = _give_up(ctx, reason)
    pending = step.awaiting
    waits = pending in ("confirmation", "detail", "order_choice", "intent_choice") or (pending or "").startswith(
        "slot:"
    )
    ctx.session.agent_pending = pending if waits else None
    return step
