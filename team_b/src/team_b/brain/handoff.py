"""Opening a handoff case: the reason table, the briefing a support person reads, and the check that it is complete.

Every fact in the briefing comes from something recorded: the session (details, identity, facts, actions), the stored
traces of this conversation (transcript, tool calls, policy answers, evidence) and the policy store (passages quoted
word for word). Nothing here is written by an AI model; the summary is a template (an optional AI rewrite, checked
against these facts, is added on top by brain/summarizer.py).
"""

import re
import uuid
from typing import TypeVar, cast

from team_b.brain.knowledge import quote_citations
from team_b.brain.redaction import redact
from team_b.brain.summarizer import add_ai_summary
from team_b.brain.transcript import transcript_from_traces
from team_b.brain.turn import TurnContext
from team_b.contracts.errors import UpstreamError
from team_b.domain.actions import ActionProposal
from team_b.domain.decision import EscalationReason
from team_b.domain.handoff import (
    AttemptedAction,
    CustomerSnapshot,
    FailureRecord,
    HandoffCase,
    HandoffPackage,
    PendingApproval,
    PolicyQuote,
    Priority,
    SimilarTicket,
    TranscriptLine,
)
from team_b.domain.tenant import TenantConfig
from team_b.domain.trace import DecisionTrace, PolicyRecord
from team_b.domain.understanding import Language

_T = TypeVar("_T")
MAX_QUOTES = 5
MAX_SIMILAR = 3

R = EscalationReason
# reason -> default priority. The shop can change any of them in its tenant file (escalation.priorities).
DEFAULT_PRIORITY: dict[EscalationReason, Priority] = {
    R.MANDATORY_RISK: "urgent",
    R.REPEATED_TOOL_FAILURE: "high",
    R.UNVERIFIED_RESULT: "high",
    R.DEPENDENCY_UNAVAILABLE: "high",
    R.IDENTITY_FAILED: "high",
    R.OWNERSHIP_MISMATCH: "high",
    R.HIGH_FRUSTRATION: "high",
    R.CUSTOMER_REQUEST: "normal",
    R.POLICY_DENIED: "normal",
    R.APPROVAL_REQUIRED: "normal",
    R.NO_EVIDENCE: "normal",
    R.LOW_CONFIDENCE: "normal",
    R.CAPABILITY_MISSING: "normal",
    R.UNSUPPORTED: "normal",
}
# reason -> what a human should do first, in English (en) and Egyptian Arabic (ar). Also tunable per shop.
NEXT_STEP: dict[EscalationReason, dict[str, str]] = {
    R.CUSTOMER_REQUEST: {
        "en": "Greet the customer and ask how you can help.",
        "ar": "رحّب بالعميل واسأله تقدر تساعده في إيه.",
    },
    R.MANDATORY_RISK: {
        "en": "Read the message first and take over personally; do not let the assistant continue.",
        "ar": "اقرا الرسالة الأول وخد المحادثة بنفسك؛ ماتسيبش المساعد يكمل.",
    },
    R.POLICY_DENIED: {
        "en": "Review the denied request and explain the policy, or decide on an exception.",
        "ar": "راجع الطلب المرفوض واشرح السياسة للعميل، أو قرر لو فيه استثناء.",
    },
    R.APPROVAL_REQUIRED: {
        "en": "Review the request and approve or reject it.",
        "ar": "راجع الطلب ووافق عليه أو ارفضه.",
    },
    R.REPEATED_TOOL_FAILURE: {
        "en": "Check the shop system, then look the information up by hand.",
        "ar": "اتأكد من سيستم المتجر وبعدين دور على المعلومة بنفسك.",
    },
    R.UNVERIFIED_RESULT: {
        "en": "Check in the shop system whether the action really happened before replying.",
        "ar": "اتأكد في سيستم المتجر إن الإجراء اتنفذ فعلاً قبل ما ترد على العميل.",
    },
    R.NO_EVIDENCE: {
        "en": "Answer the question from your own knowledge of the shop's policies.",
        "ar": "جاوب على السؤال من معرفتك بسياسات المتجر.",
    },
    R.DEPENDENCY_UNAVAILABLE: {
        "en": "A system the assistant needs is down; handle the request by hand.",
        "ar": "فيه سيستم المساعد محتاجه واقع؛ نفّذ الطلب بإيدك.",
    },
    R.LOW_CONFIDENCE: {
        "en": "The assistant could not understand the request; ask the customer to explain.",
        "ar": "المساعد ماقدرش يفهم الطلب؛ اطلب من العميل يوضحه.",
    },
    R.IDENTITY_FAILED: {
        "en": "The customer could not be verified; verify them another way before sharing anything.",
        "ar": "ماقدرناش نتأكد من هوية العميل؛ اتأكد بطريقة تانية قبل ما تشارك أي بيانات.",
    },
    R.OWNERSHIP_MISMATCH: {
        "en": "The customer asked about an order that is not theirs; check before sharing anything.",
        "ar": "العميل سأل عن أوردر مش بتاعه؛ راجع قبل ما تشارك أي بيانات.",
    },
    R.HIGH_FRUSTRATION: {
        "en": "The customer is upset; apologise and take over.",
        "ar": "العميل متضايق؛ اعتذر له وخد المحادثة.",
    },
    R.CAPABILITY_MISSING: {
        "en": "The assistant cannot do this yet; do it in the shop system.",
        "ar": "المساعد لسه مش بيعمل ده؛ نفّذه من سيستم المتجر.",
    },
    R.UNSUPPORTED: {
        "en": "The request is outside what the assistant may do; handle it personally.",
        "ar": "الطلب برا اللي المساعد مسموح له بيه؛ اتصرف فيه بنفسك.",
    },
}
# reason -> (default priority, English next step): the shop-independent table
ESCALATION_DEFAULTS: dict[EscalationReason, tuple[Priority, str]] = {
    reason: (DEFAULT_PRIORITY[reason], NEXT_STEP[reason]["en"]) for reason in EscalationReason
}


