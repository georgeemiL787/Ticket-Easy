"""Rule-based understanding: intents (all four styles), details, yes/no, people, frustration, negation, multi-intent."""

from datetime import UTC, datetime

import pytest

from team_b.brain.lexicon import default_lexicon
from team_b.brain.nlu import RuleBasedNLU, extract_amount, extract_order_ids, extract_phone
from team_b.brain.text import normalize
from team_b.config import Settings
from team_b.domain.session import SessionState
from team_b.domain.tenant import TenantConfig, TenantRegistry
from team_b.domain.understanding import Language

FIXED_NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def tenant() -> TenantConfig:
    return TenantRegistry.from_dir(Settings().config_dir).get("shop_001")


@pytest.fixture(scope="module")
def nlu() -> RuleBasedNLU:
    return RuleBasedNLU()


def intents_of(nlu: RuleBasedNLU, tenant: TenantConfig, text: str) -> list[str]:
    return [i.name for i in nlu.understand_sync(text, None, tenant).intents]


# text -> intents in order of appearance
EN = [
    ("Where is my order NS-20877?", ["order_status"]),
    ("I want to return my order NS-20512", ["return_request"]),
    ("I want a refund for order NS-20512", ["refund_request"]),
    ("Please cancel my order", ["cancel_order"]),
    ("I need to change the delivery address", ["change_address"]),
    ("I want to exchange it for a bigger size", ["exchange_request"]),
    ("My order is late, do I get a voucher?", ["voucher_request"]),
    ("I want to file a complaint about my delivery", ["complaint"]),
    ("I want to talk to a human", ["human_request"]),
    ("What is your return policy?", ["policy_question"]),
    ("How many days do I have to return an item?", ["policy_question"]),
    ("actually, how much does shipping cost?", ["policy_question"]),
    ("Hello", ["greeting"]),
    ("thanks, bye", ["greeting"]),
    ("Please book me a flight to Dubai", []),
    ("Do you offer a five year warranty on electronics?", ["policy_question"]),
]
AR = [
    ("الاوردر بتاعي وصل فين؟", ["order_status"]),
    ("عايز ارجع المنتج لان المقاس مش مظبوط", ["return_request"]),
    ("عايز فلوسي ترجع", ["refund_request"]),
    ("الغي الاوردر لو سمحت", ["cancel_order"]),
    ("عايز اغير عنوان التوصيل", ["change_address"]),
    ("عايز ابدل المقاس", ["exchange_request"]),
    ("الاوردر اتاخر وعايز كوبون", ["voucher_request"]),
    ("عايز اقدم شكوى", ["complaint"]),
    ("عايز اكلم حد من خدمة العملاء", ["human_request"]),
    ("ممكن ارجع المنتج بعد كام يوم من الاستلام؟", ["policy_question"]),
    ("السلام عليكم", ["greeting"]),
    ("عايز ارجع فلوسي للاوردر ده", ["refund_request"]),  # the longer phrase beats "ارجع"
    ("عايز الغي والاوردر ده", ["cancel_order"]),
    ("عايز ارجع للاوردر NS-20790", ["return_request"]),  # an attached prefix is fine
    ("عايز اطلب طيارة", []),
]
MIXED = [
    ("ممكن اعمل return للمنتج بعد كام يوم من الـ delivery؟", ["policy_question"]),
    ("عايز refund للاوردر بتاعي", ["refund_request"]),
    ("ازاي اعمل cancel للاوردر", ["cancel_order"]),
    ("الـ order وصل فين", ["order_status"]),
    ("عايز اغير الـ address بتاع الاوردر", ["change_address"]),
    ("عايز اعمل exchange للـ size", ["exchange_request"]),
    ("عايز voucher عشان التأخير", ["voucher_request"]),
    ("عايز اعمل complaint على المندوب", ["complaint"]),
    ("عايز اكلم agent", ["human_request"]),
    ("I want to return المنتج ده لان المقاس غلط", ["return_request"]),
]
ARABIZI = [
    ("emta a2dar araga3 el montag?", ["policy_question"]),
    ("el order bta3i et2akhar, 3ayez a3raf feen", ["order_status"]),
    ("3ayez araga3 el order 3shan el ma2as msh mazboot", ["return_request"]),
    ("3ayez flousi el order mesh 3agebny", ["refund_request", "complaint"]),
    ("3ayez alghy el order", ["cancel_order"]),
    ("3ayez a8ayar el 3enwan", ["change_address"]),
    ("3ayza abdel el ma2as", ["exchange_request"]),
    ("el order et2akhar, fi kobon ta3weed?", ["voucher_request"]),
    ("3ayez akalem mowazaf", ["human_request"]),
    ("ahlan, ezayak", ["greeting"]),
    ("feen el order NS-20877", ["order_status"]),
    ("3ayez a3mel return", ["return_request"]),
    ("el shahn bekam lel eskandareya?", ["policy_question"]),
]
ALL = EN + AR + MIXED + ARABIZI


