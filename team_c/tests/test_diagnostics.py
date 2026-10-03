"""Diagnostic redaction checks; no live model calls."""
import json
from team_c.diagnostics import response_diagnostic
from test_nilestay import candidate, inventory, run_with_transport


def test_projection_drops_values_prose_unknown_fields_and_known_secrets():
    p=candidate(inventory())
    p["configuration"]=[{"key":"api_key", "value_json":"SECRET_VALUE"}]
    p["name"]="PRIVATE_PROSE"
    p["questions"][0]["text"]="PRIVATE_QUESTION"
    p["steps"][0]["bindings"][0]["reference"]="KNOWN_PROVIDER_SECRET"
    raw=json.dumps({"proposals":[p],"headers":{"Authorization":"Bearer PRIVATE_TOKEN"},"reasoning":"PRIVATE_REASONING"})
    d=response_diagnostic(raw,("KNOWN_PROVIDER_SECRET",))
    encoded=json.dumps(d)
    for private in ("SECRET_VALUE","PRIVATE_PROSE","PRIVATE_QUESTION","KNOWN_PROVIDER_SECRET","PRIVATE_TOKEN","PRIVATE_REASONING"):
        assert private not in encoded
    assert d["structure"]["proposals"][0]["configuration"]==[{"key":"api_key","value_present":True}]
    assert d["sha256"] and d["bytes"]==len(raw.encode())


def test_malformed_json_keeps_only_fingerprint():
    d=response_diagnostic('{"Authorization":"Bearer PRIVATE_TOKEN"')
    assert d["invalid_json"] and "PRIVATE_TOKEN" not in json.dumps(d)


def test_structural_parse_failure_retains_sanitized_model_response(env):
    def mutate(p):p["steps"][0]["bindings"][0]["reference"]="/booking_reference"
    response,client=run_with_transport(env,mutate)
    assert response.status_code==502
    run=client.get('/api/v1/proposal-runs/'+response.json()["details"]["run_id"]).json()
    assert run["error"]["code"]=="invalid_model_output"
    assert [r["stage"] for r in run["diagnostics"]]==["context_budget","model_response"]
    assert run["diagnostics"][1]["payload"]["structure"]["proposals"][0]["steps"][0]["bindings"][0]["reference"]=="/booking_reference"
