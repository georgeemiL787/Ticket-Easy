"""The word lists in data/lexicon/default.json: language detection and rule-based understanding read them.

Lists are written per style (en, ar = Egyptian Arabic, arabizi, mixed = code-switched phrases). Every term is
normalized when the file is loaded, so the file can be written naturally and still match spelling variants.
"""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from team_b.brain.text import normalize
from team_b.config import PROJECT_ROOT

DEFAULT_LEXICON_PATH = PROJECT_ROOT / "data" / "lexicon" / "default.json"


class StyleWords(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    en: tuple[str, ...] = ()
    ar: tuple[str, ...] = ()
    arabizi: tuple[str, ...] = ()
    mixed: tuple[str, ...] = ()

    @field_validator("en", "ar", "arabizi", "mixed", mode="before")
    @classmethod
    def _normalized(cls, value: object) -> object:
        if isinstance(value, list | tuple):
            return tuple(dict.fromkeys(n for n in (normalize(str(term)) for term in value) if n))
        return value

    def all(self) -> tuple[str, ...]:
        """Every term of every style, normalized, each once, longest first (so phrases are tried before words)."""
        merged = dict.fromkeys(term for style in (self.en, self.ar, self.arabizi, self.mixed) for term in style)
        return tuple(sorted(merged, key=lambda term: (-len(term), term)))

    def size(self) -> int:
        return len(self.all())


class FrustrationWords(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    strong: StyleWords = StyleWords()
    medium: StyleWords = StyleWords()


class Lexicon(BaseModel):
    """Unknown keys are ignored here so each step can read the part of the file it owns."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    arabizi_tokens: frozenset[str] = frozenset()  # Arabic words written in Latin letters, normalized, no spaces
    intents: dict[str, StyleWords] = {}  # intent name -> keywords that signal it
    want_markers: StyleWords = StyleWords()  # "I want", "عايز", "3ayez"
    question_markers: StyleWords = StyleWords()  # "how", "ازاي", "emta", "?"
    policy_timing_markers: StyleWords = StyleWords()  # "how many days", "كام يوم": asks about a rule, not for an action
    yes: StyleWords = StyleWords()
    no: StyleWords = StyleWords()
    filler: StyleWords = StyleWords()  # words that may surround a yes or no without changing it
    human_markers: StyleWords = StyleWords()
    negation: StyleWords = StyleWords()
    frustration: FrustrationWords = FrustrationWords()

    @field_validator("arabizi_tokens", mode="before")
    @classmethod
    def _normalized(cls, value: object) -> object:
        if isinstance(value, list):
            return frozenset(normalize(str(token)) for token in value if str(token).strip())
        return value


def load_lexicon(path: Path) -> Lexicon:
    return Lexicon.model_validate(json.loads(path.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def default_lexicon() -> Lexicon:
    return load_lexicon(DEFAULT_LEXICON_PATH)
