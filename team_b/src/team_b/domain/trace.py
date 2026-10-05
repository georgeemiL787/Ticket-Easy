"""DecisionTrace: the record of why the brain did what it did, for one message or one human action.

Two invariants are checked on every trace (safety-critical):
1. A handoff always has an escalation reason.
2. Every tool call that is not a read is backed, in the same trace, by a policy entry that allowed it
   (or by require_human plus an approval id on the call). A write without that cannot even be recorded.
"""

from typing import Any, Literal, Self

from pydantic import Field, model_validator

from team_b.contracts.base import OperationKind
from team_b.domain.base import FrozenModel
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.session import SessionIdentity
from team_b.domain.understanding import Frustration, IntentCandidate, Language, NluMethod

TraceKind = Literal["customer_turn", "human_action"]


class EvidenceRef(FrozenModel):
    citation: str
    document_id: str
    version: str
    score: float


class ProposalRecord(FrozenModel):
    proposal_id: str
    tool: str
    state: str


class PermissionRecord(FrozenModel):
    allowed: bool
    reason: str | None = None


class PolicyRecord(FrozenModel):
    request_id: str
    action: str
    decision: Literal["allow", "deny", "require_human"]
    reason_code: str
    citations: tuple[str, ...] = ()


class ToolCallRecord(FrozenModel):
    request_id: str
    tool: str
    operation_kind: OperationKind
    arguments: dict[str, Any] = Field(default_factory=dict)  # redacted before it is stored
    status: Literal["success", "error"]
    error_code: str | None = None
    audit_id: str | None = None
    latency_ms: float = Field(default=0.0, ge=0.0)
    policy_request_id: str | None = None
    approval_id: str | None = None
    actor: Literal["customer", "human"] = "customer"


class TraceStep(FrozenModel):
    stage: str
    status: str
    duration_ms: float = Field(default=0.0, ge=0.0)
    detail: str = ""


class DecisionTrace(FrozenModel):
    trace_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    turn_index: int = Field(ge=0)
    kind: TraceKind = "customer_turn"
    customer_message: str = ""  # redacted before it is stored
    language: Language | None = None
    intents: tuple[IntentCandidate, ...] = ()
    active_intent: str | None = None
    entities: dict[str, str] = Field(default_factory=dict)  # redacted before it is stored
    nlu_method: NluMethod | None = None
    frustration: Frustration | None = None
    identity: SessionIdentity = Field(default_factory=SessionIdentity)
    risk_categories: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()
    evidence_empty_reason: str | None = None
    proposals: tuple[ProposalRecord, ...] = ()
    permission: PermissionRecord | None = None
    policy: tuple[PolicyRecord, ...] = ()
    tool_calls: tuple[ToolCallRecord, ...] = ()
    decision: Decision
    decision_reason: str = ""
    response_text: str = ""
    response_citations: tuple[str, ...] = ()
    escalation_reason: EscalationReason | None = None
    handoff_case_id: str | None = None
    errors: tuple[str, ...] = ()
    steps: tuple[TraceStep, ...] = ()
    latency_ms: float = Field(default=0.0, ge=0.0)
    versions: dict[str, str] = Field(default_factory=dict)  # prompt, lexicon, tenant config versions used

    @model_validator(mode="after")
    def _handoff_has_reason(self) -> Self:
        if self.decision is Decision.HANDOFF and self.escalation_reason is None:
            raise ValueError("a handoff must have an escalation_reason")
        return self

    @model_validator(mode="after")
    def _writes_are_authorized(self) -> Self:
        policy_by_request = {p.request_id: p for p in self.policy}
        for call in self.tool_calls:
            if call.operation_kind == "read":
                continue
            entry = policy_by_request.get(call.policy_request_id) if call.policy_request_id else None
            if entry is None:
                raise ValueError(f"write call {call.request_id} ({call.tool}) has no policy entry in this trace")
            if entry.decision == "allow":
                continue
            if entry.decision == "require_human" and call.approval_id:
                continue
            raise ValueError(
                f"write call {call.request_id} ({call.tool}) is not authorized: policy said {entry.decision}"
                + (" without an approval id" if entry.decision == "require_human" else "")
            )
        return self
