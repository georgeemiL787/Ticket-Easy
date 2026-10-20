"""Promotion rules that need no database: what must be identical between a tested artifact and its production twin, and which
idempotency header (if any) an API declares for each write.

A production artifact is built from the SAME approved proposal version as the sandbox or Live Demo artifact whose tests are the
evidence. The evidence only carries over if nothing but the destination differs, so promotion refuses any other difference.
"""
IDEMPOTENCY_HEADERS = ("idempotency-key", "x-idempotency-key")
DESTINATION_KEYS = {"id", "base_url", "environment"}


def differences(source, production):
    """Names of the artifact parts that differ besides the destination and the activation (empty: the evidence carries over).
    The production artifact may not have execution blockers."""
    found = [k for k in sorted(set(source) | set(production)) if k not in ("connector", "activation", "execution_blockers") and source.get(k) != production.get(k)]
    sc, pc = source.get("connector") or {}, production.get("connector") or {}
    found += [f"connector.{k}" for k in sorted(set(sc) | set(pc)) if k not in DESTINATION_KEYS and sc.get(k) != pc.get(k)]
    if production.get("execution_blockers"):
        found.append("execution_blockers")
    return found


def idempotency_plan(content, operations):
    """{write step id: the idempotency header the API declares for it, or None}. `operations` is the inventory's operation list."""
    ops = {o["id"]: o for o in operations}
    plan = {}
    for step in content["steps"]:
        if step["effect"] != "write":
            continue
        declared = [p["name"] for p in (ops.get(step["operation_id"]) or {}).get("parameters", [])
                    if p.get("in") == "header" and str(p.get("name", "")).lower() in IDEMPOTENCY_HEADERS]
        plan[step["id"]] = declared[0] if declared else None
    return plan