@pytest.mark.parametrize(("text", "expected"), ALL, ids=[t[:40] for t, _ in ALL])
def test_intents(nlu: RuleBasedNLU, tenant: TenantConfig, text: str, expected: list[str]) -> None:
    assert intents_of(nlu, tenant, text) == expected


# ---- several intents in one message ----


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Where is my order NS-20960 and cancel it", ["order_status", "cancel_order"]),
        ("cancel my order and give me a refund", ["cancel_order", "refund_request"]),
        ("I want a refund and I want to file a complaint", ["refund_request", "complaint"]),
        ("الاوردر وصل فين وعايز الغيه", ["order_status", "cancel_order"]),
        ("عايز اغير العنوان وعايز فلوسي", ["change_address", "refund_request"]),
        ("feen el order w 3ayez alghy", ["order_status", "cancel_order"]),
        (
            "feen el order NS-20955, 3ayez a8ayar el 3enwan, w 3ayez flousi el order NS-20701",
            ["order_status", "change_address", "refund_request"],
        ),
        ("hello, where is my order?", ["order_status"]),  # a greeting is dropped when there is a real request
    ],
)
def test_several_intents_come_in_the_order_they_appear(
    nlu: RuleBasedNLU, tenant: TenantConfig, text: str, expected: list[str]
) -> None:
    assert intents_of(nlu, tenant, text) == expected


# ---- negation ----


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I don't want to return it", []),
        ("I do not want a refund", []),
        ("don't cancel my order", []),
        ("I don't want to return it, I want a refund", ["refund_request"]),
        ("مش عايز ارجع المنتج", []),
        ("مش عايز ارجع المنتج عايز فلوسي", ["refund_request"]),
        ("msh 3ayez araga3", []),
        ("msh 3ayez araga3 3ayez flousi", ["refund_request"]),
        ("no, I want to return it", ["return_request"]),  # a comma ends the negation
        ("I never asked to talk to a human", []),
        ("I am not happy, I want a refund", ["refund_request"]),
    ],
)
def test_negation_cancels_an_action_keyword(
    nlu: RuleBasedNLU, tenant: TenantConfig, text: str, expected: list[str]
) -> None:
    assert intents_of(nlu, tenant, text) == expected


def test_an_intent_outside_the_tenant_catalog_is_ignored(nlu: RuleBasedNLU, tenant: TenantConfig) -> None:
    small = tenant.model_copy(update={"intents": {"order_status": tenant.intents["order_status"]}})
    assert intents_of(nlu, small, "I want a refund, where is my order NS-20877") == ["order_status"]


def test_longer_keywords_beat_shorter_ones(nlu: RuleBasedNLU, tenant: TenantConfig) -> None:
    assert intents_of(nlu, tenant, "what is your return policy") == ["policy_question"]  # not return_request
    assert intents_of(nlu, tenant, "ارجع فلوسي") == ["refund_request"]  # not return_request


def test_naming_an_order_makes_a_timing_question_an_action(nlu: RuleBasedNLU, tenant: TenantConfig) -> None:
    assert intents_of(nlu, tenant, "can I return order NS-20790 after 10 days") == ["return_request"]


def test_confidence_rises_with_a_want_marker(nlu: RuleBasedNLU, tenant: TenantConfig) -> None:
    plain = nlu.understand_sync("cancel the order", None, tenant).intents[0].confidence
    wanted = nlu.understand_sync("I want to cancel the order", None, tenant).intents[0].confidence
    assert 0.5 < plain < wanted <= 0.95


# ---- details ----


@pytest.mark.parametrize(
    "text",
    [
        "NS-20877",
        "ns-20877",
        "NS 20877",
        "ns20877",
        "NS_20877",
        "order NS-20877 please",
        "NS-٢٠٨٧٧",
        "رقم الاوردر NS 20877",
    ],
)
def test_order_id_spellings(nlu: RuleBasedNLU, tenant: TenantConfig, text: str) -> None:
    assert nlu.understand_sync(text, None, tenant).entities["order_id"] == "NS-20877"


@pytest.mark.parametrize("text", ["order number 20877", "الاوردر رقم 20877", "talab 20877"])
def test_a_bare_number_after_the_word_order(nlu: RuleBasedNLU, tenant: TenantConfig, text: str) -> None:
    assert nlu.understand_sync(text, None, tenant).entities["order_id"] == "NS-20877"


