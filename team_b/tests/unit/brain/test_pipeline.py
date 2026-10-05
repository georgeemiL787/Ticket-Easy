"""The turn pipeline, stage by stage, with fakes for everything outside the brain."""

from dataclasses import replace
from typing import Any

import pytest

from team_b.brain.handoff import ESCALATION_DEFAULTS
from team_b.brain.orchestrator import Orchestrator
from team_b.brain.pipeline import STAGES
from team_b.brain.templates import TEMPLATES, render
from team_b.brain.turn import MAX_QUEUED_RUNS, PlannedIntent, Step, TurnContext
from team_b.container import Container
from team_b.contracts.errors import UpstreamError
from team_b.contracts.evidence import RiskAssessment
from team_b.domain.actions import ActionProposal, ActionState
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.handoff import CaseStatus
from team_b.domain.reply import AgentReply
from team_b.domain.tenant import TenantRegistry
from team_b.domain.understanding import IntentCandidate, Language, Locale, NLUResult
from tests.fakes import FakeEvidence, ScriptedNLU

T, C = "shop_001", "conv-1"
STAGE_NAMES = [
    "load", "handed_off_check", "understand", "risk_screen", "human_request", "pending_confirmation", "merge",
    "frustration", "plan", "handler", "queue", "handoff", "finish",
]  # fmt: skip


def orch(c: Container, **kw: Any) -> Orchestrator:
    """An orchestrator on the container's stores; the safety screen defaults to one that finds nothing."""
    options: dict[str, Any] = {"evidence": FakeEvidence(), "tenants": c.tenants, **kw}
    return Orchestrator(clock=c.clock, sessions=c.sessions, traces=c.traces, cases=c.cases, **options)


async def say(o: Orchestrator, text: str, conversation: str = C) -> AgentReply:
    return await o.handle_turn(T, conversation, text)


async def last_trace(c: Container, conversation: str = C):  # type: ignore[no-untyped-def]
    return (await c.traces.for_conversation(T, conversation))[-1]


def reading(*intents: str, **fields: Any) -> NLUResult:
    base: dict[str, Any] = {"language": Language.EN, "language_confidence": 0.9}
    base["intents"] = tuple(IntentCandidate(name=n, confidence=0.8) for n in intents)
    return NLUResult.model_validate({**base, **fields})


def stage_status(trace: Any) -> dict[str, str]:
    return {s.stage: s.status for s in trace.steps}


# ---- the pipeline as a whole ----


async def test_every_turn_records_every_stage_in_order_with_durations(c: Container) -> None:
    await say(orch(c), "Hello")
    trace = await last_trace(c)
    assert [s.stage for s in trace.steps] == STAGE_NAMES
    assert [s.name for s in STAGES] == STAGE_NAMES[1:-1]
    assert all(s.duration_ms >= 0 for s in trace.steps)
    assert all(s.detail for s in trace.steps if s.status == "ok")


async def test_stages_after_a_decision_are_recorded_as_skipped(c: Container) -> None:
    await say(orch(c), "I want to talk to a human agent please")
    status = stage_status(await last_trace(c))
    assert status["human_request"] == "ok" and status["handoff"] == "ok" and status["finish"] == "ok"
    for skipped in ("pending_confirmation", "merge", "frustration", "plan", "handler"):
        assert status[skipped] == "skipped", skipped


async def test_handoff_runs_only_for_a_handoff_decision(c: Container) -> None:
    await say(orch(c), "Hello")
    assert stage_status(await last_trace(c))["handoff"] == "skipped"
    assert await c.cases.list(T) == []


async def test_a_trace_is_valid_and_matches_the_reply(c: Container) -> None:
    reply = await say(orch(c), "Hello")
    trace = await last_trace(c)
    assert (trace.trace_id, trace.decision, trace.response_text) == (reply.trace_id, reply.decision, reply.text)
    assert trace.customer_message == "Hello" and trace.escalation_reason is None and trace.handoff_case_id is None


