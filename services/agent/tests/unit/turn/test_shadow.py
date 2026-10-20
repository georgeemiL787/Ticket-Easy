"""Shadow mode (safe to leave on in a demo): the agent watches, the usual flow answers, nothing else changes."""

import asyncio
import random
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from agent_service.config import Settings
from agent_service.container import Container, build_container
from agent_service.domain.agent import (
    AgentPlan,
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
from agent_service.domain.decision import Decision, EscalationReason
from agent_service.evaluation.conversations import EvalConversation, EvalTurn, Gold, evaluate, render
from agent_service.turn.handler import Orchestrator
from agent_service.turn.shadow import compare
from tests.fakes import FunctionPicker, ScriptedMovePicker
from tests.integration.agent_expert import expert
from tests.integration.test_a_safety_property import script
from tests.integration.test_agent_safety_property import ChaosPicker
from tests.support import make_settings

T, C = "shop_001", "conv-shadow"
GREETING = Reply(why="a greeting", plan=ReplyPlan(decision=Decision.ANSWER, reply_key="greeting"))
TACTICS = ["Hello", "Where is my order NS-20877?", "01012345601", "I want a refund for order NS-20512", "yes"]


def build(tmp: Path, mode: str, picker: Any = None) -> Container:
    container = build_container(make_settings(tmp, fixed_today=date(2026, 9, 28)))
    assert container.orchestrator is not None
    container.orchestrator.set_agent(mode=mode, move_picker=picker)  # type: ignore[arg-type]
    return container


async def run(container: Container, texts: list[str], conversation: str = C) -> list[Any]:
    assert container.orchestrator is not None
    return [await container.orchestrator.handle_turn(T, conversation, text) for text in texts]


def audit(container: Container) -> list[tuple[str, str, bool]]:
    assert container.shop is not None
    return [(e.tool, e.status, e.applied) for e in container.shop.audit_log(T)]


async def session_of(container: Container, conversation: str = C) -> dict[str, Any]:
    session = await container.sessions.load(T, conversation)
    assert session is not None
    return session.model_dump(mode="json", exclude={"history"})


# ---- shadow changes nothing the customer or the shop can see ----


@pytest.mark.parametrize("picker_kind", ["honest", "chaotic", "scripted_write"])
async def test_shadow_never_changes_the_reply_the_session_or_the_shop(tmp_path: Path, picker_kind: str) -> None:
    def picker() -> Any:
        if picker_kind == "honest":
            return FunctionPicker(expert)
        if picker_kind == "chaotic":
            return ChaosPicker("shadow-1")
        move = ProposeAction(intent="refund_request", arguments={"order_id": "NS-20512", "amount": 5000})
        return ScriptedMovePicker(move, repeat_last=True)

    off, shadow = build(tmp_path / "a", "off"), build(tmp_path / "b", "shadow", picker())
    for container in (off, shadow):
        (container.settings.db_path.parent).mkdir(parents=True, exist_ok=True)
    first = await run(off, TACTICS)
    second = await run(shadow, TACTICS)
    assert [(r.text, r.decision, r.awaiting, r.citations) for r in first] == [
        (r.text, r.decision, r.awaiting, r.citations) for r in second
    ]
    assert audit(off) == audit(shadow)  # the very same calls reached the shop, in the same order
    a, b = await session_of(off), await session_of(shadow)
    assert a == b and b["agent_plan"] is None and b["agent_pending"] is None  # not one field of the session moved
    assert shadow.shop is not None and shadow.shop.change_count(T) == off.shop.change_count(T)  # type: ignore[union-attr]


async def test_shadow_survives_many_random_conversations_without_changing_them(tmp_path: Path) -> None:
    for number in range(25):
        texts = script(random.Random(f"shadow-{number}"))
        off, shadow = (
            build(tmp_path / f"a{number}", "off"),
            build(tmp_path / f"b{number}", "shadow", ChaosPicker(f"s-{number}")),
        )
        first, second = await run(off, texts, f"c{number}"), await run(shadow, texts, f"c{number}")
        assert [(r.text, r.decision) for r in first] == [(r.text, r.decision) for r in second], number
        assert audit(off) == audit(shadow), number


async def test_shadow_sends_no_extra_message_to_the_customer(tmp_path: Path) -> None:
    shadow = build(tmp_path, "shadow", FunctionPicker(expert))
    replies = await run(shadow, ["Hello"])
    session = await shadow.sessions.load(T, C)
    assert session is not None and [m.role for m in session.history] == ["customer", "agent"] and len(replies) == 1
    assert session.outbox == []


# ---- what is recorded ----


async def test_the_plan_and_first_move_are_on_the_trace_with_the_checks_but_were_not_worked(tmp_path: Path) -> None:
    plan = AgentPlan(steps=[PlanStep(text="answer the greeting")])
    shadow = build(tmp_path, "shadow", ScriptedMovePicker(MoveProposal(plan=plan, move=GREETING)))
    await run(shadow, ["Hello"])
    trace = (await shadow.traces.for_conversation(T, C))[-1]
    assert trace.shadow_plan is not None and trace.shadow_plan.steps[0].text == "answer the greeting"
    record = trace.shadow_move
    assert record is not None and record.move["kind"] == "reply" and record.result == "shadow: proposed, not worked"
    assert [c.name for c in record.checks][0] == "on_the_menu" and all(c.passed for c in record.checks)
    assert trace.shadow_error is None and trace.agent_steps == () and trace.fallback_reason is None


async def test_a_move_code_would_refuse_is_recorded_with_the_failed_check(tmp_path: Path) -> None:
    bad = ProposeAction(intent="made_up", arguments={})
    shadow = build(tmp_path, "shadow", ScriptedMovePicker(bad))
    await run(shadow, ["Hello"])
    record = (await shadow.traces.for_conversation(T, C))[-1].shadow_move
    assert record is not None and any(not c.passed and c.name == "intent_is_an_action" for c in record.checks)


async def test_personal_values_are_hidden_in_the_recorded_move(tmp_path: Path) -> None:
    move = LookUp(tool="verify_customer", arguments={"order_id": "NS-20877", "phone": "01012345601"}, why="01012345601")
    shadow = build(tmp_path, "shadow", ScriptedMovePicker(move))
    await run(shadow, ["Where is my order NS-20877? My phone is 01012345601"])
    trace = (await shadow.traces.for_conversation(T, C))[-1]
    assert "01012345601" not in trace.model_dump_json()


async def test_the_agent_is_asked_once_per_turn_and_only_when_the_front_stages_left_the_decision_open(
    tmp_path: Path,
) -> None:
    picker = ScriptedMovePicker(GREETING, repeat_last=True)
    shadow = build(tmp_path, "shadow", picker)
    await run(shadow, ["Hello", "thanks", "I want to talk to a human"])
    traces = await shadow.traces.for_conversation(T, C)
    assert len(picker.views) == 2  # the third message was decided by the front stages (a person was asked for)
    assert traces[-1].decision is Decision.HANDOFF and traces[-1].shadow_move is None
    assert {s.stage: s.status for s in traces[-1].steps}["shadow"] == "skipped"


async def test_the_plan_is_remembered_between_turns_on_the_traces_not_on_the_session(tmp_path: Path) -> None:
    plan = AgentPlan(steps=[PlanStep(text="verify and look up NS-20877")])
    picker = ScriptedMovePicker(
        MoveProposal(plan=plan, move=AskCustomer(question_key="ask_phone", slot="phone")), GREETING
    )
    shadow = build(tmp_path, "shadow", picker)
    await run(shadow, ["where is my order NS-20877", "01012345601"])
    assert picker.views[0].plan is None
    assert picker.views[1].plan is not None and picker.views[1].plan.steps[0].text == "verify and look up NS-20877"
    assert picker.views[1].pending == "slot:phone"  # what the usual flow was waiting for
    assert (await session_of(shadow))["agent_plan"] is None


# ---- failures stay invisible ----


@pytest.mark.parametrize("error", [RuntimeError("model down"), ValueError("bad json")])
async def test_a_failing_agent_is_noted_and_the_customer_never_notices(tmp_path: Path, error: Exception) -> None:
    shadow = build(tmp_path, "shadow", ScriptedMovePicker(error))
    reply = (await run(shadow, ["Hello"]))[0]
    trace = (await shadow.traces.for_conversation(T, C))[-1]
    assert reply.text == "Hello! How can I help you today?" and trace.shadow_error == type(error).__name__
    assert trace.shadow_move is None


async def test_a_slow_agent_is_cut_off(tmp_path: Path) -> None:
    class Slow:
        async def next_move(self, view: Any) -> Any:
            await asyncio.sleep(5)

    container = build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))
    bot = Orchestrator(
        clock=container.clock, tenants=container.tenants, sessions=container.sessions, traces=container.traces,
        cases=container.cases, evidence=container.evidence, capabilities=container.capabilities,
        policy=container.policy,
        move_picker=Slow(), agent_mode="shadow", agent_turn_timeout_s=0.05,
    )  # fmt: skip
    reply = await bot.handle_turn(T, C, "Hello")
    trace = (await container.traces.for_conversation(T, C))[-1]
    assert reply.decision is Decision.ANSWER and trace.shadow_error == "TimeoutError"