@pytest.mark.parametrize("text", ["NS-12", "NS-1234567", "my number is 20877", "XY-20877", "call 01012345601"])
def test_things_that_are_not_order_ids(nlu: RuleBasedNLU, tenant: TenantConfig, text: str) -> None:
    assert "order_id" not in nlu.understand_sync(text, None, tenant).entities


def test_several_order_ids_are_all_kept(nlu: RuleBasedNLU, tenant: TenantConfig) -> None:
    entities = nlu.understand_sync("NS-20955 and ns 20701, and NS-20955 again", None, tenant).entities
    assert entities["order_id"] == "NS-20955" and entities["order_ids"] == "NS-20955,NS-20701"


@pytest.mark.parametrize(
    "text",
    [
        "01012345601",
        "010 1234 5601",
        "010-1234-5601",
        "+201012345601",
        "+20 10 1234 5601",
        "+20 1012345601",
        "0020 10 1234 5601",
        "201012345601",
        "٠١٠١٢٣٤٥٦٠١",
        "my phone is 01012345601.",
        "1012345601",
    ],
)
def test_egyptian_phone_numbers_are_normalized(nlu: RuleBasedNLU, tenant: TenantConfig, text: str) -> None:
    assert nlu.understand_sync(text, None, tenant).entities["phone"] == "01012345601"


@pytest.mark.parametrize("text", ["01312345601", "0101234560", "NS-20877", "12345", "+44 7911 123456"])
def test_things_that_are_not_phones(nlu: RuleBasedNLU, tenant: TenantConfig, text: str) -> None:
    assert "phone" not in nlu.understand_sync(text, None, tenant).entities


def test_an_order_id_is_not_read_as_part_of_a_phone(nlu: RuleBasedNLU, tenant: TenantConfig) -> None:
    entities = nlu.understand_sync("NS-20877 01012345601", None, tenant).entities
    assert entities == {"order_id": "NS-20877", "phone": "01012345601"}


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("refund 300 EGP", "300"),
        ("EGP 450", "450"),
        ("500 LE", "500"),
        ("3,000 جنيه", "3000"),
        ("هاتلي ٢٥٠ جنيه", "250"),
        ("100 geneh", "100"),
        ("12.5 egp", "12.5"),
    ],
)
def test_amounts(nlu: RuleBasedNLU, tenant: TenantConfig, text: str, amount: str) -> None:
    assert nlu.understand_sync(text, None, tenant).entities["amount"] == amount


@pytest.mark.parametrize("text", ["le 5 Corniche El Nil", "I waited 4 days", "5 items"])
def test_numbers_without_a_currency_are_not_amounts(nlu: RuleBasedNLU, tenant: TenantConfig, text: str) -> None:
    assert "amount" not in nlu.understand_sync(text, None, tenant).entities


def test_all_details_in_one_message(nlu: RuleBasedNLU, tenant: TenantConfig) -> None:
    text = "refund NS-20512 please, 800 EGP, my number +20 10 1234 5601"
    assert nlu.understand_sync(text, None, tenant).entities == {
        "order_id": "NS-20512",
        "phone": "01012345601",
        "amount": "800",
    }


def test_extractors_work_on_normalized_text_directly(tenant: TenantConfig) -> None:
    assert extract_order_ids(normalize("ns 20877"), tenant) == [(0, 8, "NS-20877")]
    assert extract_phone(normalize("+20 10 1234 5601")) == "01012345601"
    assert extract_amount(normalize("3,000 EGP")) == "3000"


# ---- yes and no ----


@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "Yes!",
        "yeah",
        "ok",
        "sure",
        "go ahead",
        "yes please",
        "ايوه",
        "ايوة",
        "تمام",
        "اه",
        "ah",
        "ah tamam",
        "aywa",
    ],
)
def test_a_message_that_is_a_yes(nlu: RuleBasedNLU, tenant: TenantConfig, text: str) -> None:
    assert nlu.understand_sync(text, None, tenant).affirmation == "yes"


@pytest.mark.parametrize(
    "text", ["no", "No thanks", "nope", "cancel that", "لا", "مش عايز", "la2", "msh 3ayez", "balash"]
)
def test_a_message_that_is_a_no(nlu: RuleBasedNLU, tenant: TenantConfig, text: str) -> None:
    assert nlu.understand_sync(text, None, tenant).affirmation == "no"