# ---- understand ----


async def test_understand_records_language_intents_and_method(c: Container) -> None:
    await say(orch(c), "3ayez a3raf feen el order NS-20877")
    trace = await last_trace(c)
    assert trace.language is Language.ARABIZI and trace.nlu_method == "rules"
    assert [i.name for i in trace.intents] == ["order_status"] and trace.entities == {"order_id": "NS-20877"}
    session = await c.sessions.load(T, C)
    assert session is not None and session.language is Language.ARABIZI


async def test_understand_failing_does_not_stop_the_turn(c: Container) -> None:
    reply = await say(orch(c, nlu=ScriptedNLU(RuntimeError("boom"))), "anything")
    trace = await last_trace(c)
    assert reply.decision is Decision.CLARIFY and "understanding failed" in trace.errors
    assert trace.language is None and trace.intents == ()


async def test_a_message_with_no_language_keeps_the_session_language(c: Container) -> None:
    o = orch(c)
    await say(o, "3ayez a3raf feen el order")
    nlu = ScriptedNLU(reading(language=Language.EN, language_confidence=0.0))
    await say(orch(c, nlu=nlu), "NS-20877")
    session = await c.sessions.load(T, C)
    assert session is not None and session.language is Language.ARABIZI


# ---- risk_screen ----


async def test_a_flagged_message_is_handed_off_urgently(c: Container) -> None:
    evidence = FakeEvidence(RiskAssessment(flagged=True, categories=("fraud_suspected",), matched_terms=("fraud",)))
    reply = await say(orch(c, evidence=evidence), "Someone used my card, it is fraud")
    trace = await last_trace(c)
    assert reply.decision is Decision.HANDOFF and trace.escalation_reason is EscalationReason.MANDATORY_RISK
    assert trace.risk_categories == ("fraud_suspected",) and reply.awaiting == "human"
    (case,) = await c.cases.list(T)
    assert case.package.priority == "urgent" and case.package.reason is EscalationReason.MANDATORY_RISK
    assert case.package.safety_flags == ("fraud_suspected",)
    assert evidence.messages == ["Someone used my card, it is fraud"]


async def test_a_message_that_is_clear_continues(c: Container) -> None:
    evidence = FakeEvidence(RiskAssessment(flagged=False, categories=()))
    reply = await say(orch(c, evidence=evidence), "Hello")
    assert reply.decision is Decision.ANSWER and (await last_trace(c)).errors == ()


@pytest.mark.parametrize(
    "error",
    [NotImplementedError("placeholder"), UpstreamError("safety", "BACKEND_UNAVAILABLE", "down", retryable=True)],
)
async def test_an_unavailable_screen_is_noted_and_small_talk_still_works(c: Container, error: Exception) -> None:
    reply = await say(orch(c, evidence=FakeEvidence(error=error)), "Hello")
    trace = await last_trace(c)
    assert reply.decision is Decision.ANSWER
    assert any("safety screen unavailable" in e for e in trace.errors)
    assert "screen unavailable" in next(s.detail for s in trace.steps if s.stage == "risk_screen")


async def test_no_screen_configured_counts_as_unavailable(c: Container) -> None:
    reply = await say(orch(c, evidence=None), "Hello")
    assert reply.decision is Decision.ANSWER and "no safety screen is configured" in (await last_trace(c)).errors


async def test_a_risk_flag_raised_by_understanding_also_hands_off(c: Container) -> None:
    nlu = ScriptedNLU(reading(safety_flags=("legal_regulatory",)))
    reply = await say(orch(c, nlu=nlu), "my lawyer will call you")
    assert reply.decision is Decision.HANDOFF
    assert (await last_trace(c)).escalation_reason is EscalationReason.MANDATORY_RISK


# ---- human_request ----


