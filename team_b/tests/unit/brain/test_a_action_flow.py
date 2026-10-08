"""The action flow: check, confirm, check again, execute once, verify. Nothing reaches the shop without all of it."""

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from team_b.brain import actions, freetext
from team_b.brain.actions import Verdict, verify_result
from team_b.brain.orchestrator import Orchestrator
from team_b.brain.turn import Step
from team_b.container import Container, build_container, inject
from team_b.contracts.policy import CheckActionRequest, PolicyDecision
from team_b.contracts.tools import ToolCallRequest, ToolResult, ToolSpec
from team_b.domain.actions import ActionState
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.reply import AgentReply
from tests.support import make_settings

T, C = "shop_001", "conv-a7"
C101, C100 = "01123456702", "01012345601"
RETURN_MSG = "I want to return order NS-20790, the size is wrong"


@pytest.fixture
def container(tmp_path: Path) -> Container:
    return build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))


async def say(container: Container, text: str, conversation: str = C, bot: Orchestrator | None = None) -> AgentReply:
    chosen = bot or container.orchestrator
    assert chosen is not None
    return await chosen.handle_turn(T, conversation, text)


async def trace_of(container: Container, reply: AgentReply):  # type: ignore[no-untyped-def]
    trace = await container.traces.get(T, reply.trace_id)
    assert trace is not None
    return trace


async def proposals(container: Container, conversation: str = C):  # type: ignore[no-untyped-def]
    session = await container.sessions.load(T, conversation)
    assert session is not None
    return session.actions


def writes(container: Container) -> list[Any]:
    assert container.shop is not None
    return [
        e for e in container.shop.audit_log(T) if e.tool not in ("verify_customer", "get_order", "list_customer_orders")
    ]


def backend_order(container: Container, order_id: str) -> dict[str, Any]:
    assert container.shop is not None
    return container.shop._tenants[T].shop.orders[order_id]  # type: ignore[attr-defined, no-any-return]


async def to_confirmation(container: Container, **kw: Any) -> AgentReply:
    """The return of NS-20790 asked, identity verified, rules allow: waiting for the customer's yes."""
    await say(container, RETURN_MSG, **kw)
    reply = await say(container, C101, **kw)
    assert reply.decision is Decision.CONFIRM, reply.text
    return reply


# ---- verifying the shop's answer ----


def spec(**kw: Any) -> ToolSpec:
    fields: dict[str, Any] = {
        "name": "t", "capability": "t", "operation_kind": "create",
        "output_schema": {"required": ["reference_id", "status"]},
    }  # fmt: skip
    return ToolSpec(**{**fields, **kw})


def ok(**kw: Any) -> ToolResult:
    fields: dict[str, Any] = {
        "status": "success", "data": {"reference_id": "R-1", "status": "open"},
        "audit_id": "AUD-1", "reference_id": "R-1",
    }  # fmt: skip
    return ToolResult(**{**fields, **kw})


@pytest.mark.parametrize(
    ("result", "tool", "verdict"),
    [
        (ok(), spec(), Verdict.VERIFIED),
        (ok(audit_id=None), spec(), Verdict.UNCERTAIN),  # no audit id
        (ok(reference_id=None), spec(), Verdict.UNCERTAIN),  # a create without a reference
        (ok(reference_id=None), spec(operation_kind="update"), Verdict.VERIFIED),  # an update needs none
        (ok(data={"reference_id": "R-1"}), spec(), Verdict.UNCERTAIN),  # a promised field is missing
        (ok(), None, Verdict.UNCERTAIN),  # cannot tell what to expect
        (ToolResult(status="error", error_code="REJECTED"), spec(), Verdict.FAILED),
        (
            ToolResult(status="error", error_code="OUTCOME_UNKNOWN", write_may_have_applied=True),
            spec(),
            Verdict.UNCERTAIN,
        ),
    ],
)
def test_verify_result(result: ToolResult, tool: ToolSpec | None, verdict: Verdict) -> None:
    assert verify_result(result, tool) is verdict


# ---- the happy path, step by step ----