async def test_shadow_without_a_model_does_nothing_and_says_so(tmp_path: Path) -> None:
    container = build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))
    assert container.orchestrator is not None
    container.orchestrator.set_agent(mode="shadow")
    await run(container, ["Hello"])
    trace = (await container.traces.for_conversation(T, C))[-1]
    assert trace.versions["agent"] == "unavailable: no model" and trace.shadow_move is None


# ---- does the agent's first move agree with what was done? ----


async def test_the_honest_agent_agrees_on_the_typical_turns(tmp_path: Path) -> None:
    shadow = build(tmp_path, "shadow", FunctionPicker(expert))
    await run(
        shadow, ["Hello", "What is your return policy?", "Where is my order NS-20877?", "I want to talk to a human"]
    )
    tools = {n: s.action_tool for n, s in shadow.tenants.get(T).intents.items() if s.action_tool}
    traces = await shadow.traces.for_conversation(T, C)
    verdicts = [compare(t, tools) for t in traces]
    assert [v.agrees for v in verdicts[:3] if v] == [True, True, True]
    assert verdicts[3] is None  # a person was asked for: the front stages decided, the agent was not asked


async def test_a_different_first_move_is_a_disagreement_with_both_sides_named(tmp_path: Path) -> None:
    picker = ScriptedMovePicker(HandOver(reason=EscalationReason.NO_EVIDENCE, tried=["x"]), SearchPolicy(query="q"))
    shadow = build(tmp_path, "shadow", picker)
    await run(shadow, ["Hello", "Hello"])
    traces = await shadow.traces.for_conversation(T, C)
    first, second = compare(traces[0], {}), compare(traces[1], {})
    assert first is not None and not first.agrees and first.today == "reply" and first.agent == "hand_over"
    assert (
        second is not None
        and not second.agrees
        and second.agent == "policy"
        and second.why == "the kind of move differs"
    )


