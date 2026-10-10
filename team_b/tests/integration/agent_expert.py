"""A scripted orchestrator that follows the expected path of the lookup and knowledge scenarios (no AI model).

It plays the part the real orchestrator will: it reads the AgentView (what the reader understood, what is verified, the
plan it saved, the moves already made) and proposes one move at a time. The loop, the checks and the stand-in shop and
policy search are the real ones; only the choosing is scripted. Its plan steps carry its memory between messages:
"lookup:<order id>" (an order status is wanted), "lookup:?" (the number is still missing), "policy" (a policy question).
"""

from dataclasses import replace
from typing import Any

from team_b.brain.stages import THANKS_TERMS
from team_b.brain.text import find_spans, normalize
from team_b.container import Container
from team_b.domain.agent import (
    AgentPlan,
    AgentView,
    AskCustomer,
    HandOver,
    LookUp,
    MoveProposal,
    PlanStep,
    Reply,
    ReplyPlan,
    SearchPolicy,
)
from team_b.domain.decision import Decision, EscalationReason


def _plan(text: str, status: str = "open") -> AgentPlan:
    return AgentPlan(steps=[PlanStep(text=text, status=status)])  # type: ignore[arg-type]


def _open_step(view: AgentView) -> str:
    if view.plan is None:
        return ""
    return next((s.text for s in view.plan.steps if s.status == "open"), "")


def _kinds(view: AgentView) -> list[str]:
    return [str(m.move.get("kind")) for m in view.moves]


def expert(view: AgentView) -> MoveProposal:
    kinds, step = _kinds(view), _open_step(view)
    names_an_order = not view.intents and bool(view.entities.get("order_id"))  # "and what about NS-20790?"
    wants_order = "order_status" in view.intents or step.startswith("lookup:") or names_an_order
    wants_policy = ("policy_question" in view.intents or step == "policy") and not wants_order

    if wants_policy:
        if "search_policy" not in kinds:
            return MoveProposal(plan=_plan("policy"), move=SearchPolicy(query=view.message, why="a policy question"))
        if view.passages:
            reply = Reply(
                plan=ReplyPlan(decision=Decision.ANSWER, reply_key="policy_answer", citations=[view.passages[0].id])
            )
            return MoveProposal(plan=_plan("policy", "done"), move=reply)
        if view.no_evidence_count <= 1:
            return MoveProposal(
                plan=_plan("policy"), move=AskCustomer(question_key="ask_rephrase", why="nothing matched")
            )
        if view.no_evidence_count == 2:
            offer = Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="no_answer_offer"), why="say so honestly")
            return MoveProposal(plan=_plan("policy", "done"), move=offer)
        tried = ["searched the policy three times", "asked to rephrase"]
        return MoveProposal(plan=_plan("policy"), move=HandOver(reason=EscalationReason.NO_EVIDENCE, tried=tried))

    if wants_order:
        order = view.entities.get("order_id") or (step.split(":", 1)[1] if step.startswith("lookup:") else "")
        if not order or order == "?":
            known = view.known_order_ids
            order = known[0] if len(known) == 1 else ""
        if "look_up" in kinds and view.orders and view.orders[0].order_id == order:
            reply = Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="order_status"), why="the order was read")
            return MoveProposal(plan=_plan(f"lookup:{order}", "done"), move=reply)
        if not order:
            return MoveProposal(plan=_plan("lookup:?"), move=AskCustomer(question_key="ask_order_id", slot="order_id"))
        read = LookUp(tool="get_order", arguments={"order_id": order}, why="where is the order")
        return MoveProposal(plan=_plan(f"lookup:{order}"), move=read)

    if "greeting" in view.intents:
        text = normalize(view.message)
        key = "thanks" if any(find_spans(text, term) for term in THANKS_TERMS) else "greeting"
        return MoveProposal(
            plan=_plan("greet", "done"), move=Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key=key))
        )
    return MoveProposal(plan=_plan("understand"), move=AskCustomer(question_key="ask_rephrase", why="unclear"))


def with_expert(container: Container) -> Container:
    """The same container, with the real orchestrator now working in agent mode with the scripted picker."""
    from tests.fakes import FunctionPicker

    assert container.orchestrator is not None
    bot: Any = container.orchestrator
    bot._deps = replace(bot._deps, move_picker=FunctionPicker(expert), agent_mode="on")
    return container
