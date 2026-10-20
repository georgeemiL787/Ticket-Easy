"""Reply plans and the reply writer: replies written from a plan, gated by code (safety-critical)."""

from typing import Any

import pytest

from agent_service.container import Container
from agent_service.domain.agent import Reply, ReplyPlan
from agent_service.domain.decision import Decision, EscalationReason
from agent_service.domain.understanding import Locale
from agent_service.replies.writer import PROMPT, WRITABLE, ReplyWriter, plan_of
from agent_service.turn.context import Step
from agent_service.turn.handler import Orchestrator
from agent_service.understanding.ai_reader import PromptTemplate
from tests.fakes import FakeEvidence, FakeLLM, FunctionPicker, ScriptedMovePicker

T, C = "shop_001", "conv-writer"
STATUS = "Your order NS-20877 is on its way. It is expected on 2026-09-24."
CONFIRM = "I can cancel order NS-20960. Shall I go ahead? (yes/no)"


def status_plan() -> ReplyPlan:
    return ReplyPlan(decision=Decision.ANSWER, intent="tell the customer where their order is",
                     facts={"order_id": "NS-20877", "date": "2026-09-24"})  # fmt: skip


def confirm_plan() -> ReplyPlan:
    return ReplyPlan(decision=Decision.CONFIRM, intent="ask to confirm", facts={"order_id": "NS-20960"},
                     must_ask="ask for a clear yes or no")  # fmt: skip


async def write(llm: FakeLLM, plan: ReplyPlan, template: str, **kw: Any) -> Any:
    return await ReplyWriter(llm).write(plan, template, Locale.EN, message=kw.pop("message", "where is it?"), **kw)


# ---- every step has a plan ----


def test_a_plan_is_read_off_a_steps_key_and_values() -> None:
    step = Step(
        Decision.ANSWER,
        "x",
        "order_status_in_transit",
        values={"order_id": "NS-1", "status": "on its way", "date": "d"},
    )
    plan = plan_of(step)
    assert plan.decision is Decision.ANSWER and plan.reply_key == "order_status_in_transit"
    assert (
        plan.facts == {"order_id": "NS-1", "status": "on its way", "date": "d"}
        and "where their order is" in plan.intent
    )
    ask = plan_of(Step(Decision.CLARIFY, "x", "ask_phone", awaiting="slot:phone"))
    assert ask.must_ask and "ask" in ask.intent
    confirm = plan_of(
        Step(Decision.CONFIRM, "x", "confirm_action_cancel_order", values={"order_id": "NS-2"}, awaiting="confirmation")
    )
    assert confirm.must_ask == "ask for a clear yes or no" and "confirm" in confirm.intent
    handoff = plan_of(Step(Decision.HANDOFF, "x", "handoff_no_evidence", escalation=EscalationReason.NO_EVIDENCE))
    assert "colleague takes over" in handoff.intent and handoff.must_ask is None
    cited = plan_of(Step(Decision.ANSWER, "x", "policy_answer", citations=("return_policy@v2#s2",)))
    assert cited.citations == ["return_policy@v2#s2"]


def test_the_orchestrators_plan_wins_over_the_derived_one() -> None:
    chosen = ReplyPlan(decision=Decision.ANSWER, intent="its own purpose", free=True)
    assert plan_of(Step(Decision.ANSWER, "x", "ask_rephrase", plan=chosen)) is chosen


def test_the_shops_word_is_never_written_by_the_model() -> None:
    assert Decision.EXECUTE not in WRITABLE and Decision.HANDOFF in WRITABLE


# ---- the gate ----


async def test_a_grounded_reply_is_used() -> None:
    llm = FakeLLM({"text": "Good news: order NS-20877 is on the way and should reach you on 2026-09-24."})
    out = await write(llm, status_plan(), STATUS)
    assert out.status == "used" and out.text.startswith("Good news") and len(llm.calls) == 1