async def test_the_same_move_that_code_would_refuse_does_not_count_as_agreement(tmp_path: Path) -> None:
    bad = Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="action_done"))
    shadow = build(tmp_path, "shadow", ScriptedMovePicker(bad))
    await run(shadow, ["Hello"])
    verdict = compare((await shadow.traces.for_conversation(T, C))[-1], {})
    assert verdict is not None and not verdict.agrees and "refused" in verdict.why


# ---- the evaluation counts the agreement ----


def conv(cid: str, *says: str) -> EvalConversation:
    return EvalConversation.model_validate(
        {"id": cid, "language_style": "en", "turns": [EvalTurn(say=s, gold=Gold()).model_dump() for s in says]}
    )


async def test_the_evaluation_counts_agreement_and_lists_the_disagreements() -> None:
    picker = ScriptedMovePicker(GREETING, HandOver(reason=EscalationReason.NO_EVIDENCE, tried=["t"]), GREETING)
    report = await evaluate(
        [conv("a", "Hello", "Hello"), conv("b", "Hello")],
        Settings(llm="none", llm_rewrite=False),
        agent_mode="shadow",
        move_picker=picker,
        when=date(2026, 10, 1),
    )
    assert (report.overall.shadow_asked, report.overall.shadow_agreed) == (3, 2)
    assert report.overall.measures()["shadow_agreement"] == pytest.approx(2 / 3)
    [d] = report.shadow_disagreements
    assert (d["conversation"], d["turn"], d["said"], d["today"], d["agent"]) == ("a", 2, "Hello", "reply", "hand_over")
    text = render(report)
    assert "Shadow mode" in text and "agreed on 2 (66.7%)" in text and "| a | 2 | Hello | reply | hand_over |" in text
    assert report.as_dict()["agent_mode"] == "shadow" and len(report.as_dict()["shadow_disagreements"]) == 1


async def test_without_shadow_the_report_has_no_agreement_section() -> None:
    report = await evaluate([conv("a", "Hello")], Settings(llm="none", llm_rewrite=False), when=date(2026, 10, 1))
    assert report.overall.shadow_asked == 0 and "Shadow mode" not in render(report)


def test_shadow_and_agent_modes_of_the_command_need_the_model(tmp_path: Path) -> None:
    from agent_service import __main__ as cli

    folder = tmp_path / "set"
    folder.mkdir()
    (folder / "en.jsonl").write_text(
        '{"id": "en-1", "language_style": "en", "turns": [{"say": "Hello", "gold": {}}]}\n', encoding="utf-8"
    )
    for mode in ("shadow", "on"):
        with pytest.raises(SystemExit, match="needs the orchestrator"):
            cli.main(["eval", "--set", str(folder), "--out", str(tmp_path / "o"), "--agent-mode", mode])
    assert cli.main(["eval", "--set", str(folder), "--out", str(tmp_path / "o"), "--agent-mode", "off"]) == 0
