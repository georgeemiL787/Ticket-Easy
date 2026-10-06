"""The reply writer: every sentence the agent says, in the customer's style, from data/locales/<locale>/*.json.

Each locale is a folder of flat {key: text} files (core, actions, knowledge, handoff) with {placeholders}. PLACEHOLDERS
is the contract between code and templates: for each key that takes values it names them, and a test checks every
locale against it (so an Arabizi template cannot forget a number, and code cannot pass a value no template uses).

Naming: ask_<slot>, confirm_action_<capability> (confirm_action_default as the fallback), status_<order status>,
handoff_<escalation reason> (handoff_generic as the fallback).
"""

import json
import re
import string
from collections.abc import Mapping, Sequence
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from team_b.config import PROJECT_ROOT
from team_b.contracts.evidence import Passage
from team_b.contracts.policy import PolicyDecision
from team_b.domain.understanding import Locale

LOCALES_DIR = PROJECT_ROOT / "data" / "locales"

PLACEHOLDERS: Mapping[str, frozenset[str]] = {
    "ask_generic": frozenset({"slot"}),
    "disambiguate_intent": frozenset({"a", "b"}),
    "disambiguate_order": frozenset({"orders"}),
    "order_status_in_transit": frozenset({"order_id", "status", "date"}),
    "order_status_delivered": frozenset({"order_id", "date"}),
    "order_status_other": frozenset({"order_id", "status"}),
    "late_policy_note": frozenset({"days"}),
    "confirm_action_create_return": frozenset({"order_id"}),
    "confirm_action_create_exchange": frozenset({"order_id"}),
    "confirm_action_create_refund": frozenset({"order_id", "amount"}),
    "confirm_action_cancel_order": frozenset({"order_id"}),
    "confirm_action_update_delivery_address": frozenset({"order_id", "address"}),
    "confirm_action_apply_voucher": frozenset({"order_id", "amount"}),
    "confirm_action_create_ticket": frozenset({"order_id"}),
    "confirm_action_default": frozenset({"action"}),
    "action_done": frozenset({"reference"}),
    "policy_refusal": frozenset({"message"}),
}


def load_locale(folder: Path) -> dict[str, str]:
    """All the texts of one locale: the merge of every .json file in its folder. Duplicate keys are rejected."""
    texts: dict[str, str] = {}
    owner: dict[str, str] = {}
    files = sorted(folder.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"no locale files in {folder}")
    for file in files:
        for key, text in json.loads(file.read_text(encoding="utf-8")).items():
            if key in texts:
                raise ValueError(f"template {key!r} is defined in both {owner[key]} and {file.name} ({folder.name})")
            texts[key], owner[key] = text, file.name
    return texts


def placeholders_in(template: str) -> frozenset[str]:
    """The {names} a template expects."""
    return frozenset(name for _, name, _, _ in string.Formatter().parse(template) if name)