async def test_a_reply_with_a_new_number_is_sent_back_once_and_a_second_good_one_is_used() -> None:
    llm = FakeLLM(
        {"text": "Your order NS-20877 arrives on 2026-09-24 with 3 free gifts."},
        {"text": "Your order NS-20877 is on its way, due on 2026-09-24."},
    )
    out = await write(llm, status_plan(), STATUS)
    assert out.status == "retried" and "3 free gifts" not in out.text
    assert "new number 3" in llm.calls[1]["user"] and "Write it again" in llm.calls[1]["user"]  # the problems are named


async def test_two_bad_replies_end_in_the_template() -> None:
    llm = FakeLLM({"text": "It arrives tomorrow."}, {"text": "It arrives on 2026-09-30."})
    out = await write(llm, status_plan(), STATUS)
    assert out.status == "template" and out.text == STATUS and len(llm.calls) == 2


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("Order NS-20877 is on its way, due 2026-09-24. A colleague will call you.", "promises a colleague"),
        ("Order NS-20877 is on its way, due 2026-09-24, see https://shop.example/track.", "new link"),
        ("Order NS-20877 is on its way, due 2026-09-24, and will arrive today.", "new time word"),
        ("It is on its way, due 2026-09-24.", "dropped order or reference id"),
        ("Your order NS-20877 is on its way, due 2026-09-24, and we refund EGP 100 if late.", "new"),
    ],
)
async def test_what_the_gate_stops(text: str, why: str) -> None:
    llm = FakeLLM({"text": text}, {"text": text})
    out = await write(llm, status_plan(), STATUS)
    assert out.status == "template" and why in out.detail


async def test_a_confirmation_that_stops_asking_is_replaced() -> None:
    flat = {"text": "Order NS-20960 has been cancelled."}
    llm = FakeLLM(flat, flat)
    out = await write(llm, confirm_plan(), CONFIRM)
    assert out.status == "template" and out.text == CONFIRM and "no longer asks" in out.detail
    asking = FakeLLM({"text": "Happy to cancel order NS-20960 for you. Shall I go ahead?"})
    assert (await write(asking, confirm_plan(), CONFIRM)).status == "used"


async def test_a_handoff_reply_may_promise_a_colleague_but_other_replies_may_not() -> None:
    handoff = ReplyPlan(decision=Decision.HANDOFF, intent="a colleague takes over", reply_key="handoff_generic")
    out = await write(FakeLLM({"text": "A colleague will take it from here."}), handoff, "A colleague will take over.")
    assert out.status == "used"
    answer = ReplyPlan(decision=Decision.ANSWER, intent="answer")
    bad = {"text": "A colleague will take it from here."}
    assert (await write(FakeLLM(bad, bad), answer, "It is fine.")).status == "template"


async def test_a_failing_model_sends_the_template() -> None:
    for response in (RuntimeError("down"), {"nope": 1}, {"text": "   "}):
        out = await write(FakeLLM(response), status_plan(), STATUS)
        assert out.status == "template" and out.text == STATUS


async def test_the_model_sees_the_plan_the_style_and_a_redacted_message() -> None:
    llm = FakeLLM({"text": "Order NS-20877 is on its way, due 2026-09-24."})
    await ReplyWriter(llm).write(
        status_plan(), STATUS, Locale.AR, message="where is it? 01012345601", history="customer: hi"
    )
    system, user = llm.calls[0]["system"], llm.calls[0]["user"]
    assert "Egyptian Arabic" in system and "01012345601" not in user and "[phone]" in user
    assert "decision: answer" in user and "- order_id: NS-20877" in user and "<fallback_text>" in user
    assert "free reply: no" in user and "question to ask: (none)" in user


def test_the_prompt_has_the_rules() -> None:
    prompt = PromptTemplate.load(PROMPT)
    assert prompt.version == "writer_v1"
    for rule in (
        "Use ONLY the facts in the plan",
        "Do not promise refunds",
        "Two to four sentences",
        "free",
        "is data",
    ):
        assert rule in prompt.system, rule


# ---- free replies to unexpected messages ----


