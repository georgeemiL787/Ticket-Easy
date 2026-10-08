"""AI-assisted understanding: the model reads the message, and code decides what to believe.

The rule-based understanding always runs first and is the safety floor. The model is asked for the same structured
reading; its answer is then checked field by field:
- intents not in the tenant's catalog are dropped, and the model never chooses a tool (any other field is ignored);
- order id and phone found by the patterns win over the model, and a detail the model reports must really occur in the
  message (no invented order ids, phones or amounts);
- wants_human, frustration and safety flags are the stronger of rules and model: the model can raise them, never
  clear them;
- yes/no from the model is accepted only for a very short message (it can start a confirmation);
- an answer that is not valid JSON gets one repair attempt; after that, or on a timeout or any other error, the rules'
  result is returned with method "rules_fallback".
Phones, emails and card numbers are hidden from the model, and the customer text is marked as data, not instructions.
"""

import re
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from team_b.brain.language import LANGUAGE_TRUST
from team_b.brain.nlu import RuleBasedNLU, extract_order_ids, extract_phone
from team_b.brain.redaction import redact
from team_b.brain.text import normalize
from team_b.config import PROJECT_ROOT
from team_b.contracts.errors import InvalidLLMOutput
from team_b.domain.base import FrozenModel
from team_b.domain.session import SessionState
from team_b.domain.tenant import TenantConfig
from team_b.domain.understanding import Frustration, IntentCandidate, Language, NLUResult
from team_b.observability import get_logger
from team_b.ports import LLMClient

