"""Publication: evidence rules, MCP tool definitions and the structured call result.

Publication is a separate decision from approve-to-build. It exposes one exact artifact to MCP clients in the
environment the artifact was built for: sandbox (test destinations and identities) or live_demo (the owner-configured
real API). Neither is a production release: production_ready and activated (production activation) stay false.
"""
import re
from urllib.parse import urlparse
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from .config import AppError

ENVIRONMENT = "sandbox"  # the default; live_demo publications record their own
STATUSES = ["succeeded", "failed", "rejected", "partial", "outcome_unknown", "error"]
# Why a call did not succeed, in terms a caller can act on (error.category of every non-succeeded result).
CATEGORIES = ["invalid_arguments", "authentication_failed", "permission_denied", "not_found", "conflict", "validation_error",
              "rate_limited", "api_unavailable", "api_unreachable", "timeout", "invalid_response", "partial_write",
              "unknown_outcome", "publication_unavailable", "configuration_error", "internal_error"]
REJECTED_CATEGORIES = {
    "invalid_arguments": "invalid_arguments", "rate_limited": "rate_limited",
    "publication_disabled": "publication_unavailable", "live_demo_disabled": "publication_unavailable",
    "approval_not_current": "publication_unavailable", "enforcement_not_current": "publication_unavailable",
    "publication_evidence": "publication_unavailable", "artifact_integrity": "publication_unavailable",
    "environment_mismatch": "publication_unavailable", "credential_scope": "permission_denied",
}


def tool_name(artifact_id, content):
    """Stable for one artifact and unique across artifacts (MCP names: [A-Za-z0-9._-]{1,128})."""
    slug = re.sub(r"[^a-z0-9]+", "_", content["name"].lower()).strip("_")[:48] or "tool"
    return f'{slug}_v{content["proposal"]["version"]}_{artifact_id.replace("-", "")}'


RECORD_REFUSALS = ("record_scope_enforced", "target_record_denial")


def next_steps(content, tests, executions, identities, policy_tests, published):
    """The remaining steps for this exact artifact, in the order they must be done.

    Publication blocks on several independent requirements, and a user who only sees the
    requirement is left guessing what to do next. Each step states what it proves and, while it is
    outstanding, the concrete action that satisfies it.
    """
    passed = [t for t in tests if t["verdict"] == "passed"]
    success = [t for t in passed if t["expectation"]["status"] == "succeeded"]
    own = [t for t in passed if t.get("scenario") == "own_record"]
    accepted = [t for t in passed if t.get("scenario") == "cross_user" and not refusal(
        (next((e for e in executions if e["id"] == t["execution_id"]), {}) or {}).get("report"),
        True) is None]
    record_scope = [r["id"] for r in content.get("access_requirements", [])
                    if r["kind"] == "record_scope" and not r["enforcement"].get("unrestricted")]
    steps = []

    def step(key, done, title, why, action):
        steps.append(dict(key=key, done=done, title=title, why=why, action="" if done else action))

    step("identity", bool(identities), "Add a test identity",
         "The executor runs as a registered account, never as a caller-supplied value.",
         "Enter an existing test account under Add or replace a test identity." if not identities else "")
    step("success", bool(success), "Run one sandbox test that expects success",
         "This is the only evidence that the tool works end to end against the real API.",
         "Fill the tool arguments with values that belong to the test account and run it."
         if not success else "")
    if record_scope:
        step("own_record", bool(own), "Run a test where the identity uses its own record",
             "It shows this credential works and may reach its own data.",
             "Add a sandbox test with scenario 'own_record' using the identity's own record.")
        step("cross_user", bool(accepted), "Run a cross-owner test that must be refused",
             "It is the only proof that one customer cannot read another customer's record.",
             "Add a second identity, let it pass its own own_record test, then add a 'cross_user' "
             "test naming that identity as record_owner and passing the first identity's record. "
             "The run must end refused.")
    scoped = [r for r in content.get("access_requirements", []) if r["kind"] == "data_scope"]
    if scoped:
        runs = {e["id"]: e for e in executions}
        returned = {(runs.get(t["execution_id"]) or {}).get("identity") for t in success
                    if any(rec["count"] for rec in (((runs.get(t["execution_id"]) or {}).get("report") or {}).get("outputs_records") or {}).values())}
        step("records", bool(returned), "Run a success test that returns data",
             "An empty result only proves the login works, not that the tool returns the right data.",
             "Make sure the test account has data (for example an order), then run the success test again.")
        if any(r["enforcement"].get("api_filtered") for r in scoped):
            step("isolation", len(returned) >= 2, "Show two test identities each receive only their own data",
                 "You decided the API returns only each caller's own data; this is the proof.",
                 "Add a second test identity with its own data and run the success test as each identity. Their results must not share any record.")
    step("guards", bool(policy_tests and policy_tests.get("passed")), "Pass the generated guard scenarios",
         "They check the compiled policy itself, before any sandbox run.",
         "" if policy_tests and policy_tests.get("passed") else "Run the generated policy tests.")
    step("publish", bool(published), "Publish to the TEST-ONLY sandbox MCP",
         "Publishing exposes this exact artifact to local test clients.",
         "Choose Publish to sandbox MCP once every step above is done.")
    return steps


