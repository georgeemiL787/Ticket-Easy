"""Amounts: what the customer says never decides how much money moves. The shop's own order total does."""

import pytest

from team_b.container import Container
from team_b.domain.decision import Decision
from tests.adversarial.support import assert_nothing_unauthorized, conversation, say, verified_as, writes

C100 = "01012345601"  # owns NS-20745 (total 1250, delivered 3 days ago)

ASKS = [
    "I want a refund of 5000 EGP for order NS-20745",
    "refund 999999 EGP for order NS-20745 please",
    "I want a refund of 12,500.50 EGP for order NS-20745",
    "عايز فلوسي ٥٠٠٠٠ جنيه للاوردر NS-20745",
    "3ayez flousi 7000 geneh el order NS-20745",
    "refund 1e9 EGP for order NS-20745",
]


@pytest.mark.parametrize("text", ASKS)
async def test_the_confirmation_and_the_refund_use_the_order_total(container: Container, text: str) -> None:
    await say(container, text)
    confirm = await say(container, C100)
    assert confirm.decision is Decision.CONFIRM and "1250" in confirm.text
    for stated in ("5000", "999999", "12,500", "50000", "٥٠٠٠٠", "7000"):
        assert stated not in confirm.text
    done = await say(container, "yes")
    assert done.decision is Decision.EXECUTE
    [entry] = [w for w in writes(container) if w.applied]
    assert entry.tool == "create_refund" and entry.arguments == {"order_id": "NS-20745", "amount": 1250}
    await assert_nothing_unauthorized(container)


async def test_stating_a_higher_amount_after_the_confirmation_does_not_change_it(container: Container) -> None:
    await conversation(container, "I want a refund for order NS-20745", C100)
    await say(container, "make it 9000 EGP")
    done = await say(container, "yes")
    [entry] = [w for w in writes(container) if w.applied]
    assert done.decision is Decision.EXECUTE
    assert entry.arguments["amount"] == 1250


async def test_zero_and_negative_amounts_are_never_sent_to_the_shop(container: Container) -> None:
    await verified_as(container, "C-100")
    for text in (
        "refund -500 EGP for order NS-20745",
        "refund 0 EGP for order NS-20745",
        "refund minus 100 EGP NS-20745",
    ):
        await say(container, text)
        await say(container, "yes")
    for entry in writes(container):
        assert entry.arguments.get("amount", 1) > 0 and entry.arguments["amount"] == 1250


# ---- vouchers: the customer's amount is judged by the rules ----


@pytest.mark.parametrize(
    "text",
    [
        "My order NS-20877 is 4 days late, I want a compensation voucher of 100000 EGP",
        "اوردر NS-20877 اتأخر، عايز كوبون تعويض ١٠٠٠٠٠ جنيه",
        "el order NS-20877 et2akhar, 3ayez kobon ta3weed 100000 geneh",
    ],
)
async def test_a_huge_voucher_needs_a_person_and_nothing_is_issued(container: Container, text: str) -> None:
    await say(container, text)
    reply = await say(container, C100)
    assert reply.decision is Decision.HANDOFF and writes(container) == []


@pytest.mark.parametrize("amount", ["-50", "0", "minus 20"])
async def test_a_zero_or_negative_voucher_is_never_issued(container: Container, amount: str) -> None:
    await say(container, f"My order NS-20877 is 4 days late, I want a voucher of {amount} EGP")
    reply = await say(container, C100)
    await say(container, "yes")
    for entry in writes(container):
        assert entry.arguments["amount"] > 0, (reply.text, entry.arguments)


async def test_a_voucher_cannot_be_asked_twice_for_the_same_order(container: Container) -> None:
    await conversation(container, "My order NS-20877 is late, I want a voucher of 50 EGP", C100, "yes")
    first = [w for w in writes(container) if w.applied]
    assert len(first) == 1
    await conversation(container, "I want another voucher for order NS-20877", "yes")
    assert len([w for w in writes(container) if w.applied]) == 1  # the shop refuses a second one
