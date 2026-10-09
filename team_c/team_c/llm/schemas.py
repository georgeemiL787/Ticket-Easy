import copy
from .prompts import eligible


def regex_escape(value):
    """Escape one literal for a JSON-Schema regular expression, nothing beyond the metacharacters."""
    return "".join("\\" + ch if ch in "\\^$.|?*+()[]{}" else ch for ch in value)


def strict_schema(schema):
    """Make nullable/defaulted Pydantic fields explicit for strict provider schemas."""
    if isinstance(schema, dict):
        schema = {k: strict_schema(v) for k, v in schema.items() if k != "default"}
        if schema.get("type") == "object" and "properties" in schema:
            schema["required"] = list(schema["properties"])
            schema["additionalProperties"] = False
    elif isinstance(schema, list):
        schema = [strict_schema(v) for v in schema]
    return schema


def groq_schema(schema):
    """Flatten correlated response choices that Groq cannot disambiguate.

    The wire schema keeps the allowed values but cannot express their correlations.
    The router validates each answer against the original schema before accepting it.
    Other providers continue to receive the original, fully constrained schema.
    """
    schema = copy.deepcopy(schema)
    # The per-step binding copies constrain target to that operation's own input keys, which the
    # shared definitions below cannot repeat per operation. Keep the offered keys as one pattern
    # instead of an unconstrained string: decoding can no longer emit a bare input name where the
    # operation declares a qualified key, the wire stays small enough for the provider, and
    # grounding still checks the exact operation's keys. The provider reads an enum here as a
    # second discriminator beside kind and rejects the schema, which a pattern does not do.
    offered = sorted({key for name, definition in schema.get("$defs", {}).items()
                      if name.startswith("StepTargets") for key in (definition.get("enum") or [])})
    target_schema = {"type": "string", "pattern": "^(?:" + "|".join(regex_escape(k) for k in offered) + ")$"} \
        if offered else {"type": "string"}
    for name in ("Output", "PreviousOutputBinding"):
        definition = schema.get("$defs", {}).get(name, {})
        variants = definition.get("anyOf")
        if not variants:
            continue
        properties = copy.deepcopy(variants[0]["properties"])
        for key, prop in properties.items():
            choices = [variant["properties"][key] for variant in variants]
            if all(choice == choices[0] for choice in choices):
                continue
            # output_schema varies only these string enums between response variants.
            if not all(choice.get("type") == "string" and "enum" in choice for choice in choices):
                raise ValueError("Unsupported Groq response choice schema")
            prop["enum"] = list(dict.fromkeys(value for choice in choices for value in choice["enum"]))
        schema["$defs"][name] = dict(variants[0], properties=properties)
        if name == "PreviousOutputBinding":
            properties["target"] = target_schema
    definitions = schema.get("$defs", {})
    for step in definitions.get("Step", {}).get("anyOf", []):
        bindings = step["properties"]["bindings"].get("items", {}).get("anyOf", [])
        if not bindings or "properties" not in bindings[0]:
            continue
        # Every step now uses the same binding shapes. Reuse definitions instead
        # of repeating four complete objects for every available operation.
        # The shared definition carries the union of every offered input key, so a
        # target the operation cannot accept is not decodable; which operation may
        # take it stays with the original schema and with grounding.
        step["properties"]["bindings"]["items"]["anyOf"] = [
            {"$ref": "#/$defs/" + name} for name in ("RuntimeBinding", "ConfigurationBinding", "ContextBinding", "PreviousOutputBinding")]
    for name in ("RuntimeBinding", "ConfigurationBinding", "ContextBinding", "PreviousOutputBinding"):
        if name in definitions and "properties" in definitions[name]:
            definitions[name]["properties"]["target"] = target_schema
    return compact_schema(schema)


