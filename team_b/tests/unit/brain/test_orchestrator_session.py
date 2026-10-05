"""Session lifecycle in the orchestrator: create, remember, trim, summarize, store the transcript."""

import pytest

from team_b.brain.orchestrator import CLARIFY_TEXT, Orchestrator
from team_b.brain.summarizer import LLMHistorySummarizer
from team_b.brain.transcript import transcript_from_traces
from team_b.container import Container
from team_b.domain.tenant import UnknownTenantError
from team_b.domain.understanding import Language
from tests.fakes import FakeLLM

T, C = "shop_001", "conv-1"


def orch(container: Container) -> Orchestrator:
    assert container.orchestrator is not None
    return container.orchestrator


async def test_first_turn_creates_the_session_and_remembers_the_detected_language(c: Container) -> None:
    assert c.orchestrator is not None
    reply = await c.orchestrator.handle_turn(T, C, "Hello")
    session = await c.sessions.load(T, C)
    assert session is not None
    assert (session.turn_index, session.language, session.awaiting) == (1, Language.EN, "detail")
    assert [m.role for m in session.history] == ["customer", "agent"]
    assert session.history[1].text == CLARIFY_TEXT and session.history[1].trace_id == reply.trace_id
    assert session.version == 1


async def test_a_new_session_starts_in_the_tenant_language_and_a_message_without_language_keeps_it(
    c: Container,
) -> None:
    assert c.orchestrator is not None
    await c.orchestrator.handle_turn(T, C, "01012345601")  # no language content
    session = await c.sessions.load(T, C)
    assert session is not None and session.language is Language.AR  # shop_001 default_locale is ar
    await c.orchestrator.handle_turn(T, C, "3ayez a3raf feen el order")
    await c.orchestrator.handle_turn(T, C, "NS-20877")  # again no language content
    session = await c.sessions.load(T, C)
    assert session is not None and session.language is Language.ARABIZI


async def test_turns_accumulate_and_each_leaves_a_trace(c: Container) -> None:
    for text in ("one", "two", "three"):
        await orch(c).handle_turn(T, C, text)
    session = await c.sessions.load(T, C)
    traces = await c.traces.for_conversation(T, C)
    assert session is not None and session.turn_index == 3 and session.version == 3
    assert [t.turn_index for t in traces] == [0, 1, 2]


async def test_unknown_tenant_is_an_error_and_stores_nothing(c: Container) -> None:
    with pytest.raises(UnknownTenantError):
        await orch(c).handle_turn("nobody", C, "Hello")
    assert await c.sessions.load("nobody", C) is None


async def test_fifty_messages_keep_history_at_the_limit_with_a_summary(c: Container) -> None:
    limit = c.tenants.get(T).history_max_turns
    for n in range(25):  # 25 customer messages + 25 replies = 50 messages
        await orch(c).handle_turn(T, C, f"message {n} about NS-20877" if n == 0 else f"message {n}")
    session = await c.sessions.load(T, C)
    assert session is not None
    assert session.turn_index == 25 and len(session.history) == limit
    assert session.history_summary and "NS-20877" in session.history_summary  # folded facts survive
    assert session.history[-1].role == "agent"


async def test_the_transcript_can_be_rebuilt_from_traces_with_values_redacted(
    c: Container,
) -> None:
    await orch(c).handle_turn(T, C, "my phone is 01012345601")
    await orch(c).handle_turn(T, C, "order NS-20877")
    lines = transcript_from_traces(await c.traces.for_conversation(T, C))
    assert [(line.role, line.text) for line in lines] == [
        ("customer", "my phone is [phone]"),
        ("agent", CLARIFY_TEXT),
        ("customer", "order NS-20877"),
        ("agent", CLARIFY_TEXT),
    ]
    assert not any("01012345601" in t.model_dump_json() for t in await c.traces.for_conversation(T, C))


async def test_an_llm_summary_with_an_invented_number_is_not_stored(c: Container) -> None:
    llm = FakeLLM(*[{"summary": "The customer owes 9999 EGP."}] * 20)
    custom = Orchestrator(
        clock=c.clock,
        tenants=c.tenants,
        sessions=c.sessions,
        traces=c.traces,
        cases=c.cases,
        summarizer=LLMHistorySummarizer(llm),
    )
    for n in range(10):
        await custom.handle_turn(T, C, f"message {n}")
    session = await c.sessions.load(T, C)
    assert session is not None and llm.calls  # the model was asked
    assert "9999" not in session.history_summary and session.history_summary.startswith("Intents seen:")


async def test_the_trace_records_how_the_message_was_understood(c: Container) -> None:
    assert c.orchestrator is not None
    await c.orchestrator.handle_turn(T, C, "refund NS-20512 please, my phone is 01012345601, 300 EGP")
    (trace,) = await c.traces.for_conversation(T, C)
    assert trace.language is Language.EN and trace.nlu_method == "rules"
    assert [i.name for i in trace.intents] == ["refund_request"]
    assert trace.entities == {"order_id": "NS-20512", "phone": "[phone]", "amount": "300"}  # the phone is hidden
    assert trace.frustration == "low" and trace.versions == {}


async def test_an_ai_assisted_turn_records_the_method_and_the_prompt_version(c: Container) -> None:

    from team_b.brain.llm_nlu import LLMNLU

    llm = FakeLLM({"language": "en", "intents": [{"name": "refund_request", "confidence": 0.9}]})
    custom = Orchestrator(
        clock=c.clock, tenants=c.tenants, sessions=c.sessions, traces=c.traces, cases=c.cases, nlu=LLMNLU(llm)
    )
    await custom.handle_turn(T, C, "I want my money back")
    (trace,) = await c.traces.for_conversation(T, C)
    assert trace.nlu_method == "llm" and trace.versions == {"prompt": "nlu_v1"}
    assert [i.name for i in trace.intents] == ["refund_request"]


async def test_a_failing_model_still_answers_and_the_trace_says_fallback(c: Container) -> None:
    from team_b.brain.llm_nlu import LLMNLU

    custom = Orchestrator(
        clock=c.clock, tenants=c.tenants, sessions=c.sessions, traces=c.traces, cases=c.cases,
        nlu=LLMNLU(FakeLLM(RuntimeError("down"))),
    )  # fmt: skip
    reply = await custom.handle_turn(T, C, "Where is my order NS-20877?")
    (trace,) = await c.traces.for_conversation(T, C)
    assert reply.trace_id == trace.trace_id and trace.nlu_method == "rules_fallback"
    assert [i.name for i in trace.intents] == ["order_status"]
