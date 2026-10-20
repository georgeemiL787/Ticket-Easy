"""OpenAPI-primary discovery: 3.1 subset, per-operation contract, pointer constraints, URL fetch and scope.
Deterministic checks; model responses are HTTP/transport substitutes, never live inference."""
import copy
import hashlib
import json
import re
import httpx
import pytest
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.testclient import TestClient
from conftest import ROOT, DeterministicModelSubstitute
from helpers.openapi import inventory, op, proposal, target_spec
from team_c.config import AppError, Settings
from team_c.discovery import Normalizer, Scope
from team_c.grounding import validate_proposal
from team_c.models import GenerationOutput, ProposalContent
from team_c.providers import Providers
from team_c.service import Service
from team_c.storage import Store, now, uid
from team_c.web import create_app

URL = "http://127.0.0.1:8001/api/v1/openapi.json"


def labels(inv, supported):
    return {f'{o["method"]} {o["path"]}' for o in inv["operations"] if o["supported"] == supported}


def test_fastapi_31_nullable_forms_keep_requiredness_and_source_pointers():
    doc = target_spec()
    assert doc["openapi"].startswith("3.1")
    original = copy.deepcopy(doc)
    inv = inventory(doc)
    assert inv["document_valid"] and inv["valid"] and inv["openapi_family"] == "3.1"
    put = op(inv, "PUT", "/api/v1/items/{id}")
    assert put["supported"] and put["proposal_eligible"]
    title = put["inputs"]["body.title"]
    assert title["required"] is False and title["schema"]["nullable"] is True
    assert title["schema"]["type"] == "string" and title["schema"]["maxLength"] == 255
    read = op(inv, "GET", "/api/v1/items/{id}")["responses"]["200"]["schema"]
    assert set(read["required"]) == {"title", "id", "owner_id"}
    assert read["properties"]["description"]["nullable"] and not read["properties"]["title"].get("nullable")
    assert put["normalization"]["source_pointers"]["inputs/body.title"] == "#/components/schemas/ItemUpdate/properties/title"
    assert dict(pointer="#/components/schemas/ItemUpdate/properties/title/anyOf", rule="anyOf [T, null] -> nullable") in put["normalization"]["notes"]
    assert doc == original and put["original"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]


def test_local_media_and_error_response_problems_do_not_block_unrelated_operations():
    inv = inventory()
    assert labels(inv, False) == {"POST /api/v1/login/access-token", "POST /api/v1/password-recovery-html-content/{email}"}
    login = op(inv, "POST", "/api/v1/login/access-token")
    assert login["exposure"]["classification"] == "restricted"
    assert any(s["kind"] == "credential_exchange" and s["basis"] == "declared" for s in login["exposure"]["signals"])
    read = op(inv, "GET", "/api/v1/items/{id}")
    assert read["responses"]["422"]["schema"] is None and read["responses"]["422"]["schema_status"] == "unsupported"
    warnings = [d for d in inv["diagnostics"] if d["code"] == "error_response_unsupported"]
    assert warnings and all(d["severity"] == "warning" for d in warnings)


def test_contract_separates_support_auth_authorization_exposure_and_approval():
    doc = target_spec()
    inv = inventory(doc)
    item = op(inv, "GET", "/api/v1/items/{id}")
    assert item["binding"]["status"] == "supported"
    assert item["declared_auth"]["status"] == "required" and item["declared_auth"]["alternatives"][0][0]["type"] == "oauth2"
    assert item["authorization"]["status"] == "not_declared"
    assert any("path.id" in r for r in item["authorization"]["unresolved_requirements"])
    assert item["exposure"]["classification"] == "requires_clarification" and item["proposal_eligible"]
    assert item["owner_approval"]["state"] == "not_reviewed" and "never runtime authorization" in item["owner_approval"]["note"]
    health = op(inv, "GET", "/api/v1/utils/health-check/")
    assert health["declared_auth"]["status"] == "none_declared"
    assert any("not evidence of public access" in r for r in health["authorization"]["unresolved_requirements"])
    signup = op(inv, "POST", "/api/v1/users/signup")
    assert signup["supported"] and not signup["proposal_eligible"] and signup["exposure"]["classification"] == "restricted"
    assert all(s["basis"] in {"declared", "field_name_heuristic", "name_heuristic"} for s in signup["exposure"]["signals"])
    doc["paths"]["/api/v1/utils/health-check/"]["get"]["security"] = []
    assert op(inventory(doc), "GET", "/api/v1/utils/health-check/")["declared_auth"]["status"] == "explicitly_none"


