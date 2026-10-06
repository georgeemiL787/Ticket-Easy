"""Opening a handoff case: the stub the turn pipeline uses until the real handoff module exists.

It records what the pipeline knows when it escalates (the reason, a priority, the transcript so far, the details the
customer gave) as a valid HandoffCase, so a human has something to pick up and the case is visible in the case store.
The full briefing (summaries, policy quotes, suggested wording) and the human actions come with the handoff step.
"""

import uuid
from typing import cast

from team_b.brain.redaction import redact
from team_b.brain.turn import TurnContext
from team_b.domain.actions import ActionProposal
from team_b.domain.decision import EscalationReason
from team_b.domain.handoff import CustomerSnapshot, HandoffCase, HandoffPackage, PendingApproval, Priority
from team_b.domain.session import SessionState
from team_b.domain.tenant import TenantConfig
from team_b.domain.trace import PolicyRecord
from team_b.domain.understanding import Language

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


def build_package(
    session: SessionState,
    reason: EscalationReason,
    summary: str,
    rule_answers: tuple[PolicyRecord, ...] = (),
    tenant: TenantConfig | None = None,
) -> HandoffPackage:
    if tenant is not None:
        priority, next_step = priority_for(tenant, reason), next_step_for(tenant, reason, session.language)
    else:
        priority, next_step = ESCALATION_DEFAULTS[reason]
    identity = session.identity
    orders = (session.slots["order_id"],) if session.slots.get("order_id") else ()
    return HandoffPackage(
        summary=summary,
        reason=reason,
        priority=priority,
        suggested_next_step=next_step,
        customer=CustomerSnapshot(verified=identity.verified, customer_id=identity.customer_id, orders=orders),
        details={key: redact(value) for key, value in session.slots.items()},
        order_facts=dict(session.facts),
        safety_flags=tuple(session.risk_categories),
        rule_answers=rule_answers,
        transcript=tuple(session.history),
    )


async def open_case(
    ctx: TurnContext,
    reason: EscalationReason,
    detail: str,
    pending_approval: ActionProposal | None = None,
) -> str:
    """Store a new open case for this conversation, mark the session handed off, and return the case id.

    `pending_approval` is the action a human must approve (reason approval_required); it is recorded on the case."""
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
    case = HandoffCase(
        case_id=f"case-{uuid.uuid4().hex[:12]}",
        tenant_id=session.tenant_id,
        conversation_id=session.conversation_id,
        package=build_package(session, reason, f"Handed off ({reason.value}): {detail}.", tenant=ctx.tenant),
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
