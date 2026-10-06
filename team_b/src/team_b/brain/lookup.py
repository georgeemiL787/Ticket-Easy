"""Lookup requests: "where is my order?".

OWNER: Track A.

The orchestrator calls answer() for every intent of kind "lookup". The order of the checks is fixed:
details -> identity (brain/identity.py) -> read the order -> ownership -> answer from templates.
Nothing about an order is said, stored in the session or put in a reply before the customer is verified and the order
is confirmed to be theirs. An order that belongs to someone else, and an order that does not exist, look the same to
the customer (a handoff, no data), so the lookup cannot be used to find out which orders exist.
A failed read is retried once in the same turn; if it still fails the customer is told so honestly (lookup_failed),
the failure is counted, and at max_tool_failures the conversation goes to a person. Nothing is ever guessed.
"""

from datetime import date
from typing import Any

from team_b.brain import gates, identity, knowledge, shopcalls
from team_b.brain.composer import default_composer
from team_b.brain.slots import resolve_arguments
from team_b.brain.turn import PlannedIntent, Step, TurnContext, locale_of
from team_b.contracts.errors import UpstreamError
from team_b.contracts.evidence import Passage
from team_b.contracts.tools import ToolSpec
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.tenant import IntentSpec


def _to_date(value: Any) -> date | None:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def derive_order_facts(order: dict[str, Any], today: date) -> dict[str, Any]:
    """The order as the shop sent it, plus days_since_delivery, days_late and order_late computed from its dates."""
    facts = dict(order)
    delivered, expected = _to_date(order.get("delivered_at")), _to_date(order.get("expected_delivery_date"))
    if delivered is not None and delivered <= today:
        facts["days_since_delivery"] = (today - delivered).days
    if expected is not None:
        facts["days_late"] = max(0, ((delivered or today) - expected).days)
        facts["order_late"] = facts["days_late"] > 0
    return facts


async def answer(ctx: TurnContext, intent_spec: IntentSpec) -> Step:
    """The reply to a lookup: facts from the shop (never invented), or the next question, or a handoff."""
    from team_b.brain.stages import _tools, ask_for_details  # imported here: stages imports the action module

    planned = ctx.plan or PlannedIntent(name="lookup", kind="lookup")
    if (step := await ask_for_details(ctx, planned)) is not None:
        return step
    if (step := await identity.ensure_verified(ctx)) is not None:
        return step

    tools = await _tools(ctx)
    if tools is None:
        return _handoff(EscalationReason.DEPENDENCY_UNAVAILABLE, "the shop's tool list is not available")
    if (step := await load_own_order(ctx, intent_spec, tools)) is not None:
        return step
    ctx.session.tool_failures = 0  # the read worked
    passages = await _policy_quote(ctx, intent_spec)
    return _status_reply(ctx, ctx.session.facts, passages)


async def load_own_order(ctx: TurnContext, intent_spec: IntentSpec, tools: dict[str, ToolSpec]) -> Step | None:
    """Read the order the customer means and make sure it is theirs. None: ctx.session.facts now holds its facts.

    Otherwise a Step: a handoff (tool missing or blocked, not their order) or an honest "could not look that up".
    Facts are replaced, never merged, so facts of an earlier order can never leak into this one. An intent without a
    lookup tool has no order to read, and its facts are empty."""
    session = ctx.session
    if not intent_spec.lookup_tool:
        session.facts = {}
        return None
    tool = tools.get(intent_spec.lookup_tool)
    if tool is None:
        return _handoff(EscalationReason.CAPABILITY_MISSING, f"the shop does not publish {intent_spec.lookup_tool}")
    if not (gate := gates.check_tool(ctx.tenant, tool)).allowed:
        return _handoff(EscalationReason.UNSUPPORTED, f"permission gate: {gate.reason}: {gate.detail}")

    arguments = resolve_arguments(intent_spec, tool, session, {}).arguments
    outcome = await shopcalls.call_read(ctx, tool.name, arguments)
    if not outcome.ok:
        if outcome.error_code == "NOT_FOUND":
            return _not_theirs(ctx, "the order does not exist")
        return shopcalls.failed_read(ctx, f"reading the order ({outcome.error_code})")
    if not shopcalls.output_complete(tool, outcome.data):
        return shopcalls.failed_read(ctx, "reading the order (the answer was incomplete)")

    order = outcome.data
    asked = arguments.get("order_id")
    if asked is not None and order.get("order_id") != asked:  # the shop answered about another order: not trusted
        return shopcalls.failed_read(ctx, "reading the order (the answer was for a different order)")
    if order.get("customer_id") != session.identity.customer_id:
        return _not_theirs(ctx, "the order belongs to another customer")
    session.facts = derive_order_facts(order, ctx.deps.clock.today())
    return None


def _not_theirs(ctx: TurnContext, why: str) -> Step:
    ctx.session.slots.pop("order_id", None)
    return _handoff(EscalationReason.OWNERSHIP_MISMATCH, why)


def _handoff(reason: EscalationReason, why: str) -> Step:
    return Step(Decision.HANDOFF, reason=why, reply_key=f"handoff_{reason.value}", escalation=reason, awaiting="human")


async def _policy_quote(ctx: TurnContext, spec: IntentSpec) -> list[Passage]:
    """The policy passage that belongs next to this status (e.g. the late-delivery policy), if the tenant wants one."""
    if not spec.knowledge_when or not ctx.session.facts.get(spec.knowledge_when):
        return []
    try:
        return await knowledge.quote_for(ctx, spec.knowledge_query or "")
    except UpstreamError:
        ctx.errors.append("the policy quote is unavailable")
        return []


def _status_reply(ctx: TurnContext, facts: dict[str, Any], passages: list[Passage]) -> Step:
    composer, locale = default_composer(), locale_of(ctx)
    status = str(facts.get("order_status", ""))
    status_key = f"status_{status}"
    shown = composer.t(locale, status_key) if composer.has(locale, status_key) else status
    values = {"order_id": str(facts.get("order_id", "")), "status": shown}
    if status == "delivered" and facts.get("delivered_at"):
        key, values["date"] = "order_status_delivered", str(facts["delivered_at"])[:10]
    elif status == "shipped" and facts.get("expected_delivery_date"):
        key, values["date"] = "order_status_in_transit", str(facts["expected_delivery_date"])[:10]
    else:
        key = "order_status_other"
    return Step(
        Decision.ANSWER,
        reason=f"order {values['order_id']} is {status}",
        reply_key=key,
        values=values,
        citations=tuple(p.passage_id for p in passages),
        passages=tuple(passages),
    )