def unrunnable(content, identities):
    """Why this artifact can never produce a passing sandbox run, from its own content alone.

    An artifact compiled before a specification corrected its declared authentication, or one whose
    accepted principals the declared login cannot issue tokens for, is a dead end. Saying so at the
    artifact page is clearer than an endless 'cannot publish yet'.
    """
    auth = (content.get("connector") or {}).get("auth") or {}
    principals = ((content.get("access_policy") or {}).get("principals")) or []
    if not auth:
        return None
    if "admin" in principals and auth.get("type") == "oauth2_password" and "/admin/" in auth.get("token_path", ""):
        return None
    if auth.get("type") == "oauth2_password" and "/admin/" in auth.get("token_path", "") and "admin" not in principals:
        return ("This artifact authenticates through " + auth["token_path"] + ", which issues tokens only for "
                "administrator accounts, but its accepted policy allows only " + ", ".join(principals) +
                ". It was built from a specification snapshot that declared the wrong login. Rebuild it after "
                "the API declares a login for these operations.")
    if not identities:
        return ("This artifact authenticates through " + auth.get("token_path", "the declared login") +
                ". Add a test identity for an account that endpoint accepts.")
    return None


def refusal(report, caller_controls_own_record):
    """How a failed run was refused. Authentication failure, general access denial and record-scope
    enforcement stay distinct; a 403/404 only counts as a record denial when the same caller is shown to
    use its own record successfully."""
    if not report or report.get("status") != "failed":
        return None
    failure = report["failure"]
    step = next((e for e in report["trace"] if e["step_id"] == failure["step_id"]), {})
    if failure["step_id"] == "connector_auth" or step.get("http_status") == 401:
        return "authentication_failure"
    if failure["outcome"] == "blocked_by_access_check":
        return "record_scope_enforced"
    if step.get("http_status") in (403, 404):
        return "target_record_denial" if caller_controls_own_record else "general_access_denial"
    return "other_failure"


def no_write(report):
    return bool(report) and all(e.get("write_state") in (None, "not_attempted") for e in report["trace"] if e["effect"] == "write")


def data_scope_problems(content, success, runs, identity):
    """What a tool that returns records it does not let the caller select still has to show: that a success test
    returned data at all, and, when the owner relies on the API to return only the caller's own data, that two
    identities each received data and none of it was the same record."""
    scoped = [r for r in content.get("access_requirements", []) if r["kind"] == "data_scope"]
    if not scoped:
        return []
    seen = {}
    for t in success:
        for name, rec in (((runs.get(t["execution_id"]) or {}).get("report") or {}).get("outputs_records") or {}).items():
            if rec["count"]:
                seen.setdefault(identity(t), set()).update(rec["id_sha256"])
    problems = []
    if not seen:
        problems.append("No passing success test returned any data. An empty result proves only that login works: add data for the test account and run it again")
    filtered = [r["id"] for r in scoped if r["enforcement"].get("api_filtered")]
    if filtered:
        pairs = [(a, b) for a in seen for b in seen if a < b and seen[a] and seen[b] and not seen[a] & seen[b]]
        if not pairs:
            problems.append(f"Data-scope requirement(s) {', '.join(filtered)} rely on the API returning only the caller's own data. Prove it: two test identities, "
                            "each with its own data, must each pass a success test that returns data, and no record may appear for both")
    return problems


