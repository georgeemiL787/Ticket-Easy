"""Helpers for questions where the customer picks from a short list: "return or exchange?", "which of your orders?".

Pure functions: finding two intents that cannot both be wanted, reading an answer like "the second" / "el awel" /
"الأول" / "both", and showing orders with a masked id and a date.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date

from team_b.brain.lexicon import Lexicon
from team_b.brain.text import find_spans, normalize
from team_b.domain.tenant import TenantConfig
from team_b.domain.understanding import IntentCandidate, Locale

CLOSE_CONFIDENCE = 0.1  # two intents this close in confidence cannot be told apart
MAX_ORDER_CHOICES = 3
MAX_ANSWER_WORDS = 6  # a longer message is not just an answer to the list
_WORD = re.compile(r"\w+")
_EN_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_AZ_MONTHS = (
    "Yanayer", "Febrayer", "Mares", "Abreel", "Mayo", "Yonyo",
    "Yolyo", "Aghostos", "Sebtember", "Oktober", "November", "Desember",
)  # fmt: skip
_AR_MONTHS = (
    "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
    "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
)  # fmt: skip
_MONTHS: Mapping[Locale, Sequence[str]] = {Locale.EN: _EN_MONTHS, Locale.ARABIZI: _AZ_MONTHS, Locale.AR: _AR_MONTHS}


def conflicting_pair(intents: Sequence[IntentCandidate], tenant: TenantConfig) -> tuple[str, str] | None:
    """The first two intents the tenant says cannot both be wanted that the message cannot tell apart."""
    for i, first in enumerate(intents):
        for second in intents[i + 1 :]:
            pair = {first.name, second.name}
            if (
                any(pair == set(p) for p in tenant.conflicting_intents)
                and round(abs(first.confidence - second.confidence), 2) <= CLOSE_CONFIDENCE
            ):
                return first.name, second.name
    return None


def label_of(tenant: TenantConfig, intent: str, locale: Locale) -> str:
    """A short name for the intent in the customer's style; falls back to the intent name."""
    labels = tenant.intents[intent].labels
    return labels.get(locale.value) or labels.get(Locale.EN.value) or intent.replace("_", " ")


def _short(normalized: str) -> bool:
    return 0 < len(_WORD.findall(normalized)) <= MAX_ANSWER_WORDS


def ordinal_choice(text: str, lexicon: Lexicon, options: int) -> int | None:
    """0-based position the customer picked ("the second" -> 1), or None. Only short messages count."""
    normalized = normalize(text)
    if not _short(normalized):
        return None
    hits = {
        int(number) - 1
        for number, words in lexicon.ordinals.items()
        if any(find_spans(normalized, term) for term in words.all())
    }
    hits = {h for h in hits if 0 <= h < options}
    return hits.pop() if len(hits) == 1 else None


def says_both(text: str, lexicon: Lexicon) -> bool:
    normalized = normalize(text)
    return _short(normalized) and any(find_spans(normalized, term) for term in lexicon.both.all())


def mask_order_id(order_id: str, keep: int = 3) -> str:
    """NS-20877 -> NS-**877: the customer recognizes it, a stranger cannot read it off the screen."""
    head = order_id.rstrip("0123456789")
    digits = order_id[len(head) :]
    return head + "*" * max(0, len(digits) - keep) + digits[-keep:]


def short_date(iso: str, locale: Locale) -> str:
    """2026-09-20 -> "20 Sep", the month in the customer's style. A date that cannot be read is shown as is."""
    try:
        day = date.fromisoformat(iso[:10])
    except ValueError:
        return iso
    return f"{day.day} {_MONTHS[locale][day.month - 1]}"


def describe_orders(orders: Sequence[Mapping[str, str]], locale: Locale) -> str:
    """ "1. NS-**877, 20 Sep" per line, numbered so the customer can answer "the second"."""
    return "\n".join(
        f"{n}. {mask_order_id(o['order_id'])}, {short_date(o.get('placed_at', ''), locale)}"
        for n, o in enumerate(orders, start=1)
    )
