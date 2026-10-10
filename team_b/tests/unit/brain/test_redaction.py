import pytest

from team_b.brain.redaction import redact


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("my phone is 01012345601", "my phone is [phone]"),
        ("call +20 10 1234 5601 please", "call [phone] please"),
        ("رقمي ٠١٠١٢٣٤٥٦٠١", "رقمي [phone]"),
        ("0020-10-1234-5601", "[phone]"),
        ("mail me at mona.adel@example.com now", "mail me at [email] now"),
        ("card 4111 1111 1111 1111 expires", "card [card] expires"),
        ("my otp is 482913", "my otp is [otp]"),
        ("الكود 1234", "الكود [otp]"),
        ("order NS-20877 please", "order NS-20877 please"),  # order ids are not secret
        ("I paid 1250 EGP", "I paid 1250 EGP"),
        ("hello", "hello"),
    ],
)
def test_redact(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_several_values_in_one_message() -> None:
    out = redact("order NS-20877, phone 01012345601, email a@b.co")
    assert out == "order NS-20877, phone [phone], email [email]"


@pytest.mark.parametrize(
    "text",
    ["A" * 100_000, "1 " * 50_000, "st a " * 20_000, "a@" * 50_000, "a." * 50_000, "ا" * 100_000, "12 st x " * 12_000],
    ids=["letters", "digits", "street words", "at signs", "dots", "arabic", "mixed"],
)
def test_a_huge_text_is_hidden_in_one_pass_not_in_minutes(text: str) -> None:
    """A model's 100,000-character argument took 43 seconds to redact once (a pattern started at every letter)."""
    import time

    started = time.perf_counter()
    redact(text)
    assert time.perf_counter() - started < 2.0


def test_an_email_is_still_found_after_the_speed_fix() -> None:
    assert redact("mail mona.adel+shop@example.co.uk now") == "mail [email] now"
    assert redact("y" * 64 + "@example.com") == "[email]"  # 64 characters is the longest address part there is
