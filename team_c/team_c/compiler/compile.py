"""Compile accepted policy into bindings and ordered, validated enforcement."""
from ..config import AppError
from ..grounding import validate_proposal
from ..models import ProposalContent
from .policies import resolve_inputs, runtime_policy
from .semantics import capability, risks


def prepare(content, inventory, record, connector):
    effective, protected, problems = resolve_inputs(content, inventory, record["content"] if record else None)
    if problems:
        raise AppError("semantic_inputs_unresolved", "Resolve input trust boundaries before building", details={"blockers": problems})
    context = {}
    ops = {o["id"]: o for o in inventory["operations"]}
    declared = set(connector.get("context_fields") or []) | {"business_id"}
    for s in effective["steps"]:
        for b in s["bindings"]:
            if b["kind"] != "trusted_application_context" or b["reference"] == "business_id":
                continue
            if b["reference"] not in declared:
                raise AppError("policy_context_missing", "Declare mapped trusted context fields on the connector")
            schema = ops[s["operation_id"]]["inputs"][b["target"]]["schema"]
            if b["reference"] in context and context[b["reference"]] != schema:
                raise AppError("policy_context_conflict", "One trusted context field cannot satisfy incompatible API schemas")
            context[b["reference"]] = schema
    fields = validate_proposal(ProposalContent.model_validate(effective), inventory, trusted_context=context)
    if fields["blockers"]:
        raise AppError("policy_configuration_missing", "; ".join(fields["blockers"]))
    return effective, fields, protected, context


def guards(record, content, inventory, requirements, connector, auth):
    # Import here to keep the generic compiler independent of artifact serialization.
    from ..artifacts import enforcement_for
    policy = runtime_policy(record, auth, connector.get("context_fields") or [])
    known = {r["id"]: r for r in requirements}
    checks = policy["checks"]
    scopes = {c["requirement_id"]: c for c in policy["scopes"]}
    if (set(scopes) | {c["requirement_id"] for c in checks}) - set(known):
        raise AppError("policy_scope", "Policy scope does not belong to this proposal version")
    ops = {o["id"]: o for o in inventory["operations"]}
    result = []
    for r in requirements:
        matching = [c for c in checks if c["requirement_id"] == r["id"]]
        if matching and r["kind"] != "record_scope":
            raise AppError("policy_scope", "Resource checks must reference record-scope requirements")
        unrestricted = scopes.get(r["id"], {}).get("mode") == "unrestricted"
        if unrestricted and matching:
            raise AppError("policy_scope", "A scope cannot be unrestricted and checked")
        if r["kind"] == "record_scope" and not matching and not unrestricted:
            raise AppError("policy_scope_unresolved", "Every resource selector needs an explicit scope decision or check")
        if r["kind"] == "account_administration" and not (set(policy["principals"]) <= {"admin", "service"} or policy["roles"] or policy["permissions"]):
            raise AppError("policy_scope_unresolved", "Account administration requires explicit privileged principals, roles or permissions")
        if not matching:
            result.append(dict(id=r["id"], kind=r["kind"], text=r["text"], owner_confirmed_answer="Structured policy decision",
                               enforcement=dict(status="configured", mechanism="structured_policy", enforced_by="team_c_executor", policy_sha256=record["sha256"], unrestricted=unrestricted)))
        for check in matching:
            config = dict(mechanism="response_field_matches_context", step_id=check["step_id"], response_status=check["response_status"],
                          pointer=check["resource_field"], context_field=check["identity_source"], comparison=check["rule"], check_point="after_step")
            guard = enforcement_for(r, config, content, ops, auth, set(connector.get("context_fields") or []))
            guard["scope_kind"] = check["kind"]
            result.append(dict(id=r["id"], kind=r["kind"], text=r["text"], owner_confirmed_answer="Structured policy decision", enforcement=guard))
    policy["implementation_state"] = "GENERATED"
    return policy, result


def manifest(content, inventory, policy, protected, context):
    return dict(version=1, capability=capability(content, inventory), risk=risks(content, inventory, policy),
                protected_bindings=protected, context_schemas=context,
                evidence_note="Accepted policy verifies owner intent. Generated enforcement and simulated tests do not prove target API authorization.")
