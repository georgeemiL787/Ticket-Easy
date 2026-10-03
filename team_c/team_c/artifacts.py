"""Deterministic compilation of an approved proposal version into an executable tool artifact.

No model is involved: every step, mapping and output comes from the approved, grounded proposal and
the stored inventory. Anything the executor cannot perform exactly is reported, never approximated.
"""
import re
from types import SimpleNamespace
from urllib.parse import urlparse
from .config import AppError
from .grounding import response_field
from .storage import digest

FORMAT = "team_c.tool_artifact/1"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LIMITS = dict(timeout_seconds=10.0, max_request_bytes=64 * 1024, max_response_bytes=256 * 1024, max_steps=8, follow_redirects=False, retries=0)
FAILURE = dict(on_step_failure="Stop. Later steps, including dependent writes, are not attempted.",
               write_timeout="Outcome unknown: the write may or may not have been applied.",
               retries="None. Writes are never retried.")
ACTIVATION = dict(permitted_uses=["artifact_creation", "sandbox_testing"], runtime_ready=False, activated=False,
                  note="Approval to build permits artifact creation and configured sandbox tests only; nothing is published or activated.")
# Supported mechanisms; anything else leaves the requirement unenforced and blocks execution.
MECHANISMS = {"caller_access": {"delegated_user_credential"}, "record_scope": {"response_field_matches_context"}}
COMPARISONS = {"equals"}
CHECK_POINTS = {"after_step"}
RECORD_KEYS = {"mechanism", "step_id", "response_status", "pointer", "context_field", "comparison", "check_point"}


def auth_for(op, missing):
    auth = op["declared_auth"]
    if auth["status"] in ("none_declared", "explicitly_none"):
        return None
    for option in auth["alternatives"]:
        if len(option) != 1:
            continue
        scheme = op["security_schemes"].get(option[0]["scheme"], {})
        if scheme.get("type") == "oauth2" and "password" in scheme.get("flows", {}):
            token_url = scheme["flows"]["password"].get("tokenUrl", "")
            parsed = urlparse(token_url)
            if parsed.scheme or parsed.netloc or not token_url.startswith("/"):
                missing.append(f'{op["method"]} {op["path"]}: token URL {token_url!r} is not a path on the connector origin')
                return None
            return dict(scheme=option[0]["scheme"], type="oauth2_password", token_path=token_url, scopes=sorted(option[0]["scopes"]))
        if scheme.get("type") == "http" and str(scheme.get("scheme", "")).lower() == "bearer":
            return dict(scheme=option[0]["scheme"], type="http_bearer", scopes=[])
    missing.append(f'{op["method"]} {op["path"]}: no supported authentication (OAuth2 password flow or HTTP bearer) is declared')
    return None


def compile_steps(content, ops, missing):
    steps = []
    for s in content["steps"]:
        op = ops[s["operation_id"]]
        declared = {f'{p["in"]}.{p["name"]}': p for p in op.get("parameters", [])}
        parameters, fields, whole = [], [], None
        for b in s["bindings"]:
            source = {k: b[k] for k in ("kind", "reference", "step_id", "response_status") if b.get(k) is not None}
            location, _, name = b["target"].partition(".")
            schema = op["inputs"][b["target"]]["schema"]
            kind = schema.get("type")
            if location in ("path", "query"):
                p = declared[b["target"]]
                style = p.get("style", "simple" if location == "path" else "form")
                explode = p.get("explode", style == "form")
                items = (schema.get("items") or {}).get("type")
                if kind is None or kind == "object" or p.get("allowReserved") \
                        or (location == "path" and (style != "simple" or kind == "array")) \
                        or (location == "query" and (style != "form" or (kind == "array" and (not explode or items in (None, "object", "array"))))):
                    missing.append(f'{s["id"]}: {b["target"]} uses unsupported serialization (type {kind}, style {style}, explode {explode})')
                    continue
                parameters.append(dict(name=name, location=location, type=kind, style=style, explode=explode, source=source))
            elif location == "body" and name:
                fields.append(dict(name=name, source=source))
            elif b["target"] == "body":
                whole = dict(source=source)
            else:
                missing.append(f'{s["id"]}: {b["target"]} is sent in {location}, which the executor does not support')
        bound = {f'{p["location"]}.{p["name"]}' for p in parameters}
        for name in re.findall(r"\{([^}]+)\}", op["path"]):
            if f"path.{name}" not in bound:
                missing.append(f'{s["id"]}: path parameter {name} has no executable binding')
        responses = {c: dict(media_type="application/json" if r.get("schema") is not None else None, schema=r.get("schema"))
                     for c, r in sorted(op["responses"].items()) if len(c) == 3 and c.isdigit() and c.startswith("2")}
        if not responses:
            missing.append(f'{s["id"]}: {op["method"]} {op["path"]} declares no success response')
        body = None
        if fields or whole or (op.get("request_body") or {}).get("required"):
            body = dict(media_type="application/json", fields=fields, whole=whole)
        steps.append(dict(id=s["id"], operation_id=op["id"], method=op["method"], path=op["path"], source_pointer=op["source_pointer"],
                          effect="read" if op["method"] in SAFE_METHODS else "write", parameters=parameters, body=body, responses=responses))
    return steps


