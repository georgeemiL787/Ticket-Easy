"""The stages of one turn. Each is a small async function that reads the TurnContext and may set ctx.step (a decision).

Order (see brain/pipeline.py): handed_off_check, understand, risk_screen, human_request, pending_confirmation, merge,
frustration, plan, handler, queue, handoff, finish. A stage that runs after a decision was made is skipped, except
handoff (it acts on a handoff decision) and finish (it always runs). Every stage returns one line for the trace.

Knowledge, lookup and action requests still get a placeholder "tell me more" reply: the real handlers are added by
later steps through Deps.handlers (one handler per intent kind).
"""

import time
import uuid
from dataclasses import replace

from team_b.brain import actions
from team_b.brain.choices import (
    MAX_ORDER_CHOICES,
    conflicting_pair,
    describe_orders,
    label_of,
    ordinal_choice,
    says_both,
)
from team_b.brain.composer import default_composer
from team_b.brain.handoff import open_case, priority_for
from team_b.brain.language import LANGUAGE_TRUST
from team_b.brain.lexicon import default_lexicon
from team_b.brain.redaction import redact
from team_b.brain.slots import next_question, order_questions, required_slots, resolve_arguments
from team_b.brain.text import find_spans, normalize
from team_b.brain.turn import COMPLETED, MAX_QUEUED_RUNS, PlannedIntent, Step, TurnContext, locale_of
from team_b.contracts.errors import UpstreamError
from team_b.contracts.tools import ToolCallRequest, ToolSpec
from team_b.domain.actions import ActionState
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.handoff import CaseStatus
from team_b.domain.trace import ToolCallRecord
from team_b.domain.understanding import IntentCandidate, Locale
from team_b.observability import get_logger

log = get_logger(__name__)
SLOT_KEYS = ("order_id", "phone", "amount", "item", "reason", "new_address", "description")
THANKS_TERMS = tuple(
    normalize(t)
    for t in (
        "thanks",
        "thank you",
        "thx",
        "bye",
        "goodbye",
        "شكرا",
        "تسلم",
        "مع السلامه",
        "shokran",
        "shukran",
        "tslm",
    )
)
FINISHED_CASES = (CaseStatus.RESOLVED, CaseStatus.RETURNED_TO_AGENT)


def _kind(ctx: TurnContext, intent: str) -> str:
    return ctx.tenant.intents[intent].kind


# ---- a human may already own this conversation ----


async def handed_off_check(ctx: TurnContext) -> str:
    """If a human owns the chat, log the message on the case and reply once that a colleague will answer."""
    session = ctx.session
    if session.status != "handed_off" or session.handoff_case_id is None:
        return "the agent has this conversation"
    case = await ctx.deps.cases.get(ctx.tenant.tenant_id, session.handoff_case_id)
    if case is None or case.status in FINISHED_CASES:
        session.status, session.handoff_case_id, session.handoff_notice_sent = "active", None, False
        session.awaiting = None
        return "the case is closed: the agent resumes"
    case.add_event(actor="customer", kind="customer_message", at=ctx.now, note=redact(ctx.text))
    await ctx.deps.cases.save(case)
    ctx.human_owned = True
    first = not session.handoff_notice_sent
    session.handoff_notice_sent = True
    ctx.step = Step(
        Decision.HANDOFF,
        reason="a colleague owns this conversation; the message was added to the case",
        reply_key="human_will_reply",
        escalation=session.last_escalation or EscalationReason.CUSTOMER_REQUEST,
        awaiting="human",
        silent=not first,
    )
    return f"case {case.case_id} is {case.status.value}: message logged" + ("" if first else ", no reply")


# ---- reading the message ----


async def understand(ctx: TurnContext) -> str:
    """Language, intents, details, yes/no, wants a person, frustration. Never stops the turn."""
    try:
        ctx.understanding = await ctx.deps.nlu.understand(ctx.text, ctx.session, ctx.tenant)
    except Exception:  # even the rules failed: carry on without a reading
        log.exception("understanding_failed")
        ctx.errors.append("understanding failed")
        return "failed: continuing without a reading"
    result = ctx.understanding
    if result.language_confidence >= LANGUAGE_TRUST:
        ctx.session.language = result.language  # a message with no language content keeps the old one
    names = ",".join(i.name for i in result.intents) or "none"
    return f"{result.method}: language={result.language.value} intents={names}"