@pytest.mark.parametrize(
    ("text", "locale", "expected"),
    [
        ("I want to talk to a human agent please", Locale.EN, render("handoff_customer_request", Locale.EN)),
        ("عايز اكلم حد من خدمة العملاء", Locale.AR, render("handoff_customer_request", Locale.AR)),
        ("3ayez akalem mowazaf", Locale.ARABIZI, render("handoff_customer_request", Locale.ARABIZI)),
    ],
)
async def test_a_request_for_a_person_is_a_handoff_in_the_customers_style(
    c: Container, text: str, locale: Locale, expected: str
) -> None:
    reply = await say(orch(c), text)
    assert (reply.decision, reply.locale, reply.text, reply.awaiting) == (Decision.HANDOFF, locale, expected, "human")
    trace = await last_trace(c)
    assert (
        trace.escalation_reason is EscalationReason.CUSTOMER_REQUEST and trace.handoff_case_id == reply.handoff_case_id
    )


# ---- handoff (the stub that opens a case) ----


async def test_handoff_opens_a_case_with_the_transcript_and_marks_the_session(c: Container) -> None:
    o = orch(c)
    await say(o, "Where is my order NS-20877? my phone is 01012345601")
    reply = await say(o, "I want to talk to a human")
    session = await c.sessions.load(T, C)
    case = await c.cases.get(T, reply.handoff_case_id or "")
    assert case is not None and session is not None
    assert (case.status, case.package.priority, case.conversation_id) == (CaseStatus.OPEN, "normal", C)
    assert [m.role for m in case.package.transcript] == ["customer", "agent", "customer"]
    assert case.package.details["phone"] == "[phone]" and case.package.details["order_id"] == "NS-20877"
    assert case.package.customer.orders == ("NS-20877",)
    assert (session.status, session.handoff_case_id, session.awaiting) == ("handed_off", case.case_id, "human")
    assert session.last_escalation is EscalationReason.CUSTOMER_REQUEST


def test_every_escalation_reason_has_a_priority_and_a_next_step() -> None:
    assert set(ESCALATION_DEFAULTS) == set(EscalationReason)
    assert all(step for _, step in ESCALATION_DEFAULTS.values())


# ---- handed_off_check ----


async def test_a_message_to_a_conversation_a_human_owns_is_logged_and_answered_once(c: Container) -> None:
    o = orch(c)
    first = await say(o, "I want to talk to a human")
    second = await say(o, "my phone is 01012345601, hello?")
    third = await say(o, "are you there")
    assert second.text == render("handed_off_wait", Locale.EN) and second.decision is Decision.HANDOFF
    assert third.text == "" and third.decision is Decision.HANDOFF  # told once, then silence
    assert second.awaiting == third.awaiting == "human"
    cases = await c.cases.list(T)
    assert len(cases) == 1  # no second case
    notes = [e.note for e in cases[0].events if e.kind == "customer_message"]
    assert notes == ["my phone is [phone], hello?", "are you there"]
    trace = await last_trace(c)
    assert (
        trace.escalation_reason is EscalationReason.CUSTOMER_REQUEST and trace.handoff_case_id == first.handoff_case_id
    )
    assert stage_status(trace)["understand"] == "skipped"


@pytest.mark.parametrize("closing", [CaseStatus.RESOLVED, CaseStatus.RETURNED_TO_AGENT])
async def test_when_the_case_is_closed_the_agent_resumes(c: Container, closing: CaseStatus) -> None:
    o = orch(c)
    first = await say(o, "I want to talk to a human")
    case = await c.cases.get(T, first.handoff_case_id or "")
    assert case is not None
    if closing is CaseStatus.RETURNED_TO_AGENT:
        case.transition(CaseStatus.CLAIMED, actor="agent-1", at=c.clock.now())
    case.transition(closing, actor="agent-1", at=c.clock.now())
    await c.cases.save(case)
    reply = await say(o, "Hello again")
    session = await c.sessions.load(T, C)
    assert reply.decision is Decision.ANSWER and reply.text == render("greeting", Locale.EN)
    assert session is not None and (session.status, session.handoff_case_id) == ("active", None)


