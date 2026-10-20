"""Explain readiness without allowing a numeric score to override a gate."""
from .policies import resolve_inputs
from .semantics import capability, risks


def assess(content, inventory, policy=None, artifact=None, test_report=None):
    cap = capability(content, inventory)
    _, _, input_problems = resolve_inputs(content, inventory, policy)
    risk = risks(content, inventory, policy)
    factors = []
    def add(name, passed, detail, stage="build"):
        factors.append(dict(name=name, passed=bool(passed), detail=detail, stage=stage))
    add("schema", inventory.get("document_valid", inventory.get("valid", False)), "API document must be understood")
    add("capability", bool(cap["purpose"] and cap["operations"]), "A business purpose and operation mapping are required")
    add("inputs", not input_problems, "Resolve protected inputs and trusted context sources" if input_problems else "Input trust boundaries resolved")
    factors[-1]["technical_details"] = input_problems
    add("authorization", bool(policy), "Accept a structured access policy (legacy artifacts retain their reviewed enforcement)")
    add("side_effects", not cap["writes"] or policy and all(policy.get(k) is not None for k in ("financial", "irreversible", "external_side_effects", "idempotent")), "Writes require explicit effect and idempotency decisions")
    add("configuration", all(c["value_json"] is not None for c in content["configuration"]), "Required configuration must be supplied")
    add("authentication_and_scope", artifact and not artifact.get("execution_blockers"), "Authentication, ownership and tenant checks must compile", "publish")
    add("enforcement", artifact and bool(artifact.get("access_policy")), "Accepted policy must have generated runtime enforcement", "publish")
    for kind in ("positive", "negative"):
        cases = [c for c in (test_report or {}).get("cases", []) if c["kind"] == kind]
        add(kind + "_tests", cases and all(c["passed"] for c in cases), "Generated " + kind + " guard tests must pass; sandbox evidence is separately required", "publish")
    blockers = [f["detail"] for f in factors if not f["passed"]]
    return dict(score=round(100 * sum(f["passed"] for f in factors) / len(factors)), factors=factors, blockers=blockers,
                build_ready=all(f["passed"] for f in factors if f["stage"] == "build"), publish_ready=not blockers,
                activation=False, risk=risk)