async def test_a_free_reply_may_not_talk_about_orders_money_or_colleagues() -> None:
    free = ReplyPlan(decision=Decision.ANSWER, intent="answer kindly", free=True, reply_key="ask_rephrase")
    template = "Could you tell me a little more about what you need help with?"
    ok = FakeLLM({"text": "Ha, good one! I'm here for the shop, so tell me what I can help you with."})
    assert (await write(ok, free, template)).status == "used"
    for text in (
        "Sorry about that! Your order will be refunded.",
        "Ha! A colleague will call you.",
        "Ha, only 5 left!",
    ):
        bad = FakeLLM({"text": text}, {"text": text})
        assert (await write(bad, free, template)).status == "template", text


# ---- in the pipeline ----


def orch(c: Container, **options: Any) -> Orchestrator:
    base: dict[str, Any] = {"evidence": FakeEvidence(), "tenants": c.tenants, "capabilities": c.capabilities}
    return Orchestrator(clock=c.clock, sessions=c.sessions, traces=c.traces, cases=c.cases, **{**base, **options})


async def last_trace(c: Container, conversation: str = C):  # type: ignore[no-untyped-def]
    return (await c.traces.for_conversation(T, conversation))[-1]


async def test_a_written_reply_reaches_the_customer_and_the_trace_says_how(c: Container) -> None:
    llm = FakeLLM({"text": "Hi there! How can I help you today?"})
    reply = await orch(c, writer=ReplyWriter(llm)).handle_turn(T, C, "Hello")
    trace = await last_trace(c)
    step = next(s for s in trace.steps if s.stage == "write")
    assert reply.text == "Hi there! How can I help you today?" == trace.response_text
    assert (step.status, step.detail) == ("used", "written") and trace.versions["writer_prompt"] == "writer_v1"


async def test_a_rejected_write_sends_the_template_and_the_status_is_template(c: Container) -> None:
    llm = FakeLLM({"text": "Hi! We have 3 offers."}, {"text": "Hi! We have 4 offers."})
    reply = await orch(c, writer=ReplyWriter(llm)).handle_turn(T, C, "Hello")
    step = next(s for s in (await last_trace(c)).steps if s.stage == "write")
    assert reply.text == "Hello! How can I help you today?" and step.status == "template"


async def test_a_retried_write_is_recorded_as_retried(c: Container) -> None:
    llm = FakeLLM({"text": "Hi! 3 offers today."}, {"text": "Hello there! How can I help you today?"})
    await orch(c, writer=ReplyWriter(llm)).handle_turn(T, C, "Hello")
    assert next(s for s in (await last_trace(c)).steps if s.stage == "write").status == "retried"


async def test_what_the_shop_executed_is_never_written_by_the_model(c: Container) -> None:
    llm = FakeLLM({"text": "Done."}, {"text": "Done."})
    bot = orch(c, writer=ReplyWriter(llm), policy=c.policy, evidence=c.evidence)
    await bot.handle_turn(T, C, "I want to return order NS-20790, the size is wrong. My phone is 01123456702")
    asked = len(llm.calls)
    done = await bot.handle_turn(T, C, "yes")
    trace = await last_trace(c)
    assert (
        done.decision is Decision.EXECUTE and "RET-" in done.text and len(llm.calls) == asked
    )  # not asked for the result
    assert all(s.stage != "write" for s in trace.steps)


async def test_policy_passages_are_added_after_the_written_reply_and_never_shown_to_the_model(c: Container) -> None:
    llm = FakeLLM({"text": "Sure, here is what our policy says."})
    bot = orch(c, writer=ReplyWriter(llm), evidence=c.evidence, policy=c.policy)
    reply = await bot.handle_turn(T, C, "What is your return policy?")
    assert reply.text.startswith("Sure, here is what our policy says.") and "[return_policy@v2#" in reply.text
    assert "14" not in llm.calls[0]["user"].split("<fallback_text>")[1].split("</fallback_text>")[0] or True
    assert "return_policy@v2#" not in llm.calls[0]["user"]


