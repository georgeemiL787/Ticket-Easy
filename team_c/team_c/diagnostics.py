"""Bounded structural diagnostics, never raw prompts, prose, credentials or values."""
import hashlib
import json
import re


def identifier(value, secrets=()):
    if value is None:
        return None
    if not isinstance(value, str):
        return "[invalid type]"
    if any(secret and secret in value for secret in secrets):
        return "[redacted]"
    if len(value) > 160 or not re.fullmatch(r"[A-Za-z0-9_./{}~ -]*", value):
        return "[redacted non-identifier]"
    return value


def output_diagnostic(value, secrets=()):
    """Project pre-parse and post-parse outputs onto the binding contract only.

    Intentionally omit all free text, configuration values, answers, headers,
    URLs, reasoning, and unknown fields. This is not a replayable raw response.
    """
    def rows(obj, key):
        values = obj.get(key, [])
        return [v for v in values[:64] if isinstance(v, dict)] if isinstance(values, list) else []

    def fields(obj, names):
        return {k: identifier(obj.get(k), secrets) for k in names}

    def proposal(obj):
        return {
            "configuration": [dict(key=identifier(c.get("key"), secrets), value_present=c.get("value_json") is not None) for c in rows(obj, "configuration")],
            "questions": [fields(q, ("id", "configuration_key")) for q in rows(obj, "questions")],
            "steps": [dict(**fields(s, ("id", "operation_id")), bindings=[fields(b, ("target", "kind", "reference", "step_id", "response_status")) for b in rows(s, "bindings")]) for s in rows(obj, "steps")],
            "outputs": [fields(o, ("name", "step_id", "response_status", "pointer")) for o in rows(obj, "outputs")],
        }
    if not isinstance(value, dict):
        return {"invalid_root": True}
    result = {"proposals": [proposal(p) for p in rows(value, "proposals")]}
    if "interpretations" in value:
        result["interpretations"]=[dict(operation_id=identifier(i.get("operation_id"), secrets), evidence_ids=[identifier(e,secrets) for e in i.get("evidence_ids",[])[:64]] if isinstance(i.get("evidence_ids"),list) else []) for i in rows(value,"interpretations")]
    if isinstance(value.get("revised_proposal"), dict):
        result["revised_proposal"] = proposal(value["revised_proposal"])
    if value.get("outcome") in ("revised", "cannot_repair", "capability_gap"):
        result["repair_outcome"] = value["outcome"]
    return result


def response_diagnostic(raw, secrets=()):
    if not isinstance(raw, str):
        return {"invalid_response_type": True}
    result = {"sha256": hashlib.sha256(raw.encode()).hexdigest(), "bytes": len(raw.encode())}
    try:
        result["structure"] = output_diagnostic(json.loads(raw), secrets)
    except (ValueError, RecursionError):
        result["invalid_json"] = True
    return result


def check_errors(errors, secrets=()):
    """Grounding check messages for the owner, the stored run and the model's retry: bounded, secret-bearing lines redacted."""
    return ["[redacted]" if any(secret and secret in e for secret in secrets) else e[:300] for e in errors[:20] if isinstance(e, str)]


def contract_diagnostic(contract, secrets=()):
    return {
        **{k: [identifier(v, secrets) for v in contract.get(k, [])[:64]] for k in ("declared", "referenced", "unused_declarations", "undeclared_references")},
        "bindings": [{k: identifier(b.get(k), secrets) for k in ("step_id", "target", "reference")} for b in contract.get("bindings", [])[:64]],
    }
