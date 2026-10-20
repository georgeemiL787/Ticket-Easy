"""Details the customer says in their own words: a reason, a new address, a description, an item.

OWNER: Track A.

The understanding step finds order numbers, phones and amounts. These four are free text, so they are picked up here by
simple, predictable rules (no AI), and only for slots the request actually needs:
  - after the agent asked for one (awaiting slot:<name>), the whole answer is the value;
  - a reason may come with the first message: after "because" / "لان" / "3shan", or as a clause after the request
    ("I want to return order NS-20790, the size is wrong"). A clause that holds a number, a request or a want-marker is
    never taken as a reason;
  - a new address may come as "... to 5 Nile Corniche, Maadi" (text after "to" / "لـ" that starts with a street number).
If nothing is found the slot stays empty and the agent asks. A value is never invented and never longer than MAX_LENGTH.
"""

import re
from collections.abc import Collection, Mapping

from team_b.brain.lexicon import default_lexicon
from team_b.brain.text import find_spans, normalize

FREE_TEXT_SLOTS = ("reason", "new_address", "description", "item")
MAX_LENGTH = 300
_CLAUSE_SPLIT = re.compile(r"[,;.!?،؛\n]+|\s-\s")
_REASON_MARKERS = re.compile(
    r"(?:\b(?:because|since)\b|لان(?:ه|ها)?\b|عشان\b|علشان\b|بسبب\b|\b(?:3shan|3ashan|3lshan|ashan|le2an|li2an)\b)\s*(.+)$",
    re.IGNORECASE,
)
_ADDRESS_LEAD_INS = re.compile(
    r"^\s*(?:the\s+)?(?:new\s+)?(?:delivery\s+)?address\s*(?:is|:)?\s*"
    r"|^\s*(?:العنوان الجديد|عنوان جديد|العنوان)\s*(?:هو|:)?\s*"
    r"|^\s*(?:el\s+)?3enwan\s+(?:el\s+)?(?:gdid|gedid)\s*",
    re.IGNORECASE,
)
_NUMBERS = re.compile(r"[0-9٠-٩]")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip(" \t\n\r.,;:!?،؛-")[:MAX_LENGTH].strip()


def _is_request_or_detail(clause: str) -> bool:
    """A clause that is not a reason: it has a number (order id, phone, amount), a request keyword or a want-marker."""
    if _NUMBERS.search(clause):
        return True
    lexicon, normalized = default_lexicon(), normalize(clause)
    if any(find_spans(normalized, normalize(term)) for words in lexicon.intents.values() for term in words.all()):
        return True
    return any(find_spans(normalized, normalize(term)) for term in lexicon.want_markers.all())


_VAGUE = re.compile(
    r"\b(?:problem|issue|trouble|help|complain\w*)\b|مشكل[ةه]|مساعد[ةه]|شكو[ىي]|\b(?:moshkela|mushkila|mos?a3da|shakwa)\b",
    re.IGNORECASE,
)
MAX_VAGUE_WORDS = 8


def _only_announces_a_problem(value: str) -> bool:
    """ "I have a problem with my order" / "عندي مشكلة": a problem is announced, but nothing says what it is."""
    return len(value.split()) <= MAX_VAGUE_WORDS and _VAGUE.search(value) is not None


def reason_from(text: str) -> str | None:
    """The reason in a message that also makes a request, or None."""
    marked = _REASON_MARKERS.search(text)
    if marked is not None:
        value = _clean(marked.group(1))
        return value or None
    parts = [_clean(c) for c in _CLAUSE_SPLIT.split(text)]
    reasons = [c for c in parts[1:] if len(c.split()) >= 2 and not _is_request_or_detail(c)]
    return _clean(", ".join(reasons)) or None


_ADDRESS_AFTER = re.compile(
    r"(?:\s+(?:to|le|lel|ela|ila)\s+|(?:^|\s)(?:لـ|الى|إلى|الي)\s*)([0-9٠-٩].*)$", re.IGNORECASE
)
_PART_SPLIT = re.compile(r"\s*[,;،؛]\s*")


def _is_request(part: str) -> bool:
    """Does this part of a message ask for something (a request keyword or a want-marker)?"""
    lexicon, normalized = default_lexicon(), normalize(part)
    if any(find_spans(normalized, normalize(term)) for words in lexicon.intents.values() for term in words.all()):
        return True
    return any(find_spans(normalized, normalize(term)) for term in lexicon.want_markers.all())


def _before_next_request(value: str) -> str:
    """The address up to where the next request of the same message starts ("..., Maadi, w 3ayez flousi ...")."""
    kept: list[str] = []
    for part in _PART_SPLIT.split(value):
        if kept and _is_request(part):
            break
        kept.append(part)
    return ", ".join(kept)


def address_from(text: str) -> str | None:
    """A street address after "to" / "le" / "لـ" / "الى" when it starts with a street number, or None."""
    found = _ADDRESS_AFTER.search(text.strip())
    if found is None:
        return None
    value = _clean(_before_next_request(found.group(1)))
    return value if len(value.split()) >= 2 else None


def answer_to(slot: str, text: str) -> str | None:
    """The whole message as the value of the slot that was asked for."""
    value = _clean(_ADDRESS_LEAD_INS.sub("", text, count=1) if slot == "new_address" else text)
    return value or None


def capture(
    text: str,
    wanted: Collection[str],
    awaiting: str | None,
    *,
    other_intent: bool,
    entities: Mapping[str, str],
) -> dict[str, str]:
    """The free-text details in this message, for the slots in `wanted` (the slots this request needs).

    other_intent: the message also asks for something other than the request in progress, so it is not a plain answer.
    entities: what the understanding step found; a message that is only an order number or phone is not free text."""
    found: dict[str, str] = {}
    asked = awaiting.removeprefix("slot:") if awaiting and awaiting.startswith("slot:") else None
    if asked in FREE_TEXT_SLOTS and not other_intent:
        remainder = text
        for key in ("order_id", "phone", "amount"):
            if entities.get(key):
                remainder = remainder.replace(entities[key], " ")
        if _clean(remainder) and (answer := answer_to(asked, text)) is not None:
            found[asked] = answer
            return found
    if "reason" in wanted and (reason := reason_from(text)) is not None:
        found["reason"] = reason  # what is wrong, said in the request itself
    if "description" in wanted:
        # A complaint needs a real description. "Hi, I have a problem with my order" says nothing yet, so a short
        # clause that only announces a problem is not taken; the agent then asks what happened.
        described = entities.get("reason") or reason_from(text)
        if described and not _only_announces_a_problem(described):
            found["description"] = described
    if "new_address" in wanted and (address := address_from(text)) is not None:
        found["new_address"] = address
    return found
