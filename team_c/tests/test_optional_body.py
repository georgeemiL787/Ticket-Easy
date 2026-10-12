"""Optional request bodies: omitted when nothing is supplied, validated against the complete schema when sent."""
import json
import re
import httpx
import pytest
from team_c.artifacts import compile_steps
from team_c.config import AppError
from team_c.discovery import discover
from team_c.executor import Run
from team_c.grounding import validate_proposal
from team_c.models import ProposalContent

CREATED = {"201": {"description": "Created", "content": {"application/json": {"schema": {"type": "object", "properties": {"id": {"type": "integer"}}}}}}}
DOCUMENT = {"openapi": "3.0.3", "info": {"title": "TEST-ONLY bodies", "version": "1"}, "paths": {
    "/notes": {"post": {"operationId": "createNote", "responses": CREATED, "requestBody": {"required": False, "content": {"application/json": {"schema": {
        "type": "object", "required": ["title"], "properties": {"title": {"type": "string", "minLength": 1}, "tag": {"type": "string"}}}}}}}},
    "/orders": {"post": {"operationId": "createOrder", "responses": CREATED, "requestBody": {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "required": ["item"], "properties": {"item": {"type": "string"}, "qty": {"type": "integer"}}}}}}}},
    "/blobs": {"post": {"operationId": "createBlob", "responses": CREATED, "requestBody": {"content": {"application/json": {"schema": {
        "type": "object", "minProperties": 1}}}}}},
    "/marks": {"post": {"operationId": "createMark", "responses": CREATED, "requestBody": {"content": {"application/json": {"schema": {
        "type": "object", "nullable": True, "maxProperties": 2, "additionalProperties": {"type": "integer"}}}}}}},
    "/closed": {"post": {"operationId": "createClosed", "responses": CREATED, "requestBody": {"required": True, "content": {"application/json": {"schema": {
        "type": "object", "additionalProperties": False}}}}}},
    "/flags": {"post": {"operationId": "createFlag", "responses": CREATED, "requestBody": {"content": {"application/json": {"schema": {
        "type": "object", "maxProperties": 1, "properties": {"label": {"type": "string", "nullable": True}, "level": {"type": "integer"}}}}}}}},
    "/drafts/{id}": {"get": {"operationId": "getDraft", "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
        "responses": {"200": {"description": "Draft", "content": {"application/json": {"schema": {"type": "object", "properties": {"title": {"type": "string"}}}}}}}}}}}


@pytest.fixture(scope="module")
def ops():
    inventory = discover(json.dumps(DOCUMENT).encode(), "bodies.json", "b")[1]
    return {o["operation_id"]: o for o in inventory["operations"]}, inventory


def runtime(target, ref):
    return dict(target=target, kind="runtime_argument", reference=ref, step_id=None, response_status=None)


def execute(ops, plan, arguments, respond=lambda request: httpx.Response(201, json={"id": 1})):
    """plan: [(operationId, bindings)] run as steps s1, s2, ..."""
    by_name, _ = ops
    content = dict(steps=[dict(id=f"s{i}", operation_id=by_name[name]["id"], bindings=bindings) for i, (name, bindings) in enumerate(plan, 1)])
    missing = []
    steps = compile_steps(content, {o["id"]: o for o in by_name.values()}, missing)
    assert missing == []
    sent = []
    def target(request):
        sent.append(request)
        return respond(request)
    artifact = dict(steps=steps, outputs=[], output_schema=dict(required=[]), configuration={}, access_requirements=[], connector=dict(auth=None),
                    proposal=dict(business_id="b"), limits=dict(timeout_seconds=5, max_request_bytes=10_000, max_response_bytes=10_000))
    report, _ = Run(artifact, arguments, {}, "http://target.test", httpx.MockTransport(target)).execute()
    return report, sent, steps


def run(ops, operation, bindings, arguments):
    return execute(ops, [(operation, bindings)], arguments)


def refused(report, sent, *keywords):
    """Refused before sending; the detail names validator keywords only and the report says no write was sent."""
    assert sent == [] and report["status"] == "failed" and report["failure"]["outcome"] == "invalid_request"
    assert re.search(r"\(violates ([^)]*)\)", report["failure"]["detail"]).group(1).split(", ") == sorted(keywords)
    assert report["message"].endswith("No write was sent.") and report["trace"][-1]["write_state"] == "not_applied"


