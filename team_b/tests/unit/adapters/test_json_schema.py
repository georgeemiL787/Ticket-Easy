from team_b.adapters.standins.json_schema import validate

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "n": {"type": ["number", "null"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "kind": {"type": "string", "enum": ["a", "b"]},
        "inner": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
    },
    "required": ["name"],
    "additionalProperties": False,
}


def test_a_fitting_value_has_no_problems() -> None:
    assert validate(SCHEMA, {"name": "x", "n": None, "tags": ["a"], "kind": "a", "inner": {"x": 1}}) == []


def test_each_kind_of_problem_is_reported_with_its_path() -> None:
    problems = validate(SCHEMA, {"n": "5", "tags": ["a", 2], "kind": "z", "inner": {}, "extra": 1})
    assert "arguments: missing required field name" in problems
    assert "n: expected number or null" in problems
    assert "tags[1]: expected string" in problems
    assert "kind: must be one of ['a', 'b']" in problems
    assert "inner: missing required field x" in problems
    assert "arguments: unknown field extra" in problems


def test_booleans_are_not_numbers_and_the_root_must_match() -> None:
    assert validate({"type": "number"}, True) == ["arguments: expected number"]
    assert validate({"type": "integer"}, 1.5) == ["arguments: expected integer"]
    assert validate(SCHEMA, []) == ["arguments: expected object"]
