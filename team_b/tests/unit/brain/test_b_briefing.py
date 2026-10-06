"""The handoff briefing: every part comes from recorded facts, and the completeness checker catches gaps."""

from datetime import UTC, datetime

import pytest

from team_b.brain.handoff import incomplete, mask_phone, open_case
from team_b.brain.turn import TurnContext
from team_b.container import Container
from team_b.contracts.policy import PolicyDecision
from team_b.contracts.tools import ToolResult
from team_b.domain.actions import ActionProposal, ActionState
from team_b.domain.decision import EscalationReason
from team_b.domain.handoff import FailureRecord, HandoffCase
from team_b.domain.session import SessionIdentity, SessionState
from team_b.domain.trace import EvidenceRef
from team_b.domain.understanding import Language
from tests.unit.brain.test_pipeline import C, T, last_trace, orch, say

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
R = EscalationReason


def test_phone_numbers_are_masked_to_first_three_and_last_four_digits() -> None:
    assert mask_phone("01012345678") == "010****5678"
    assert mask_phone("+20 101 234 5678") == "201*****5678"
    assert mask_phone("1234") == "**34"


def rich_context(c: Container) -> tuple[TurnContext, ActionProposal]:
    """A conversation that went wrong: verified customer, a refund the rule checker denied and a failed call."""
    deps = orch(c, evidence=c.evidence)._deps
    session = SessionState(
        tenant_id=T, conversation_id="rich", created_at=NOW, updated_at=NOW, language=Language.AR,
        slots={"order_id": "NS-20512", "phone": "01012345678", "amount": "450"},
        facts={"order_status": "delivered", "delivered_at": "2026-09-08"},
        intents_seen=["refund_request"], active_intent="refund_request", risk_categories=["fraud"],
    )  # fmt: skip
    session.identity = SessionIdentity(verified=True, customer_id="C-1001", method="order+phone")
    proposal = ActionProposal(
        proposal_id="p1", tool="create_refund", capability="create_refund", idempotency_key="k1",
        arguments={"order_id": "NS-20512", "amount": 450},
    )  # fmt: skip
    proposal.policy_decisions.append(
        PolicyDecision(
            request_id="pol-1", action="create_refund", decision="deny", reason_code="REFUND_WINDOW",
            citations=("refund_policy@v1#s1",),
        )
    )  # fmt: skip
    proposal.transition(ActionState.BLOCKED, at=NOW, note="denied by R-REFUND-14D")
    proposal.result = ToolResult(status="error", error_code="BACKEND_ERROR", audit_id="aud-7", reference_id="exec-9")
    session.actions.append(proposal)
    ctx = TurnContext(deps=deps, tenant=c.tenants.get(T), session=session, text="refund NS-20512", now=NOW,
                      request_id="r", trace_id="t-now")  # fmt: skip
    ctx.evidence.append(EvidenceRef(citation="return_policy@v2#s2", document_id="return_policy", version="v2", score=9))
    return ctx, proposal


async def test_the_briefing_is_built_from_recorded_facts(c: Container) -> None:
    ctx, _ = rich_context(c)
    case_id = await open_case(ctx, R.POLICY_DENIED, "refund after 20 days")
    case = await c.cases.get(T, case_id)
    assert case is not None
    pkg = case.package
    assert (pkg.reason, pkg.detail, pkg.priority, pkg.language) == (
        R.POLICY_DENIED,
        "refund after 20 days",
        "normal",
        Language.AR,
    )
    assert pkg.suggested_next_step == "راجع الطلب المرفوض واشرح السياسة للعميل، أو قرر لو فيه استثناء."
    assert pkg.intents == ("refund_request",) and pkg.safety_flags == ("fraud",)
    assert (pkg.customer.customer_id, pkg.customer.method, pkg.customer.phone_masked) == (
        "C-1001",
        "order+phone",
        "010****5678",
    )
    assert pkg.customer.orders == ("NS-20512",) and pkg.details == {"order_id": "NS-20512", "amount": "450"}
    assert pkg.order_facts["order_status"] == "delivered"
    assert [(r.decision, r.reason_code, r.citations) for r in pkg.rule_answers] == [
        ("deny", "REFUND_WINDOW", ("refund_policy@v1#s1",))
    ]
    quoted = {q.citation: q.text for q in pkg.policy_quotes}
    stored = await c.evidence.get_passage(T, "return_policy@v2#s2")  # type: ignore[union-attr]
    assert stored is not None and quoted["return_policy@v2#s2"] == stored.text  # word for word
    (action,) = pkg.attempted_actions
    assert (action.state, action.error, action.audit_id, action.execution_id) == (
        "blocked",
        "BACKEND_ERROR",
        "aud-7",
        "exec-9",
    )
    assert action.history == ("proposed -> blocked (denied by R-REFUND-14D)",)
    assert (
        FailureRecord(
            source="create_refund", error_code="BACKEND_ERROR", message="", audit_id="aud-7", execution_id="exec-9"
        )
        in pkg.failures
    )
    assert pkg.trace_ids == ("t-now",) and [(line.role, line.text) for line in pkg.transcript] == [
        ("customer", "refund NS-20512")
    ]
    assert "01012345678" not in pkg.model_dump_json()
    assert incomplete(case) == []


