"""The numbers behind the dashboard. Every function answers one question from team_b/docs/metrics.md.

All of them take (tenant_id, start, end, bucket, filters) and return one point per time bucket, in order, with empty
buckets included so a chart has no holes. They read only the summary rows (turn_facts, tool_call_facts, policy_facts)
and the cases, never message text. Times are UTC. A conversation counts in the bucket of its first turn in the window.
"""

import asyncio
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, TypeVar

from team_b.domain.base import FrozenModel
from team_b.domain.facts import Facts, FactsSummary, PolicyFact, ToolCallFact, TurnFact
from team_b.domain.handoff import HandoffCase
from team_b.ports import CaseStore, TraceStore

_T = TypeVar("_T")
Bucket = Literal["hour", "day", "week", "month"]
MAX_BUCKETS = 2000  # a window that would need more points is refused rather than computed
UNKNOWN = "unknown"
HANDOFF = "handoff"
READ = "read"
AI_METHODS = ("llm", "rules_fallback")  # the turns where an AI model was asked


@dataclass(frozen=True)
class MetricFilters:
    """Narrow every metric to one language, intent, decision or escalation reason (all optional)."""

    language: str | None = None
    intent: str | None = None
    decision: str | None = None
    escalation_reason: str | None = None

    @property
    def active(self) -> bool:
        return any(v is not None for v in (self.language, self.intent, self.decision, self.escalation_reason))

    def matches(self, turn: TurnFact) -> bool:
        return (
            (self.language is None or turn.language == self.language)
            and (self.intent is None or turn.intent == self.intent)
            and (self.decision is None or turn.decision == self.decision)
            and (self.escalation_reason is None or turn.escalation_reason == self.escalation_reason)
        )


# ---- the answers ----


class Point(FrozenModel):
    bucket_start: datetime


class CountPoint(Point):
    value: int


class RatePoint(Point):
    numerator: int
    denominator: int
    rate: float | None  # None when the denominator is 0


class KeyCountsPoint(Point):
    counts: dict[str, int]


class ReasonPoint(Point):
    conversations: int
    by_reason: dict[str, int]  # conversations whose first handoff had this reason
    rates: dict[str, float]  # by_reason / conversations


class DurationPoint(Point):
    count: int
    p50: float | None  # seconds
    p95: float | None


class ToolStat(FrozenModel):
    calls: int
    failures: int
    rate: float
    errors: dict[str, int]  # error code -> count


class ToolPoint(Point):
    tools: dict[str, ToolStat]


class SpeedStat(FrozenModel):
    count: int
    p50: float | None  # milliseconds
    p95: float | None


class LatencyPoint(Point):
    overall: SpeedStat
    stages: dict[str, SpeedStat]


# ---- time buckets ----


def bucket_start(moment: datetime, bucket: Bucket) -> datetime:
    """The first moment (UTC) of the bucket that contains `moment`: hour, day, week (Monday) or month."""
    m = moment.astimezone(UTC)
    if bucket == "hour":
        return m.replace(minute=0, second=0, microsecond=0)
    day = m.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket == "day":
        return day
    if bucket == "week":
        return day - timedelta(days=day.weekday())
    return day.replace(day=1)


def next_bucket(start: datetime, bucket: Bucket) -> datetime:
    if bucket == "hour":
        return start + timedelta(hours=1)
    if bucket == "day":
        return start + timedelta(days=1)
    if bucket == "week":
        return start + timedelta(days=7)
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)


def bucket_starts(start: datetime, end: datetime, bucket: Bucket) -> list[datetime]:
    """Every bucket start from the one holding `start` up to (not including) `end`."""
    if end <= start:
        raise ValueError("end must be after start")
    found, current = [], bucket_start(start, bucket)
    while current < end:
        found.append(current)
        if len(found) > MAX_BUCKETS:
            raise ValueError(f"more than {MAX_BUCKETS} buckets: use a bigger bucket or a shorter window")
        current = next_bucket(current, bucket)
    return found


