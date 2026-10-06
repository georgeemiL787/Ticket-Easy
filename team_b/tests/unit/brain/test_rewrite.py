"""AI rewording and the fact check that stops it from inventing anything (safety-critical)."""

from typing import Any

import pytest

from team_b.brain.composer import check_grounded, normalize_digits
from team_b.brain.orchestrator import Orchestrator
from team_b.brain.rewrite import REWRITE_PROMPT, LLMRewriter
from team_b.brain.turn import PlannedIntent, Step, TurnContext
from team_b.config import Settings
from team_b.container import Container, ContainerError, build_container
from team_b.contracts.errors import UpstreamError
from team_b.contracts.evidence import Passage
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.understanding import Locale
from tests.fakes import FakeEvidence, FakeLLM

T, C = "shop_001", "conv-1"
ORIGINAL = "Your order NS-20877 is on its way to you. It is expected on 2026-09-24 and costs EGP 1250."
SOURCES = [ORIGINAL]


def bad(text: str, **kw: Any) -> list[str]:
    problems = check_grounded(text, SOURCES, **kw)
    assert problems, f"expected a rejection for: {text}"
    return problems


# ---- what passes ----


@pytest.mark.parametrize(
    "text",
    [
        ORIGINAL,
        "Good news: order NS-20877 is on the way and should reach you on 2026-09-24. The total is EGP 1250.",
        "Your order NS-20877 is on its way. Expected 2026-09-24. Total: 1250 EGP.",
        "Order NS-20877 is out for delivery, due on 2026-09-24, total EGP 1,250.",  # a thousands separator
        "Order NS-20877 is on its way, due 2026-09-24, total EGP 1250.00.",  # the same number
        "Your order ns-20877 is on its way. Expected 2026-09-24. EGP 1250.",  # case does not matter
    ],
)
def test_a_grounded_rewrite_passes(text: str) -> None:
    assert check_grounded(text, SOURCES, keep=SOURCES) == []


def test_arabic_digits_are_read_as_the_same_numbers() -> None:
    sources = ["الأوردر NS-20877 هيوصل يوم 24 وسعره 1250 جنيه"]
    assert check_grounded("الأوردر NS-٢٠٨٧٧ هيوصل يوم ٢٤ وسعره ١٬٢٥٠ جنيه", sources, keep=sources) == []
    assert check_grounded("الأوردر NS-20877 هيوصل يوم ٢٥", sources) == ["new number 25"]


def test_normalize_digits() -> None:
    assert normalize_digits("٠١٢٣٤٥٦٧٨٩ ۰۱۲") == "0123456789 012"
    assert normalize_digits("3,000 and 1,250.5 and ١٬٢٥٠٫٥") == "3000 and 1250.5 and 1250.5"
    assert normalize_digits("a, b, 12,34") == "a, b, 12,34"  # not a thousands group


# ---- each rejection category ----


def test_rejects_a_new_number() -> None:
    assert "new number 3" in bad("Your order NS-20877 is on its way, 3 items. Expected 2026-09-24, EGP 1250.")
    assert "new number 5" in bad(ORIGINAL + " It should take 5 days.")


def test_rejects_a_changed_amount() -> None:
    assert "new number 1520" in bad(ORIGINAL.replace("1250", "1520"))
    assert "new number 12500" in bad(ORIGINAL.replace("1250", "12,500"))
    assert "new number 1250.5" in bad(ORIGINAL.replace("1250", "1250.5"))
    assert "new number 125" in bad(ORIGINAL.replace("1250", "125"))


def test_rejects_arabic_digits_that_are_a_new_number() -> None:
    assert "new number 999" in bad("الأوردر NS-20877 سعره ٩٩٩ جنيه".replace("999", "٩٩٩"), keep=[])
    assert bad(ORIGINAL.replace("1250", "١٢٥١")) == ["new number 1251"]


def test_rejects_a_new_date() -> None:
    assert "new date 2026-09-25" in bad(ORIGINAL.replace("2026-09-24", "2026-09-25"))
    assert any("new date" in p for p in bad(ORIGINAL.replace("2026-09-24", "2026-24-09")))  # digits swapped
    assert "new date 25/09/2026" in bad(ORIGINAL + " Or on 25/09/2026.")