# ---- pending_confirmation ----


async def with_pending_action(c: Container) -> ActionProposal:
    session = await c.sessions.load(T, C)
    assert session is not None
    proposal = ActionProposal(proposal_id="p1", tool="create_return", capability="create_return", idempotency_key="k1")
    proposal.transition(ActionState.AWAITING_CONFIRMATION, at=c.clock.now())
    session.actions.append(proposal)
    session.pending_action_id, session.awaiting = "p1", "confirmation"
    await c.sessions.save(session)
    return proposal


async def pending(c: Container) -> tuple[Orchestrator, ActionProposal]:
    o = orch(c)
    await say(o, "Hello")
    return o, await with_pending_action(c)


async def action_state(c: Container) -> ActionState:
    session = await c.sessions.load(T, C)
    assert session is not None
    return session.actions[0].state


async def test_no_cancels_the_pending_action(c: Container) -> None:
    o, _ = await pending(c)
    reply = await say(o, "no")
    session = await c.sessions.load(T, C)
    assert reply.decision is Decision.ANSWER and reply.text == render("action_cancelled", Locale.EN)
    assert await action_state(c) is ActionState.CANCELLED
    assert session is not None and (session.pending_action_id, session.awaiting) == (None, None)


async def test_yes_never_executes_from_here(c: Container) -> None:
    o, _ = await pending(c)
    reply = await say(o, "yes")
    assert reply.decision is Decision.CLARIFY and await action_state(c) is ActionState.AWAITING_CONFIRMATION
    assert "not built yet" in (await last_trace(c)).decision_reason


async def test_a_topic_change_cancels_the_action_and_the_new_message_goes_on(c: Container) -> None:
    o, _ = await pending(c)
    reply = await say(o, "actually, how much does shipping cost?")
    assert await action_state(c) is ActionState.CANCELLED
    assert reply.decision is Decision.CLARIFY  # the placeholder for the knowledge handler
    assert "topic change" in next(s.detail for s in (await last_trace(c)).steps if s.stage == "pending_confirmation")


async def test_a_reply_that_is_neither_yes_nor_no_asks_again(c: Container) -> None:
    o, _ = await pending(c)
    reply = await say(o, "hmm let me think about it")
    assert reply.text == render("confirm_again", Locale.EN) and reply.awaiting == "confirmation"
    assert await action_state(c) is ActionState.AWAITING_CONFIRMATION


async def test_nothing_pending_means_the_stage_does_nothing(c: Container) -> None:
    await say(orch(c), "no")
    assert "nothing pending" in next(s.detail for s in (await last_trace(c)).steps if s.stage == "pending_confirmation")


# ---- merge ----


async def test_details_go_into_slots_and_the_first_intent_becomes_active(c: Container) -> None:
    await say(orch(c), "I want a refund for order NS-20512, my phone is 01012345601, 300 EGP")
    session = await c.sessions.load(T, C)
    assert session is not None
    assert session.slots == {"order_id": "NS-20512", "phone": "01012345601", "amount": "300"}
    assert (session.active_intent, session.intents_seen, session.intent_queue) == (
        "refund_request",
        ["refund_request"],
        [],
    )


async def test_further_intents_wait_in_the_queue(c: Container) -> None:
    await say(orch(c), "Where is my order NS-20960 and cancel it and I want a refund")
    session = await c.sessions.load(T, C)
    assert session is not None
    assert session.active_intent == "order_status" and session.intent_queue == ["cancel_order", "refund_request"]
    assert session.intents_seen == ["order_status", "cancel_order", "refund_request"]


