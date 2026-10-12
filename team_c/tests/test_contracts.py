"""Records produced at the module boundaries have exactly the keys team_c.contracts documents."""
import json
import types
import typing
import httpx
from conftest import ROOT
from team_c import contracts
from team_c.artifacts import FORMAT, compile_artifact
from team_c.discovery import discover
from team_c.executor import Run
from team_c.grounding import validate_proposal
from team_c.models import ProposalContent


def conforms(value, hint, where="$"):
    """Keys of every TypedDict level are within its documented keys and include the required ones; Literals hold."""
    origin, args = typing.get_origin(hint), typing.get_args(hint)
    if typing.is_typeddict(hint):
        assert isinstance(value, dict), where
        required, optional = hint.__required_keys__, hint.__optional_keys__
        assert required <= set(value) <= required | optional, (where, sorted(required - set(value)), sorted(set(value) - required - optional))
        hints = typing.get_type_hints(hint)
        for key, child in value.items():
            conforms(child, hints[key], f"{where}.{key}")
    elif origin is typing.Literal:
        assert value in args, (where, value)
    elif origin in (typing.Union, types.UnionType):
        options = [a for a in args if a is not type(None)]
        if value is not None and len(options) == 1:
            conforms(value, options[0], where)
    elif origin is list:
        for i, item in enumerate(value):
            conforms(item, args[0], f"{where}[{i}]")
    elif origin is dict:
        for key, item in value.items():
            conforms(item, args[1], f"{where}[{key}]")


def built():
    record = json.loads((ROOT / "examples/reviewed-ecommerce-proposal.json").read_text())
    inventory = discover((ROOT / "examples" / record["source_file"]).read_bytes(), record["source_file"], record["business_id"])[1]
    content = ProposalContent(**record["content"])
    derived = validate_proposal(content, inventory)
    artifact, sha = compile_artifact(content.model_dump(), derived, inventory, [], dict(id="p1", version=1, business_id=record["business_id"]),
                                     dict(spec_id="spec1", spec_sha256="0" * 64), dict(id="shop-sandbox", base_url="http://target.test"), None)
    return inventory, derived, artifact


def test_discovered_inventory_and_grounding_have_the_documented_keys():
    inventory, derived, _ = built()
    conforms(inventory, contracts.Inventory)
    conforms(derived, contracts.Grounding)
    op = inventory["operations"][1]
    assert set(op["inputs"]) == {"body.order_id", "body.customer_id", "body.summary", "body.queue_id"} and op["request_body"]["required"] is True
    blocked = discover(b'{"openapi": "3.2.0", "paths": {"/x": {"get": {}}}}', "x.json", "b")[1]
    conforms(blocked, contracts.Inventory)
    assert set(blocked["operations"][0]) == contracts.Operation.__required_keys__
    optional = discover(json.dumps({"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": {"/n": {"post": {
        "requestBody": {"content": {"application/json": {"schema": {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}}}},
        "responses": {"204": {"description": "ok"}}}}}}).encode(), "n.json", "b")[1]
    conforms(optional, contracts.Inventory)
    assert optional["operations"][0]["inputs"]["body.a"]["required_when_body_sent"] is True


def test_compiled_artifact_and_execution_reports_have_the_documented_keys():
    _, _, artifact = built()
    conforms(artifact, contracts.Artifact)
    assert artifact["format"] == FORMAT == "team_c.tool_artifact/2"
    s1, s2 = artifact["steps"]
    assert s1["body"] is None and set(s2["body"]) == contracts.CompiledBody.__required_keys__ | contracts.CompiledBody.__optional_keys__
    assert s2["body"]["required"] is True and s2["body"]["schema"]["required"] == ["order_id", "customer_id", "summary", "queue_id"]
    def target(request):
        if request.method == "GET":
            return httpx.Response(200, json=dict(order_id="o1", customer_id="c1", status="open"))
        return httpx.Response(201, json=dict(ticket_id="t1"))
    identity = dict(token="sandbox-token", scope="end_user")
    report, outputs = Run(artifact, dict(order_id="o1", summary="Broken"), identity, "http://target.test", httpx.MockTransport(target)).execute()
    conforms(report, contracts.ExecutionReport)
    assert report["status"] == "succeeded" and outputs == {"ticket_id": "t1"}
    report, _ = Run(artifact, dict(order_id="o1", summary="Broken"), identity, "http://target.test", httpx.MockTransport(lambda r: httpx.Response(404))).execute()
    conforms(report, contracts.ExecutionReport)
    assert report["status"] == "failed" and report["trace"][1] == dict(step_id="s2", method="POST", path="/tickets", effect="write", outcome="not_attempted", write_state="not_attempted")
    assert "sandbox-token" not in json.dumps(report)
