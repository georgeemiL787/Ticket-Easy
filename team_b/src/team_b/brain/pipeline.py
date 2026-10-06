"""One turn, as an explicit pipeline of stages, each recorded in the trace with its duration.

load -> handed_off_check -> understand -> risk_screen -> human_request -> pending_confirmation -> disambiguate
-> merge -> frustration -> plan -> handler -> queue -> handoff -> finish

A stage that runs after a decision was made is recorded as skipped (handoff runs only for a handoff decision, finish
always runs). finish composes the reply in the customer's locale, validates the trace, and saves session and trace.
"""

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from team_b.brain import stages
from team_b.brain.composer import default_composer
from team_b.brain.redaction import redact, redact_mapping, redact_value
from team_b.brain.rewrite import REWRITABLE
from team_b.brain.turn import Deps, StageFn, Step, TurnContext, locale_of
from team_b.brain.versions import base_versions
from team_b.domain.decision import Decision
from team_b.domain.reply import AgentReply
from team_b.domain.session import Message, SessionState
from team_b.domain.tenant import TenantConfig
from team_b.domain.trace import DecisionTrace, ProposalRecord, TraceStep, record_problems
from team_b.domain.understanding import Language, Locale, NLUResult
from team_b.observability import get_logger, turn_context

log = get_logger(__name__)


@dataclass(frozen=True)
class Stage:
    name: str
    run: StageFn
    skip_when_decided: bool = True  # recorded as skipped once a decision exists
    only_when_handoff: bool = False  # runs only if the decision is a handoff


STAGES: tuple[Stage, ...] = (
    Stage("handed_off_check", stages.handed_off_check, skip_when_decided=False),
    Stage("understand", stages.understand),
    Stage("risk_screen", stages.risk_screen),
    Stage("human_request", stages.human_request),
    Stage("pending_confirmation", stages.pending_confirmation),
    Stage("disambiguate", stages.disambiguate),
    Stage("merge", stages.merge),
    Stage("frustration", stages.frustration),
    Stage("plan", stages.plan),
    Stage("handler", stages.handler),
    Stage("queue", stages.queue, skip_when_decided=False),
    Stage("handoff", stages.handoff, skip_when_decided=False, only_when_handoff=True),
)


async def run_stage(ctx: TurnContext, stage: Stage) -> None:
    started = time.perf_counter()
    decided = ctx.step is not None
    if stage.only_when_handoff:
        skip = not (decided and ctx.step is not None and ctx.step.decision is Decision.HANDOFF and not ctx.human_owned)
    else:
        skip = stage.skip_when_decided and decided
    if skip:
        why = "already decided" if decided else "nothing to do"
        ctx.steps.append(TraceStep(stage=stage.name, status="skipped", duration_ms=0.0, detail=why))
        return
    detail = await stage.run(ctx)
    ctx.steps.append(
        TraceStep(stage=stage.name, status="ok", duration_ms=(time.perf_counter() - started) * 1000, detail=detail)
    )


async def say_step(ctx: TurnContext, step: Step, locale: Locale) -> str:
    """One step's reply: the template, optionally reworded by the AI model (only if the fact check passes), then the
    policy passages appended verbatim. The passages are added after the rewrite, so the model never sees them."""
    composer = default_composer()
    text = composer.t(locale, step.reply_key, **step.values)
    rewriter = ctx.deps.rewriter
    if rewriter is not None and step.decision in REWRITABLE and not step.silent:
        started = time.perf_counter()
        result = await rewriter.reword(text, locale, {k: str(v) for k, v in step.values.items()})
        elapsed = (time.perf_counter() - started) * 1000
        ctx.steps.append(TraceStep(stage="rewrite", status=result.status, duration_ms=elapsed, detail=result.detail))
        ctx.versions["rewrite_prompt"] = getattr(rewriter, "prompt_version", "unknown")
        text = result.text
    if step.passages:
        text = f"{text}\n\n{composer.passage_block(step.passages)}"
    return text


async def compose(ctx: TurnContext, locale: Locale) -> str:
    """The replies of every completed step and the final one, joined, in the customer's locale."""
    assert ctx.step is not None
    parts = [*ctx.earlier, ctx.step]
    return "\n\n".join([await say_step(ctx, s, locale) for s in parts if not s.silent])


def understanding_fields(result: NLUResult | None) -> dict[str, Any]:
    """What the trace records about how the message was read (details redacted)."""
    if result is None:
        return {}
    return {
        "language": result.language,
        "intents": result.intents,
        "entities": {key: str(redact_value(key, value)) for key, value in result.entities.items()},
        "nlu_method": result.method,
        "frustration": result.frustration,
        "versions": {"prompt": result.prompt_version} if result.prompt_version else {},
    }


async def _trim(ctx: TurnContext) -> None:
    """Keep the last history_max_turns messages; older ones are folded into history_summary."""
    session = ctx.session
    excess = len(session.history) - ctx.tenant.history_max_turns
    if excess > 0:
        folded = session.history[:excess]
        session.history_summary = await ctx.deps.summarizer.summarize(session, folded, ctx.tenant)
        del session.history[:excess]


def log_turn_events(ctx: TurnContext, trace: DecisionTrace) -> None:
    """One log line per policy check, tool call and the end of the turn (ids and codes only, no message text)."""
    for entry in trace.policy:
        log.info(
            "policy_check", action=entry.action, decision=entry.decision, reason_code=entry.reason_code,
            policy_request_id=entry.request_id,
        )  # fmt: skip
    for call in trace.tool_calls:
        log.info(
            "tool_call", tool=call.tool, operation_kind=call.operation_kind, status=call.status,
            error_code=call.error_code, audit_id=call.audit_id, latency_ms=round(call.latency_ms, 1),
        )  # fmt: skip
    log.info(
        "turn_complete", decision=trace.decision.value,
        escalation_reason=trace.escalation_reason.value if trace.escalation_reason else None,
        intents=[i.name for i in trace.intents], latency_ms=round(trace.latency_ms, 1), error_count=len(trace.errors),
        handoff_case_id=trace.handoff_case_id,
    )  # fmt: skip