def test_rejects_a_new_month_even_when_the_numbers_are_the_same() -> None:
    sources = ["Your order is expected on 24 Sep."]
    assert check_grounded("It should arrive on 24 Sep.", sources) == []
    assert check_grounded("It should arrive on 24 September, as planned.", sources) == []
    assert "new month 10" in check_grounded("It should arrive on 24 Oct.", sources)
    assert "new month 10" in check_grounded("It should arrive on October 24.", sources)
    assert "new month 10" in check_grounded("هيوصل يوم 24 أكتوبر", sources)


def test_a_month_word_outside_a_date_is_just_a_word() -> None:
    assert check_grounded("You may call us", SOURCES) == []  # "may" is not a month here


def test_rejects_a_new_order_id() -> None:
    assert "new order or reference id ns-20878" in bad(ORIGINAL.replace("20877", "20878"))
    assert any("new order or reference id" in p for p in bad(ORIGINAL.replace("NS-", "ES-")))
    assert "new order or reference id ret-30001" in bad(ORIGINAL + " Reference RET-30001.")


def test_rejects_a_new_link_or_citation_and_a_new_currency_or_time_word() -> None:
    assert any("new link" in p for p in bad(ORIGINAL + " Track it at https://example.com/track."))
    assert any("new link" in p for p in bad(ORIGINAL + " See www.nilestyle.example"))
    assert any("new citation" in p for p in bad(ORIGINAL + " (return_policy@v2#s2)"))
    assert "new currency usd" in bad(ORIGINAL.replace("EGP 1250", "USD 1250"))
    assert "new currency egp" in check_grounded("It costs 5 EGP", ["It costs 5 dollars"])
    assert "new time word tomorrow" in bad("Your order NS-20877 will arrive tomorrow, 2026-09-24, EGP 1250.")
    assert "new time word بكرة" in check_grounded("هيوصل بكرة", ["هيوصل يوم الخميس"])


def test_currency_words_in_the_same_family_are_the_same_currency() -> None:
    assert check_grounded("1250 جنيه", ["EGP 1250"]) == []
    assert check_grounded("1250 geneh", ["EGP 1250"]) == []


def test_a_citation_in_the_sources_may_be_repeated() -> None:
    sources = ["Policy return_policy@v2#s2 says 14 days."]
    assert check_grounded("As return_policy@v2#s2 says, 14 days.", sources) == []


# ---- what must not be dropped ----


def test_a_rewrite_may_not_drop_what_the_original_carries() -> None:
    assert "dropped order or reference id ns-20877" in check_grounded(
        "Your order is on its way.", SOURCES, keep=SOURCES
    )
    assert "dropped number 1250" in check_grounded(
        "Order NS-20877 is on its way, due 2026-09-24.", SOURCES, keep=SOURCES
    )
    assert "dropped date 2026-09-24" in check_grounded(
        "Order NS-20877 is on its way, total EGP 1250, soon.", SOURCES, keep=SOURCES
    )
    assert check_grounded("Order NS-20877 is on its way, total EGP 1250, soon.", SOURCES) == []  # only when asked


# ---- the rewriter with a scripted model ----


def rewriter(*responses: dict[str, Any] | Exception) -> tuple[LLMRewriter, FakeLLM]:
    llm = FakeLLM(*responses)
    return LLMRewriter(llm), llm


async def test_a_grounded_rewrite_is_used() -> None:
    r, llm = rewriter({"text": "Good news! Order NS-20877 is on the way, due 2026-09-24, total EGP 1250."})
    result = await r.reword(ORIGINAL, Locale.EN, {"order_id": "NS-20877"})
    assert (result.status, result.text) == (
        "used",
        "Good news! Order NS-20877 is on the way, due 2026-09-24, total EGP 1250.",
    )
    assert "- order_id: NS-20877" in llm.calls[0]["user"] and ORIGINAL in llm.calls[0]["user"]
    assert "plain, warm, polite English" in llm.calls[0]["system"] and llm.calls[0]["temperature"] == 0.0


