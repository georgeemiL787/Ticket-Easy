"""The agent loop's frame (safety-critical): moves are checked by code, limits and fallbacks hold, nothing is written.

The moves are scripted (ScriptedMovePicker): no AI model is involved. What the loop must guarantee is shown with moves a
confused or hostile model might propose.
"""

import asyncio
from typing import Any

import pytest

from team_b.brain.orchestrator import Orchestrator
from team_b.container import Container
from team_b.domain.agent import (
    AgentPlan,
    AskCustomer,
    HandOver,
    LookUp,
    PlanStep,
    ProposeAction,
    Reply,
    ReplyPlan,
    SearchPolicy,
    parse_move,
)
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.trace import DecisionTrace, record_problems
from tests.fakes import FakeEvidence, ScriptedMovePicker

T, C = "shop_001", "conv-agent"
GREETING = Reply(why="a greeting", plan=ReplyPlan(decision=Decision.ANSWER, reply_key="greeting"))


def orch(c: Container, picker: Any = None, **options: Any) -> Orchestrator:
    base: dict[str, Any] = {"evidence": FakeEvidence(), "tenants": c.tenants, "capabilities": c.capabilities}
    return Orchestrator(
        clock=c.clock, sessions=c.sessions, traces=c.traces, cases=c.cases, move_picker=picker, **{**base, **options}
    )


async def trace_of(c: Container, conversation: str = C) -> DecisionTrace:
    return (await c.traces.for_conversation(T, conversation))[-1]


def stages(trace: DecisionTrace) -> dict[str, str]:
    return {s.stage: s.status for s in trace.steps}


# ---- mode and availability ----


async def test_with_no_model_for_the_orchestrator_the_usual_stages_run_and_the_trace_says_so(c: Container) -> None:
    reply = await orch(c).handle_turn(T, C, "Hello")
    trace = await trace_of(c)
    assert reply.decision is Decision.ANSWER and "agent" not in stages(trace)
    assert trace.agent_mode == "on" and trace.versions["agent"] == "unavailable: no model"
    assert trace.agent_steps == () and trace.fallback_reason is None


async def test_mode_off_is_exactly_the_usual_flow_even_with_a_picker(c: Container) -> None:
    picker = ScriptedMovePicker()
    reply = await orch(c, picker, agent_mode="off").handle_turn(T, C, "Hello")
    trace = await trace_of(c)
    assert picker.views == [] and "agent" not in stages(trace) and "disambiguate" in stages(trace)
    assert reply.decision is Decision.ANSWER and trace.agent_mode == "off" and "agent" not in trace.versions


async def test_shadow_behaves_like_off_until_it_is_built(c: Container) -> None:
    picker = ScriptedMovePicker()
    await orch(c, picker, agent_mode="shadow").handle_turn(T, C, "Hello")
    assert picker.views == [] and (await trace_of(c)).agent_mode == "shadow"


async def test_a_business_can_set_its_own_mode(c: Container) -> None:
    tenant = c.tenants.get(T)
    from team_b.domain.tenant import TenantRegistry

    only_off = TenantRegistry({T: tenant.model_copy(update={"agent_mode": "off"})})
    picker = ScriptedMovePicker()
    await orch(c, picker, tenants=only_off, agent_mode="on").handle_turn(T, C, "Hello")
    assert picker.views == [] and (await trace_of(c)).agent_mode == "off"


# ---- the agent stage replaces the middle ----


async def test_a_reply_move_ends_the_turn_with_todays_template(c: Container) -> None:
    picker = ScriptedMovePicker(GREETING)
    reply = await orch(c, picker).handle_turn(T, C, "Hello")
    trace = await trace_of(c)
    assert reply.text == "Hello! How can I help you today?" and reply.decision is Decision.ANSWER
    s = stages(trace)
    assert s["agent"] == "ok" and all(
        s.get(n) is None for n in ("disambiguate", "merge", "frustration", "plan", "handler")
    )
    assert s["handoff"] == "skipped" and s["finish"] == "ok"  # the tail stays: handoff acts only on a handoff
    assert trace.agent_mode == "on" and [r.move["kind"] for r in trace.agent_steps] == ["reply"]
    assert trace.agent_steps[0].checks and all(check.passed for check in trace.agent_steps[0].checks)
    assert record_problems(trace) == []


