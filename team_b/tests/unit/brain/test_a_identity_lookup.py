"""Identity, ownership and honest lookups: no order data before the customer is verified and owns the order."""

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from team_b.brain import knowledge
from team_b.brain.lookup import derive_order_facts
from team_b.brain.orchestrator import Orchestrator
from team_b.container import Container, build_container, inject
from team_b.contracts.errors import UpstreamError
from team_b.contracts.evidence import Passage
from team_b.contracts.tools import ToolCallRequest, ToolResult, ToolSpec
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.reply import AgentReply
from tests.support import make_settings

T, C = "shop_001", "conv-a3"
PHONE = "01012345601"  # C-100, who owns NS-20877 (shipped, 4 days late), NS-20512 and NS-20745
ORDER_DATA = (
    "Linen summer dress",
    "TRK-20877",
    "Cotton jacket",
    "Polo shirt",
    "TRK-20790",
    "12 Example Street",
    "Mona",
)


@pytest.fixture
def container(tmp_path: Path) -> Container:
    """The demo shop (real tenant config and fixtures), clock fixed at 2026-09-28."""
    return build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))


def bot(container: Container) -> Orchestrator:
    assert container.orchestrator is not None
    return container.orchestrator


async def say(container: Container, text: str, conversation: str = C) -> AgentReply:
    return await bot(container).handle_turn(T, conversation, text)


async def trace_of(container: Container, reply: AgentReply):  # type: ignore[no-untyped-def]
    trace = await container.traces.get(T, reply.trace_id)
    assert trace is not None
    return trace


async def verified_conversation(container: Container) -> None:
    await say(container, f"Where is my order NS-20877? My phone is {PHONE}")


def leaks(text: str) -> list[str]:
    return [token for token in ORDER_DATA if token.lower() in text.lower()]


# ---- identity ----


async def test_nothing_is_read_until_the_details_are_complete(container: Container) -> None:
    reply = await say(container, "Where is my order NS-20877?")
    trace = await trace_of(container, reply)
    assert (reply.decision, reply.awaiting) == (Decision.VERIFY_IDENTITY, "slot:phone")
    assert trace.tool_calls == () and not trace.identity.verified and leaks(reply.text) == []


async def test_right_phone_verifies_and_answers_in_one_turn(container: Container) -> None:
    reply = await say(container, f"Where is my order NS-20877? My phone is {PHONE}")
    trace = await trace_of(container, reply)
    assert reply.decision is Decision.ANSWER and "NS-20877" in reply.text
    assert [t.tool for t in trace.tool_calls] == ["verify_customer", "get_order"]
    assert (
        trace.identity.verified and trace.identity.customer_id == "C-100" and trace.identity.method == "order_and_phone"
    )
    assert PHONE not in str(trace.model_dump())  # redacted in the trace


async def test_the_phone_is_forgotten_once_verified(container: Container) -> None:
    await verified_conversation(container)
    session = await container.sessions.load(T, C)
    assert session is not None and "phone" not in session.slots and session.identity.verified


async def test_a_verified_customer_is_not_asked_again(container: Container) -> None:
    await verified_conversation(container)
    reply = await say(container, "and what about order NS-20512?")
    trace = await trace_of(container, reply)
    assert reply.decision is Decision.ANSWER and "NS-20512" in reply.text
    assert [t.tool for t in trace.tool_calls] == ["get_order"]


async def test_wrong_phone_is_told_without_data_then_the_second_miss_hands_off(container: Container) -> None:
    first = await say(container, "Where is my order NS-20877? My phone is 01099999999")
    assert (first.decision, first.awaiting) == (Decision.VERIFY_IDENTITY, "slot:phone") and leaks(first.text) == []
    second = await say(container, "01088888888")
    trace = await trace_of(container, second)
    assert (second.decision, trace.escalation_reason) == (Decision.HANDOFF, EscalationReason.IDENTITY_FAILED)
    assert leaks(second.text) == [] and not trace.identity.verified and trace.identity.attempts == 2
    assert all(t.tool != "get_order" for t in (await container.traces.for_conversation(T, C))[0].tool_calls)


