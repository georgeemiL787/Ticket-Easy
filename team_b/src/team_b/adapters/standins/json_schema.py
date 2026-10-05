"""A tiny JSON Schema checker for the subset the shop tool schemas use.

Supported keywords: type (one name or a list), properties, required, additionalProperties (false), items, enum.
It returns readable problems instead of raising, so the shop can answer INVALID_ARGUMENTS.
"""

from typing import Any

_TYPES: dict[str, Any] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "null": lambda v: v is None,
}


def validate(schema: dict[str, Any], value: Any, path: str = "") -> list[str]:
    """Return a list of problems (empty when the value fits the schema)."""
    where = path or "arguments"
    expected = schema.get("type")
    if expected is not None:
        names = [expected] if isinstance(expected, str) else list(expected)
        if not any(_TYPES[name](value) for name in names):
            return [f"{where}: expected {' or '.join(names)}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where}: must be one of {schema['enum']}"]
    problems: list[str] = []
    if isinstance(value, dict):
        properties: dict[str, Any] = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                problems.append(f"{where}: missing required field {key}")
        for key, item in value.items():
            if key in properties:
                problems += validate(properties[key], item, f"{path}.{key}" if path else key)
            elif schema.get("additionalProperties") is False:
                problems.append(f"{where}: unknown field {key}")
    elif isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            problems += validate(schema["items"], item, f"{where}[{i}]")
    return problems