async def test_the_front_stages_still_run_first_and_a_forced_handoff_never_waits_for_the_agent(c: Container) -> None:
    picker = ScriptedMovePicker(GREETING)
    reply = await orch(c, picker).handle_turn(T, C, "I want to talk to a human")
    trace = await trace_of(c)
    assert reply.decision is Decision.HANDOFF and picker.views == []  # the person was asked for: code decided at once
    assert stages(trace)["agent"] == "skipped" and trace.escalation_reason is EscalationReason.CUSTOMER_REQUEST


async def test_ask_customer_asks_for_a_detail_with_todays_question(c: Container) -> None:
    picker = ScriptedMovePicker(AskCustomer(why="need the order", question_key="ask_order_id", slot="order_id"))
    reply = await orch(c, picker).handle_turn(T, C, "where is my order")
    session = await c.sessions.load(T, C)
    assert reply.decision is Decision.CLARIFY and reply.awaiting == "slot:order_id"
    assert "order number" in reply.text and session is not None and session.agent_pending == "slot:order_id"


async def test_hand_over_opens_a_case_with_what_was_tried(c: Container) -> None:
    move = HandOver(reason=EscalationReason.LOW_CONFIDENCE, tried=["asked twice", "searched the policy"], why="unclear")
    reply = await orch(c, ScriptedMovePicker(move)).handle_turn(T, C, "gibberish")
    trace = await trace_of(c)
    assert reply.decision is Decision.HANDOFF and trace.escalation_reason is EscalationReason.LOW_CONFIDENCE
    assert stages(trace)["handoff"] == "ok" and trace.handoff_case_id
    assert "asked twice" in trace.decision_reason


async def test_work_moves_answer_not_built_yet_and_the_loop_goes_on(c: Container) -> None:
    picker = ScriptedMovePicker(
        LookUp(tool="get_order", arguments={"order_id": "NS-20877"}, why="status"),
        SearchPolicy(query="return window", why="policy"),
        ProposeAction(intent="refund_request", arguments={"order_id": "NS-20877"}, why="refund"),
        GREETING,
    )
    reply = await orch(c, picker).handle_turn(T, C, "hi")
    trace = await trace_of(c)
    assert reply.decision is Decision.ANSWER
    assert [r.result for r in trace.agent_steps[:3]] == ["not built yet"] * 3
    assert len(picker.views) == 4 and len(picker.views[3].moves) == 3  # the picker sees what was already tried


async def test_the_loop_cannot_write_or_read_the_shop_yet(c: Container) -> None:
    assert c.shop is not None
    picker = ScriptedMovePicker(
        ProposeAction(intent="cancel_order", arguments={"order_id": "NS-20899"}),
        LookUp(tool="create_refund", arguments={"order_id": "NS-20899", "amount": 5000}),
        GREETING,
    )
    await orch(c, picker, capabilities=c.shop).handle_turn(T, C, "cancel my order")
    assert c.shop.audit_log(T) == []  # no call reached the shop, read or write
    assert c.shop.change_count(T) == 0


# ---- code refuses what is not safe ----


@pytest.mark.parametrize(
    ("move", "why"),
    [
        (Reply(plan=ReplyPlan(decision=Decision.EXECUTE, reply_key="greeting")), "answer, clarify or refuse"),
        (Reply(plan=ReplyPlan(decision=Decision.HANDOFF, reply_key="greeting")), "answer, clarify or refuse"),
        (Reply(plan=ReplyPlan(decision=Decision.CONFIRM, reply_key="greeting")), "answer, clarify or refuse"),
        (Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="action_done")), "reserved for code"),
        (Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="handoff_generic")), "reserved for code"),
        (Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="no_such_key")), "unknown"),
        (Reply(plan=ReplyPlan(decision=Decision.ANSWER)), "unknown"),
        (Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="greeting", facts={"amount": "5000"})), "verified"),
        (
            Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="greeting", citations=["return_policy@v2#s2"])),
            "citation",
        ),
        (AskCustomer(question_key="greeting"), "no question template"),
        (AskCustomer(question_key="ask_generic"), "not a detail this agent collects"),
        (AskCustomer(question_key="ask_order_id", slot="password"), "not a detail"),
        (ProposeAction(intent="made_up", arguments={}), "not an action"),
        (ProposeAction(intent="greeting", arguments={}), "not an action"),
        (LookUp(tool="get_order", arguments={"note": "x" * 3000}), "characters"),
    ],
)
async def test_a_move_code_does_not_accept_is_refused_and_the_picker_is_told_why(
    c: Container, move: Any, why: str
) -> None:
    picker = ScriptedMovePicker(move, GREETING)
    reply = await orch(c, picker).handle_turn(T, C, "hi")
    trace = await trace_of(c)
    assert reply.decision is Decision.ANSWER and reply.text == "Hello! How can I help you today?"
    assert trace.agent_steps[0].refused and why in picker.views[1].refused
    assert why in trace.agent_steps[0].result and trace.fallback_reason is None  # one refusal is not a fallback


