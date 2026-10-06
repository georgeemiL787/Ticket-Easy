"""Hide sensitive values (phone, email, card, one-time code, address) before text is stored in a trace or logged.

Detection runs on a copy with Arabic-Indic digits turned into ASCII (same length), so the positions fit the original.

Addresses cannot be told apart from other words with certainty, so they are found by their shape: a street word
(street, road, شارع, share3 ...) with the few words around it, a house number before a street word, or a building,
apartment or floor number. Values stored under an address-like key (new_address, address) are always hidden, whatever
they look like (see redact_value).
"""

import re
from collections.abc import Mapping
from typing import Any

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w+])(?:(?:\+|00)\s?20[\s-]?|0)1[0125](?:[\s-]?\d){8}(?!\d)")
_CARD = re.compile(r"(?<![\w+])\d(?:[ -]?\d){12,18}(?!\d)")
_OTP = re.compile(r"(?i)(?:otp|code|pin|cvv|cvc|رمز|كود)\D{0,12}?(\d{4,8})(?!\d)")
_STREET = r"(?:street|st|road|rd|avenue|ave|شارع|شارع|ش|share3|shari3|sharia|sh)"
_WORD = r"[\w؀-ۿ'-]+"
_ADDRESS = re.compile(
    rf"(?ix)"
    rf"(?:\b\d{{1,4}}[,\s]+)?(?<!\w){_STREET}\.?\s+{_WORD}(?:[\s,]+(?!\d{{4}}){_WORD}){{0,3}}"  # [12] street name ...
    rf"|\b\d{{1,4}}\s+(?:{_WORD}\s+){{0,3}}(?:street|road|avenue)\b"  # 15 Tahrir street
    rf"|(?<!\w)(?:building|bldg|apt|apartment|flat|floor|عمارة|عماره|شقة|شقه|برج|دور)\.?\s*(?:no\.?\s*|رقم\s*)?\d+"
)
ADDRESS_KEYS = frozenset({"address", "new_address", "delivery_address", "shipping_address", "street"})
PHONE_KEYS = frozenset({"phone", "phone_number", "mobile"})
EMAIL_KEYS = frozenset({"email"})
CARD_KEYS = frozenset({"card", "card_number", "pan"})
OTP_KEYS = frozenset({"otp", "code", "pin", "cvv"})
_BY_KEY = {
    **dict.fromkeys(ADDRESS_KEYS, "[address]"),
    **dict.fromkeys(PHONE_KEYS, "[phone]"),
    **dict.fromkeys(EMAIL_KEYS, "[email]"),
    **dict.fromkeys(CARD_KEYS, "[card]"),
    **dict.fromkeys(OTP_KEYS, "[otp]"),
}


def redact(text: str) -> str:
    """Replace phones, emails, cards, one-time codes and addresses with [phone], [email], [card], [otp], [address]."""
    ascii_text = text.translate(_DIGITS)
    spans: list[tuple[int, int, str]] = []

    def free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e, _ in spans)

    patterns = (
        ("[email]", _EMAIL),
        ("[phone]", _PHONE),
        ("[card]", _CARD),
        ("[otp]", _OTP),
        ("[address]", _ADDRESS),
    )
    for label, pattern in patterns:
        for match in pattern.finditer(ascii_text):
            start, end = match.span(1) if label == "[otp]" else match.span()
            if free(start, end):
                spans.append((start, end, label))
    out: list[str] = []
    position = 0
    for start, end, label in sorted(spans):
        out.append(text[position:start])
        out.append(label)
        position = end
    out.append(text[position:])
    return "".join(out)


def contains_sensitive(text: str) -> bool:
    """Would redact() change this text? (Already hidden values like [phone] do not count.)"""
    return redact(text) != text


def sensitive_kinds(text: str) -> set[str]:
    """Which kinds of personal value (phone, email, card, otp, address) are still in the text."""
    ascii_text = text.translate(_DIGITS)
    found = {
        label.strip("[]")
        for label, pattern in (
            ("[email]", _EMAIL),
            ("[phone]", _PHONE),
            ("[card]", _CARD),
            ("[otp]", _OTP),
            ("[address]", _ADDRESS),
        )
        if pattern.search(ascii_text)
    }
    return found


def redact_value(key: str, value: Any) -> Any:
    """Redact one named value: a value under a sensitive key is hidden whole, other text is pattern-redacted."""
    label = _BY_KEY.get(key.lower())
    if label is not None and value not in (None, ""):
        return label
    return redact(value) if isinstance(value, str) else value


def redact_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    """redact_value for every entry of an entities or tool-arguments mapping (nested mappings and lists too)."""

    def walk(key: str, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {k: walk(k, v) for k, v in value.items()}
        if isinstance(value, list | tuple):
            return [walk(key, v) for v in value]
        return redact_value(key, value)

    return {k: walk(k, v) for k, v in values.items()}
