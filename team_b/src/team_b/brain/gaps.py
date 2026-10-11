"""Questions the agent could not answer, grouped so a manager sees "12 customers asked about warranty", not 12 rows.

Two questions are the same when their sets of meaningful words overlap enough (Jaccard similarity of the normalized
token sets). Word order, case, Arabic letter variants and digits do not matter.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from team_b.brain.text import normalize
from team_b.domain.base import FrozenModel

SIMILARITY = 0.5  # share of words two questions must have in common (intersection over union)
MAX_EXAMPLES = 3
_WORD = re.compile(r"\w+")
_FILLER = frozenset(
    {"a", "an", "the", "is", "are", "do", "does", "you", "your", "i", "me", "my", "we", "can", "to", "of", "for", "on",
     "in", "it", "and", "or", "what", "how", "any", "please", "pls", "hi", "hello",
     "هل", "في", "من", "علي", "عن", "ده", "دي", "انا", "ممكن", "لو", "ايه",
     "el", "w", "fi", "da", "di", "ana", "momken", "3ayez", "3ayza"}
)  # fmt: skip


class QuestionGroup(FrozenModel):
    question: str  # the first question of the group, as the customer wrote it (personal values already hidden)
    count: int
    examples: tuple[str, ...]
    last_seen: datetime


def tokens(text: str) -> frozenset[str]:
    """The meaningful words of a question, normalized."""
    return frozenset(w for w in _WORD.findall(normalize(text)) if len(w) > 1 and w not in _FILLER)


def similar(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= SIMILARITY


@dataclass
class _Cluster:
    words: frozenset[str]
    texts: list[str]
    last: datetime


def group_questions(questions: Sequence[tuple[str, datetime]]) -> list[QuestionGroup]:
    """Group (text, when) pairs, biggest group first then the most recent; a text without meaningful words is alone."""
    clusters: list[_Cluster] = []
    for text, when in sorted(questions, key=lambda q: q[1]):
        words = tokens(text)
        home = next((c for c in clusters if similar(words, c.words)), None)
        if home is None:
            clusters.append(_Cluster(words, [text], when))
        else:
            home.texts.append(text)
            home.last = max(home.last, when)
    clusters.sort(key=lambda c: (-len(c.texts), -c.last.timestamp()))
    return [
        QuestionGroup(
            question=c.texts[0], count=len(c.texts), examples=tuple(dict.fromkeys(c.texts))[:MAX_EXAMPLES],
            last_seen=c.last,
        )
        for c in clusters
    ]  # fmt: skip