async def test_a_short_answer_to_a_question_is_not_a_new_intent(c: Container) -> None:
    o = orch(c)
    await say(o, "I want a refund")
    session = await c.sessions.load(T, C)
    assert session is not None
    session.awaiting = "slot:order_id"
    await c.sessions.save(session)
    await say(o, "NS-20512")
    session = await c.sessions.load(T, C)
    assert session is not None and session.slots["order_id"] == "NS-20512"
    assert session.active_intent == "refund_request" and session.intent_queue == []
    assert "details only" in next(s.detail for s in (await last_trace(c)).steps if s.stage == "merge")


async def test_a_greeting_opens_no_intent(c: Container) -> None:
    await say(orch(c), "Hello")
    session = await c.sessions.load(T, C)
    assert session is not None and session.active_intent is None and session.intents_seen == []


# ---- frustration ----


async def test_a_very_frustrated_customer_is_handed_off(c: Container) -> None:
    text = "This is ridiculous!!! Worst service ever, I am so angry, stop asking me questions!!!"
    reply = await say(orch(c), text)
    assert reply.decision is Decision.HANDOFF
    assert (await last_trace(c)).escalation_reason is EscalationReason.HIGH_FRUSTRATION


async def test_a_medium_level_does_not_hand_off(c: Container) -> None:
    reply = await say(orch(c), "I already asked, this is useless, why is it so slow?")
    assert reply.decision is Decision.CLARIFY and (await last_trace(c)).frustration == "medium"


async def test_the_tenant_can_switch_frustration_handoff_off(c: Container) -> None:
    tenant = c.tenants.get(T)
    relaxed = tenant.model_copy(
        update={"escalation": tenant.escalation.model_copy(update={"escalate_on_high_frustration": False})}
    )
    o = orch(c, tenants=TenantRegistry({T: relaxed}))
    reply = await say(o, "This is ridiculous!!! Worst service ever, I am so angry, stop asking me questions!!!")
    assert reply.decision is Decision.CLARIFY


# ---- plan and handler ----


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("Hello", "greeting"),
        ("السلام عليكم", "greeting"),
        ("thanks, bye", "thanks"),
        ("shokran", "thanks"),
        ("شكرا ليك", "thanks"),
    ],
)
async def test_small_talk_is_answered_from_templates(c: Container, text: str, key: str) -> None:
    reply = await say(orch(c), text)
    language = (await c.sessions.load(T, C)).language  # type: ignore[union-attr]
    assert language is not None
    expected = render(key, {Language.EN: Locale.EN, Language.AR: Locale.AR, Language.ARABIZI: Locale.ARABIZI}[language])
    assert (reply.decision, reply.text, reply.awaiting) == (Decision.ANSWER, expected, None)


async def test_a_knowledge_request_gets_the_placeholder_for_now(c: Container) -> None:
    reply = await say(orch(c), "What is your return policy?")
    assert (reply.decision, reply.awaiting) == (Decision.CLARIFY, "detail")
    assert "not built yet" in (await last_trace(c)).decision_reason


async def test_no_intent_asks_for_detail(c: Container) -> None:
    reply = await say(orch(c), "asdf qwer zxcv")
    assert (reply.decision, reply.awaiting) == (Decision.CLARIFY, "detail")
    assert (await last_trace(c)).decision_reason == "no intent understood"


async def test_a_handler_for_a_kind_can_be_plugged_in(c: Container) -> None:
    async def knowledge(ctx: TurnContext, planned: PlannedIntent) -> Step:
        return Step(Decision.ANSWER, reason="found it", reply_key="greeting", citations=("return_policy@v2#s2",))

    reply = await say(orch(c, handlers={"knowledge": knowledge}), "What is your return policy?")
    assert reply.decision is Decision.ANSWER and reply.citations == ("return_policy@v2#s2",)
    assert (await last_trace(c)).response_citations == ("return_policy@v2#s2",)


