import json
import pytest
from conftest import ROOT,candidate
from team_c.discovery import discover
from team_c.grounding import validate_proposal
from team_c.models import ProposalContent
from team_c.config import AppError


def inventory():
    return discover((ROOT/"examples/ecommerce.json").read_bytes(),"ecommerce.json","b")[1]


def test_chained_inputs_and_configuration():
    inv=inventory();p=ProposalContent(**candidate(inv))
    derived=validate_proposal(p,inv)
    assert derived["blockers"]
    assert set(derived["runtime_inputs"])=={"order_id","summary"}
    assert "customer_id" not in derived["runtime_inputs"]
    assert not validate_proposal(ProposalContent(**candidate(inv,True)),inv)["blockers"]


def test_saved_live_proposal_still_matches_its_source():
    record=json.loads((ROOT/"examples/reviewed-ecommerce-proposal.json").read_text())
    inv=discover((ROOT/"examples"/record["source_file"]).read_bytes(),record["source_file"],record["business_id"])[1]
    p=ProposalContent(**record["content"])
    derived=validate_proposal(p,inv)
    assert not derived["blockers"]
    assert set(derived["runtime_inputs"])=={"order_id","summary"}
    b=next(b for b in p.steps[1].bindings if b.target=="body.customer_id")
    assert (b.kind,b.step_id,b.reference,b.response_status)==("previous_operation_output","s1","/customer_id","200")


@pytest.mark.parametrize("kind",["runtime_argument","business_configuration","trusted_application_context"])
def test_named_input_source_is_not_a_response_pointer(kind):
    from pydantic import ValidationError
    p=candidate(inventory())
    p["steps"][0]["bindings"][0].update(kind=kind,reference="/order_id")
    with pytest.raises(ValidationError):
        ProposalContent(**p)


@pytest.mark.parametrize("mutation",["forward","unknown_field","nullable","optional","type","missing_required","context_identity","duplicate_target","invented_operation","write_only","config_as_runtime"])
def test_bad_binding_rejected(mutation):
    inv=inventory();p=candidate(inv)
    binding=p["steps"][1]["bindings"][0]
    schema=inv["operations"][0]["responses"]["200"]["schema"]
    if mutation=="forward":binding["step_id"]="s2"
    if mutation=="unknown_field":binding["reference"]="/does_not_exist"
    if mutation=="nullable":schema["properties"]["customer_id"]["nullable"]=True
    if mutation=="optional":schema["required"].remove("customer_id")
    if mutation=="type":schema["properties"]["customer_id"]["type"]="integer"
    if mutation=="missing_required":p["steps"][1]["bindings"].pop()
    if mutation=="context_identity":binding.update(kind="trusted_application_context",reference="verified_customer_id",step_id=None,response_status=None)
    if mutation=="duplicate_target":p["steps"][1]["bindings"].append(binding.copy())
    if mutation=="invented_operation":p["steps"][1]["operation_id"]="invented"
    if mutation=="write_only":schema["properties"]["customer_id"]["writeOnly"]=True
    if mutation=="config_as_runtime":p["steps"][1]["bindings"][2]["kind"]="runtime_argument"
    with pytest.raises(AppError,match="grounding"):
        validate_proposal(ProposalContent(**p),inv)
