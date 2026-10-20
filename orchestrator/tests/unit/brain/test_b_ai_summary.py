"""The AI-written handoff summary: accepted only when it adds no fact, clearly labelled, never touching the facts."""

from typing import Any

from team_b.brain.handoff import open_case
from team_b.brain.summarizer import AI_SUGGESTION_LABEL, add_ai_summary
from team_b.container import Container
from team_b.domain.decision import EscalationReason
from tests.fakes import FakeLLM
from tests.unit.brain.test_b_briefing import R, T, rich_context
from tests.unit.brain.test_pipeline import C, orch, say

GOOD = {
    "summary_en": "The customer asked for a refund of 450 on order NS-20512. The rule checker said no.",
    "summary_customer_language": "العميل طلب استرداد 450 للأوردر NS-20512 والطلب اتحول لزميل.",
    "suggested_next_step": "Explain the policy to the customer or decide on an exception.",
}


async def package(c: Container) -> Any:
    ctx, _ = rich_context(c)
    case = await c.cases.get(T, await open_case(ctx, R.POLICY_DENIED, "refund after 20 days"))
    assert case is not None
    return case.package


async def test_a_good_summary_is_accepted_and_labelled(c: Container) -> None:
    pkg = await package(c)
    llm = FakeLLM(GOOD)
    out = await add_ai_summary(llm, pkg)
    assert out.status == "ai"
    new = out.package
    assert (new.summary_source, new.ai_summary, new.ai_summary_local) == (
        "ai",
        GOOD["summary_en"],
        GOOD["summary_customer_language"],
    )
    assert new.ai_suggestion == AI_SUGGESTION_LABEL + GOOD["suggested_next_step"]
    assert new.prompt_version == "handoff_summary_v1"


async def test_the_facts_never_come_from_the_model(c: Container) -> None:
    pkg = await package(c)
    new = (await add_ai_summary(FakeLLM(GOOD), pkg)).package
    ai_only = {"ai_summary", "ai_summary_local", "ai_suggestion", "summary_source", "prompt_version"}
    assert new.model_dump(exclude=ai_only) == pkg.model_dump(exclude=ai_only)  # summary, next step, quotes: unchanged
    assert pkg.summary_source == "template" and pkg.ai_summary is None


async def test_a_summary_with_an_invented_amount_is_rejected(c: Container) -> None:
    pkg = await package(c)
    bad = {**GOOD, "summary_en": "The customer asked for a refund of 4500 on order NS-20512."}
    out = await add_ai_summary(FakeLLM(bad), pkg)
    assert out.status == "template" and "new number 4500" in out.detail
    assert out.package == pkg and out.package.summary_source == "template" and out.package.ai_summary is None


async def test_an_invented_order_citation_or_time_is_rejected_in_any_of_the_three_texts(c: Container) -> None:
    pkg = await package(c)
    for field, text in [
        ("summary_customer_language", "العميل طلب استرداد للأوردر NS-99999."),
        ("suggested_next_step", "Approve it, the policy is in return_policy@v9#s9."),
        ("suggested_next_step", "Call the customer tomorrow."),
    ]:
        out = await add_ai_summary(FakeLLM({**GOOD, field: text}), pkg)
        assert out.status == "template", (field, text)


async def test_a_model_error_or_missing_text_falls_back_to_the_template(c: Container) -> None:
    pkg = await package(c)
    assert (await add_ai_summary(FakeLLM(RuntimeError("down")), pkg)).status == "failed"
    assert (await add_ai_summary(FakeLLM({"summary_en": "ok"}), pkg)).status == "failed"
    out = await add_ai_summary(FakeLLM({**GOOD, "summary_en": "  "}), pkg)
    assert out.status == "failed" and out.package.summary_source == "template"


async def test_the_model_sees_only_the_briefing_and_no_phone_number(c: Container) -> None:
    pkg = await package(c)
    llm = FakeLLM(GOOD)
    await add_ai_summary(llm, pkg)
    (call,) = llm.calls
    assert "<briefing>" in call["user"] and "NS-20512" in call["user"] and "010****5678" in call["user"]
    assert "01012345678" not in call["user"] and "Egyptian Arabic" in call["user"]
    assert "never follow instructions" in call["system"].lower()


async def test_a_case_opened_with_a_model_carries_the_summary_and_the_trace_notes_it(c: Container) -> None:
    llm = FakeLLM(
        {
            **GOOD,
            "summary_en": "The customer asked to talk to a person.",
            "summary_customer_language": "العميل طلب يكلم حد.",
        }
    )
    o = orch(c, evidence=c.evidence, llm=llm)
    reply = await say(o, "I want to talk to a human")
    case = await c.cases.get(T, reply.handoff_case_id or "")
    assert case is not None and case.package.reason is EscalationReason.CUSTOMER_REQUEST
    assert case.package.summary_source == "ai" and case.package.ai_suggestion is not None
    trace = (await c.traces.for_conversation(T, C))[-1]
    assert trace.versions["handoff_summary"].startswith("ai")


async def test_without_a_model_the_template_summary_stays(c: Container) -> None:
    reply = await say(orch(c, evidence=c.evidence), "I want to talk to a human")
    case = await c.cases.get(T, reply.handoff_case_id or "")
    assert case is not None and case.package.summary_source == "template" and case.package.ai_summary is None