async def risk_screen(ctx: TurnContext) -> str:
    """Ask the safety screen. A flagged message goes to a human; a screen that cannot answer is noted, never guessed."""
    flags = list(ctx.understanding.safety_flags) if ctx.understanding else []
    assessment = None
    if ctx.deps.evidence is None:
        ctx.risk_unavailable = True
        ctx.errors.append("no safety screen is configured")
    else:
        try:
            assessment = await ctx.deps.evidence.classify_risk(
                ctx.tenant.tenant_id, ctx.text, request_id=ctx.request_id, conversation_id=ctx.session.conversation_id
            )
        except (UpstreamError, NotImplementedError) as exc:
            ctx.risk_unavailable = True
            ctx.errors.append(f"safety screen unavailable: {type(exc).__name__}")
    if assessment is not None:
        ctx.risk = assessment
        flags += [c for c in assessment.categories if c not in flags]
    ctx.session.risk_categories = list(dict.fromkeys([*ctx.session.risk_categories, *flags]))
    if (assessment is not None and assessment.flagged) or (ctx.understanding and ctx.understanding.safety_flags):
        ctx.step = Step(
            Decision.HANDOFF,
            reason=f"risk categories: {', '.join(flags) or 'flagged'}",
            reply_key="handoff_mandatory_risk",
            escalation=EscalationReason.MANDATORY_RISK,
            awaiting="human",
        )
        return f"flagged: {', '.join(flags)}"
    return "screen unavailable: actions stay blocked until it answers" if ctx.risk_unavailable else "clear"


async def human_request(ctx: TurnContext) -> str:
    if ctx.understanding is not None and ctx.understanding.wants_human:
        ctx.step = Step(
            Decision.HANDOFF,
            reason="the customer asked for a person",
            reply_key="handoff_customer_request",
            escalation=EscalationReason.CUSTOMER_REQUEST,
            awaiting="human",
        )
        return "the customer wants a person"
    return "no"


async def pending_confirmation(ctx: TurnContext) -> str:
    """A yes or no to something the agent asked. A different topic cancels the pending action."""
    session = ctx.session
    if session.pending_action_id is None:
        return "nothing pending"
    proposal = next(a for a in session.actions if a.proposal_id == session.pending_action_id)
    understanding = ctx.understanding
    answer = understanding.affirmation if understanding else None
    has_new_topic = bool(understanding and understanding.intents)

    if answer == "yes":
        assert understanding is not None
        ctx.step = await actions.on_confirmation(ctx, understanding)  # Track A: re-check, execute, verify
        return f"yes to {proposal.tool}: handed to the action flow (not built yet)"
    if answer == "no" or has_new_topic:
        proposal.transition(
            ActionState.CANCELLED, at=ctx.now, note="no" if answer == "no" else "the customer changed topic"
        )
        session.pending_action_id, session.awaiting = None, None
        if answer == "no":
            ctx.step = Step(Decision.ANSWER, reason="the customer declined", reply_key="action_cancelled")
            return f"no: {proposal.tool} cancelled"
        return f"topic change: {proposal.tool} cancelled, the new message goes on"
    ctx.step = Step(
        Decision.CLARIFY, reason="waiting for yes or no", reply_key="confirm_again", awaiting="confirmation"
    )
    return "neither yes nor no: asked again"


# ---- what the customer wants ----


def _store_entities(ctx: TurnContext) -> None:
    if ctx.understanding is not None:
        for key, value in ctx.understanding.entities.items():
            if key in SLOT_KEYS:
                ctx.session.slots[key] = value


def _wanted_names(ctx: TurnContext, intents: tuple[IntentCandidate, ...]) -> list[str]:
    return [i.name for i in intents if _kind(ctx, i.name) not in ("smalltalk", "handoff")]


def _ask_which_intent(ctx: TurnContext, options: list[str], *, again: bool) -> Step:
    session = ctx.session
    session.choice_options = options
    locale = locale_of(ctx)
    return Step(
        Decision.CLARIFY,
        reason=f"cannot tell {options[0]} from {options[1]}" + (" (asked again)" if again else ""),
        reply_key="disambiguate_intent",
        values={"a": label_of(ctx.tenant, options[0], locale), "b": label_of(ctx.tenant, options[1], locale)},
        awaiting="intent_choice",
    )