@pytest.mark.parametrize(
    ("rewrite", "why"),
    [
        (ORIGINAL + " It will take 3 more days.", "new number 3"),
        (ORIGINAL.replace("1250", "1520"), "new number 1520"),
        (ORIGINAL.replace("20877", "20878"), "new order or reference id"),
        (ORIGINAL.replace("2026-09-24", "2026-09-25"), "new date"),
        ("Your order is on its way and will be there tomorrow.", "new time word tomorrow"),
        ("Your order is on its way.", "dropped"),
        (ORIGINAL + " " + "Thank you for shopping with us. " * 20, "much longer"),
    ],
)
async def test_an_ungrounded_rewrite_falls_back_to_the_original(rewrite: str, why: str) -> None:
    r, _ = rewriter({"text": rewrite})
    result = await r.reword(ORIGINAL, Locale.EN, {})
    assert result.status == "rejected" and result.text == ORIGINAL and why in result.detail


@pytest.mark.parametrize(
    "response",
    [
        UpstreamError("llm", "TIMEOUT", "slow", retryable=True),
        RuntimeError("boom"),
        {"text": ""},
        {"text": 5},
        {"other": "x"},
        {},
    ],
)
async def test_a_failing_model_falls_back_to_the_original(response: dict[str, Any] | Exception) -> None:
    r, _ = rewriter(response)
    result = await r.reword(ORIGINAL, Locale.EN, {})
    assert result.status == "failed" and result.text == ORIGINAL


async def test_the_register_follows_the_locale() -> None:
    r, llm = rewriter({"text": "تمام، الأوردر في الطريق ليك."})
    await r.reword("الأوردر في الطريق ليك.", Locale.AR, {})
    assert "Egyptian Arabic" in llm.calls[0]["system"]
    assert LLMRewriter(FakeLLM()).prompt_version == REWRITE_PROMPT


async def test_phones_are_hidden_from_the_model() -> None:
    r, llm = rewriter({"text": "ok"})
    await r.reword("We will call 01012345601 soon.", Locale.EN, {"phone": "01012345601"})
    assert "01012345601" not in llm.calls[0]["user"] and "[phone]" in llm.calls[0]["user"]


# ---- in the pipeline ----


def orch(c: Container, llm: FakeLLM | None, **kw: Any) -> Orchestrator:
    options: dict[str, Any] = {"evidence": FakeEvidence(), "tenants": c.tenants, "capabilities": c.capabilities, **kw}
    if llm is not None:
        options["rewriter"] = LLMRewriter(llm)
    return Orchestrator(clock=c.clock, sessions=c.sessions, traces=c.traces, cases=c.cases, **options)


async def trace_of(c: Container):  # type: ignore[no-untyped-def]
    return (await c.traces.for_conversation(T, C))[-1]


async def test_a_used_rewrite_reaches_the_customer_and_is_recorded(c: Container) -> None:
    llm = FakeLLM({"text": "Hi there! How can I help you today?"})
    reply = await orch(c, llm).handle_turn(T, C, "Hello")
    trace = await trace_of(c)
    assert reply.text == "Hi there! How can I help you today?" == trace.response_text
    step = next(s for s in trace.steps if s.stage == "rewrite")
    assert (step.status, step.detail) == ("used", "reworded") and trace.versions["rewrite_prompt"] == "rewrite_v1"


async def test_a_rejected_rewrite_sends_the_template_and_says_so(c: Container) -> None:
    llm = FakeLLM({"text": "Hi! We have 3 offers today."})
    reply = await orch(c, llm).handle_turn(T, C, "Hello")
    assert reply.text == "Hello! How can I help you today?"
    step = next(s for s in (await trace_of(c)).steps if s.stage == "rewrite")
    assert step.status == "rejected" and "new number 3" in step.detail


async def test_without_a_rewriter_nothing_is_recorded_and_no_model_is_called(c: Container) -> None:
    reply = await orch(c, None).handle_turn(T, C, "Hello")
    trace = await trace_of(c)
    assert reply.text == "Hello! How can I help you today?"
    assert all(s.stage != "rewrite" for s in trace.steps) and "rewrite_prompt" not in trace.versions


