"""Stand-in for the Team A rule checker: may this action happen? Yes, no, or only with a human.

Deterministic, no AI. The first decisive step wins:
  1. record of another tenant                              -> deny TENANT_MISMATCH
  2. personal data or side effect, identity not verified   -> deny IDENTITY_REQUIRED
  3. side effect and a mandatory risk category             -> require_human MANDATORY_RISK
  4. approved rules of the action (applies_if holds): the most restrictive effect wins (deny > require_human > allow);
     a missing or malformed fact -> deny MISSING_CONTEXT, unless another rule already denies (RULE_BLOCKED)
  5. no rule: read -> allow NO_RULE_READ_ONLY; high-risk write -> require_human HIGH_RISK_DEFAULT;
     other write -> require_human NO_RULE_SIDE_EFFECT
  6. a recorded human approval turns require_human into allow APPROVED_BY_HUMAN; a deny stays a deny.
Conditions read facts unless they say "from": "arguments"; an argument never stands in for a fact and a fact never
stands in for an argument. Only status=approved rules count. If the rules cannot be read, the check fails closed
(UpstreamError), it never guesses.
"""

import json
import math
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from team_b.contracts.errors import UpstreamError
from team_b.contracts.policy import CheckActionRequest, LocalizedText, PolicyDecision, PolicyEffect, RuleOutcome

SERVICE = "rule_checker"
_SEVERITY: dict[str, int] = {"allow": 0, "require_human": 1, "deny": 2}
_MISSING = object()


class _Condition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    field: str
    op: Literal["<=", "<", ">=", ">", "==", "!=", "in", "not_in"]
    value: Any
    source: Literal["facts", "arguments"] = Field(default="facts", alias="from")


