"""The orchestrator's read moves (safety-critical): every check passing and failing, with scripted moves, no AI model.

The loop may look up an order, search the policy, ask the customer, hand over and reply. Whatever the picker proposes,
code checks it first: an invented order number, a write tool, someone else's order and a reply that could say more than
was verified are all refused, and nothing is read before the customer is verified.
"""

import json
from typing import Any

import pytest

from team_b.brain.orchestrator import Orchestrator
from team_b.container import Container
from team_b.domain.agent import (
    AgentPlan,
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
from team_b.domain.trace import DecisionTrace
from tests.fakes import FunctionPicker, ScriptedMovePicker

T, C = "shop_001", "conv-reads"
PHONE = "01012345601"  # C-100: NS-20877 (shipped, late), NS-20512, NS-20745
ORDER_DATA = ("Linen summer dress", "TRK-20877", "Cotton jacket", "Polo shirt", "TRK-20790", "Mona", "Street")


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


def status_reply(why: str = "the order was read") -> Reply:
    return Reply(why=why, plan=ReplyPlan(decision=Decision.ANSWER, reply_key="order_status"))


def look(order: str = "NS-20877", tool: str = "get_order", **extra: Any) -> LookUp:
    return LookUp(tool=tool, arguments={"order_id": order, **extra}, why="read the order")


def writes(c: Container) -> list[Any]:
    assert c.shop is not None
    return [e for e in c.shop.audit_log(T) if e.tool not in ("verify_customer", "get_order", "list_customer_orders")]


# ---- a look-up that passes every check ----


async def test_an_order_is_read_once_verified_and_its_status_comes_from_the_order(c: Container) -> None:
    picker = ScriptedMovePicker(look(), status_reply())
    reply = await orch(c, picker).handle_turn(T, C, f"Where is my order NS-20877? My phone is {PHONE}")
    trace = await trace_of(c)
    assert reply.decision is Decision.ANSWER and "NS-20877" in reply.text and "on its way" in reply.text
    first = trace.agent_steps[0]
    assert [ch.name for ch in first.checks] == [
        "on_the_menu",
        "arguments_fit",
        "not_done_before",
        "tool_is_published",
        "tool_is_allowed",
        "tool_only_reads",
        "arguments_match_the_schema",
        "order_id_is_known",
        "customer_id_is_the_verified_customer",
    ]
    assert all(ch.passed for ch in first.checks)
    result = json.loads(first.result)
    assert result["order_id"] == "NS-20877" and result["order_status"] == "shipped" and "customer_id" not in result
    assert not any(token in first.result for token in ORDER_DATA)  # no name, address or item in what the picker sees
    session = await c.sessions.load(T, C)
    assert session is not None and session.identity.verified and session.facts["order_id"] == "NS-20877"
    assert [t.tool for t in trace.tool_calls] == ["verify_customer", "get_order"] and writes(c) == []


async def test_the_picker_is_shown_a_redacted_view_with_the_menu(c: Container) -> None:
    picker = FunctionPicker(lambda v: status_reply() if v.orders else look())
    await orch(c, picker).handle_turn(T, C, f"Where is my order NS-20877? My phone is {PHONE}")
    first, second = picker.views[0], picker.views[1]
    assert PHONE not in first.message and "[phone]" in first.message
    assert not any(PHONE in str(v.entities) for v in picker.views)
    assert not first.identity_verified and first.known_order_ids == ["NS-20877"]
    assert {t.name for t in first.tools} >= {"get_order", "list_customer_orders", "verify_customer"}
    assert all(t.kind == "read" for t in first.tools if t.name == "get_order")
    assert any(t.kind == "write" for t in first.tools) and "refund_request" in {a.intent for a in first.actions}
    assert second.identity_verified and second.orders[0].order_id == "NS-20877" and second.orders[0].status == "shipped"
    assert second.orders[0].days_late == 4 and len(second.moves) == 1


# ---- the checks that refuse ----


@pytest.mark.parametrize(
    ("move", "check", "why"),
    [
        (look(tool="create_refund", amount=100), "tool_only_reads", "changes something"),
        (look(tool="cancel_order"), "tool_only_reads", "changes something"),
        (look(tool="no_such_tool"), "tool_is_published", "publishes"),
        (look(tool="delete_customer"), "tool_is_allowed", "only be used by a person"),
        (LookUp(tool="get_order", arguments={}), "arguments_match_the_schema", "missing required field order_id"),
        (
            LookUp(tool="get_order", arguments={"order_id": "NS-20877", "x": 1}),
            "arguments_match_the_schema",
            "unknown field x",
        ),
        (LookUp(tool="get_order", arguments={"order_id": 20877}), "arguments_match_the_schema", "expected string"),
        (look(order="NS-99999"), "order_id_is_known", "NS-99999"),
        (look(order="NS-20790"), "order_id_is_known", "NS-20790"),
        (
            LookUp(tool="list_customer_orders", arguments={"customer_id": "C-101"}),
            "customer_id_is_the_verified_customer",
            "other",
        ),
    ],
)
async def test_a_look_up_that_breaks_a_rule_is_refused_before_anything_is_read(
    c: Container, move: LookUp, check: str, why: str
) -> None:
    assert c.shop is not None
    picker = ScriptedMovePicker(move, Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="greeting")))
    await orch(c, picker).handle_turn(T, C, "Where is my order NS-20877?")
    first = (await trace_of(c)).agent_steps[0]
    failed = [ch for ch in first.checks if not ch.passed]
    assert failed and failed[0].name == check and why in failed[0].reason
    assert why in picker.views[1].refused  # the picker is told which rule it broke
    assert c.shop.audit_log(T) == []  # nothing reached the shop: not even the identity check


