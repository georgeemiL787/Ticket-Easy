"""Safety screen stand-in: flagged and clean messages in every style, precision and recall, failure switch."""

import json
from pathlib import Path

import pytest

from team_b.adapters.standins.safety_screen import SafetyScreenStandin
from team_b.config import Settings
from team_b.container import Container, inject
from team_b.contracts.errors import UpstreamError

FIXTURES = Settings().fixtures_dir
T = "shop_001"

# (message, the category that must be found)
FLAGGED = [
    # English
    ("This is a scam, I did not make this payment", "fraud_suspected"),
    ("There is an unauthorized charge on my card", "fraud_suspected"),
    ("You charged without asking me", "fraud_suspected"),
    ("I will call my lawyer and sue you", "legal_regulatory"),
    ("I'm filing a police report about this", "legal_regulatory"),
    ("I had an allergic reaction to the cream", "medical_safety"),
    ("The charger caught fire last night", "medical_safety"),
    ("I want compensation for the delay", "compensation_demand"),
    ("I think my account got hacked", "identity_concern"),
    ("Someone else logged in to my account", "identity_concern"),
    # Egyptian Arabic
    ("ده نصب واحتيال", "fraud_suspected"),
    ("دي عملية مش انا اللي عملتها", "fraud_suspected"),
    ("بطاقتي اتسرقت واتخصم منها", "fraud_suspected"),
    ("هرفع قضية عليكم", "legal_regulatory"),
    ("هبلغ جهاز حماية المستهلك", "legal_regulatory"),
    ("عندي حساسية من المنتج ده", "medical_safety"),
    ("الشاحن ولع في ايدي", "medical_safety"),
    ("عايز تعويض عن التأخير", "compensation_demand"),
    ("المحامي بتاعي هيكلمكم", "legal_regulatory"),
    ("هاخد حقي من خلال المحكمة", "legal_regulatory"),
    ("لازم يتعمل بلاغ للنيابة", "legal_regulatory"),
    ("حسابي اتهكر", "identity_concern"),
    ("الحَسَاسِيَّة وصلتني المستشفى", "medical_safety"),  # with diacritics
    # Arabizi
    ("da nasb, ana msh ana elly 3amalt el 3amaleya", "fraud_suspected"),
    ("ha3mel 2adeya 3aleko", "legal_regulatory"),
    ("hakalem ma7ami", "legal_regulatory"),
    ("3andy 7asaseya mn el montag", "medical_safety"),
    ("el montag wele3 fel 2ouda", "medical_safety"),
    ("3ayez ta3weed 3ala el ta2khir", "compensation_demand"),
    ("7esaby et7aked", "identity_concern"),
    # stretched letters and mixed style
    ("this is a scaaaam!!!", "fraud_suspected"),
    ("7asaseyaaaa shedeeda", "medical_safety"),
    ("ana msh 3aref, it's a fraud 3ala el card", "fraud_suspected"),
    ("I demand compensation and a voucher", "compensation_demand"),  # the demand is outside the voucher phrase
    ("I want ta3weed, el order wasal 3ala el 7ala el 3ayeba", "compensation_demand"),
]

CLEAN = [
    # ordinary refund, return and order questions
    "I want to return my order, it is too small",
    "Please refund me for order 1002, it was delivered 3 days ago",
    "Can I exchange this shirt for a bigger size?",
    "Where is my order 1004?",
    "I'd like to cancel my order",
    "I want my money back for the item",
    "The delivery was late and I'm disappointed",
    "hello",
    "thanks!",
    # words that merely contain a risky word
    "Thank you, courtesy of the store",
    "I want a compensation voucher of 300 EGP",  # the shop's own voucher is not a demand
    "My order is late, can I get the late delivery voucher?",
    "عايز كوبون تعويض عن التأخير",
    "I love the burnt orange shirt",
    "The app crashed when I paid",
    # Egyptian Arabic
    "عايز أرجع الأوردر لأنه مقاس صغير",
    "فين الاوردر بتاعي؟",
    "ممكن استرجاع فلوسي؟",
    "عايز أغير عنوان التوصيل",
    "الاوردر اتأخر اوي",
    # Arabizi
    "3ayez araga3 el order",
    "feen el order beta3y?",
    "3ayez a3raf el refund hayakhod kam youm",
    "3ayez a8ayar el 3enwan",
    "el montag wasal el naharda",
]


@pytest.fixture
def screen() -> SafetyScreenStandin:
    return SafetyScreenStandin(FIXTURES)


