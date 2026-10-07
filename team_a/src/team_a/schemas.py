"""Public contracts owned by Team A: RetrievalResult, Rule and PolicyDecision.

Frozen in week 1. Any breaking change bumps SCHEMA_VERSION and is announced to Teams B and C.
"""

from datetime import date, datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from team_a import SCHEMA_VERSION

TENANT_PATTERN = r"^[a-z0-9_]{1,64}$"

ErrorCode = Literal[
    "INVALID_REQUEST",
    "UNAUTHORIZED",
    "TENANT_NOT_FOUND",
    "NOT_FOUND",
    "INDEX_NOT_BUILT",
    "EMBEDDING_UNAVAILABLE",
    "LLM_UNAVAILABLE",
    "INTERNAL_ERROR",
]


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    request_id: Optional[str] = None


class ErrorResponse(BaseModel):
    schema_version: str = SCHEMA_VERSION
    error: ErrorBody


class RequestEnvelope(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(pattern=TENANT_PATTERN)
    conversation_id: Optional[str] = Field(default=None, max_length=128)


# ---------------------------------------------------------------- knowledge


class Passage(BaseModel):
    passage_id: str
    document_id: str
    version: str
    section: str
    language: Literal["ar", "en", "mixed"]
    text: str
    score: float
    citation: str


class SearchKnowledgeRequest(RequestEnvelope):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    include_superseded: bool = False


EmptyReason = Literal["below_threshold", "no_documents"]


class RetrievalResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    request_id: str
    tenant_id: str
    query: str
    passages: list[Passage]
    empty_reason: Optional[EmptyReason] = None
    retrieval_mode: Literal["hybrid", "keyword_only"] = "hybrid"

    @model_validator(mode="after")
    def _empty_reason_iff_no_passages(self):
        if not self.passages and self.empty_reason is None:
            raise ValueError("empty_reason is required when passages is empty")
        if self.passages and self.empty_reason is not None:
            raise ValueError("empty_reason must be null when passages are returned")
        return self


class PastTicket(BaseModel):
    ticket_id: str
    category: str
    customer_message: str
    resolution: str
    created_at: date
    score: float
    citation: str


class SearchPastTicketsRequest(RequestEnvelope):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=10)


class PastTicketResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    request_id: str
    tenant_id: str
    query: str
    tickets: list[PastTicket]
    empty_reason: Optional[EmptyReason] = None


# ------------------------------------------------------------------- policy

Effect = Literal["allow", "deny", "require_human"]
ApprovalStatus = Literal["proposed", "approved", "rejected"]
Operator = Literal["<=", "<", ">=", ">", "==", "!=", "in", "not_in"]


class Condition(BaseModel):
    """`field` is read from verified backend `facts` by default. Use `from_: "arguments"` only for
    values the agent proposes (e.g. a requested refund amount); facts can never come from arguments."""

    field: str = Field(pattern=r"^[a-z_][a-z0-9_]*$")
    op: Operator
    value: Any
    from_: Literal["facts", "arguments"] = Field(default="facts", alias="from")

    model_config = {"populate_by_name": True, "serialize_by_alias": True}

    @model_validator(mode="after")
    def _list_ops_need_lists(self):
        if self.op in ("in", "not_in") and not isinstance(self.value, list):
            raise ValueError(f"operator {self.op!r} needs a list value")
        return self


class RuleSource(BaseModel):
    document_id: str
    version: str
    citation: str
    quote: str = Field(description="Exact source span the rule was derived from")


class LocalizedText(BaseModel):
    ar: str
    en: str


