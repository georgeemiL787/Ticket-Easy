"""The word lists in data/lexicon/default.json (shared by language detection and, later, understanding)."""

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from team_b.brain.text import normalize
from team_b.config import PROJECT_ROOT

DEFAULT_LEXICON_PATH = PROJECT_ROOT / "data" / "lexicon" / "default.json"


class Lexicon(BaseModel):
    """Unknown keys are ignored here so each step can read the part of the file it owns."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    arabizi_tokens: frozenset[str] = frozenset()  # Arabic words written in Latin letters, normalized, no spaces

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
