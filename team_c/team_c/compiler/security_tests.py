"""Policy-derived guard tests. Synthetic identities are never sent to a target API."""
import copy
from ..config import AppError
from .policies import authorize


def scope_matches(found, expected):
    return not isinstance(found, bool) and not isinstance(expected, bool) and isinstance(found, (str, int)) and isinstance(expected, (str, int)) and found != "" and expected != "" and str(found) == str(expected)


def exercise_guard(artifact, requirement, matching):
    """Run the real executor's read/check/write sequence against an isolated HTTP fixture.

    This checks guard wiring, failure propagation and suppression of later writes. It does
    not claim that the synthetic resource is a valid record in the business API.
    """
    import httpx
    from ..executor import Run
    guard = requirement["enforcement"]
    body = "same" if matching else "different"
    for part in reversed(guard["pointer"].split("/")[1:] if guard["pointer"] else []):
        body = {part.replace("~1", "/").replace("~0", "~"): body}
    common = dict(operation_id="synthetic", source_pointer="", parameters=[], body=None)
    read = dict(common, id=guard["step_id"], method="GET", path="/guard-fixture", effect="read", responses={guard["response_status"]: dict(schema={})})
    write = dict(common, id="guard-test-write", method="POST", path="/guard-fixture", effect="write", responses={"200": dict(schema=None)})
    isolated = dict(artifact, access_policy=None, access_requirements=[requirement], steps=[read, write], outputs=[],
                    connector=dict(artifact["connector"], auth=None), configuration={}, input_schema=dict(type="object", properties={}, required=[]))
    sent = []
    def target(request):
        sent.append(request.method)
        return httpx.Response(int(guard["response_status"]) if request.method == "GET" else 200, json=body)
    identity = dict(context={guard["context_field"]: "same"})
    result, outputs = Run(isolated, {}, identity, "http://policy-fixture.invalid", httpx.MockTransport(target)).execute()
    if matching:
        return result["status"] == "succeeded" and sent == ["GET", "POST"]
    return result["status"] == "failed" and result["failure"]["outcome"] == "blocked_by_access_check" and sent == ["GET"] and outputs is None


def run(artifact):
    from ..executor import credentials, validate_arguments
    policy = artifact.get("access_policy")
    if not policy:
        return dict(scope="runtime_guard_simulation", passed=False, cases=[], limitations=["Legacy policy has no generated test plan"])
    cases = []
    def case(name, kind, expect, action):
        try:
            action()
            allowed = True
        except AppError:
            allowed = False
        cases.append(dict(name=name, kind=kind, passed=allowed == expect, expected="allow" if expect else "deny"))
    context = {}
    if policy.get("role_source"):
        context[policy["role_source"]] = policy["roles"]
    if policy.get("permission_source"):
        context[policy["permission_source"]] = sorted(set(context.get(policy["permission_source"], [])) | set(policy["permissions"]))
    for principal in policy["principals"]:
        scope = "end_user" if principal == "resource_owner" else principal
        identity = dict(scope=scope, context=copy.deepcopy(context))
        case("allowed_" + principal, "positive", True, lambda: authorize(policy, identity))
    identity = dict(scope="end_user" if policy["principals"][0] == "resource_owner" else policy["principals"][0], context=context)
    case("unregistered_identity", "negative", False, lambda: authorize(policy, None))
    case("unsupported_identity_class", "negative", False, lambda: authorize(policy, dict(scope="unregistered", context=context)))
    allowed_scopes = set(policy["principals"]) | ({"end_user"} if "resource_owner" in policy["principals"] else set())
    for scope in sorted({"public", "guest", "end_user", "admin", "service"} - allowed_scopes):
        case("forbidden_" + scope, "negative", False, lambda: authorize(policy, dict(scope=scope, context=context)))
    for source in (policy.get("role_source"), policy.get("permission_source")):
        if source:
            missing = dict(identity, context={k: v for k, v in context.items() if k != source})
            forbidden = dict(identity, context=dict(context, **{source: []}))
            case("missing_" + source, "negative", False, lambda: authorize(policy, missing))
            case("forbidden_" + source, "negative", False, lambda: authorize(policy, forbidden))
    if artifact["connector"]["auth"]:
        case("missing_authentication", "negative", False, lambda: credentials(identity, artifact))
    # Guard cases test the exact predicate used after the checked read and before later writes.
    for n, req in enumerate(artifact["access_requirements"]):
        guard = req["enforcement"]
        if guard.get("mechanism") != "response_field_matches_context":
            continue
        for suffix, found, expected, want in (("same_scope", "a", "a", True), ("cross_scope", "b", "a", False), ("missing_scope", None, "a", False)):
            cases.append(dict(name=f"{n}_{guard.get('scope_kind', 'ownership')}_{suffix}", kind="positive" if want else "negative", passed=scope_matches(found, expected) == want, expected="allow" if want else "deny"))
        for matching in (True, False):
            cases.append(dict(name=f"{n}_{guard.get('scope_kind', 'ownership')}_executor_{'allow' if matching else 'deny_before_write'}",
                              kind="positive" if matching else "negative", passed=exercise_guard(artifact, req, matching), expected="allow" if matching else "deny"))
    protected = list(artifact.get("compiler", {}).get("protected_bindings", []))
    protected += [dict(step_id=i["step_id"], target=i["input"]) for i in artifact.get("compiler", {}).get("capability", {}).get("protected_inputs", []) if i["category"] == "server_derived"]
    for b in protected:
        # An API field must not exist as an undeclared escape hatch in the tool interface.
        key = b["target"]
        if key not in artifact["input_schema"]["properties"]:
            schema = dict(artifact["input_schema"], required=[])
            case("override_" + b["step_id"] + "_" + key, "negative", False, lambda: validate_arguments(schema, {key: "injected"}))
    return dict(scope="runtime_guard_simulation", passed=bool(cases) and all(c["passed"] for c in cases), cases=cases,
                limitations=["Synthetic guard tests do not verify target API behavior. Actual sandbox success and applicable cross-owner tests remain mandatory."])
