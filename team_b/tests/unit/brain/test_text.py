import pytest

from team_b.brain.text import contains_term, normalize


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("٠١٠١٢٣٤٥٦٠١", "01012345601"),  # Arabic-Indic digits
        ("۰۱۰۱۲۳۴۵۶۰۱", "01012345601"),  # Persian digits
        ("أحمد إبراهيم آخر", "احمد ابراهيم اخر"),  # alef variants
        ("مصطفى", "مصطفي"),  # alef maqsura -> yaa
        ("القاهرة", "القاهره"),  # taa marbuta -> haa
        ("مُحَمَّد", "محمد"),  # diacritics removed
        ("جمـــيل", "جميل"),  # tatweel removed
        ("HeLLo WORLD", "hello world"),
        ("  too    many \n spaces\t", "too many spaces"),
        ("هل ده صح؟", "هل ده صح?"),  # Arabic question mark
        ("NS-٢٠٨٧٧", "ns-20877"),  # mixed script id
        ("", ""),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize("raw", ["عايز أرجّع المنتَج", "3ayez A3RAF", "  ٣ ayez  "])
def test_normalize_is_idempotent(raw: str) -> None:
    assert normalize(normalize(raw)) == normalize(raw)


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("I want a refund please", "refund"),
        ("I want a REFUND please", "refund"),
        ("refund", "refund"),
        ("Refund!", "refund"),
        ("where is my order, please", "my order"),
        ("عايز استرجاع فلوسي", "استرجاع"),
        ("عايز أسترجاع فلوسي", "استرجاع"),  # alef variant
        ("المنتج وصل مكسور", "مكسور"),
        ("ازاي الغي الاوردر؟", "الاوردر"),
        ("3ayez flousi", "flousi"),
        ("el order NS-20877", "ns-20877"),
    ],
)
def test_contains_term_finds_whole_words(text: str, term: str) -> None:
    assert contains_term(text, term)


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("I want to return it", "ret"),  # inside a longer word
        ("refunds", "refund"),
        ("prefund", "refund"),
        ("المنتجات", "المنتج"),  # inside a longer Arabic word
        ("استرجاعات", "استرجاع"),
        ("flousi", "flous"),
        ("hello", ""),
        ("", "hello"),
        ("NS-20877", "20"),  # a number inside a number
    ],
)
def test_contains_term_rejects_partial_words(text: str, term: str) -> None:
    assert not contains_term(text, term)
