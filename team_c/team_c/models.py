from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Configuration(StrictModel):
    key: str = Field(pattern="^[A-Za-z_][A-Za-z0-9_]*$", description="Same key referenced by a business_configuration binding")
    value_json: str | None = Field(default=None, description="JSON-encoded owner-provided setting; null when unknown, never omit the configuration entry")


class Question(StrictModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    configuration_key: str | None = Field(default=None, description="Link only to an actual owner setting bound into an API input. General design, authorization and ownership questions MUST use null; do not invent a setting to link them.")


class Binding(StrictModel):
    target: str = Field(description="Exact operation input key, e.g. path.order_id or body.customer_id")
    kind: Literal["business_configuration", "runtime_argument", "trusted_application_context", "previous_operation_output"]
    reference: str = Field(description="Config/runtime/context key, or JSON Pointer into previous response")
    step_id: str | None = None
    response_status: str | None = None


class RuntimeBinding(Binding):
    kind: Literal["runtime_argument"]
    reference: str = Field(pattern="^[A-Za-z_][A-Za-z0-9_]*$", description="Plain runtime argument name, never a JSON Pointer; no leading slash")
    step_id: None = None
    response_status: None = None


class ConfigurationBinding(Binding):
    kind: Literal["business_configuration"]
    reference: str = Field(pattern="^[A-Za-z_][A-Za-z0-9_]*$", description="Exact configuration[].key, never a JSON Pointer; no leading slash")
    step_id: None = None
    response_status: None = None


class ContextBinding(Binding):
    kind: Literal["trusted_application_context"]
    reference: str = Field(pattern="^[A-Za-z_][A-Za-z0-9_]*$", description="Plain trusted context key, never a JSON Pointer")
    step_id: None = None
    response_status: None = None


class PreviousOutputBinding(Binding):
    kind: Literal["previous_operation_output"]
    reference: str = Field(description="JSON Pointer to the earlier response field with the SAME MEANING as the target. Read every response property; do not choose the first identifier merely because it has the same type.")
    step_id: str = Field(pattern="^s[1-8]$", description="Earlier step ID, e.g. s1")
    response_status: str = Field(pattern="^2[0-9]{2}$", description="Exact response status code, e.g. 200; no label or media type")


class Step(StrictModel):
    id: str = Field(pattern="^s[1-8]$")
    operation_id: str = Field(description="Exact stable UUID from inventory.operations[].id, NOT the OpenAPI operationId name")
    purpose: str = Field(min_length=1)
    bindings: list[RuntimeBinding | ConfigurationBinding | ContextBinding | PreviousOutputBinding]


class Output(StrictModel):
    name: str
    step_id: str = Field(pattern="^s[1-8]$")
    response_status: str = Field(pattern="^2[0-9]{2}$", description="Exact response status code, e.g. 201")
    pointer: str


class ProposalContent(StrictModel):
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    business_purpose: str = Field(min_length=1)
    steps: list[Step] = Field(min_length=1, max_length=8)
    configuration: list[Configuration] = Field(description="Exactly the owner settings bound to declared API inputs. Use [] when none are needed. Exclude credentials, policy/verification processes, and enums already declared by the API. A required question does not require a configuration entry.", examples=[[]])
    questions: list[Question] = Field(description="Required unresolved design questions, including ownership verification. Use configuration_key:null for questions that do not supply an actual API input setting. Link unknown bound settings to their configuration key.")
    outputs: list[Output] = Field(min_length=1)
    expected_reads: list[str]
    expected_writes: list[str]
    assumptions: list[str]
    limitations: list[str]
    risk: Literal["low", "medium", "high", "unknown"]
    risk_rationale: str = Field(min_length=1)


class CapabilityGap(StrictModel):
    requested_capability: str
    explanation: str


class GenerationOutput(StrictModel):
    proposals: list[ProposalContent] = Field(max_length=3)
    capability_gaps: list[CapabilityGap]


class MissingCapability(StrictModel):
    kind: Literal["absent_operation", "unsupported_operation", "restricted_operation", "missing_information"] = Field(
        description="absent_operation: no operation in the index does this (operation_ids []); unsupported_operation / restricted_operation: cite the ineligible operation; missing_information: owner input needed")
    description: str = Field(min_length=1)
    operation_ids: list[str] = Field(max_length=8)


class RequestTriageOutput(StrictModel):
    outcome: Literal["feasible", "needs_clarification", "existing_tool", "unavailable"]
    summary: str = Field(min_length=1, description="One or two sentences for the owner explaining the outcome")
    operation_ids: list[str] = Field(max_length=8, description="Eligible operations needed for the requested tool; [] unless feasible")
    existing_proposal_ids: list[str] = Field(description="Existing tools that already do this; [] unless existing_tool")
    questions: list[str] = Field(max_length=5, description="Specific questions about the tool's behavior; [] unless needs_clarification")
    missing: list[MissingCapability] = Field(max_length=8)


class Suggestion(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    purpose: str = Field(min_length=1)
    benefit: str = Field(min_length=1, description="Expected benefit for the business or its customers")
    business_reason: str = Field(min_length=1, description="Why this is useful for THIS business, beyond an endpoint existing")
    category: Literal["feasible", "needs_clarification", "blocked_by_missing_api"]
    operation_ids: list[str] = Field(max_length=8, description="Eligible supporting operations")
    related_proposal_ids: list[str]
    relationship: str = Field(min_length=1, description="How it relates to existing tools (extends, complements, or none)")
    missing: list[MissingCapability] = Field(max_length=8)


class SuggestionOutput(StrictModel):
    suggestions: list[Suggestion] = Field(max_length=10)


class NamedArea(StrictModel):
    name: str = Field(min_length=1, max_length=80, description="Short business-facing name for this area of the API")
    description: str = Field(min_length=1, max_length=300, description="What this area lets the business or its customers do")
    audience: Literal["customer", "staff", "internal", "mixed"] = Field(description="Who these operations mainly serve: customers, staff, internal/technical use, or a mix. A hint only, never a permission")
    reason: str = Field(min_length=1, max_length=300, description="Why this area is placed at this priority for THIS business's customers")


class AreaNamingOutput(StrictModel):
    areas: list[NamedArea] = Field(min_length=1, max_length=8, description="Business areas, most useful for tools serving this business's customers first")


class AreaAssignmentOutput(StrictModel):
    assignments: dict[str, str] = Field(description="The area name chosen for each supplied API group key")


class RepairOutput(StrictModel):
    outcome: Literal["revised", "cannot_repair", "capability_gap"] = Field(description="revised: rewired proposal; cannot_repair: no rewiring of the existing steps meets the expected behavior; capability_gap: a needed operation or response field does not exist")
    explanation: str = Field(min_length=1, description="What was changed and why, or why it cannot be repaired")
    revised_proposal: ProposalContent | None = Field(description="The complete corrected proposal when outcome is revised; otherwise null")


class CodeInterpretation(StrictModel):
    operation_id: str
    explanation: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    call_trace: list[str] = Field(description="Evidence IDs for resolved internal functions in this operation's extracted call_trace. Use [] when none are observed. No prose or invented function names.")
    uncertainties: list[str]


class CodeAnalysisOutput(StrictModel):
    interpretations: list[CodeInterpretation] = Field(max_length=50)


class Finding(StrictModel):
    question_id: str
    status: Literal["resolved", "insufficient", "contradictory"]
    explanation: str
    answer_revision_ids: list[int]


class ReconciliationOutput(StrictModel):
    findings: list[Finding]
    revised_proposal: ProposalContent | None = None
    capability_gaps: list[CapabilityGap]


class AnswerSubmission(StrictModel):
    expected_revision: int
    answers: dict[str, str]


class ReviewSubmission(StrictModel):
    expected_revision: int


class DecisionSubmission(StrictModel):
    expected_revision: int
    action: Literal["approve_to_build", "reject", "request_changes"]
    reason: str = ""
    idempotency_key: str = Field(min_length=8, max_length=200)


class RevisionSubmission(StrictModel):
    expected_revision: int
    instruction: str = Field(min_length=1)


class ArtifactBuildSubmission(StrictModel):
    connector_id: str = Field(min_length=1)


class EnforcementSubmission(StrictModel):
    connector_id: str = Field(min_length=1)
    enforcement: dict[str, dict[str, str]] = {}


class EnforcementReviewSubmission(StrictModel):
    note: str = Field(min_length=1, max_length=2000)


class SandboxRunSubmission(StrictModel):
    identity: str = Field(min_length=1)
    arguments: dict


class SandboxExpectation(StrictModel):
    status: Literal["succeeded", "failed", "rejected", "partial", "outcome_unknown"]
    failure_step: str | None = None
    failure_outcome: str | None = None
    outputs: dict = {}
    outputs_present: list[str] = []


class SandboxTestSubmission(SandboxRunSubmission):
    name: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,80}$")
    expect: SandboxExpectation
    scenario: Literal["general", "own_record", "cross_user"] = Field(default="general", description="own_record: the identity uses its own record and must succeed; cross_user: the identity targets record_owner's record and must be refused")
    record_owner: str | None = Field(default=None, description="cross_user only: the configured identity that owns the targeted record")


class PublicationSubmission(StrictModel):
    environment: Literal["sandbox"] = "sandbox"
    note: str = Field(min_length=1, max_length=2000)


class PublicationDisableSubmission(StrictModel):
    note: str = Field(min_length=1, max_length=2000)


class ToolRequestSubmission(StrictModel):
    goal: str = Field(min_length=10, max_length=2000)
    examples: str = Field(default="", max_length=4000)
    spec_id: str | None = None
    idempotency_key: str = Field(min_length=8, max_length=200)


class ClarificationSubmission(StrictModel):
    text: str = Field(default="", max_length=4000)


class SuggestionRunSubmission(StrictModel):
    spec_id: str | None = None
    count: int | None = Field(default=None, ge=1, le=10)
    next_batch: bool = Field(default=False, description="Review the operations earlier suggestion runs did not consider")


class AreaSelectionSubmission(StrictModel):
    area_ids: list[str] = Field(min_length=1)


class SuggestionDecisionSubmission(StrictModel):
    action: Literal["accept", "dismiss", "revise"]
    note: str = Field(default="", max_length=2000)
    clarification: str = Field(default="", max_length=4000)
    title: str | None = Field(default=None, max_length=120)
    purpose: str | None = Field(default=None, max_length=2000)


class RepairSubmission(StrictModel):
    verification: str | None = Field(default=None, max_length=2000, description="Independent evidence that a rejected write changed nothing")


class SupersedeSubmission(StrictModel):
    expected_revision: int
    reason: str = Field(min_length=1, max_length=2000)


def canonical_content(content: ProposalContent) -> ProposalContent:
    """Binding/config order is not behavior. Step order is, and remains untouched."""
    content = content.model_copy(deep=True)
    for step in content.steps:
        step.bindings.sort(key=lambda b: b.target)
    content.configuration.sort(key=lambda c: c.key)
    content.questions.sort(key=lambda q: q.id)
    content.outputs.sort(key=lambda o: o.name)
    for field in ("expected_reads", "expected_writes", "assumptions", "limitations"):
        setattr(content, field, sorted(getattr(content, field)))
    return content
