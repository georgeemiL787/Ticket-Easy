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