class _Rule(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    rule_id: str
    action: str
    status: str
    applies_if: tuple[_Condition, ...] = ()
    conditions: tuple[_Condition, ...] = ()
    effect: PolicyEffect
    else_effect: PolicyEffect
    citation: str = ""
    user_message: LocalizedText


class _RuleBook(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    tenant_id: str
    rules: tuple[_Rule, ...]


# What the customer is told when no rule speaks (a rule's own user_message is used when one decided).
_MESSAGES: dict[str, LocalizedText] = {
    "TENANT_MISMATCH": LocalizedText(
        en="I can't act on that record from this shop's chat.",
        ar="مقدرش أتعامل مع السجل ده من شات المتجر ده.",
    ),
    "IDENTITY_REQUIRED": LocalizedText(
        en="I need to verify your identity before I can do this.",
        ar="لازم أتأكد من هويتك الأول قبل ما أنفذ ده.",
    ),
    "MANDATORY_RISK": LocalizedText(
        en="This needs a colleague from our customer care team. I'm passing it to them now.",
        ar="الموضوع ده محتاج زميل من فريق خدمة العملاء. هحوّله ليهم دلوقتي.",
    ),
    "MISSING_CONTEXT": LocalizedText(
        en="I couldn't confirm the details I need to do this safely, so I can't go ahead.",
        ar="معرفتش أتأكد من التفاصيل اللي محتاجها عشان أنفذ ده بأمان، فمش هقدر أكمل.",
    ),
    "HIGH_RISK_DEFAULT": LocalizedText(
        en="This request needs approval from our customer care team. I'm passing it to them now.",
        ar="الطلب ده محتاج موافقة من فريق خدمة العملاء. هحوّله ليهم دلوقتي.",
    ),
    "NO_RULE_SIDE_EFFECT": LocalizedText(
        en="This request needs a colleague from our customer care team to review it. I'm passing it to them now.",
        ar="الطلب ده محتاج زميل من فريق خدمة العملاء يراجعه. هحوّله ليهم دلوقتي.",
    ),
}


class RuleCheckerStandin:
    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir
        self._books: dict[str, _RuleBook] = {}
        self._risk: dict[str, dict[str, bool]] = {}
        self._fail = 0

    # ---- the PolicyGate plug ----

    async def check_action(self, request: CheckActionRequest) -> PolicyDecision:
        self._maybe_fail()
        decision = self._decide(request)
        if decision.decision == "require_human" and request.human_approval is not None:
            approval = request.human_approval
            return decision.model_copy(
                update={
                    "decision": "allow",
                    "reason_code": "APPROVED_BY_HUMAN",
                    "rationale": f"{decision.reason_code} was approved by {approval.approved_by} "
                    f"(case {approval.case_id}). {decision.rationale}",
                    "user_message": None,
                }
            )
        return decision

    # ---- failure switch ----

    def fail_next(self, times: int = 1) -> None:
        """The next `times` checks raise UpstreamError(retryable=True): nothing may then be executed."""
        if times < 1:
            raise ValueError("times must be at least 1")
        self._fail += times

    def reset(self) -> None:
        self._fail = 0

    def inject(self, spec: Mapping[str, Any]) -> None:
        """Apply a switch from a scenario file, e.g. {"switch": "fail_next", "times": 1}."""
        switch = spec.get("switch")
        operation = spec.get("operation", "check_action")
        if operation != "check_action":
            raise ValueError("rule_checker has only the operation check_action")
        if switch == "fail_next":
            self.fail_next(int(spec.get("times", 1)))
        elif switch == "reset":
            self.reset()
        else:
            raise ValueError(f"unknown rule_checker switch: {switch!r}")

    def _maybe_fail(self) -> None:
        if self._fail > 0:
            self._fail -= 1
            raise UpstreamError(SERVICE, "BACKEND_UNAVAILABLE", "injected failure on check_action", retryable=True)

    # ---- the evaluation order ----

    def _decide(self, request: CheckActionRequest) -> PolicyDecision:
        def answer(decision: PolicyEffect, code: str, rationale: str, **extra: Any) -> PolicyDecision:
            if decision != "allow" and "user_message" not in extra:
                extra["user_message"] = _MESSAGES.get(code)
            return PolicyDecision(
                request_id=request.request_id, tenant_id=request.tenant_id,
                conversation_id=request.conversation_id, action=request.action,
                decision=decision, reason_code=code, rationale=rationale, **extra,
            )  # fmt: skip

        tool = request.tool
        if request.resource_tenant_id is not None and request.resource_tenant_id != request.tenant_id:
            return answer("deny", "TENANT_MISMATCH", "The record belongs to a different tenant.")

        if (tool.personal_data or tool.side_effects) and not request.identity.verified:
            return answer("deny", "IDENTITY_REQUIRED", "Customer identity must be verified before this action.")

        if tool.side_effects:
            flagged = self._mandatory(request)
            if flagged:
                return answer(
                    "require_human", "MANDATORY_RISK",
                    f"Conversation flagged for mandatory escalation: {', '.join(flagged)}.",
                )  # fmt: skip

        outcomes, rules, missing = self._evaluate_rules(request)
        if not outcomes:
            return self._without_rule(request, answer)
        return self._combine(outcomes, rules, missing, answer)

    def _without_rule(self, request: CheckActionRequest, answer: Any) -> PolicyDecision:
        tool = request.tool
        if not tool.side_effects:
            return answer("allow", "NO_RULE_READ_ONLY", "Read-only action with verified scope; no policy rule applies.")
        code = "HIGH_RISK_DEFAULT" if tool.risk == "high" else "NO_RULE_SIDE_EFFECT"
        return answer("require_human", code, "No approved rule covers this action; a human must decide.")

    def _evaluate_rules(self, request: CheckActionRequest) -> tuple[list[RuleOutcome], dict[str, _Rule], list[str]]:
        facts = derive_facts(request.facts, request.as_of)
        outcomes: list[RuleOutcome] = []
        rules: dict[str, _Rule] = {}
        missing: list[str] = []
        for rule in self._book(request.tenant_id).rules:
            if rule.status != "approved" or rule.action != request.action:
                continue
            applies, gaps = evaluate(rule.applies_if, facts, request.arguments)
            if applies is False:
                continue
            held, gaps = (None, gaps) if applies is None else evaluate(rule.conditions, facts, request.arguments)
            effect: PolicyEffect = "deny" if held is None else (rule.effect if held else rule.else_effect)
            missing.extend(gaps)
            rules[rule.rule_id] = rule
            outcomes.append(
                RuleOutcome(
                    rule_id=rule.rule_id, predicate=describe(rule.conditions), held=held,
                    effect_applied=effect, citation=rule.citation,
                )
            )  # fmt: skip
        return outcomes, rules, list(dict.fromkeys(missing))

    def _combine(
        self, outcomes: list[RuleOutcome], rules: dict[str, _Rule], missing: list[str], answer: Any
    ) -> PolicyDecision:
        decided = [o for o in outcomes if o.held is not None]
        if missing and not any(o.effect_applied == "deny" for o in decided):
            return answer(
                "deny", "MISSING_CONTEXT", f"Cannot evaluate policy without: {', '.join(missing)}.",
                rule_outcomes=tuple(outcomes), missing_fields=tuple(missing),
                citations=tuple(dict.fromkeys(o.citation for o in outcomes if o.held is None and o.citation)),
            )  # fmt: skip
        worst = max((o.effect_applied for o in decided), key=lambda e: _SEVERITY[e])
        deciding = [o for o in decided if o.effect_applied == worst]
        code = {"allow": "RULES_PASSED", "deny": "RULE_BLOCKED", "require_human": "RULE_REQUIRES_HUMAN"}[worst]
        rationale = "; ".join(
            f"{o.rule_id}: {o.predicate} is {'true' if o.held else 'false'} -> {o.effect_applied}" for o in deciding
        )
        message = None if worst == "allow" else rules[deciding[0].rule_id].user_message
        return answer(
            worst, code, rationale, rule_outcomes=tuple(outcomes), missing_fields=tuple(missing),
            citations=tuple(dict.fromkeys(o.citation for o in deciding if o.citation)), user_message=message,
        )  # fmt: skip

    # ---- data ----

    def _mandatory(self, request: CheckActionRequest) -> list[str]:
        """Risk categories that force a human. A category the tenant does not list counts as mandatory (fail closed)."""
        if not request.risk_categories:
            return []
        known = self._risk_table(request.tenant_id)
        return sorted({c for c in request.risk_categories if known.get(c, True)})

    def _risk_table(self, tenant_id: str) -> dict[str, bool]:
        if tenant_id not in self._risk:
            path = self._dir / tenant_id / "risk.json"
            table: dict[str, bool] = {}
            try:
                for name, spec in json.loads(path.read_text(encoding="utf-8"))["categories"].items():
                    table[name] = spec.get("mandatory_escalation", True) is not False
            except (OSError, ValueError, KeyError, AttributeError, TypeError):
                table = {}  # unreadable: every category is treated as mandatory
            self._risk[tenant_id] = table
        return self._risk[tenant_id]

    def _book(self, tenant_id: str) -> _RuleBook:
        if tenant_id not in self._books:
            path = self._dir / tenant_id / "rules.json"
            if not path.is_file():
                raise UpstreamError(SERVICE, "TENANT_NOT_FOUND", f"no rules for tenant {tenant_id}")
            try:
                book = _RuleBook.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError, ValidationError) as exc:
                raise UpstreamError(SERVICE, "RULES_INVALID", f"rules for tenant {tenant_id} cannot be read") from exc
            if book.tenant_id != tenant_id:
                raise UpstreamError(SERVICE, "RULES_INVALID", f"rules file of {tenant_id} names another tenant")
            self._books[tenant_id] = book
        return self._books[tenant_id]


# ---- facts and conditions (module level so they can be tested alone) ----


def _to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def derive_facts(facts: Mapping[str, Any], as_of: date | None) -> dict[str, Any]:
    """Add days_since_delivery and days_late, computed from the backend dates and as_of.

    A derived value replaces one the backend sent (the dates are the source). Without as_of, or with an unreadable or
    impossible date, nothing is derived (a rule that needs it then fails closed as MISSING_CONTEXT).
    """
    out = dict(facts)
    if as_of is None:
        return out
    delivered_raw, expected_raw = facts.get("delivered_at"), facts.get("expected_delivery_date")
    delivered = _to_date(delivered_raw)
    delivered_ok = delivered is not None and delivered <= as_of
    if delivered_raw is not None:
        if delivered_ok and delivered is not None:
            out["days_since_delivery"] = (as_of - delivered).days
        else:
            out.pop("days_since_delivery", None)
    if expected_raw is not None:
        expected = _to_date(expected_raw)
        arrived = delivered if delivered_raw is not None else as_of
        if expected is not None and arrived is not None and (delivered_raw is None or delivered_ok):
            out["days_late"] = max(0, (arrived - expected).days)
        else:
            out.pop("days_late", None)
    return out


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _kind(value: Any) -> str:
    """bool, number, text or other: a fact of another kind than the rule's value is malformed, not just different."""
    if isinstance(value, bool):
        return "bool"
    if _is_number(value):
        return "number"
    return "text" if isinstance(value, str) else "other"


def _compare(actual: Any, op: str, expected: Any) -> bool:
    """Raises TypeError when the fact has the wrong type for the operator (the caller treats that as missing)."""
    if op in ("in", "not_in"):
        if not isinstance(expected, (list, tuple)) or _kind(actual) == "other":
            raise TypeError("in/not_in needs a scalar fact and a list")
        if _kind(actual) not in {_kind(e) for e in expected}:
            raise TypeError("the fact is not of the kind the list holds")
        found = actual in expected
        return found if op == "in" else not found
    if op in ("==", "!="):
        if _kind(actual) == "other" or _kind(actual) != _kind(expected):
            raise TypeError("== / != needs a fact of the same kind as the value")
        return (actual == expected) == (op == "==")
    if not _is_number(actual) or not _is_number(expected):
        raise TypeError(f"operator {op} needs numbers")
    return {"<=": actual <= expected, "<": actual < expected, ">=": actual >= expected, ">": actual > expected}[op]


def describe(conditions: tuple[_Condition, ...]) -> str:
    if not conditions:
        return "always"
    return " AND ".join(
        f"{c.field if c.source == 'facts' else 'arguments.' + c.field} {c.op} {c.value!r}" for c in conditions
    )


def evaluate(
    conditions: tuple[_Condition, ...], facts: Mapping[str, Any], arguments: Mapping[str, Any]
) -> tuple[bool | None, list[str]]:
    """(held, missing). held is None when any needed value is missing, null or of the wrong type."""
    missing: list[str] = []
    results: list[bool] = []
    for cond in conditions:
        source = facts if cond.source == "facts" else arguments
        name = cond.field if cond.source == "facts" else f"arguments.{cond.field}"
        actual = source.get(cond.field, _MISSING)
        if actual is _MISSING or actual is None:
            missing.append(name)
            continue
        try:
            results.append(_compare(actual, cond.op, cond.value))
        except TypeError:
            missing.append(name)
    return (None, missing) if missing else (all(results), [])
