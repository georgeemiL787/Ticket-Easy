"""What the brain understood from one customer message."""

from enum import StrEnum
from typing import Literal

from pydantic import Field

from team_b.domain.base import FrozenModel

Frustration = Literal["low", "medium", "high"]
NluMethod = Literal["rules", "llm", "rules_fallback"]


class Language(StrEnum):
    """Language style of the customer message."""

    EN = "en"
    AR = "ar"  # Egyptian Arabic in Arabic script
    MIXED = "mixed"  # Arabic and English in the same message
    ARABIZI = "arabizi"  # Arabic written in Latin letters and digits


class Locale(StrEnum):
    """Reply style. Mixed-language customers get Egyptian Arabic, so there is no mixed locale."""

    EN = "en"
    AR = "ar"
    ARABIZI = "arabizi"


class IntentCandidate(FrozenModel):
    name: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class NLUResult(FrozenModel):
    language: Language
    language_confidence: float = Field(ge=0.0, le=1.0)
    intents: tuple[IntentCandidate, ...] = ()
    entities: dict[str, str] = Field(default_factory=dict)  # order_id, phone, amount, ... as written by the customer
    affirmation: Literal["yes", "no"] | None = None
    wants_human: bool = False
    frustration: Frustration = "low"
    safety_flags: tuple[str, ...] = ()
    method: NluMethod = "rules"
    prompt_version: str | None = None  # which prompt file the AI model was given, when it was used
