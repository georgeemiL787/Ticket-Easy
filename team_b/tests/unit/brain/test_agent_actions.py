"""Actions through the agent loop (safety-critical): the orchestrator chooses, every gate of actions.py still decides.

Scripted moves stand in for the model. What must hold whatever it proposes: a write needs the customer's yes (or a
person's approval) and a rule check that allowed it; details the customer never gave are dropped; a refusal reaches the
customer with the rule's own words; and the loop never runs an action twice.
"""

import json
from typing import Any

import pytest

from team_b.brain.orchestrator import Orchestrator
from team_b.container import Container
from team_b.domain.agent import (
    AgentPlan,
    AskCustomer,
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
from team_b.domain.trace import DecisionTrace
from tests.fakes import FunctionPicker, ScriptedMovePicker

T, C = "shop_001", "conv-actions"
RETURN = "I want to return order NS-20790, the size is wrong. My phone is 01123456702"  # C-101, 10 days: allowed
REFUND_OLD = "I want a refund for order NS-20512. My phone is 01012345601"  # C-100, 20 days: denied
OUTCOME = Reply(why="tell the outcome", plan=ReplyPlan(decision=Decision.ANSWER, reply_key="action_outcome"))


def orch(c: Container, picker: Any, **options: Any) -> Orchestrator:
    base: dict[str, Any] = {
        "evidence": c.evidence,
        "tenants": c.tenants,
        "capabilities": c.capabilities,
        "policy": c.policy,
    }
    return Orchestrator(
        clock=c.clock, sessions=c.sessions, traces=c.traces, cases=c.cases, move_picker=picker, **{**base, **options}
    )


async def trace_of(c: Container, conversation: str = C) -> DecisionTrace:
    return (await c.traces.for_conversation(T, conversation))[-1]


def writes(c: Container) -> list[Any]:
    assert c.shop is not None
    return [e for e in c.shop.audit_log(T) if e.tool not in ("verify_customer", "get_order", "list_customer_orders")]


def propose(intent: str = "return_request", order: str = "NS-20790", **arguments: Any) -> ProposeAction:
    return ProposeAction(intent=intent, arguments={"order_id": order, **arguments}, why="the customer asked")


# ---- the whole gate order, then the customer's yes ----


async def test_a_proposal_ends_in_a_confirmation_and_nothing_is_written_before_the_yes(c: Container) -> None:
    reply = await orch(c, ScriptedMovePicker(propose())).handle_turn(T, C, RETURN)
    trace = await trace_of(c)
    assert reply.decision is Decision.CONFIRM and reply.awaiting == "confirmation"
    assert writes(c) == [] and [p.decision for p in trace.policy] == [
        "allow"
    ]  # the rule checker was asked, and said yes
    assert [t.tool for t in trace.tool_calls] == ["verify_customer", "get_order"]
    session = await c.sessions.load(T, C)
    assert session is not None and session.agent_pending == "confirmation" and session.pending_action_id
    assert "waiting for the customer's yes" in trace.agent_steps[-1].result


async def test_the_customers_yes_executes_once_through_the_usual_confirmation_flow(c: Container) -> None:
    await orch(c, ScriptedMovePicker(propose())).handle_turn(T, C, RETURN)
    picker = ScriptedMovePicker()
    done = await orch(c, picker).handle_turn(T, C, "yes")
    assert done.decision is Decision.EXECUTE and "RET-" in done.text and len(writes(c)) == 1
    assert picker.views == []  # the yes is handled by the front stage; no plan is open, so the agent is not asked
    again = await orch(c, ScriptedMovePicker()).handle_turn(T, C, "yes")
    assert len(writes(c)) == 1 and again.decision is not Decision.EXECUTE  # a second yes does nothing


async def test_a_no_cancels_and_a_yes_from_before_never_covers_a_new_action(c: Container) -> None:
    await orch(c, ScriptedMovePicker(propose())).handle_turn(T, C, RETURN)
    await orch(c, ScriptedMovePicker()).handle_turn(T, C, "yes")
    assert len(writes(c)) == 1
    fresh = await orch(c, ScriptedMovePicker(propose("cancel_order", "NS-20790"))).handle_turn(
        T, C, "and cancel order NS-20790 too"
    )
    assert writes(c).__len__() == 1 and fresh.decision in (
        Decision.CONFIRM,
        Decision.REFUSE,
        Decision.CLARIFY,
        Decision.HANDOFF,
    )
    declined = await orch(c, ScriptedMovePicker()).handle_turn(T, C, "no")
    assert len(writes(c)) == 1 and declined.decision is not Decision.EXECUTE


@pytest.mark.parametrize(
    "bad",
    [
        Reply(plan=ReplyPlan(decision=Decision.EXECUTE, reply_key="action_done", facts={"reference": "RET-1"})),
        Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="action_done", facts={"reference": "RET-1"})),
        LookUp(tool="create_return", arguments={"order_id": "NS-20790", "reason": "x"}),
    ],
)
async def test_a_model_cannot_execute_or_claim_done_without_the_customers_yes(c: Container, bad: Any) -> None:
    picker = ScriptedMovePicker(bad, bad, propose())
    reply = await orch(c, picker).handle_turn(T, C, RETURN)
    trace = await trace_of(c)
    assert writes(c) == [] and "RET-" not in reply.text
    assert trace.fallback_reason == "two refused moves in a row" or reply.decision is Decision.CONFIRM


