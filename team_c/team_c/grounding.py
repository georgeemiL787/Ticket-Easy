import json
import re
from openapi_schema_validator import OAS30Validator
from .config import AppError
from .models import ProposalContent

CONTEXT = {"business_id": {"type": "string"}}


def response_field(step, operations, status, path):
    if not status or not status.startswith("2") or status not in operations[step.operation_id]["responses"]:
        raise ValueError("Output must select a declared success response")
    schema = operations[step.operation_id]["responses"][status]["schema"]
    if schema is None:
        raise ValueError("Selected response has no JSON schema")
    if path and not path.startswith("/"):
        raise ValueError("Output field must be a JSON Pointer")
    guaranteed = True
    for raw in path.split("/")[1:] if path else []:
        key = raw.replace("~1", "/").replace("~0", "~")
        if schema.get("type") != "object" or key not in schema.get("properties", {}):
            raise ValueError(f"Unknown output field {path}; array selection/transforms are unsupported")
        guaranteed = guaranteed and key in schema.get("required", []) and not schema.get("nullable", False)
        schema = schema["properties"][key]
        if schema.get("writeOnly"):
            raise ValueError("A writeOnly field is not available in response data")
    return schema, guaranteed and not schema.get("nullable", False)


def compatible(source, target):
    # Conservative schema subsumption: reject uncertain constraints rather than guessing.
    st, tt = source.get("type"), target.get("type")
    if not st or not tt or (st != tt and (st, tt) != ("integer", "number")):
        return False
    if source.get("nullable") and not target.get("nullable"):
        return False
    if "enum" in target and ("enum" not in source or not set(map(str, source["enum"])).issubset(set(map(str, target["enum"])))):
        return False
    for key in ("minimum", "minLength", "minItems", "minProperties"):
        if key in target and (key not in source or source[key] < target[key]):
            return False
    for key in ("maximum", "maxLength", "maxItems", "maxProperties"):
        if key in target and (key not in source or source[key] > target[key]):
            return False
    for key in ("pattern", "format", "multipleOf", "exclusiveMinimum", "exclusiveMaximum", "uniqueItems"):
        if key in target and source.get(key) != target[key]:
            return False
    if tt == "array":
        return compatible(source.get("items", {}), target.get("items", {}))
    if tt == "object":
        for key, child in source.get("properties", {}).items():
            target_child = target.get("properties", {}).get(key)
            if target_child is not None and not compatible(child, target_child):
                return False
            additional = target.get("additionalProperties", True)
            if target_child is None and isinstance(additional, dict) and not compatible(child, additional):
                return False
        for key in target.get("required", []):
            if key not in source.get("required", []) or not compatible(source.get("properties", {}).get(key, {}), target.get("properties", {}).get(key, {})):
                return False
        if target.get("additionalProperties") is False and (source.get("additionalProperties", True) is not False or not set(source.get("properties", {})) <= set(target.get("properties", {}))):
            return False
    return True


def response_only_fields(content, ops):
    """Question review: names a question uses that the steps only return, never accept or configure."""
    inputs, returned = set(), {}
    for step in content.steps:
        op = ops.get(step.operation_id)
        if not op:
            continue
        inputs.update(key.split(".", 1)[-1] for key in op["inputs"])
        def walk(node, label):
            for name, child in (node or {}).get("properties", {}).items():
                returned.setdefault(name, set()).add(label)
                walk(child, label)
        for code, response in op.get("responses", {}).items():
            if code.startswith("2"):
                walk(response.get("schema"), f'{op["method"]} {op["path"]} {code}')
    configs = {c.key for c in content.configuration}
    notes = []
    for q in content.questions:
        for name in sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", q.text)) & set(returned) - inputs - configs):
            notes.append(dict(question_id=q.id, field=name, returned_by=sorted(returned[name]),
                              note=f"{name} is only returned by the selected operations; it is not a request input or business configuration, so it cannot be bound or configured. If this concerns access or ownership, the server-derived access requirements cover it; consider superseding the question."))
    return notes