def priority_for(tenant: TenantConfig, reason: EscalationReason) -> Priority:
    """The shop's priority for this reason, else the default."""
    chosen = tenant.escalation.priorities.get(reason.value)
    return cast(Priority, chosen) if chosen else DEFAULT_PRIORITY[reason]


def next_step_for(tenant: TenantConfig, reason: EscalationReason, language: Language | None) -> str:
    """What a human should do first. Arabic and mixed customers get the Arabic text, the others English."""
    key = "ar" if language in (Language.AR, Language.MIXED) else "en"
    own = tenant.escalation.next_steps.get(reason.value, {})
    return own.get(key) or own.get("en") or NEXT_STEP[reason][key]


def mask_phone(phone: str) -> str:
    """010****5678: the first three and last four digits of an 11-digit number, the rest hidden."""
    digits = re.sub(r"\D", "", phone)
    if len(digits) >= 8:
        return digits[:3] + "*" * (len(digits) - 7) + digits[-4:]
    return "*" * max(0, len(digits) - 2) + digits[-2:]


def _unique(items: list[_T]) -> list[_T]:
    return list(dict.fromkeys(items))


def _policy_records(traces: list[DecisionTrace], actions: list[ActionProposal]) -> tuple[PolicyRecord, ...]:
    found: dict[str, PolicyRecord] = {p.request_id: p for t in traces for p in t.policy}
    for action in actions:
        for d in action.policy_decisions:
            found.setdefault(
                d.request_id,
                PolicyRecord(
                    request_id=d.request_id,
                    action=d.action or action.capability,
                    decision=d.decision,
                    reason_code=d.reason_code,
                    citations=d.citations,
                ),
            )
    return tuple(found.values())


def _attempted(actions: list[ActionProposal]) -> tuple[AttemptedAction, ...]:
    out = []
    for a in actions:
        history = tuple(
            f"{c.from_state.value} -> {c.to_state.value}" + (f" ({c.note})" if c.note else "") for c in a.history
        )
        result = a.result
        out.append(
            AttemptedAction(
                proposal_id=a.proposal_id,
                tool=a.tool,
                state=a.state.value,
                error=result.error_code if result is not None and result.status == "error" else None,
                arguments={k: redact(str(v)) for k, v in a.arguments.items()},
                history=history,
                audit_id=result.audit_id if result is not None else None,
                execution_id=result.reference_id if result is not None else None,
            )
        )
    return tuple(out)