async def test_an_order_number_the_model_made_up_is_refused_even_when_it_looks_real(c: Container) -> None:
    # NS-20790 exists and belongs to someone else: the customer never wrote it, so it cannot even be asked for
    picker = ScriptedMovePicker(look("NS-20790"), look("NS-20790"))
    await orch(c, picker).handle_turn(T, C, f"Where is my order NS-20877? My phone is {PHONE}")
    trace = await trace_of(c)
    assert trace.fallback_reason == "two refused moves in a row"
    assert all(t.arguments.get("order_id") != "NS-20790" for t in trace.tool_calls)  # the made-up number is never read


async def test_an_order_number_from_an_earlier_message_of_the_conversation_is_allowed(c: Container) -> None:
    await orch(
        c, ScriptedMovePicker(Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="greeting")))
    ).handle_turn(T, C, "my order is NS-20877")
    picker = ScriptedMovePicker(look(), status_reply())
    reply = await orch(c, picker).handle_turn(T, C, f"it is late, my phone is {PHONE}")
    assert reply.decision is Decision.ANSWER and "NS-20877" in reply.text


# ---- identity first, ownership, not found ----


async def test_the_customer_is_asked_to_verify_before_any_personal_data_is_read(c: Container) -> None:
    assert c.shop is not None
    picker = ScriptedMovePicker(look())
    reply = await orch(c, picker).handle_turn(T, C, "Where is my order NS-20877?")
    trace = await trace_of(c)
    assert reply.decision is Decision.VERIFY_IDENTITY and reply.awaiting == "slot:phone"
    assert c.shop.audit_log(T) == [] and trace.tool_calls == ()  # not a single call: no order was read
    assert "identity first" in trace.agent_steps[0].result
    session = await c.sessions.load(T, C)
    assert session is not None and session.facts == {} and session.agent_pending == "slot:phone"