def drop_stale_configuration(content: ProposalContent):
    """A declared configuration that no binding uses, whose key a runtime_argument binding now uses, is left over from
    rebinding that input: remove the declaration and unlink its questions, which stay required. Returns (content, removed keys)."""
    used = {b.reference for s in content.steps for b in s.bindings if b.kind == "business_configuration"}
    runtime = {b.reference for s in content.steps for b in s.bindings if b.kind == "runtime_argument"}
    stale = sorted(({c.key for c in content.configuration} - used) & runtime)
    if not stale:
        return content, []
    return content.model_copy(update=dict(
        configuration=[c for c in content.configuration if c.key not in stale],
        questions=[q.model_copy(update=dict(configuration_key=None)) if q.configuration_key in stale else q for q in content.questions])), stale


def validate_proposal(content: ProposalContent, inventory, scope=None):
    # Per-step eligibility is checked below; this gate only needs the document itself to be valid.
    if not inventory.get("document_valid", inventory["valid"]):
        raise AppError("inventory_blocked", "This specification has document-level discovery errors")
    ops = {op["id"]: op for op in inventory["operations"]}
    errors, blockers, schemas, outputs = [], [], {}, {}
    seen = {}
    configs = {c.key: c for c in content.configuration}
    used_configs = {b.reference for s in content.steps for b in s.bindings if b.kind == "business_configuration"}
    runtime_names = {b.reference for s in content.steps for b in s.bindings if b.kind == "runtime_argument"}
    unused_configs = sorted(set(configs) - used_configs)
    undeclared_configs = sorted(used_configs - set(configs))
    configuration_contract = dict(
        declared=sorted(configs), referenced=sorted(used_configs),
        unused_declarations=unused_configs, undeclared_references=undeclared_configs,
        bindings=[dict(step_id=s.id, target=b.target, reference=b.reference) for s in content.steps for b in s.bindings if b.kind == "business_configuration"],
    )
    if unused_configs:
        errors.append("Unused business configuration declarations: " + ", ".join(unused_configs))
    if undeclared_configs:
        errors.append("Undeclared business configuration references: " + ", ".join(undeclared_configs))
    if runtime_names & set(configs):
        errors.append("Business configuration keys cannot also be future runtime arguments")
    question_ids = [q.id for q in content.questions]
    if len(configs) != len(content.configuration) or len(set(question_ids)) != len(question_ids):
        errors.append("Configuration keys and question IDs must be unique")
    if any(not q.id or not q.text for q in content.questions):
        errors.append("Questions require nonempty IDs and text")
    for q in content.questions:
        if q.configuration_key and q.configuration_key not in configs:
            errors.append(f"Question {q.id} references unknown configuration")
    for step in content.steps:
        if step.id in seen or not step.id:
            errors.append("Step IDs must be unique and nonempty")
        op = ops.get(step.operation_id)
        if not op:
            errors.append(f"Unknown operation {step.operation_id}")
            continue
        # Legacy inventories have no proposal_eligible field; code "supported" already included restrictions.
        if not op.get("proposal_eligible", op.get("supported", True)):
            reason = op.get("exposure", {}).get("classification") or op.get("exposure", {}).get("status") or "unsupported"
            errors.append(f"{step.id}: Operation {op['method']} {op['path']} is not eligible for proposals ({reason})")
            continue
        if scope is not None and step.operation_id not in scope:
            errors.append(f"{step.id}: Operation {op['method']} {op['path']} is outside the selected generation scope")
            continue
        if inventory.get("source_kind") == "code":
            from .code_discovery import validate_code_provenance
            try:
                validate_code_provenance(op, inventory)
            except (ValueError, KeyError, TypeError) as exc:
                errors.append(f"{step.id}: Invalid code provenance: {exc}")
        targets = [b.target for b in step.bindings]
        if len(targets) != len(set(targets)):
            errors.append(f"Duplicate input binding in {step.id}")
        for target, field in op["inputs"].items():
            if field["required"] and target not in targets:
                errors.append(f"Missing required mapping: {step.id}:{target}")
        for b in step.bindings:
            try:
                if b.target not in op["inputs"]:
                    raise ValueError(f"Unknown target {b.target}")
                target = op["inputs"][b.target]["schema"]
                if b.kind != "previous_operation_output" and (b.step_id or b.response_status):
                    raise ValueError("Only previous-operation inputs may declare step/response references")
                if not b.reference and b.kind != "previous_operation_output":
                    raise ValueError("Input source key must not be empty")
                if b.kind == "previous_operation_output":
                    if b.step_id not in seen:
                        raise ValueError("Previous output must refer to an earlier step; cycles/forward references are forbidden")
                    source, guaranteed = response_field(seen[b.step_id], ops, b.response_status, b.reference)
                    if not guaranteed and op["inputs"][b.target]["required"]:
                        raise ValueError("Required input cannot depend on optional/nullable output")
                    if not compatible(source, target):
                        raise ValueError("Previous output schema is not provably compatible with target")
                elif b.kind == "business_configuration":
                    if b.reference not in configs:
                        raise ValueError(f"Undeclared business configuration reference: {b.reference}")
                    conf = configs[b.reference]
                    if conf.value_json is None:
                        blockers.append(f"Configuration {conf.key} has no reconciled value")
                        if not any(q.configuration_key == conf.key for q in content.questions):
                            raise ValueError(f"Unknown business configuration needs an explicit clarification question with configuration_key {conf.key}; "
                                             f"if the value is chosen per use rather than one owner-wide setting, bind {b.target} as runtime_argument and remove configuration {conf.key}")
                    else:
                        try:
                            value = json.loads(conf.value_json)
                        except ValueError:
                            raise ValueError(f"The value of configuration {conf.key} is not valid JSON; value_json must be JSON-encoded, so text needs quotes, e.g. '\"knee\"'") from None
                        OAS30Validator(target).validate(value)
                elif b.kind == "trusted_application_context":
                    if b.reference not in CONTEXT:
                        raise ValueError("No trusted context contract exists for this key (customer identity is not verified)")
                    if not compatible(CONTEXT[b.reference], target):
                        raise ValueError("Context schema incompatible with target")
                else:
                    if b.reference in schemas and schemas[b.reference]["schema"] != target:
                        raise ValueError("Runtime argument is mapped to different schemas; use distinct arguments")
                    schemas[b.reference] = {"schema": target, "required": op["inputs"][b.target]["required"] or schemas.get(b.reference, {}).get("required", False)}
            except Exception as exc:
                errors.append(f"{step.id}:{b.target}: {str(exc)[:300]}")
        # For optional object request bodies, if any field is sent its required siblings still apply.
        request = op.get("request_body") or {}
        body = request.get("content", {}).get("application/json", {}).get("schema", {})
        if any(t.startswith("body.") for t in targets):
            for name in body.get("required", []):
                if not body.get("properties", {}).get(name, {}).get("readOnly") and "body." + name not in targets:
                    errors.append(f"Missing required body sibling {step.id}:body.{name}")
        seen[step.id] = step
    for output in content.outputs:
        try:
            if output.name in outputs:
                raise ValueError("Output names must be unique")
            if output.step_id not in seen:
                raise ValueError("Unknown output step")
            schema, guaranteed = response_field(seen[output.step_id], ops, output.response_status, output.pointer)
            outputs[output.name] = {"schema": schema, "guaranteed": guaranteed}
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        raise AppError("invalid_bindings", "Proposal grounding failed", details={"errors": errors, "configuration_contract": configuration_contract})
    used = [ops[i] for i in dict.fromkeys(s.operation_id for s in content.steps)]
    review = [dict(operation_id=op["id"], method=op["method"], path=op["path"], declared_auth=op["declared_auth"]["status"], unresolved_requirements=op["authorization"]["unresolved_requirements"]) for op in used if "authorization" in op]
    return dict(runtime_inputs=schemas, outputs=outputs, blockers=blockers, source_operations=[s.operation_id for s in content.steps], authorization_review=review, facts_vs_interpretation="Schemas and method/path are extracted facts; purpose, effects and risk are AI interpretations. Security/authorization unverified.")
