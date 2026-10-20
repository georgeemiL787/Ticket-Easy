"""Lookup requests: "where is my order?".


The orchestrator calls answer() for every intent of kind "lookup". The order of the checks is fixed:
details -> identity (actions/identity.py) -> read the order -> ownership -> answer from templates.
Nothing about an order is said, stored in the session or put in a reply before the customer is verified and the order
is confirmed to be theirs. An order that belongs to someone else, and an order that does not exist, look the same to
the customer (a handoff, no data), so the lookup cannot be used to find out which orders exist.
A failed read is retried once in the same turn; if it still fails the customer is told so honestly (lookup_failed),
the failure is counted, and at max_tool_failures the conversation goes to a person. Nothing is ever guessed.
"""

from datetime import date
from typing import Any

from agent_service.actions import identity, permissions, shop_calls
from agent_service.contracts.errors import UpstreamError
from agent_service.contracts.evidence import Passage
from agent_service.contracts.tools import ToolSpec
from agent_service.domain.decision import Decision, EscalationReason
from agent_service.domain.tenant import IntentSpec
from agent_service.knowledge import answers
from agent_service.replies.composer import PLACEHOLDERS, default_composer
from agent_service.turn.context import PlannedIntent, Step, TurnContext, locale_of
from agent_service.understanding.choices import describe_orders, mask_order_id
from agent_service.understanding.slots import resolve_arguments

MAX_ORDER_MISSES = 2  # unknown order numbers in a row before a person takes over


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
    from agent_service.turn.stages import _tools, ask_for_details  # imported here: stages imports the action module

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
    if not (gate := permissions.check_tool(ctx.tenant, tool)).allowed:
        return _handoff(EscalationReason.UNSUPPORTED, f"permission gate: {gate.reason}: {gate.detail}")

    arguments = resolve_arguments(intent_spec, tool, session, {}).arguments
    outcome = await shop_calls.call_read(ctx, tool.name, arguments)
    if not outcome.ok:
        if outcome.error_code == "NOT_FOUND":
            return await _not_found(ctx, str(arguments.get("order_id", "")))
        return shop_calls.failed_read(ctx, f"reading the order ({outcome.error_code})")
    if not shop_calls.output_complete(tool, outcome.data):
        return shop_calls.failed_read(ctx, "reading the order (the answer was incomplete)")

    order = outcome.data
    asked = arguments.get("order_id")
    if asked is not None and order.get("order_id") != asked:  # the shop answered about another order: not trusted
        return shop_calls.failed_read(ctx, "reading the order (the answer was for a different order)")
    if order.get("customer_id") != session.identity.customer_id:
        return _not_theirs(ctx, "the order belongs to another customer")
    session.order_misses = 0
    session.facts = derive_order_facts(order, ctx.deps.clock.today())
    return None


async def _not_found(ctx: TurnContext, order_id: str) -> Step:
    """The shop does not know this order number. The first time it is probably a typo: ask for the number again (with
    the customer's own open orders to pick from when the shop lists them). A second miss in a row goes to a person.

    This is different from another customer's order, which is an immediate handoff with no data (_not_theirs)."""
    from agent_service.turn.stages import _list_orders  # imported here: stages imports the action module

    session = ctx.session
    session.order_misses += 1
    session.slots.pop("order_id", None)
    if session.order_misses >= MAX_ORDER_MISSES:
        return _handoff(
            EscalationReason.OWNERSHIP_MISMATCH, f"the shop does not know the order number ({order_id}) twice"
        )
    masked = mask_order_id(order_id) if order_id else ""
    if (listing := await _list_orders(ctx)) is not None:
        session.order_choices = [o["order_id"] for o in listing]
        return Step(
            Decision.CLARIFY,
            reason="the shop does not know that order number: asked again with the customer's own orders",
            reply_key="order_not_found_choose",
            values={"order_id": masked, "orders": describe_orders(listing, locale_of(ctx))},
            awaiting="order_choice",
        )
    return Step(
        Decision.CLARIFY,
        reason="the shop does not know that order number: asked to check it",
        reply_key="order_not_found",
        values={"order_id": masked},
        awaiting="slot:order_id",
    )


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
        return await answers.quote_for(ctx, spec.knowledge_query or "")
    except UpstreamError:
        ctx.errors.append("the policy quote is unavailable")
        return []


def _return_reply(ctx: TurnContext, facts: dict[str, Any], passages: list[Passage]) -> Step:
    """Where a return and its refund are, from the shop's returns table (amounts and dates exactly as it sent them)."""
    composer, locale = default_composer(), locale_of(ctx)
    order_id = str(facts.get("order_id", ""))
    returns = sorted(facts.get("returns") or [], key=lambda r: str(r.get("return_id", "")))
    if not returns:
        return Step(
            Decision.ANSWER,
            reason=f"order {order_id} has no return",
            reply_key="return_none",
            values={"order_id": order_id},
        )
    latest = returns[-1]
    status = str(latest.get("status", ""))
    key = f"return_status_{status}" if composer.has(locale, f"return_status_{status}") else "return_status_other"
    destination = str(latest.get("refund_destination") or "unknown")
    destination_key = (
        f"refund_to_{destination}" if composer.has(locale, f"refund_to_{destination}") else "refund_to_unknown"
    )
    amount = latest.get("refund_amount_egp")
    values = {
        "order_id": order_id,
        "return_id": str(latest.get("return_id", "")),
        "status": status.replace("_", " "),
        "amount": f"{amount:g}" if isinstance(amount, int | float) else "",
        "destination": composer.t(locale, destination_key),
        "date": str(latest.get("refund_initiated_at") or latest.get("requested_at") or "")[:10],
    }
    used = PLACEHOLDERS.get(
        key, frozenset()
    )  # only the facts this sentence is about: a date must not be read as another
    values = {k: v for k, v in values.items() if k in used}
    return Step(
        Decision.ANSWER,
        reason=f"return {latest.get('return_id')} of order {order_id} is {status}",
        reply_key=key,
        values=values,
        citations=tuple(p.passage_id for p in passages),
        passages=tuple(passages),
    )


def _status_reply(ctx: TurnContext, facts: dict[str, Any], passages: list[Passage]) -> Step:
    if "returns" in facts:  # a return and refund lookup (get_return_status), not an order lookup
        return _return_reply(ctx, facts, passages)
    composer, locale = default_composer(), locale_of(ctx)
    status = str(facts.get("order_status", ""))
    status_key = f"status_{status}"
    shown = composer.t(locale, status_key) if composer.has(locale, status_key) else status
    values = {"order_id": str(facts.get("order_id", "")), "status": shown}
    if status == "delivered" and facts.get("delivered_at"):
        key, values["date"] = "order_status_delivered", str(facts["delivered_at"])[:10]
    elif status == "shipped" and facts.get("expected_delivery_date"):
        key, values["date"] = "order_status_in_transit", str(facts["expected_delivery_date"])[:10]
    elif status in ("pending", "processing") and facts.get("expected_delivery_date"):
        key, values["date"] = "order_status_preparing", str(facts["expected_delivery_date"])[:10]
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