async def test_a_return_is_confirmed_then_done_with_a_reference(container: Container) -> None:
    first = await say(container, RETURN_MSG)
    assert (first.decision, first.awaiting) == (Decision.VERIFY_IDENTITY, "slot:phone") and writes(container) == []
    confirm = await say(container, C101)
    assert confirm.decision is Decision.CONFIRM and "NS-20790" in confirm.text and writes(container) == []
    assert "return_policy@v2#s2" in confirm.citations
    (proposal,) = await proposals(container)
    assert proposal.state is ActionState.AWAITING_CONFIRMATION and proposal.arguments["reason"] == "the size is wrong"
    done = await say(container, "yes")
    assert done.decision is Decision.EXECUTE and "RET-" in done.text
    (proposal,) = await proposals(container)
    assert proposal.state is ActionState.CONFIRMED and [e.tool for e in writes(container)] == ["create_return"]


async def test_the_confirm_turn_trace_holds_the_rule_answer_and_the_proposal(container: Container) -> None:
    await say(container, RETURN_MSG)
    trace = await trace_of(container, await say(container, C101))
    assert [p.decision for p in trace.policy] == ["allow"] and trace.policy[0].action == "create_return"
    assert [(p.tool, p.state) for p in trace.proposals] == [("create_return", "awaiting_confirmation")]
    assert not any(t.operation_kind != "read" for t in trace.tool_calls)  # nothing written yet


async def test_the_yes_turn_checks_again_and_writes_with_that_check(container: Container) -> None:
    await to_confirmation(container)
    trace = await trace_of(container, await say(container, "yes"))
    (write,) = [t for t in trace.tool_calls if t.operation_kind != "read"]
    assert [p.request_id for p in trace.policy] == [write.policy_request_id]  # the renewed check, made in this turn
    assert [t.tool for t in trace.tool_calls] == ["get_order", "create_return"]  # the order was read again first
    assert (write.actor, write.approval_id) == ("customer", None)
    assert [e.policy_request_id for e in writes(container)] == [write.policy_request_id]


async def test_what_the_rule_checker_is_asked_comes_from_the_shop(container: Container) -> None:
    seen: list[CheckActionRequest] = []
    real = container.policy

    class Spy:
        async def check_action(self, request: CheckActionRequest) -> PolicyDecision:
            seen.append(request)
            return await real.check_action(request)

    bot = Orchestrator(
        clock=container.clock, tenants=container.tenants, sessions=container.sessions, traces=container.traces,
        cases=container.cases, evidence=container.evidence, capabilities=container.capabilities, policy=Spy(),
    )  # fmt: skip
    await say(container, "I want a refund of 5000 EGP for order NS-20745", bot=bot)
    await say(container, C100, bot=bot)
    (request,) = seen
    assert request.action == "create_refund" and request.tenant_id == T and request.as_of == date(2026, 9, 28)
    assert request.arguments == {"order_id": "NS-20745", "amount": 1250}  # the shop's total, not the 5000 asked
    assert request.facts["order_total"] == 1250 and request.facts["days_since_delivery"] == 3
    assert request.identity.verified and request.identity.customer_id == "C-100"
    assert request.tool.risk == "high" and request.tool.side_effects and request.resource_tenant_id == T


# ---- the renewed check and the re-read: things change between the question and the yes ----


async def test_an_order_that_became_ineligible_is_blocked_by_the_renewed_check(container: Container) -> None:
    await to_confirmation(container)
    backend_order(container, "NS-20790")["delivered_at"] = "2026-08-01"  # now 58 days ago
    reply = await say(container, "yes")
    trace = await trace_of(container, reply)
    assert (reply.decision, trace.escalation_reason) == (
        Decision.REFUSE,
        None,
    )  # the first no: explained, a person offered
    (proposal,) = await proposals(container)
    assert proposal.state is ActionState.BLOCKED and writes(container) == []
    assert [p.decision for p in proposal.policy_decisions] == ["allow", "deny"]


