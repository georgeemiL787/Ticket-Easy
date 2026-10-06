"""The alert engine: warns managers about problems before customers complain.

OWNER: Track A.

Every minute (a task of the API) the engine looks at what happened recently and compares it with the thresholds of each
business (config/tenants/<tenant>.json, "alerts"; every number has a default). A rule that is true opens ONE alert (a
second one for the same rule and key is never opened); when the rule is no longer true the alert is resolved. Nothing
else changes an alert except a person acknowledging it. Time comes from the injected Clock.

The rules (each reads only counts, never message text; "key" tells apart the alerts of one rule):
  service_down       >= N failures of one service (policy search, rule checker, safety screen, shop) in a few minutes
  action_failing     more than X% of the changes sent to the shop failed or came back unclear, over enough calls
  unverified_result  any change whose outcome could not be proven
  action_missing     any request that needed a tool the shop does not publish or the business does not allow
  knowledge_gap      too many questions unanswered in a day, or one unanswered question asked again and again
  slow_replies       the 95th percentile of reply time is too high
  escalation_spike   the share of conversations handed to a person is far above the 7-day share
  ai_fallback        too many AI turns fell back to the rules
  queue_backlog      an urgent case nobody has taken for too long

It reads through the ports: TraceStore.facts() (the text-free rows of every turn, tool call and rule answer),
TraceStore.query() and CaseStore.list(). A failing evaluation is logged and tried again next minute; it never crashes
the service. Optional TEAM_B_ALERT_WEBHOOK receives each opened and resolved alert as JSON.
"""

import asyncio
import math
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

import httpx

from team_b.brain.gaps import group_questions
from team_b.domain.alerts import Alert, AlertSeverity
from team_b.domain.decision import EscalationReason
from team_b.domain.facts import Facts, TurnFact
from team_b.domain.handoff import CaseStatus
from team_b.domain.tenant import AlertThresholds, TenantRegistry
from team_b.observability import get_logger
from team_b.ports import AlertStore, AlreadyExistsError, CaseStore, Clock, TraceStore

log = get_logger(__name__)
UNAVAILABLE_CODES = frozenset({"BACKEND_UNAVAILABLE", "TIMEOUT"})
SERVICES = ("policy_search", "safety_screen", "rule_checker", "shop_tools")
AI_METHODS = frozenset({"llm", "rules_fallback"})
BASELINE_DAYS = 7
RULES = (
    "service_down", "action_failing", "unverified_result", "action_missing", "knowledge_gap",
    "slow_replies", "escalation_spike", "ai_fallback", "queue_backlog",
)  # fmt: skip


@dataclass(frozen=True)
class Condition:
    """A rule that is true right now."""

    rule: str
    severity: AlertSeverity
    key: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    opened: list[Alert] = field(default_factory=list)
    resolved: list[Alert] = field(default_factory=list)


class Notifier(Protocol):
    async def notify(self, event: str, alert: Alert) -> None: ...


class WebhookNotifier:
    """POST every opened/resolved alert to a URL. A failing webhook is logged, never raised."""

    def __init__(self, url: str, timeout_s: float = 5.0) -> None:
        self._url, self._timeout = url, timeout_s

    async def notify(self, event: str, alert: Alert) -> None:
        body = {"event": event, "alert": alert.model_dump(mode="json")}  # counts and ids only, no message text
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                response = await http.post(self._url, json=body)
                response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - an alert must never be lost because the webhook is down
            log.warning("alert_webhook_failed", alert_id=alert.alert_id, error=type(exc).__name__)


def _within(rows: Sequence[Any], now: datetime, minutes: int) -> list[Any]:
    start = now - timedelta(minutes=minutes)
    return [r for r in rows if start <= r.created_at <= now]


