"""Several requests in one message, each about its own order: "where is NS-20955, change its address, refund NS-20701".

OWNER: Track A.

The understanding step lists the intents in the order they were said and the order numbers found. When the message holds
more than one order number, this module decides which request each number belongs to, by position: a number belongs to
the request whose keyword comes last before it (a number before the first keyword goes to the first request). A request
with no number of its own is about the same order as the request before it. A message with a single order number needs
none of this: every request uses it.
"""

from collections.abc import Sequence

from team_b.brain.lexicon import default_lexicon
from team_b.brain.nlu import extract_order_ids
from team_b.brain.text import find_spans, normalize
from team_b.domain.tenant import TenantConfig


def _first_keyword_position(normalized: str, intent: str) -> int | None:
    words = default_lexicon().intents.get(intent)
    if words is None:
        return None
    starts = [s for term in words.all() for s, _ in find_spans(normalized, normalize(term))]
    return min(starts) if starts else None


def assign_order_ids(text: str, intents: Sequence[str], tenant: TenantConfig) -> dict[str, str]:
    """intent -> its order number, only when the message names two or more different orders. Otherwise empty."""
    normalized = normalize(text)
    ids = extract_order_ids(normalized, tenant)
    if len({order for _, _, order in ids}) < 2:
        return {}
    placed = [
        (pos, name) for name in dict.fromkeys(intents) if (pos := _first_keyword_position(normalized, name)) is not None
    ]
    placed.sort()
    if not placed:
        return {}
    chosen: dict[str, str] = {}
    last: str | None = None
    for index, (position, name) in enumerate(placed):
        end = placed[index + 1][0] if index + 1 < len(placed) else len(normalized) + 1
        start = 0 if index == 0 else position
        own = next((order for s, _, order in ids if start <= s < end), None)
        last = own or last
        if last is not None:
            chosen[name] = last
    return chosen
