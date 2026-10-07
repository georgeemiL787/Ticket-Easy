"""The same compiler contract exercised across unrelated synthetic APIs."""
import copy
import json
import httpx
import pytest
from team_c.artifacts import compile_artifact
from team_c.compiler.policies import AccessPolicy, resolve_inputs
from team_c.compiler.readiness import assess
from team_c.compiler.semantics import enrich, input_semantics
from team_c.compiler.security_tests import run
from team_c.config import AppError
from team_c.discovery import discover
from team_c.executor import Run, credentials, validate_arguments
from team_c.persistence.util import digest
from team_c.requirements import derive


DOMAINS = [("healthcare", "appointments", "patient", "clinic"), ("banking", "transfers", "beneficiary", "institution"),
           ("logistics", "shipments", "recipient", "depot"), ("HR", "leave", "employee", "division"),
           ("opaque", "r_83", "v_19", "v_77")]


def fixture(domain):
    area, resource, owner, tenant = domain
    response = dict(type="object", properties={"ref": {"type": "string"}, owner: {"type": "string"}, tenant: {"type": "string"}}, required=["ref", owner, tenant])
    path = "/" + resource + "/{ref}"
    params = [dict(name="ref", **{"in": "path"}, required=True, schema={"type": "string"})]
    document = dict(openapi="3.0.3", info=dict(title=area, version="1"), security=[{"bearer": []}],
                    components=dict(securitySchemes=dict(bearer=dict(type="http", scheme="bearer"))), paths={path: {
        "get": dict(summary="Read " + resource, parameters=params, responses={"200": dict(description="Found", content={"application/json": {"schema": response}})}),
        "post": dict(summary="Change " + resource, parameters=params, requestBody=dict(required=True, content={"application/json": {"schema": dict(type="object", properties={
            "choice": {"type": "string"}, owner: {"type": "string", "x-semantic-role": "identity_context"},
            "computed": {"type": "number", "readOnly": True}}, required=["choice", owner])}}),
            responses={"200": dict(description="Updated", content={"application/json": {"schema": response}})})}})
    _, inventory = discover(json.dumps(document).encode(), "json", "business", 100)
    inventory = enrich(inventory, area)
    ops = {o["method"]: o for o in inventory["operations"]}
    def binding(target, ref):
        return dict(target=target, kind="runtime_argument", reference=ref, step_id=None, response_status=None)
    content = dict(name="Perform business action", business_purpose="Update an authorized resource", description="Read and update the resource", steps=[
        dict(id="s1", operation_id=ops["GET"]["id"], purpose="Check resource", bindings=[binding("path.ref", "record")]),
        dict(id="s2", operation_id=ops["POST"]["id"], purpose="Apply change", bindings=[binding("path.ref", "record"), binding("body.choice", "choice"), binding("body." + owner, "actor")])],
        outputs=[dict(name="result", step_id="s2", response_status="200", pointer="")], configuration=[], expected_reads=["Resource"], expected_writes=["Change"], assumptions=[], questions=[], limitations=[], risk="medium", risk_rationale="Writes")
    reqs = [dict(r, status="owner_confirmed", answer=dict(text="Test-only accepted intent")) for r in derive(content["steps"], inventory)]
    checks = []
    for r in reqs:
        if r["kind"] == "record_scope":
            for kind, field, context in (("ownership", owner, "subject"), ("tenant", tenant, "tenant")):
                checks.append(dict(requirement_id=r["id"], kind=kind, resource=resource, step_id="s1", response_status="200", resource_field="/" + field, identity_source=context))
    policy = AccessPolicy(principals=["resource_owner"], authentication="required", role_source="roles", roles=["operator"], permission_source="permissions", permissions=["change"], checks=checks,
        inputs=[dict(step_id="s2", target="body." + owner, category="identity_context", source="trusted_application_context", reference="subject", reason="Registered principal supplies identity")],
        financial=False, irreversible=False, external_side_effects=False, idempotent=False).model_dump()
    record = dict(id="policy", sha256=digest(policy), content=policy, accepted_by="TEST OWNER")
    connector = dict(id="test", base_url="http://synthetic.test", context_fields=["subject", "tenant", "roles", "permissions"])
    return inventory, content, reqs, record, connector, owner, tenant


