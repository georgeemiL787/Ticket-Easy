"""check_action: the deterministic policy gate every side-effecting tool call must pass.

Order of checks (first failing check decides, all later checks are skipped):
  1. tenant scope        record belongs to another tenant           -> deny  TENANT_MISMATCH
  2. identity            personal data or side effect, unverified   -> deny  IDENTITY_REQUIRED
  3. mandatory risk      side effect during a flagged conversation  -> require_human MANDATORY_RISK
  4. approved rules      rules whose applies_if is false are skipped; of the rest the most
                         restrictive outcome wins (deny > require_human > allow);
                         a missing fact fails closed                 -> deny  MISSING_CONTEXT
                         (a rule that definitively denies takes precedence: RULE_BLOCKED)
  5. no rule applies             read-only                                   -> allow NO_RULE_READ_ONLY
                         high-risk side effect                       -> require_human HIGH_RISK_DEFAULT
                         other side effect                           -> require_human NO_RULE_SIDE_EFFECT
No LLM is involved anywhere in this module.
"""

from datetime import date
from typing import Any

from team_a.policy.rules_store import RuleStore
from team_a.schemas import (
    MANDATORY_ESCALATION,
    CheckActionRequest,
    Condition,
    Effect,
    PolicyDecision,
    Rule,
    RuleOutcome,
)

_SEVERITY: dict[str, int] = {"allow": 0, "require_human": 1, "deny": 2}
_MISSING = object()


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def derive_facts(facts: dict[str, Any], as_of: date) -> dict[str, Any]:
    """Add day counts computed from backend dates, unless the backend already supplied them."""
    out = dict(facts)
    delivered = _parse_date(facts.get("delivered_at"))
    if "days_since_delivery" not in out and delivered:
        out["days_since_delivery"] = (as_of - delivered).days
    expected = _parse_date(facts.get("expected_delivery_date"))
    if "days_late" not in out and expected:
        arrived = delivered or as_of
        out["days_late"] = max(0, (arrived - expected).days)
    return out


def _compare(actual: Any, op: str, expected: Any) -> bool:
    if op == "in":
        return actual in expected
    if op == "not_in":
        return actual not in expected
    if op == "==":
        return actual == expected
    if op == "!=":
        return actual != expected
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        raise TypeError(f"operator {op} needs a number, got {actual!r}")
    return {"<=": actual <= expected, "<": actual < expected,
            ">=": actual >= expected, ">": actual > expected}[op]


def describe(conditions: list[Condition]) -> str:
    if not conditions:
        return "always"
    parts = []
    for c in conditions:
        name = c.field if c.from_ == "facts" else f"arguments.{c.field}"
        parts.append(f"{name} {c.op} {c.value!r}")
    return " AND ".join(parts)


def evaluate(conditions: list[Condition], facts: dict, arguments: dict) -> tuple[bool | None, list[str]]:
    """Return (held, missing_fields). held is None when a required value is missing or malformed."""
    missing = []
    results = []
    for cond in conditions:
        source = facts if cond.from_ == "facts" else arguments
        name = cond.field if cond.from_ == "facts" else f"arguments.{cond.field}"
        actual = source.get(cond.field, _MISSING)
        if actual is _MISSING or actual is None:
            missing.append(name)
            continue
        try:
            results.append(_compare(actual, cond.op, cond.value))
        except TypeError:
            missing.append(name)
    return (None, missing) if missing else (all(results), [])


def check_action(req: CheckActionRequest, store: RuleStore) -> PolicyDecision:
    as_of = req.as_of or date.today()
    base = dict(
        request_id=req.request_id,
        tenant_id=req.tenant_id,
        conversation_id=req.conversation_id,
        action=req.tool.name,
    )
    tool = req.tool

    if req.resource_tenant_id is not None and req.resource_tenant_id != req.tenant_id:
        return PolicyDecision(**base, decision="deny", reason_code="TENANT_MISMATCH",
                              rationale="The record belongs to a different tenant.")

    if (tool.exposes_personal_data or tool.has_side_effects) and not req.identity.verified:
        return PolicyDecision(**base, decision="deny", reason_code="IDENTITY_REQUIRED",
                              rationale="Customer identity must be verified before this action.")

    flagged = sorted(set(req.risk_categories) & MANDATORY_ESCALATION)
    if flagged and tool.has_side_effects:
        return PolicyDecision(**base, decision="require_human", reason_code="MANDATORY_RISK",
                              rationale=f"Conversation flagged for mandatory escalation: {', '.join(flagged)}.")

    facts = derive_facts(req.facts, as_of)
    outcomes: list[RuleOutcome] = []
    missing: list[str] = []
    rules_by_id: dict[str, Rule] = {}
    for rule in store.active_for(tool.name, as_of):
        applies, scope_missing = evaluate(rule.applies_if, facts, req.arguments)
        if applies is False:
            continue
        held, rule_missing = (None, scope_missing) if applies is None else evaluate(
            rule.conditions, facts, req.arguments)
        rules_by_id[rule.rule_id] = rule
        if held is None:
            missing.extend(rule_missing)
            effect: Effect = "deny"
        else:
            effect = rule.effect if held else rule.else_effect
        outcomes.append(RuleOutcome(
            rule_id=rule.rule_id,
            predicate=describe(rule.conditions),
            held=held,
            effect_applied=effect,
            citation=rule.source.citation,
        ))

    if not outcomes:
        if not tool.has_side_effects:
            return PolicyDecision(**base, decision="allow", reason_code="NO_RULE_READ_ONLY",
                                  rationale="Read-only action with verified scope; no policy rule applies.")
        code = "HIGH_RISK_DEFAULT" if tool.risk == "high" else "NO_RULE_SIDE_EFFECT"
        return PolicyDecision(**base, decision="require_human", reason_code=code,
                              rationale="No approved rule covers this action; a human must decide.")

    # A rule that definitively denies explains the outcome better than a missing fact does.
    evaluated = [o for o in outcomes if o.held is not None]
    definitive_deny = [o for o in evaluated if o.effect_applied == "deny"]
    missing = list(dict.fromkeys(missing))
    if missing and not definitive_deny:
        return PolicyDecision(
            **base, decision="deny", reason_code="MISSING_CONTEXT",
            rationale=f"Cannot evaluate policy without: {', '.join(missing)}.",
            rule_outcomes=outcomes,
            citations=list(dict.fromkeys(o.citation for o in outcomes if o.held is None)),
            missing_fields=missing,
        )

    worst = max(evaluated, key=lambda o: _SEVERITY[o.effect_applied])
    decision = worst.effect_applied
    deciding = [o for o in evaluated if o.effect_applied == decision]
    code = {"allow": "RULES_PASSED", "deny": "RULE_BLOCKED", "require_human": "RULE_REQUIRES_HUMAN"}[decision]
    rationale = "; ".join(
        f"{o.rule_id}: {o.predicate} is {'true' if o.held else 'false'} -> {o.effect_applied}"
        for o in deciding
    )
    user_message = None if decision == "allow" else rules_by_id[deciding[0].rule_id].user_message
    return PolicyDecision(
        **base, decision=decision, reason_code=code, rationale=rationale,
        rule_outcomes=outcomes, citations=list(dict.fromkeys(o.citation for o in deciding)),
        user_message=user_message, missing_fields=missing,
    )