async def test_a_changed_amount_is_not_executed_under_the_old_confirmation(container: Container) -> None:
    await say(container, "I want a refund for order NS-20745")
    confirm = await say(container, C100)
    assert confirm.decision is Decision.CONFIRM and "1250" in confirm.text
    backend_order(container, "NS-20745")["order_total"] = 1300
    reply = await say(container, "yes")
    assert reply.decision is Decision.CLARIFY and "1250" not in reply.text and "1300" not in reply.text
    (proposal,) = await proposals(container)
    assert proposal.state is ActionState.BLOCKED and writes(container) == []


async def test_an_order_that_changed_hands_is_not_executed(container: Container) -> None:
    await to_confirmation(container)
    backend_order(container, "NS-20790")["customer_id"] = "C-100"
    reply = await say(container, "yes")
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.OWNERSHIP_MISMATCH and writes(container) == []
    assert (await proposals(container))[0].state is ActionState.BLOCKED


async def test_a_yes_that_needs_a_person_after_the_renewed_check_waits_for_one(container: Container) -> None:
    await say(container, "I want to cancel order NS-20960")
    confirm = await say(container, "01098765405")
    assert confirm.decision is Decision.CONFIRM  # pending: allowed
    backend_order(container, "NS-20960")["order_status"] = "shipped"  # now cancelling needs a person
    reply = await say(container, "yes")
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.APPROVAL_REQUIRED and writes(container) == []
    (proposal,) = await proposals(container)
    assert proposal.state is ActionState.AWAITING_HUMAN


async def test_the_safety_screen_must_answer_in_the_yes_turn_too(container: Container) -> None:
    await to_confirmation(container)
    inject(container, "safety_screen", {"switch": "fail_next", "times": 1})
    reply = await say(container, "yes")
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.DEPENDENCY_UNAVAILABLE and writes(container) == []
    assert (await proposals(container))[0].state is ActionState.BLOCKED


async def test_the_rule_checker_must_answer_the_renewed_check(container: Container) -> None:
    await to_confirmation(container)
    inject(container, "rule_checker", {"switch": "fail_next", "times": 2})  # the check and its one retry
    reply = await say(container, "yes")
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.DEPENDENCY_UNAVAILABLE and writes(container) == []
    assert (await proposals(container))[0].state is ActionState.BLOCKED


async def test_a_rule_checker_that_fails_once_is_asked_again(container: Container) -> None:
    inject(container, "rule_checker", {"switch": "fail_next", "times": 1})
    await say(container, RETURN_MSG)
    reply = await say(container, C101)
    trace = await trace_of(container, reply)
    assert reply.decision is Decision.CONFIRM and "rule checker failed: BACKEND_UNAVAILABLE" in trace.errors


async def test_a_read_failure_on_yes_keeps_the_question_open(container: Container) -> None:
    await to_confirmation(container)
    inject(container, "shop", {"switch": "fail_next", "tool": "get_order", "code": "BACKEND_UNAVAILABLE", "times": 2})
    reply = await say(container, "yes")
    assert reply.decision is Decision.CLARIFY and writes(container) == []
    assert (await proposals(container))[0].state is ActionState.AWAITING_CONFIRMATION
    again = await say(container, "yes")
    assert again.decision is Decision.EXECUTE and len(writes(container)) == 1


# ---- once and only once ----


async def test_a_second_yes_does_not_act_again(container: Container) -> None:
    await to_confirmation(container)
    assert (await say(container, "yes")).decision is Decision.EXECUTE
    again = await say(container, "yes")
    assert again.decision is not Decision.EXECUTE and "RET-" not in again.text
    assert len(writes(container)) == 1 and container.shop is not None and container.shop.change_count(T) == 1


async def test_two_yes_messages_at_the_same_time_write_once(container: Container) -> None:
    await to_confirmation(container)
    replies = await asyncio.gather(say(container, "yes"), say(container, "yes"))
    assert sorted(r.decision.value for r in replies).count("execute") == 1
    assert len(writes(container)) == 1 and container.shop is not None and container.shop.change_count(T) == 1