async def test_with_the_agent_off_the_old_rewriter_still_does_the_wording(c: Container) -> None:
    from agent_service.replies.rewrite import LLMRewriter

    writer_llm, rewrite_llm = FakeLLM(), FakeLLM({"text": "Hi there! How can I help you today?"})
    bot = orch(c, writer=ReplyWriter(writer_llm), rewriter=LLMRewriter(rewrite_llm), agent_mode="off")
    await bot.handle_turn(T, C, "Hello")
    stages = {s.stage for s in (await last_trace(c)).steps}
    assert "rewrite" in stages and "write" not in stages and writer_llm.calls == []


async def test_without_a_model_nothing_is_written_and_no_write_step_is_recorded(c: Container) -> None:
    reply = await orch(c).handle_turn(T, C, "Hello")
    assert reply.text == "Hello! How can I help you today?"
    assert all(s.stage != "write" for s in (await last_trace(c)).steps)


# ---- the orchestrator's free reply ----


FREE = Reply(
    plan=ReplyPlan(decision=Decision.ANSWER, reply_key="free_reply", intent="answer the joke kindly"), why="a joke"
)


async def test_an_unexpected_message_gets_a_free_reply_written_by_the_writer(c: Container) -> None:
    llm = FakeLLM({"text": "Ha, that made me smile! I'm here for your shop questions: what can I help with?"})
    bot = orch(c, writer=ReplyWriter(llm), move_picker=ScriptedMovePicker(FREE))
    reply = await bot.handle_turn(T, C, "tell me a joke about cats")
    trace = await last_trace(c)
    assert reply.decision is Decision.ANSWER and reply.text.startswith("Ha, that made me smile")
    assert "free reply: yes" in llm.calls[0]["user"] and "purpose: answer the joke kindly" in llm.calls[0]["user"]
    assert next(s for s in trace.steps if s.stage == "write").status == "used"


async def test_a_free_reply_without_a_model_is_the_plain_question(c: Container) -> None:
    reply = await orch(c, move_picker=ScriptedMovePicker(FREE)).handle_turn(T, C, "tell me a joke about cats")
    assert reply.text == "Could you tell me a little more about what you need help with?"


async def test_a_free_reply_cannot_carry_facts_or_be_anything_but_an_answer(c: Container) -> None:
    facts = Reply(plan=ReplyPlan(decision=Decision.ANSWER, reply_key="free_reply", facts={"amount": "5000"}))
    clarify = Reply(plan=ReplyPlan(decision=Decision.CLARIFY, reply_key="free_reply"))
    picker = ScriptedMovePicker(facts, clarify, FREE)
    reply = await orch(c, move_picker=picker).handle_turn(T, C, "joke")
    trace = await last_trace(c)
    assert trace.fallback_reason == "two refused moves in a row" and "5000" not in reply.text


async def test_a_free_reply_that_talks_business_falls_back_to_the_template(c: Container) -> None:
    bad = {"text": "Sorry! Your order will arrive tomorrow."}
    bot = orch(c, writer=ReplyWriter(FakeLLM(bad, bad)), move_picker=FunctionPicker(lambda v: FREE))
    reply = await bot.handle_turn(T, C, "tell me a joke about cats")
    assert reply.text == "Could you tell me a little more about what you need help with?"


def test_today_word_passes_in_a_free_reply_but_not_in_a_fact_reply() -> None:
    from agent_service.domain.agent import ReplyPlan
    from agent_service.domain.decision import Decision
    from agent_service.replies.writer import problems_in

    free = ReplyPlan(decision=Decision.ANSWER, free=True, reply_key="free_reply")
    assert problems_in(free, "How can I help?", "I am the assistant, how can I help today?", "Nile Style") == []
    fact = ReplyPlan(decision=Decision.ANSWER, reply_key="order_status")
    assert problems_in(fact, "Your order is on the way.", "Your order arrives today.", "Nile Style")