def test_unsupported_union_blocks_affected_operation_and_dependents_only():
    doc = target_spec()
    doc["components"]["schemas"]["ItemUpdate"]["properties"]["title"] = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    inv = inventory(doc)
    assert "PUT /api/v1/items/{id}" in labels(inv, False) and op(inv, "GET", "/api/v1/items/{id}")["supported"]
    assert any(d["code"] == "unsupported_union" and d["operation"] == "PUT /api/v1/items/{id}" and d["pointer"].startswith("#/components/schemas/ItemUpdate") for d in inv["diagnostics"])
    doc = target_spec()
    doc["components"]["schemas"]["ItemPublic"]["properties"]["owner_id"] = {"type": ["string", "integer"]}
    inv = inventory(doc)
    assert {"GET /api/v1/items/", "GET /api/v1/items/{id}", "PUT /api/v1/items/{id}"} <= labels(inv, False)
    assert op(inv, "GET", "/api/v1/utils/health-check/")["supported"] and inv["document_valid"] and inv["valid"]


def test_declared_version_is_validated_before_normalization():
    doc = target_spec()
    doc["components"]["schemas"]["ItemUpdate"]["properties"]["title"] = {"type": "string", "nullable": True}
    inv = inventory(doc)
    assert "PUT /api/v1/items/{id}" in labels(inv, False) and op(inv, "GET", "/api/v1/items/{id}")["supported"]
    assert any("not an OpenAPI 3.1 keyword" in d["message"] for d in inv["diagnostics"])
    doc30 = json.loads((ROOT / "examples/ecommerce.json").read_text())
    doc30["components"]["schemas"]["Order"]["properties"]["status"] = {"type": ["string", "null"]}
    inv = inventory(doc30)
    assert labels(inv, False) == {"GET /orders/{order_id}"} and any(d["code"] == "invalid_openapi" for d in inv["diagnostics"])
    doc = target_spec()
    doc["paths"]["/api/v1/items/"]["get"]["parameters"][0]["in"] = "bogus"
    inv = inventory(doc)
    assert inv["document_valid"] and "GET /api/v1/items/" in labels(inv, False) and op(inv, "GET", "/api/v1/items/{id}")["supported"]
    doc = target_spec()
    del doc["info"]
    inv = inventory(doc)
    assert not inv["document_valid"] and not inv["valid"] and not labels(inv, True)


def norm(schema, doc=None):
    scope = Scope()
    return Normalizer(doc or {}, "3.1").schema(schema, "#/x", "loc", scope), scope


def test_31_subset_normalizes_only_semantics_preserving_forms():
    assert norm({"type": ["integer", "null"], "exclusiveMinimum": 0})[0] == {"type": "integer", "nullable": True, "minimum": 0, "exclusiveMinimum": True}
    assert norm({"const": "a"})[0] == {"enum": ["a"]}
    doc = {"components": {"schemas": {"A": {"type": "string"}}}}
    assert norm({"$ref": "#/components/schemas/A", "description": "d"}, doc)[0] == {"type": "string", "description": "d"}
    for schema, code in [({"oneOf": [{}, {"type": "null"}]}, "unsupported_union"),
                         ({"anyOf": [{"type": "string", "maxLength": 3}, {"type": "null"}], "maxLength": 5}, "conflicting_constraints"),
                         ({"type": ["string", "integer"]}, "unsupported_union"),
                         ({"$ref": "#/components/schemas/A", "maxLength": 2}, "reference_siblings"),
                         ({"prefixItems": [{"type": "string"}]}, "unsupported_construct"),
                         ({"minimum": 1, "exclusiveMinimum": 2}, "conflicting_constraints")]:
        result, scope = norm(schema, doc)
        assert result is None and scope.problems[0]["code"] == code, schema


