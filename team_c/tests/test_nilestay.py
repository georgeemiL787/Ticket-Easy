"""NileStay regression. HTTP transport responses are deterministic model substitutes."""
import copy
import json
import pytest
from conftest import ROOT
from helpers.nilestay import candidate, inventory, run_with_transport
from team_c.config import AppError
from team_c.grounding import validate_proposal
from team_c.models import GenerationOutput, ProposalContent, canonical_content


def test_empty_configuration_contract_and_dependency():
    inv=inventory();raw=candidate(inv)
    parsed=ProposalContent.model_validate(raw)
    normalized=canonical_content(parsed)
    assert normalized.configuration==[]
    assert normalized.questions[0].configuration_key is None
    derived=validate_proposal(normalized,inv)
    assert not derived["blockers"]
    assert set(derived["runtime_inputs"])=={"booking_reference","category","message"}
    assert derived["runtime_inputs"]["category"]["schema"]["enum"]==["housekeeping","maintenance"]
    assert derived["runtime_inputs"]["message"]["schema"]["maxLength"]==500
    binding=next(b for b in normalized.steps[1].bindings if b.target=="body.reservation_id")
    assert (binding.kind,binding.step_id,binding.reference)==("previous_operation_output","s1","/reservation_id")
    assert inv["operations"][0]["security"]==[{"ServiceBearer":[]}]
    assert not any("auth" in b.target.lower() for s in normalized.steps for b in s.bindings)


def test_reproduced_model_declarations_remain_invalid_without_normalization_repair():
    inv=inventory();p=candidate(inv)
    p["configuration"]=[dict(key="verification_method",value_json=None),dict(key="allowed_service_request_categories",value_json=None)]
    p["questions"]=[dict(id="q1",text="How is ownership verified?",configuration_key="verification_method"),dict(id="q2",text="Which categories?",configuration_key="allowed_service_request_categories")]
    parsed=ProposalContent(**p);normalized=canonical_content(parsed)
    for content in (parsed,normalized):
        with pytest.raises(AppError) as caught:validate_proposal(content,inv)
        info=caught.value.details["configuration_contract"]
        assert info["declared"]==info["unused_declarations"]==["allowed_service_request_categories","verification_method"]
        assert info["referenced"]==info["undeclared_references"]==[]


def test_prompt_and_schema_distinguish_questions_from_input_configuration():
    from team_c.providers import SYSTEM,strict_schema
    schema=strict_schema(GenerationOutput.model_json_schema())
    prop=schema["$defs"]["ProposalContent"]["properties"]["configuration"]
    assert prop.get("minItems",0)==0 and [] in prop["examples"]
    assert "configuration:[]" in SYSTEM and "configuration_key:null" in SYSTEM
    assert "connector-managed" in SYSTEM and "runtime_argument" in SYSTEM
    assert "ownership" in schema["$defs"]["Question"]["properties"]["configuration_key"]["description"]


def test_captured_reproduction_is_model_error_not_parser_change():
    saved=json.loads((ROOT/"docs/nilestay-reproduction-diagnostics.json").read_text())
    stages={r["stage"]:r["payload"] for r in saved["stages"]}
    assert stages["model_response"]["structure"]==stages["parsed_output"]
    for stage in (stages["parsed_output"],stages["normalized_output"]):
        p=stage["proposals"][0]
        assert {c["key"] for c in p["configuration"]}=={"verification_method","allowed_service_request_categories"}
        assert not any(b["kind"]=="business_configuration" for s in p["steps"] for b in s["bindings"])


@pytest.mark.parametrize("unused,undeclared",[(True,False),(False,True),(True,True)])
def test_configuration_mismatch_names_each_side(unused,undeclared):
    inv=inventory();p=candidate(inv)
    if unused:p["configuration"]=[dict(key="guest_ownership_verification",value_json=None)]
    if undeclared:p["steps"][1]["bindings"][1].update(kind="business_configuration",reference="default_category")
    with pytest.raises(AppError) as caught:validate_proposal(ProposalContent(**p),inv)
    info=caught.value.details["configuration_contract"]
    assert info["unused_declarations"]==(["guest_ownership_verification"] if unused else [])
    assert info["undeclared_references"]==(["default_category"] if undeclared else [])
    assert any("guest_ownership_verification" in s for s in caught.value.details["errors"])==unused
    assert any("default_category" in s for s in caught.value.details["errors"])==undeclared


def test_empty_configuration_full_pipeline_still_blocks_unanswered_ownership(env):
    response,client=run_with_transport(env)
    assert response.status_code==200,response.text
    result=response.json();pid=result["proposal_ids"][0]
    p=client.get(f"/api/v1/proposals/{pid}").json()
    assert p["content"]["configuration"]==[] and p["state"]=="needs_clarification"
    blocked=client.post(f"/api/v1/proposals/{pid}/versions/1/decisions",json=dict(expected_revision=p["review_revision"],action="approve_to_build",idempotency_key="nilestay-approval"))
    assert blocked.status_code==422
    d=client.get(f'/api/v1/proposal-runs/{result["run_id"]}').json()["diagnostics"]
    stages={r["stage"]:r["payload"] for r in d}
    assert stages["model_response"]["structure"]==stages["parsed_output"]
    assert stages["normalized_output"]["proposals"][0]["configuration"]==[]


def test_failure_diagnostics_survive_grounding_rejection(env):
    def mutate(p):p["configuration"]=[dict(key="guest_ownership_verification",value_json=None)]
    response,client=run_with_transport(env,mutate)
    assert response.status_code==422,response.text
    assert len(response.json()["details"]["earlier_run_ids"])==1
    run=client.get('/api/v1/proposal-runs/'+response.json()["details"]["run_id"]).json()
    assert run["error"]["details"]["configuration_contract"]["unused_declarations"]==["guest_ownership_verification"]
    stages={r["stage"]:r["payload"] for r in run["diagnostics"]}
    assert stages["model_response"]["structure"]==stages["parsed_output"]
    assert stages["grounding_error"]["proposal_index"]==0
    assert stages["normalized_output"]["proposals"][0]["configuration"][0]["key"]=="guest_ownership_verification"
    page=client.get('/runs/'+run["id"])
    assert page.status_code==200 and "Sanitized output diagnostics" in page.text
