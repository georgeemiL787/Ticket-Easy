from datetime import UTC, datetime

import pytest

from team_b.brain.summarizer import LLMHistorySummarizer, TemplateHistorySummarizer, invented_facts
from team_b.domain.actions import ActionProposal
from team_b.domain.decision import EscalationReason
from team_b.domain.session import Message, SessionState
from team_b.domain.tenant import TenantConfig
from tests.fakes import FakeLLM

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def msg(text: str, role: str = "customer") -> Message:
    return Message(role=role, text=text, at=NOW)  # type: ignore[arg-type]


def make_session(**over: object) -> SessionState:
    base: dict[str, object] = {"tenant_id": "shop_001", "conversation_id": "c1", "created_at": NOW, "updated_at": NOW}
    return SessionState.model_validate({**base, **over})


@pytest.fixture
def tenant(container_with_tenants: object) -> TenantConfig:
    return container_with_tenants.tenants.get("shop_001")  # type: ignore[attr-defined,no-any-return]


async def test_template_lists_intents_orders_identity_actions_and_escalation(tenant: TenantConfig) -> None:
    session = make_session(
        intents_seen=["order_status", "return_request"],
        active_intent="refund_request",
        slots={"order_id": "NS-20512"},
        last_escalation=EscalationReason.POLICY_DENIED,
        history_summary="Orders mentioned: NS-20877.",
    )
    session.actions.append(
        ActionProposal(proposal_id="p1", tool="create_return", capability="create_return", idempotency_key="k")
    )
    text = await TemplateHistorySummarizer().summarize(session, [msg("my order NS 20790? No: NS-20790")], tenant)
    assert "order_status, return_request, refund_request" in text
    assert "NS-20877" in text and "NS-20790" in text and "NS-20512" in text
    assert "Identity: not verified." in text
    assert "create_return (proposed)" in text
    assert "Last escalation: policy_denied." in text


async def test_template_says_verified_identity_and_is_never_empty(tenant: TenantConfig) -> None:
    empty = await TemplateHistorySummarizer().summarize(make_session(), [], tenant)
    assert empty
    session = make_session(identity={"verified": True, "customer_id": "C-100"})
    assert "Identity: verified as C-100." in await TemplateHistorySummarizer().summarize(session, [], tenant)


async def test_template_carries_order_ids_forward_from_the_previous_summary(tenant: TenantConfig) -> None:
    first = await TemplateHistorySummarizer().summarize(make_session(), [msg("order NS-20877")], tenant)
    second = await TemplateHistorySummarizer().summarize(make_session(history_summary=first), [msg("thanks")], tenant)
    assert "NS-20877" in second


async def test_llm_summary_without_new_facts_is_used(tenant: TenantConfig) -> None:
    llm = FakeLLM({"summary": "The customer asked about order NS-20877, delivered 2026-09-25."})
    folded = [msg("where is NS-20877"), msg("delivered 2026-09-25", "agent")]
    text = await LLMHistorySummarizer(llm).summarize(make_session(), folded, tenant)
    assert text == "The customer asked about order NS-20877, delivered 2026-09-25."
    assert "NS-20877" in llm.calls[0]["user"]


@pytest.mark.parametrize(
    "invented",
    [
        "The customer paid 4500 EGP.",  # invented number
        "The customer asked about NS-20999.",  # invented order id
        "Delivery was on 2026-01-01.",  # invented date
    ],
)
async def test_llm_summary_with_an_invented_fact_is_rejected(tenant: TenantConfig, invented: str) -> None:
    llm = FakeLLM({"summary": invented})
    folded = [msg("where is NS-20877")]
    text = await LLMHistorySummarizer(llm).summarize(make_session(), folded, tenant)
    assert text == await TemplateHistorySummarizer().summarize(make_session(), folded, tenant)
    assert "4500" not in text and "NS-20999" not in text and "2026-01-01" not in text


@pytest.mark.parametrize("response", [RuntimeError("down"), {"other": "x"}, {"summary": "  "}])
async def test_llm_failure_or_empty_summary_falls_back_to_the_template(
    tenant: TenantConfig, response: dict[str, str] | Exception
) -> None:
    text = await LLMHistorySummarizer(FakeLLM(response)).summarize(make_session(), [msg("hello")], tenant)
    assert text.startswith("Intents seen:")


def test_invented_facts_reads_arabic_digits_and_ignores_known_numbers(tenant: TenantConfig) -> None:
    assert invented_facts("order NS-20877", "رقم NS-٢٠٨٧٧", tenant) == []
    assert invented_facts("total 1250 and 99", "refund 1250", tenant) == ["99"]