async def test_another_customers_order_is_an_immediate_handoff_with_no_data(c: Container) -> None:
    first = ScriptedMovePicker(look(), status_reply())
    await orch(c, first).handle_turn(T, C, f"Where is my order NS-20877? My phone is {PHONE}")
    second = ScriptedMovePicker(look("NS-20790"), status_reply("this must never run"))
    reply = await orch(c, second).handle_turn(T, C, "and what about order NS-20790?")
    trace = await trace_of(c)
    assert reply.decision is Decision.HANDOFF and trace.escalation_reason is EscalationReason.OWNERSHIP_MISMATCH
    assert not any(token.lower() in (reply.text + trace.model_dump_json()).lower() for token in ORDER_DATA[:5])
    assert "not the customer's" in trace.agent_steps[0].result and len(second.views) == 1  # the turn ended there
    session = await c.sessions.load(T, C)
    assert session is not None and session.facts["order_id"] == "NS-20877"  # the first order's facts, not the other's


async def test_an_order_the_shop_does_not_know_is_asked_about_once(c: Container) -> None:
    await orch(c, ScriptedMovePicker(look(), status_reply())).handle_turn(T, C, f"order NS-20877 phone {PHONE}")
    reply = await orch(c, ScriptedMovePicker(look("NS-99999"))).handle_turn(T, C, "and what about order NS-99999?")
    assert reply.decision is Decision.CLARIFY and "NS-**999" in reply.text and reply.awaiting == "order_choice"


async def test_a_failing_read_is_told_honestly_and_nothing_is_guessed(c: Container) -> None:
    assert c.shop is not None
    await orch(c, ScriptedMovePicker(look(), status_reply())).handle_turn(T, C, f"order NS-20877 phone {PHONE}")
    c.shop.fail_next("get_order", "BACKEND_UNAVAILABLE", 2)
    reply = await orch(c, ScriptedMovePicker(look("NS-20512"))).handle_turn(T, C, "what about NS-20512?")
    assert reply.decision is Decision.CLARIFY and "could not look that up" in reply.text


# ---- ask, hand over, choose ----


async def test_the_customers_own_orders_can_be_offered_as_a_choice_and_the_answer_is_understood(c: Container) -> None:
    listing = LookUp(tool="list_customer_orders", arguments={"customer_id": "C-100"}, why="which orders")
    choose = AskCustomer(question_key="disambiguate_order", options=["NS-20877", "NS-20745"], why="which one?")
    await orch(c, ScriptedMovePicker(look(), status_reply())).handle_turn(T, C, f"order NS-20877 phone {PHONE}")
    picker = ScriptedMovePicker(listing, choose)
    asked = await orch(c, picker).handle_turn(T, C, "I want to know about another order")
    assert asked.decision is Decision.CLARIFY and asked.awaiting == "order_choice"
    assert "NS-**877" in asked.text and "NS-**745" in asked.text and "NS-20745" not in asked.text
    session = await c.sessions.load(T, C)
    assert session is not None and session.order_choices == ["NS-20877", "NS-20745"]
    again = await orch(c, ScriptedMovePicker(look("NS-20745"), status_reply())).handle_turn(T, C, "the second")
    assert again.decision is Decision.ANSWER and "NS-20745" in again.text


async def test_a_choice_may_only_offer_orders_the_shop_listed(c: Container) -> None:
    await orch(c, ScriptedMovePicker(look(), status_reply())).handle_turn(T, C, f"order NS-20877 phone {PHONE}")
    bad = AskCustomer(question_key="disambiguate_order", options=["NS-20790"])
    fine = AskCustomer(question_key="ask_order_id", slot="order_id")
    reply = await orch(c, ScriptedMovePicker(bad, fine)).handle_turn(T, C, "which order?")
    trace = await trace_of(c)
    assert trace.agent_steps[0].refused and reply.awaiting == "slot:order_id"