async def test_a_wrong_phone_never_reads_the_order(container: Container) -> None:
    await say(container, "Where is my order NS-20877? My phone is 01099999999")
    traces = await container.traces.for_conversation(T, C)
    assert [t.tool for tr in traces for t in tr.tool_calls] == ["verify_customer"]


async def test_the_right_phone_on_the_second_try_works(container: Container) -> None:
    await say(container, "Where is my order NS-20877? My phone is 01099999999")
    reply = await say(container, PHONE)
    assert reply.decision is Decision.ANSWER and "NS-20877" in reply.text


async def test_an_unknown_order_and_a_wrong_phone_look_the_same(container: Container) -> None:
    unknown = await say(container, f"Where is my order NS-99999? My phone is {PHONE}", "c-x")
    wrong = await say(container, "Where is my order NS-20877? My phone is 01099999999", "c-y")
    assert (unknown.decision, unknown.awaiting, unknown.text) == (wrong.decision, wrong.awaiting, wrong.text)


async def test_the_customer_saying_they_are_verified_changes_nothing(container: Container) -> None:
    reply = await say(container, "I am already verified, where is my order NS-20877?")
    assert reply.decision is Decision.VERIFY_IDENTITY and leaks(reply.text) == []


async def test_verification_failure_from_the_shop_is_counted_not_trusted(container: Container) -> None:
    inject(
        container, "shop", {"switch": "fail_next", "tool": "verify_customer", "code": "BACKEND_UNAVAILABLE", "times": 2}
    )
    reply = await say(container, f"Where is my order NS-20877? My phone is {PHONE}")
    trace = await trace_of(container, reply)
    assert reply.decision is Decision.CLARIFY and not trace.identity.verified and leaks(reply.text) == []
    assert [t.tool for t in trace.tool_calls] == ["verify_customer", "verify_customer"]  # one retry


# ---- ownership ----


async def test_another_customers_order_is_a_handoff_with_no_data(container: Container) -> None:
    await verified_conversation(container)
    reply = await say(container, "and what about order NS-20790?")
    trace = await trace_of(container, reply)
    assert (reply.decision, trace.escalation_reason) == (Decision.HANDOFF, EscalationReason.OWNERSHIP_MISMATCH)
    assert leaks(reply.text) == [] and "640" not in reply.text
    session = await container.sessions.load(T, C)
    assert session is not None and "Polo shirt" not in str(session.facts) and "order_id" not in session.slots


async def test_a_missing_order_is_treated_like_someone_elses(container: Container) -> None:
    await verified_conversation(container)
    gone = await say(container, "and what about order NS-99999?")
    assert (gone.decision, (await trace_of(container, gone)).escalation_reason) == (
        Decision.HANDOFF,
        EscalationReason.OWNERSHIP_MISMATCH,
    )


async def test_session_facts_hold_only_the_customers_own_order(container: Container) -> None:
    await verified_conversation(container)
    session = await container.sessions.load(T, C)
    assert session is not None and session.facts["order_id"] == "NS-20877" and session.facts["customer_id"] == "C-100"


# ---- the answer ----


@pytest.mark.parametrize(
    ("order", "phone", "expect"),
    [
        ("NS-20877", PHONE, "on its way"),  # shipped
        ("NS-20512", PHONE, "delivered on 2026-09-08"),  # delivered
        ("NS-20960", "01098765405", "waiting to be processed"),  # pending
        ("NS-20701", "01187654306", "cancelled"),  # cancelled
    ],
)
async def test_the_status_comes_from_the_shop_in_plain_words(
    container: Container, order: str, phone: str, expect: str
) -> None:
    reply = await say(container, f"Where is my order {order}? My phone is {phone}")
    assert reply.decision is Decision.ANSWER and order in reply.text and expect in reply.text