def test_output_pointers_are_per_operation_and_echo_mismatch_is_rejected(tmp_path):
    inv = inventory()
    items, item = op(inv, "GET", "/api/v1/items/"), op(inv, "GET", "/api/v1/items/{id}")
    settings = Settings(_env_file=None, database_path=str(tmp_path / "m.db"), ollama_context=200_000)
    store = Store(settings.database_path)
    bid = uid()
    with store.connect(write=True) as c:
        c.execute("INSERT INTO businesses VALUES(?,?,?,?)", (bid, "Items", "Items", now()))
    sent, raw = [], [None]
    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": raw[0]}, "done": True})
    providers = Providers(settings, store, httpx.MockTransport(handler))
    payload = dict(business=dict(name="Items", description="Items"), inventory=Service.scoped(inv, [items["id"], item["id"]]))
    good = proposal(item, dict(name="title", step_id="s1", response_status="200", pointer="/title", operation_id=item["id"]))
    raw[0] = json.dumps(dict(proposals=[good], capability_gaps=[]))
    result = providers.call("generation", payload, GenerationOutput, store.start_run(bid, "generation", {}))
    assert result.proposals[0].outputs[0].pointer == "/title"
    variants = sent[0]["format"]["$defs"]["Output"]["anyOf"]
    offered = {v["properties"]["operation_id"]["enum"][0]: v["properties"]["pointer"]["enum"] for v in variants}
    assert "/data" in offered[items["id"]] and "/data" not in offered[item["id"]] and "/title" in offered[item["id"]]
    # Decoding order: the step and its operation are committed before any pointer is chosen.
    assert list(variants[0]["properties"]) == variants[0]["required"] == ["name", "step_id", "operation_id", "response_status", "pointer"]
    binding = sent[0]["format"]["$defs"]["PreviousOutputBinding"]["anyOf"][0]
    assert list(binding["properties"]) == ["target", "kind", "step_id", "source_operation_id", "response_status", "reference"]
    # The observed live failure: a pointer from the list operation attached to the single-item step.
    bad = proposal(item, dict(name="title", step_id="s1", response_status="200", pointer="/data", operation_id=items["id"]))
    raw[0] = json.dumps(dict(proposals=[bad], capability_gaps=[]))
    with pytest.raises(AppError) as caught:
        providers.call("generation", payload, GenerationOutput, store.start_run(bid, "generation", {}))
    assert caught.value.code == "invalid_model_output"
    unechoed = proposal(item, dict(name="title", step_id="s1", response_status="200", pointer="/data"))
    with pytest.raises(AppError) as caught:
        validate_proposal(ProposalContent(**unechoed), inv)
    assert any("/data" in e for e in caught.value.details["errors"])


def test_grounding_rejects_restricted_and_out_of_scope_operations():
    inv = inventory()
    items, item, signup = op(inv, "GET", "/api/v1/items/"), op(inv, "GET", "/api/v1/items/{id}"), op(inv, "POST", "/api/v1/users/signup")
    output = dict(name="title", step_id="s1", response_status="200", pointer="/title")
    fields = validate_proposal(ProposalContent(**proposal(item, output)), inv, [item["id"]])
    assert fields["authorization_review"][0]["declared_auth"] == "required"
    with pytest.raises(AppError) as caught:
        validate_proposal(ProposalContent(**proposal(item, output)), inv, [items["id"]])
    assert any("outside the selected generation scope" in e for e in caught.value.details["errors"])
    with pytest.raises(AppError) as caught:
        validate_proposal(ProposalContent(**proposal(signup, dict(output, pointer=""))), inv)
    assert any("not eligible for proposals (restricted)" in e for e in caught.value.details["errors"])


@pytest.fixture
def fetch_app(tmp_path):
    def make(handler, **overrides):
        settings = Settings(**{"_env_file": None, "database_path": str(tmp_path / "f.db"), "session_secret": "t", "openapi_fetch_hosts": "127.0.0.1:8001", **overrides})
        app = create_app(settings, DeterministicModelSubstitute)
        app.state.service.fetch_transport = httpx.MockTransport(handler)
        return app
    return make