async def test_the_reply_is_in_the_locale_of_the_session(c: Container) -> None:
    arabizi = await say(orch(c), "ahlan, ezayak", "arabizi-chat")
    assert (arabizi.locale, arabizi.text) == (Locale.ARABIZI, render("greeting", Locale.ARABIZI))
    default = await say(orch(c), "01012345601", "fresh-chat")  # no language content: the tenant default
    assert default.locale is Locale.AR and default.decision is Decision.CLARIFY


# ---- queue ----


async def answering(ctx: TurnContext, planned: PlannedIntent) -> Step:
    return Step(Decision.ANSWER, reason=f"done {planned.name}", reply_key="thanks", citations=(f"doc#{planned.name}",))


async def test_queued_intents_run_in_order_after_a_completed_one(c: Container) -> None:
    handlers = {"lookup": answering, "action": answering}
    reply = await say(orch(c, handlers=handlers), "Where is my order NS-20960 and cancel it and I want a refund")
    session = await c.sessions.load(T, C)
    assert session is not None and session.intent_queue == [] and session.active_intent == "refund_request"
    assert reply.text == "\n\n".join([render("thanks", Locale.EN)] * 3)
    assert reply.citations == ("doc#order_status", "doc#cancel_order", "doc#refund_request")


async def test_a_queued_intent_waits_when_the_first_one_needs_the_customer(c: Container) -> None:
    reply = await say(orch(c), "Where is my order NS-20960 and cancel it")  # the placeholder is a clarify
    session = await c.sessions.load(T, C)
    assert reply.decision is Decision.VERIFY_IDENTITY and reply.awaiting == "slot:phone"
    assert session is not None and session.intent_queue == ["cancel_order"]
    assert next(s.detail for s in (await last_trace(c)).steps if s.stage == "queue") == "nothing queued"


async def test_at_most_three_queued_intents_run_in_one_turn(c: Container) -> None:
    names = ["order_status", "cancel_order", "refund_request", "return_request", "exchange_request"]
    nlu = ScriptedNLU(reading(*names))
    handlers = {"lookup": answering, "action": answering}
    reply = await say(orch(c, nlu=nlu, handlers=handlers), "five things")
    session = await c.sessions.load(T, C)
    assert session is not None and len(session.intent_queue) == 1  # 1 + 3 ran, one left for next time
    assert reply.text.count(render("thanks", Locale.EN)) == 1 + MAX_QUEUED_RUNS


# ---- finish ----


async def test_history_is_trimmed_through_the_pipeline_too(c: Container) -> None:
    o = orch(c)
    for n in range(20):
        await say(o, f"message {n} about NS-20877" if n == 0 else f"message {n}")
    session = await c.sessions.load(T, C)
    assert session is not None
    assert len(session.history) == c.tenants.get(T).history_max_turns and "NS-20877" in session.history_summary


async def test_a_silent_step_leaves_no_agent_message_in_the_history(c: Container) -> None:
    o = orch(c)
    await say(o, "I want to talk to a human")
    await say(o, "hello?")
    await say(o, "anyone?")  # silent
    session = await c.sessions.load(T, C)
    assert session is not None
    assert [m.role for m in session.history] == ["customer", "agent", "customer", "agent", "customer"]


# ---- templates ----


def test_every_template_exists_in_all_three_locales() -> None:
    for key, texts in TEMPLATES.items():
        assert set(texts) == set(Locale), key
        assert all(text.strip() for text in texts.values()), key


def test_an_unknown_template_is_an_error() -> None:
    with pytest.raises(KeyError, match="no template 'nope'"):
        render("nope", Locale.EN)


def test_a_step_that_hands_off_needs_a_reason() -> None:
    with pytest.raises(ValueError, match="escalation reason"):
        Step(Decision.HANDOFF, reason="x", reply_key="handoff_generic")
    assert replace(Step(Decision.ANSWER, reason="x", reply_key="greeting"), awaiting="detail").awaiting == "detail"
