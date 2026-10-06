"""The manager dashboard API: everything the dashboard shows, scoped to one business.

All endpoints take tenant_id and a window (from, to; default the last 7 days up to now). Numbers come from the summary
rows (see docs/metrics.md); the conversation list pages with a cursor. The response models are exported as JSON Schemas
(contracts/schemas/Dashboard*.schema.json) so the dashboard app can generate its TypeScript types from them.
"""

import base64
import binascii
import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from team_b.api.chat import CONVERSATION, TENANT, checked_tenant
from team_b.api.errors import invalid_request, not_found
from team_b.brain import metrics as m
from team_b.brain.gaps import QuestionGroup, group_questions
from team_b.brain.transcript import transcript_from_traces
from team_b.container import Container
from team_b.domain.facts import FactsSummary, TurnFact
from team_b.domain.handoff import CaseStatus, HandoffCase, TranscriptLine
from team_b.domain.trace import DecisionTrace

DEFAULT_DAYS = 7
DEFAULT_LIMIT, MAX_LIMIT = 50, 200
MAX_TEXT_SEARCH = 300  # conversations whose text is searched for ?q=
MAX_GAP_TRACES = 500
SPEED_DECIMALS = 1

router = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])


# ---- response models (exported as Dashboard*.schema.json) ----


class DashboardKpi(BaseModel):
    value: float | None
    previous: float | None
    change: float | None  # value - previous, when both exist


class DashboardOverview(BaseModel):
    tenant_id: str
    period_start: datetime
    period_end: datetime
    previous_start: datetime
    previous_end: datetime
    conversations: DashboardKpi
    automation_rate: DashboardKpi
    escalation_rate: DashboardKpi
    unverified_results: DashboardKpi
    p95_latency_ms: DashboardKpi
    open_cases: int  # cases waiting for a person right now (not a period figure)


class DashboardTimeseries(BaseModel):
    tenant_id: str
    metric: str
    bucket: m.Bucket
    period_start: datetime
    period_end: datetime
    points: list[dict[str, Any]]  # one per bucket; the fields depend on the metric (see docs/metrics.md)


class DashboardConversationRow(BaseModel):
    conversation_id: str
    first_at: datetime
    last_at: datetime
    turns: int
    language: str | None
    intents: list[str]
    outcome: str  # the decision of the last turn
    escalation_reason: str | None  # the reason of the first handoff
    has_case: bool
    case_id: str | None
    case_status: CaseStatus | None
    actions: int  # write tool calls
    avg_latency_ms: float
    problems: list[str]  # why a manager may want to look: handoff reasons, failed tools, dependency errors, no answer


class DashboardConversationPage(BaseModel):
    items: list[DashboardConversationRow]
    next_cursor: str | None = None


class DashboardConversation(BaseModel):
    tenant_id: str
    conversation_id: str
    transcript: list[TranscriptLine]
    traces: list[DecisionTrace]
    case: HandoffCase | None


class DashboardRuleCount(BaseModel):
    rule: str  # "<action>:<reason_code>"
    decision: str
    count: int


class DashboardEscalations(BaseModel):
    tenant_id: str
    period_start: datetime
    period_end: datetime
    conversations: int
    by_reason_total: dict[str, int]
    by_reason: list[m.ReasonPoint]
    open_cases: int
    claimed_cases: int
    top_rules: list[DashboardRuleCount]  # the rules behind denied or human-approval answers, most frequent first


class DashboardToolRow(BaseModel):
    tool: str
    calls: int
    failures: int
    rate: float
    errors: dict[str, int]
    p50_ms: float | None
    p95_ms: float | None


class DashboardTools(BaseModel):
    tenant_id: str
    period_start: datetime
    period_end: datetime
    tools: list[DashboardToolRow]


class DashboardKnowledgeGaps(BaseModel):
    tenant_id: str
    period_start: datetime
    period_end: datetime
    questions_without_answer: int
    groups: list[QuestionGroup]


# ---- helpers ----


def built(request: Request, tenant_id: str) -> Container:
    return checked_tenant(request, tenant_id)