def test_url_fetch_records_provenance_and_sends_no_credentials(fetch_app):
    body = json.dumps(target_spec()).encode()
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=body, headers={"content-type": "application/json", "set-cookie": "s=1"})
    with TestClient(fetch_app(handler)) as client:
        bid = client.post("/api/v1/businesses", json=dict(name="Target", description="Items")).json()["id"]
        r = client.post(f"/api/v1/businesses/{bid}/specifications/fetch", json=dict(url=URL))
        assert r.status_code == 200, r.text
        source = r.json()["inventory"]["source"]
        assert source["kind"] == "url" and source["url"] == URL and source["sha256"] == hashlib.sha256(body).hexdigest()
        assert r.json()["inventory"]["summary"]["proposal_eligible"] > 0
        assert client.get(f'/api/v1/specifications/{r.json()["id"]}/inventory').json()["filename"] == URL
        page = client.get("/").text
        csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
        form = client.post("/actions/fetch", data=dict(csrf=csrf, business_id=bid, url=URL))
        assert form.status_code == 200 and "Source: url" in form.text and "restricted" in form.text
    assert [req.method for req in seen] == ["GET", "GET"]
    assert all("authorization" not in req.headers and "cookie" not in req.headers for req in seen)


@pytest.mark.parametrize("url,hosts,code", [(URL, "", "fetch_disabled"), ("http://example.com/openapi.json", "127.0.0.1:8001", "fetch_host"),
                                            ("http://u:p@127.0.0.1:8001/openapi.json", "127.0.0.1:8001", "fetch_url"), ("file:///etc/passwd", "127.0.0.1:8001", "fetch_url")])
def test_url_fetch_rejects_unlisted_or_unsafe_urls(fetch_app, url, hosts, code):
    with TestClient(fetch_app(lambda req: pytest.fail("must not contact"), openapi_fetch_hosts=hosts)) as client:
        bid = client.post("/api/v1/businesses", json=dict(name="Target", description="Items")).json()["id"]
        r = client.post(f"/api/v1/businesses/{bid}/specifications/fetch", json=dict(url=url))
        assert r.json()["code"] == code


@pytest.mark.parametrize("response,code", [(httpx.Response(302, headers={"location": "http://127.0.0.1:8001/other.json"}), "fetch_status"),
                                           (httpx.Response(200, content=b"x" * 200, headers={"content-type": "application/json"}), "upload_limit"),
                                           (httpx.Response(200, content=b"<html></html>", headers={"content-type": "text/html"}), "file_type")])
def test_url_fetch_does_not_follow_redirects_or_exceed_limits(fetch_app, response, code):
    calls = []
    def handler(request):
        calls.append(request)
        return response
    with TestClient(fetch_app(handler, max_upload_bytes=100)) as client:
        bid = client.post("/api/v1/businesses", json=dict(name="Target", description="Items")).json()["id"]
        assert client.post(f"/api/v1/businesses/{bid}/specifications/fetch", json=dict(url=URL)).json()["code"] == code
    assert len(calls) == 1


@pytest.mark.parametrize("url", ["http://localhost:8080/docs", "http://localhost:9123/docs",
                                     "http://127.0.0.1:5678/docs", "http://127.0.0.2:8765/docs",
                                     "http://[::1]:9000/docs", "http://LOCALHOST:8081/docs"])
def test_loopback_rule_fetches_docs_on_any_local_port(fetch_app, url):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/docs":
            return httpx.Response(200, content=get_swagger_ui_html(openapi_url="/api/v1/openapi.json", title="Docs").body,
                                  headers={"content-type": "text/html"})
        return httpx.Response(200, json=target_spec())
    with TestClient(fetch_app(handler, openapi_fetch_hosts="loopback:*")) as client:
        bid = client.post("/api/v1/businesses", json=dict(name="Target", description="Items")).json()["id"]
        result = client.post(f"/api/v1/businesses/{bid}/specifications/fetch", json=dict(url=url))
        assert result.status_code == 200, result.text
        assert len(seen) == 2 and seen[1].endswith("/api/v1/openapi.json")
        page = client.get(f"/businesses/{bid}").text
        assert "Local services (any port)" in page and 'placeholder="http://localhost:8080/docs"' in page