def compact_schema(schema):
    """Remove annotations and unreachable definitions, never validation constraints."""
    schema_maps = {"properties", "patternProperties", "$defs", "definitions", "dependentSchemas"}
    child_schemas = {"items", "contains", "additionalProperties", "unevaluatedProperties", "propertyNames", "not", "if", "then", "else"}
    schema_lists = {"anyOf", "oneOf", "allOf", "prefixItems"}
    def clean(node):
        if not isinstance(node, dict):
            return node
        result = {}
        for key, value in node.items():
            if key in {"title", "description", "examples", "$comment"}:
                continue
            if key in schema_maps:
                value = {name: clean(child) for name, child in value.items()}
            elif key in child_schemas:
                value = clean(value)
            elif key in schema_lists:
                value = [clean(child) for child in value]
            result[key] = value
        return result
    schema = clean(schema)
    definitions, used = schema.get("$defs", {}), set()
    def references(node):
        if isinstance(node, dict):
            ref = node.get("$ref", "")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.split("/")[2]
                if name not in used:
                    used.add(name)
                    references(definitions.get(name, {}))
            for key, value in node.items():
                if key != "$defs":
                    references(value)
        elif isinstance(node, list):
            for child in node:
                references(child)
    references(schema)
    if "$defs" in schema:
        schema["$defs"] = {name: value for name, value in definitions.items() if name in used}
    return schema


def response_pointers(op):
    """(status, object-property JSON Pointers) for each success response with a schema."""
    result = []
    for code, response in sorted(op.get("responses", {}).items()):
        if len(code) != 3 or not code.startswith("2") or not code.isdigit() or not response.get("schema"):
            continue
        pointers = [""]
        def collect(node, path):
            for name, child in (node or {}).get("properties", {}).items():
                child_path = path + "/" + name.replace("~", "~0").replace("/", "~1")
                pointers.append(child_path)
                collect(child, child_path)
        collect(response["schema"], "")
        result.append((code, pointers))
    return result


def constrain(node, name, values):
    """Limit every string array property called `name` to `values` (empty: no items allowed)."""
    if isinstance(node, dict):
        prop = node.get("properties", {}).get(name) if isinstance(node.get("properties"), dict) else None
        if isinstance(prop, dict) and prop.get("type") == "array":
            if values:
                prop["items"] = {"type": "string", "enum": list(values)}
            else:
                prop["maxItems"] = 0
        for value in node.values():
            constrain(value, name, values)
    elif isinstance(node, list):
        for value in node:
            constrain(value, name, values)


def drop_echoes(data, echoes):
    """Reject outputs/previous bindings whose echoed operation differs from the referenced step; drop the echo."""
    proposals = list(data.get("proposals") or [])
    if isinstance(data.get("revised_proposal"), dict):
        proposals.append(data["revised_proposal"])
    for proposal in proposals:
        steps = {s.get("id"): s.get("operation_id") for s in proposal.get("steps", [])}
        # A step may repeat an operation, so only an operation claimed by exactly one step names it.
        owners = {}
        for step_id, operation in steps.items():
            owners.setdefault(operation, []).append(step_id)
        items = [("operation_id", o, None) for o in proposal.get("outputs", [])]
        items += [("source_operation_id", b, s.get("id"))
                  for s in proposal.get("steps", []) for b in s.get("bindings", [])
                  if b.get("kind") == "previous_operation_output"]
        for echo, item, owner in items:
            if echo not in echoes:
                continue
            operation = item.pop(echo, None)
            if operation == steps.get(item.get("step_id")):
                continue
            # A step cannot source a value from itself, yet a strict output schema cannot express
            # step order, so a provider that flattens the correlated choices has no signal for it and
            # cites the step that owns the binding. That citation carries no information, so take the
            # step the echo already names when exactly one step runs that operation; grounding still
            # rejects cycles, forward references and pointers missing from the resolved step.
            # Citing a different real step stays a contradiction between two assertions and is rejected.
            cited = item.get("step_id")
            claimed = owners.get(operation, [])
            if owner is not None and cited == owner and len(claimed) == 1:
                item["step_id"] = claimed[0]
                continue
            raise ValueError("A response pointer was chosen for a different operation than its referenced step")
    return data


