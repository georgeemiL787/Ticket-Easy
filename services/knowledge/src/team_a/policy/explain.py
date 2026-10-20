from datetime import date

from team_a.policy.check import describe
from team_a.policy.rules_store import RuleStore
from team_a.schemas import RuleExplanation


def explain_rule(store: RuleStore, rule_id: str, as_of: date | None = None) -> RuleExplanation:
    rule = store.get(rule_id)
    as_of = as_of or date.today()
    active = rule.approval_status == "approved" and rule.effective_date <= as_of
    predicate = describe(rule.conditions)
    summary = (
        f"For '{rule.action}': if {predicate} then {rule.effect}, otherwise {rule.else_effect}."
        if rule.conditions else f"For '{rule.action}': always {rule.effect}."
    )
    if rule.applies_if:
        summary += f" Applies only when {describe(rule.applies_if)}."

    return RuleExplanation(
        rule_id=rule.rule_id,
        tenant_id=rule.tenant_id,
        active=active,
        approval_status=rule.approval_status,
        summary=summary,
        predicate=predicate,
        effect=rule.effect,
        else_effect=rule.else_effect,
        source=rule.source,
        user_message=rule.user_message,
    )
