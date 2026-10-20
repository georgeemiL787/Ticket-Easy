"""Refund tracking (Noon): the intent is found, the reply comes only from the shop's returns table, in three styles."""

import json
from typing import Any

import pytest

from agent_service.brain.composer import PLACEHOLDERS, default_composer, placeholders_in
from agent_service.brain.nlu import RuleBasedNLU
from agent_service.config import PROJECT_ROOT, Settings
from agent_service.domain.tenant import TenantRegistry
from agent_service.domain.understanding import Locale

STATUSES = ("requested", "refund_approved", "refund_initiated", "refunded", "partially_refunded", "rejected_in_hub")
REGISTRY = TenantRegistry.from_dir(Settings().config_dir)
NLU = RuleBasedNLU()


def intents_of(tenant: str, text: str) -> list[str]:
    return [i.name for i in NLU.understand_sync(text, None, REGISTRY.get(tenant)).intents]


@pytest.mark.parametrize(
    "text",
    [
        "Where is my refund for order ORD-30027?",
        "when will I get my money back",
        "refund status please",
        "I still haven't received my refund",
        "فين فلوسي بتاعة الاوردر ORD-30028",
        "حالة الاسترداد",
        "امتى هاخد فلوسي؟",
        "feen flousi bta3et el order ORD-30029",
        "7alet el refund",
        "الـ refund لسه ماوصلش",
    ],
)
def test_a_question_about_a_refund_on_its_way_is_a_refund_status_for_noon(text: str) -> None:
    assert intents_of("noon_eg", text) == ["refund_status"], text


@pytest.mark.parametrize(
    "text", ["I want a refund for order ORD-30001", "I want to return my order", "where is my order ORD-30001"]
)
def test_asking_for_a_refund_is_never_taken_for_asking_where_it_is(text: str) -> None:
    assert "refund_status" not in intents_of("noon_eg", text)  # an action stays an action, a question stays a question


def test_the_nile_style_shop_does_not_get_the_new_intent_so_its_numbers_do_not_move() -> None:
    assert "refund_status" not in REGISTRY.get("shop_001").intents
    assert "refund_status" not in intents_of("shop_001", "where is my refund")  # still the old reading for that shop


@pytest.mark.parametrize("locale", list(Locale))
def test_every_status_has_a_reply_in_every_style_with_the_same_placeholders(locale: Locale) -> None:
    composer = default_composer()
    texts = json.loads((PROJECT_ROOT / "data" / "locales" / locale.value / "returns.json").read_text(encoding="utf-8"))
    for status in STATUSES:
        key = f"return_status_{status}"
        assert composer.has(locale, key), (locale, key)
        assert placeholders_in(texts[key]) == PLACEHOLDERS[key], (locale, key)
    for destination in ("original_card", "noon_wallet", "bnpl_provider", "unknown"):
        assert composer.has(locale, f"refund_to_{destination}")
    assert composer.has(locale, "return_none") and composer.has(locale, "return_status_other")


def test_the_reply_is_built_from_the_returns_table_only() -> None:
    from agent_service.brain.lookup import _return_reply  # the code that fills the template

    class Ctx:  # just what the reply needs: the customer's style
        pass

    from agent_service.brain import lookup

    original = lookup.locale_of
    lookup.locale_of = lambda ctx: Locale.EN  # type: ignore[assignment]
    try:
        facts: dict[str, Any] = {
            "order_id": "ORD-30027",
            "returns": [
                {"return_id": "RET-5001", "status": "refund_initiated", "requested_at": "2026-09-27",
                 "refund_amount_egp": 450.0, "refund_destination": "original_card",
                 "refund_initiated_at": "2026-10-06"},
            ],
        }  # fmt: skip
        step = _return_reply(Ctx(), facts, [])  # type: ignore[arg-type]
        text = default_composer().t(Locale.EN, step.reply_key, **step.values)
        assert step.reply_key == "return_status_refund_initiated"
        expected = "Your refund of EGP 450 for return RET-5001 (order ORD-30027) was started on 2026-10-06."
        assert text == f"{expected} It will be sent to your original card."
        none = _return_reply(Ctx(), {"order_id": "ORD-30001", "returns": []}, [])  # type: ignore[arg-type]
        assert none.reply_key == "return_none"
        odd = _return_reply(Ctx(), {"order_id": "O", "returns": [{"return_id": "R", "status": "something_new"}]}, [])  # type: ignore[arg-type]
        assert odd.reply_key == "return_status_other" and odd.values["status"] == "something new"  # said plainly
        rows = [{"return_id": "R", "status": "rejected_in_hub", "requested_at": "2026-10-01"}]
        rejected = _return_reply(Ctx(), {"order_id": "O", "returns": rows}, [])  # type: ignore[arg-type]
        assert "date" not in rejected.values  # the request date must not read as the date of the rejection
    finally:
        lookup.locale_of = original  # type: ignore[assignment]