async def test_the_same_look_up_twice_is_refused_the_second_time(c: Container) -> None:
    look = LookUp(tool="get_order", arguments={"order_id": "NS-20877"})
    picker = ScriptedMovePicker(look, look.model_copy(update={"why": "again"}), GREETING)
    await orch(c, picker).handle_turn(T, C, "hi")
    steps = (await trace_of(c)).agent_steps
    assert [s.result for s in steps][:2] == [
        "not built yet",
        "refused: the same look-up with the same arguments was already made",
    ]
    other = SearchPolicy(query="a"), SearchPolicy(query="a"), SearchPolicy(query="b"), GREETING
    await orch(c, ScriptedMovePicker(*other)).handle_turn(T, "conv-2", "hi")
    assert [s.refused for s in (await trace_of(c, "conv-2")).agent_steps] == [False, True, False, False]


async def test_a_reply_cannot_carry_a_number_the_loop_never_verified(c: Container) -> None:
    lie = Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="action_done", facts={"reference": "RET-1"}))
    picker = ScriptedMovePicker(lie, lie, GREETING)
    reply = await orch(c, picker).handle_turn(T, C, "hi")
    trace = await trace_of(c)
    assert "RET-1" not in reply.text and trace.fallback_reason == "two refused moves in a row"


# ---- limits and fallbacks: today's middle stages run instead ----


async def test_two_refused_moves_in_a_row_fall_back_to_the_usual_stages(c: Container) -> None:
    bad = ProposeAction(intent="made_up", arguments={})
    reply = await orch(c, ScriptedMovePicker(bad, bad, GREETING)).handle_turn(T, C, "Hello")
    trace = await trace_of(c)
    assert reply.text == "Hello! How can I help you today?"  # the usual flow answered the greeting
    assert trace.fallback_reason == "two refused moves in a row" and stages(trace)["agent"] == "ok"
    names = list(stages(trace))
    assert names[names.index("agent") :] == [
        "agent", "disambiguate", "merge", "frustration", "plan", "handler", "queue", "handoff", "finish",
    ]  # fmt: skip  # the usual middle, from its start, after the agent step
    assert [r.refused for r in trace.agent_steps] == [True, True]


async def test_a_good_move_between_two_refusals_resets_the_count(c: Container) -> None:
    bad = ProposeAction(intent="made_up", arguments={})
    ok = SearchPolicy(query="q")
    reply = await orch(c, ScriptedMovePicker(bad, ok, bad, GREETING)).handle_turn(T, C, "hi")
    assert reply.decision is Decision.ANSWER and (await trace_of(c)).fallback_reason is None


async def test_the_move_limit(c: Container) -> None:
    picker = ScriptedMovePicker(*[SearchPolicy(query=f"q{i}") for i in range(10)])
    reply = await orch(c, picker, agent_max_moves=3).handle_turn(T, C, "Hello")
    trace = await trace_of(c)
    assert len(picker.views) == 3 and trace.fallback_reason == "more than 3 moves without an answer"
    assert reply.text == "Hello! How can I help you today?"  # the usual flow


async def test_the_default_limit_is_five_moves(c: Container) -> None:
    picker = ScriptedMovePicker(*[SearchPolicy(query=f"q{i}") for i in range(10)])
    await orch(c, picker).handle_turn(T, C, "Hello")
    assert len(picker.views) == 5


async def test_a_picker_error_falls_back(c: Container) -> None:
    reply = await orch(c, ScriptedMovePicker(RuntimeError("model down"))).handle_turn(T, C, "Hello")
    trace = await trace_of(c)
    assert reply.decision is Decision.ANSWER and trace.fallback_reason == "move picker error: RuntimeError"
    assert trace.agent_steps[0].result == "RuntimeError"


async def test_the_turn_time_limit_falls_back(c: Container) -> None:
    class Slow:
        async def next_move(self, view: Any) -> Any:
            await asyncio.sleep(5)

    reply = await orch(c, Slow(), agent_turn_timeout_s=0.05).handle_turn(T, C, "Hello")
    trace = await trace_of(c)
    assert reply.decision is Decision.ANSWER and trace.fallback_reason == "the turn took longer than 0.05 seconds"


