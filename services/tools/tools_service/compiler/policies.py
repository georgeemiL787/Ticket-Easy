"""Owner policy contracts and pure compilation. A policy is never runtime verification."""
import copy
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ..config import AppError
from .semantics import PROTECTED, input_semantics, evidence


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AccessCheck(Contract):
    requirement_id: str
    kind: Literal["ownership", "tenant", "account"] = "ownership"
    resource: str = Field(min_length=1)
    step_id: str
    response_status: str
    resource_field: str
    identity_source: str
    rule: Literal["equals"] = "equals"
    failure: Literal["deny"] = "deny"


class InputResolution(Contract):
    step_id: str
    target: str
    category: Literal["business_input", "identity_context", "runtime_context", "server_derived", "resource_selector", "configuration", "sensitive_internal", "unknown"]
    source: Literal["runtime_argument", "trusted_application_context", "business_configuration", "omit"]
    reference: str = ""
    reason: str = Field(min_length=1)


class ScopeDecision(Contract):
    requirement_id: str
    mode: Literal["checked", "unrestricted", "api_filtered"]
    reason: str = Field(min_length=1)


class AccessPolicy(Contract):
    principals: list[Literal["public", "end_user", "resource_owner", "admin", "service", "guest"]] = Field(min_length=1)
    authentication: Literal["required", "none", "declared"] = "declared"
    role_source: str | None = None
    roles: list[str] = Field(default_factory=list)
    permission_source: str | None = None
    permissions: list[str] = Field(default_factory=list)
    scopes: list[ScopeDecision] = Field(default_factory=list)
    checks: list[AccessCheck] = Field(default_factory=list)
    inputs: list[InputResolution] = Field(default_factory=list)
    failure: Literal["deny"] = "deny"
    financial: bool | None = None
    irreversible: bool | None = None
    external_side_effects: bool | None = None
    idempotent: bool | None = None
    explanation: str = ""

    @model_validator(mode="after")
    def complete(self):
        if bool(self.roles) != bool(self.role_source) or bool(self.permissions) != bool(self.permission_source):
            raise ValueError("Roles and permissions require explicit trusted context sources and allowed values")
        if "resource_owner" in self.principals and not any(c.kind == "ownership" for c in self.checks):
            raise ValueError("Resource-owner access requires an ownership check")
        if len({(i.step_id, i.target) for i in self.inputs}) != len(self.inputs):
            raise ValueError("Input decisions must be unique per step and target")
        if len({(c.requirement_id, c.kind) for c in self.checks}) != len(self.checks):
            raise ValueError("Access checks must be unique per requirement")
        if len({c.requirement_id for c in self.scopes}) != len(self.scopes):
            raise ValueError("Scope decisions must be unique per requirement")
        if any(not value for value in self.roles + self.permissions):
            raise ValueError("Role and permission values cannot be empty")
        return self


class PolicySubmission(Contract):
    expected_revision: int
    policy: AccessPolicy


ANONYMOUS = {"public", "guest"}


def login_conflicts(principals, authentication, auth, operations=""):
    """Where a policy asks for something the API's declared login cannot provide, as (code, owner-facing message).

    auth is the executable login the tool's operations declare (None when they declare none). The same rules
    decide policy acceptance, the policy form, the access advisor and the build, so they cannot disagree.
    """
    where = f" ({operations})" if operations else ""
    found = []
    if authentication == "none" and auth:
        found.append(("policy_auth_conflict", f"The API{where} requires a login, so the policy cannot set authentication to none."))
    if authentication == "required" and not auth:
        found.append(("policy_auth_unresolved", f"The API{where} declares no supported login, so authentication cannot be required. Use the declared API authentication setting."))
    signed_in = sorted(set(principals) - ANONYMOUS)
    if not auth and signed_in:
        found.append(("policy_identity_unresolved", f"The API{where} declares no login, so the tools service cannot verify who is calling and cannot allow {', '.join(signed_in)} callers. "
                      "Choose Public or Guest/session user, or use an API version that declares authentication (OAuth2 password or HTTP bearer)."))
    return found