class Rule(BaseModel):
    """An enforceable constraint derived from policy text.

    The rule only takes part when every `applies_if` condition holds (e.g. the 14-day refund
    window applies to delivered orders, not to orders cancelled before shipping). Then, if every
    condition holds the rule yields `effect`, otherwise `else_effect`. A rule with no conditions
    always yields `effect`. Only `approved` rules whose `effective_date` has passed are used.
    """

    rule_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{2,63}$")
    tenant_id: str = Field(pattern=TENANT_PATTERN)
    source: RuleSource
    scope: str
    action: str = Field(description="Tool name the rule governs, e.g. create_refund")
    applies_if: list[Condition] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    effect: Effect
    else_effect: Effect = "deny"
    user_message: LocalizedText
    approval_status: ApprovalStatus = "proposed"
    effective_date: date
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _approval_is_attributed(self):
        if self.approval_status == "approved" and not self.approved_by:
            raise ValueError("approved rules must record approved_by")
        return self


class ToolContext(BaseModel):
    """What Team C's ToolSpec says about the tool being called."""

    name: str
    operation_kind: Literal["read", "create", "update", "delete"]
    risk: Literal["low", "medium", "high"] = "medium"
    exposes_personal_data: bool = True

    @property
    def has_side_effects(self) -> bool:
        return self.operation_kind != "read"


class IdentityContext(BaseModel):
    verified: bool = False
    customer_id: Optional[str] = None
    method: Optional[str] = None


class CheckActionRequest(RequestEnvelope):
    tool: ToolContext
    arguments: dict[str, Any] = Field(default_factory=dict)
    facts: dict[str, Any] = Field(
        default_factory=dict,
        description="Verified facts from backend records, e.g. order_status, delivered_at, amount",
    )
    identity: IdentityContext = Field(default_factory=IdentityContext)
    resource_tenant_id: Optional[str] = Field(
        default=None, description="Tenant that owns the order/customer record being touched"
    )
    risk_categories: list[str] = Field(
        default_factory=list, description="Output of classify_risk for this conversation"
    )
    as_of: Optional[date] = Field(default=None, description="Evaluation date; defaults to today")


ReasonCode = Literal[
    "RULES_PASSED",
    "RULE_BLOCKED",
    "RULE_REQUIRES_HUMAN",
    "NO_RULE_READ_ONLY",
    "NO_RULE_SIDE_EFFECT",
    "HIGH_RISK_DEFAULT",
    "MISSING_CONTEXT",
    "IDENTITY_REQUIRED",
    "TENANT_MISMATCH",
    "MANDATORY_RISK",
]


class RuleOutcome(BaseModel):
    rule_id: str
    predicate: str
    held: Optional[bool] = Field(description="null when a required fact was missing")
    effect_applied: Effect
    citation: str


class PolicyDecision(BaseModel):
    schema_version: str = SCHEMA_VERSION
    request_id: str
    tenant_id: str
    conversation_id: Optional[str] = None
    action: str
    decision: Effect
    reason_code: ReasonCode
    rationale: str
    rule_outcomes: list[RuleOutcome] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    user_message: Optional[LocalizedText] = None
    missing_fields: list[str] = Field(default_factory=list)
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


RiskCategory = Literal[
    "fraud_suspected",
    "legal_regulatory",
    "medical_safety",
    "compensation_demand",
    "identity_concern",
]
MANDATORY_ESCALATION: frozenset[str] = frozenset(RiskCategory.__args__)


class ClassifyRiskRequest(RequestEnvelope):
    message: str = Field(min_length=1, max_length=4000)
    use_llm: bool = True


class RiskAssessment(BaseModel):
    schema_version: str = SCHEMA_VERSION
    request_id: str
    tenant_id: str
    categories: list[RiskCategory]
    mandatory_escalation: bool
    method: Literal["keywords", "keywords+llm"]
    matched_terms: list[str] = Field(default_factory=list)
    llm_rationale: Optional[str] = None


class RuleExplanation(BaseModel):
    schema_version: str = SCHEMA_VERSION
    rule_id: str
    tenant_id: str
    active: bool
    approval_status: ApprovalStatus
    summary: str
    predicate: str
    effect: Effect
    else_effect: Effect
    source: RuleSource
    user_message: LocalizedText