def output_schema(kind, payload, output_model):
    """The strict output schema narrowed to this payload's identifiers, and the echo fields drop_echoes must remove."""
    schema = strict_schema(output_model.model_json_schema())
    echoes = set()
    if kind == "code_analysis":
        schema["$defs"]["CodeInterpretation"]["properties"]["operation_id"]["enum"] = [o["id"] for o in payload["inventory"]["operations"]]
        schema["$defs"]["CodeInterpretation"]["properties"]["evidence_ids"]["items"]["enum"] = [e["id"] for e in payload["selected_code"]]
        observed={(c["file"],c["symbol"]) for o in payload["inventory"]["operations"] for c in o.get("call_trace",[])}
        call_ids=[e["id"] for e in payload["selected_code"] if (e["file"],e["symbol"]) in observed]
        if call_ids:schema["$defs"]["CodeInterpretation"]["properties"]["call_trace"]["items"]["enum"]=call_ids
        else:schema["$defs"]["CodeInterpretation"]["properties"]["call_trace"]["maxItems"]=0
    if kind == "reconciliation":
        questions = payload["proposal"]["content"]["questions"]
        ids = [q["id"] for q in questions]
        assessed = ids + [r["id"] for r in payload.get("requirements", [])]
        defs = schema.get("$defs", {})
        if "ProposalContent" in defs:
            defs["ProposalContent"]["properties"]["questions"].update(minItems=len(ids), maxItems=len(ids))
        if ids:
            defs["Question"]["properties"]["id"]["enum"] = ids
        if assessed:
            defs["Finding"]["properties"]["question_id"]["enum"] = assessed
        schema["properties"]["findings"].update(minItems=len(assessed), maxItems=len(assessed))
    if "operation_index" in payload:
        tools = [t["proposal_id"] for t in payload.get("existing_tools", [])]
        constrain(schema, "operation_ids", [o["id"] for o in payload["operation_index"]["operations"]])
        constrain(schema, "existing_proposal_ids", tools)
        constrain(schema, "related_proposal_ids", tools)
    if kind == "area_assignment":
        # One required property per group whose value must be a named area: each group is assigned exactly once.
        keys = [g["key"] for g in payload["groups"]]
        schema["properties"]["assignments"] = {"type": "object", "properties": {k: {"type": "string", "enum": [a["name"] for a in payload["areas"]]} for k in keys},
                                               "required": keys, "additionalProperties": False}
    # Constrain references at generation time too; deterministic validation still runs.
    # Original operationId aliases and duplicate raw schemas remain in storage/UI,
    # but aren't competing identifiers in the model-facing inventory.
    if "inventory" in payload:
        operations = eligible(payload["inventory"])
        step_schema = schema.get("$defs", {}).get("Step", {})
        if step_schema:
            step_schema["properties"]["operation_id"]["enum"] = [op["id"] for op in operations]
        # Pointers are offered per (operation, success status): the model echoes the referenced
        # step's operation, which is checked against that step and removed before validation.
        # Constrained decoding emits properties in order, so step and operation are fixed before the pointer.
        for name, field, echo in (("Output", "pointer", "operation_id"), ("PreviousOutputBinding", "reference", "source_operation_id")):
            base = schema.get("$defs", {}).get(name)
            variants = []
            for op in operations if base else []:
                for code, pointers in response_pointers(op):
                    props = {**copy.deepcopy({k: v for k, v in base["properties"].items() if k not in (field, "response_status")}),
                             echo: {"type": "string", "enum": [op["id"]], "description": "operation_id of the referenced step"},
                             "response_status": {"type": "string", "enum": [code]}, field: {"type": "string", "enum": pointers}}
                    variants.append(dict(base, properties=props, required=list(props)))
            if variants:
                schema["$defs"][name] = {"anyOf": variants}
                echoes.add(echo)
        # One step variant per operation: operation_id is decoded first, so binding targets can be
        # limited to that operation's own input keys and the step can bind each input at most once.
        # Previous-output variants are many and shared across steps, so their target is limited to the
        # input keys of any supplied operation instead; grounding still checks the exact operation.
        if step_schema:
            defs, bindings = schema["$defs"], step_schema["properties"]["bindings"]
            every = sorted({key for op in operations for key in op["inputs"]})
            if every:
                for v in defs["PreviousOutputBinding"].get("anyOf", [defs["PreviousOutputBinding"]]):
                    v["properties"]["target"]["enum"] = every
            variants = []
            for i, op in enumerate(operations):
                keys = sorted(op["inputs"])
                limited = dict(bindings, minItems=sum(1 for f in op["inputs"].values() if f["required"]), maxItems=len(keys))
                if keys:
                    defs[f"StepTargets{i}"] = {"type": "string", "enum": keys}
                    limited["items"] = {"anyOf": [dict(defs[k], properties={**defs[k]["properties"], "target": {"$ref": f"#/$defs/StepTargets{i}"}})
                                                  for k in ("RuntimeBinding", "ConfigurationBinding", "ContextBinding")] + [{"$ref": "#/$defs/PreviousOutputBinding"}]}
                variants.append(dict(step_schema, properties={**step_schema["properties"], "operation_id": dict(step_schema["properties"]["operation_id"], enum=[op["id"]]), "bindings": limited}))
            defs["Step"] = {"anyOf": variants}
    return schema, echoes
