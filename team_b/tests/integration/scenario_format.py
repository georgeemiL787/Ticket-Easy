"""The scenario file format (documented at the top of test_scenarios.py) as strict models: a typo is an error."""

import json
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCENARIO_ROOT = Path(__file__).resolve().parents[2] / "scenarios"
DEFAULT_TODAY = date(2026, 9, 28)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Setup(Strict):
    today: date = DEFAULT_TODAY


class Inject(Strict):
    plug: Literal["shop", "policy_search", "rule_checker", "safety_screen", "llm"]
    operation: str = Field(min_length=1)  # a shop tool name, or a policy_search operation
    mode: Literal["fail", "timeout", "uncertain", "no_audit", "unpublish"]
    code: str | None = None  # error code for mode "fail" (default BACKEND_UNAVAILABLE)
    times: int = Field(default=1, ge=1)


class Human(Strict):
    action: Literal["claim", "reply", "approve", "reject", "resolve", "return_to_agent"]
    text: str = ""


class Expect(Strict):
    decision: str | None = None
    escalation: str | None = None  # an explicit null means "no escalation reason"
    awaiting: str | None = None  # an explicit null means "not waiting for anything"
    locale: str | None = None
    citations_include: list[str] = []
    citations_exclude: list[str] = []
    text_contains: list[str] = []
    text_contains_any: list[str] = []
    text_not_contains: list[str] = []

    @field_validator(
        "citations_include",
        "citations_exclude",
        "text_contains",
        "text_contains_any",
        "text_not_contains",
        mode="before",
    )
    @classmethod
    def _one_string_is_a_list(cls, value: Any) -> Any:
        return [value] if isinstance(value, str) else value


class Turn(Strict):
    say: str | None = None
    human: Human | None = None
    advance_days: int = Field(default=0, ge=0)
    expect: Expect = Field(default_factory=Expect)

    @model_validator(mode="after")
    def _does_something(self) -> "Turn":
        if self.say is None and self.human is None:
            raise ValueError("a turn needs 'say' or 'human'")
        return self


class Final(Strict):
    executed_tools: list[str] | None = None  # successful, non-replayed writes, in order
    audit_count: int | None = None  # write calls that reached the stand-in shop (one executed write = 1)
    no_writes: bool | None = None  # true: no write call reached the shop at all
    case_reason: str | None = None  # an explicit null means "no handoff case"
    case_priority: str | None = None
    case_has_pending_approval: bool | None = None
    trace_invariants: bool | None = None


class Scenario(Strict):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    status: Literal["active", "pending"]
    pending_reason: str | None = None
    setup: Setup = Field(default_factory=Setup)
    inject: list[Inject] = []
    turns: list[Turn] = Field(min_length=1)
    final: Final = Field(default_factory=Final)

    @model_validator(mode="after")
    def _pending_says_why(self) -> "Scenario":
        if self.status == "pending" and not self.pending_reason:
            raise ValueError("a pending scenario needs a pending_reason")
        return self


def load_scenario(path: Path) -> Scenario:
    return Scenario.model_validate(json.loads(path.read_text(encoding="utf-8")))


def discover(root: Path = SCENARIO_ROOT) -> list[tuple[str, Path]]:
    """(tenant_id, file) for every scenarios/<tenant>/*.json, in a stable order."""
    return sorted((p.parent.name, p) for p in root.glob("*/*.json"))
