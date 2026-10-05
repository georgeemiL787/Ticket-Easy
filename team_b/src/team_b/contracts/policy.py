"""Rule-checker formats. Mirror team_a/contracts/schemas (CheckActionRequest, PolicyDecision)."""

from datetime import date, datetime
from typing import Any, Literal, Self

from pydantic import AliasChoices, Field, model_validator

from team_b.contracts.base import OperationKind, PlugModel, RequestModel, RiskLevel

PolicyEffect = Literal["allow", "deny", "require_human"]


class ToolContext(RequestModel):
    """What the rule checker needs to know about the tool being used.

    side_effects is derived when missing (anything but a read changes something) and must be true for non-reads.
    personal_data is the Team A field exposes_personal_data (either name is accepted); it defaults to true (fail-safe).
    """

    name: str = Field(min_length=1)
    operation_kind: OperationKind
    side_effects: bool = False
    personal_data: bool = Field(default=True, validation_alias=AliasChoices("personal_data", "exposes_personal_data"))
    risk: RiskLevel = "medium"

    @model_validator(mode="before")
    @classmethod
    def _derive_side_effects(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("side_effects") is None:
            return {**data, "side_effects": data.get("operation_kind") != "read"}
        return data

    @model_validator(mode="after")
    def _non_read_has_side_effects(self) -> Self:
        if self.operation_kind != "read" and not self.side_effects:
            raise ValueError("a create/update/delete tool must have side_effects=true")
        return self


class IdentityContext(RequestModel):
    verified: bool = False
    customer_id: str | None = None
    method: str | None = None


class HumanApproval(RequestModel):
    """A recorded approval by a support person. It can turn require_human into allow, never a deny."""

    approved_by: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    approved_at: datetime


class CheckActionRequest(RequestModel):
    request_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    conversation_id: str | None = Field(default=None, max_length=128)
    action: str = Field(min_length=1)  # capability name, e.g. create_refund
    tool: ToolContext
    identity: IdentityContext = Field(default_factory=IdentityContext)
    facts: dict[str, Any] = Field(default_factory=dict)  # verified backend facts, never customer-supplied
    arguments: dict[str, Any] = Field(default_factory=dict)
    risk_categories: tuple[str, ...] = ()
    as_of: date | None = None
    resource_tenant_id: str | None = None
    human_approval: HumanApproval | None = None


class LocalizedText(PlugModel):
    en: str
    ar: str


class RuleOutcome(PlugModel):
    rule_id: str
    predicate: str
    held: bool | None = None  # None when a required fact was missing
    effect_applied: PolicyEffect
    citation: str = ""


class PolicyDecision(PlugModel):
    """Answer of the rule checker. reason_code is kept as text because Team A may add codes."""

    request_id: str
    tenant_id: str = ""
    conversation_id: str | None = None
    action: str = ""
    decision: PolicyEffect
    reason_code: str
    rationale: str = ""
    rule_outcomes: tuple[RuleOutcome, ...] = ()
    citations: tuple[str, ...] = ()
    user_message: LocalizedText | None = None
    missing_fields: tuple[str, ...] = ()
    evaluated_at: datetime | None = None
