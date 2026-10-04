from team_a.text import detect_language, expand_query, normalize, tokenize


def test_normalize_unifies_arabic_letter_variants_and_digits():
    assert normalize("إسترجاع المُنتَج خلال ١٤ يومًا") == "استرجاع المنتج خلال 14 يوما"
    assert normalize("مكتبة") == normalize("مكتبه")
    assert normalize("على") == normalize("علي")


def test_tokenize_strips_definite_article_and_stopwords():
    assert tokenize("والاسترجاع في المتجر") == ["استرجاع", "متجر"]
    assert tokenize("Can I return the item") == ["return", "item"]


def test_expand_query_adds_arabic_and_english_for_arabizi():
    expanded = normalize(expand_query("momken araga3 el 7aga"))
    assert "استرجاع" in expanded and "return" in expanded


def test_expand_query_matches_prefixed_dialect_words():
    assert "upper egypt" in expand_query("التوصيل للصعيد")


def test_detect_language():
    assert detect_language("سياسة الاسترجاع") == "ar"
    assert detect_language("Return policy") == "en"
    assert detect_language("question: سؤال answer: جواب") == "mixed"