def _failures(
    ctx: TurnContext, traces: list[DecisionTrace], actions: list[ActionProposal]
) -> tuple[FailureRecord, ...]:
    found: list[FailureRecord] = []
    calls = [(c, t.trace_id) for t in traces for c in t.tool_calls] + [(c, ctx.trace_id) for c in ctx.tool_calls]
    for call, trace_id in calls:
        if call.status == "error":
            found.append(
                FailureRecord(
                    source=call.tool, error_code=call.error_code or "ERROR", audit_id=call.audit_id, trace_id=trace_id
                )
            )
    for a in actions:
        r = a.result
        if r is not None and r.status == "error":
            maybe = " (the write may have been applied)" if r.write_may_have_applied else ""
            found.append(
                FailureRecord(
                    source=a.tool,
                    error_code=r.error_code or "ERROR",
                    message=(r.error_message or "") + maybe,
                    audit_id=r.audit_id,
                    execution_id=r.reference_id,
                )
            )
    problems = [(e, t.trace_id) for t in traces for e in t.errors] + [(e, ctx.trace_id) for e in ctx.errors]
    found += [
        FailureRecord(source="system", error_code="DEPENDENCY_ERROR", message=e, trace_id=trace_id)
        for e, trace_id in problems
    ]
    return tuple(_unique(found))


async def _similar_tickets(ctx: TurnContext) -> tuple[SimilarTicket, ...]:
    provider = ctx.deps.evidence
    if provider is None:
        return ()
    try:
        result = await provider.search_past_tickets(
            ctx.tenant.tenant_id, redact(ctx.text), request_id=ctx.request_id, top_k=MAX_SIMILAR
        )
    except (UpstreamError, NotImplementedError):
        return ()  # a missing "similar tickets" list never stops a handoff
    return tuple(
        SimilarTicket(ticket_id=t.ticket_id, category=t.category, resolution=t.resolution, citation=t.citation)
        for t in result.tickets
    )


def template_summary(
    intents: tuple[str, ...], reason: EscalationReason, detail: str, customer: CustomerSnapshot, failures: int
) -> str:
    """Plain facts for the first line of the briefing."""
    who = f"verified customer {customer.customer_id}" if customer.verified else "an unverified customer"
    wanted = ", ".join(intents) if intents else "no clear request"
    parts = [f"Handed off ({reason.value}): {detail}.", f"The customer ({who}) asked about: {wanted}."]
    if customer.orders:
        parts.append(f"Orders mentioned: {', '.join(customer.orders)}.")
    if failures:
        parts.append(f"{failures} failure(s) are listed below.")
    return " ".join(parts)


async def build_package(
    ctx: TurnContext, reason: EscalationReason, detail: str, pending: PendingApproval | None
) -> HandoffPackage:
    session, tenant = ctx.session, ctx.tenant
    traces = await ctx.deps.traces.for_conversation(tenant.tenant_id, session.conversation_id)
    identity = session.identity
    phone = session.slots.get("phone")
    ids = [t.entities.get("order_id", "") for t in traces] + [session.slots.get("order_id", "")]
    customer = CustomerSnapshot(
        verified=identity.verified,
        customer_id=identity.customer_id,
        method=identity.method,
        phone_masked=mask_phone(phone) if phone else None,
        orders=tuple(_unique([i for i in ids if i])),
    )
    policy = _policy_records(traces, session.actions)
    cited = _unique(
        [c for p in policy for c in p.citations]
        + [e.citation for t in traces for e in t.evidence]
        + [e.citation for e in ctx.evidence]
    )[:MAX_QUOTES]
    passages = await quote_citations(ctx, tuple(cited), record=False)
    transcript = [*transcript_from_traces(traces)]
    transcript.append(
        TranscriptLine(role="customer", text=redact(ctx.text), trace_id=ctx.trace_id, turn_index=session.turn_index)
    )
    failures = _failures(ctx, traces, session.actions)
    intents = tuple(_unique([*session.intents_seen, *([session.active_intent] if session.active_intent else [])]))
    return HandoffPackage(
        summary=template_summary(intents, reason, detail, customer, len(failures)),
        reason=reason,
        detail=detail,
        priority=priority_for(tenant, reason),
        suggested_next_step=next_step_for(tenant, reason, session.language),
        language=session.language,
        intents=intents,
        customer=customer,
        details={k: redact(v) for k, v in session.slots.items() if k != "phone"},
        order_facts=dict(session.facts),
        safety_flags=tuple(session.risk_categories),
        policy_quotes=tuple(PolicyQuote(citation=p.citation, text=p.text) for p in passages),
        rule_answers=policy,
        attempted_actions=_attempted(session.actions),
        failures=failures,
        pending_approval=pending,
        transcript=tuple(transcript),
        trace_ids=tuple(_unique([*(t.trace_id for t in traces), ctx.trace_id])),
        similar_tickets=await _similar_tickets(ctx),
    )