def enforcement_for(requirement, config, content, ops, auth, context_fields):
    """Validate one operator-supplied mechanism against the approved steps; the model never supplies these."""
    kind, rid = requirement["kind"], requirement["id"]
    invalid = lambda message: AppError("enforcement_invalid", f"{rid}: {message}")
    if not config:
        return dict(status="missing", reason=f"No enforceable mechanism is configured for {kind}. An owner confirmation is not enforcement.")
    mechanism = config.get("mechanism")
    if mechanism not in MECHANISMS.get(kind, ()):
        raise invalid(f"mechanism {mechanism!r} cannot enforce {kind}; supported: {sorted(MECHANISMS.get(kind, ())) or 'none'}")
    if mechanism == "delegated_user_credential":
        if set(config) != {"mechanism"}:
            raise invalid("delegated_user_credential takes no other settings")
        if not auth:
            raise invalid("delegated credentials need a declared authentication scheme")
        return dict(status="configured", mechanism=mechanism, enforced_by="target_api", credential_source="trusted_execution_context",
                    detail="Each run uses one operator-registered identity's own credential; service/administrator-labelled identities are refused. "
                           "The target API decides what that credential may do. The end-user label is an operator declaration, not proof of authorization.")
    if set(config) != RECORD_KEYS:
        raise invalid(f"{mechanism} needs exactly {sorted(RECORD_KEYS)}")
    if config["comparison"] not in COMPARISONS or config["check_point"] not in CHECK_POINTS:
        raise invalid(f"supported comparison: {sorted(COMPARISONS)}; supported check_point: {sorted(CHECK_POINTS)}")
    if config["context_field"] not in context_fields:
        raise invalid(f"context_field must be one of the connector's declared trusted context fields {sorted(context_fields)}")
    steps = {s["id"]: s for s in content["steps"]}
    order = [s["id"] for s in content["steps"]]
    labels = {sid: f'{ops[s["operation_id"]]["method"]} {ops[s["operation_id"]]["path"]}' for sid, s in steps.items()}
    step = steps.get(config["step_id"])
    if not step:
        raise invalid(f"unknown step {config['step_id']!r}")
    if ops[step["operation_id"]]["method"] not in SAFE_METHODS:
        raise invalid("the check must run on a read step, before any write it protects")
    try:
        schema, guaranteed = response_field(SimpleNamespace(operation_id=step["operation_id"]), ops, config["response_status"], config["pointer"])
    except ValueError as exc:
        raise invalid(str(exc))
    if not guaranteed or schema.get("type") not in ("string", "integer"):
        raise invalid("the compared response field must be a required, non-nullable string or integer")
    source = lambda s: next((b for b in s["bindings"] if b["target"] == requirement["field"]), None)
    checked = source(step) if labels[step["id"]] in requirement["operations"] else None
    scoped = [sid for sid in order if labels[sid] in requirement["operations"] and sid != step["id"]]
    if checked is None and not scoped:
        raise invalid(f"step {step['id']} neither uses {requirement['field']} nor returns the record a scoped step uses")
    for sid in scoped:
        b = source(steps[sid])
        from_check = bool(b) and b["kind"] == "previous_operation_output" and b["step_id"] == step["id"]
        if order.index(sid) < order.index(step["id"]) or not (from_check or (checked is not None and b == checked)):
            raise invalid(f"{sid} must follow {step['id']} and take {requirement['field']} from the checked record (same source, or {step['id']}'s response)")
    return dict(status="configured", mechanism=mechanism, enforced_by="team_c_executor", step_id=step["id"], response_status=config["response_status"],
                pointer=config["pointer"], context_field=config["context_field"], comparison=config["comparison"], check_point=config["check_point"],
                detail=f"After {step['id']} returns {config['response_status']}, {config['pointer']} must equal trusted context field "
                       f"{config['context_field']}; otherwise no later step runs and no output is returned.")


