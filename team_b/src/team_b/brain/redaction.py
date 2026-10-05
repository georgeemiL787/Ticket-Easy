"""Hide sensitive values (phone, email, card, one-time codes) before text is stored in a trace or written to a log.

Detection runs on a copy with Arabic-Indic digits turned into ASCII (same length), so the positions fit the original.
Not covered: free-text street addresses; they cannot be told apart from other words reliably, so they are kept out of
traces by never copying slot values (entities) unredacted.
"""

import re

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w+])(?:(?:\+|00)\s?20[\s-]?|0)1[0125](?:[\s-]?\d){8}(?!\d)")
_CARD = re.compile(r"(?<![\w+])\d(?:[ -]?\d){12,18}(?!\d)")
_OTP = re.compile(r"(?i)(?:otp|code|pin|cvv|cvc|رمز|كود)\D{0,12}?(\d{4,8})(?!\d)")


def redact(text: str) -> str:
    """Replace phones, emails, card numbers and one-time codes with [phone], [email], [card], [otp]."""
    ascii_text = text.translate(_DIGITS)
    spans: list[tuple[int, int, str]] = []

    def free(start: int, end: int) -> bool:
        return all(end <= s or start >= e for s, e, _ in spans)

    for label, pattern in (("[email]", _EMAIL), ("[phone]", _PHONE), ("[card]", _CARD), ("[otp]", _OTP)):
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
