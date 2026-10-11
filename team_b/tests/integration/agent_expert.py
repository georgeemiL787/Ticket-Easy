"""A scripted orchestrator that follows the expected path of the lookup, knowledge and action scenarios (no AI model).

It plays the part the real orchestrator will: it reads the AgentView (what the reader understood, what is verified, the
plan it saved, the moves already made) and proposes one move at a time. The loop, the checks, the whole gate order of
actions.py and the stand-in shop, policy search and rule checker are the real ones; only the choosing is scripted. Its
plan steps carry its memory between messages:
  "status:<order id>"  an order status is wanted        "policy"  a policy question
  "action:<intent>:<order id>"  an action is wanted      "lookup:?"  the order number is still missing
"""

import json
import re
from dataclasses import replace
from typing import Any

from team_b.brain.freetext import address_from, reason_from
from team_b.brain.lexicon import default_lexicon
from team_b.brain.multi import assign_order_ids
from team_b.brain.stages import THANKS_TERMS
from team_b.brain.text import find_spans, normalize
from team_b.config import Settings
from team_b.container import Container
from team_b.domain.agent import (
    AgentPlan,
    AgentView,
    AskCustomer,
    HandOver,
    LookUp,
    MoveProposal,
    PlanStep,
    ProposeAction,
    Reply,
    ReplyPlan,
    SearchPolicy,
)
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.tenant import TenantRegistry

_TENANT = TenantRegistry.from_dir(Settings().config_dir).get("shop_001")
AMOUNT = re.compile(r"(\d[\d,]*)\s*(?:egp|le|جنيه)", re.IGNORECASE)
Steps = list[tuple[str, str]]


def _plan(steps: Steps) -> AgentPlan:
    return AgentPlan(steps=[PlanStep(text=text, status=status) for text, status in steps])  # type: ignore[arg-type]


def _mark(steps: Steps, text: str, status: str) -> Steps:
    return [(t, status if t == text else s) for t, s in steps]


def _customer_lines(view: AgentView) -> list[str]:
    return [line.split(": ", 1)[1] for line in view.history if line.startswith("customer: ")] + [view.message]


def _details(view: AgentView, intent: str) -> dict[str, Any]:
    """The details of an action, read from the customer's own words (so the loop's provenance check passes)."""
    out: dict[str, Any] = {}
    for line in reversed(_customer_lines(view)):
        if intent == "change_address" and "new_address" not in out and (address := address_from(line)):
            out["new_address"] = address
        if intent in ("return_request", "exchange_request") and "reason" not in out and (reason := reason_from(line)):
            out["reason"] = reason
        if intent == "complaint" and "description" not in out and (reason := reason_from(line)):
            out["description"] = reason
        if intent == "voucher_request" and "amount" not in out and (found := AMOUNT.search(line)):
            out["amount"] = found.group(1).replace(",", "")
    return out


def _new_steps(view: AgentView, steps: Steps, carried: str = "") -> Steps:
    """The requests of this message that the saved plan does not hold yet, status first, actions in the order said."""
    action_intents = {a.intent for a in view.actions}
    order = view.entities.get("order_id", "") or carried
    own = assign_order_ids(view.message, list(view.intents), _TENANT)  # two orders in one message: each request its own
    wanted: Steps = []
    if "order_status" in view.intents:
        wanted.append((f"status:{own.get('order_status', order) or '?'}", "open"))
    elif not view.intents and order and not any(t.startswith(("status:", "action:")) for t, _ in steps):
        wanted.append((f"status:{order}", "open"))  # "and what about NS-20790?"
    pairs = [set(p) for p in _TENANT.conflicting_intents]
    asking = [i for i in view.intents if i in action_intents]
    if len(asking) >= 2 and set(asking[:2]) in pairs:
        return steps  # "return or exchange?": ask which one before planning either
    wanted += [(f"action:{i}:{own.get(i, order) or '?'}", "open") for i in view.intents if i in action_intents]
    if "policy_question" in view.intents:
        wanted.append(("policy", "open"))
    have = {t.split(":")[0] + ":" + (t.split(":")[1] if t.startswith("action") else "") for t, _ in steps}
    added = [
        (t, s)
        for t, s in wanted
        if t.split(":")[0] + ":" + (t.split(":")[1] if t.startswith("action") else "") not in have
    ]
    return steps + added


def _asks_for_something_else(view: AgentView) -> bool:
    """A clear request ("please", "I want") for something this shop does not do: no intent, no entity."""
    if view.intents or view.entities or len(normalize(view.message).split()) < 3:
        return False
    return any(find_spans(normalize(view.message), normalize(t)) for t in default_lexicon().want_markers.all())


def _corrected(view: AgentView, steps: Steps) -> Steps:
    """The customer corrects an order number the shop did not know ("I meant NS-20512"): the status step follows."""
    named = view.entities.get("order_id", "")
    if not named or view.intents:
        return steps
    unknown = [
        t for t, s in steps if s == "open" and t.startswith("status:") and t.split(":", 1)[1] not in (named, "?")
    ]
    if not unknown or any(t == f"status:{named}" for t, _ in steps):
        return steps
    return [(f"status:{named}" if t == unknown[0] else t, s) for t, s in steps]


