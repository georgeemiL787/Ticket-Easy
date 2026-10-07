"""Regression for Groq's discriminator_multiple_candidates rejection of proposal schemas."""
import copy
import json

import httpx
import pytest
from jsonschema import Draft202012Validator

from team_c.config import AppError
from team_c.llm.router import Providers
from team_c.llm.schemas import groq_schema, output_schema
from team_c.models import GenerationOutput, ReconciliationOutput, RepairOutput
from team_c.services.proposals import Proposals
from helpers.desk import desk_proposal


def proposal_case(d):
    proposal = desk_proposal(d.spec["inventory"], d.n)
    answer = GenerationOutput(proposals=[proposal], capability_gaps=[]).model_dump()
    proposal = answer["proposals"][0]
    steps = {s["id"]: s["operation_id"] for s in proposal["steps"]}
    for output in proposal["outputs"]:
        output["operation_id"] = steps[output["step_id"]]
    for step in proposal["steps"]:
        for binding in step["bindings"]:
            if binding["kind"] == "previous_operation_output":
                binding["source_operation_id"] = steps[binding["step_id"]]
    payload = dict(inventory=Proposals.scoped(d.spec["inventory"], list(steps.values())))
    return payload, answer


@pytest.mark.parametrize("model,kind", [(GenerationOutput, "generation"), (ReconciliationOutput, "revision"), (RepairOutput, "repair")])
def test_groq_schema_adaptation_is_isolated_and_keeps_allowed_response_values(desk, model, kind):
    payload, _ = proposal_case(desk)
    original, _ = output_schema(kind, payload, model)
    before = copy.deepcopy(original)
    wire = groq_schema(original)
    assert original == before
    Draft202012Validator.check_schema(wire)
    for name, field in (("Output", "pointer"), ("PreviousOutputBinding", "reference")):
        variants = original["$defs"][name]["anyOf"]
        assert "anyOf" not in wire["$defs"][name]
        expected = {v for variant in variants for v in variant["properties"][field]["enum"]}
        assert set(wire["$defs"][name]["properties"][field]["enum"]) == expected
    for step in wire["$defs"]["Step"]["anyOf"]:
        for binding in step["properties"]["bindings"]["items"]["anyOf"]:
            assert binding["properties"]["target"] == {"type": "string"}


@pytest.mark.parametrize("defect", [None, "output_pointer", "output_status", "binding_target", "previous_operation", "echo_mismatch"])
def test_full_response_contract_is_enforced_after_groq_compatible_decoding(desk, defect):
    payload, answer = proposal_case(desk)
    proposal = answer["proposals"][0]
    lookup, create = proposal["steps"]
    previous = next(b for b in create["bindings"] if b["kind"] == "previous_operation_output")
    output = proposal["outputs"][0]
    if defect == "output_pointer":
        output["pointer"] = previous["reference"]
    elif defect == "output_status":
        output["response_status"] = "200"
    elif defect == "binding_target":
        lookup["bindings"][0]["target"] = "query.invented"
    elif defect == "previous_operation":
        previous["source_operation_id"] = create["operation_id"]
    elif defect == "echo_mismatch":
        output.update(operation_id=lookup["operation_id"], response_status="200", pointer="")
    original, _ = output_schema("generation", payload, GenerationOutput)
    wire = groq_schema(original)
    # These represent the additional combinations allowed by the provider schema.
    Draft202012Validator(wire).validate(answer)
    if defect not in (None, "echo_mismatch"):
        assert not Draft202012Validator(original).is_valid(answer)

    settings = desk.app.state.settings.model_copy(update=dict(llm_primary="groq", llm_fallback="openrouter",
        groq_api_key="TEST-ONLY-primary", groq_api_key_2="TEST-ONLY-backup", openrouter_api_key="TEST-ONLY",
        openrouter_model="TEST-ONLY"))
    sent = []
    def handler(request):
        sent.append(request)
        assert request.url.host == "api.groq.com"
        assert json.loads(request.content)["response_format"]["json_schema"]["schema"] == wire
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(answer)}}]})
    store = desk.service.store
    providers = Providers(settings, store, httpx.MockTransport(handler))
    run = store.start_run(desk.bid, "generation", payload)
    if defect:
        with pytest.raises(AppError) as caught:
            providers.call("generation", payload, GenerationOutput, run)
        assert caught.value.code == "invalid_model_output"
    else:
        result = providers.call("generation", payload, GenerationOutput, run)
        assert result.proposals[0].steps[0].operation_id == lookup["operation_id"]
    assert len(sent) == 1  # Invalid output cannot rotate keys or providers.