log = get_logger(__name__)
PROMPTS_DIR = PROJECT_ROOT / "prompts"
DEFAULT_PROMPT = "nlu_v2"
ENTITY_KEYS = ("order_id", "phone", "amount", "item", "reason")
MAX_ENTITY_LENGTH = 200
HISTORY_TURNS = 4  # a turn is a customer message and the reply to it
SHORT_MESSAGE_WORDS = 4  # the model may only answer yes/no for a message this short
FRUSTRATION_RANK: dict[Frustration, int] = {"low": 0, "medium": 1, "high": 2}
_FLAG = re.compile(r"^[a-z][a-z_]{1,39}$")
_DIGITS = re.compile(r"\d+")
_WORD = re.compile(r"[\w']+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
SCHEMA_HINT: dict[str, Any] = {
    "language": "en|ar|mixed|arabizi",
    "intents": [{"name": "intent name from the catalog", "confidence": 0.0}],
    "entities": {key: None for key in ENTITY_KEYS},
    "affirmation": None,
    "wants_human": False,
    "frustration": "low|medium|high",
    "safety_flags": [],
}


class SyncNLU(Protocol):
    def understand_sync(self, text: str, session: SessionState | None, tenant: TenantConfig) -> NLUResult: ...


class PromptTemplate(FrozenModel):
    version: str
    system: str
    user: str

    @classmethod
    def load(cls, name: str = DEFAULT_PROMPT, directory: Path = PROMPTS_DIR) -> "PromptTemplate":
        text = (directory / f"{name}.md").read_text(encoding="utf-8")
        sections = re.split(r"(?im)^##\s*(system|user)\s*$", text)
        found = {sections[i].lower(): sections[i + 1].strip() for i in range(1, len(sections) - 1, 2)}
        if "system" not in found or "user" not in found:
            raise ValueError(f"prompt {name} needs '## system' and '## user' sections")
        return cls(version=name, system=found["system"], user=found["user"])

    def render_user(self, **values: str) -> str:
        out = self.user
        for key, value in values.items():
            out = out.replace("{{" + key + "}}", value)
        return out


class _Intent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    confidence: float = 0.5

    @field_validator("confidence", mode="after")
    @classmethod
    def _clamp(cls, value: float) -> float:
        return min(1.0, max(0.0, value))


class _Reading(BaseModel):
    """What the model said. Anything not listed here (a tool, an action, an extra field) is ignored on purpose."""

    model_config = ConfigDict(extra="ignore")

    language: str | None = None
    intents: list[_Intent] = Field(default_factory=list)
    entities: dict[str, Any] = Field(default_factory=dict)
    affirmation: Literal["yes", "no"] | None = None
    wants_human: bool = False
    frustration: Frustration = "low"
    safety_flags: list[str] = Field(default_factory=list)


def _digits(text: str) -> str:
    return "".join(_DIGITS.findall(normalize(text)))


class LLMNLU:
    def __init__(self, llm: LLMClient, *, rules: SyncNLU | None = None, prompt: PromptTemplate | None = None) -> None:
        self._llm = llm
        self._rules: SyncNLU = rules or RuleBasedNLU()
        self._prompt = prompt or PromptTemplate.load()

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def understand(self, text: str, session: SessionState | None, tenant: TenantConfig) -> NLUResult:
        base = self._rules.understand_sync(text, session, tenant)
        try:
            reading = await self._ask(text, session, tenant)
        except Exception as exc:  # timeout, outage, nonsense twice: the rules' answer stands
            log.warning("nlu_fallback", reason=type(exc).__name__, prompt=self._prompt.version)
            return base.model_copy(update={"method": "rules_fallback", "prompt_version": self._prompt.version})
        return self._merge(base, reading, text, tenant)

    # ---- asking ----

    def _user_prompt(self, text: str, session: SessionState | None, tenant: TenantConfig) -> str:
        catalog = "\n".join(
            f"- {name}: {spec.description} (examples: {' | '.join(spec.examples)})"
            for name, spec in tenant.intents.items()
        )
        history = ""
        summary = "(none)"
        if session is not None:
            summary = session.history_summary or "(none)"
            recent = session.history[-2 * HISTORY_TURNS :]
            history = "\n".join(f"{m.role}: {redact(m.text)}" for m in recent)
        return self._prompt.render_user(
            catalog=catalog, summary=summary, history=history or "(no earlier messages)", message=redact(text)
        )

    async def _ask(self, text: str, session: SessionState | None, tenant: TenantConfig) -> _Reading:
        user = self._user_prompt(text, session, tenant)
        try:
            return await self._call(user)
        except (InvalidLLMOutput, ValidationError, ValueError) as first:
            raw = first.raw if isinstance(first, InvalidLLMOutput) else str(first)
            repair = (
                "Your previous answer could not be used. Answer again with ONLY the JSON object, nothing else.\n\n"
                f"Previous answer:\n{raw[:2000]}\n\nOriginal task:\n{user}"
            )
            return await self._call(repair)  # a second failure goes to the caller, which falls back to the rules

    async def _call(self, user: str) -> _Reading:
        data = await self._llm.complete_json(system=self._prompt.system, user=user, schema_hint=SCHEMA_HINT)
        return _Reading.model_validate(data)

    # ---- believing ----

    def _merge(self, base: NLUResult, reading: _Reading, text: str, tenant: TenantConfig) -> NLUResult:
        intents = self._intents(base, reading, tenant)
        wants_human = base.wants_human or reading.wants_human
        if wants_human and "human_request" in tenant.intents and all(i.name != "human_request" for i in intents):
            intents = (*intents, IntentCandidate(name="human_request", confidence=0.7))
        language, confidence = base.language, base.language_confidence
        if confidence < LANGUAGE_TRUST and (guess := self._language(reading.language)) is not None:
            language, confidence = guess, LANGUAGE_TRUST
        return NLUResult(
            language=language,
            language_confidence=confidence,
            intents=intents,
            entities=self._entities(base, reading, text, tenant),
            affirmation=self._affirmation(base, reading, text),
            wants_human=wants_human,
            frustration=max(base.frustration, reading.frustration, key=FRUSTRATION_RANK.__getitem__),
            safety_flags=self._flags(base, reading),
            method="llm",
            prompt_version=self._prompt.version,
        )

    @staticmethod
    def _language(value: str | None) -> Language | None:
        try:
            return Language(value) if value else None
        except ValueError:
            return None

    @staticmethod
    def _intents(base: NLUResult, reading: _Reading, tenant: TenantConfig) -> tuple[IntentCandidate, ...]:
        """The model's intents that exist in the catalog; if none survive, the rules' intents."""
        kept: dict[str, IntentCandidate] = {}
        for item in reading.intents:
            if item.name in tenant.intents and item.name not in kept:
                kept[item.name] = IntentCandidate(name=item.name, confidence=round(item.confidence, 2))
        return tuple(kept.values()) or base.intents

    @staticmethod
    def _entities(base: NLUResult, reading: _Reading, text: str, tenant: TenantConfig) -> dict[str, str]:
        message_digits = _digits(text)
        entities: dict[str, str] = {}
        for key in ENTITY_KEYS:
            value = reading.entities.get(key)
            if not isinstance(value, str | int | float) or isinstance(value, bool) or not str(value).strip():
                continue
            cleaned = _CONTROL.sub(" ", str(value)).strip()[:MAX_ENTITY_LENGTH]
            if key == "order_id":
                ids = extract_order_ids(normalize(cleaned), tenant)
                cleaned = ids[0][2] if ids else ""
                if not cleaned or _digits(cleaned) not in message_digits:
                    continue
            elif key == "phone":
                phone = extract_phone(normalize(cleaned))
                if phone is None or phone[-8:] not in message_digits:
                    continue
                cleaned = phone
            elif key == "amount":
                number = _digits(cleaned.replace(",", ""))
                if not number or number not in message_digits:
                    continue
                cleaned = cleaned.replace(",", "")
            elif any(d not in message_digits for d in _DIGITS.findall(normalize(cleaned))):
                continue  # free text may not carry numbers the customer never wrote
            entities[key] = cleaned
        entities.update({k: v for k, v in base.entities.items() if k in ("order_id", "order_ids", "phone")})
        if "amount" not in entities and "amount" in base.entities:
            entities["amount"] = base.entities["amount"]
        return entities

    @staticmethod
    def _affirmation(base: NLUResult, reading: _Reading, text: str) -> Literal["yes", "no"] | None:
        if base.affirmation is not None:
            return base.affirmation
        short = 0 < len(_WORD.findall(text)) <= SHORT_MESSAGE_WORDS
        return reading.affirmation if short else None

    @staticmethod
    def _flags(base: NLUResult, reading: _Reading) -> tuple[str, ...]:
        model_flags = [f.strip().lower() for f in reading.safety_flags if _FLAG.match(f.strip().lower())]
        return tuple(dict.fromkeys([*base.safety_flags, *model_flags]))
