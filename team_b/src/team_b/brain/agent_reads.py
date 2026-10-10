"""What the agent loop does with the read moves, and what it shows the orchestrator: the checks are all code.

LookUp(tool, arguments) is worked like this, and the first failing check refuses the move (the picker is told why):
  the tool is published, allowed by the permission gate (gates.check_tool) and only READS (a write is refused here: a
  write is an action, and an action goes through the whole gate order of actions.py); the arguments fit the tool's
  schema; every order id came from the customer's own messages or an earlier shop answer of this conversation (never
  from the model alone); a customer_id argument is the verified customer's. Then, when the tool touches personal
data and
  the customer is not verified yet, the move BECOMES the identity step (identity.ensure_verified asks for the order
  number and phone, exactly as today): nothing is read before that. The read itself is shopcalls.call_read (one retry in
  the turn); an order that is not the customer's is an immediate ownership handoff with no data, an order the shop does
  not know is asked about once (lookup._not_found), and facts of the order are kept on the session only for the
  customer's own order.

SearchPolicy(query) runs knowledge.quote_for; the passages (id and a short text) go back to the picker.

Results go back to the picker as short redacted summaries: only the fields it needs (never an address, a phone, a name).
"""

import json
from dataclasses import dataclass, field
from typing import Any

from team_b.brain import gates, identity, knowledge, lookup, shopcalls
from team_b.brain.knowledge_agent import NotFound
from team_b.brain.nlu import extract_order_ids
from team_b.brain.redaction import redact, redact_mapping
from team_b.brain.text import normalize
from team_b.brain.turn import PlannedIntent, Step, TurnContext, locale_of
from team_b.contracts.json_schema import validate
from team_b.contracts.tools import ToolSpec
from team_b.domain.agent import (
    ActionMenuEntry,
    AgentView,
    CheckRecord,
    LookUp,
    OrderBrief,
    PassageBrief,
    SearchPolicy,
    ToolMenuEntry,
)
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.session import SessionState

HISTORY_MESSAGES = 6
MAX_RESULT_CHARS = 1200
PASSAGE_CHARS = 280
MAX_ORDERS_SHOWN = 10
MAX_SEEN_IDS = 30
MAX_PASSAGES_SHOWN = 3
SHORTLIST_OVER = 10  # the tool menu is cut down to the reader's intents when there are more tools than this
ORDER_FIELDS = ("order_id", "order_status", "placed_at", "delivered_at", "expected_delivery_date", "order_total")


@dataclass
class ReadResult:
    """What a read move came to: a redacted summary, a step that ends the turn (if any), and the checks it made."""

    result: str
    step: Step | None = None
    checks: list[CheckRecord] = field(default_factory=list)

    @property
    def refused(self) -> bool:
        return any(not c.passed for c in self.checks)


def _check(name: str, passed: bool, reason: str = "") -> CheckRecord:
    return CheckRecord(name=name, passed=passed, reason="" if passed else reason)


def _handoff(reason: EscalationReason, why: str) -> Step:
    return Step(Decision.HANDOFF, reason=why, reply_key=f"handoff_{reason.value}", escalation=reason, awaiting="human")


# ---- where an order id may come from ----


def remember_order_ids(session: SessionState, ids: list[str]) -> None:
    """Keep the order numbers the customer wrote or the shop returned, so a later move may use them."""
    for order_id in ids:
        if order_id and order_id not in session.order_ids_seen:
            session.order_ids_seen.append(order_id)
    del session.order_ids_seen[:-MAX_SEEN_IDS]


def known_order_ids(ctx: TurnContext) -> set[str]:
    """Every order number this conversation really contains: the customer's messages (this one and the recent ones),
    the details collected, what was offered as a choice, and what the shop returned. Case does not matter."""
    session = ctx.session
    found: set[str] = set(session.order_ids_seen) | set(session.order_choices)
    found |= {str(value) for key, value in session.slots.items() if key == "order_id"}
    found |= {str(session.facts["order_id"])} if session.facts.get("order_id") else set()
    found |= {o["order_id"] for o in ctx.agent_listing if "order_id" in o}
    texts = [ctx.text, *(m.text for m in session.history if m.role == "customer")]
    for text in texts:
        found |= {order_id for _, _, order_id in extract_order_ids(normalize(text), ctx.tenant)}
    return {order_id.upper() for order_id in found}