async def test_the_transcript_and_trace_ids_come_from_the_stored_traces(c: Container) -> None:
    o = orch(c, evidence=c.evidence)
    await say(o, "How many days do I have to return an item?")
    reply = await say(o, "I want to talk to a human")
    case = await c.cases.get(T, reply.handoff_case_id or "")
    assert case is not None
    first = (await c.traces.for_conversation(T, C))[0]
    assert [line.role for line in case.package.transcript] == ["customer", "agent", "customer"]
    assert case.package.trace_ids[0] == first.trace_id and case.package.trace_ids[-1] == reply.trace_id
    assert "return_policy@v2#s2" in [q.citation for q in case.package.policy_quotes]
    assert (await last_trace(c)).handoff_case_id == case.case_id  # the trace links to the case
    assert incomplete(case) == []


async def test_a_handoff_because_the_search_is_down_lists_the_failure(c: Container) -> None:
    assert c.policy_search is not None
    c.policy_search.fail_next("search_knowledge", 2)
    reply = await say(orch(c, evidence=c.evidence), "What is your return policy?")
    case = await c.cases.get(T, reply.handoff_case_id or "")
    assert case is not None and case.package.reason is R.DEPENDENCY_UNAVAILABLE
    assert any("policy search failed" in f.message for f in case.package.failures)
    assert incomplete(case) == []


async def _case(c: Container) -> HandoffCase:
    ctx, _ = rich_context(c)
    case = await c.cases.get(T, await open_case(ctx, R.POLICY_DENIED, "refund after 20 days"))
    assert case is not None
    return case


@pytest.mark.parametrize(
    ("change", "what"),
    [
        ({"summary": " "}, "summary"),
        ({"detail": ""}, "detail"),
        ({"suggested_next_step": ""}, "suggested next step"),
        ({"language": None}, "language"),
        ({"trace_ids": ()}, "trace ids"),
        ({"transcript": ()}, "transcript"),
        ({"rule_answers": ()}, "the rule checker's deny"),
        ({"summary": "call 01012345678"}, "phone number hidden"),
    ],
)
async def test_the_checker_names_what_is_missing(c: Container, change: dict[str, object], what: str) -> None:
    case = await _case(c)
    broken = case.model_copy(update={"package": case.package.model_copy(update=change)})
    assert any(what in m for m in incomplete(broken)), incomplete(broken)


async def test_the_checker_wants_the_action_waiting_for_approval(c: Container) -> None:
    ctx, proposal = rich_context(c)
    case = await c.cases.get(T, await open_case(ctx, R.APPROVAL_REQUIRED, "above the limit", pending_approval=proposal))
    assert case is not None and incomplete(case) == []
    broken = case.model_copy(update={"pending_approval": None})
    assert any("pending approval" in m for m in incomplete(broken))
    assert any("waiting for approval" in m for m in incomplete(broken))


async def test_failure_reasons_need_their_failures(c: Container) -> None:
    ctx, _ = rich_context(c)
    ctx.session.actions.clear()
    case = await c.cases.get(T, await open_case(ctx, R.REPEATED_TOOL_FAILURE, "the shop keeps failing"))
    assert case is not None and any("failures" in m for m in incomplete(case))