def _resolve_intent_choice(ctx: TurnContext) -> str:
    """The answer to "do you mean A or B?": a position, a name, both, or a change of subject."""
    session, understanding = ctx.session, ctx.understanding
    options, lexicon = list(session.choice_options), default_lexicon()
    chosen: list[str] = []
    if says_both(ctx.text, lexicon):
        chosen = options
    elif (idx := ordinal_choice(ctx.text, lexicon, len(options))) is not None:
        chosen = [options[idx]]
    elif understanding is not None:
        named = list(dict.fromkeys(n for n in _wanted_names(ctx, understanding.intents) if n in options))
        if len(named) == 1:
            chosen = named
        elif not named and _wanted_names(ctx, understanding.intents):
            session.choice_options, session.awaiting = [], None  # the customer moved on to something else
            return "topic change: the question is dropped"
    if chosen and understanding is not None:
        others = tuple(i for i in understanding.intents if i.name not in options)
        picked = tuple(IntentCandidate(name=n, confidence=0.9) for n in chosen)
        ctx.understanding = understanding.model_copy(update={"intents": picked + others})
        session.choice_options, session.awaiting, session.clarifications = [], None, 0
        return f"chose {', '.join(chosen)}"
    if _count_clarification(ctx, "intent_choice"):
        ctx.step = _give_up(ctx, EscalationReason.LOW_CONFIDENCE, "the customer did not say which request they meant")
        return "no answer after asking: giving up"
    ctx.step = _ask_which_intent(ctx, options, again=True)
    return "no clear answer: asked again"


def _resolve_order_choice(ctx: TurnContext) -> str:
    """The answer to "which order?": the number typed in full, a position in the list, or a change of subject."""
    session, understanding = ctx.session, ctx.understanding
    if understanding is not None and understanding.entities.get("order_id"):
        session.order_choices = []
        return "the customer typed the order number"
    idx = ordinal_choice(ctx.text, default_lexicon(), len(session.order_choices))
    if idx is not None:
        session.slots["order_id"] = session.order_choices[idx]
        session.order_choices = []
        return f"chose position {idx + 1}"
    if understanding is not None and _wanted_names(ctx, understanding.intents):
        session.order_choices = []
        return "topic change: the list is dropped"
    return "no usable answer: the list is shown again"


async def disambiguate(ctx: TurnContext) -> str:
    """Ask instead of guessing: two requests that cannot both be meant, or which of the customer's orders."""
    session, understanding = ctx.session, ctx.understanding
    if session.awaiting == "order_choice" and session.order_choices:
        return _resolve_order_choice(ctx)
    if session.awaiting == "intent_choice" and len(session.choice_options) == 2:
        return _resolve_intent_choice(ctx)
    pair = conflicting_pair(understanding.intents, ctx.tenant) if understanding else None
    if pair is None:
        return "no conflict"
    _store_entities(ctx)
    for name in _wanted_names(ctx, understanding.intents if understanding else ()):
        if name not in pair and name not in session.intent_queue:
            session.intent_queue.append(name)  # the rest of the message waits its turn
    session.clarifications = 0
    ctx.step = _ask_which_intent(ctx, list(pair), again=False)
    return f"asked: {pair[0]} or {pair[1]}"


async def merge(ctx: TurnContext) -> str:
    """Details go into slots; new intents start or queue. A short answer to a question is not a new intent."""
    understanding, session = ctx.understanding, ctx.session
    if understanding is None:
        return "no reading to merge"
    for key, value in understanding.entities.items():
        if key in SLOT_KEYS:
            session.slots[key] = value
    wanted = [i.name for i in understanding.intents if _kind(ctx, i.name) not in ("smalltalk", "handoff")]
    answering_a_question = bool(
        session.awaiting
        and (session.awaiting.startswith("slot:") or session.awaiting in ("order_choice", "intent_choice"))
        and not wanted
    )
    for name in wanted:
        if name not in session.intents_seen:
            session.intents_seen.append(name)
        if session.active_intent is None:
            session.active_intent = name
            session.clarifications = 0  # clarifications are counted per intent
        elif name != session.active_intent and name not in session.intent_queue:
            session.intent_queue.append(name)
    if answering_a_question:
        return "details only: answers the question that was asked"
    return f"active={session.active_intent or 'none'} queued={','.join(session.intent_queue) or 'none'}"


async def frustration(ctx: TurnContext) -> str:
    level = ctx.understanding.frustration if ctx.understanding else "low"
    if level == "high" and ctx.tenant.escalation.escalate_on_high_frustration:
        ctx.step = Step(
            Decision.HANDOFF,
            reason="the customer is very frustrated",
            reply_key="handoff_high_frustration",
            escalation=EscalationReason.HIGH_FRUSTRATION,
            awaiting="human",
        )
    return level