# ---- the read moves ----


def _order_summary(data: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    brief: dict[str, Any] = {k: data[k] for k in ORDER_FIELDS if k in data}
    if "days_late" in facts:
        brief["days_late"] = facts["days_late"]
    return brief


def _planned_for(ctx: TurnContext, tool: ToolSpec) -> PlannedIntent:
    """The intent the identity questions are asked for: the one that reads with this tool, else any lookup intent."""
    for name, spec in ctx.tenant.intents.items():
        if spec.kind == "lookup" and spec.lookup_tool == tool.name:
            return PlannedIntent(name, "lookup")
    name = next(n for n, s in ctx.tenant.intents.items() if s.kind == "lookup")
    return PlannedIntent(name, "lookup")


async def look_up(ctx: TurnContext, move: LookUp) -> ReadResult:
    from team_b.brain.stages import _tools  # imported here: stages imports the agent module through the pipeline

    session = ctx.session
    tools = await _tools(ctx)
    if tools is None:
        step = _handoff(EscalationReason.DEPENDENCY_UNAVAILABLE, "the shop's tool list is not available")
        return ReadResult("the shop's tool list is not available", step)
    tool = tools.get(move.tool)
    checks = [_check("tool_is_published", tool is not None, f"{move.tool!r} is not a tool this shop publishes")]
    if tool is None:
        return ReadResult("refused", None, checks)
    gate = gates.check_tool(ctx.tenant, tool)
    checks.append(_check("tool_is_allowed", gate.allowed, gate.detail))
    checks.append(
        _check("tool_only_reads", tool.operation_kind == "read", f"{tool.name} changes something: that is an action")
    )
    problems = validate(tool.input_schema, move.arguments)
    checks.append(_check("arguments_match_the_schema", not problems, "; ".join(problems)))
    known = known_order_ids(ctx)
    invented = [str(v) for k, v in move.arguments.items() if k.endswith("order_id") and str(v).upper() not in known]
    checks.append(
        _check(
            "order_id_is_known",
            not invented,
            f"order number {', '.join(invented)} is not one the customer or the shop gave",
        )
    )
    customer = move.arguments.get("customer_id")
    own = customer is None or (session.identity.verified and customer == session.identity.customer_id)
    checks.append(
        _check("customer_id_is_the_verified_customer", own, "a customer id other than the verified customer's")
    )
    if any(not c.passed for c in checks):
        return ReadResult("refused", None, checks)

    if tool.name == ctx.tenant.identity.verify_tool or (tool.requires_identity and not session.identity.verified):
        ctx.plan = ctx.plan or _planned_for(ctx, tool)
        if (first := await identity.ensure_verified(ctx)) is not None:
            return ReadResult(f"identity first: {first.decision.value}", first, checks)  # nothing is read before this
        if tool.name == ctx.tenant.identity.verify_tool:
            return ReadResult("the customer is verified", None, checks)

    outcome = await shopcalls.call_read(ctx, tool.name, dict(move.arguments))
    asked = move.arguments.get("order_id")
    if not outcome.ok:
        if outcome.error_code == "NOT_FOUND" and asked is not None:
            step = await lookup._not_found(ctx, str(asked))
            return ReadResult("the shop does not know that order number", step, checks)
        return ReadResult(
            "the read failed", shopcalls.failed_read(ctx, f"reading {tool.name} ({outcome.error_code})"), checks
        )
    data = outcome.data
    if not shopcalls.output_complete(tool, data):
        return ReadResult(
            "the read failed", shopcalls.failed_read(ctx, f"reading {tool.name} (incomplete answer)"), checks
        )
    session.tool_failures = 0
    if "order_id" in data and "customer_id" in data:
        return _order_result(ctx, data, asked, checks)
    if isinstance(data.get("orders"), list):
        return _listing_result(ctx, data["orders"], checks)
    text = json.dumps(redact_mapping(data), ensure_ascii=False, default=str)
    return ReadResult(text[:MAX_RESULT_CHARS], None, checks)


def _order_result(ctx: TurnContext, data: dict[str, Any], asked: Any, checks: list[CheckRecord]) -> ReadResult:
    session = ctx.session
    if asked is not None and data.get("order_id") != asked:  # the shop answered about another order: not trusted
        step = shopcalls.failed_read(ctx, "reading the order (the answer was for a different order)")
        return ReadResult("the read failed", step, checks)
    if data.get("customer_id") != session.identity.customer_id:
        step = lookup._not_theirs(ctx, "the order belongs to another customer")
        return ReadResult("that order is not the customer's", step, checks)  # not a word of the order's data
    session.order_misses = 0
    session.facts = lookup.derive_order_facts(data, ctx.deps.clock.today())
    remember_order_ids(session, [str(data["order_id"])])
    summary = _order_summary(data, session.facts)
    return ReadResult(json.dumps(summary, ensure_ascii=False, default=str), None, checks)


def _listing_result(ctx: TurnContext, orders: list[Any], checks: list[CheckRecord]) -> ReadResult:
    mine = [o for o in orders if isinstance(o, dict) and o.get("order_id")]
    mine.sort(key=lambda o: str(o.get("placed_at", "")), reverse=True)
    ctx.agent_listing = [{k: str(v) for k, v in o.items() if k in ("order_id", "placed_at")} for o in mine]
    remember_order_ids(ctx.session, [str(o["order_id"]) for o in mine])
    shown = [
        {k: o[k] for k in ("order_id", "order_status", "placed_at", "order_total") if k in o}
        for o in mine[:MAX_ORDERS_SHOWN]
    ]
    return ReadResult(json.dumps({"orders": shown}, ensure_ascii=False, default=str), None, checks)


async def _research(ctx: TurnContext, move: SearchPolicy) -> ReadResult:
    """SearchPolicy with the knowledge agent: search, search again if needed, then answer or say what is missing."""
    try:
        outcome = await knowledge.research(ctx, ctx.text, None, query=move.query)
    except knowledge.SearchUnavailable:
        step = _handoff(EscalationReason.DEPENDENCY_UNAVAILABLE, "the policy search is unavailable")
        return ReadResult("the policy search is unavailable", step)
    if isinstance(outcome, NotFound):
        ctx.session.no_evidence_count += 1
        return ReadResult(f"no policy passage answers this: {outcome.what_was_missing}")
    ctx.session.no_evidence_count = 0
    passages = outcome.passages[:MAX_PASSAGES_SHOWN]
    knowledge.record_evidence(ctx, passages)
    known = {p.passage_id for p in ctx.agent_passages}
    ctx.agent_passages.extend(p for p in passages if p.passage_id not in known)
    if outcome.text:  # the agent's own words, already checked against the passages
        ctx.agent_policy_text, ctx.agent_policy_citations = outcome.text, outcome.citations
    shown = [{"id": p.citation, "text": redact(p.text)[:PASSAGE_CHARS]} for p in passages]
    return ReadResult(json.dumps(shown, ensure_ascii=False))


async def search_policy(ctx: TurnContext, move: SearchPolicy) -> ReadResult:
    if ctx.deps.knowledge_agent is not None:
        return await _research(ctx, move)
    errors_before = len(ctx.errors)
    passages = await knowledge.quote_for(ctx, move.query)
    if not passages and any(e.startswith("policy search failed") for e in ctx.errors[errors_before:]):
        step = _handoff(EscalationReason.DEPENDENCY_UNAVAILABLE, "the policy search is unavailable")
        return ReadResult("the policy search is unavailable", step)
    if not passages:
        ctx.session.no_evidence_count += 1
        return ReadResult("no policy passage answers this")
    ctx.session.no_evidence_count = 0
    known = {p.passage_id for p in ctx.agent_passages}
    ctx.agent_passages.extend(p for p in passages if p.passage_id not in known)
    shown = [{"id": p.citation, "text": redact(p.text)[:PASSAGE_CHARS]} for p in passages]
    return ReadResult(json.dumps(shown, ensure_ascii=False))


# ---- what the picker is shown ----


def _shortlist(tools: dict[str, ToolSpec], ctx: TurnContext) -> list[ToolSpec]:
    """All the allowed tools, or the ones the reader's intents point at when there are many."""
    allowed = [t for t in tools.values() if gates.check_tool(ctx.tenant, t).allowed]
    if len(allowed) <= SHORTLIST_OVER:
        return allowed
    wanted = {ctx.tenant.identity.verify_tool}
    for intent in ctx.understanding.intents if ctx.understanding else ():
        spec = ctx.tenant.intents.get(intent.name)
        if spec is not None:
            wanted |= {t for t in (spec.lookup_tool, spec.action_tool) if t}
    wanted |= {t.name for t in allowed if t.operation_kind == "read"}  # reads are cheap to show
    return [t for t in allowed if t.name in wanted]


async def build_view(ctx: TurnContext, refused: str | None, moves: list[Any]) -> AgentView:
    """Everything the orchestrator may see this ask, built by code with personal values hidden."""
    from team_b.brain.stages import _tools  # imported here: stages imports the agent module through the pipeline

    session, reading = ctx.session, ctx.understanding
    tools = await _tools(ctx) or {}
    menu = [
        ToolMenuEntry(
            name=t.name,
            description=t.description or t.title,
            kind="read" if t.operation_kind == "read" else "write",
            arguments=t.input_schema,
        )
        for t in _shortlist(tools, ctx)
    ]
    actions = [
        ActionMenuEntry(intent=name, description=spec.description)
        for name, spec in ctx.tenant.intents.items()
        if spec.kind == "action"
    ]
    orders: list[OrderBrief] = []
    if session.identity.verified and session.facts.get("order_id"):
        facts = session.facts
        orders.append(
            OrderBrief(
                order_id=str(facts["order_id"]),
                status=str(facts.get("order_status", "")),
                delivered_at=str(facts["delivered_at"])[:10] if facts.get("delivered_at") else None,
                expected_delivery_date=str(facts["expected_delivery_date"])[:10]
                if facts.get("expected_delivery_date")
                else None,
                total=float(facts["order_total"]) if isinstance(facts.get("order_total"), int | float) else None,
                days_late=int(facts["days_late"]) if isinstance(facts.get("days_late"), int) else None,
            )
        )
    recent = session.history[-(HISTORY_MESSAGES + 1) : -1]  # the current message is shown on its own
    return AgentView(
        message=redact(ctx.text),
        turn_id=ctx.trace_id,
        locale=locale_of(ctx).value,
        history=[f"{m.role}: {redact(m.text)}" for m in recent],
        language=reading.language.value if reading else None,
        intents=[i.name for i in reading.intents] if reading else [],
        entities={k: str(v) for k, v in redact_mapping(reading.entities).items()} if reading else {},
        frustration=reading.frustration if reading else "low",
        identity_verified=session.identity.verified,
        customer_id=session.identity.customer_id if session.identity.verified else None,
        orders=orders,
        known_order_ids=sorted(known_order_ids(ctx)),
        passages=[PassageBrief(id=p.citation, text=redact(p.text)[:PASSAGE_CHARS]) for p in ctx.agent_passages],
        no_evidence_count=session.no_evidence_count,
        questions_asked=session.agent_questions,
        frustration_strikes=session.frustration_strikes,
        tools=menu,
        actions=actions,
        plan=session.agent_plan,
        moves=list(moves),
        refused=refused,
        pending=session.agent_pending,
        told_already=[
            f"{s.decision.value}: {s.reply_key} {s.values.get('reference', '')}".strip() for s in ctx.earlier
        ],
    )