def _p95(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[max(1, math.ceil(0.95 * len(ordered))) - 1]


# ---- the rules: each returns the conditions that are true now ----


def service_down(t: AlertThresholds, now: datetime, facts: Facts) -> list[Condition]:
    counts: Counter[str] = Counter()
    for turn in _within(facts.turns, now, t.service_down_window_min):
        counts.update(s for s in turn.dependency_errors if s in SERVICES)
    for call in _within(facts.tool_calls, now, t.service_down_window_min):
        if call.error_code in UNAVAILABLE_CODES:
            counts["shop"] += 1
    return [
        Condition("service_down", "critical", service, {"key": service, "service": service, "failures": n,
                                                        "window_min": t.service_down_window_min})
        for service, n in sorted(counts.items())
        if n >= t.service_down_errors
    ]  # fmt: skip


def action_failing(t: AlertThresholds, now: datetime, facts: Facts) -> list[Condition]:
    calls = [c for c in _within(facts.tool_calls, now, t.action_failing_window_min) if c.operation_kind != "read"]
    failed = sum(1 for c in calls if c.status != "success")
    if len(calls) >= t.action_failing_min_calls and failed / len(calls) > t.action_failing_rate:
        details = {"calls": len(calls), "failed": failed, "rate": round(failed / len(calls), 3),
                   "window_min": t.action_failing_window_min}  # fmt: skip
        return [Condition("action_failing", "critical", details=details)]
    return []


def _turns_with(facts: Facts, now: datetime, minutes: int, reason: EscalationReason) -> list[TurnFact]:
    return [t for t in _within(facts.turns, now, minutes) if t.escalation_reason == reason.value]


def unverified_result(t: AlertThresholds, now: datetime, facts: Facts) -> list[Condition]:
    found = _turns_with(facts, now, t.unverified_result_window_min, EscalationReason.UNVERIFIED_RESULT)
    return [Condition("unverified_result", "critical", details={"count": len(found)})] if found else []


def action_missing(t: AlertThresholds, now: datetime, facts: Facts) -> list[Condition]:
    found = _turns_with(facts, now, t.action_missing_window_min, EscalationReason.CAPABILITY_MISSING)
    return [Condition("action_missing", "warning", details={"count": len(found)})] if found else []


def knowledge_gap_rate(t: AlertThresholds, now: datetime, facts: Facts) -> list[Condition]:
    turns = _within(facts.turns, now, t.knowledge_gap_window_min)
    asked = [x for x in turns if x.evidence_count > 0 or x.evidence_empty or x.escalation_reason == "no_evidence"]
    unanswered = [x for x in asked if x.evidence_empty or x.escalation_reason == "no_evidence"]
    if len(asked) >= t.knowledge_gap_min_questions and len(unanswered) / len(asked) > t.knowledge_gap_rate:
        details = {"key": "rate", "questions": len(asked), "unanswered": len(unanswered),
                   "rate": round(len(unanswered) / len(asked), 3)}  # fmt: skip
        return [Condition("knowledge_gap", "warning", "rate", details)]
    return []


def slow_replies(t: AlertThresholds, now: datetime, facts: Facts) -> list[Condition]:
    turns = _within(facts.turns, now, t.slow_window_min)
    if len(turns) >= t.slow_min_turns and (p95 := _p95([x.latency_ms for x in turns])) > t.slow_p95_ms:
        return [Condition("slow_replies", "warning", details={"p95_ms": round(p95), "turns": len(turns),
                                                              "window_min": t.slow_window_min})]  # fmt: skip
    return []


def escalation_spike(t: AlertThresholds, now: datetime, facts: Facts) -> list[Condition]:
    recent = _within(facts.turns, now, 60)
    baseline = _within(facts.turns, now, BASELINE_DAYS * 24 * 60)
    if len(recent) < t.escalation_spike_min_turns or not baseline:
        return []
    now_rate = sum(1 for x in recent if x.decision == "handoff") / len(recent)
    base_rate = sum(1 for x in baseline if x.decision == "handoff") / len(baseline)
    if now_rate > 0 and now_rate > t.escalation_spike_factor * base_rate:
        details = {"hour_rate": round(now_rate, 3), "week_rate": round(base_rate, 3), "turns": len(recent)}
        return [Condition("escalation_spike", "warning", details=details)]
    return []


def ai_fallback(t: AlertThresholds, now: datetime, facts: Facts) -> list[Condition]:
    ai = [x for x in _within(facts.turns, now, t.ai_fallback_window_min) if x.nlu_method in AI_METHODS]
    fell = sum(1 for x in ai if x.nlu_method == "rules_fallback")
    if len(ai) >= t.ai_fallback_min_turns and fell / len(ai) > t.ai_fallback_rate:
        return [Condition("ai_fallback", "info", details={"ai_turns": len(ai), "fell_back": fell,
                                                          "rate": round(fell / len(ai), 3)})]  # fmt: skip
    return []


FACT_RULES: tuple[Callable[[AlertThresholds, datetime, Facts], list[Condition]], ...] = (
    service_down, action_failing, unverified_result, action_missing, knowledge_gap_rate, slow_replies,
    escalation_spike, ai_fallback,
)  # fmt: skip


class AlertEngine:
    def __init__(
        self,
        *,
        traces: TraceStore,
        cases: CaseStore,
        alerts: AlertStore,
        clock: Clock,
        tenants: TenantRegistry,
        notifier: Notifier | None = None,
    ) -> None:
        self._traces, self._cases, self._alerts = traces, cases, alerts
        self._clock, self._tenants, self._notifier = clock, tenants, notifier

    async def evaluate_all(self) -> dict[str, Report]:
        return {tenant.tenant_id: await self.evaluate(tenant.tenant_id) for tenant in self._tenants}

    async def evaluate(self, tenant_id: str) -> Report:
        """Open the alerts whose condition started and resolve those whose condition cleared."""
        thresholds = self._tenants.get(tenant_id).alerts
        report = Report()
        if not thresholds.enabled:
            return report
        now = self._clock.now()
        conditions = await self._conditions(tenant_id, thresholds, now)
        wanted = {(c.rule, c.key): c for c in conditions}
        open_alerts = list(await self._alerts.list(tenant_id, open_only=True, limit=1000))
        still = {(a.rule, str(a.details.get("key", ""))) for a in open_alerts}
        for (rule, key), condition in wanted.items():
            if (rule, key) in still:
                continue
            alert = Alert(
                alert_id=uuid.uuid4().hex, tenant_id=tenant_id, rule=rule, severity=condition.severity,
                opened_at=now, details=condition.details,
            )  # fmt: skip
            try:
                await self._alerts.add(alert)
            except AlreadyExistsError:
                continue  # another evaluation opened it first
            report.opened.append(alert)
            await self._tell("opened", alert)
        for alert in open_alerts:
            if (alert.rule, str(alert.details.get("key", ""))) not in wanted:
                alert.resolved_at = now
                await self._alerts.save(alert)
                report.resolved.append(alert)
                await self._tell("resolved", alert)
        return report

    async def _conditions(self, tenant_id: str, t: AlertThresholds, now: datetime) -> list[Condition]:
        horizon = max(BASELINE_DAYS * 24 * 60, t.knowledge_gap_window_min)
        facts = await self._traces.facts(tenant_id, now - timedelta(minutes=horizon), now + timedelta(seconds=1))
        found: list[Condition] = []
        for rule in FACT_RULES:
            found += rule(t, now, facts)
        found += await self._same_question(tenant_id, t, now, facts)
        found += await self._backlog(tenant_id, t, now)
        return found

    async def _same_question(self, tenant_id: str, t: AlertThresholds, now: datetime, facts: Facts) -> list[Condition]:
        """The same unanswered question asked again and again (grouped by the words in it)."""
        recent = {x.trace_id for x in _within(facts.turns, now, t.knowledge_gap_window_min)}
        traces = await self._traces.query(tenant_id, escalation_reason=EscalationReason.NO_EVIDENCE, limit=500)
        questions = [(tr.customer_message, now) for tr in traces if tr.trace_id in recent and tr.customer_message]
        groups = group_questions(questions)
        biggest = max((g.count for g in groups), default=0)
        if biggest >= t.knowledge_gap_same_question:
            return [Condition("knowledge_gap", "warning", "group", {"key": "group", "same_question": biggest})]
        return []

    async def _backlog(self, tenant_id: str, t: AlertThresholds, now: datetime) -> list[Condition]:
        limit = now - timedelta(minutes=t.backlog_sla_min)
        late = [
            c for c in await self._cases.list(tenant_id, status=CaseStatus.OPEN)
            if c.package.priority == "urgent" and c.created_at <= limit
        ]  # fmt: skip
        if not late:
            return []
        oldest = round((now - min(c.created_at for c in late)).total_seconds() / 60)
        return [Condition("queue_backlog", "critical", details={"cases": len(late), "oldest_min": oldest,
                                                                 "sla_min": t.backlog_sla_min})]  # fmt: skip

    async def _tell(self, event: str, alert: Alert) -> None:
        log.info("alert_" + event, alert_id=alert.alert_id, tenant_id=alert.tenant_id, rule=alert.rule,
                 severity=alert.severity)  # fmt: skip
        if self._notifier is not None:
            await self._notifier.notify(event, alert)


async def alert_loop(engine: AlertEngine, *, interval_s: float = 60.0) -> None:
    """Evaluate now, then every interval, until cancelled. A failed evaluation is logged and tried again."""
    while True:
        try:
            await engine.evaluate_all()
        except Exception:
            log.exception("alert_evaluation_failed")
        await asyncio.sleep(interval_s)