async def test_a_no_cancels_and_a_later_yes_does_nothing(container: Container) -> None:
    await to_confirmation(container)
    assert (await say(container, "no")).decision is Decision.ANSWER
    later = await say(container, "yes")
    assert later.decision is not Decision.EXECUTE and writes(container) == []
    assert (await proposals(container))[0].state is ActionState.CANCELLED


# ---- the rule checker's answers ----


async def test_a_final_deny_is_a_refusal_with_the_rule_wording_and_no_case(container: Container) -> None:
    await say(container, "I want to return order NS-20822, the colour is wrong")
    reply = await say(container, "01276543207")
    trace = await trace_of(container, reply)
    assert reply.decision is Decision.REFUSE and "cannot be returned" in reply.text and trace.escalation_reason is None
    assert reply.citations == ("return_policy@v2#s4",) and reply.handoff_case_id is None and writes(container) == []


async def test_a_deny_that_a_person_may_reconsider_is_explained_then_handed_off_when_asked_again(
    container: Container,
) -> None:
    await say(container, "I want a refund for order NS-20512")
    reply = await say(container, C100)
    assert reply.decision is Decision.REFUSE and "more than 14 days" in reply.text and reply.handoff_case_id is None
    assert "colleague" in reply.text and "I'll connect you" not in reply.text  # offered, not promised
    again = await say(container, "I want a refund for order NS-20512 anyway")
    trace = await trace_of(container, again)
    assert (again.decision, trace.escalation_reason) == (Decision.HANDOFF, EscalationReason.POLICY_DENIED)
    assert again.handoff_case_id is not None and writes(container) == []


async def test_a_deny_in_arabic_uses_the_arabic_wording(container: Container) -> None:
    await say(container, "عايز اغير عنوان التوصيل للاوردر NS-20877 لـ 5 كورنيش النيل المعادي")
    reply = await say(container, C100)
    assert reply.decision is Decision.REFUSE and "اتشحن" in reply.text


async def test_a_denial_that_comes_from_missing_facts_goes_to_a_person(container: Container) -> None:
    class Blind:
        async def check_action(self, request: CheckActionRequest) -> PolicyDecision:
            return PolicyDecision(request_id=request.request_id, decision="deny", reason_code="MISSING_CONTEXT")

    bot = Orchestrator(
        clock=container.clock, tenants=container.tenants, sessions=container.sessions, traces=container.traces,
        cases=container.cases, evidence=container.evidence, capabilities=container.capabilities, policy=Blind(),
    )  # fmt: skip
    await say(container, RETURN_MSG, bot=bot)
    reply = await say(container, C101, bot=bot)
    assert reply.decision is Decision.HANDOFF and writes(container) == []


async def test_a_person_is_needed_above_the_refund_limit_and_the_proposal_waits(container: Container) -> None:
    await say(container, "I want a refund for order NS-20934")
    reply = await say(container, "01098765405")
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.APPROVAL_REQUIRED and reply.citations == ("refund_policy@v1#s3",)
    (proposal,) = await proposals(container)
    assert proposal.state is ActionState.AWAITING_HUMAN and proposal.arguments["amount"] == 3450
    case = await container.cases.get(T, reply.handoff_case_id or "")
    assert (
        case is not None
        and case.pending_approval is not None
        and case.pending_approval.proposal_id == proposal.proposal_id
    )
    session = await container.sessions.load(T, C)
    assert session is not None and session.pending_action_id is None  # nothing for the customer to confirm


async def test_without_a_rule_checker_nothing_is_done(container: Container) -> None:
    bot = Orchestrator(
        clock=container.clock, tenants=container.tenants, sessions=container.sessions, traces=container.traces,
        cases=container.cases, evidence=container.evidence, capabilities=container.capabilities,
    )  # fmt: skip
    await say(container, RETURN_MSG, bot=bot)
    reply = await say(container, C101, bot=bot)
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.DEPENDENCY_UNAVAILABLE and writes(container) == []


