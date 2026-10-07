"""Evidence-backed input semantics. API names are labels, never security rules."""
import copy
import re
from ..persistence.util import digest

CATEGORIES = ("business_input", "identity_context", "runtime_context", "server_derived", "resource_selector", "configuration", "sensitive_internal", "unknown")
PROTECTED = {"identity_context", "runtime_context", "server_derived", "sensitive_internal"}
CHOICES = [dict(value=k, label=v) for k, v in (("runtime_context", "Runtime context"), ("business_input", "Explicit tool input"),
           ("server_derived", "Server-derived"), ("not_required", "Not required"), ("configuration", "Configuration"), ("unknown", "Custom / unresolved"))]


def evidence(state, source, reference, claim, scope="schema"):
    return dict(state=state, source=source, reference=reference, claim=claim, scope=scope)


OPAQUE = re.compile(r"(?:#[/]|\b[0-9a-f]{8}-[0-9a-f-]{27,}\b|(?:^|\s)/[a-z_][\w/-]*|\bs[1-8]\b)", re.I)


def label(op):
    text = (op.get("summary") or op.get("description") or "").split("\n")[0].strip()
    if text and not OPAQUE.search(text):
        return text[:180]
    resource = next(iter(op.get("tags") or []), None)
    if not resource:
        segments = [s for s in op.get("path", "").split("/") if s and not s.startswith("{")]
        resource = (segments[-1] if segments else "records").replace("_", " ").replace("-", " ")
    action = {"GET": "Read", "HEAD": "Inspect", "OPTIONS": "Inspect", "POST": "Create or submit", "PUT": "Replace", "PATCH": "Update", "DELETE": "Remove"}.get(op.get("method"), "Use")
    return f"{action} {resource}"


def classify(op, key, field):
    schema = field.get("schema") or {}
    location = key.split(".", 1)[0]
    pointers = op.get("normalization", {}).get("source_pointers") or {}
    nested_key = "inputs/" + key.replace(".", "/properties/")
    reference = pointers.get("inputs/" + key) or pointers.get(nested_key) or op.get("source_pointer", "")
    sources = [evidence("DECLARED", "openapi", reference, f"Input location: {location}; type: {schema.get('type', 'unspecified')}")]
    category, state, reason = "unknown", "UNRESOLVED", "The API does not establish who supplies this value."
    parameter = next((p for p in op.get("parameters", []) if f"{p.get('in')}.{p.get('name')}" == key), {})
    declared = schema.get("x-semantic-role") or parameter.get("x-semantic-role")
    security = [s for s in op.get("security_schemes", {}).values() if s.get("type") == "apiKey" and f"{s.get('in')}.{s.get('name')}" == key]
    if security:
        category, state, reason = "identity_context", "DECLARED", "A declared security scheme supplies this credential."
    elif schema.get("readOnly"):
        category, state, reason = "server_derived", "DECLARED", "The schema declares this field read-only."
    elif declared in CATEGORIES:
        category, state, reason = declared, "UNRESOLVED" if declared == "unknown" else "DECLARED", "The schema explicitly declares its semantic role."
    elif declared:
        reason = "The declared semantic role is unsupported and requires clarification."
    elif schema.get("writeOnly") or schema.get("format") == "password":
        category, state, reason = "sensitive_internal", "DECLARED", "The schema marks this value write-only or secret."
    elif location == "cookie":
        category, state, reason = "runtime_context", "INFERRED", "Cookies are supplied by a session; their exact runtime source requires an accepted mapping."
    elif location == "header":
        category, state, reason = "runtime_context", "INFERRED", "Header transport context requires an explicit runtime source or owner decision."
    elif location == "path":
        category, state, reason = "resource_selector", "DECLARED", "A path parameter selects the target resource; its ownership policy is not implied."
    elif location in ("query", "body"):
        category, state, reason = "business_input", "INFERRED", "A request value is a candidate business choice, subject to security and derivation evidence."
    # Operation-wide prose is considered only when it explicitly discusses this input.
    input_name = key.partition(".")[2]
    statements = re.split(r"[.\n]", op.get("description") or "")
    relevant = " ".join(sentence for sentence in statements if input_name and re.search(r"(?<![\w])" + re.escape(input_name) + r"(?![\w])", sentence, re.I))
    description = " ".join(str(s or "") for s in (field.get("description"), schema.get("description"), relevant)).lower()
    # These describe semantics in documentation, not particular parameter/resource names.
    if state != "DECLARED" and re.search(r"\b(server[- ](?:generated|calculated|assigned|derived)|computed by the server)\b", description):
        category, state, reason = "server_derived", "INFERRED", "Documentation suggests server derivation; the source must be confirmed."
    elif state != "DECLARED" and re.search(r"\b(authenticated (?:user|identity)|current (?:user|tenant)|derived from (?:identity|session))\b", description):
        category, state, reason = "identity_context", "INFERRED", "Documentation ties this value to authenticated context, not caller choice."
    sources.append(evidence(state, "semantic_classifier", reference, reason, "interpretation" if state == "INFERRED" else "schema"))
    if relevant:
        sources.append(evidence("DECLARED", "operation_documentation", op.get("source_pointer", "") + "/description", relevant[:400], "documentation"))
    for item in op.get("evidence_ids", []):
        sources.append(evidence("DECLARED", "code_evidence_reference", str(item), "Implementation evidence is available for review; its presence alone does not verify a policy.", "implementation"))
    return dict(category=category, state=state, sources=sources, llm_control=category not in PROTECTED and category != "unknown",
                resolution_required=category in PROTECTED or category == "unknown", reason=reason)