NOTE = [runtime("body.title", "title"), runtime("body.tag", "tag")]


def test_discovery_keeps_properties_required_whenever_an_optional_body_is_sent(ops):
    by_name, _ = ops
    inputs = by_name["createNote"]["inputs"]
    assert inputs["body.title"]["required"] is False and inputs["body.title"]["required_when_body_sent"] is True
    assert "required_when_body_sent" not in inputs["body.tag"] and by_name["createOrder"]["inputs"]["body.item"]["required"] is True


def test_omitted_optional_body_is_not_sent(ops):
    report, sent, steps = run(ops, "createNote", NOTE, {})
    assert report["status"] == "succeeded" and len(sent) == 1
    assert sent[0].content == b"" and "content-type" not in sent[0].headers
    assert steps[0]["body"]["required"] is False and steps[0]["body"]["schema"]["required"] == ["title"]


def test_optional_body_missing_a_required_sibling_is_refused_before_sending(ops):
    report, sent, _ = run(ops, "createNote", NOTE, dict(tag="urgent"))
    assert sent == [] and report["status"] == "failed" and report["failure"]["outcome"] == "invalid_request"
    assert "violates required" in report["failure"]["detail"] and "No write was sent." in report["message"]


def test_valid_optional_body_is_sent(ops):
    report, sent, _ = run(ops, "createNote", NOTE, dict(title="Leaking tap"))
    assert report["status"] == "succeeded" and json.loads(sent[0].content) == {"title": "Leaking tap"}
    report, sent, _ = run(ops, "createNote", NOTE, dict(title=""))
    assert sent == [] and "violates minLength" in report["failure"]["detail"]


def test_required_body_is_always_validated(ops):
    bindings = [runtime("body.item", "item"), runtime("body.qty", "qty")]
    report, sent, steps = run(ops, "createOrder", bindings, dict(qty=2))
    assert sent == [] and report["failure"]["outcome"] == "invalid_request" and steps[0]["body"]["required"] is True
    report, sent, _ = run(ops, "createOrder", bindings, {})
    assert sent == [] and "violates required" in report["failure"]["detail"]
    report, sent, _ = run(ops, "createOrder", bindings, dict(item="soap", qty=2))
    assert report["status"] == "succeeded" and json.loads(sent[0].content) == {"item": "soap", "qty": 2}


def test_explicit_empty_whole_body_is_a_supplied_value_and_is_validated(ops):
    bindings = [runtime("body", "payload")]
    report, sent, _ = run(ops, "createBlob", bindings, {})
    assert report["status"] == "succeeded" and sent[0].content == b""
    report, sent, _ = run(ops, "createBlob", bindings, dict(payload={}))
    assert sent == [] and "violates minProperties" in report["failure"]["detail"]
    report, sent, _ = run(ops, "createBlob", bindings, dict(payload={"k": 1}))
    assert json.loads(sent[0].content) == {"k": 1}


def test_artifacts_built_before_body_schemas_keep_sending_a_body(ops):
    by_name, _ = ops
    op = by_name["createNote"]
    steps = compile_steps(dict(steps=[dict(id="s1", operation_id=op["id"], bindings=NOTE)]), {op["id"]: op}, [])
    for key in ("required", "schema"):
        steps[0]["body"].pop(key)
    sent = []
    artifact = dict(steps=steps, outputs=[], output_schema=dict(required=[]), configuration={}, access_requirements=[], connector=dict(auth=None),
                    proposal=dict(business_id="b"), limits=dict(timeout_seconds=5, max_request_bytes=10_000, max_response_bytes=10_000))
    Run(artifact, {}, {}, "http://target.test", httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(201, json={"id": 1}))).execute()
    assert json.loads(sent[0].content) == {}


def test_explicit_null_whole_body_is_a_supplied_value_and_is_validated(ops):
    report, sent, _ = run(ops, "createBlob", [runtime("body", "payload")], dict(payload=None))
    refused(report, sent, "type")
    report, sent, _ = run(ops, "createMark", [runtime("body", "payload")], dict(payload=None))
    assert report["status"] == "succeeded" and sent[0].content == b"null" and sent[0].headers["content-type"] == "application/json"
    report, sent, _ = run(ops, "createMark", [runtime("body", "payload")], {})
    assert report["status"] == "succeeded" and sent[0].content == b""


