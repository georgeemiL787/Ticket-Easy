"""MCP schemas must validate as 2020-12 and retain the source's accepted values."""
import copy
import json

import pytest
from jsonschema import Draft202012Validator
from openapi_schema_validator import OAS30Validator

from team_c import publishing
from team_c.config import AppError
from team_c.discovery import discover
from team_c.persistence.util import digest, dump
from helpers.desk import generated, review_and_build
from helpers.publication import publish, with_evidence


@pytest.mark.parametrize("schema,values", [
    ({"type": "number", "minimum": 0, "exclusiveMinimum": True, "maximum": 10, "exclusiveMaximum": True}, [-1, 0, 1, 9, 10]),
    ({"type": "number", "minimum": 0, "exclusiveMinimum": False, "maximum": 10, "exclusiveMaximum": False}, [-1, 0, 10, 11]),
    ({"type": "number", "exclusiveMinimum": False}, [-1, 0, 1]),
    ({"type": "string", "nullable": True, "enum": ["yes"]}, [None, "yes", "no"]),
    ({"type": "string", "nullable": True, "enum": [None, "yes"]}, [None, "yes", "no"]),
    ({"type": "array", "nullable": True, "items": {"type": "number", "minimum": 2, "exclusiveMinimum": True}}, [None, [], [2], [3]]),
    ({"type": "object", "additionalProperties": {"type": "number", "maximum": 5, "exclusiveMaximum": True}}, [{}, {"x": 4}, {"x": 5}]),
])
def test_conversion_preserves_openapi_validation(schema, values):
    before = copy.deepcopy(schema)
    converted = publishing.json_schema(schema)
    Draft202012Validator.check_schema(converted)
    assert schema == before
    source, target = OAS30Validator(schema), Draft202012Validator(converted)
    for value in values:
        assert target.is_valid(value) == source.is_valid(value), (schema, value)


@pytest.mark.parametrize("version,bound", [("3.0.3", {"minimum": 0, "exclusiveMinimum": True}), ("3.1.0", {"exclusiveMinimum": 0})])
def test_discovery_to_mcp_preserves_exclusive_bounds(version, bound):
    schema = dict(type="object", properties={"amount": dict(type="number", **bound)})
    doc = dict(openapi=version, info=dict(title="TEST-ONLY", version="1"), paths={"/quote": {"post": {
        "requestBody": {"content": {"application/json": {"schema": schema}}},
        "responses": {"200": {"description": "quote", "content": {"application/json": {"schema": schema}}}},
    }}})
    _, inventory = discover(json.dumps(doc).encode(), "quote.json", "test")
    op = inventory["operations"][0]
    assert op["proposal_eligible"]
    for source in (op["request_body"]["content"]["application/json"]["schema"], op["responses"]["200"]["schema"]):
        converted = publishing.json_schema(source)
        Draft202012Validator.check_schema(converted)
        validator = Draft202012Validator(converted)
        assert validator.is_valid({"amount": 1}) and not validator.is_valid({"amount": 0})


def test_annotations_are_data_and_nested_schema_locations_are_converted():
    example = {"type": "number", "exclusiveMinimum": True, "minimum": 0}
    schema = {"type": "object", "default": example, "example": example,
              "properties": {"exclusiveMinimum": example}, "$defs": {"value": example},
              "anyOf": [{"properties": {"n": example}}]}
    converted = publishing.json_schema(schema)
    Draft202012Validator.check_schema(converted)
    assert converted["default"] == example and converted["example"] == example
    assert converted["properties"]["exclusiveMinimum"]["exclusiveMinimum"] == 0
    assert converted["$defs"]["value"]["exclusiveMinimum"] == 0


@pytest.mark.parametrize("field", ["input_schema", "output_schema"])
def test_invalid_schemas_are_rejected(field):
    content = dict(input_schema={"type": "object"}, output_schema={"type": "object"})
    content[field] = {"type": "object", "properties": {"bad": {"type": "invalid-type"}}}
    with pytest.raises(AppError) as exc:
        publishing.tool_schemas(content)
    assert exc.value.code == "invalid_mcp_schema"


@pytest.mark.parametrize("status", publishing.STATUSES)
def test_result_schema_accepts_all_envelopes_including_unattributed_errors(status):
    validator = Draft202012Validator(publishing.tool_schemas(dict(input_schema={"type": "object"}, output_schema={"type": "object"}))["output_schema"])
    pub = dict(id="pub", tool_name="tool", artifact_id="a", artifact_sha256="hash", proposal_id="p", version=1)
    for publication in (pub, None):
        validator.validate(publishing.envelope(publication, status, output={} if status == "succeeded" else None))


def test_invalid_legacy_artifact_schema_blocks_publication(desk):
    d = desk
    artifact = review_and_build(d, generated(d))
    with_evidence(d, artifact["id"])
    # Simulate an immutable record produced by a historical buggy compiler, with a valid hash.
    content = copy.deepcopy(artifact["content"])
    content["input_schema"]["properties"]["broken"] = {"type": "not-a-type"}
    with d.service.store.connect(write=True) as conn:
        conn.execute("UPDATE artifacts SET content=?,sha256=? WHERE id=?", (dump(content), digest(content), artifact["id"]))
    response = publish(d, artifact["id"])
    assert response.status_code == 409
    assert any("invalid_mcp_schema" in p for p in response.json()["details"]["problems"])
