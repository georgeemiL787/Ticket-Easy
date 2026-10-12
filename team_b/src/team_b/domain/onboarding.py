"""The onboarding wizard's saved state: where the owner is and what they chose. Ids and statuses only, never a secret.

The business profile is also written into a draft tenant bundle (domain/bundle.py) so every later step builds on it;
this record is what lets the wizard resume at the right step after a reload or on another device.
"""

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from team_b.domain.base import FrozenModel
from team_b.domain.bundle import SECRET_NAME, Tone, secret_like_keys
from team_b.domain.understanding import Locale

Step = Literal["profile", "knowledge", "guardrails", "connect", "tools", "review"]
STEPS: tuple[Step, ...] = ("profile", "knowledge", "guardrails", "connect", "tools", "review")


class BusinessProfile(FrozenModel):
    """What the owner tells us about the business on the first screen."""

    name: str = Field(min_length=2, max_length=80)
    locales: tuple[Locale, ...] = (Locale.EN,)
    default_locale: Locale = Locale.EN
    tone: Tone = "friendly"
    order_id_prefix: str = Field(default="", max_length=12, pattern=r"^[A-Za-z0-9-]*$")
    order_id_digits: int = Field(default=6, ge=3, le=12)  # how many digits follow the prefix
    contacts: tuple[str, ...] = Field(default=(), max_length=10)  # names of the people who take over escalated chats

    @field_validator("name")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 2 or any(ord(ch) < 32 for ch in value):
            raise ValueError("the name must be plain text of at least two characters")
        return value

    @field_validator("contacts")
    @classmethod
    def _plain_contacts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(dict.fromkeys(c.strip() for c in value if c.strip()))
        if any(len(c) > 80 or any(ord(ch) < 32 for ch in c) for c in cleaned):
            raise ValueError("a contact is a person's name, at most 80 characters")
        return cleaned

    @model_validator(mode="after")
    def _default_is_offered(self) -> "BusinessProfile":
        if self.default_locale not in self.locales:
            raise ValueError("default_locale must be one of locales")
        return self

    @property
    def order_id_pattern(self) -> str:
        return f"{re.escape(self.order_id_prefix)}\\d{{{self.order_id_digits}}}"


class OnboardingDraft(FrozenModel):
    tenant_id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    step: Step = "profile"
    profile: BusinessProfile
    verify_tool: str | None = None  # the tool the owner marked as "checks who the customer is"
    updated_at: datetime
    updated_by: str = Field(min_length=1)

    @model_validator(mode="after")
    def _no_secrets(self) -> "OnboardingDraft":
        leaked = secret_like_keys(self.model_dump(mode="json"))
        if leaked or (self.verify_tool and SECRET_NAME.search(self.verify_tool)):
            raise ValueError(f"a draft never holds secrets; remove these fields: {leaked or ['verify_tool']}")
        return self