# ---- details are the customer's, not the model's ----


async def test_an_amount_the_customer_never_said_is_dropped_and_the_default_stands(c: Container) -> None:
    message = "My order NS-20877 is 4 days late, I want a compensation voucher. My phone is 01012345601"
    move = propose("voucher_request", "NS-20877", amount=5000)
    reply = await orch(c, ScriptedMovePicker(move)).handle_turn(T, C, message)
    trace = await trace_of(c)
    assert reply.decision is Decision.CONFIRM and "5000" not in reply.text and "100" in reply.text
    dropped = [ch for ch in trace.agent_steps[-1].checks if ch.name.startswith("detail_dropped")]
    assert [d.name for d in dropped] == ["detail_dropped:amount"] and "never said" in dropped[0].reason
    assert "dropped: amount" in trace.agent_steps[-1].result


async def test_an_amount_the_customer_did_say_is_used_and_the_rules_judge_it(c: Container) -> None:
    message = "My order NS-20877 is 4 days late, I want a compensation voucher of 300 EGP. My phone is 01012345601"
    reply = await orch(c, ScriptedMovePicker(propose("voucher_request", "NS-20877", amount=300))).handle_turn(
        T, C, message
    )
    assert (
        reply.decision is Decision.HANDOFF
        and (await trace_of(c)).escalation_reason is EscalationReason.APPROVAL_REQUIRED
    )
    assert writes(c) == []  # above the limit: a person must approve, nothing runs


async def test_a_refund_amount_comes_from_the_shops_order_whatever_the_model_proposes(c: Container) -> None:
    message = "I want a refund of 5000 EGP for order NS-20745. My phone is 01012345601"
    reply = await orch(c, ScriptedMovePicker(propose("refund_request", "NS-20745", amount=5000))).handle_turn(
        T, C, message
    )
    assert reply.decision is Decision.CONFIRM and "1250" in reply.text and "5000" not in reply.text.replace("1250", "")
    session = await c.sessions.load(T, C)
    proposal = session.actions[-1] if session else None
    assert proposal is not None and proposal.arguments["amount"] == 1250


async def test_an_order_the_customer_never_named_is_dropped_and_never_read(c: Container) -> None:
    assert c.shop is not None
    picker = ScriptedMovePicker(propose("cancel_order", "NS-20960"))  # a real order, but nobody wrote its number
    reply = await orch(c, picker).handle_turn(T, C, "I want to cancel my order. My phone is 01098765405")
    trace = await trace_of(c)
    assert reply.decision is Decision.VERIFY_IDENTITY or reply.awaiting == "slot:order_id"
    assert any(ch.name == "detail_dropped:order_id" for ch in trace.agent_steps[-1].checks)
    assert all(e.arguments.get("order_id") != "NS-20960" for e in c.shop.audit_log(T)) and writes(c) == []


async def test_an_intent_that_is_not_an_action_is_refused(c: Container) -> None:
    picker = ScriptedMovePicker(propose("greeting"), propose("made_up"))
    await orch(c, picker).handle_turn(T, C, RETURN)
    trace = await trace_of(c)
    assert trace.fallback_reason == "two refused moves in a row" and writes(c) == []


# ---- a "no" from the rules goes back to the orchestrator ----


async def test_a_refusal_comes_back_with_the_rules_words_and_the_alternatives(c: Container) -> None:
    picker = FunctionPicker(lambda v: propose("refund_request", "NS-20512") if not v.moves else OUTCOME)
    reply = await orch(c, picker).handle_turn(T, C, REFUND_OLD)
    trace = await trace_of(c)
    shown = json.loads(picker.views[1].moves[0].result)
    assert "14 days" in shown["refused_by_the_rules"] and shown["alternatives"] == [
        "exchange_request",
        "voucher_request",
    ]
    assert shown["citations"] == ["refund_policy@v1#s1"]
    assert reply.decision is Decision.REFUSE and "14 days" in reply.text and "other options" in reply.text
    assert "refund_policy@v1#s1" in reply.citations and writes(c) == [] and trace.escalation_reason is None


async def test_the_second_request_for_the_same_denied_action_is_still_a_handoff(c: Container) -> None:
    refuse = FunctionPicker(lambda v: propose("refund_request", "NS-20512") if not v.moves else OUTCOME)
    await orch(c, refuse).handle_turn(T, C, REFUND_OLD)
    again = await orch(c, ScriptedMovePicker(propose("refund_request", "NS-20512"))).handle_turn(
        T, C, "I want a refund for order NS-20512 anyway"
    )
    assert (
        again.decision is Decision.HANDOFF and (await trace_of(c)).escalation_reason is EscalationReason.POLICY_DENIED
    )