async def finish(ctx: TurnContext) -> AgentReply:
    started = time.perf_counter()
    step, session = ctx.step, ctx.session
    assert step is not None, "a turn must end with a decision"
    locale = locale_of(ctx)
    text = await compose(ctx, locale)
    if text:
        session.history.append(Message(role="agent", text=text, at=ctx.now, trace_id=ctx.trace_id))
    session.awaiting = step.awaiting
    session.turn_index += 1
    session.updated_at = ctx.now
    await _trim(ctx)
    ctx.steps.append(
        TraceStep(
            stage="finish", status="ok", duration_ms=(time.perf_counter() - started) * 1000, detail=step.decision.value
        )
    )

    citations = tuple(dict.fromkeys(c for s in (*ctx.earlier, step) for c in s.citations))
    fields = understanding_fields(ctx.understanding)
    fields["versions"] = {**base_versions(ctx.tenant), **fields.get("versions", {}), **ctx.versions}
    calls = tuple(call.model_copy(update={"arguments": redact_mapping(call.arguments)}) for call in ctx.tool_calls)
    trace = DecisionTrace(
        trace_id=ctx.trace_id,
        request_id=ctx.request_id,
        tenant_id=ctx.tenant.tenant_id,
        conversation_id=session.conversation_id,
        turn_index=session.turn_index - 1,
        customer_message=redact(ctx.text),
        **fields,
        active_intent=session.active_intent,
        identity=session.identity.model_copy(),
        risk_categories=tuple(session.risk_categories),
        evidence=tuple(ctx.evidence),
        evidence_empty_reason=ctx.evidence_empty_reason,
        knowledge_answer=any(s.knowledge for s in (*ctx.earlier, step)),
        decision=step.decision,
        decision_reason=redact(step.reason),
        response_text=redact(text),  # the customer gets `text`; the stored record hides personal values
        response_citations=citations,
        escalation_reason=step.escalation if step.decision is Decision.HANDOFF else None,
        handoff_case_id=(ctx.handoff_case_id or session.handoff_case_id) if step.decision is Decision.HANDOFF else None,
        tool_calls=calls,
        policy=tuple(ctx.policy),
        proposals=tuple(
            ProposalRecord(proposal_id=a.proposal_id, tool=a.tool, state=a.state.value)
            for a in session.actions
            if a.proposal_id in ctx.proposal_ids
        ),
        errors=tuple(redact(e) for e in ctx.errors),
        steps=tuple(ctx.steps),
        latency_ms=(time.perf_counter() - ctx.started) * 1000,
    )
    problems = record_problems(trace)
    if problems:  # a bug in the brain, never the customer's doing: fail loudly rather than store a thin record
        raise ValueError(f"incomplete trace {trace.trace_id}: {'; '.join(problems)}")
    await ctx.deps.sessions.save(session)
    await ctx.deps.traces.add(trace)
    log_turn_events(ctx, trace)
    return AgentReply(
        request_id=ctx.request_id,
        tenant_id=ctx.tenant.tenant_id,
        conversation_id=session.conversation_id,
        text=text,
        locale=locale,
        decision=step.decision,
        citations=citations,
        trace_id=ctx.trace_id,
        handoff_case_id=trace.handoff_case_id,
        awaiting=step.awaiting,
    )


async def _load(
    deps: Deps, tenant: TenantConfig, conversation_id: str, text: str, request_id: str | None, channel: str
) -> TurnContext:
    started = time.perf_counter()
    now = deps.clock.now()
    session = await deps.sessions.load(tenant.tenant_id, conversation_id) or _new_session(
        tenant, conversation_id, now, channel
    )
    ctx = TurnContext(
        deps=deps,
        tenant=tenant,
        session=session,
        text=text,
        now=now,
        request_id=request_id or uuid.uuid4().hex,
        trace_id=uuid.uuid4().hex,
    )
    session.history.append(Message(role="customer", text=text, at=now, trace_id=ctx.trace_id))
    created = "created" if session.turn_index == 0 and len(session.history) == 1 else "loaded"
    ctx.steps.append(
        TraceStep(
            stage="load", status="ok", duration_ms=(time.perf_counter() - started) * 1000, detail=f"session {created}"
        )
    )
    return ctx


def _new_session(tenant: TenantConfig, conversation_id: str, now: datetime, channel: str) -> SessionState:
    return SessionState(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        channel=channel,
        language=Language(tenant.default_locale.value),
        created_at=now,
        updated_at=now,
    )


async def run_turn(
    deps: Deps,
    tenant: TenantConfig,
    conversation_id: str,
    text: str,
    *,
    request_id: str | None = None,
    channel: str = "web",
) -> AgentReply:
    """Handle one customer message start to finish, one message at a time per conversation."""
    async with deps.sessions.lock(tenant.tenant_id, conversation_id):
        ctx = await _load(deps, tenant, conversation_id, text, request_id, channel)
        with turn_context(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
            request_id=ctx.request_id,
            trace_id=ctx.trace_id,
        ):
            log.info("turn_start", turn_index=ctx.session.turn_index, channel=channel, message_chars=len(text))
            for stage in STAGES:
                await run_stage(ctx, stage)
            return await finish(ctx)