async def classify(screen: SafetyScreenStandin, message: str):  # type: ignore[no-untyped-def]
    return await screen.classify_risk(T, message, request_id="r1")


def test_enough_test_messages() -> None:
    assert len(FLAGGED) + len(CLEAN) >= 30


@pytest.mark.parametrize(("message", "category"), FLAGGED)
async def test_flagged_message(screen: SafetyScreenStandin, message: str, category: str) -> None:
    result = await classify(screen, message)
    assert result.flagged, message
    assert category in result.categories, (message, result.categories)
    assert result.matched_terms and result.method == "keywords"


@pytest.mark.parametrize("message", CLEAN)
async def test_clean_message(screen: SafetyScreenStandin, message: str) -> None:
    result = await classify(screen, message)
    assert (result.flagged, result.categories, result.matched_terms) == (False, (), ()), message


async def test_precision_and_recall_on_the_test_messages(screen: SafetyScreenStandin) -> None:
    true_pos = sum([(await classify(screen, m)).flagged for m, _ in FLAGGED])
    false_pos = sum([(await classify(screen, m)).flagged for m in CLEAN])
    assert true_pos == len(FLAGGED) and false_pos == 0  # precision 100%, recall 100%


async def test_several_categories_are_all_reported(screen: SafetyScreenStandin) -> None:
    result = await classify(screen, "This is fraud and I will sue you, I want compensation")
    assert set(result.categories) == {"fraud_suspected", "legal_regulatory", "compensation_demand"}
    assert {"fraud", "sue you", "compensation"} <= set(result.matched_terms)


async def test_matching_ignores_case_and_punctuation(screen: SafetyScreenStandin) -> None:
    assert (await classify(screen, "FRAUD!!!")).categories == ("fraud_suspected",)
    assert (await classify(screen, "...Lawyer?")).categories == ("legal_regulatory",)


async def test_empty_message_is_clear(screen: SafetyScreenStandin) -> None:
    assert not (await classify(screen, "")).flagged
    assert not (await classify(screen, "   ")).flagged


async def test_a_non_mandatory_category_is_listed_but_does_not_flag(tmp_path: Path) -> None:
    folder = tmp_path / T
    folder.mkdir()
    risk = {"categories": {"mild": {"mandatory_escalation": False, "en": ["grumpy"]}, "bad": {"en": ["fraud"]}}}
    (folder / "risk.json").write_text(json.dumps(risk), encoding="utf-8")
    screen = SafetyScreenStandin(tmp_path)
    mild = await classify(screen, "I am grumpy")
    assert (mild.flagged, mild.categories) == (False, ("mild",))
    both = await classify(screen, "grumpy about fraud")
    assert (both.flagged, both.categories) == (True, ("mild", "bad"))


async def test_unknown_tenant_and_unreadable_file_raise_upstream_error(tmp_path: Path) -> None:
    with pytest.raises(UpstreamError) as caught:
        await SafetyScreenStandin(tmp_path).classify_risk("shop_001", "hi", request_id="r")
    assert caught.value.code == "TENANT_NOT_FOUND"
    (tmp_path / T).mkdir()
    (tmp_path / T / "risk.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(UpstreamError) as broken:
        await SafetyScreenStandin(tmp_path).classify_risk(T, "hi", request_id="r")
    assert broken.value.code == "RISK_INVALID"


async def test_failure_switch(screen: SafetyScreenStandin) -> None:
    screen.fail_next(2)
    for _ in range(2):
        with pytest.raises(UpstreamError) as caught:
            await classify(screen, "hello")
        assert caught.value.retryable
    assert not (await classify(screen, "hello")).flagged
    with pytest.raises(ValueError, match="at least 1"):
        screen.fail_next(0)


async def test_inject_and_container_plug(container: Container) -> None:
    assert container.safety_screen is not None
    inject(container, "safety_screen", {"switch": "fail_next", "times": 2})
    for _ in range(2):  # through the EvidenceProvider plug
        with pytest.raises(UpstreamError):
            await container.evidence.classify_risk(T, "fraud", request_id="r")
    inject(container, "safety_screen", {"switch": "fail_next"})
    inject(container, "safety_screen", {"switch": "reset"})
    assert (await container.evidence.classify_risk(T, "fraud", request_id="r")).flagged
    with pytest.raises(ValueError, match="unknown safety_screen switch"):
        inject(container, "safety_screen", {"switch": "explode"})
    with pytest.raises(ValueError, match="only the operation"):
        inject(container, "safety_screen", {"switch": "fail_next", "operation": "other"})