async def test_without_a_safety_screen_nothing_is_done(container: Container) -> None:
    bot = Orchestrator(
        clock=container.clock, tenants=container.tenants, sessions=container.sessions, traces=container.traces,
        cases=container.cases, capabilities=container.capabilities, policy=container.policy,
    )  # fmt: skip
    await say(container, RETURN_MSG, bot=bot)
    reply = await say(container, C101, bot=bot)
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.DEPENDENCY_UNAVAILABLE and writes(container) == []


# ---- confirmation is only asked for the kinds the tenant lists ----


async def test_a_kind_the_tenant_does_not_confirm_runs_straight_after_the_check(container: Container) -> None:
    tenant = container.tenants.get(T)
    relaxed = tenant.model_copy(
        update={"permissions": tenant.permissions.model_copy(update={"confirm_operation_kinds": ("delete",)})}
    )
    from team_b.domain.tenant import TenantRegistry

    bot = Orchestrator(
        clock=container.clock, tenants=TenantRegistry({T: relaxed}), sessions=container.sessions,
        traces=container.traces, cases=container.cases, evidence=container.evidence,
        capabilities=container.capabilities, policy=container.policy,
    )  # fmt: skip
    await say(container, RETURN_MSG, bot=bot)
    reply = await say(container, C101, bot=bot)
    assert reply.decision is Decision.EXECUTE and "RET-" in reply.text and len(writes(container)) == 1


# ---- results are verified before anything is called done ----


async def test_a_clear_refusal_by_the_shop_is_reported_as_failed_and_counted(container: Container) -> None:
    await to_confirmation(container)
    inject(container, "shop", {"switch": "fail_next", "tool": "create_return", "code": "NOT_FOUND", "times": 1})
    reply = await say(container, "yes")
    assert reply.decision is Decision.REFUSE and "Nothing was changed" in reply.text and "RET-" not in reply.text
    (proposal,) = await proposals(container)
    session = await container.sessions.load(T, C)
    assert proposal.state is ActionState.FAILED and session is not None and session.write_failures == 1
    assert len(writes(container)) == 1  # one attempt, no retry


async def test_an_unreachable_shop_on_a_write_is_one_attempt_and_a_clear_failure(container: Container) -> None:
    await to_confirmation(container)
    inject(
        container, "shop", {"switch": "fail_next", "tool": "create_return", "code": "BACKEND_UNAVAILABLE", "times": 3}
    )
    reply = await say(container, "yes")
    assert reply.decision is Decision.REFUSE and len(writes(container)) == 1
    assert container.shop is not None and container.shop.change_count(T) == 0


async def test_a_timeout_on_a_write_is_unclear_and_goes_to_a_person(container: Container) -> None:
    await to_confirmation(container)
    inject(container, "shop", {"switch": "fail_next", "tool": "create_return", "code": "TIMEOUT", "times": 3})
    reply = await say(container, "yes")
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.UNVERIFIED_RESULT and len(writes(container)) == 1
    assert "RET-" not in reply.text and (await proposals(container))[0].state is ActionState.EXECUTED


async def test_repeated_clear_failures_end_in_a_handoff(container: Container) -> None:
    inject(container, "shop", {"switch": "fail_next", "tool": "create_return", "code": "NOT_FOUND", "times": 2})
    await to_confirmation(container)
    first = await say(container, "yes")
    assert first.decision is Decision.REFUSE
    await say(container, RETURN_MSG)  # asks again, a new proposal
    await say(container, "yes")
    again = await say(container, "yes")
    trace = await trace_of(container, again)
    assert again.decision is Decision.HANDOFF and trace.escalation_reason is EscalationReason.REPEATED_TOOL_FAILURE
    assert len(writes(container)) == 2


class Mangling:
    """The real shop with the answer to one write changed."""

    def __init__(self, inner: Any, **changes: Any) -> None:
        self._inner, self._changes = inner, changes

    async def list_tools(self, tenant_id: str) -> list[ToolSpec]:
        tools: list[ToolSpec] = await self._inner.list_tools(tenant_id)
        return tools

    async def call_tool(self, tenant_id: str, request: ToolCallRequest) -> ToolResult:
        result: ToolResult = await self._inner.call_tool(tenant_id, request)
        if request.tool == "create_return" and result.status == "success":
            data = {k: v for k, v in result.data.items() if k not in self._changes.get("drop", ())}
            return result.model_copy(update={"data": data, **{k: v for k, v in self._changes.items() if k != "drop"}})
        return result


