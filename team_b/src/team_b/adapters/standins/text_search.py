"""Small text-search engine for the policy search stand-in: normalizing, synonym expansion and BM25-like scoring.

It handles English, Egyptian Arabic and Arabizi (Arabic written with Latin letters and digits, like 3ayez or araga3).
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field

_DIACRITICS = re.compile(r"[\u064b-\u065f\u0670\u0640]")  # tashkeel and tatweel
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_ARABIC_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"})
_TOKEN = re.compile(r"[a-z0-9\u0621-\u064a]+")  # Arabic letters only: no question mark or comma
_REPEATS = re.compile(r"(.)\1{2,}")

STOPWORDS = {
    # English
    "a", "an", "the", "is", "are", "was", "were", "be", "do", "does", "did", "i", "me", "my", "we", "you", "your",
    "it", "its", "of", "to", "in", "on", "at", "for", "and", "or", "can", "could", "would", "will", "please", "pls",
    "plz", "what", "how", "when", "where", "which", "who", "this", "that", "there", "have", "has", "if", "with",
    # Arabic (normalized)
    "في", "من", "علي", "عن", "الي", "هل", "ما", "هو", "هي", "هذا", "هذه", "ده", "دي", "دا", "انا", "انت", "عايز",
    "عايزه", "عاوز", "عاوزه", "ممكن", "لو", "ايه", "اي", "يا", "و", "بس", "كمان", "لما", "علشان", "عشان", "ازاي",
    # Arabizi
    "el", "al", "w", "fi", "fy", "ma", "mesh", "msh", "ana", "enta", "ya", "dah", "di", "da", "eh", "ezay", "lw",
    "law", "3shan", "3ashan", "min", "3ala", "3n", "elly", "ely", "3ayez", "3ayza", "3ayzeen", "3awz", "3aiz",
}  # fmt: skip
_SUFFIXES = ("ing", "ed", "es", "s")
# Question and time words appear in every kind of question, so a match on them alone proves little.
WEAK_WORDS = {"yom", "youm", "yoom", "يوم", "feen", "fein", "fen", "فين", "kam", "كام", "emta", "امتي", "today", "now"}
WEAK_WEIGHT = 0.25


def normalize(text: str) -> str:
    """Lowercase, unify Arabic letter forms, drop diacritics and punctuation, shorten stretched letters."""
    text = text.lower().translate(_DIGITS).replace("'", "").replace("’", "")
    text = _DIACRITICS.sub("", text).translate(_ARABIC_LETTERS)
    text = _REPEATS.sub(r"\1\1", text)
    return " ".join(_TOKEN.findall(text))


def stem(token: str) -> str:
    """Very light stemming. Arabizi tokens (letters mixed with digits) are left alone."""
    if token.isascii() and token.isalpha() and len(token) > 3:
        for suffix in _SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= 3 and not token.endswith("ss"):
                token = token[: -len(suffix)]
                break
        if len(token) > 3 and token[-1] == token[-2] and token[-1] not in "lsz":
            token = token[:-1]
        if len(token) > 3 and token.endswith("e"):
            token = token[:-1]
    elif token.startswith(("وال", "بال", "فال", "كال")) and len(token) > 5:
        token = token[3:]
    elif token.startswith("ال") and len(token) > 4:
        token = token[2:]
    return token


def tokens(text: str, *, keep_stopwords: bool = False) -> list[str]:
    return [stem(t) for t in normalize(text).split() if keep_stopwords or t not in STOPWORDS]


def build_synonyms(entries: list[dict[str, list[str]]]) -> dict[str, list[str]]:
    """Map every variant (one or two words, normalized) to the stemmed terms it means."""
    table: dict[str, list[str]] = {}
    for entry in entries:
        means = [t for m in entry["means"] for t in tokens(m)]
        for variant in entry["variants"]:
            key = normalize(variant)
            if key:
                table.setdefault(key, [])
                table[key] += [m for m in means if m not in table[key]]
    return table


@dataclass
class Document:
    doc_id: str
    fields: list[tuple[str, float]]  # (text, weight): a word in the keywords counts more than one in the body
    phrases: list[str] = field(default_factory=list)  # whole keyword phrases, matched as a unit


@dataclass
class Hit:
    doc_id: str
    score: float
    matched: list[str]
    coverage: float  # share of the question content words that this document covers (0 to 1)
    words: int = 0  # how many content words the question has


class Index:
    K1, B, PHRASE_BONUS, EXPANSION_WEIGHT = 1.5, 0.75, 2.5, 0.6

    def __init__(self, documents: list[Document], synonyms: dict[str, list[str]]) -> None:
        self._synonyms = synonyms
        self._docs = documents
        self._tf: list[dict[str, float]] = []
        self._phrases: list[list[str]] = []
        for doc in documents:
            tf: dict[str, float] = {}  # weighted counts, so floats
            for text, weight in doc.fields:
                for token in tokens(text):
                    tf[token] = tf.get(token, 0.0) + weight
            self._tf.append(tf)
            self._phrases.append([normalize(p) for p in doc.phrases if len(normalize(p).split()) > 1])
        self._lengths = [sum(tf.values()) for tf in self._tf]
        self._avg = sum(self._lengths) / max(1, len(self._lengths))
        df: Counter[str] = Counter(term for tf in self._tf for term in tf)
        n = len(documents)
        self._idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def query_terms(self, query: str) -> tuple[dict[str, float], list[set[str]]]:
        """Words of the query (weight 1) plus what the synonym table says they mean (weight 0.6).

        Also returns one group of acceptable terms per content word, to measure how much of the question is covered.
        """
        words = normalize(query).split()
        terms: dict[str, float] = {}
        groups: dict[int, set[str]] = {}
        for i, t in enumerate(words):
            if t not in STOPWORDS:
                terms[stem(t)] = WEAK_WEIGHT if t in WEAK_WORDS else 1.0
                if t not in WEAK_WORDS:
                    groups[i] = {stem(t), *self._synonyms.get(t, [])}
        for i, (a, b) in enumerate(zip(words, words[1:], strict=False)):
            for meant in self._synonyms.get(f"{a} {b}", []):
                for j in (i, i + 1):
                    if j in groups:
                        groups[j].add(meant)
        for t in words:
            for meant in self._synonyms.get(t, []):
                terms.setdefault(meant, self.EXPANSION_WEIGHT * (WEAK_WEIGHT if t in WEAK_WORDS else 1.0))
        for i in range(len(words) - 1):
            for meant in self._synonyms.get(f"{words[i]} {words[i + 1]}", []):
                terms.setdefault(meant, self.EXPANSION_WEIGHT)
        return terms, list(groups.values())

    def search(self, query: str) -> list[Hit]:
        terms, groups = self.query_terms(query)
        padded = f" {normalize(query)} "
        hits: list[Hit] = []
        for i, doc in enumerate(self._docs):
            tf, norm = self._tf[i], 1 - self.B + self.B * self._lengths[i] / max(self._avg, 1e-9)
            score, matched = 0.0, []
            for term, weight in terms.items():
                if term in tf:
                    score += weight * self._idf[term] * tf[term] * (self.K1 + 1) / (tf[term] + self.K1 * norm)
                    matched.append(term)
            score += sum(self.PHRASE_BONUS for p in self._phrases[i] if f" {p} " in padded)
            if score > 0:
                covered = sum(1 for g in groups if any(t in tf for t in g))
                hits.append(Hit(doc.doc_id, round(score, 4), matched, covered / max(1, len(groups)), len(groups)))
        return sorted(hits, key=lambda h: (-h.score, h.doc_id))
