import pytest

from team_b.brain.language import LOCALE_FOR, detect_language, locale_for
from team_b.brain.lexicon import Lexicon, default_lexicon, load_lexicon
from team_b.config import PROJECT_ROOT
from team_b.domain.understanding import Language, Locale

EN = [
    "Where is my order?",
    "I want to return the dress I bought last week",
    "How long does shipping take to Alexandria?",
    "Can I get a refund for order NS-20877",
    "hello",
    "thanks, bye",
    "Please cancel my order",
    "My phone is 01012345601 and the order was late",
    "What is your return policy?",
    "I need to change the delivery address",
    "the package arrived damaged, what should I do",
    "Hi, I have a question about my order",
    "Do you ship outside Egypt?",
    "I want to talk to a human agent please",
    "3 days ago I ordered a jacket",  # a digit that is just a number
    "my 2nd order is missing the 5g charger",  # ordinals and units are not Arabizi
]
AR = [
    "ممكن ارجع المنتج بعد كام يوم من الاستلام؟",
    "الاوردر بتاعي وصل فين؟",
    "عايز الغي الاوردر لو سمحت",
    "السلام عليكم",
    "المنتج وصلني مكسور اعمل ايه",
    "عايز فلوسي ترجع",
    "هو الشحن بكام للاسكندرية؟",
    "ازاي اغير عنوان التوصيل",
    "عايز اكلم حد من خدمة العملاء",
    "الطلب اتاخر ومحدش بيرد عليا",
    "NS-20790 عايز ارجع الاوردر ده",  # an order id does not make it mixed
    "رقمي ٠١٠١٢٣٤٥٦٠١ والاوردر اتاخر",  # neither does a phone number
    "شكرا ليكم",
    "مُمكن أسترجع المنتَج؟",  # diacritics and alef variants
    "ايوه تمام",
    "هل يوجد تغليف هدايا",
]
MIXED = [
    "ممكن اعمل return للمنتج بعد كام يوم من الـ delivery؟",
    "عايز refund للاوردر بتاعي",
    "ازاي اعمل cancel للاوردر",
    "الـ order لسه مجاش وعايز اعرف tracking number",
    "عايز اغير الـ address بتاع الاوردر",
    "I want to return المنتج ده لان المقاس غلط",
    "Hello عايز اسأل عن الشحن",
    "الـ delivery اتاخر ومحدش كلمني",
    "عايز اعمل exchange للـ size",
    "ممكن اعرف الـ status والـ tracking بتاع الطلب ده",
    "The size غلط وعايز استبدل",
    "عايز voucher عشان التأخير",
    "فين الـ package بتاعي",
    "refund ده هيرجع امتى على الكارت",
    "ازاي اعمل return",
    "هل ينفع اعمل cancel بعد الـ shipping",
]
ARABIZI = [
    "emta a2dar araga3 el montag?",
    "el order bta3i et2akhar, 3ayez a3raf feen",
    "3ayez alghy el order",
    "ahlan, ezayak",
    "3ayez flousi el order mesh 3agebny",
    "3ayez a-return el order",  # Arabizi with an English word inside
    "ok tamam",
    "ah tamam",
    "el order NS-20877 et2akhar",
    "ezay a8ayar el 3enwan",
    "ma3lesh, el montag wasal mksour",
    "3ayez akalem mowazaf",
    "mafeesh 7ad byrod 3alaya",
    "ana 3ayez a3raf el order wasal wala la2",
    "momken a3raf feen el talab bta3i?",
    "la2 msh 3ayez",
]
STYLES = [(text, Language.EN) for text in EN] + [(t, Language.AR) for t in AR]
STYLES += [(t, Language.MIXED) for t in MIXED] + [(t, Language.ARABIZI) for t in ARABIZI]


@pytest.mark.parametrize(("text", "expected"), STYLES, ids=[f"{lang.value}:{text[:30]}" for text, lang in STYLES])
def test_language_of_each_style(text: str, expected: Language) -> None:
    language, confidence = detect_language(text)
    assert language is expected, (language, confidence)
    assert 0.5 <= confidence <= 1.0


@pytest.mark.parametrize(
    "text", ["ok", "OK", "okay", "123", "01012345601", "NS-20877", "+20 10 1234 5601", "👍", "???", "", "   ", "٣٠٠٠"]
)
def test_content_free_messages_have_no_language(text: str) -> None:
    language, confidence = detect_language(text)
    assert language is None and confidence == 0.0


def test_a_single_marker_word_is_arabizi_with_modest_confidence() -> None:
    language, confidence = detect_language("tamam")
    assert language is Language.ARABIZI and confidence <= 0.7


def test_confidence_grows_with_evidence() -> None:
    _, short = detect_language("hello")
    _, longer = detect_language("hello, where is my order please")
    assert longer > short
    _, one = detect_language("ahlan")
    _, several = detect_language("ahlan ezayak 3ayez a3raf feen el order")
    assert several > one


def test_numbers_and_ids_do_not_change_the_language() -> None:
    assert detect_language("NS-20877 ns20877 NS 20877 where is it")[0] is Language.EN
    assert detect_language("عايز ارجع ns20877")[0] is Language.AR
    assert detect_language("3ayez a3raf el order 20877")[0] is Language.ARABIZI


def test_digit_words_need_letters_around_the_digit() -> None:
    assert detect_language("mp3 player and 4k tv")[0] is Language.EN
    assert detect_language("a3raf")[0] is Language.ARABIZI


def test_locale_mapping_is_one_table() -> None:
    assert LOCALE_FOR == {
        Language.EN: Locale.EN,
        Language.AR: Locale.AR,
        Language.MIXED: Locale.AR,  # mixed customers get Egyptian Arabic
        Language.ARABIZI: Locale.ARABIZI,
    }
    assert [locale_for(lang) for lang in Language] == [Locale.EN, Locale.AR, Locale.AR, Locale.ARABIZI]


def test_the_default_lexicon_loads_and_is_normalized() -> None:
    lexicon = default_lexicon()
    assert {"3ayez", "feen", "tamam", "el"} <= lexicon.arabizi_tokens
    assert all(t == t.lower() and " " not in t for t in lexicon.arabizi_tokens)
    # words that are also common English words are deliberately not markers
    assert not {"la", "me", "we", "men", "law", "ya", "al", "ok"} & lexicon.arabizi_tokens


def test_a_custom_lexicon_changes_the_result(tmp_path: object) -> None:
    custom = Lexicon(arabizi_tokens=frozenset({"bonjour"}))
    assert detect_language("bonjour bonjour", custom)[0] is Language.ARABIZI
    assert detect_language("bonjour bonjour")[0] is Language.EN


def test_load_lexicon_ignores_keys_it_does_not_own(tmp_path: object) -> None:
    from pathlib import Path

    path = Path(str(tmp_path)) / "lex.json"
    path.write_text('{"arabizi_tokens": ["Yalla"], "yes": ["aywa"]}', encoding="utf-8")
    assert load_lexicon(path).arabizi_tokens == frozenset({"yalla"})
    assert (PROJECT_ROOT / "data" / "lexicon" / "default.json").is_file()
