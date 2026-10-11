import pytest
from pydantic import ValidationError

from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.reply import AgentReply
from team_b.domain.understanding import IntentCandidate, Language, Locale, NLUResult


def test_language_and_locale_values() -> None:
    assert {x.value for x in Language} == {"en", "ar", "mixed", "arabizi"}
    assert {x.value for x in Locale} == {"en", "ar", "arabizi"}  # no mixed locale: mixed customers get Arabic


def test_decision_values() -> None:
    assert {d.value for d in Decision} == {
        "answer",
        "clarify",
        "verify_identity",
        "confirm",
        "execute",
        "refuse",
        "handoff",
    }


def test_there_are_fourteen_escalation_reasons() -> None:
    assert {r.value for r in EscalationReason} == {
        "customer_request",
        "mandatory_risk",
        "policy_denied",
        "approval_required",
        "repeated_tool_failure",
        "unverified_result",
        "no_evidence",
        "dependency_unavailable",
        "low_confidence",
        "identity_failed",
        "ownership_mismatch",
        "high_frustration",
        "capability_missing",
        "unsupported",
    }


def test_nlu_result_defaults() -> None:
    r = NLUResult(language=Language.ARABIZI, language_confidence=0.9)
    assert r.method == "rules" and r.frustration == "low" and not r.wants_human
    assert r.affirmation is None and r.intents == () and r.entities == {} and r.safety_flags == ()


def test_nlu_result_validates_ranges_and_values() -> None:
    with pytest.raises(ValidationError):
        NLUResult(language=Language.EN, language_confidence=1.5)
    with pytest.raises(ValidationError):
        IntentCandidate(name="refund", confidence=-0.1)
    with pytest.raises(ValidationError):
        NLUResult.model_validate({"language": "en", "language_confidence": 0.5, "frustration": "furious"})
    with pytest.raises(ValidationError):
        NLUResult.model_validate({"language": "en", "language_confidence": 0.5, "method": "magic"})
    with pytest.raises(ValidationError):
        NLUResult.model_validate({"language": "klingon", "language_confidence": 0.5})
    with pytest.raises(ValidationError):
        NLUResult.model_validate({"language": "en", "language_confidence": 0.5, "affirmation": "maybe"})


def test_nlu_result_full() -> None:
    r = NLUResult(
        language=Language.ARABIZI,
        language_confidence=0.95,
        intents=(IntentCandidate(name="order_status", confidence=0.8),),
        entities={"order_id": "NS-20877"},
        affirmation="yes",
        wants_human=True,
        frustration="high",
        safety_flags=("legal",),
        method="llm",
    )
    assert r.intents[0].name == "order_status" and r.entities["order_id"] == "NS-20877"
    with pytest.raises(ValidationError):
        r.wants_human = False  # type: ignore[misc]


def test_agent_reply() -> None:
    reply = AgentReply(
        request_id="r1",
        tenant_id="shop_001",
        conversation_id="c1",
        text="Done",
        locale=Locale.EN,
        decision=Decision.ANSWER,
        trace_id="t1",
    )
    assert reply.citations == () and reply.handoff_case_id is None and reply.awaiting is None
    with pytest.raises(ValidationError):
        AgentReply(
            request_id="r1",
            tenant_id="shop_001",
            conversation_id="c1",
            text="x",
            locale="mixed",  # type: ignore[arg-type]
            decision=Decision.ANSWER,
            trace_id="t1",
        )