def expert(view: AgentView) -> MoveProposal:
    kinds = [str(m.move.get("kind")) for m in view.moves]
    if view.frustration == "high" and view.frustration_strikes >= 2:  # still very upset after one try: a person
        tried = ["apologised and kept working on the request", "the customer is still very frustrated"]
        return MoveProposal(
            plan=_plan([("calm the customer", "open")]),
            move=HandOver(reason=EscalationReason.HIGH_FRUSTRATION, tried=tried),
        )
    steps: Steps = [(s.text, s.status) for s in view.plan.steps] if view.plan else []
    # steps from earlier messages that are already closed are forgotten; open ones carry on
    steps = [(t, s) for t, s in steps if s in ("open", "waiting")]
    if not any(s == "open" for _, s in steps):
        steps = [(t, s) for t, s in steps if s == "waiting"]
    carried = next((t.split(":", 1)[1] for t, _ in steps if t.startswith("choose the request:")), "")
    steps = [(t, s) for t, s in steps if not t.startswith("choose the request")]  # asked last turn, answered now
    steps = _corrected(view, steps)
    steps = _new_steps(view, steps, carried)
    order = view.entities.get("order_id") or (view.known_order_ids[0] if len(view.known_order_ids) == 1 else "")

    current = next((t for t, s in steps if s == "open"), None)
    chosen = [i for i in view.intents if i in {a.intent for a in view.actions}]
    if current is None and len(chosen) >= 2 and {chosen[0], chosen[1]} in [set(p) for p in _TENANT.conflicting_intents]:
        ask = AskCustomer(question_key="disambiguate_intent", options=[chosen[0], chosen[1]], why="return or exchange?")
        carry = view.entities.get("order_id", "")
        return MoveProposal(plan=_plan([(f"choose the request:{carry}", "open")]), move=ask)
    if current is None:
        if "greeting" in view.intents and not steps:
            text = normalize(view.message)
            key = "thanks" if any(find_spans(text, term) for term in THANKS_TERMS) else "greeting"
            return MoveProposal(
                plan=_plan([("greet", "done")]), move=Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key=key))
            )
        if _asks_for_something_else(view):
            refuse = ReplyPlan(decision=Decision.REFUSE, reply_key="out_of_scope")
            return MoveProposal(
                plan=_plan([("out of scope", "done")]), move=Reply(plan=refuse, why="not something I do")
            )
        return MoveProposal(
            plan=_plan(steps or [("understand", "open")]), move=AskCustomer(question_key="ask_rephrase", why="unclear")
        )
    plan = _plan(steps)
    done = _plan(_mark(steps, current, "done"))

    if current == "policy":
        if "search_policy" not in kinds:
            return MoveProposal(plan=plan, move=SearchPolicy(query=view.message, why="a policy question"))
        if view.passages:
            answer = ReplyPlan(decision=Decision.ANSWER, reply_key="policy_answer", citations=[view.passages[0].id])
            return MoveProposal(plan=done, move=Reply(plan=answer))
        if view.no_evidence_count <= 1:
            return MoveProposal(plan=plan, move=AskCustomer(question_key="ask_rephrase", why="nothing matched"))
        if view.no_evidence_count == 2:
            offer = Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="no_answer_offer"), why="say so honestly")
            return MoveProposal(plan=done, move=offer)
        tried = ["searched the policy three times", "asked to rephrase"]
        return MoveProposal(plan=plan, move=HandOver(reason=EscalationReason.NO_EVIDENCE, tried=tried))

    kind, _, rest = current.partition(":")
    if kind in ("status", "lookup"):
        wanted_id = (rest if rest != "?" else "") or order
        if not wanted_id and len(view.known_order_ids) == 1:
            wanted_id = view.known_order_ids[0]
        if "look_up" in kinds and view.orders and view.orders[0].order_id == wanted_id:
            reply = Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="order_status"), why="the order was read")
            return MoveProposal(plan=done, move=reply)
        if not wanted_id:
            return MoveProposal(plan=plan, move=AskCustomer(question_key="ask_order_id", slot="order_id"))
        return MoveProposal(plan=plan, move=LookUp(tool="get_order", arguments={"order_id": wanted_id}, why="status"))

    if kind == "action":
        intent, _, planned_order = rest.partition(":")
        wanted_id = (planned_order if planned_order != "?" else "") or view.entities.get("order_id", "")
        proposed = [m for m in view.moves if m.move.get("kind") == "propose_action"]
        if proposed:
            result = proposed[-1].result
            if result.startswith(("done", "failed")) or result.startswith('{"refused_by_the_rules"'):
                reply = Reply(
                    plan=ReplyPlan(decision=Decision.ANSWER, reply_key="action_outcome"), why="tell the outcome"
                )
                return MoveProposal(plan=done, move=reply)
        if not wanted_id and view.identity_verified and view.customer_id:  # which order? show the customer's own
            listed = [m for m in view.moves if m.move.get("tool") == "list_customer_orders"]
            if not listed:
                look = LookUp(
                    tool="list_customer_orders", arguments={"customer_id": view.customer_id}, why="which order?"
                )
                return MoveProposal(plan=plan, move=look)
            ids = [o["order_id"] for o in json.loads(listed[-1].result)["orders"]][:3]
            return MoveProposal(plan=plan, move=AskCustomer(question_key="disambiguate_order", options=ids))
        arguments = {"order_id": wanted_id, **_details(view, intent)} if wanted_id else _details(view, intent)
        return MoveProposal(plan=plan, move=ProposeAction(intent=intent, arguments=arguments, why="the customer asked"))

    return MoveProposal(plan=plan, move=AskCustomer(question_key="ask_rephrase", why="unclear"))


def with_expert(container: Container) -> Container:
    """The same container, with the real orchestrator now working in agent mode with the scripted picker."""
    from tests.fakes import FunctionPicker

    assert container.orchestrator is not None
    bot: Any = container.orchestrator
    bot._deps = replace(bot._deps, move_picker=FunctionPicker(expert), agent_mode="on")
    return container