def window(built_: Container, start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    """The requested window, or the last DEFAULT_DAYS days up to now."""
    stop = end or built_.clock.now()
    begin = start or stop - timedelta(days=DEFAULT_DAYS)
    if begin.tzinfo is None or stop.tzinfo is None:
        raise invalid_request("from and to must include a time zone, for example 2026-09-28T00:00:00Z")
    if stop <= begin:
        raise invalid_request("to must be after from")
    return begin, stop


FROM = Annotated[datetime | None, Query(alias="from", description="start of the window (ISO time with zone)")]
TO = Annotated[datetime | None, Query(description="end of the window (exclusive)")]


def kpi(value: float | None, previous: float | None) -> DashboardKpi:
    change = value - previous if value is not None and previous is not None else None
    return DashboardKpi(value=value, previous=previous, change=change)


def ratio(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def service(built_: Container) -> m.MetricsService:
    return m.MetricsService(built_.traces, built_.cases)


async def count_cases(built_: Container, tenant_id: str, status: CaseStatus) -> int:
    return len(await built_.cases.list(tenant_id, status=status))


# ---- overview ----


@router.get("/overview", response_model=DashboardOverview)
async def get_overview(request: Request, tenant_id: TENANT, from_: FROM = None, to: TO = None) -> DashboardOverview:
    """The headline numbers of the window next to the same numbers for the period before it."""
    b = built(request, tenant_id)
    start, end = window(b, from_, to)
    now, before = await m.overview(b.traces, tenant_id, start, end)
    previous_start, previous_end = m.previous_period(start, end)

    def automation(s: FactsSummary) -> float | None:
        return ratio(s.conversations - s.conversations_with_case, s.conversations)

    return DashboardOverview(
        tenant_id=tenant_id,
        period_start=start,
        period_end=end,
        previous_start=previous_start,
        previous_end=previous_end,
        conversations=kpi(now.conversations, before.conversations),
        automation_rate=kpi(automation(now), automation(before)),
        escalation_rate=kpi(
            ratio(now.conversations_with_case, now.conversations),
            ratio(before.conversations_with_case, before.conversations),
        ),
        unverified_results=kpi(now.unverified_results, before.unverified_results),
        p95_latency_ms=kpi(now.p95_latency_ms, before.p95_latency_ms),
        open_cases=await count_cases(b, tenant_id, CaseStatus.OPEN),
    )


# ---- time series ----

METRICS = {
    "conversations": "conversations",
    "automation_rate": "automation_rate",
    "resolved_with_action": "resolved_with_action",
    "escalations": "escalation_by_reason",
    "first_reply_time": "time_to_first_human_reply",
    "resolution_time": "case_resolution_time",
    "action_failures": "action_failures",
    "unverified_results": "unverified_results",
    "unanswered_rate": "unanswered_rate",
    "policy_outcomes": "policy_outcomes",
    "dependency_errors": "dependency_errors",
    "latency": "latency",
    "language_mix": "language_mix",
    "ai_fallback_rate": "ai_fallback_rate",
}


@router.get("/timeseries", response_model=DashboardTimeseries)
async def get_timeseries(
    request: Request,
    tenant_id: TENANT,
    metric: Annotated[str, Query(description=f"one of: {', '.join(METRICS)}")],
    bucket: m.Bucket = "day",
    from_: FROM = None,
    to: TO = None,
    language: Annotated[str | None, Query(max_length=16)] = None,
    intent: Annotated[str | None, Query(max_length=64)] = None,
    decision: Annotated[str | None, Query(max_length=32)] = None,
    reason: Annotated[str | None, Query(max_length=64)] = None,
) -> DashboardTimeseries:
    """One metric over time, one point per bucket (empty buckets included)."""
    if metric not in METRICS:
        raise invalid_request(f"metric must be one of {', '.join(METRICS)}")
    b = built(request, tenant_id)
    start, end = window(b, from_, to)
    filters = m.MetricFilters(language=language, intent=intent, decision=decision, escalation_reason=reason)
    try:
        points = await getattr(service(b), METRICS[metric])(tenant_id, start, end, bucket, filters)
    except ValueError as exc:
        raise invalid_request(str(exc)) from None
    return DashboardTimeseries(
        tenant_id=tenant_id,
        metric=metric,
        bucket=bucket,
        period_start=start,
        period_end=end,
        points=[p.model_dump(mode="json") for p in points],
    )


# ---- conversations ----

Status = Annotated[str | None, Query(pattern="^(automated|escalated)$", description="automated = no case")]


def encode_cursor(row: DashboardConversationRow) -> str:
    return base64.urlsafe_b64encode(json.dumps([row.last_at.isoformat(), row.conversation_id]).encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        last_at, conversation_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(last_at), str(conversation_id)
    except (ValueError, TypeError, binascii.Error):
        raise invalid_request("cursor is not valid") from None


def problems_of(turns: list[TurnFact]) -> list[str]:
    found: list[str] = []
    for t in turns:
        if t.decision == "handoff" and t.escalation_reason and f"handoff: {t.escalation_reason}" not in found:
            found.append(f"handoff: {t.escalation_reason}")
    if any(t.tool_errors for t in turns):
        found.append("a tool failed")
    if any(t.dependency_errors for t in turns):
        found.append("a system was unavailable")
    if any(t.evidence_empty for t in turns):
        found.append("no policy answer found")
    return found


async def conversation_rows(
    b: Container, tenant_id: str, start: datetime, end: datetime
) -> list[DashboardConversationRow]:
    facts = await b.traces.facts(tenant_id, start, end)
    turns_by_conversation: dict[str, list[TurnFact]] = defaultdict(list)
    for turn in facts.turns:
        turns_by_conversation[turn.conversation_id].append(turn)
    conversation_of = {t.trace_id: t.conversation_id for t in facts.turns}
    writes: dict[str, int] = defaultdict(int)
    for call in facts.tool_calls:
        if call.operation_kind != m.READ and call.trace_id in conversation_of:
            writes[conversation_of[call.trace_id]] += 1
    cases = {c.conversation_id: c for c in await b.cases.list(tenant_id)}  # the latest case of each conversation
    rows = []
    for conversation_id, turns in turns_by_conversation.items():
        turns.sort(key=lambda t: t.created_at)
        handoffs = [t for t in turns if t.decision == m.HANDOFF]
        case = cases.get(conversation_id)
        rows.append(
            DashboardConversationRow(
                conversation_id=conversation_id,
                first_at=turns[0].created_at,
                last_at=turns[-1].created_at,
                turns=len(turns),
                language=next((t.language for t in reversed(turns) if t.language), None),
                intents=list(dict.fromkeys(t.intent for t in turns if t.intent)),
                outcome=turns[-1].decision,
                escalation_reason=handoffs[0].escalation_reason if handoffs else None,
                has_case=bool(handoffs),
                case_id=case.case_id if case else None,
                case_status=case.status if case else None,
                actions=writes.get(conversation_id, 0),
                avg_latency_ms=round(sum(t.latency_ms for t in turns) / len(turns), SPEED_DECIMALS),
                problems=problems_of(turns),
            )
        )
    rows.sort(key=lambda r: (r.last_at, r.conversation_id), reverse=True)
    return rows


async def text_matches(b: Container, tenant_id: str, conversation_id: str, needle: str) -> bool:
    if needle in conversation_id.casefold():
        return True
    return any(
        needle in t.customer_message.casefold() or needle in t.response_text.casefold()
        for t in await b.traces.for_conversation(tenant_id, conversation_id)
    )


@router.get("/conversations", response_model=DashboardConversationPage)
async def list_conversations(
    request: Request,
    tenant_id: TENANT,
    from_: FROM = None,
    to: TO = None,
    status: Status = None,
    reason: Annotated[str | None, Query(max_length=64)] = None,
    language: Annotated[str | None, Query(max_length=16)] = None,
    q: Annotated[str | None, Query(max_length=100, description="text in the conversation or its id")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(max_length=300)] = None,
) -> DashboardConversationPage:
    """Conversations of the window, newest first. Pass next_cursor to get the next page."""
    b = built(request, tenant_id)
    start, end = window(b, from_, to)
    rows = [
        r
        for r in await conversation_rows(b, tenant_id, start, end)
        if (status is None or r.has_case == (status == "escalated"))
        and (reason is None or r.escalation_reason == reason)
        and (language is None or r.language == language)
    ]
    if cursor is not None:
        after_at, after_id = decode_cursor(cursor)
        rows = [r for r in rows if (r.last_at, r.conversation_id) < (after_at, after_id)]
    if q:
        needle = q.casefold()
        kept: list[DashboardConversationRow] = []
        for r in rows[: MAX_TEXT_SEARCH + limit * 5]:
            if await text_matches(b, tenant_id, r.conversation_id, needle):
                kept.append(r)
            if len(kept) > limit:
                break
        rows = kept
    page = rows[:limit]
    return DashboardConversationPage(
        items=page, next_cursor=encode_cursor(page[-1]) if len(rows) > limit and page else None
    )


@router.get("/conversations/{conversation_id}", response_model=DashboardConversation)
async def get_conversation(request: Request, conversation_id: CONVERSATION, tenant_id: TENANT) -> DashboardConversation:
    """One conversation: the transcript, every decision trace, and the case if one was opened."""
    b = built(request, tenant_id)
    traces = await b.traces.for_conversation(tenant_id, conversation_id)
    if not traces:
        raise not_found("conversation not found")
    cases = [c for c in await b.cases.list(tenant_id) if c.conversation_id == conversation_id]
    return DashboardConversation(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        transcript=transcript_from_traces(traces),
        traces=traces,
        case=cases[-1] if cases else None,
    )


# ---- escalations, tools, knowledge gaps ----

HUMAN_DECISIONS = ("deny", "require_human")


@router.get("/escalations", response_model=DashboardEscalations)
async def get_escalations(
    request: Request, tenant_id: TENANT, bucket: m.Bucket = "day", from_: FROM = None, to: TO = None
) -> DashboardEscalations:
    """Why conversations went to a person, over time, with the queue now and the rules behind the refusals."""
    b = built(request, tenant_id)
    start, end = window(b, from_, to)
    try:
        by_reason = await service(b).escalation_by_reason(tenant_id, start, end, bucket)
        outcomes = await service(b).policy_outcomes(tenant_id, start, end, "month" if bucket != "hour" else bucket)
    except ValueError as exc:
        raise invalid_request(str(exc)) from None
    totals: dict[str, int] = defaultdict(int)
    for point in by_reason:
        for key, count in point.by_reason.items():
            totals[key] += count
    rules: dict[tuple[str, str], int] = defaultdict(int)
    for outcome in outcomes:
        for key, count in outcome.counts.items():
            action, reason_code, decision = key.rsplit(":", 2)
            if decision in HUMAN_DECISIONS:
                rules[(f"{action}:{reason_code}", decision)] += count
    return DashboardEscalations(
        tenant_id=tenant_id,
        period_start=start,
        period_end=end,
        conversations=sum(p.conversations for p in by_reason),
        by_reason_total=dict(totals),
        by_reason=by_reason,
        open_cases=await count_cases(b, tenant_id, CaseStatus.OPEN),
        claimed_cases=await count_cases(b, tenant_id, CaseStatus.CLAIMED),
        top_rules=sorted(
            (DashboardRuleCount(rule=r, decision=d, count=n) for (r, d), n in rules.items()),
            key=lambda x: (-x.count, x.rule),
        ),
    )


@router.get("/tools", response_model=DashboardTools)
async def get_tools(request: Request, tenant_id: TENANT, from_: FROM = None, to: TO = None) -> DashboardTools:
    """Per shop tool: calls, failures, the error codes and the speed. Reads and actions both count."""
    b = built(request, tenant_id)
    start, end = window(b, from_, to)
    calls = (await b.traces.facts(tenant_id, start, end)).tool_calls
    rows = []
    for tool in sorted({c.tool for c in calls}):
        made = [c for c in calls if c.tool == tool]
        failed = [c for c in made if c.status == "error"]
        codes: dict[str, int] = defaultdict(int)
        for c in failed:
            codes[c.error_code or m.UNKNOWN] += 1
        speeds = [c.latency_ms for c in made]
        rows.append(
            DashboardToolRow(
                tool=tool,
                calls=len(made),
                failures=len(failed),
                rate=len(failed) / len(made),
                errors=dict(codes),
                p50_ms=m.percentile(speeds, 0.5),
                p95_ms=m.percentile(speeds, 0.95),
            )  # fmt: skip
        )
    rows.sort(key=lambda r: (-r.calls, r.tool))
    return DashboardTools(tenant_id=tenant_id, period_start=start, period_end=end, tools=rows)


@router.get("/knowledge-gaps", response_model=DashboardKnowledgeGaps)
async def get_knowledge_gaps(
    request: Request, tenant_id: TENANT, from_: FROM = None, to: TO = None
) -> DashboardKnowledgeGaps:
    """Questions the policy search could not answer, grouped by what they ask (the next document to upload)."""
    b = built(request, tenant_id)
    start, end = window(b, from_, to)
    unanswered = [t for t in (await b.traces.facts(tenant_id, start, end)).turns if t.evidence_empty]
    questions: list[tuple[str, datetime]] = []
    for turn in unanswered[-MAX_GAP_TRACES:]:
        found = await b.traces.get(tenant_id, turn.trace_id)
        if found is not None and found.customer_message:
            questions.append((found.customer_message, turn.created_at))
    return DashboardKnowledgeGaps(
        tenant_id=tenant_id,
        period_start=start,
        period_end=end,
        questions_without_answer=len(unanswered),
        groups=group_questions(questions),
    )
