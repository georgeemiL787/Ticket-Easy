"""Typed shapes of the stored and exchanged records at the discovery, grounding, compilation and execution boundaries.

Types only: these describe the existing JSON formats and never change what is serialized. Schemas stay plain dicts
(the normalized OpenAPI 3.0-style subset).
"""
from typing import Any, Literal, NotRequired, TypedDict

Schema = dict[str, Any]


class Diagnostic(TypedDict):
    code: str
    message: str
    pointer: str
    severity: Literal["error", "warning"]
    operation: str | None
    scope: Literal["document", "path", "operation"]


class OperationInput(TypedDict):
    """One bindable input keyed as "path.x", "query.x", "header.x", "body.x" or "body" (whole body)."""
    schema: Schema | None
    required: bool
    description: str
    required_when_body_sent: NotRequired[bool]  # body.x of an optional body that requires x whenever it is sent


class RequestBody(TypedDict):
    required: bool
    description: str
    content: dict[str, dict[str, Schema | None]]  # {"application/json": {"schema": ...}}


class Response(TypedDict):
    schema: Schema | None
    schema_status: Literal["none", "normalized", "unsupported"]
    description: str
    headers: dict[str, Any]
    content: dict[str, Any]


class Operation(TypedDict):
    """Keys after id..proposal_eligible exist only when the operation could be extracted (not for blocked documents)."""
    id: str
    source_pointer: str
    method: str
    path: str
    original: Any
    supported: bool
    proposal_eligible: bool
    operation_id: NotRequired[str | None]
    description: NotRequired[str]
    summary: NotRequired[str]
    tags: NotRequired[list[str]]
    parameters: NotRequired[list[dict[str, Any]]]
    request_body: NotRequired[RequestBody | None]
    inputs: NotRequired[dict[str, OperationInput]]
    responses: NotRequired[dict[str, Response]]
    security: NotRequired[list[dict[str, list[str]]]]
    security_schemes: NotRequired[dict[str, Any]]
    access_status: NotRequired[str]
    declared_auth: NotRequired[dict[str, Any]]
    authorization: NotRequired[dict[str, Any]]
    exposure: NotRequired[dict[str, Any]]
    owner_approval: NotRequired[dict[str, Any]]
    normalization: NotRequired[dict[str, Any]]
    binding: NotRequired[dict[str, Any]]


class InventorySummary(TypedDict):
    operations: int
    technically_supported: int
    proposal_eligible: int
    restricted: int
    requires_clarification: int


class Inventory(TypedDict):
    """discovery.discover's normalized inventory. Legacy code inventories (source_kind "code") share the operation core
    and carry their own provenance keys; grounding only reads them."""
    valid: bool  # deprecated alias of proposal_generation_ready
    document_valid: bool
    eligible_operation_count: int
    proposal_generation_ready: bool
    parser_version: str
    openapi_version: Any  # the declared value, kept even when unsupported or malformed
    openapi_family: Literal["3.0", "3.1"] | None
    operations: list[Operation]
    diagnostics: list[Diagnostic]
    summary: InventorySummary
    source_kind: NotRequired[str]


class RuntimeInput(TypedDict):
    schema: Schema
    required: bool


class DerivedOutput(TypedDict):
    schema: Schema
    guaranteed: bool


class Grounding(TypedDict):
    """grounding.validate_proposal's result."""
    runtime_inputs: dict[str, RuntimeInput]
    outputs: dict[str, DerivedOutput]
    blockers: list[str]
    source_operations: list[str]
    authorization_review: list[dict[str, Any]]
    facts_vs_interpretation: str


BindingKind = Literal["runtime_argument", "business_configuration", "trusted_application_context", "previous_operation_output"]


class BindingSource(TypedDict):
    kind: BindingKind
    reference: str
    step_id: NotRequired[str]
    response_status: NotRequired[str]


class CompiledParameter(TypedDict):
    name: str
    location: Literal["path", "query", "header", "cookie"]
    type: str
    style: str
    explode: bool
    source: BindingSource


class CompiledField(TypedDict):
    name: str
    source: BindingSource


class CompiledWholeBody(TypedDict):
    source: BindingSource


class CompiledBody(TypedDict):
    media_type: Literal["application/json"]
    fields: list[CompiledField]
    whole: CompiledWholeBody | None
    required: NotRequired[bool]  # absent in format /1 bodies, which are always sent
    schema: NotRequired[Schema | None]


class CompiledResponse(TypedDict):
    media_type: Literal["application/json"] | None
    schema: Schema | None


class CompiledStep(TypedDict):
    id: str
    operation_id: str
    method: str
    path: str
    source_pointer: str
    effect: Literal["read", "write"]
    parameters: list[CompiledParameter]
    body: CompiledBody | None
    responses: dict[str, CompiledResponse]


class ArtifactOutput(TypedDict):
    name: str
    step_id: str
    response_status: str
    media_type: Literal["application/json"]
    pointer: str


class ArtifactConnector(TypedDict):
    id: str
    base_url: str
    auth: dict[str, Any] | None


class Artifact(TypedDict):
    """artifacts.compile_artifact's tool artifact; stored as-is and hashed with digest(content)."""
    format: str
    name: str
    purpose: str
    description: str
    proposal: dict[str, Any]
    source: dict[str, Any]
    input_schema: Schema
    output_schema: Schema
    configuration: dict[str, str | None]  # key -> value_json
    steps: list[CompiledStep]
    outputs: list[ArtifactOutput]
    connector: ArtifactConnector
    access_requirements: list[dict[str, Any]]
    enforcement_config: dict[str, Any] | None
    limits: dict[str, Any]
    failure_behavior: dict[str, str]
    activation: dict[str, Any]
    execution_blockers: list[str]
    compiler: NotRequired[dict[str, Any]]
    access_policy: NotRequired[dict[str, Any] | None]
    readiness: NotRequired[dict[str, Any]]


WriteState = Literal["applied", "not_applied", "unknown", "rejected_unverified", "not_attempted"]


class TraceEntry(TypedDict):
    step_id: str
    method: str
    path: str
    effect: Literal["read", "write"]
    outcome: str  # "succeeded", "not_attempted" or a StepFailure outcome
    write_state: WriteState | None  # None for reads
    http_status: NotRequired[int]
    response_bytes: NotRequired[int]
    detail: NotRequired[str]
    duration_ms: NotRequired[int]


class Failure(TypedDict):
    step_id: str
    outcome: str
    detail: str


class ExecutionReport(TypedDict):
    """executor.Run.execute's report; it never contains credentials or output values (only their digests)."""
    status: Literal["succeeded", "failed", "partial", "outcome_unknown"]
    message: str
    failure: Failure | None
    trace: list[TraceEntry]
    arguments: list[str]
    outputs_sha256: dict[str, str]
    runtime_ready: bool
