from dataclasses import dataclass, field
from typing import TypedDict


class AttemptInfo(TypedDict):
    """A failed attempt: stored on the attempt row and listed in providers_failed details."""
    code: str
    message: str
    provider: str


@dataclass(frozen=True)
class ProviderResponse:
    """A provider's finished answer text before parsing; usage is recorded in the model_response diagnostic."""
    raw: str
    usage: dict = field(default_factory=dict)