def compiled(domain):
    inventory, content, reqs, record, connector, owner, tenant = fixture(domain)
    artifact, sha = compile_artifact(content, {}, inventory, reqs, dict(id="proposal", version=1, business_id="business"), {}, connector, None, record)
    return artifact, owner, tenant


def identity():
    return dict(scope="end_user", token="synthetic", context=dict(subject="a", tenant="t", roles=["operator"], permissions=["change"]))


@pytest.mark.parametrize("domain", DOMAINS)
def test_cross_domain_compilation_and_runtime_boundaries(domain):
    artifact, owner, tenant = compiled(domain)
    assert set(artifact["input_schema"]["properties"]) == {"record", "choice"}
    assert artifact["format"].endswith("/3")
    assert run(artifact)["passed"]
    assert artifact["access_policy"]["evidence"][0]["scope"] == "policy_decision"
    sent = []
    def target(request):
        sent.append(request)
        if request.method == "POST":
            assert json.loads(request.content)[owner] == "a"
            assert "computed" not in json.loads(request.content)
        return httpx.Response(200, json=dict(ref="x", **{owner: "a", tenant: "t"}))
    report, _ = Run(artifact, dict(record="x", choice="selected"), identity(), "http://synthetic.test", httpx.MockTransport(target)).execute()
    assert report["status"] == "succeeded"
    assert [r.method for r in sent] == ["GET", "POST"]
    for field, value in ((owner, "other"), (tenant, "other")):
        sent.clear()
        def wrong_scope(request):
            sent.append(request)
            return httpx.Response(200, json=dict(ref="x", **{owner: "a", tenant: "t", field: value}))
        report, output = Run(artifact, dict(record="x", choice="selected"), identity(), "http://synthetic.test", httpx.MockTransport(wrong_scope)).execute()
        assert report["status"] == "failed" and output is None
        assert [r.method for r in sent] == ["GET"]
    for key in ("actor", "computed", "body." + owner):
        with pytest.raises(AppError, match="Arguments"):
            validate_arguments(artifact["input_schema"], dict(record="x", choice="selected", **{key: "override"}))


@pytest.mark.parametrize("domain", DOMAINS)
def test_protected_inputs_fail_closed_without_mapping(domain):
    inventory, content, reqs, _, connector, _, _ = fixture(domain)
    with pytest.raises(AppError) as error:
        compile_artifact(content, {}, inventory, reqs, {}, {}, connector, None)
    assert error.value.code == "semantic_inputs_unresolved"
    assert assess(content, inventory)["build_ready"] is False


def test_trusted_roles_permissions_and_missing_context_rejected_before_io():
    artifact, _, _ = compiled(DOMAINS[0])
    for change in (dict(scope="service"), dict(token=""), dict(context={}), dict(context=dict(subject="a", tenant="t", roles=["visitor"], permissions=["change"]))):
        with pytest.raises(AppError):
            credentials(dict(identity(), **change), artifact)


def test_inferred_context_and_nested_server_fields_never_silently_exposed():
    op = dict(source_pointer="#/paths/x", inputs={"body.payload": dict(required=True, schema={"type": "object", "properties": {"x": {"type": "string", "readOnly": True}}}),
              "query.opaque": dict(required=True, schema={"type": "string"}, description="Derived from session")},
              request_body=dict(content={"application/json": {"schema": {"type": "object", "properties": {"payload": {"type": "object", "properties": {"x": {"type": "string", "readOnly": True}}}}}}}))
    meanings = input_semantics(op)
    assert meanings["query.opaque"]["category"] == "identity_context"
    assert meanings["query.opaque"]["state"] == "INFERRED"
    assert meanings["body.payload.x"]["state"] == "DECLARED"
    content = dict(steps=[dict(id="s", operation_id="op", bindings=[dict(target="body.payload", kind="runtime_argument", reference="payload")])])
    _, _, problems = resolve_inputs(content, dict(operations=[dict(op, id="op")]), None)
    assert problems