@pytest.mark.parametrize("changes", [{"drop": ("status",)}, {"reference_id": None}, {"audit_id": None}])
async def test_a_success_the_shop_cannot_prove_is_never_called_done(
    container: Container, changes: dict[str, Any]
) -> None:
    bot = Orchestrator(
        clock=container.clock, tenants=container.tenants, sessions=container.sessions, traces=container.traces,
        cases=container.cases, evidence=container.evidence, policy=container.policy,
        capabilities=Mangling(container.capabilities, **changes),
    )  # fmt: skip
    await to_confirmation(container, bot=bot)
    reply = await say(container, "yes", bot=bot)
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.UNVERIFIED_RESULT and "RET-" not in reply.text
    assert (await proposals(container))[0].state is ActionState.EXECUTED  # not confirmed


# ---- the safety screen does not block the shop's own voucher; amounts the customer states ----


async def test_a_stated_voucher_amount_is_what_the_rules_judge(container: Container) -> None:
    await say(container, "My order NS-20877 is 4 days late, I want a compensation voucher of 300 EGP")
    reply = await say(container, C100)
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.APPROVAL_REQUIRED
    (proposal,) = await proposals(container)
    assert proposal.arguments["amount"] == 300 and proposal.state is ActionState.AWAITING_HUMAN


async def test_a_voucher_without_a_stated_amount_uses_the_default(container: Container) -> None:
    await say(container, "My order NS-20877 is late, I want a voucher", conversation="c-v")
    reply = await say(container, C100, conversation="c-v")
    assert reply.decision is Decision.CONFIRM and "100" in reply.text


async def test_a_smaller_stated_voucher_amount_is_used(container: Container) -> None:
    await say(container, "My order NS-20877 is late, I want a voucher of 50 EGP", conversation="c-v")
    reply = await say(container, C100, conversation="c-v")
    assert reply.decision is Decision.CONFIRM and "50" in reply.text and "100" not in reply.text


# ---- topic change and the safety screen, with a stand-in for the knowledge answers (Track B's) ----


async def knowledge_standin(ctx: Any, planned: Any) -> Step:
    return Step(Decision.ANSWER, reason="stand-in", reply_key="greeting", citations=("shipping_policy@v1#s3",))


def with_knowledge(container: Container) -> Orchestrator:
    return Orchestrator(
        clock=container.clock, tenants=container.tenants, sessions=container.sessions, traces=container.traces,
        cases=container.cases, evidence=container.evidence, capabilities=container.capabilities,
        policy=container.policy, handlers={"knowledge": knowledge_standin},
    )  # fmt: skip


async def test_a_topic_change_cancels_the_pending_action_and_a_later_yes_does_nothing(container: Container) -> None:
    bot = with_knowledge(container)
    await to_confirmation(container, bot=bot)
    answer = await say(container, "actually, how much does shipping cost?", bot=bot)
    assert answer.decision is Decision.ANSWER and "RET-" not in answer.text
    assert (await proposals(container))[0].state is ActionState.CANCELLED
    later = await say(container, "yes", bot=bot)
    assert later.decision is not Decision.EXECUTE and "RET-" not in later.text and writes(container) == []


async def test_with_the_safety_screen_down_questions_are_answered_but_actions_are_blocked(container: Container) -> None:
    bot = with_knowledge(container)
    inject(container, "safety_screen", {"switch": "fail_next", "times": 10})
    question = await say(container, "What is your return policy?", bot=bot)
    assert question.decision is Decision.ANSWER
    await say(container, "I want a refund for order NS-20745", bot=bot)
    reply = await say(container, C100, bot=bot)
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.DEPENDENCY_UNAVAILABLE and writes(container) == []


