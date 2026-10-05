"""Text normalization shared by language detection, understanding and search.

normalize() makes spelling variants comparable: Arabic-Indic and Persian digits become ASCII digits, the usual alef,
yaa and taa-marbuta variants collapse, diacritics and tatweel disappear, Latin text is lowercased and spaces collapse.
contains_term() then finds a word or phrase with word boundaries, for Latin and Arabic text alike.
"""

import re
import unicodedata
from functools import lru_cache

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_LETTERS = str.maketrans(
    {
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
        "ى": "ي",
        "ة": "ه",
        "ؤ": "و",
        "ئ": "ي",
        "ڤ": "ف",
        "پ": "ب",
        "چ": "ج",
        "گ": "ك",
        "ک": "ك",
        "ی": "ي",
        "؟": "?",
        "،": ",",
        "؛": ";",
    }
)
_TATWEEL = "ـ"
_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    """The comparable form of `text`. Safe to call twice: normalize(normalize(x)) == normalize(x)."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(_TATWEEL, "").translate(_DIGITS).translate(_LETTERS)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")  # diacritics (harakat) and other marks
    return _SPACES.sub(" ", text.lower()).strip()


def contains_term(text: str, term: str) -> bool:
    """Is `term` in `text` as a whole word or phrase? Both are normalized first, so spelling variants match.

    A match must not touch a letter or digit on either side, so "ret" is not found in "return" and an Arabic word is
    not found inside a longer one (but is found next to punctuation or spaces)."""
    wanted = normalize(term)
    if not wanted:
        return False
    return re.search(rf"(?<!\w){re.escape(wanted)}(?!\w)", normalize(text)) is not None


_ARABIC_PREFIXES = "وفبكسل"  # one attached letter: and, so, with, like, will, to


@lru_cache(maxsize=2048)
def _term_regex(term: str) -> re.Pattern[str]:
    body = re.escape(term)
    if "ء" <= term[0] <= "ي":
        # Arabic attaches small words to the next word: "للاوردر" is "ل" + "الاوردر", "والغي" is "و" + "الغي".
        alternatives = [rf"[{_ARABIC_PREFIXES}]?{body}"]
        if term.startswith("ال") and len(term) > 2:
            alternatives.append(rf"[وف]?لل{re.escape(term[2:])}")
        body = "(?:" + "|".join(alternatives) + ")"
    return re.compile(rf"(?<!\w){body}(?!\w)")


def find_spans(normalized_text: str, normalized_term: str) -> list[tuple[int, int]]:
    """Where `normalized_term` occurs in `normalized_text` as a whole word or phrase: [(start, end), ...].

    Both arguments must already be normalized. An Arabic term also matches with one attached prefix letter
    ("والغي", "للاوردر"); nothing else may touch the match."""
    if not normalized_term:
        return []
    return [m.span() for m in _term_regex(normalized_term).finditer(normalized_text)]