async def test_an_endless_loop_of_the_same_move_ends_in_a_fallback(c: Container) -> None:
    picker = ScriptedMovePicker(SearchPolicy(query="same"), repeat_last=True)
    await orch(c, picker).handle_turn(T, C, "Hello")
    trace = await trace_of(c)
    assert trace.fallback_reason == "two refused moves in a row" and len(picker.views) == 3


async def test_a_fallback_leaves_no_trace_of_the_loop_on_the_session(c: Container) -> None:
    plan = AgentPlan(steps=[PlanStep(text="look up the order")])
    picker = ScriptedMovePicker(plan, RuntimeError("boom"))
    await orch(c, picker).handle_turn(T, C, "Hello")
    session = await c.sessions.load(T, C)
    assert session is not None and session.agent_plan is None and session.agent_pending is None


# ---- the saved plan ----


async def test_a_plan_is_saved_and_the_next_turn_sees_it(c: Container) -> None:
    plan = AgentPlan(steps=[PlanStep(text="check identity"), PlanStep(text="look up the order")])
    first = ScriptedMovePicker(plan, GREETING)
    bot = orch(c, first)
    await bot.handle_turn(T, C, "hi")
    session = await c.sessions.load(T, C)
    assert session is not None and session.agent_plan is not None and session.agent_plan.revision == 1
    assert [s.text for s in session.agent_plan.steps] == ["check identity", "look up the order"]
    trace = await trace_of(c)
    assert trace.agent_plan is not None and trace.agent_plan.revision == 1
    assert trace.agent_steps[0].move == {"kind": "plan_update"}

    second = ScriptedMovePicker(
        plan.model_copy(update={"steps": [PlanStep(text="check identity", status="done")]}), GREETING
    )
    await orch(c, second).handle_turn(T, C, "again")
    assert second.views[0].plan is not None and second.views[0].plan.revision == 1  # the plan of the last turn
    after = await c.sessions.load(T, C)
    assert after is not None and after.agent_plan is not None and after.agent_plan.revision == 2


async def test_an_old_saved_session_without_the_agent_fields_still_loads(c: Container) -> None:
    await orch(c).handle_turn(T, C, "Hello")
    session = await c.sessions.load(T, C)
    assert session is not None
    data = session.model_dump(mode="json", exclude={"agent_plan", "agent_pending"})
    from team_b.domain.session import SessionState

    old = SessionState.model_validate(data)
    assert old.agent_plan is None and old.agent_pending is None


async def test_personal_values_in_a_plan_or_a_move_are_hidden_in_the_trace(c: Container) -> None:
    plan = AgentPlan(steps=[PlanStep(text="verify the phone 01012345601")])
    look = LookUp(tool="verify_customer", arguments={"phone": "01012345601"}, why="check 01012345601")
    await orch(c, ScriptedMovePicker(plan, look, GREETING)).handle_turn(T, C, "hi")
    trace = await trace_of(c)
    dumped = trace.model_dump_json()
    assert "01012345601" not in dumped


# ---- the menu itself ----


def test_a_move_is_read_from_json_and_anything_else_is_refused() -> None:
    assert isinstance(parse_move({"kind": "look_up", "tool": "get_order", "arguments": {"order_id": "NS-1"}}), LookUp)
    assert isinstance(parse_move({"kind": "hand_over", "reason": "no_evidence", "tried": ["a"]}), HandOver)
    for bad in (
        {"kind": "call_tool", "tool": "create_refund"},
        {"kind": "hand_over", "reason": "because_i_said_so"},
        {"kind": "reply", "plan": {"decision": "teleport"}},
        {"kind": "look_up"},
    ):
        with pytest.raises(Exception):  # noqa: B017 - pydantic's ValidationError, whatever the way it is wrong
            parse_move(bad)


async def test_a_move_given_as_a_dict_is_parsed_and_checked_the_same_way(c: Container) -> None:
    picker = ScriptedMovePicker(
        {"kind": "teleport"}, {"kind": "reply", "plan": {"decision": "answer", "reply_key": "greeting"}}
    )
    reply = await orch(c, picker).handle_turn(T, C, "hi")
    trace = await trace_of(c)
    assert reply.decision is Decision.ANSWER and trace.agent_steps[0].refused


async def test_a_plan_with_more_than_five_steps_is_not_accepted() -> None:
    with pytest.raises(Exception):  # noqa: B017
        AgentPlan(steps=[PlanStep(text=f"step {i}") for i in range(50)])