def input_semantics(op):
    result = {key: classify(op, key, field) for key, field in op.get("inputs", {}).items()}
    def children(schema, path):
        for name, child in (schema or {}).get("properties", {}).items():
            key = path + "." + name
            meaning = classify(op, key, dict(schema=child, description=child.get("description", "")))
            # Ambiguous dotted field names must never hide a protected nested property.
            if key not in result or meaning["category"] in PROTECTED:
                result[key] = meaning
            children(child, key)
        if isinstance((schema or {}).get("items"), dict):
            children(schema["items"], path + ".*")
    body = ((op.get("request_body") or {}).get("content", {}).get("application/json") or {}).get("schema")
    if body:
        children(body, "body")
    return result


def enrich(inventory, business=None):
    """Return a projection; old stored facts and artifact hashes are never rewritten."""
    value = copy.deepcopy(inventory)
    for op in value.get("operations", []):
        op["semantic_inputs"] = input_semantics(op)
        op["business_label"] = label(op)
        op["semantic_context"] = dict(business_context_available=bool(business), implementation_evidence_available=bool(op.get("evidence_ids")),
                                      security=op.get("declared_auth", {}), dependencies=op.get("dependencies", []))
    value["semantic_version"] = "1"
    return value


def capability(content, inventory):
    ops = {o["id"]: o for o in inventory["operations"]}
    used = [ops[s["operation_id"]] for s in content["steps"] if s["operation_id"] in ops]
    decisions, questions, protected = [], [], []
    for step in content["steps"]:
        op = ops.get(step["operation_id"], {})
        meanings = input_semantics(op)
        for key, meaning in meanings.items():
            row = dict(operation_id=op.get("id"), step_id=step["id"], input=key, label=key.partition(".")[2].replace("_", " "), **meaning)
            decisions.append(row)
            if meaning["resolution_required"]:
                protected.append(row)
                binding = next((b for b in step["bindings"] if b["target"] == key), None)
                if not binding and not op.get("inputs", {}).get(key, {}).get("required") or binding and binding["kind"] != "runtime_argument":
                    continue
                questions.append(dict(id="semantic-" + digest([op.get("id"), key])[:12], operation_id=op.get("id"), step_id=step["id"], input=key,
                    text=f"How should {row['label'] or 'this value'} participate in {label(op)}?", options=CHOICES, evidence=meaning["sources"]))
    name = content["name"] if not OPAQUE.search(content["name"]) else (label(used[0]) if used else "Business capability")
    description = content["description"]
    if OPAQUE.search(description):
        description = content["business_purpose"] if not OPAQUE.search(content["business_purpose"]) else "; ".join(label(o) for o in used)
    return dict(id="capability-" + digest(dict(purpose=content["business_purpose"], steps=content["steps"]))[:12], name=name, description=description, purpose=content["business_purpose"],
                state="INFERRED", operations=[dict(id=o["id"], label=label(o), method=o["method"], path=o["path"]) for o in used],
                inputs=decisions, questions=questions, protected_inputs=protected,
                reads=[label(o) for o in used if o["method"] in ("GET", "HEAD", "OPTIONS")],
                writes=[label(o) for o in used if o["method"] not in ("GET", "HEAD", "OPTIONS")])


def risks(content, inventory, policy=None):
    cap = capability(content, inventory)
    factors = []
    def add(name, present, severity, state, detail):
        if present:
            factors.append(dict(factor=name, severity=severity, state=state, detail=detail))
    add("write", cap["writes"], "medium", "DECLARED", "The capability sends state-changing API requests.")
    add("destructive", any(o["method"] == "DELETE" for o in cap["operations"]), "high", "INFERRED", "Deletion may be irreversible; recovery is not established.")
    add("sensitive_input", any(i["category"] == "sensitive_internal" for i in cap["inputs"]), "high", "DECLARED", "Sensitive fields require a trusted input source.")
    add("caller_controlled_selector", any(i["category"] == "resource_selector" for i in cap["inputs"]), "medium", "DECLARED", "Resource selectors require ownership or scope enforcement.")
    resolved = {(i["step_id"], i["target"]) for i in (policy or {}).get("inputs", [])}
    add("runtime_context", any((i["step_id"], i["input"]) not in resolved for i in cap["questions"]), "high", "UNRESOLVED", "Protected inputs need accepted runtime mappings.")
    add("access_policy", not policy, "high", "UNRESOLVED", "No structured access policy has been accepted.")
    unresolved = [f["factor"] for f in factors if f["state"] == "UNRESOLVED"]
    if cap["writes"]:
        for key in ("idempotent", "external_side_effects", "financial", "irreversible"):
            value = (policy or {}).get(key)
            if value is None:
                unresolved.append(key)
            elif value and key != "idempotent":
                add(key, True, "high", "VERIFIED", "Owner accepted this effect classification; target behavior remains unverified.")
    if policy:
        unresolved.append("target_authorization_verification")
    return dict(level="high" if any(f["severity"] == "high" for f in factors) else "medium" if factors else "low",
                state="INFERRED", factors=factors, unresolved=unresolved, confidence="limited" if unresolved else "supported")