def declared_login(content, inventory):
    """The executable login the tool's steps declare (as the build derives it), and the operations it was read from."""
    from ..artifacts import auth_for
    ops = {o["id"]: o for o in inventory["operations"]}
    labels = ", ".join(f'{ops[s["operation_id"]]["method"]} {ops[s["operation_id"]]["path"]}' for s in content["steps"] if s["operation_id"] in ops)
    auth = next((a for a in (auth_for(ops[s["operation_id"]], []) for s in content["steps"] if s["operation_id"] in ops) if a), None)
    return auth, labels


def runtime_policy(record, auth, context_fields):
    p = AccessPolicy.model_validate(record["content"])
    fields = set(context_fields)
    required = {f for f in (p.role_source, p.permission_source) if f} | {c.identity_source for c in p.checks}
    required |= {i.reference for i in p.inputs if i.source == "trusted_application_context"}
    if required - fields - {"business_id"}:
        raise AppError("policy_context_missing", "Declare policy context fields on the connector: " + ", ".join(sorted(required - fields - {"business_id"})))
    for code, message in login_conflicts(p.principals, p.authentication, auth):
        raise AppError(code, message)
    return dict(**p.model_dump(), id=record["id"], sha256=record["sha256"], accepted_by=record["accepted_by"],
                authentication_required=bool(auth), evidence=[evidence("VERIFIED", "owner_policy", record["id"], "Owner accepted this structured policy.", "policy_decision")],
                implementation_state="UNRESOLVED")


def runtime_exposure_allowed(semantics, target, choice=None):
    meaning = semantics.get(target, {})
    descendants = any(k.startswith(target + ".") and m["category"] in PROTECTED | {"unknown"} for k, m in semantics.items())
    protected = descendants or meaning.get("category") in PROTECTED | {"unknown"}
    return not protected or bool(choice and not descendants and meaning.get("state") != "DECLARED" and choice["category"] in ("business_input", "resource_selector"))


def resolve_inputs(content, inventory, policy):
    """Return explicit, reviewable binding changes. Never let model arguments override protected inputs."""
    value = copy.deepcopy(content)
    ops = {o["id"]: o for o in inventory["operations"]}
    choices = {(i["step_id"], i["target"]): i for i in (policy or {}).get("inputs", [])}
    used, protected, problems = set(), [], []
    for step in value["steps"]:
        op = ops[step["operation_id"]]
        semantics = input_semantics(op)
        bindings = {b["target"]: b for b in step["bindings"]}
        for (sid, target), decision in choices.items():
            if sid != step["id"]:
                continue
            if target not in op["inputs"]:
                problems.append(f"{sid}: {target} is not a bindable input")
                continue
            used.add((sid, target))
            meaning = semantics[target]
            if decision["source"] == "omit":
                if op["inputs"][target]["required"]:
                    problems.append(f"{sid}: required input {target} cannot be omitted; an implementation change is needed")
                else:
                    bindings.pop(target, None)
            else:
                if not decision["reference"]:
                    problems.append(f"{sid}: {target} requires an explicit source reference")
                if decision["source"] == "runtime_argument" and (decision["category"] in PROTECTED or meaning["category"] in PROTECTED and meaning["state"] == "DECLARED"):
                    problems.append(f"{sid}: declared protected input {target} cannot be delegated to the LLM")
                bindings[target] = dict(target=target, kind=decision["source"], reference=decision["reference"], step_id=None, response_status=None)
        for target, binding in bindings.items():
            meaning = semantics.get(target, {})
            choice = choices.get((step["id"], target))
            if binding["kind"] == "runtime_argument" and not runtime_exposure_allowed(semantics, target, choice):
                problems.append(f"{step['id']}: resolve the protected or unknown input {target} before exposing a tool argument")
            if binding["kind"] == "trusted_application_context" and binding["reference"] != "business_id" and not choice:
                problems.append(f"{step['id']}: trusted context {target} needs an accepted policy mapping")
            if binding["kind"] != "runtime_argument":
                protected.append(dict(step_id=step["id"], target=target, reference=binding["reference"], category=meaning.get("category", "unknown")))
        step["bindings"] = list(bindings.values())
    if set(choices) - used:
        problems.append("Some input decisions do not belong to this capability")
    return value, protected, list(dict.fromkeys(problems))


