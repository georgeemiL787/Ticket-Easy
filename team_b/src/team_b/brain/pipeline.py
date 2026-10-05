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
from team_b.brain.redaction import redact
from team_b.brain.rewrite import REWRITABLE
from team_b.brain.turn import Deps, StageFn, Step, TurnContext, locale_of
from team_b.domain.decision import Decision
from team_b.domain.reply import AgentReply
from team_b.domain.session import Message, SessionState
from team_b.domain.tenant import TenantConfig
from team_b.domain.trace import DecisionTrace, TraceStep
from team_b.domain.understanding import Language, Locale, NLUResult


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
        ctx.steps.append(TraceStep(stage=stage.name, status="skipped", detail=why))
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
        result = await rewriter.reword(text, locale, {k: str(v) for k, v in step.values.items()})
        ctx.steps.append(TraceStep(stage="rewrite", status=result.status, detail=result.detail))
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
        "entities": {key: redact(value) for key, value in result.entities.items()},
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
    fields["versions"] = {**fields.get("versions", {}), **ctx.versions}
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
        decision=step.decision,
        decision_reason=step.reason,
        response_text=text,
        response_citations=citations,
        escalation_reason=step.escalation if step.decision is Decision.HANDOFF else None,
        handoff_case_id=(ctx.handoff_case_id or session.handoff_case_id) if step.decision is Decision.HANDOFF else None,
        tool_calls=tuple(ctx.tool_calls),
        errors=tuple(ctx.errors),
        steps=tuple(ctx.steps),
        latency_ms=(time.perf_counter() - ctx.started) * 1000,
    )
    await ctx.deps.sessions.save(session)
    await ctx.deps.traces.add(trace)
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


async def _load(deps: Deps, tenant: TenantConfig, conversation_id: str, text: str) -> TurnContext:
    started = time.perf_counter()
    now = deps.clock.now()
    session = await deps.sessions.load(tenant.tenant_id, conversation_id) or _new_session(tenant, conversation_id, now)
    ctx = TurnContext(
        deps=deps,
        tenant=tenant,
        session=session,
        text=text,
        now=now,
        request_id=uuid.uuid4().hex,
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


def _new_session(tenant: TenantConfig, conversation_id: str, now: datetime) -> SessionState:
    return SessionState(
        tenant_id=tenant.tenant_id,
        conversation_id=conversation_id,
        language=Language(tenant.default_locale.value),
        created_at=now,
        updated_at=now,
    )


async def run_turn(deps: Deps, tenant: TenantConfig, conversation_id: str, text: str) -> AgentReply:
    """Handle one customer message start to finish, one message at a time per conversation."""
    async with deps.sessions.lock(tenant.tenant_id, conversation_id):
        ctx = await _load(deps, tenant, conversation_id, text)
        for stage in STAGES:
            await run_stage(ctx, stage)
        return await finish(ctx)