# ---- the rest of the plan goes on ----


async def test_the_plan_continues_after_the_yes_with_the_next_request(c: Container) -> None:
    message = "cancel order NS-20960 and how long do refunds take? My phone is 01098765405"
    steps = [PlanStep(text="cancel NS-20960"), PlanStep(text="refund time")]

    def decide(view: Any) -> Any:
        if not view.plan or not any(s.text == "cancel NS-20960" and s.status != "done" for s in view.plan.steps):
            pass
        statuses = {s.text: s.status for s in view.plan.steps} if view.plan else {}
        if statuses.get("cancel NS-20960") in (None, "open"):
            return MoveProposal(plan=AgentPlan(steps=steps), move=propose("cancel_order", "NS-20960"))
        if not view.passages:
            return SearchPolicy(query="refund policy 14 days")
        done = AgentPlan(steps=[PlanStep(text=s.text, status="done") for s in steps])
        answer = ReplyPlan(decision=Decision.ANSWER, reply_key="policy_answer", citations=[view.passages[0].id])
        return MoveProposal(plan=done, move=Reply(plan=answer))

    picker = FunctionPicker(decide)
    asked = await orch(c, picker).handle_turn(T, C, message)
    assert asked.decision is Decision.CONFIRM and writes(c) == []
    session = await c.sessions.load(T, C)
    assert session is not None and session.agent_plan is not None
    assert [s.status for s in session.agent_plan.steps] == ["waiting", "open"]  # the cancellation waits for the yes
    done = await orch(c, picker).handle_turn(T, C, "yes")
    assert done.decision is Decision.ANSWER and len(writes(c)) == 1 and "CAN-" in done.text  # the cancellation ran ...
    assert done.citations and "[" in done.text  # ... and the refund-time question was answered in the same turn
    after = await c.sessions.load(T, C)
    assert after is not None and after.agent_plan is not None and not after.agent_plan.open_steps


async def test_a_step_that_waited_for_a_yes_is_dropped_when_the_customer_changes_topic(c: Container) -> None:
    plan = AgentPlan(steps=[PlanStep(text="return NS-20790")])
    await orch(c, ScriptedMovePicker(MoveProposal(plan=plan, move=propose()))).handle_turn(T, C, RETURN)

    def decide(view: Any) -> Any:
        assert view.plan is not None and [s.status for s in view.plan.steps] == ["dropped"]
        return Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="greeting"))

    reply = await orch(c, FunctionPicker(decide)).handle_turn(T, C, "actually, hello")
    assert reply.decision is Decision.ANSWER and writes(c) == []


# ---- when the loop gives up ----


async def test_a_fallback_never_counts_a_refusal_the_loop_recorded(c: Container) -> None:
    picker = ScriptedMovePicker(propose("refund_request", "NS-20512"), RuntimeError("model down"))
    reply = await orch(c, picker).handle_turn(T, C, REFUND_OLD)
    session = await c.sessions.load(T, C)
    trace = await trace_of(c)
    assert trace.fallback_reason == "move picker error: RuntimeError"
    assert reply.decision is Decision.REFUSE  # the usual stages refused it once, as they would have anyway
    assert session is not None and session.denied_actions == ["create_refund"]  # once, not twice


async def test_once_the_shop_was_asked_to_change_something_the_loop_never_runs_the_message_again(c: Container) -> None:
    tenant = c.tenants.get(T)
    direct = tenant.model_copy(
        update={"permissions": tenant.permissions.model_copy(update={"confirm_operation_kinds": ()})}
    )
    picker = ScriptedMovePicker(propose("cancel_order", "NS-20960"), RuntimeError("model down"))
    reply = await orch(c, picker, tenants=TenantRegistry({T: direct})).handle_turn(
        T, C, "cancel order NS-20960. My phone is 01098765405"
    )
    trace = await trace_of(c)
    assert len(writes(c)) == 1 and reply.decision is Decision.EXECUTE and "CAN-" in reply.text
    assert trace.fallback_reason is None and any("agent stopped early" in e for e in trace.errors)
    assert [p.tool for p in trace.proposals] == ["cancel_order"]  # one proposal, not two


async def test_an_outcome_the_orchestrator_forgot_is_still_told(c: Container) -> None:
    picker = FunctionPicker(
        lambda v: (
            propose("refund_request", "NS-20512")
            if not v.moves
            else AskCustomer(question_key="ask_rephrase", why="the model forgot to tell the no")
        )
    )
    reply = await orch(c, picker).handle_turn(T, C, REFUND_OLD)
    assert "14 days" in reply.text  # the rules' answer comes first, whatever the model did next
