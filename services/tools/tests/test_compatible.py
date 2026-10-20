"""compatible(): every value the source schema allows must satisfy the target, including additional properties."""
from openapi_schema_validator import OAS30Validator
from team_c.grounding import compatible

INT = {"type": "integer"}
STR = {"type": "string"}


def obj(properties=None, required=(), **extra):
    return dict(type="object", properties=properties or {}, required=list(required), **extra)


def test_reported_case_open_source_into_typed_additional_properties():
    source, target = obj({"id": INT}), obj({"id": INT}, additionalProperties=INT)
    value = {"id": 1, "extra": "invalid"}
    assert OAS30Validator(source).is_valid(value) and not OAS30Validator(target).is_valid(value)
    assert compatible(source, target) is False


def test_incompatible_additional_property_policies():
    cases = {
        "open source, closed target": (obj({"id": INT}), obj({"id": INT}, additionalProperties=False)),
        "true source, typed target": (obj({"id": INT}, additionalProperties=True), obj({"id": INT}, additionalProperties=INT)),
        "empty-schema source, typed target": (obj({"id": INT}, additionalProperties={}), obj({"id": INT}, additionalProperties=INT)),
        "typed source, differently typed target": (obj({"id": INT}, additionalProperties=STR), obj({"id": INT}, additionalProperties=INT)),
        "typed source, closed target": (obj({"id": INT}, additionalProperties=INT), obj({"id": INT}, additionalProperties=False)),
        "declared source field outside a closed target": (obj({"id": INT, "name": STR}, additionalProperties=False), obj({"id": INT}, additionalProperties=False)),
        "declared source field against typed target extras": (obj({"id": INT, "name": STR}, additionalProperties=False), obj({"id": INT}, additionalProperties=INT)),
        "open source may carry a constrained optional target field": (obj({"id": INT}), obj({"id": INT, "count": INT})),
        "typed source extras against an optional target field": (obj({"id": INT}, additionalProperties=STR), obj({"id": INT, "count": INT})),
        "nested open object": (obj({"meta": obj({"a": INT})}, additionalProperties=False), obj({"meta": obj({"a": INT}, additionalProperties=INT)}, additionalProperties=False)),
        "pattern properties": (obj({"id": INT}, additionalProperties=False, patternProperties={"^x": STR}), obj({"id": INT})),
    }
    for name, (source, target) in cases.items():
        assert compatible(source, target) is False, name


def test_compatible_object_schemas_still_pass():
    cases = {
        "open target": (obj({"id": INT}, ["id"]), obj({"id": INT}, ["id"])),
        "true target": (obj({"id": INT}), obj({"id": INT}, additionalProperties=True)),
        "empty-schema target": (obj({"id": INT}), obj({"id": INT}, additionalProperties={})),
        "closed source into closed target": (obj({"id": INT}, additionalProperties=False), obj({"id": INT, "name": STR}, additionalProperties=False)),
        "closed source into typed target": (obj({"id": INT, "n": INT}, additionalProperties=False), obj({"id": INT}, additionalProperties=INT)),
        "same typed extras": (obj({"id": INT}, additionalProperties=INT), obj({"id": INT}, additionalProperties=INT)),
        "narrower typed extras": (obj({"id": INT}, additionalProperties=dict(INT, minimum=5)), obj({"id": INT}, additionalProperties=dict(INT, minimum=0))),
        "integer extras into number extras": (obj({}, additionalProperties=INT), obj({}, additionalProperties={"type": "number"})),
        "nested closed object": (obj({"meta": obj({"a": INT}, additionalProperties=False)}, additionalProperties=False),
                                 obj({"meta": obj({"a": INT}, additionalProperties=INT)}, additionalProperties=False)),
    }
    for name, (source, target) in cases.items():
        assert compatible(source, target) is True, name


def arr(items):
    return dict(type="array", items=items)


def test_nested_policies_arrays_of_objects_and_target_fields_reached_through_source_extras():
    incompatible = {
        "array items open into typed extras": (arr(obj({"id": INT})), arr(obj({"id": INT}, additionalProperties=INT))),
        "array items typed extras into closed items": (arr(obj({}, additionalProperties=INT)), arr(obj({}, additionalProperties=False))),
        "nested typed extras into narrower nested extras": (obj({"meta": obj({}, additionalProperties=INT)}, additionalProperties=False),
                                                           obj({"meta": obj({}, additionalProperties=dict(INT, minimum=0))}, additionalProperties=False)),
        "doubly nested open object": (obj({"a": obj({"b": obj({"c": INT})}, additionalProperties=False)}, additionalProperties=False),
                                      obj({"a": obj({"b": obj({"c": INT}, additionalProperties=False)}, additionalProperties=False)}, additionalProperties=False)),
        "source extras reach a narrower target field": (obj({}, additionalProperties=INT), obj({"count": dict(INT, minimum=1)})),
        "source extras reach a required target field": (obj({}, additionalProperties=INT), obj({"count": INT}, ["count"])),
        "nullable source extras reach a non-nullable target field": (obj({}, additionalProperties=dict(INT, nullable=True)), obj({"count": INT})),
    }
    for name, (source, target) in incompatible.items():
        assert compatible(source, target) is False, name
    compatible_cases = {
        "array items closed into typed extras": (arr(obj({"id": INT}, additionalProperties=False)), arr(obj({"id": INT}, additionalProperties=INT))),
        "array items with the same typed extras": (arr(obj({}, additionalProperties=INT)), arr(obj({}, additionalProperties=INT))),
        "source extras satisfy an optional target field": (obj({}, additionalProperties=INT), obj({"count": {"type": "number"}})),
        "doubly nested closed object": (obj({"a": obj({"b": obj({"c": INT}, additionalProperties=False)}, additionalProperties=False)}, additionalProperties=False),
                                        obj({"a": obj({"b": obj({"c": INT}, additionalProperties=INT)}, additionalProperties=False)}, additionalProperties=False)),
    }
    for name, (source, target) in compatible_cases.items():
        assert compatible(source, target) is True, name


def test_enum_values_compare_as_json_values():
    source, target = dict(STR, nullable=True, enum=["a", None]), dict(STR, nullable=True, enum=["a", "None"])
    assert OAS30Validator(source).is_valid(None) and not OAS30Validator(target).is_valid(None)
    assert compatible(source, target) is False
    assert compatible(dict(STR, nullable=True, enum=["a", None]), dict(STR, nullable=True, enum=[None, "a", "b"])) is True
