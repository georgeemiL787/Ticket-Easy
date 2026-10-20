"""Tell English, Egyptian Arabic, mixed and Arabizi apart, and decide which reply style each one gets.

Signals, after order ids, phone numbers, other numbers, links and emails are ignored:
- Arabic words (Arabic script);
- English words (Latin letters that are not Arabizi);
- Arabizi markers: words from the lexicon's arabizi_tokens, and words that use digits as letters (3ayez, a3raf, 7aga).

Rules: Arabic with a substantial share of English words is MIXED; Latin text with Arabizi markers (at least two, or a
quarter of its words) is ARABIZI; Arabic only is AR; English only is EN. A message with no language content ("ok",
"123", an order id, an emoji) gives (None, low confidence) so the conversation keeps the language it already has.
"""

import re
from collections.abc import Mapping

from team_b.brain.lexicon import Lexicon, default_lexicon
from team_b.brain.text import normalize
from team_b.domain.understanding import Language, Locale

# One place decides the reply style: mixed-language customers get Egyptian Arabic.
LOCALE_FOR: Mapping[Language, Locale] = {
    Language.EN: Locale.EN,
    Language.AR: Locale.AR,
    Language.MIXED: Locale.AR,
    Language.ARABIZI: Locale.ARABIZI,
}

LANGUAGE_TRUST = 0.6  # a detected language with at least this confidence replaces the conversation language
MIXED_MIN_SHARE = 0.15  # each of Arabic and English must be at least this share of the words to count as mixed
ARABIZI_MIN_SHARE = 0.25  # markers as a share of the Latin words (two or more markers always count)
NEUTRAL_WORDS = frozenset({"ok", "okay", "k", "kk", "lol", "hmm", "hm", "mm"})  # say nothing about the language
DIGIT_LETTERS = "2356789"  # digits used as Arabic letters: 2 hamza, 3 ayn, 5 khaa, 6 taa, 7 haa, 8 ghayn, 9 qaaf

_NOISE = re.compile(
    r"https?://\S+|\S+@\S+"  # links and emails
    r"|\+?\d[\d\s\-]{6,}\d"  # phone numbers and other long digit runs
    r"|\b[a-z]{2,3}[-\s]?\d{4,6}\b"  # order ids like ns-20877, ns 20877, ns20877
)
_ARABIC_WORD = re.compile(r"[ء-يٮ-ۓ]+")
_LATIN_TOKEN = re.compile(r"[a-z0-9]+")
_UNIT = re.compile(r"^\d+(st|nd|rd|th|k|g|gb|mb|kb|kg|cm|mm|m|x|pm|am|px|hz)$")
_NOT_ARABIZI = frozenset({"mp3", "mp4", "h2o", "b2b", "b2c", "3d", "4k", "5g", "covid19", "web3", "i18n", "l10n"})


def locale_for(language: Language) -> Locale:
    return LOCALE_FOR[language]


def _has_digit_letter(token: str) -> bool:
    """3ayez, a3raf, 7aga: letters mixed with a digit that Arabizi uses as a letter."""
    if token in _NOT_ARABIZI or _UNIT.match(token):
        return False
    letters = sum(c.isalpha() for c in token)
    return letters >= 2 and any(c in DIGIT_LETTERS for c in token) and any(c.isalpha() for c in token)


def _count(text: str, lexicon: Lexicon) -> tuple[int, int, int]:
    """(arabic words, english words, arabizi markers) in the message."""
    cleaned = _NOISE.sub(" ", normalize(text))
    arabic = len(_ARABIC_WORD.findall(cleaned))
    english = markers = 0
    for token in _LATIN_TOKEN.findall(_ARABIC_WORD.sub(" ", cleaned)):
        if not any(c.isalpha() for c in token) or token in NEUTRAL_WORDS:
            continue
        if token in lexicon.arabizi_tokens or _has_digit_letter(token):
            markers += 1
        elif len(token) > 1 or token in {"i", "a"}:
            english += 1
    return arabic, english, markers


def detect_language(text: str, lexicon: Lexicon | None = None) -> tuple[Language | None, float]:
    """(language, confidence 0..1). Language is None when the message has no language content."""
    arabic, english, markers = _count(text, lexicon or default_lexicon())
    total = arabic + english + markers
    if total == 0:
        return None, 0.0

    if arabic:
        foreign = english  # Arabizi words inside Arabic text are just Arabic words
        arabic_share, english_share = (arabic + markers) / total, foreign / total
        if arabic_share >= MIXED_MIN_SHARE and english_share >= MIXED_MIN_SHARE:
            return Language.MIXED, round(0.6 + min(0.3, 0.6 * min(arabic_share, english_share)), 2)
        if english > arabic + markers:
            return Language.EN, 0.55
        return Language.AR, round(min(0.99, 0.6 + 0.08 * total), 2)

    if markers and (markers >= 2 or markers / (markers + english) >= ARABIZI_MIN_SHARE):
        return Language.ARABIZI, round(min(0.98, 0.6 + 0.1 * markers), 2)
    if english:
        return Language.EN, round(min(0.99, 0.5 + 0.1 * english), 2)
    return Language.ARABIZI, 0.55  # a single marker word on its own, e.g. "tamam"