async def test_a_hand_over_needs_a_valid_reason_and_keeps_what_was_tried(c: Container) -> None:
    move = HandOver(reason=EscalationReason.NO_EVIDENCE, tried=["searched twice"], why="not in the policy")
    reply = await orch(c, ScriptedMovePicker(move)).handle_turn(T, C, "do you ship to the moon?")
    trace = await trace_of(c)
    assert reply.decision is Decision.HANDOFF and "searched twice" in trace.decision_reason


# ---- policy search ----


async def test_policy_passages_go_back_to_the_picker_and_the_answer_quotes_them_word_for_word(c: Container) -> None:
    def decide(view: Any) -> Any:
        if not view.passages:
            return SearchPolicy(query="return window days", why="policy question")
        return Reply(
            plan=ReplyPlan(decision=Decision.ANSWER, reply_key="policy_answer", citations=[view.passages[0].id])
        )

    picker = FunctionPicker(decide)
    reply = await orch(c, picker).handle_turn(T, C, "How many days do I have to return an item?")
    trace = await trace_of(c)
    assert reply.decision is Decision.ANSWER and reply.citations and "14" in reply.text
    shown = json.loads(trace.agent_steps[0].result)
    assert shown[0]["id"] == reply.citations[0] and shown[0]["text"]
    assert trace.knowledge_answer and trace.evidence and picker.views[1].passages[0].id == reply.citations[0]


async def test_a_policy_answer_cannot_cite_what_was_not_retrieved_or_answer_without_a_search(c: Container) -> None:
    no_search = Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="policy_answer"))
    found = ScriptedMovePicker(no_search, AskCustomer(question_key="ask_rephrase"))
    await orch(c, found).handle_turn(T, C, "How many days do I have to return an item?")
    assert "no policy passage was retrieved" in found.views[1].refused

    def decide(view: Any) -> Any:
        if not view.passages:
            return SearchPolicy(query="return window days")
        return Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="policy_answer", citations=["made_up@v9#s9"]))

    picker = FunctionPicker(decide)
    await orch(c, picker).handle_turn(T, "conv-2", "How many days do I have to return an item?")
    assert "made_up@v9#s9 was not retrieved" in picker.views[2].refused


async def test_a_search_that_finds_nothing_is_counted_and_the_picker_sees_the_count(c: Container) -> None:
    picker = FunctionPicker(
        lambda v: SearchPolicy(query="zzzz qqqq xxxx") if not v.moves else AskCustomer(question_key="ask_rephrase")
    )
    reply = await orch(c, picker).handle_turn(T, C, "zzzz qqqq xxxx")
    trace = await trace_of(c)
    assert reply.decision is Decision.CLARIFY and trace.agent_steps[0].result == "no policy passage answers this"
    assert picker.views[1].no_evidence_count == 1


async def test_a_policy_search_that_is_down_is_a_handoff_not_a_guess(c: Container) -> None:
    assert c.policy_search is not None
    c.policy_search.fail_next("search_knowledge", 4)
    reply = await orch(c, ScriptedMovePicker(SearchPolicy(query="return policy"))).handle_turn(
        T, C, "What is your return policy?"
    )
    trace = await trace_of(c)
    assert reply.decision is Decision.HANDOFF and trace.escalation_reason is EscalationReason.DEPENDENCY_UNAVAILABLE
    assert reply.citations == ()


# ---- replies are limited to what code can back ----


async def test_an_order_status_reply_needs_an_order_that_was_read(c: Container) -> None:
    picker = ScriptedMovePicker(status_reply(), AskCustomer(question_key="ask_order_id", slot="order_id"))
    await orch(c, picker).handle_turn(T, C, "where is my order")
    assert "no order of the verified customer was read" in picker.views[1].refused


# ---- plan, act, replan ----