@pytest.mark.parametrize(
    "text",
    [
        "I want to talk to a human",
        "someone used my card, it is fraud",
        "yes",
        "Where is my order NS-20877 and cancel it",
    ],
)
async def test_high_stakes_replies_are_never_reworded(c: Container, text: str) -> None:
    llm = FakeLLM(*[{"text": "reworded"}] * 4)
    evidence = FakeEvidence()
    if "fraud" in text:
        from team_b.contracts.evidence import RiskAssessment

        evidence = FakeEvidence(RiskAssessment(flagged=True, categories=("fraud_suspected",)))
    reply = await orch(c, llm, evidence=evidence).handle_turn(T, C, text)
    if reply.decision in (Decision.HANDOFF, Decision.CONFIRM, Decision.EXECUTE, Decision.REFUSE):
        assert llm.calls == [] and reply.text != "reworded"


async def test_confirmation_execution_refusal_and_handoff_decisions_are_not_eligible() -> None:
    from team_b.brain.rewrite import REWRITABLE

    assert REWRITABLE == {Decision.ANSWER, Decision.CLARIFY, Decision.VERIFY_IDENTITY}


async def test_policy_passages_are_appended_after_the_rewrite_and_never_sent_to_the_model(c: Container) -> None:
    quote = "يحق للعميل استرجاع المنتج خلال 14 يومًا من تاريخ الاستلام."
    passage = Passage(
        passage_id="return_policy@v2#s2", document_id="return_policy", version="v2", section="s2",
        language="ar", text=quote, score=1.0,
    )  # fmt: skip

    async def knowledge(ctx: TurnContext, planned: PlannedIntent) -> Step:
        return Step(
            Decision.ANSWER, reason="found it", reply_key="thanks", citations=(passage.citation,), passages=(passage,)
        )

    llm = FakeLLM({"text": "You are welcome! Anything else?"})
    reply = await orch(c, llm, handlers={"knowledge": knowledge}).handle_turn(T, C, "What is your return policy?")
    assert reply.text == f"You are welcome! Anything else?\n\n“{quote}” [return_policy@v2#s2]"
    assert quote not in llm.calls[0]["user"] and "return_policy@v2#s2" not in llm.calls[0]["user"]
    assert reply.citations == ("return_policy@v2#s2",)


async def test_the_passage_is_quoted_verbatim_even_when_the_rewrite_is_rejected(c: Container) -> None:
    quote = "Returns are accepted within 14 days."
    passage = Passage(
        passage_id="return_policy@v2#s2", document_id="return_policy", version="v2", section="s2",
        language="en", text=quote, score=1.0,
    )  # fmt: skip

    async def knowledge(ctx: TurnContext, planned: PlannedIntent) -> Step:
        return Step(Decision.ANSWER, reason="found it", reply_key="thanks", passages=(passage,))

    llm = FakeLLM({"text": "You are welcome, 99 times over!"})
    reply = await orch(c, llm, handlers={"knowledge": knowledge}).handle_turn(T, C, "What is your return policy?")
    assert reply.text.endswith(f"“{quote}” [return_policy@v2#s2]") and "99" not in reply.text


# ---- settings and wiring ----


def test_the_rewrite_switch_defaults_to_off_and_reads_the_environment() -> None:
    assert Settings().llm_rewrite is False
    assert Settings.from_env({"TEAM_B_LLM_REWRITE": "1"}).llm_rewrite is True
    assert Settings.from_env({"TEAM_B_LLM_REWRITE": "0"}).llm_rewrite is False


def test_rewrite_needs_an_ai_model() -> None:
    with pytest.raises(ContainerError, match="TEAM_B_LLM_REWRITE=1 needs an AI model"):
        build_container(Settings(llm_rewrite=True, llm="none"))


def test_the_container_builds_the_rewriter_only_when_asked() -> None:
    off = build_container(Settings(llm="ollama"))
    on = build_container(Settings(llm="ollama", llm_rewrite=True))
    assert off.orchestrator is not None and on.orchestrator is not None
    assert off.orchestrator._deps.rewriter is None  # type: ignore[attr-defined]
    assert isinstance(on.orchestrator._deps.rewriter, LLMRewriter)  # type: ignore[attr-defined]


def test_a_handoff_step_keeps_its_reason_when_passages_are_added() -> None:
    step = Step(Decision.HANDOFF, reason="x", reply_key="handoff_generic", escalation=EscalationReason.UNSUPPORTED)
    assert step.passages == ()