@pytest.mark.parametrize(
    "text",
    [
        "yes I want to return my order NS-20877",  # a yes inside a request
        "ok where is my order",
        "ايوه عايز ارجع الاوردر",
        "tamam 3ayez a8ayar el 3enwan",
        "yes no",  # both
        "hello",
        "",
    ],
)
def test_a_yes_inside_a_longer_message_is_not_an_affirmation(
    nlu: RuleBasedNLU, tenant: TenantConfig, text: str
) -> None:
    assert nlu.understand_sync(text, None, tenant).affirmation is None


# ---- a person ----


@pytest.mark.parametrize(
    "text",
    [
        "I want to talk to a human",
        "can I speak to a real person",
        "agent please",
        "human",
        "عايز اكلم حد من خدمة العملاء",
        "موظف لو سمحت",
        "عايز اكلم agent",
        "3ayez akalem mowazaf",
        "mowazaf",
    ],
)
def test_wants_a_person(nlu: RuleBasedNLU, tenant: TenantConfig, text: str) -> None:
    result = nlu.understand_sync(text, None, tenant)
    assert result.wants_human and "human_request" in [i.name for i in result.intents]


@pytest.mark.parametrize(
    "text",
    [
        "Where is my order?",
        "the agent was rude",
        "I contacted support last week about my order",
        "I don't want a human",
    ],
)
def test_does_not_want_a_person(nlu: RuleBasedNLU, tenant: TenantConfig, text: str) -> None:
    assert not nlu.understand_sync(text, None, tenant).wants_human


# ---- frustration ----


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("Where is my order?", "low"),
        ("it is late", "low"),
        ("I already asked, this is useless, why is it so slow?", "medium"),
        ("This is ridiculous!!! Worst service ever, I am so angry, stop asking me questions!!!", "high"),
        ("WHERE IS MY ORDER", "low"),  # caps alone is a hint, not enough
        ("WHERE IS MY ORDER!!!", "medium"),
        ("محدش بيرد عليا وزهقت", "medium"),
        ("اسوأ خدمة، مش معقول، قرفتوني!!!", "high"),
        ("mafeesh 7ad byrod w zah2t", "medium"),
        ("msh ma3ool, afsha khedma!!!", "high"),
        ("pleeeease help", "low"),
        ("ok thanks", "low"),
    ],
)
def test_frustration(nlu: RuleBasedNLU, tenant: TenantConfig, text: str, level: str) -> None:
    assert nlu.understand_sync(text, None, tenant).frustration == level


def test_an_order_id_in_capitals_is_not_shouting(nlu: RuleBasedNLU, tenant: TenantConfig) -> None:
    assert nlu.understand_sync("where is NS-20877 ?", None, tenant).frustration == "low"


# ---- language and the rest of the result ----


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Where is my order?", Language.EN),
        ("الاوردر بتاعي وصل فين؟", Language.AR),
        ("عايز اعمل refund للاوردر بتاعي", Language.MIXED),
        ("3ayez a3raf feen el order", Language.ARABIZI),
    ],
)
def test_the_language_comes_with_the_result(
    nlu: RuleBasedNLU, tenant: TenantConfig, text: str, language: Language
) -> None:
    result = nlu.understand_sync(text, None, tenant)
    assert result.language is language and result.language_confidence > 0.5 and result.method == "rules"


def test_a_message_with_no_language_keeps_the_conversation_language(nlu: RuleBasedNLU, tenant: TenantConfig) -> None:
    session = SessionState(
        tenant_id="shop_001", conversation_id="c", language=Language.ARABIZI, created_at=FIXED_NOW, updated_at=FIXED_NOW
    )
    result = nlu.understand_sync("01012345601", session, tenant)
    assert result.language is Language.ARABIZI and result.language_confidence == 0.0
    assert nlu.understand_sync("01012345601", None, tenant).language is Language.AR  # the tenant default


async def test_the_async_interface_gives_the_same_answer(nlu: RuleBasedNLU, tenant: TenantConfig) -> None:
    text = "I want a refund for order NS-20512"
    assert await nlu.understand(text, None, tenant) == nlu.understand_sync(text, None, tenant)


def test_every_catalog_intent_has_keywords_in_every_style(tenant: TenantConfig) -> None:
    lexicon = default_lexicon()
    for name in tenant.intents:
        words = lexicon.intents[name]
        assert words.en and words.ar and words.arabizi and words.mixed, name


def test_lexicon_sizes_are_reasonable() -> None:
    lexicon = default_lexicon()
    assert min(w.size() for w in lexicon.intents.values()) >= 25
    assert lexicon.yes.size() >= 20 and lexicon.no.size() >= 20 and lexicon.negation.size() >= 15