# ---- free-text details ----


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("I want to return order NS-20790, the size is wrong", "the size is wrong"),
        ("I want to exchange order NS-20790 for a bigger size, it is too small", "it is too small"),
        ("I want to return it because it is damaged", "it is damaged"),
        ("عايز ارجع الاوردر NS-20790 لان المقاس مش مظبوط", "المقاس مش مظبوط"),
        ("3ayez araga3 el order 3shan el lon mesh mazboot", "el lon mesh mazboot"),
        ("I want to return order NS-20790, wrong size. My phone is 01123456702", "wrong size"),
        ("Hi, I want to return it", None),  # the second clause is the request, not a reason
        ("I want to return order NS-20790", None),
        ("Hello, my phone is 01123456702", None),
    ],
)
def test_a_reason_in_the_first_message(text: str, reason: str | None) -> None:
    assert freetext.reason_from(text) == reason


@pytest.mark.parametrize(
    ("text", "address"),
    [
        ("I want to change the delivery address of order NS-20877 to 5 Nile Corniche, Maadi", "5 Nile Corniche, Maadi"),
        ("please deliver it to 12 Tahrir Street Dokki", "12 Tahrir Street Dokki"),
        ("I want to change the address of order NS-20877", None),
        ("send it to my office", None),  # no number: not taken as an address
    ],
)
def test_an_address_in_the_first_message(text: str, address: str | None) -> None:
    assert freetext.address_from(text) == address


def test_the_answer_to_a_question_is_the_whole_message() -> None:
    assert freetext.answer_to("new_address", "العنوان الجديد 5 كورنيش النيل المعادي") == "5 كورنيش النيل المعادي"
    assert freetext.answer_to("new_address", "the new address is 5 Nile Corniche") == "5 Nile Corniche"
    assert freetext.answer_to("reason", "  it does not fit  ") == "it does not fit"
    assert freetext.answer_to("reason", "   ") is None
    assert len(freetext.answer_to("reason", "x" * 1000) or "") == freetext.MAX_LENGTH


def test_capture_only_fills_what_the_request_needs() -> None:
    text = "I want to return order NS-20790, the size is wrong"
    assert freetext.capture(text, {"order_id"}, None, other_intent=False, entities={}) == {}
    assert freetext.capture(text, {"reason"}, None, other_intent=False, entities={}) == {"reason": "the size is wrong"}


def test_capture_takes_the_answer_to_the_question_that_was_asked() -> None:
    got = freetext.capture("it is too small", {"reason"}, "slot:reason", other_intent=False, entities={})
    assert got == {"reason": "it is too small"}
    assert (
        freetext.capture("NS-20790", {"reason"}, "slot:reason", other_intent=False, entities={"order_id": "NS-20790"})
        == {}
    )
    other = freetext.capture("how much is shipping", {"reason"}, "slot:reason", other_intent=True, entities={})
    assert "reason" not in other  # a different request is not an answer


async def test_the_reason_is_asked_for_when_none_was_given(container: Container) -> None:
    await say(container, "I want to return order NS-20790")
    asked = await say(container, C101)
    assert (asked.decision, asked.awaiting) == (Decision.CLARIFY, "slot:reason")
    confirm = await say(container, "it does not fit")
    assert confirm.decision is Decision.CONFIRM
    assert (await proposals(container))[0].arguments["reason"] == "it does not fit"


async def test_a_new_address_is_asked_for_then_confirmed_with_the_address(container: Container) -> None:
    await say(container, "I want to change the address of order NS-20955")
    asked = await say(container, "01187654306")
    assert (asked.decision, asked.awaiting) == (Decision.CLARIFY, "slot:new_address")
    confirm = await say(container, "5 Nile Corniche, Maadi")
    assert confirm.decision is Decision.CONFIRM and "5 Nile Corniche, Maadi" in confirm.text
    done = await say(container, "yes")
    assert done.decision is Decision.EXECUTE and "ADR-" in done.text


def test_the_flow_is_documented_as_safety_critical() -> None:
    assert "needs a second reviewer" in (actions.__doc__ or "")
