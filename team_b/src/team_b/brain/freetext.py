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


def reason_from(text: str) -> str | None:
    """The reason in a message that also makes a request, or None."""
    marked = _REASON_MARKERS.search(text)
    if marked is not None:
        value = _clean(marked.group(1))
        return value or None
    clauses = [_clean(c) for c in _CLAUSE_SPLIT.split(text)]
    reasons = [c for c in clauses[1:] if len(c.split()) >= 2 and not _is_request_or_detail(c)]
    return _clean(", ".join(reasons)) or None


_ADDRESS_AFTER = re.compile(r"(?:\s+to\s+|(?:^|\s)(?:لـ|الى|إلى|الي)\s*)([0-9٠-٩].*)$", re.IGNORECASE)


def address_from(text: str) -> str | None:
    """A street address after "to" / "لـ" / "الى" when it starts with a street number ("5 Nile Corniche"), or None."""
    found = _ADDRESS_AFTER.search(text.strip())
    if found is None:
        return None
    value = _clean(found.group(1))
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
        found["reason"] = reason
    if "new_address" in wanted and (address := address_from(text)) is not None:
        found["new_address"] = address
    return found