async def plan(ctx: TurnContext) -> str:
    """Pick what to work on: small talk if that is all there is, else the intent in progress."""
    understanding, session = ctx.understanding, ctx.session
    names = [i.name for i in understanding.intents] if understanding else []
    small_talk = [n for n in names if _kind(ctx, n) == "smalltalk"]
    if small_talk and len(small_talk) == len(names):
        ctx.plan = PlannedIntent(small_talk[0], "smalltalk")
    elif session.active_intent is not None:
        ctx.plan = PlannedIntent(session.active_intent, _kind(ctx, session.active_intent))
    else:
        ctx.plan = None
    return f"{ctx.plan.name} ({ctx.plan.kind})" if ctx.plan else "nothing to work on"


# ---- doing it ----


async def smalltalk_handler(ctx: TurnContext, planned: PlannedIntent) -> Step:
    """Greetings and thanks are answered from templates; no intent is opened."""
    text = normalize(ctx.text)
    key = "thanks" if any(find_spans(text, term) for term in THANKS_TERMS) else "greeting"
    return Step(Decision.ANSWER, reason=f"small talk ({planned.name})", reply_key=key)


async def placeholder_handler(ctx: TurnContext, planned: PlannedIntent) -> Step:
    """Stands in for the knowledge, lookup and action handlers that later steps build."""
    return Step(
        Decision.CLARIFY,
        reason=f"{planned.kind} handling for {planned.name} is not built yet",
        reply_key="ask_rephrase",
        awaiting="detail",
    )


async def handoff_handler(ctx: TurnContext, planned: PlannedIntent) -> Step:
    return Step(
        Decision.HANDOFF,
        reason=f"intent {planned.name} is handled by a person",
        reply_key="handoff_customer_request",
        escalation=EscalationReason.CUSTOMER_REQUEST,
        awaiting="human",
    )


def _count_clarification(ctx: TurnContext, asking: str) -> bool:
    """Asking for the same thing again (nothing was gained since last turn) counts; True once the limit is reached."""
    session = ctx.session
    session.clarifications = session.clarifications + 1 if session.awaiting == asking else 0
    return session.clarifications >= ctx.tenant.escalation.max_clarifications


def _give_up(ctx: TurnContext, reason: EscalationReason, why: str) -> Step:
    return Step(Decision.HANDOFF, reason=why, reply_key=f"handoff_{reason.value}", escalation=reason, awaiting="human")


async def _tools(ctx: TurnContext) -> dict[str, ToolSpec] | None:
    """The shop's published tools by name, or None when they cannot be listed (then the safe assumptions apply)."""
    if ctx.deps.capabilities is None:
        return None
    try:
        return {t.name: t for t in await ctx.deps.capabilities.list_tools(ctx.tenant.tenant_id)}
    except UpstreamError:
        ctx.errors.append("the shop tool list is unavailable")
        return None