async def test_a_message_with_two_requests_is_planned_and_both_are_answered(c: Container) -> None:
    steps = [PlanStep(text="status of NS-20877"), PlanStep(text="how long refunds take")]

    def decide(view: Any) -> Any:
        if not view.orders:
            return MoveProposal(plan=AgentPlan(steps=steps), move=look())
        if not view.moves or view.moves[-1].move.get("kind") == "look_up":
            done = [PlanStep(text=steps[0].text, status="done"), steps[1]]
            return MoveProposal(plan=AgentPlan(steps=done), move=status_reply())
        if not view.passages:
            return SearchPolicy(query="refund time")
        finished = [PlanStep(text=s.text, status="done") for s in steps]
        reply = Reply(
            plan=ReplyPlan(decision=Decision.ANSWER, reply_key="policy_answer", citations=[view.passages[0].id])
        )
        return MoveProposal(plan=AgentPlan(steps=finished), move=reply)

    picker = FunctionPicker(decide)
    reply = await orch(c, picker).handle_turn(T, C, f"Where is my order NS-20877 and how long do refunds take? {PHONE}")
    trace = await trace_of(c)
    assert reply.decision is Decision.ANSWER and reply.citations
    assert "on its way" in reply.text and "[" in reply.text  # the status, then the quoted passage with its citation
    session = await c.sessions.load(T, C)
    assert session is not None and session.agent_plan is not None and not session.agent_plan.open_steps
    assert trace.agent_plan is not None and trace.agent_plan.revision == 3
    assert [r.move["kind"] for r in trace.agent_steps if r.move["kind"] == "plan_update"] == ["plan_update"] * 3


async def test_the_plan_is_changed_after_an_unexpected_status(c: Container) -> None:
    first = AgentPlan(steps=[PlanStep(text="check where the order is")])

    def decide(view: Any) -> Any:
        if not view.orders:
            return MoveProposal(plan=first, move=look("NS-20701"))
        replanned = AgentPlan(steps=[PlanStep(text="tell the customer the order is cancelled", status="done")])
        return MoveProposal(plan=replanned, move=status_reply("it was cancelled, say so"))

    reply = await orch(c, FunctionPicker(decide)).handle_turn(T, C, "Where is order NS-20701? my phone is 01187654306")
    trace = await trace_of(c)
    assert reply.decision is Decision.ANSWER and "cancelled" in reply.text.lower()
    assert trace.agent_plan is not None and trace.agent_plan.revision == 2


async def test_an_open_plan_is_continued_on_the_next_message(c: Container) -> None:
    plan = AgentPlan(steps=[PlanStep(text="verify the customer and look up NS-20877")])
    first = ScriptedMovePicker(MoveProposal(plan=plan, move=AskCustomer(question_key="ask_phone", slot="phone")))
    asked = await orch(c, first).handle_turn(T, C, "where is my order NS-20877")
    assert asked.awaiting == "slot:phone"

    def decide(view: Any) -> Any:
        if not view.orders:
            return look()
        done = AgentPlan(steps=[PlanStep(text=plan.steps[0].text, status="done")])
        return MoveProposal(plan=done, move=status_reply())

    second = FunctionPicker(decide)
    answered = await orch(c, second).handle_turn(T, C, PHONE)
    assert second.views[0].plan is not None and second.views[0].plan.revision == 1  # last turn's plan is in front of it
    assert second.views[0].pending == "slot:phone"
    assert answered.decision is Decision.ANSWER and "NS-20877" in answered.text
    session = await c.sessions.load(T, C)
    assert session is not None and session.agent_plan is not None and session.agent_plan.revision == 2


# ---- nothing is written, ever, in this step ----


async def test_no_write_reaches_the_shop_whatever_the_picker_proposes(c: Container) -> None:
    attempts = [
        look(tool="create_refund", amount=890),
        look(tool="cancel_order"),
        look(tool="apply_voucher", amount=100),
    ]
    picker = ScriptedMovePicker(*attempts, Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="greeting")))
    await orch(c, picker).handle_turn(T, C, f"order NS-20877 phone {PHONE}")
    assert writes(c) == []