@pytest.mark.parametrize("host", ["example.com", "localhost.example.com", "127.0.0.1.example.com", "192.168.1.2", "0.0.0.0"])
def test_loopback_rule_rejects_remote_hosts_and_docs_links(fetch_app, host):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, content=f'<script>SwaggerUIBundle({{url: "http://{host}:8080/openapi.json"}})</script>',
                              headers={"content-type": "text/html"})
    with TestClient(fetch_app(handler, openapi_fetch_hosts="loopback:*")) as client:
        bid = client.post("/api/v1/businesses", json=dict(name="Target", description="Items")).json()["id"]
        endpoint = f"/api/v1/businesses/{bid}/specifications/fetch"
        assert client.post(endpoint, json=dict(url=f"http://{host}:8080/docs")).json()["code"] == "fetch_host"
        assert not seen
        assert client.post(endpoint, json=dict(url="http://localhost:8080/docs")).json()["code"] == "fetch_host"
        assert seen == ["http://localhost:8080/docs"]


@pytest.mark.parametrize("page", [get_swagger_ui_html(openapi_url="/openapi.json", title="Docs"), get_redoc_html(openapi_url="/openapi.json", title="Docs")])
def test_url_fetch_follows_docs_page_to_its_openapi_document(fetch_app, page):
    body = json.dumps(target_spec()).encode()
    seen = []
    def handler(request):
        seen.append(str(request.url))
        if request.url.path == "/docs":
            return httpx.Response(200, content=page.body, headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})
    with TestClient(fetch_app(handler)) as client:
        bid = client.post("/api/v1/businesses", json=dict(name="Target", description="Items")).json()["id"]
        r = client.post(f"/api/v1/businesses/{bid}/specifications/fetch", json=dict(url="http://127.0.0.1:8001/docs"))
        assert r.status_code == 200, r.text
        source = r.json()["inventory"]["source"]
        assert source["url"] == "http://127.0.0.1:8001/openapi.json" and source["docs_page"] == "http://127.0.0.1:8001/docs"
    assert seen == ["http://127.0.0.1:8001/docs", "http://127.0.0.1:8001/openapi.json"]


@pytest.mark.parametrize("html,code", [('<script>SwaggerUIBundle({url: "http://example.com/openapi.json"})</script>', "fetch_host"),
                                       ('<script>SwaggerUIBundle({url: "/docs"})</script>', "file_type")])
def test_url_fetch_docs_link_is_checked_and_followed_once(fetch_app, html, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=html.encode(), headers={"content-type": "text/html"})
    with TestClient(fetch_app(handler)) as client:
        bid = client.post("/api/v1/businesses", json=dict(name="Target", description="Items")).json()["id"]
        assert client.post(f"/api/v1/businesses/{bid}/specifications/fetch", json=dict(url="http://127.0.0.1:8001/docs")).json()["code"] == code
    assert len(calls) == (1 if code == "fetch_host" else 2)


def test_generation_scope_limits_model_inventory_and_is_recorded(env):
    app, client, _ = env
    service = app.state.service
    b = client.post("/api/v1/businesses", json=dict(name="Scope", description="Items")).json()
    spec = service.upload(b["id"], "openapi.json", json.dumps(target_spec()).encode())
    inv = spec["inventory"]
    item, signup = op(inv, "GET", "/api/v1/items/{id}"), op(inv, "POST", "/api/v1/users/signup")
    sent = []
    class Capture(DeterministicModelSubstitute):
        def call(self, kind, payload, output_model, run):
            sent.append([o["id"] for o in payload["inventory"]["operations"]])
            return GenerationOutput(proposals=[proposal(item, dict(name="title", step_id="s1", response_status="200", pointer="/title"))], capability_gaps=[])
    service.providers = Capture(None, app.state.store)
    r = client.post(f'/api/v1/inventories/{spec["id"]}/proposal-runs', json=dict(operation_ids=[item["id"]]))
    assert r.status_code == 200, r.text
    assert sent == [[item["id"]]]
    assert service.view(r.json()["proposal_ids"][0])["derived"]["generation_scope"] == [item["id"]]
    for ids in ([signup["id"]], ["made-up"]):
        assert client.post(f'/api/v1/inventories/{spec["id"]}/proposal-runs', json=dict(operation_ids=ids)).json()["code"] == "operation_scope"