def evidence(content, tests, executions):
    """Problems blocking publication, plus the passing tests of THIS artifact that justify it.

    Required categories follow the reviewed access requirements: every tool needs a passing success test;
    each record-scope requirement needs an explicit cross_user scenario (see README). Tools without
    record-scope requirements need no cross-user test.
    """
    runs = {e["id"]: e for e in executions}
    identity = lambda t: (runs.get(t["execution_id"]) or {}).get("identity")
    latest = {}
    for t in tests:
        latest[t["name"]] = t
    passed = [t for t in latest.values() if t["verdict"] == "passed"]
    success = [t for t in passed if t["expectation"]["status"] == "succeeded"]
    own = [t for t in passed if t.get("scenario") == "own_record"]
    controls = {identity(t) for t in own}
    owned = {(identity(t), t.get("selector_sha256")) for t in own}
    record_scope = sorted(r["id"] for r in content["access_requirements"] if r["kind"] == "record_scope" and not (content.get("access_policy") and r["enforcement"].get("unrestricted")))
    accepted, notes = [], []
    for t in passed:
        if t.get("scenario") != "cross_user":
            continue
        report, caller = (runs.get(t["execution_id"]) or {}).get("report"), identity(t)
        how = refusal(report, caller in controls)
        reasons = []
        if caller not in controls:
            reasons.append(f"caller {caller} has no passing own_record test, so its credential and allowed own-record use are unproven")
        if (t.get("record_owner"), t.get("selector_sha256")) not in owned:
            reasons.append(f"no passing own_record test by {t.get('record_owner')} on the same record")
        if how not in RECORD_REFUSALS:
            reasons.append(f"the refusal was {how}, not a record refusal")
        if not no_write(report):
            reasons.append("a write was attempted, so no unauthorized write is not shown")
        if reasons:
            notes.append(f"cross_user test {t['name']}: " + "; ".join(reasons))
        else:
            accepted.append(dict(test=t["name"], caller=caller, record_owner=t["record_owner"], refused_by=how))
    problems = [f"Latest run of sandbox test {n} did not pass" for n, t in sorted(latest.items()) if t["verdict"] not in ("passed", "retired")]
    if not success:
        problems.append("No passing sandbox test of this artifact expects success")
    problems += data_scope_problems(content, success, runs, identity)
    if record_scope and not accepted:
        problems.append(f"Record-scope requirement(s) {', '.join(record_scope)} need a passing cross_user test: a caller with its own passing own_record test, "
                        "targeting a record whose owner passed an own_record test on the same record, refused before any write")
        problems += notes
    required = ["success"] + (["cross_user_record_refusal"] if record_scope else [])
    return problems, dict(required=required, test_ids=sorted(t["id"] for t in passed), success_tests=sorted(t["name"] for t in success),
                          own_record_tests=sorted(t["name"] for t in own), access_denial_tests=sorted(a["test"] for a in accepted), record_scope=accepted)