# ---- is the briefing complete? ----

FULL_PHONE = re.compile(r"(?<!\d)01[0-25]\d{8}(?!\d)")
NEEDS_FAILURES = {
    EscalationReason.REPEATED_TOOL_FAILURE,
    EscalationReason.UNVERIFIED_RESULT,
    EscalationReason.DEPENDENCY_UNAVAILABLE,
}


def incomplete(case: HandoffCase) -> list[str]:
    """What is missing from the briefing of this case; empty when a human has everything they need."""
    pkg, missing = case.package, []

    def need(ok: bool, what: str) -> None:
        if not ok:
            missing.append(what)

    need(bool(pkg.summary.strip()), "summary")
    need(bool(pkg.detail.strip()), "detail (the specific cause)")
    need(bool(pkg.suggested_next_step.strip()), "suggested next step")
    need(pkg.language is not None, "language")
    need(bool(pkg.trace_ids), "trace ids")
    need(any(line.role == "customer" for line in pkg.transcript), "transcript with the customer's messages")
    need(not pkg.customer.verified or bool(pkg.customer.customer_id), "customer id of a verified customer")
    need(FULL_PHONE.search(pkg.model_dump_json()) is None, "phone number hidden (a full number is in the briefing)")
    need(case.pending_approval == pkg.pending_approval, "pending approval on both the case and the briefing")
    if pkg.reason is EscalationReason.APPROVAL_REQUIRED:
        need(case.pending_approval is not None, "the action waiting for approval")
    if pkg.reason is EscalationReason.MANDATORY_RISK:
        need(bool(pkg.safety_flags), "risk categories")
    if pkg.reason is EscalationReason.POLICY_DENIED:
        need(any(r.decision == "deny" for r in pkg.rule_answers), "the rule checker's deny")
    if pkg.reason in NEEDS_FAILURES:
        need(bool(pkg.failures), "the failures behind the handoff")
    if pkg.reason is EscalationReason.UNVERIFIED_RESULT:
        need(bool(pkg.attempted_actions), "the attempted action")
    return missing


async def open_case(
    ctx: TurnContext,
    reason: EscalationReason,
    detail: str,
    pending_approval: ActionProposal | None = None,
) -> str:
    """Store a new open case for this conversation, mark the session handed off, and return the case id.

    `pending_approval` is the action a human must approve (reason approval_required); it is recorded on the case
    and on the briefing."""
    session = ctx.session
    waiting = (
        PendingApproval(
            proposal_id=pending_approval.proposal_id,
            tool=pending_approval.tool,
            capability=pending_approval.capability,
            arguments=dict(pending_approval.arguments),
            reason=detail,
        )
        if pending_approval is not None
        else None
    )
    package = await build_package(ctx, reason, detail, waiting)
    if ctx.deps.llm is not None:
        outcome = await add_ai_summary(ctx.deps.llm, package)
        package = outcome.package
        ctx.versions["handoff_summary"] = f"{outcome.status}: {outcome.detail}"
    case = HandoffCase(
        case_id=f"case-{uuid.uuid4().hex[:12]}",
        tenant_id=session.tenant_id,
        conversation_id=session.conversation_id,
        package=package,
        pending_approval=waiting,
        created_at=ctx.now,
        updated_at=ctx.now,
    )
    await ctx.deps.cases.add(case)
    session.status = "handed_off"
    session.handoff_case_id = case.case_id
    session.last_escalation = reason
    session.handoff_notice_sent = False
    return case.case_id