def suggested_inputs(content, inventory, existing):
    """Form defaults only. These suggestions never participate in compilation."""
    choices = {(i["step_id"], i["target"]) for i in existing}
    ops = {o["id"]: o for o in inventory["operations"]}
    suggestions = []
    for step in content["steps"]:
        semantics = input_semantics(ops[step["operation_id"]])
        for binding in step["bindings"]:
            target = binding["target"]
            meaning = semantics.get(target, {})
            if ((step["id"], target) in choices or meaning.get("category") not in {"identity_context", "runtime_context"}
                    or binding["kind"] not in {"runtime_argument", "trusted_application_context"}):
                continue
            reference = binding["reference"] if binding["kind"] == "trusted_application_context" else target.split(".", 1)[-1]
            suggestions.append(dict(step_id=step["id"], target=target, category=meaning["category"],
                                    source="trusted_application_context", reference=reference,
                                    reason="Supply this API context from the registered test identity instead of caller arguments"))
    return suggestions


def authorize(policy, identity):
    """Predicate over trusted connector identity only; no caller arguments or free text."""
    if not policy:
        return
    if not identity:
        raise AppError("policy_denied", "An operator-registered identity is required", 403)
    scopes = set(policy["principals"])
    if "resource_owner" in scopes:
        scopes.add("end_user")
    if identity.get("scope") not in scopes:
        raise AppError("policy_denied", "This identity class is not allowed by the accepted policy", 403)
    context = identity.get("context") or {}
    for source, required, mode in ((policy.get("role_source"), policy.get("roles", []), "any"), (policy.get("permission_source"), policy.get("permissions", []), "all")):
        if not required:
            continue
        actual = context.get(source)
        actual = {actual} if isinstance(actual, str) else set(actual) if isinstance(actual, list) and all(isinstance(x, str) for x in actual) else set()
        permitted = bool(actual & set(required)) if mode == "any" else set(required) <= actual
        if not permitted:
            raise AppError("policy_denied", "Required trusted roles or permissions are missing", 403)


# Response fields that commonly name the account a record belongs to. A match is only a form
# suggestion for the owner to review; the compiler and sandbox tests remain the authority.
OWNER_FIELDS = ("customer_id", "user_id", "owner_id", "account_id", "member_id", "client_id", "buyer_id", "guest_id")


def suggested_checks(content, inventory, requirements):
    """Ownership-check form defaults for record-scope requirements, each with the schema evidence behind it.

    A suggestion is made only when a read step of the same requirement returns a required,
    non-nullable identity-like field on success. Nothing here is accepted or compiled until the owner
    accepts the policy, and the owner can change or clear every field.
    """
    ops = {o["id"]: o for o in inventory["operations"]}
    result = []
    for r in requirements:
        if r["kind"] != "record_scope":
            continue
        for step in content["steps"]:
            op = ops.get(step["operation_id"])
            if not op or op["method"] != "GET" or f'{op["method"]} {op["path"]}' not in r["operations"]:
                continue
            match = next(((code, name) for code, response in sorted(op.get("responses", {}).items()) if code.startswith("2")
                          for name in OWNER_FIELDS
                          if name in ((response.get("schema") or {}).get("required") or [])
                          and ((response.get("schema") or {}).get("properties") or {}).get(name, {}).get("type") in ("string", "integer")
                          and not ((response.get("schema") or {}).get("properties") or {})[name].get("nullable")), None)
            if match:
                code, name = match
                result.append(dict(requirement_id=r["id"],
                                   check=dict(requirement_id=r["id"], kind="ownership", resource=op.get("business_label") or "Record", step_id=step["id"],
                                              response_status=code, resource_field="/" + name, identity_source=name),
                                   scope=dict(requirement_id=r["id"], mode="checked", reason=f"Callers may only reach records whose {name} is their own."),
                                   evidence=f'{op["method"]} {op["path"]} always returns {name} (required in the {code} response schema).'))
                break
    return result