async def _list_orders(ctx: TurnContext) -> list[dict[str, str]] | None:
    """A verified customer's open orders, newest first (at most three), read from the shop. None: ask for the number."""
    session, capabilities = ctx.session, ctx.deps.capabilities
    if capabilities is None or not session.identity.verified or not session.identity.customer_id:
        return None
    request = ToolCallRequest(
        request_id=uuid.uuid4().hex,
        tool="list_customer_orders",
        arguments={"customer_id": session.identity.customer_id},
        idempotency_key=uuid.uuid4().hex,
    )
    started = time.perf_counter()
    try:
        result = await capabilities.call_tool(ctx.tenant.tenant_id, request)
    except UpstreamError as exc:
        ctx.errors.append(f"order list unavailable: {exc.code}")
        return None
    ctx.tool_calls.append(
        ToolCallRecord(
            request_id=request.request_id,
            tool=request.tool,
            operation_kind="read",
            arguments=dict(request.arguments),
            status=result.status,
            error_code=result.error_code,
            audit_id=result.audit_id,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    )
    if result.status != "success":
        ctx.errors.append(f"order list failed: {result.error_code}")
        return None
    orders = [o for o in result.data.get("orders", []) if o.get("order_status") != "cancelled"]
    orders.sort(key=lambda o: str(o.get("placed_at", "")), reverse=True)
    return orders[:MAX_ORDER_CHOICES] or None


async def slot_handler(ctx: TurnContext, planned: PlannedIntent) -> Step:
    """Lookup and action requests: find out which details are still missing and ask for the next one.

    When nothing is missing the rest of the flow (identity check, facts, rules, confirmation, execution) takes over;
    that is built by later steps, so for now this ends in the placeholder reply."""
    spec, session = ctx.tenant.intents[planned.name], ctx.session
    tool_name = spec.lookup_tool if planned.kind == "lookup" else spec.action_tool
    tools = await _tools(ctx)
    tool = tools.get(tool_name) if tools is not None and tool_name else None
    needs_identity = (tool.requires_identity if tool is not None else True) and not session.identity.verified

    resolution = resolve_arguments(spec, tool, session, session.facts)
    if resolution.unsourced:
        why = f"no source for required argument(s): {', '.join(resolution.unsourced)}"
        return _give_up(ctx, EscalationReason.UNSUPPORTED, why)

    wanted = required_slots(spec, tool, needs_identity, ctx.tenant.identity.required_slots)
    missing = [s for s in order_questions(wanted) if not session.slots.get(s) or s in resolution.missing]
    slot = next_question(missing)
    if slot is None:
        session.clarifications = 0
        return await placeholder_handler(ctx, planned)

    if slot == "order_id" and (listing := await _list_orders(ctx)) is not None:
        if _count_clarification(ctx, "order_choice"):
            return _give_up(ctx, EscalationReason.LOW_CONFIDENCE, "the customer did not say which order they meant")
        session.order_choices = [o["order_id"] for o in listing]
        return Step(
            Decision.CLARIFY,
            reason=f"{planned.name} needs order_id: offered {len(listing)} of the customer's orders",
            reply_key="disambiguate_order",
            values={"orders": describe_orders(listing, locale_of(ctx))},
            awaiting="order_choice",
        )

    if _count_clarification(ctx, f"slot:{slot}"):
        why = f"asked for {slot} {session.clarifications} times without an answer"
        return _give_up(ctx, EscalationReason.LOW_CONFIDENCE, why)
    key = f"ask_{slot}" if default_composer().has(Locale.EN, f"ask_{slot}") else "ask_generic"
    own = slot in spec.required_slots or slot in resolution.missing
    return Step(
        Decision.CLARIFY if own else Decision.VERIFY_IDENTITY,
        reason=f"{planned.name} needs {slot}" + ("" if own else " to verify the customer"),
        reply_key=key,
        values={"slot": slot},
        awaiting=f"slot:{slot}",
    )


async def handler(ctx: TurnContext) -> str:
    if ctx.plan is None:
        if _count_clarification(ctx, "detail"):
            ctx.step = _give_up(ctx, EscalationReason.LOW_CONFIDENCE, "the request is still unclear after asking")
            return "no intent: giving up after repeated clarifications"
        ctx.step = Step(Decision.CLARIFY, reason="no intent understood", reply_key="ask_rephrase", awaiting="detail")
        return "no intent: asking for detail"
    chosen = ctx.deps.handlers.get(ctx.plan.kind, placeholder_handler)
    ctx.step = await chosen(ctx, ctx.plan)
    return f"{ctx.plan.kind}: {ctx.step.decision.value}"


async def queue(ctx: TurnContext) -> str:
    """After a completed request, run the next queued one (at most MAX_QUEUED_RUNS in a turn)."""
    session, ran = ctx.session, 0
    while ctx.step is not None and ctx.step.decision in COMPLETED and session.intent_queue and ran < MAX_QUEUED_RUNS:
        name = session.intent_queue.pop(0)
        session.active_intent = name
        ctx.earlier.append(ctx.step)
        planned = PlannedIntent(name, _kind(ctx, name))
        ctx.plan = planned
        ctx.step = await ctx.deps.handlers.get(planned.kind, placeholder_handler)(ctx, planned)
        ran += 1
    return f"ran {ran} queued intent(s)" if ran else "nothing queued"


async def handoff(ctx: TurnContext) -> str:
    """A stage chose to escalate: open the case. (A stub: the real briefing is built by the handoff step.)"""
    step = ctx.step
    assert step is not None and step.escalation is not None
    waiting = next((a for a in ctx.session.actions if a.state is ActionState.AWAITING_HUMAN), None)
    case_id = await open_case(ctx, step.escalation, step.reason, pending_approval=waiting)
    ctx.handoff_case_id = case_id
    ctx.step = replace(step, awaiting="human")
    return f"case {case_id} opened ({priority_for(ctx.tenant, step.escalation)})"