def percentile(values: Sequence[float], p: float) -> float | None:
    """Nearest rank: the value at position ceil(p * n) of the sorted values (p between 0 and 1); None if no values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(1, math.ceil(p * len(ordered))) - 1]


def rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _speed(values: Sequence[float]) -> SpeedStat:
    return SpeedStat(count=len(values), p50=percentile(values, 0.5), p95=percentile(values, 0.95))


# ---- the service ----


@dataclass(frozen=True)
class Window:
    """The rows of one question, after the filters."""

    turns: tuple[TurnFact, ...]
    calls: tuple[ToolCallFact, ...]
    policy: tuple[PolicyFact, ...]
    conversations: dict[str, list[TurnFact]]  # conversation id -> its turns, oldest first


def _group(turns: Iterable[TurnFact]) -> dict[str, list[TurnFact]]:
    grouped: dict[str, list[TurnFact]] = defaultdict(list)
    for turn in sorted(turns, key=lambda t: t.created_at):
        grouped[turn.conversation_id].append(turn)
    return dict(grouped)


def _has_case(turns: list[TurnFact]) -> bool:
    return any(t.decision == HANDOFF for t in turns)


class MetricsService:
    def __init__(self, traces: TraceStore, cases: CaseStore) -> None:
        self._traces = traces
        self._cases = cases

    async def _window(self, tenant_id: str, start: datetime, end: datetime, filters: MetricFilters | None) -> Window:
        facts: Facts = await self._traces.facts(tenant_id, start, end)
        chosen = filters or MetricFilters()
        turns = tuple(t for t in facts.turns if chosen.matches(t))
        calls, policy = facts.tool_calls, facts.policy
        if chosen.active:  # only what happened in the turns that passed the filters
            keep = {t.trace_id for t in turns}
            calls = tuple(c for c in calls if c.trace_id in keep)
            policy = tuple(p for p in policy if p.trace_id in keep)
        return Window(turns, calls, policy, _group(turns))

    @staticmethod
    def _by_bucket(
        items: Iterable[_T], when: Callable[[_T], datetime], starts: list[datetime], bucket: Bucket
    ) -> dict[datetime, list[_T]]:
        out: dict[datetime, list[_T]] = {s: [] for s in starts}
        for item in items:
            key = bucket_start(when(item), bucket)
            if key in out:
                out[key].append(item)
        return out

    @staticmethod
    def _conversations_by_bucket(
        window: Window, starts: list[datetime], bucket: Bucket
    ) -> dict[datetime, list[list[TurnFact]]]:
        out: dict[datetime, list[list[TurnFact]]] = {s: [] for s in starts}
        for turns in window.conversations.values():
            key = bucket_start(turns[0].created_at, bucket)
            if key in out:
                out[key].append(turns)
        return out

    # 1. conversations
    async def conversations(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        bucket: Bucket = "day",
        filters: MetricFilters | None = None,
    ) -> list[CountPoint]:
        starts = bucket_starts(start, end, bucket)
        grouped = self._conversations_by_bucket(await self._window(tenant_id, start, end, filters), starts, bucket)
        return [CountPoint(bucket_start=s, value=len(grouped[s])) for s in starts]

    # 2. automation rate: conversations that ended without a case / conversations
    async def automation_rate(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        bucket: Bucket = "day",
        filters: MetricFilters | None = None,
    ) -> list[RatePoint]:
        starts = bucket_starts(start, end, bucket)
        grouped = self._conversations_by_bucket(await self._window(tenant_id, start, end, filters), starts, bucket)
        points = []
        for s in starts:
            total = len(grouped[s])
            automated = sum(1 for turns in grouped[s] if not _has_case(turns))
            points.append(
                RatePoint(bucket_start=s, numerator=automated, denominator=total, rate=rate(automated, total))
            )
        return points

    # 3. resolved with an action: no case, and a write succeeded in the conversation / conversations
    async def resolved_with_action(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        bucket: Bucket = "day",
        filters: MetricFilters | None = None,
    ) -> list[RatePoint]:
        starts = bucket_starts(start, end, bucket)
        window = await self._window(tenant_id, start, end, filters)
        wrote = {c.trace_id for c in window.calls if c.operation_kind != READ and c.status == "success"}
        grouped = self._conversations_by_bucket(window, starts, bucket)
        points = []
        for s in starts:
            done = sum(1 for turns in grouped[s] if not _has_case(turns) and any(t.trace_id in wrote for t in turns))
            points.append(
                RatePoint(bucket_start=s, numerator=done, denominator=len(grouped[s]), rate=rate(done, len(grouped[s])))
            )
        return points

    # 4. escalation rate by reason: the reason of a conversation's first handoff
    async def escalation_by_reason(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        bucket: Bucket = "day",
        filters: MetricFilters | None = None,
    ) -> list[ReasonPoint]:
        starts = bucket_starts(start, end, bucket)
        grouped = self._conversations_by_bucket(await self._window(tenant_id, start, end, filters), starts, bucket)
        points = []
        for s in starts:
            total = len(grouped[s])
            reasons = Counter(
                next(t.escalation_reason or UNKNOWN for t in turns if t.decision == HANDOFF)
                for turns in grouped[s]
                if _has_case(turns)
            )
            points.append(
                ReasonPoint(
                    bucket_start=s,
                    conversations=total,
                    by_reason=dict(reasons),
                    rates={r: n / total for r, n in reasons.items()},
                )  # fmt: skip
            )
        return points

    # 5 and 6. case times, from the case events (a case counts in the bucket where it was opened)
    async def _cases_in(
        self, tenant_id: str, start: datetime, end: datetime, filters: MetricFilters | None
    ) -> list[HandoffCase]:
        chosen = filters or MetricFilters()
        return [
            c
            for c in await self._cases.list(tenant_id)
            if start <= c.created_at < end
            and (chosen.escalation_reason is None or c.package.reason.value == chosen.escalation_reason)
            and (chosen.language is None or (c.package.language and c.package.language.value == chosen.language))
            and (chosen.intent is None or chosen.intent in c.package.intents)
        ]

    async def _case_durations(
        self, tenant_id: str, start: datetime, end: datetime, bucket: Bucket, filters: MetricFilters | None, kind: str
    ) -> list[DurationPoint]:
        starts = bucket_starts(start, end, bucket)
        cases = await self._cases_in(tenant_id, start, end, filters)
        grouped = self._by_bucket(cases, lambda c: c.created_at, starts, bucket)
        points = []
        for s in starts:
            seconds = []
            for case in grouped[s]:
                event = next((e for e in case.events if e.kind == kind), None)
                if event is not None:
                    seconds.append((event.at - case.created_at).total_seconds())
            points.append(
                DurationPoint(
                    bucket_start=s, count=len(seconds), p50=percentile(seconds, 0.5), p95=percentile(seconds, 0.95)
                )
            )
        return points

    async def time_to_first_human_reply(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        bucket: Bucket = "day",
        filters: MetricFilters | None = None,
    ) -> list[DurationPoint]:
        return await self._case_durations(tenant_id, start, end, bucket, filters, "reply")

    async def case_resolution_time(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        bucket: Bucket = "day",
        filters: MetricFilters | None = None,
    ) -> list[DurationPoint]:
        return await self._case_durations(tenant_id, start, end, bucket, filters, "resolved")

    # 7. action failure rate by tool and error code (calls that are not reads)
    async def action_failures(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        bucket: Bucket = "day",
        filters: MetricFilters | None = None,
    ) -> list[ToolPoint]:
        starts = bucket_starts(start, end, bucket)
        window = await self._window(tenant_id, start, end, filters)
        grouped = self._by_bucket(
            (c for c in window.calls if c.operation_kind != READ), lambda c: c.created_at, starts, bucket
        )
        points = []
        for s in starts:
            tools: dict[str, ToolStat] = {}
            for tool in sorted({c.tool for c in grouped[s]}):
                made = [c for c in grouped[s] if c.tool == tool]
                failed = [c for c in made if c.status == "error"]
                errors = Counter(c.error_code or UNKNOWN for c in failed)
                tools[tool] = ToolStat(
                    calls=len(made), failures=len(failed), rate=len(failed) / len(made), errors=dict(errors)
                )
            points.append(ToolPoint(bucket_start=s, tools=tools))
        return points

    # 8. unverified results: turns that ended in a handoff because a write could not be confirmed
    async def unverified_results(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        bucket: Bucket = "day",
        filters: MetricFilters | None = None,
    ) -> list[CountPoint]:
        starts = bucket_starts(start, end, bucket)
        window = await self._window(tenant_id, start, end, filters)
        grouped = self._by_bucket(
            (t for t in window.turns if t.escalation_reason == "unverified_result"),
            lambda t: t.created_at,
            starts,
            bucket,
        )
        return [CountPoint(bucket_start=s, value=len(grouped[s])) for s in starts]

    # 9. unanswered rate: policy searches that found nothing / policy searches
    async def unanswered_rate(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        bucket: Bucket = "day",
        filters: MetricFilters | None = None,
    ) -> list[RatePoint]:
        starts = bucket_starts(start, end, bucket)
        window = await self._window(tenant_id, start, end, filters)
        searched = (t for t in window.turns if t.evidence_empty or t.evidence_count > 0)
        grouped = self._by_bucket(searched, lambda t: t.created_at, starts, bucket)
        points = []
        for s in starts:
            empty = sum(1 for t in grouped[s] if t.evidence_empty)
            points.append(
                RatePoint(
                    bucket_start=s, numerator=empty, denominator=len(grouped[s]), rate=rate(empty, len(grouped[s]))
                )
            )
        return points

    # 10. policy outcomes by rule: "<action>:<reason_code>" -> {decision: count}
    async def policy_outcomes(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        bucket: Bucket = "day",
        filters: MetricFilters | None = None,
    ) -> list[KeyCountsPoint]:
        starts = bucket_starts(start, end, bucket)
        window = await self._window(tenant_id, start, end, filters)
        grouped = self._by_bucket(window.policy, lambda p: p.created_at, starts, bucket)
        return [
            KeyCountsPoint(
                bucket_start=s, counts=dict(Counter(f"{p.action}:{p.reason_code}:{p.decision}" for p in grouped[s]))
            )
            for s in starts
        ]

    # 11. dependency errors by service
    async def dependency_errors(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        bucket: Bucket = "day",
        filters: MetricFilters | None = None,
    ) -> list[KeyCountsPoint]:
        starts = bucket_starts(start, end, bucket)
        window = await self._window(tenant_id, start, end, filters)
        grouped = self._by_bucket(window.turns, lambda t: t.created_at, starts, bucket)
        return [
            KeyCountsPoint(bucket_start=s, counts=dict(Counter(svc for t in grouped[s] for svc in t.dependency_errors)))
            for s in starts
        ]

    # 12. latency p50 / p95, overall and per stage (milliseconds)
    async def latency(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        bucket: Bucket = "day",
        filters: MetricFilters | None = None,
    ) -> list[LatencyPoint]:
        starts = bucket_starts(start, end, bucket)
        window = await self._window(tenant_id, start, end, filters)
        grouped = self._by_bucket(window.turns, lambda t: t.created_at, starts, bucket)
        points = []
        for s in starts:
            per_stage: dict[str, list[float]] = defaultdict(list)
            for turn in grouped[s]:
                for stage, ms in turn.stage_ms.items():
                    per_stage[stage].append(ms)
            points.append(
                LatencyPoint(
                    bucket_start=s,
                    overall=_speed([t.latency_ms for t in grouped[s]]),
                    stages={stage: _speed(v) for stage, v in sorted(per_stage.items())},
                )  # fmt: skip
            )
        return points

    # 13. language mix: turns per language
    async def language_mix(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        bucket: Bucket = "day",
        filters: MetricFilters | None = None,
    ) -> list[KeyCountsPoint]:
        starts = bucket_starts(start, end, bucket)
        window = await self._window(tenant_id, start, end, filters)
        grouped = self._by_bucket(window.turns, lambda t: t.created_at, starts, bucket)
        return [
            KeyCountsPoint(bucket_start=s, counts=dict(Counter(t.language or UNKNOWN for t in grouped[s])))
            for s in starts
        ]

    # 14. AI fallback rate: AI-assisted readings that fell back to the rules / readings where the AI was asked
    async def ai_fallback_rate(
        self,
        tenant_id: str,
        start: datetime,
        end: datetime,
        bucket: Bucket = "day",
        filters: MetricFilters | None = None,
    ) -> list[RatePoint]:
        starts = bucket_starts(start, end, bucket)
        window = await self._window(tenant_id, start, end, filters)
        asked = (t for t in window.turns if t.nlu_method in AI_METHODS)
        grouped = self._by_bucket(asked, lambda t: t.created_at, starts, bucket)
        points = []
        for s in starts:
            fell = sum(1 for t in grouped[s] if t.nlu_method == "rules_fallback")
            points.append(
                RatePoint(bucket_start=s, numerator=fell, denominator=len(grouped[s]), rate=rate(fell, len(grouped[s])))
            )
        return points


# ---- the overview: current period and the one before it ----


def previous_period(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """The window of the same length that ends where this one starts."""
    return start - (end - start), start


async def overview(
    traces: TraceStore, tenant_id: str, start: datetime, end: datetime
) -> tuple[FactsSummary, FactsSummary]:
    """Headline counts of [start, end) and of the previous period, from the store's fast summary."""
    before_start, before_end = previous_period(start, end)
    current, before = await asyncio.gather(
        traces.summary(tenant_id, start, end), traces.summary(tenant_id, before_start, before_end)
    )
    return current, before
