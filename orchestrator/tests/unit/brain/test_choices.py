import pytest

from team_b.brain.choices import (
    conflicting_pair,
    describe_orders,
    label_of,
    mask_order_id,
    ordinal_choice,
    says_both,
    short_date,
)
from team_b.brain.lexicon import default_lexicon
from team_b.config import Settings
from team_b.domain.tenant import TenantRegistry
from team_b.domain.understanding import IntentCandidate, Locale

TENANT = TenantRegistry.from_dir(Settings().config_dir).get("shop_001")
LEX = default_lexicon()


def cands(*pairs: tuple[str, float]) -> list[IntentCandidate]:
    return [IntentCandidate(name=n, confidence=c) for n, c in pairs]


# ---- two intents that cannot both be meant ----


@pytest.mark.parametrize(
    ("pairs", "expected"),
    [
        ([("return_request", 0.8), ("exchange_request", 0.8)], ("return_request", "exchange_request")),
        ([("exchange_request", 0.8), ("return_request", 0.75)], ("exchange_request", "return_request")),  # order kept
        ([("exchange_request", 0.8), ("refund_request", 0.8)], ("exchange_request", "refund_request")),
        ([("return_request", 0.8), ("cancel_order", 0.8)], ("return_request", "cancel_order")),
        (
            [("order_status", 0.7), ("return_request", 0.8), ("exchange_request", 0.8)],
            ("return_request", "exchange_request"),
        ),
    ],
)
def test_a_conflicting_pair_that_cannot_be_told_apart(
    pairs: list[tuple[str, float]], expected: tuple[str, str]
) -> None:
    assert conflicting_pair(cands(*pairs), TENANT) == expected


@pytest.mark.parametrize(
    "pairs",
    [
        [("return_request", 0.9), ("exchange_request", 0.6)],  # one clearly stronger
        [("cancel_order", 0.8), ("refund_request", 0.8)],  # a sensible pair
        [("return_request", 0.8), ("refund_request", 0.8)],  # a sensible pair
        [("order_status", 0.8), ("cancel_order", 0.8)],  # a sensible pair
        [("refund_request", 0.8), ("complaint", 0.8)],
        [("return_request", 0.8)],
        [],
    ],
)
def test_no_question_for_sensible_pairs_or_a_clear_winner(pairs: list[tuple[str, float]]) -> None:
    assert conflicting_pair(cands(*pairs), TENANT) is None


def test_the_confidence_gap_limit_is_inclusive() -> None:
    assert conflicting_pair(cands(("return_request", 0.8), ("exchange_request", 0.7)), TENANT) is not None
    assert conflicting_pair(cands(("return_request", 0.8), ("exchange_request", 0.69)), TENANT) is None


@pytest.mark.parametrize(
    ("locale", "label"),
    [(Locale.EN, "return the item"), (Locale.AR, "ترجيع المنتج"), (Locale.ARABIZI, "return el montag")],
)
def test_labels_follow_the_customers_style(locale: Locale, label: str) -> None:
    assert label_of(TENANT, "return_request", locale) == label


def test_a_label_falls_back_to_english_then_to_the_intent_name() -> None:
    spec = TENANT.intents["cancel_order"].model_copy(update={"labels": {"en": "cancel it"}})
    custom = TENANT.model_copy(update={"intents": {**TENANT.intents, "cancel_order": spec}})
    assert label_of(custom, "cancel_order", Locale.AR) == "cancel it"
    assert label_of(TENANT, "complaint", Locale.EN) == "complaint"
    assert label_of(TENANT, "order_status", Locale.AR) == "order status"


# ---- reading "the second", "el awel", "الأول", "both" ----


@pytest.mark.parametrize(
    ("text", "index"),
    [
        ("the first", 0), ("first one", 0), ("1", 0), ("the first one please", 0), ("1st", 0),
        ("the second", 1), ("second", 1), ("2", 1), ("2nd one", 1),
        ("the third", 2), ("3", 2),
        ("الأول", 0), ("الاول", 0), ("اول واحد", 0), ("التاني", 1), ("الثاني", 1), ("التالت", 2), ("١", 0), ("٢", 1),
        ("el awel", 0), ("awel", 0), ("el tany", 1), ("tany", 1), ("el talet", 2),
    ],
)  # fmt: skip
def test_an_ordinal_answer(text: str, index: int) -> None:
    assert ordinal_choice(text, LEX, options=3) == index


@pytest.mark.parametrize(
    "text",
    [
        "hmm",
        "I want the first order to be refunded and the second to be cancelled please",  # too long to be just an answer
        "NS-20877",
        "first or second",  # two answers
        "",
    ],
)
def test_not_an_ordinal_answer(text: str) -> None:
    assert ordinal_choice(text, LEX, options=3) is None


def test_a_position_beyond_the_list_is_not_an_answer() -> None:
    assert ordinal_choice("the third", LEX, options=2) is None
    assert ordinal_choice("the second", LEX, options=2) == 1


@pytest.mark.parametrize("text", ["both", "both of them", "الاتنين", "الاثنين", "el etneen", "both please"])
def test_both(text: str) -> None:
    assert says_both(text, LEX)


@pytest.mark.parametrize("text", ["the first", "yes", "both orders were late and I want to talk about each one"])
def test_not_both(text: str) -> None:
    assert not says_both(text, LEX)


# ---- showing orders ----


@pytest.mark.parametrize(
    ("order_id", "masked"),
    [
        ("NS-20877", "NS-**877"),
        ("NS-123456", "NS-***456"),
        ("NS-123", "NS-123"),
        ("NS-12", "NS-12"),
        ("20877", "**877"),
    ],
)
def test_mask_order_id(order_id: str, masked: str) -> None:
    assert mask_order_id(order_id) == masked


@pytest.mark.parametrize(
    ("locale", "text"),
    [(Locale.EN, "20 Sep"), (Locale.AR, "20 سبتمبر"), (Locale.ARABIZI, "20 Sebtember")],
)
def test_short_date(locale: Locale, text: str) -> None:
    assert short_date("2026-09-20", locale) == text
    assert short_date("2026-09-20T10:00:00+00:00", locale) == text


def test_an_unreadable_date_is_shown_as_it_is() -> None:
    assert short_date("soon", Locale.EN) == "soon"
    assert short_date("", Locale.EN) == ""


def test_describe_orders_numbers_the_lines_and_masks_the_ids() -> None:
    orders = [
        {"order_id": "NS-20877", "placed_at": "2026-09-20"},
        {"order_id": "NS-20745", "placed_at": "2026-09-19"},
    ]
    assert describe_orders(orders, Locale.EN) == "1. NS-**877, 20 Sep\n2. NS-**745, 19 Sep"