class ResponseComposer:
    def __init__(self, texts: Mapping[Locale, Mapping[str, str]]) -> None:
        missing = [locale.value for locale in Locale if locale not in texts]
        if missing:
            raise ValueError(f"no texts for locale(s): {', '.join(missing)}")
        self._texts = texts

    @classmethod
    def from_dir(cls, directory: Path = LOCALES_DIR) -> "ResponseComposer":
        """Load data/locales/<locale>/*.json (core, actions, knowledge, handoff) and merge each locale's files.

        The same key in two files of one locale is an error: every sentence has exactly one owner."""
        return cls({locale: load_locale(directory / locale.value) for locale in Locale})

    def keys(self, locale: Locale) -> frozenset[str]:
        return frozenset(self._texts[locale])

    def has(self, locale: Locale, key: str) -> bool:
        return key in self._texts[locale]

    def t(self, locale: Locale, key: str, **values: object) -> str:
        """The text for `key` in `locale` with its placeholders filled. An unknown key or a missing value is a bug."""
        try:
            template = self._texts[locale][key]
        except KeyError:
            raise KeyError(f"no template {key!r} for locale {locale.value!r}") from None
        needed = placeholders_in(template)
        absent = needed - set(values)
        if absent:
            raise KeyError(f"template {key!r} needs {', '.join(sorted(absent))}")
        return template.format(**{name: values[name] for name in needed})

    def t_first(self, locale: Locale, keys: Sequence[str], **values: object) -> str:
        """The first key that exists, e.g. confirm_action_create_refund, else confirm_action_default."""
        for key in keys:
            if self.has(locale, key):
                return self.t(locale, key, **values)
        raise KeyError(f"no template among {list(keys)} for locale {locale.value!r}")

    def passage_block(self, passages: Sequence[Passage]) -> str:
        """Policy passages quoted exactly as written, each with its citation. Never reworded."""
        return "\n".join(f"“{p.text}” [{p.citation}]" for p in passages)

    def policy_message(self, locale: Locale, decision: PolicyDecision) -> str:
        """What to tell the customer about a rule checker answer: its own message (Arabizi customers get the Arabic
        text), or the generic policy_denied sentence when it sent none."""
        message = decision.user_message
        if message is not None:
            return (message.en if locale is Locale.EN else message.ar).strip() or self.t(locale, "policy_denied")
        return self.t(locale, "policy_denied")


@lru_cache(maxsize=1)
def default_composer() -> ResponseComposer:
    return ResponseComposer.from_dir()


def render(key: str, locale: Locale, **values: object) -> str:
    """Shortcut: the default composer's t(), with the key first (the way stages name what they want to say)."""
    return default_composer().t(locale, key, **values)


_KEY_IN_CODE = re.compile(r"""reply_key\s*=\s*["']([a-z_]+)["']""")


def keys_used_in(source: str) -> set[str]:
    """Literal template keys named in a piece of Python source (reply_key="..."). Used by the template tests."""
    return set(_KEY_IN_CODE.findall(source))


# ---- the fact check for reworded replies (safety-critical: this is what stops invented numbers) ----

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")
_ISO_DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}")
_SLASH_DATE = re.compile(r"\d{1,2}[/.]\d{1,2}[/.]\d{2,4}")
_ID = re.compile(r"[a-z]{1,5}-\d+")
_CITATION = re.compile(r"[a-z_]+@v\d+#[a-z]+\d+")
_URL = re.compile(r"(?:https?://|www\.)[^\s)\]]+")
_WORD = re.compile(r"[^\W\d_]+(?:\.[^\W\d_]+)*", re.UNICODE)

_MONTHS: Mapping[int, tuple[str, ...]] = {
    1: ("january", "jan", "يناير", "yanayer"),
    2: ("february", "feb", "فبراير", "febrayer"),
    3: ("march", "mar", "مارس", "mares"),
    4: ("april", "apr", "أبريل", "ابريل", "abreel"),
    5: ("may", "مايو", "mayo"),
    6: ("june", "jun", "يونيو", "yonyo"),
    7: ("july", "jul", "يوليو", "yolyo"),
    8: ("august", "aug", "أغسطس", "اغسطس", "aghostos"),
    9: ("september", "sept", "sep", "سبتمبر", "sebtember"),
    10: ("october", "oct", "أكتوبر", "اكتوبر", "oktober"),
    11: ("november", "nov", "نوفمبر"),
    12: ("december", "dec", "ديسمبر", "desember"),
}
_MONTH_OF = {name: number for number, names in _MONTHS.items() for name in names}
_MONTH_NAMES = "|".join(sorted(_MONTH_OF, key=len, reverse=True))
_DATE_MENTION = re.compile(
    rf"(?:\d{{1,2}}\s*(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m1>{_MONTH_NAMES})\b)|(?:\b(?P<m2>{_MONTH_NAMES})\s+\d{{1,2}}\b)",
    re.IGNORECASE,
)
_CURRENCY_OF = {
    "egp": "egp", "le": "egp", "l.e": "egp", "pound": "egp", "pounds": "egp", "geneh": "egp", "gnh": "egp",
    "جنيه": "egp", "جنيها": "egp", "جنيهات": "egp",
    "usd": "usd", "dollar": "usd", "dollars": "usd", "دولار": "usd", "dolar": "usd",
    "eur": "eur", "euro": "eur", "euros": "eur", "يورو": "eur",
    "gbp": "gbp", "sterling": "gbp",
}  # fmt: skip
_RELATIVE_TIME = (
    "today", "tomorrow", "yesterday", "tonight", "next week", "next month", "this week", "this month", "last week",
    "اليوم", "النهارده", "النهاردة", "بكرة", "بكره", "غدا", "غدًا", "امبارح", "أمس", "الاسبوع الجاي", "الشهر الجاي",
    "enharda", "elnaharda", "bokra", "bukra", "embare7", "embareh", "el esbo3 el gay", "el shahr el gay",
)  # fmt: skip


