"""Echo resolution for references a strict output schema cannot express as step order.

Regression: `drop_echoes` rejected any answer whose `step_id` did not match the operation it
echoed. A strict structured-output schema has no way to say "an earlier step", so a provider that
flattens the correlated choices (Groq) answers with its own step id while naming the correct source
operation. Every checkout proposal failed `invalid_model_output` for that reason alone.
"""
import pytest

from team_c.llm.schemas import drop_echoes

ECHOES = {"operation_id", "source_operation_id"}
OP_ME = "a" * 32
OP_ORDER = "b" * 32
OP_GET = "c" * 32


def proposal(**overrides):
    data = {
        "proposals": [{
            "name": "Place order",
            "steps": [
                {"id": "s1", "operation_id": OP_ME, "bindings": []},
                {"id": "s2", "operation_id": OP_ORDER, "bindings": []},
                {"id": "s3", "operation_id": OP_GET, "bindings": [
                    {"kind": "previous_operation_output", "reference": "/id", "target": "path.order_id",
                     "step_id": "s3", "response_status": "201", "source_operation_id": OP_ORDER},
                ]},
            ],
            "outputs": [
                {"name": "order_id", "step_id": "s3", "response_status": "200", "pointer": "/id",
                 "operation_id": OP_GET},
            ],
        }],
    }
    data["proposals"][0].update(overrides)
    return data


def test_self_referenced_step_resolves_to_the_echoed_operation():
    """The model cited its own step but named the correct source operation."""
    out = drop_echoes(proposal(), ECHOES)["proposals"][0]
    binding = out["steps"][2]["bindings"][0]
    assert binding["step_id"] == "s2"
    assert "source_operation_id" not in binding


def test_matching_step_id_is_left_alone():
    out = drop_echoes(proposal(), ECHOES)["proposals"][0]
    assert out["outputs"][0]["step_id"] == "s3"
    assert "operation_id" not in out["outputs"][0]


def test_output_step_is_never_resolved_from_its_echo():
    """An output has no owning step, so a mismatch is a real contradiction, not a self-citation."""
    data = proposal()
    data["proposals"][0]["outputs"][0]["step_id"] = "s1"
    with pytest.raises(ValueError, match="different operation"):
        drop_echoes(data, ECHOES)


def test_binding_citing_another_real_step_is_still_rejected():
    """Only a self-citation is resolvable; naming a different real step contradicts the echo."""
    data = proposal()
    data["proposals"][0]["steps"][2]["bindings"][0]["step_id"] = "s1"
    with pytest.raises(ValueError, match="different operation"):
        drop_echoes(data, ECHOES)


def test_unknown_echoed_operation_is_still_rejected():
    data = proposal()
    data["proposals"][0]["steps"][2]["bindings"][0]["source_operation_id"] = "d" * 32
    with pytest.raises(ValueError, match="different operation"):
        drop_echoes(data, ECHOES)


def test_operation_used_by_two_steps_stays_ambiguous_and_is_rejected():
    data = proposal()
    data["proposals"][0]["steps"].append({"id": "s4", "operation_id": OP_ORDER, "bindings": []})
    with pytest.raises(ValueError, match="different operation"):
        drop_echoes(data, ECHOES)


def test_revised_proposal_is_resolved_too():
    data = {"proposals": [], "revised_proposal": proposal()["proposals"][0]}
    out = drop_echoes(data, ECHOES)["revised_proposal"]
    assert out["steps"][2]["bindings"][0]["step_id"] == "s2"


def test_echoes_absent_from_a_kind_are_not_touched():
    """Without the echo there is nothing to check; the field is left exactly as sent."""
    data = proposal()
    del data["proposals"][0]["outputs"][0]["operation_id"]
    out = drop_echoes(data, {"source_operation_id"})["proposals"][0]
    assert out["outputs"][0]["step_id"] == "s3"


def test_resolution_cannot_smuggle_a_forward_reference_past_grounding():
    """A self-citation may only relabel a reference; grounding still refuses forward references.

    Resolution never widens what grounding accepts: `validate_proposal` requires the referenced
    step to have already run, so a self or forward reference is still rejected.
    """
    from team_c.grounding import validate_proposal
    from team_c.models import ProposalContent

    schema = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
    inventory = {"valid": True, "document_valid": True, "operations": [
        {"id": OP_ME, "method": "GET", "path": "/me", "effect": "read", "inputs": {},
         "responses": {"200": {"schema": schema}}},
        {"id": OP_ORDER, "method": "POST", "path": "/orders", "effect": "write", "inputs": {},
         "responses": {"201": {"schema": schema}}},
        {"id": OP_GET, "method": "GET", "path": "/orders/{order_id}", "effect": "read",
         "inputs": {"path.order_id": {"required": True, "schema": {"type": "string"}}},
         "responses": {"200": {"schema": schema}}},
    ]}
    content = {
        "name": "T", "description": "d", "business_purpose": "p", "assumptions": [], "limitations": [],
        "risk": "low", "risk_rationale": "r", "configuration": [], "questions": [],
        "expected_reads": ["CartRead"], "expected_writes": ["OrderCreate"],
        "outputs": [{"name": "order_id", "step_id": "s1", "response_status": "200", "pointer": "/id"}],
        "steps": [
            {"id": "s1", "operation_id": OP_ME, "purpose": "me", "bindings": []},
            {"id": "s2", "operation_id": OP_ORDER, "purpose": "create", "bindings": []},
            {"id": "s3", "operation_id": OP_GET, "purpose": "get", "bindings": [
                {"target": "path.order_id", "kind": "previous_operation_output", "reference": "/id",
                 "step_id": "s3", "response_status": "200"}]},
        ],
    }
    # A step sourcing from itself survives resolution only if grounding accepts it, which it must not.
    from team_c.config import AppError

    with pytest.raises(AppError) as caught:
        validate_proposal(ProposalContent.model_validate(content), inventory)
    assert any("earlier step" in error for error in caught.value.details["errors"])