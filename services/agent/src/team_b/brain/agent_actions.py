"""ProposeAction: the orchestrator picks WHICH action and WITH WHICH details; the gate order of actions.py decides.

(SAFETY-CRITICAL, needs a second reviewer.) The move changes nothing by itself. Code maps the intent to the tenant's
IntentSpec (an action intent or the move is refused), fills the request's details, and then calls actions.handle()
exactly as the usual flow does: required details -> identity -> order read and ownership -> permission gate -> safety
screen answered this turn -> rule check -> the customer's confirmation (or a person's approval) -> the rule check again
-> execution authorized -> one call -> verified result. Nothing in this module calls a write tool, and ActionCoordinator
.execute is only reached from actions.py after those gates.

The details a model gives are treated like details a customer states, and no better: a value fills a slot only when it
also appears in the customer's own messages or in what the shop returned this conversation (an order id the customer
wrote, an amount they said, an item name the shop read). Anything else is dropped, and the trace says so. A detail the
customer states in the current message is picked up the way the usual merge stage does it (brain/freetext.py).

What comes back from the usual flow decides what the loop does:
  CONFIRM (waiting for the customer's yes), a question for a missing detail, or a handoff  -> the turn ends with it;
  a refusal by the rules (with the alternatives the shop lists), or a done / failed action -> an OUTCOME: its text is
  kept for the customer and the orchestrator is told what happened, so it can go on with the rest of its plan
  or deliver it (Reply with reply_key "action_outcome"). An outcome left when the turn ends is shown before the reply.
"""

import json
from typing import Any

from team_b.brain import actions, freetext
from team_b.brain.agent_reads import ReadResult, known_order_ids
from team_b.brain.text import normalize
from team_b.brain.turn import PlannedIntent, Step, TurnContext
from team_b.domain.agent import AgentPlan, CheckRecord, PlanStep, ProposeAction
from team_b.domain.decision import Decision
from team_b.domain.tenant import IntentSpec

OUTCOME_DECISIONS = frozenset({Decision.EXECUTE, Decision.REFUSE})  # a refusal, a done action, a failed one


def slot_of(spec: IntentSpec, argument: str) -> str | None:
    """The slot a tool argument is filled from for this intent (argument_map slot:<name>, else a same-named slot)."""
    source = spec.argument_map.get(argument, "")
    if source.startswith("slot:"):
        return source.removeprefix("slot:")
    if source.startswith("const:"):
        return argument  # a default that a same-named detail the customer stated replaces (a voucher amount)
    return argument if argument in spec.required_slots else None


def _customer_texts(ctx: TurnContext) -> list[str]:
    return [normalize(t) for t in [ctx.text, *(m.text for m in ctx.session.history if m.role == "customer")]]


def _facts_text(ctx: TurnContext) -> set[str]:
    return {normalize(str(v)) for v in ctx.session.facts.values() if isinstance(v, str | int | float) and str(v)}


def stated(ctx: TurnContext, argument: str, value: Any) -> str | None:
    """Why this value may not be used (None: it may): it is not in the customer's words or in what the shop returned."""
    text = normalize(str(value))
    if not text:
        return "it is empty"
    if argument.endswith("order_id"):
        return None if str(value).upper() in known_order_ids(ctx) else "the customer never gave that order number"
    if any(text in said for said in _customer_texts(ctx)):
        return None
    if text in _facts_text(ctx):
        return None
    return "the customer never said it and no tool returned it"


def _outcome_summary(step: Step, spec: IntentSpec) -> str:
    if step.decision is Decision.EXECUTE:
        return f"done: reference {step.values.get('reference', '')}"
    if step.reply_key == "action_failed":
        return "failed: the shop refused it and nothing was changed"
    shown: dict[str, Any] = {
        "refused_by_the_rules": str(step.values.get("message", ""))[:600],
        "citations": list(step.citations),
        "alternatives": list(spec.alternatives),
    }
    return json.dumps(shown, ensure_ascii=False)


def mark_waiting(ctx: TurnContext) -> None:
    """The action waits for the customer's yes: its plan step (the first open one, unless the picker already marked
    one) is "waiting", so the rest of the plan can go on after the answer and a "no" can drop just that step."""
    plan = ctx.session.agent_plan
    if plan is None or any(s.status == "waiting" for s in plan.steps):
        return
    steps = list(plan.steps)
    for index, step in enumerate(steps):
        if step.status == "open":
            steps[index] = PlanStep(text=step.text, status="waiting")
            ctx.session.agent_plan = AgentPlan(steps=steps, revision=plan.revision + 1)
            return


def settle_waiting(ctx: TurnContext, status: str) -> None:
    """The confirmation was answered (or lost): steps that waited for it become done or dropped."""
    plan = ctx.session.agent_plan
    if plan is None or not any(s.status == "waiting" for s in plan.steps):
        return
    steps = [PlanStep(text=s.text, status=status) if s.status == "waiting" else s for s in plan.steps]  # type: ignore[arg-type]
    ctx.session.agent_plan = AgentPlan(steps=steps, revision=plan.revision + 1)


async def propose_action(ctx: TurnContext, move: ProposeAction) -> ReadResult:
    session = ctx.session
    spec = ctx.tenant.intents[move.intent]  # checked before: it is an action intent of this shop
    checks: list[CheckRecord] = []
    ctx.plan = PlannedIntent(move.intent, "action")
    session.active_intent = move.intent
    needed = {s for a in spec.argument_map if (s := slot_of(spec, a))} | set(spec.required_slots)
    entities = ctx.understanding.entities if ctx.understanding is not None else {}
    own = freetext.capture(ctx.text, needed, session.awaiting, other_intent=False, entities=entities)
    session.slots.update(own)  # what the customer says in this very message, as the usual merge stage would
    dropped: list[str] = []
    for argument, value in move.arguments.items():
        slot = slot_of(spec, argument)
        if slot is None:
            dropped.append(f"{argument} (not a detail of this request)")
            continue
        if (why := stated(ctx, argument, value)) is not None:
            dropped.append(f"{argument} ({why})")
            continue
        if slot not in own:  # the customer's own words in this message win over a model's rewording
            session.slots[slot] = str(value)
    for note in dropped:
        checks.append(CheckRecord(name=f"detail_dropped:{note.split(' ')[0]}", passed=True, reason=note))

    step = await actions.handle(ctx, spec)  # the whole gate order, unchanged
    extra = f" (dropped: {'; '.join(dropped)})" if dropped else ""
    if step.decision in OUTCOME_DECISIONS:
        ctx.agent_outcomes.append(step)
        if step.decision is Decision.EXECUTE or step.reply_key == "action_failed":
            ctx.agent_wrote = True  # the shop was asked to change something: a later fallback must not run it again
        return ReadResult(_outcome_summary(step, spec) + extra, None, checks)
    if step.decision is Decision.CONFIRM:
        mark_waiting(ctx)
        return ReadResult(f"waiting for the customer's yes to {move.intent}{extra}", step, checks)
    return ReadResult(f"{step.decision.value}: {step.reply_key}{extra}", step, checks)