def normalize_digits(text: str) -> str:
    """Arabic-Indic and Persian digits become ASCII, Arabic separators become . and ,, thousands commas disappear."""
    return _THOUSANDS.sub("", text.translate(_DIGITS))


def _numbers(text: str) -> set[Decimal]:
    return {Decimal(n) for n in _NUMBER.findall(text)}


def _months_in_dates(text: str) -> set[int]:
    found = set()
    for match in _DATE_MENTION.finditer(text):
        name = (match.group("m1") or match.group("m2")).lower()
        found.add(_MONTH_OF[name])
    return found


def _currencies(text: str) -> set[str]:
    words = {w.lower().rstrip(".") for w in _WORD.findall(text)}
    return {_CURRENCY_OF[w] for w in words if w in _CURRENCY_OF}


def _has_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _exact_tokens(text: str) -> dict[str, set[str]]:
    return {
        "date": set(_ISO_DATE.findall(text)) | set(_SLASH_DATE.findall(text)),
        "order or reference id": set(_ID.findall(text)),
        "citation": set(_CITATION.findall(text)),
        "link": set(_URL.findall(text)),
    }


def check_grounded(text: str, sources: Sequence[str], *, keep: Sequence[str] = ()) -> list[str]:
    """Why `text` must not be sent to a customer, as a list of plain problems; empty means it is grounded.

    Grounded means it adds no fact the sources do not contain. After normalizing digits (Arabic-Indic, thousands
    separators) every number, date (also its month), order or reference id, currency, link, citation id and relative
    day word ("tomorrow") in `text` must occur in the sources (the template text and the facts it was filled from).
    Whatever numbers, dates, ids and links the `keep` texts contain must also survive: a rewrite may not drop the
    reference number the customer needs."""
    out = normalize_digits(text).lower()
    given = normalize_digits(" ".join(sources)).lower()
    problems: list[str] = []

    known = _numbers(given)
    problems += [f"new number {n}" for n in dict.fromkeys(_NUMBER.findall(out)) if Decimal(n) not in known]
    given_tokens, out_tokens = _exact_tokens(given), _exact_tokens(out)
    for kind, tokens in out_tokens.items():
        problems += [f"new {kind} {t}" for t in sorted(tokens - given_tokens[kind])]
    problems += [f"new month {m}" for m in sorted(_months_in_dates(out) - _months_in_dates(given))]
    problems += [f"new currency {c}" for c in sorted(_currencies(out) - _currencies(given))]
    problems += [f"new time word {w}" for w in _RELATIVE_TIME if _has_phrase(out, w) and not _has_phrase(given, w)]

    required = normalize_digits(" ".join(keep)).lower()
    kept_tokens = _exact_tokens(out)
    problems += [f"dropped number {n}" for n in sorted(_numbers(required) - _numbers(out))]
    for kind, tokens in _exact_tokens(required).items():
        problems += [f"dropped {kind} {t}" for t in sorted(tokens - kept_tokens[kind])]
    return problems