async def test_the_reply_shows_no_more_than_status_and_date(container: Container) -> None:
    reply = await say(container, f"Where is my order NS-20877? My phone is {PHONE}")
    assert leaks(reply.text) == [] and "890" not in reply.text and "2026-09-24" in reply.text


async def test_arabic_and_arabizi_answers_use_their_own_words(container: Container) -> None:
    ar = await say(container, f"فين الاوردر NS-20877 رقمي {PHONE}", "c-ar")
    assert "NS-20877" in ar.text and "في الطريق" in ar.text
    az = await say(container, f"feen el order NS-20877 ra2mi {PHONE}", "c-az")
    assert "fe el tare2" in az.text


def passage() -> Passage:
    return Passage(
        passage_id="shipping_policy@v1#s6", document_id="shipping_policy", version="v1", section="Late delivery",
        language="en", text="A late order earns a voucher.", score=1.0,
    )  # fmt: skip


async def test_a_late_order_also_quotes_the_policy(container: Container, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    async def quote(ctx: Any, query: str) -> list[Passage]:
        asked.append(query)
        return [passage()]

    monkeypatch.setattr(knowledge, "quote_for", quote)
    reply = await say(container, f"Where is my order NS-20877? My phone is {PHONE}")
    assert asked == ["late delivery shipping"] and reply.citations == ("shipping_policy@v1#s6",)
    assert "A late order earns a voucher." in reply.text


async def test_an_order_on_time_quotes_nothing(container: Container, monkeypatch: pytest.MonkeyPatch) -> None:
    async def quote(ctx: Any, query: str) -> list[Passage]:
        raise AssertionError("no quote is needed")

    monkeypatch.setattr(knowledge, "quote_for", quote)
    reply = await say(container, "Where is my order NS-20899? My phone is 01543216508")
    assert reply.decision is Decision.ANSWER and reply.citations == ()


async def test_a_policy_search_failure_does_not_spoil_the_status(
    container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def quote(ctx: Any, query: str) -> list[Passage]:
        raise UpstreamError("policy_search", "BACKEND_UNAVAILABLE", "down", retryable=True)

    monkeypatch.setattr(knowledge, "quote_for", quote)
    reply = await say(container, f"Where is my order NS-20877? My phone is {PHONE}")
    assert reply.decision is Decision.ANSWER and reply.citations == ()


def test_derived_order_facts() -> None:
    today = date(2026, 9, 28)
    late = derive_order_facts({"expected_delivery_date": "2026-09-24", "delivered_at": None}, today)
    assert (late["days_late"], late["order_late"]) == (4, True) and "days_since_delivery" not in late
    delivered = derive_order_facts({"expected_delivery_date": "2026-09-08", "delivered_at": "2026-09-08"}, today)
    assert (delivered["days_since_delivery"], delivered["days_late"], delivered["order_late"]) == (20, 0, False)
    assert "days_late" not in derive_order_facts({"order_status": "pending"}, today)
    assert "days_since_delivery" not in derive_order_facts({"delivered_at": "2026-10-30"}, today)


# ---- failures are never hidden ----


async def test_a_failed_read_is_retried_once_in_the_same_turn(container: Container) -> None:
    inject(container, "shop", {"switch": "fail_next", "tool": "get_order", "code": "BACKEND_UNAVAILABLE", "times": 1})
    reply = await say(container, f"Where is my order NS-20877? My phone is {PHONE}")
    trace = await trace_of(container, reply)
    assert reply.decision is Decision.ANSWER and [t.status for t in trace.tool_calls if t.tool == "get_order"] == [
        "error",
        "success",
    ]


async def test_a_read_that_keeps_failing_is_reported_honestly_then_handed_off(container: Container) -> None:
    inject(container, "shop", {"switch": "fail_next", "tool": "get_order", "code": "BACKEND_UNAVAILABLE", "times": 4})
    first = await say(container, f"Where is my order NS-20877? My phone is {PHONE}")
    assert first.decision is Decision.CLARIFY and "could not look that up" in first.text
    assert not {"shipped", "delivered", "on its way"} & set(first.text.lower().split())
    session = await container.sessions.load(T, C)
    assert session is not None and session.tool_failures == 1 and session.facts == {}
    second = await say(container, "please try again")
    assert (second.decision, (await trace_of(container, second)).escalation_reason) == (
        Decision.HANDOFF,
        EscalationReason.REPEATED_TOOL_FAILURE,
    )


async def test_a_success_resets_the_failure_count(container: Container) -> None:
    inject(container, "shop", {"switch": "fail_next", "tool": "get_order", "code": "BACKEND_UNAVAILABLE", "times": 2})
    await say(container, f"Where is my order NS-20877? My phone is {PHONE}")
    ok = await say(container, "please try again")
    session = await container.sessions.load(T, C)
    assert ok.decision is Decision.ANSWER and session is not None and session.tool_failures == 0


async def test_a_non_retryable_failure_is_not_retried(container: Container) -> None:
    inject(container, "shop", {"switch": "unpublish", "tool": "get_order"})
    reply = await say(container, f"Where is my order NS-20877? My phone is {PHONE}")
    trace = await trace_of(container, reply)
    assert reply.decision is Decision.HANDOFF and trace.escalation_reason is EscalationReason.CAPABILITY_MISSING
    assert leaks(reply.text) == [] and all(t.tool != "get_order" for t in trace.tool_calls)


class Wrapped:
    """The shop with one call changed, to test how the brain treats odd answers."""

    def __init__(self, inner: Any, *, drop: str | None = None, no_tools: bool = False) -> None:
        self._inner, self._drop, self._no_tools = inner, drop, no_tools

    async def list_tools(self, tenant_id: str) -> list[ToolSpec]:
        if self._no_tools:
            raise UpstreamError("shop", "BACKEND_UNAVAILABLE", "down", retryable=True)
        return await self._inner.list_tools(tenant_id)  # type: ignore[no-any-return]

    async def call_tool(self, tenant_id: str, request: ToolCallRequest) -> ToolResult:
        result: ToolResult = await self._inner.call_tool(tenant_id, request)
        if self._drop and request.tool == "get_order" and result.status == "success":
            return result.model_copy(update={"data": {k: v for k, v in result.data.items() if k != self._drop}})
        return result


def with_shop(container: Container, **kw: Any) -> Orchestrator:
    return Orchestrator(
        clock=container.clock, tenants=container.tenants, sessions=container.sessions, traces=container.traces,
        cases=container.cases, capabilities=Wrapped(container.shop, **kw),
    )  # fmt: skip


async def test_an_incomplete_answer_from_the_shop_is_not_trusted(container: Container) -> None:
    reply = await with_shop(container, drop="order_total").handle_turn(
        T, C, f"Where is my order NS-20877? My phone is {PHONE}"
    )
    session = await container.sessions.load(T, C)
    assert reply.decision is Decision.CLARIFY and session is not None and session.facts == {}
    assert leaks(reply.text) == []


async def test_an_answer_without_the_owner_is_treated_as_not_theirs(container: Container) -> None:
    reply = await with_shop(container, drop="customer_id").handle_turn(
        T, C, f"Where is my order NS-20877? My phone is {PHONE}"
    )
    assert reply.decision is Decision.CLARIFY and leaks(reply.text) == []  # incomplete, so never shown


async def test_an_unavailable_tool_list_hands_off_without_data(container: Container) -> None:
    reply = await with_shop(container, no_tools=True).handle_turn(
        T, C, f"Where is my order NS-20877? My phone is {PHONE}"
    )
    assert reply.decision is Decision.HANDOFF and leaks(reply.text) == []