def validate_enforcement(configs, content, inventory, requirements, connector):
    """Resolved enforcement per requirement id; unknown requirements or invalid mechanisms are rejected."""
    unknown = set(configs) - {r["id"] for r in requirements}
    if unknown:
        raise AppError("enforcement_invalid", "Enforcement refers to unknown requirements: " + ", ".join(sorted(unknown)))
    ops = {o["id"]: o for o in inventory["operations"]}
    missing = []
    auth = next((a for a in (auth_for(ops[s["operation_id"]], missing) for s in content["steps"]) if a), None)
    context_fields = set(connector.get("context_fields") or [])
    return {r["id"]: enforcement_for(r, configs.get(r["id"]), content, ops, auth, context_fields) for r in requirements}


def compile_artifact(content, derived, inventory, requirements, proposal, source, connector, enforcement):
    ops = {o["id"]: o for o in inventory["operations"]}
    missing = []
    steps = compile_steps(content, ops, missing)
    if len(steps) > LIMITS["max_steps"]:
        missing.append(f"More than {LIMITS['max_steps']} steps")
    auths = []
    for s in content["steps"]:
        found = auth_for(ops[s["operation_id"]], missing)
        if found not in auths:
            auths.append(found)
    if len(auths) > 1:
        missing.append("Steps declare different authentication; one connector credential cannot serve them all")
    if missing:
        raise AppError("artifact_unsupported", "The approved proposal has execution semantics the executor cannot perform exactly", details={"missing": missing})
    auth = auths[0]
    configs = validate_enforcement(enforcement["content"] if enforcement else {}, content, inventory, requirements, connector)
    access = []
    for r in requirements:
        if r["status"] != "owner_confirmed":
            raise AppError("artifact_not_approved", f"Requirement {r['id']} is not owner-confirmed")
        access.append(dict(id=r["id"], kind=r["kind"], text=r["text"], owner_confirmed_answer=r["answer"]["text"], enforcement=configs[r["id"]]))
    runtime = derived["runtime_inputs"]
    configuration = {c["key"]: c["value_json"] for c in content["configuration"]}
    artifact = dict(
        format=FORMAT, name=content["name"], purpose=content["business_purpose"], description=content["description"],
        proposal=proposal, source=dict(source, operations=[dict(id=s["operation_id"], method=s["method"], path=s["path"], source_pointer=s["source_pointer"]) for s in steps]),
        input_schema=dict(type="object", properties={k: v["schema"] for k, v in sorted(runtime.items())}, required=sorted(k for k, v in runtime.items() if v["required"]), additionalProperties=False),
        output_schema=dict(type="object", properties={k: v["schema"] for k, v in sorted(derived["outputs"].items())}, required=sorted(k for k, v in derived["outputs"].items() if v["guaranteed"]), additionalProperties=False),
        configuration=configuration, steps=steps,
        outputs=[dict(name=o["name"], step_id=o["step_id"], response_status=o["response_status"], media_type="application/json", pointer=o["pointer"]) for o in content["outputs"]],
        connector=dict(id=connector["id"], base_url=connector["base_url"], auth=auth), access_requirements=access,
        enforcement_config={k: enforcement[k] for k in ("id", "sha256", "submitted_by", "submitted_at", "reviewed_by", "reviewed_at")} if enforcement else None,
        limits=LIMITS, failure_behavior=FAILURE, activation=ACTIVATION)
    blocked = [f'{a["id"]}: {a["enforcement"]["reason"]}' for a in access if a["enforcement"]["status"] != "configured"]
    artifact["execution_blockers"] = blocked
    return artifact, digest(artifact)
