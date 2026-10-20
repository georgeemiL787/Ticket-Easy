"""Small talk: check-ins are understood by the rules, and an AI model may word the answer (checked by code)."""

from typing import Any

import pytest

from agent_service.container import Container
from agent_service.domain.understanding import Locale
from agent_service.replies.smalltalk import SmallTalkWriter, problems_with
from agent_service.turn.handler import Orchestrator
from tests.fakes import FakeEvidence, FakeLLM

T, C = "shop_001", "conv-st"
TEMPLATE = "Hello! How can I help you today?"
SHOP = "Nile Style"


def orch(c: Container, llm: FakeLLM | None) -> Orchestrator:
    options: dict[str, Any] = {"evidence": FakeEvidence(), "tenants": c.tenants, "capabilities": c.capabilities}
    if llm is not None:
        options["smalltalk"] = SmallTalkWriter(llm)
    return Orchestrator(clock=c.clock, sessions=c.sessions, traces=c.traces, cases=c.cases, **options)


async def last_trace(c: Container):  # type: ignore[no-untyped-def]
    return (await c.traces.for_conversation(T, C))[-1]


# ---- the AI writer is used when the reply is small talk ----


@pytest.mark.parametrize(
    ("message", "reply"),
    [
        ("how are you?", "I'm doing well, thank you for asking! How can I help you today?"),
        ("عامل ايه؟", "الحمد لله تمام، شكرا على سؤالك! أقدر أساعدك إزاي؟"),
        ("ezayak 3amel eh", "Ana tamam el hamdulillah, shokran 3ala so2alak! A2dar asa3dak ezay?"),
    ],
)
async def test_a_checked_small_talk_reply_is_used(c: Container, message: str, reply: str) -> None:
    llm = FakeLLM({"text": reply})
    out = await orch(c, llm).handle_turn(T, C, message)
    trace = await last_trace(c)
    assert out.text == reply == trace.response_text
    step = next(s for s in trace.steps if s.stage == "smalltalk_write")
    assert (step.status, step.detail) == ("used", "written") and trace.versions["smalltalk_prompt"] == "smalltalk_v1"
    assert trace.decision.value == "answer" and trace.escalation_reason is None
    assert message in llm.calls[0]["user"] and SHOP in llm.calls[0]["system"]


@pytest.mark.parametrize(
    ("reply", "why"),
    [
        ("I'm good! We have 20 offers today, how can I help?", "new number 20"),
        ("I'm good! Your order is on its way, anything else?", "talks about the business"),
        ("Doing well! I promise a refund if you need one.", "talks about the business"),
        ("Doing well! A colleague will call you.", "mentions a colleague"),
        ("Doing well! Visit https://example.com for deals.", "new link"),
        ("Doing well! " + "Thanks for asking. " * 30, "too long"),
    ],
)
async def test_a_reply_that_adds_a_fact_a_promise_or_a_colleague_is_replaced_by_the_template(
    c: Container, reply: str, why: str
) -> None:
    out = await orch(c, FakeLLM({"text": reply})).handle_turn(T, C, "how are you?")
    step = next(s for s in (await last_trace(c)).steps if s.stage == "smalltalk_write")
    assert out.text == TEMPLATE and step.status == "rejected" and why in step.detail


@pytest.mark.parametrize("response", [RuntimeError("model down"), {"text": ""}, {"nope": 1}])
async def test_a_failing_model_sends_the_template(c: Container, response: Any) -> None:
    out = await orch(c, FakeLLM(response)).handle_turn(T, C, "how are you?")
    step = next(s for s in (await last_trace(c)).steps if s.stage == "smalltalk_write")
    assert out.text == TEMPLATE and step.status == "failed"


async def test_without_a_writer_the_template_is_sent_and_no_model_is_called(c: Container) -> None:
    out = await orch(c, None).handle_turn(T, C, "how are you?")
    trace = await last_trace(c)
    assert out.text == TEMPLATE
    assert all(s.stage != "smalltalk_write" for s in trace.steps) and "smalltalk_prompt" not in trace.versions


async def test_a_greeting_inside_a_request_is_not_small_talk(c: Container) -> None:
    llm = FakeLLM()
    out = await orch(c, llm).handle_turn(T, C, "hi, where is my order NS-20877?")
    assert llm.calls == [] and out.decision.value == "verify_identity"


async def test_thanks_uses_the_thanks_template_as_the_draft(c: Container) -> None:
    llm = FakeLLM({"text": "You're welcome! Is there anything else I can help with?"})
    await orch(c, llm).handle_turn(T, C, "thanks, bye")
    assert "You're welcome! Is there anything else I can help you with?" in llm.calls[0]["user"]


async def test_the_customers_phone_is_hidden_from_the_small_talk_model(c: Container) -> None:
    llm = FakeLLM({"text": "Hello! How can I help you today?"})
    await orch(c, llm).handle_turn(T, C, "hi 01012345601")
    assert "01012345601" not in llm.calls[0]["user"]


# ---- the check on its own ----


def test_the_check_accepts_plain_small_talk_and_the_shop_name() -> None:
    assert problems_with("Welcome to Nile Style! How can I help you today?", TEMPLATE, SHOP) == []
    assert problems_with("Good night! Come back any time.", TEMPLATE, SHOP) == []


@pytest.mark.parametrize(
    "text",
    [
        "Your order is ready",
        "ممكن اساعدك في الاوردر بتاعك",
        "Any refund questions?",
        "I will connect you with a person",
    ],
)
def test_the_check_rejects_business_talk_in_any_style(text: str) -> None:
    assert problems_with(text, TEMPLATE, SHOP)


def test_the_prompt_names_the_style_and_the_shop() -> None:
    writer = SmallTalkWriter(FakeLLM())
    assert writer.prompt_version == "smalltalk_v1" and Locale.ARABIZI.value == "arabizi"