FLAG = [runtime("body.label", "label"), runtime("body.level", "level")]


def test_explicit_null_field_is_a_supplied_value_and_is_validated(ops):
    report, sent, _ = run(ops, "createFlag", FLAG, {})
    assert report["status"] == "succeeded" and sent[0].content == b""
    report, sent, _ = run(ops, "createFlag", FLAG, dict(label=None))
    assert report["status"] == "succeeded" and json.loads(sent[0].content) == {"label": None}
    report, sent, _ = run(ops, "createFlag", FLAG, dict(level=None))
    refused(report, sent, "type")


def test_body_level_constraints_apply_to_the_assembled_body_and_details_never_echo_values(ops):
    report, sent, _ = run(ops, "createFlag", FLAG, dict(label="a", level=1))
    refused(report, sent, "maxProperties")
    report, sent, _ = run(ops, "createFlag", FLAG, dict(label=5, level="secret-level"))
    refused(report, sent, "maxProperties", "type")
    report, sent, _ = run(ops, "createMark", [runtime("body", "payload")], dict(payload={"a": 1, "b": 2, "c": 3}))
    refused(report, sent, "maxProperties")
    report, sent, _ = run(ops, "createMark", [runtime("body", "payload")], dict(payload={"a": "secret-mark"}))
    refused(report, sent, "type")
    assert "secret-mark" not in json.dumps(report)
    report, sent, _ = run(ops, "createClosed", [runtime("body", "payload")], dict(payload={"undeclared_name": "secret-value"}))
    refused(report, sent, "additionalProperties")
    assert "secret-value" not in json.dumps(report) and "undeclared_name" not in json.dumps(report)


def test_body_from_a_previous_output_is_validated_at_its_step(ops):
    plan = [("getDraft", [runtime("path.id", "draft")]),
            ("createNote", [dict(target="body.title", kind="previous_operation_output", reference="/title", step_id="s1", response_status="200")])]
    draft = lambda title: lambda request: httpx.Response(200, json={"title": title}) if request.method == "GET" else httpx.Response(201, json={"id": 1})
    report, sent, _ = execute(ops, plan, dict(draft="d1"), draft(""))
    assert [r.method for r in sent] == ["GET"] and report["failure"]["step_id"] == "s2"
    refused(report, [], "minLength")
    assert report["trace"][0]["outcome"] == "succeeded"
    report, sent, _ = execute(ops, plan, dict(draft="d1"), draft("Fix the tap"))
    assert report["status"] == "succeeded" and json.loads(sent[1].content) == {"title": "Fix the tap"}


def test_a_refused_body_after_an_applied_write_is_reported_as_partial(ops):
    plan = [("createOrder", [runtime("body.item", "item")]), ("createNote", NOTE)]
    report, sent, _ = execute(ops, plan, dict(item="soap", tag="urgent"))
    assert len(sent) == 1 and report["status"] == "partial" and report["failure"]["outcome"] == "invalid_request"
    assert [e["write_state"] for e in report["trace"]] == ["applied", "not_applied"]
    assert report["message"].startswith("s1 applied a change before s2 failed") and "not rolled back" in report["message"]


def test_grounding_requires_required_siblings_of_an_optional_body(ops):
    by_name, inventory = ops
    op = by_name["createNote"]
    proposal = dict(name="TEST-ONLY note", description="d", business_purpose="p", steps=[dict(id="s1", operation_id=op["id"], purpose="Create", bindings=[runtime("body.tag", "tag")])],
                    configuration=[], questions=[], outputs=[dict(name="id", step_id="s1", response_status="201", pointer="/id")],
                    expected_reads=[], expected_writes=["Note"], assumptions=[], limitations=[], risk="low", risk_rationale="r")
    with pytest.raises(AppError) as exc:
        validate_proposal(ProposalContent.model_validate(proposal), inventory)
    assert "Missing required body sibling s1:body.title" in exc.value.details["errors"]
    proposal["steps"][0]["bindings"] = NOTE
    assert validate_proposal(ProposalContent.model_validate(proposal), inventory)["runtime_inputs"]["title"]["required"] is False
