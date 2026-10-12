"""Automatic test plan: which sandbox tests a tool needs, derived from the artifact alone (deterministic, no model).

The plan mirrors `publishing.evidence()` and `publishing.next_steps()` exactly: it asks for the tests those functions
will require and nothing else, so a plan that passes is the evidence publication needs. Nothing here weakens a rule.

  always                          a success test as account A
  record-scope requirement        own_record A on A's record, own_record B on B's record (same kind), cross_user B -> A's record
  data-scope requirement          a success test as A and as B, each with its own data (the isolation pair)
  caller-access requirement       diagnostic: a revoked credential must be refused (not publication evidence)
  input schema                    diagnostic: an unknown argument must be rejected before any request (not publication evidence)

Argument values come from the input schema (enum, default, example, format, constraints); only what the schema cannot
decide is left `unresolved` for the model helper, and such values are marked `source: model` in the stored plan.
"""

EVIDENCE, DIAGNOSTIC = "evidence", "diagnostic"
FORMATS = {"date": "2026-01-15", "date-time": "2026-01-15T10:00:00Z", "email": "autotest@example.com", "uri": "https://example.com/autotest",
           "uuid": "123e4567-e89b-42d3-a456-426614174000", "time": "10:00:00"}


def schema_value(name, schema):
    """(value, source) for one input from its schema alone; source is `unresolved` when the schema cannot decide."""
    schema = schema or {}
    if schema.get("enum"):
        return schema["enum"][0], "enum"
    if "default" in schema and schema["default"] is not None:
        return schema["default"], "default"
    if schema.get("example") is not None:
        return schema["example"], "example"
    if schema.get("examples"):
        examples = schema["examples"]
        first = next(iter(examples.values()), None) if isinstance(examples, dict) else examples[0]
        if isinstance(first, dict) and "value" in first:
            first = first["value"]
        if first is not None:
            return first, "example"
    kind = schema.get("type")
    if kind == "boolean":
        return True, "placeholder"
    if kind in ("integer", "number"):
        low = schema.get("minimum")
        exclusive = schema.get("exclusiveMinimum")
        if isinstance(exclusive, (int, float)) and not isinstance(exclusive, bool):
            low = exclusive + 1
        elif exclusive is True and low is not None:
            low = low + 1
        value = low if low is not None else 1
        high = schema.get("maximum")
        if high is not None and value > high:
            return None, "unresolved"
        return (int(value) if kind == "integer" else float(value)), "placeholder"
    if kind == "array":
        return ([], "placeholder") if not schema.get("minItems") else (None, "unresolved")
    if kind == "object":
        return {}, "placeholder"
    if kind in (None, "string"):
        if schema.get("pattern") and not schema.get("format"):
            return None, "unresolved"
        if schema.get("format") in FORMATS:
            return FORMATS[schema["format"]], "format"
        text = "Automatic test"
        low, high = schema.get("minLength") or 0, schema.get("maxLength")
        if high is not None:
            text = text[:high]
        return text.ljust(low, "x"), "placeholder"
    return None, "unresolved"


def requirements(content):
    """The access requirements publication evidence is judged on (same filter as `publishing.evidence`)."""
    reqs = content.get("access_requirements") or []
    record = [r for r in reqs if r["kind"] == "record_scope" and not (content.get("access_policy") and r["enforcement"].get("unrestricted"))]
    return reqs, record, [r for r in reqs if r["kind"] == "data_scope"]


def plan(content, selectors, kinds):
    """The test plan for one artifact. `selectors` are its record-selecting runtime arguments (Building.record_selectors),
    `kinds` the fixture provider's record kinds (empty when it has none: the records then come from existing accounts)."""
    schema = content.get("input_schema") or {}
    props, required = schema.get("properties") or {}, list(schema.get("required") or [])
    reqs, record_scope, data_scope = requirements(content)
    by_input = {}
    for name in sorted(set(props) | set(selectors)):
        # an input selects a record kind by the name the provider declares; a model-written tool may decorate it (order_id_input)
        found = next((k for k in kinds for i in (k.get("selects_by") or []) if name == i or name.lower().startswith(i.lower())
                      or name.lower().endswith(i.lower())), None)
        if found:
            by_input[name] = found
    record_inputs = sorted(set(selectors) | {n for n in props if n in by_input})
    blockers, notes = [], []
    chosen = {by_input[i]["kind"] for i in record_inputs if i in by_input}
    kind = None
    if kinds:
        unmatched = [i for i in record_inputs if i not in by_input]
        if unmatched:
            blockers.append(f"The fixture provider has no record kind that is selected by {', '.join(unmatched)}; declare one in /_fixtures/kinds (selects_by) or add the test records by hand")
        if len(chosen) > 1:
            blockers.append(f"The inputs {', '.join(record_inputs)} select different record kinds ({', '.join(sorted(chosen))}); automatic tests support one kind per tool")
        kind = next((k for k in kinds if k["kind"] in chosen), None) or (kinds[0] if (data_scope and not record_inputs) else None)
    writes = any(s["effect"] == "write" for s in content.get("steps", []))
    # A write may need the record in a particular state (the API answers 409/422 otherwise): try each state the kind offers, in its order.
    states = list(kind["states"]) if kind and writes else list(kind["states"][:1]) if kind else []
    two = bool(record_scope or data_scope)
    labels = ["a", "b"] if two else ["a"]
    arguments, sources, unresolved = {}, {}, []
    for name in required:
        if name in record_inputs:
            sources[name] = "fixture_record"
            continue
        value, source = schema_value(name, props.get(name))
        if source == "unresolved":
            unresolved.append(name)
        else:
            arguments[name] = value
        sources[name] = source
    base = dict(record_kind=kind["kind"] if kind else None, record_state=None, argument_sources=sources, purpose=EVIDENCE)
    tests = []

    def add(name, scenario, who, expect, record_owner=None, record_of=None, **extra):
        tests.append(dict(base, name=name, scenario=scenario, identity_label=who, record_owner_label=record_owner,
                          record_of=record_of or (who if record_inputs else None), expect=expect, arguments=dict(arguments), **extra))

    if record_scope:
        add("auto.own_record.a", "own_record", "a", "succeeded")
        add("auto.own_record.b", "own_record", "b", "succeeded")
        add("auto.cross_user.b-on-a", "cross_user", "b", "failed", record_owner="a", record_of="a")
    else:
        add("auto.success.a", "general", "a", "succeeded")
        if data_scope:
            add("auto.success.b", "general", "b", "succeeded")
    if any(r["kind"] == "caller_access" for r in reqs):
        tests.append(dict(base, name="auto.bad_credential", scenario="bad_credential", identity_label="revoked", record_owner_label=None, record_of="a" if record_inputs else None,
                          expect="failed", arguments=dict(arguments), purpose=DIAGNOSTIC))
    tests.append(dict(base, name="auto.invalid_arguments", scenario="invalid_arguments", identity_label="a", record_owner_label=None, record_of=None,
                      expect="rejected", arguments=dict(arguments), purpose=DIAGNOSTIC))
    needs_records = bool(record_inputs or data_scope)
    if needs_records and not kinds:
        notes.append("No fixture provider: records are taken from what the existing test accounts own")
    return dict(artifact_name=content.get("name"), record_kind=kind["kind"] if kind else None, record_inputs=record_inputs, needs_records=needs_records,
                states_to_try=states, writes=writes, accounts=labels, record_scope=[r["id"] for r in record_scope], data_scope=[r["id"] for r in data_scope],
                unresolved=unresolved, tests=tests, blockers=blockers, notes=notes)
