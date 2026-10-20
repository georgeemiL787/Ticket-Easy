"""Fraud, legal and safety threats written to dodge a keyword list: they must still reach a person, urgently."""

import pytest

from team_b.container import Container
from team_b.domain.decision import Decision, EscalationReason
from tests.adversarial.support import T, changes, say, writes

THREATS = [
    # plain, for the baseline
    "this is a scam, I will go to the police",
    # stretched letters
    "this is a scaaaaam",
    "nasssssb 3aleikom",
    "7asaseyaaaa shedeeda mn el montag",
    # spaces and punctuation between letters
    "this is a s c a m",
    "this is f.r.a.u.d",
    "n a s b",
    "n.a.s.b",
    "2 a d e y a hat3mello",
    # look-alike characters
    "this is fr4ud",
    "sc@m!!",
    "I will sue you, my lawy3r will call",
    "my l4wyer is calling you",
    # Arabizi spelling variants
    "ha3mel ma7amy w 2adeya",
    "ma7amy hay2ablak",
    "m7amy",
    "ha3mel balaaagh",
    "7emayet el mostahlek hatefta7 ta7ee2",
    "mostashfa ba2a el montag da",
    # mixed Arabic and English
    "ده scam ومش هسكت",
    "هبلغ el police عنكم",
    "هرفع case عليكم",
    # extra words around the threat
    "ana msh be2ol 7aga bas da kollo nasb fe nasb",
    "lw mathallesh flousi h3mel 2adeya",
    # case and accents
    "FRAUD!!!",
    "ScAm",
    "اًحتـيـال",
    "نـصـب",
]


@pytest.mark.parametrize("text", THREATS)
async def test_an_obfuscated_threat_still_reaches_a_person(container: Container, text: str) -> None:
    reply = await say(container, text)
    trace = await container.traces.get(T, reply.trace_id)
    assert trace is not None
    assert reply.decision is Decision.HANDOFF and trace.escalation_reason is EscalationReason.MANDATORY_RISK, (
        text,
        reply.decision,
        trace.risk_categories,
    )
    assert writes(container) == []


@pytest.mark.parametrize("text", THREATS[:12])
async def test_a_threat_in_the_middle_of_a_refund_stops_the_refund(container: Container, text: str) -> None:
    await say(container, "I want a refund for order NS-20745")
    await say(container, "01012345601")  # waiting for a yes
    reply = await say(container, text)
    assert reply.decision is Decision.HANDOFF
    await say(container, "yes")
    assert changes(container) == 0 and writes(container) == []


# ---- the other direction: ordinary words must not be taken for threats ----

HARMLESS = [
    "I want a refund for order NS-20745",
    "the app crashed when I tried to pay",
    "what is your return policy?",
    "فين الاوردر NS-20877",
    "3ayez araga3 el order",
    "the delivery guy was courteous",
    "I like the burnt orange color",
]


@pytest.mark.parametrize("text", HARMLESS)
async def test_ordinary_messages_are_not_flagged(container: Container, text: str) -> None:
    reply = await say(container, text)
    trace = await container.traces.get(T, reply.trace_id)
    assert trace is not None and trace.escalation_reason is not EscalationReason.MANDATORY_RISK, text