def test_readiness_score_never_overrides_unresolved_side_effects():
    inventory, content, _, record, _, _, _ = fixture(DOMAINS[1])
    record["content"]["irreversible"] = None
    readiness = assess(content, inventory, record["content"])
    assert not readiness["build_ready"]
    assert "Writes require" in " ".join(readiness["blockers"])


def test_missing_scope_and_invalid_context_do_not_compile():
    inventory, content, reqs, record, connector, _, _ = fixture(DOMAINS[0])
    connector["context_fields"].remove("subject")
    with pytest.raises(AppError) as exc:
        compile_artifact(content, {}, inventory, reqs, {}, {}, connector, None, record)
    assert exc.value.code == "policy_context_missing"


@pytest.mark.parametrize("principal,authenticated", [("public", False), ("guest", False), ("service", True), ("admin", True)])
def test_explicit_public_guest_and_service_policies(principal, authenticated):
    inventory, content, reqs, record, connector, _, _ = fixture(DOMAINS[4])
    policy = record["content"]
    policy.update(principals=[principal], authentication="required" if authenticated else "none", checks=[], roles=[], role_source=None, permissions=[], permission_source=None,
                  scopes=[dict(requirement_id=r["id"], mode="unrestricted", reason="Explicit test intent for all records") for r in reqs if r["kind"] == "record_scope"])
    if not authenticated:
        for op in inventory["operations"]:
            op["declared_auth"] = dict(status="explicitly_none", alternatives=[])
    record["sha256"] = digest(policy)
    artifact, _ = compile_artifact(content, {}, inventory, reqs, dict(business_id="b"), {}, connector, None, record)
    assert run(artifact)["passed"]
    credentials(dict(identity(), scope=principal), artifact)
    with pytest.raises(AppError):
        credentials(dict(identity(), scope="end_user"), artifact)


@pytest.mark.parametrize("location", ["header", "cookie"])
def test_runtime_transport_context_is_not_a_tool_argument(location):
    inventory, content, reqs, record, connector, _, _ = fixture(DOMAINS[4])
    op = next(o for o in inventory["operations"] if o["method"] == "POST")
    op["inputs"][location + ".opaque"] = dict(required=True, schema={"type": "string"})
    op["parameters"].append(dict(name="opaque", **{"in": location}, required=True, schema={"type": "string"}))
    content["steps"][1]["bindings"].append(dict(target=location + ".opaque", kind="runtime_argument", reference="session", step_id=None, response_status=None))
    record["content"]["inputs"].append(dict(step_id="s2", target=location + ".opaque", category="runtime_context", source="trusted_application_context", reference="session", reason="Trusted session"))
    connector["context_fields"].append("session")
    record["sha256"] = digest(record["content"])
    artifact, _ = compile_artifact(content, {}, inventory, reqs, dict(business_id="b"), {}, connector, None, record)
    assert "session" not in artifact["input_schema"]["properties"]
    who = identity()
    who["context"]["session"] = "trusted-session"
    runner = Run(artifact, {}, who, "http://synthetic.test", None)
    headers = runner.transport_headers(artifact["steps"][1], {"Authorization": "Bearer trusted"})
    assert headers["opaque" if location == "header" else "Cookie"] == ("trusted-session" if location == "header" else "opaque=trusted-session")
    runner.client.close()


def test_negative_generated_tests_fail_if_authorizer_is_disabled(monkeypatch):
    artifact, _, _ = compiled(DOMAINS[4])
    monkeypatch.setattr("team_c.compiler.security_tests.authorize", lambda *_: None)
    report = run(artifact)
    assert not report["passed"]
    assert any(not case["passed"] for case in report["cases"] if case["kind"] == "negative")
