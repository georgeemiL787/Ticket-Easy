"""Shop-action formats: what a tool is, how we call it, what comes back."""

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from team_b.contracts.base import OperationKind, PlugModel, RequestModel, RiskLevel


class ToolSpec(PlugModel):
    """One action the shop offers. capability is the business meaning (e.g. create_refund), name is the tool id."""

    name: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    title: str = ""
    description: str = ""
    operation_kind: OperationKind
    risk: RiskLevel = "medium"
    requires_identity: bool = True
    exposes_personal_data: bool = True
    human_only: bool = False
    enabled: bool = True
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)


class ToolCallRequest(RequestModel):
    request_id: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1)  # the do-it-once key: a repeat can never act twice
    policy_request_id: str | None = None  # the check_action call that allowed this
    approval_id: str | None = None  # the human approval, when the rules needed one
    actor: Literal["customer", "human"] = "customer"


class ToolResult(PlugModel):
    """Outcome of one tool call. write_may_have_applied marks an unclear result: never report it as done or failed."""

    status: Literal["success", "error"]
    data: dict[str, Any] = Field(default_factory=dict)
    audit_id: str | None = None
    reference_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    write_may_have_applied: bool = False

    @model_validator(mode="after")
    def _status_matches_fields(self) -> Self:
        if self.status == "error" and not self.error_code:
            raise ValueError("an error result needs an error_code")
        if self.status == "success" and (self.error_code or self.write_may_have_applied):
            raise ValueError("a success result cannot carry an error_code or write_may_have_applied")
        return self