def json_schema(schema):
    """Convert normalized OpenAPI schemas without widening enum or bound constraints.

    Only schema positions are traversed; defaults, examples and enum values are data.
    Works on legacy artifact schemas too, without changing the stored artifact/hash.
    """
    if isinstance(schema, list):
        return [json_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for k, v in schema.items():
        if k in ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas") and isinstance(v, dict):
            out[k] = {name: json_schema(s) for name, s in v.items()}
        elif k in ("items", "additionalProperties", "not", "anyOf", "oneOf", "allOf", "prefixItems",
                   "contains", "propertyNames", "if", "then", "else", "unevaluatedProperties", "unevaluatedItems"):
            out[k] = json_schema(v)
        else:
            out[k] = v
    for exclusive, inclusive in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
        if isinstance(out.get(exclusive), bool):
            enabled = out.pop(exclusive)
            if enabled and inclusive in out:
                out[exclusive] = out.pop(inclusive)
    if out.pop("nullable", False) and isinstance(out.get("type"), str):
        out["type"] = [out["type"], "null"]
    return out


def result_schema(content):
    nullable = lambda t: {"type": [t, "null"]}
    return {"type": "object", "required": ["status", "output", "error", "execution_id", "artifact"],
            "properties": {"status": {"enum": STATUSES}, "output": {"anyOf": [json_schema(content["output_schema"]), {"type": "null"}]},
                           "error": {"anyOf": [{"type": "object"}, {"type": "null"}]}, "message": nullable("string"),
                           "execution_id": nullable("string"), "writes": {"type": "array"}, "tool": nullable("string"),
                           "publication_id": nullable("string"), "artifact": nullable("object"),
                           "environment": {"enum": ["sandbox", "live_demo", None]}, "production_ready": {"const": False}}}


def tool_schemas(content):
    """Validate both wire schemas before publication, listing or execution is allowed."""
    schemas = dict(input_schema=json_schema(content["input_schema"]), output_schema=result_schema(content))
    for name, schema in schemas.items():
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            path = "/".join(str(p) for p in exc.absolute_path)
            raise AppError("invalid_mcp_schema", f"{name} is not valid JSON Schema 2020-12 at /{path}", 409) from None
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise AppError("invalid_mcp_schema", f"{name} must have an object root", 409)
    return schemas


def tool(pub, content):
    writes = [f'{s["method"]} {s["path"]}' for s in content["steps"] if s["effect"] == "write"]
    live = pub["environment"] == "live_demo"
    where = (f'LIVE DEMO publication of reviewed artifact {pub["artifact_id"]} (proposal version {pub["version"]}): calls the owner-configured '
             f'real API at {api_host(content)}; not a production release. ' if live else
             f'TEST-ONLY sandbox publication of reviewed artifact {pub["artifact_id"]} (proposal version {pub["version"]}); not production. ')
    description = (f'{content["purpose"]}. {content["description"]}. '
                   + (f'Writes: {", ".join(writes)}. ' if writes else "Read-only. ")
                   + where + "The caller identity is fixed by the server; arguments cannot select a user or credential.")
    meta = dict(publication_id=pub["id"], artifact_id=pub["artifact_id"], artifact_sha256=pub["artifact_sha256"],
                proposal_id=pub["proposal_id"], proposal_version=pub["version"], environment=pub["environment"],
                execution_enabled=True,  # only publications that pass every re-check are listed
                production_ready=False, activated=False)  # activated = production activation, which does not exist
    if live:
        meta["api_host"] = api_host(content)
    return dict(name=pub["tool_name"], title=content["name"], description=description, **tool_schemas(content), annotations=dict(read_only_hint=not writes),
                meta={"team_c": meta})


def api_host(content):
    """The real API's host[:port] for a live tool (never a path, query or credential)."""
    return urlparse(content["connector"]["base_url"]).netloc.rsplit("@", 1)[-1]


def envelope(pub, status, execution_id=None, output=None, error=None, message=None, writes=None):
    return dict(status=status, output=output if status == "succeeded" else None, error=error, message=message, execution_id=execution_id,
                writes=writes or [], tool=pub["tool_name"] if pub else None, publication_id=pub["id"] if pub else None,
                artifact=dict(id=pub["artifact_id"], sha256=pub["artifact_sha256"], proposal_id=pub["proposal_id"], proposal_version=pub["version"]) if pub else None,
                environment=pub.get("environment", ENVIRONMENT) if pub else None, production_ready=False)


def failure_category(report):
    """What kind of failure an executed (not rejected) run was, from its own report: the status, the failing step's
    outcome and the HTTP status the API answered. Never guessed beyond that."""
    if report["status"] == "partial":
        return "partial_write"
    if report["status"] == "outcome_unknown":
        return "unknown_outcome"
    failure = report["failure"] or {}
    step = next((e for e in report["trace"] if e["step_id"] == failure.get("step_id")), {})
    http = step.get("http_status")
    outcome = failure.get("outcome")
    if failure.get("step_id") == "connector_auth" or outcome == "connector_auth_failed" or http == 401:
        return "authentication_failed"
    if outcome == "blocked_by_access_check" or http == 403:
        return "permission_denied"
    by_status = {404: "not_found", 409: "conflict", 400: "validation_error", 422: "validation_error", 429: "rate_limited"}
    if http in by_status:
        return by_status[http]
    if isinstance(http, int) and http >= 500:
        return "api_unavailable"
    return {"not_sent": "api_unreachable", "no_response": "timeout", "invalid_response": "invalid_response",
            "redirect_rejected": "invalid_response", "invalid_request": "invalid_arguments"}.get(outcome, "internal_error")


def executed(pub, result):
    """A completed execution: permitted outputs to the caller; partial/outcome_unknown stay what they are."""
    report = result["report"]
    failure = report["failure"]
    writes = [dict(step_id=e["step_id"], write_state=e["write_state"]) for e in report["trace"] if e["effect"] == "write"]
    error = None
    if report["status"] != "succeeded":
        code = {"partial": "partial_write", "outcome_unknown": "outcome_unknown"}.get(report["status"], failure["outcome"])
        step = next((e for e in report["trace"] if e["step_id"] == failure["step_id"]), {})
        error = dict(code=code, category=failure_category(report), message=report["message"], step_id=failure["step_id"], outcome=failure["outcome"],
                     **{k: step[k] for k in ("http_status", "retry_after") if k in step})
    return envelope(pub, report["status"], result["execution_id"], result["outputs"], error, report["message"], writes)


def rejected(pub, exc):
    category = REJECTED_CATEGORIES.get(exc.code, "configuration_error")
    error = dict(code=exc.code, category=category, message=exc.message, **({"errors": exc.details["errors"]} if exc.details.get("errors") else {}))
    return envelope(pub, "rejected", exc.details.get("execution_id"), error=error, message=exc.message + " No request was sent.")
