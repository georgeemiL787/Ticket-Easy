"""The connection points between the two tracks: stub signatures, routing through them, and the human methods."""

import inspect
from datetime import UTC, datetime
from typing import Any

import pytest

from team_b.brain import actions, identity, knowledge, lookup
from team_b.brain.handoff import open_case
from team_b.brain.orchestrator import Orchestrator
from team_b.brain.turn import PlannedIntent, Step, TurnContext
from team_b.container import Container
from team_b.domain.actions import ActionProposal, ActionState
from team_b.domain.alerts import Alert
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.handoff import CaseStatus
from team_b.domain.session import SessionIdentity, SessionState
from team_b.domain.understanding import Language, NLUResult
from team_b.ports import NotFoundError
from tests.fakes import FakeEvidence

T, C = "shop_001", "conv-1"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def orch(c: Container, **kw: Any) -> Orchestrator:
    options: dict[str, Any] = {
        "evidence": FakeEvidence(),
        "tenants": c.tenants,
        "capabilities": c.capabilities,
        "events": c.events,
        **kw,
    }
    return Orchestrator(clock=c.clock, sessions=c.sessions, traces=c.traces, cases=c.cases, **options)


def params(fn: Any) -> list[str]:
    return list(inspect.signature(fn).parameters)


# ---- fixed signatures ----


def test_the_stub_signatures_are_the_agreed_ones() -> None:
    assert params(knowledge.answer) == ["ctx"] and params(knowledge.quote_for) == ["ctx", "query"]
    assert params(identity.ensure_verified) == ["ctx"]
    assert params(lookup.answer) == ["ctx", "intent_spec"]
    assert params(actions.handle) == ["ctx", "intent_spec"] and params(actions.on_confirmation) == ["ctx", "nlu"]
    assert params(open_case) == ["ctx", "reason", "detail", "pending_approval"]
    for name, expected in {
        "human_reply": ["case_id", "agent", "text"],
        "human_decide": ["case_id", "agent", "approve", "note"],
        "return_to_agent": ["case_id", "agent", "note"],
        "resolve": ["case_id", "agent", "note"],
        "claim": ["case_id", "agent"],
        "release": ["case_id", "agent"],
    }.items():
        assert params(getattr(Orchestrator, name))[1:] == expected, name


def test_every_stub_names_its_owner() -> None:
    owners = {knowledge: "Track B", identity: "Track A", lookup: "Track A", actions: "Track A"}
    for module, owner in owners.items():
        assert f"OWNER: {owner}" in (module.__doc__ or ""), module.__name__


async def test_the_stubs_return_what_the_pipeline_needs(c: Container) -> None:
    o = orch(c)
    session = SessionState(tenant_id=T, conversation_id=C, created_at=NOW, updated_at=NOW)
    ctx = TurnContext(
        deps=o._deps, tenant=c.tenants.get(T), session=session, text="x", now=NOW, request_id="r", trace_id="t"
    )
    ctx.plan = PlannedIntent("policy_question", "knowledge")
    step = await knowledge.answer(ctx)
    assert isinstance(step, Step) and step.decision is Decision.CLARIFY and "not built yet" in step.reason
    assert await knowledge.quote_for(ctx, "return policy") == []
    asked = await identity.ensure_verified(ctx)  # not verified and no details yet: asks for the first one
    assert isinstance(asked, Step) and (asked.decision, asked.awaiting) == (Decision.VERIFY_IDENTITY, "slot:order_id")
    session.identity = SessionIdentity(verified=True, customer_id="C-100", method="test")
    assert await identity.ensure_verified(ctx) is None
    reading = NLUResult(language=Language.EN, language_confidence=0.9, affirmation="yes")
    confirmed = await actions.on_confirmation(ctx, reading)  # nothing is waiting for a yes: nothing happens
    assert confirmed.decision is Decision.CLARIFY and "no action is waiting" in confirmed.reason


async def test_requests_are_routed_through_the_track_modules(c: Container, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_knowledge(ctx: TurnContext) -> Step:
        calls.append("knowledge")
        return Step(Decision.ANSWER, reason="b", reply_key="thanks")

    async def fake_lookup(ctx: TurnContext, spec: Any) -> Step:
        calls.append(f"lookup:{ctx.plan.name if ctx.plan else ''}")
        return Step(Decision.ANSWER, reason="a", reply_key="thanks")

    async def fake_action(ctx: TurnContext, spec: Any) -> Step:
        calls.append(f"action:{ctx.plan.name if ctx.plan else ''}")
        return Step(Decision.ANSWER, reason="a", reply_key="thanks")

    monkeypatch.setattr(knowledge, "answer", fake_knowledge)
    monkeypatch.setattr(lookup, "answer", fake_lookup)
    monkeypatch.setattr(actions, "handle", fake_action)
    o = orch(c)
    for text in ("What is your return policy?", "Where is my order NS-20877?", "I want a refund for NS-20512"):
        await o.handle_turn(T, f"route-{text[:5]}", text)
    assert calls == ["knowledge", "lookup:order_status", "action:refund_request"]


async def test_a_yes_to_a_pending_action_goes_to_actions_on_confirmation(
    c: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    async def fake_on_confirmation(ctx: TurnContext, nlu: NLUResult) -> Step:
        seen.append(nlu.affirmation or "")
        return Step(Decision.ANSWER, reason="x", reply_key="thanks")

    monkeypatch.setattr(actions, "on_confirmation", fake_on_confirmation)
    o = orch(c)
    await o.handle_turn(T, C, "Hello")
    session = await c.sessions.load(T, C)
    assert session is not None
    proposal = ActionProposal(proposal_id="p1", tool="create_return", capability="create_return", idempotency_key="k")
    proposal.transition(ActionState.AWAITING_CONFIRMATION, at=NOW)
    session.actions.append(proposal)
    session.pending_action_id = "p1"
    await c.sessions.save(session)
    await o.handle_turn(T, C, "yes")
    assert seen == ["yes"]


# ---- open_case ----


async def test_open_case_returns_the_id_and_records_the_pending_approval(c: Container) -> None:
    o = orch(c)
    session = SessionState(tenant_id=T, conversation_id=C, created_at=NOW, updated_at=NOW)
    ctx = TurnContext(
        deps=o._deps, tenant=c.tenants.get(T), session=session, text="x", now=NOW, request_id="r", trace_id="t"
    )
    proposal = ActionProposal(
        proposal_id="p9", tool="create_refund", capability="create_refund", idempotency_key="k9",
        arguments={"order_id": "NS-20934", "amount": 3450},
    )  # fmt: skip
    case_id = await open_case(
        ctx, EscalationReason.APPROVAL_REQUIRED, "refund above the limit", pending_approval=proposal
    )
    case = await c.cases.get(T, case_id)
    assert case is not None and case.status is CaseStatus.OPEN and case.package.priority == "high"
    assert case.pending_approval is not None
    assert (case.pending_approval.proposal_id, case.pending_approval.arguments["amount"]) == ("p9", 3450)
    assert (session.status, session.handoff_case_id) == ("handed_off", case_id)
    plain = await open_case(ctx, EscalationReason.CUSTOMER_REQUEST, "asked")
    assert (await c.cases.get(T, plain)).pending_approval is None  # type: ignore[union-attr]


# ---- human methods ----


async def handed_off(c: Container) -> tuple[Orchestrator, str]:
    o = orch(c)
    reply = await o.handle_turn(T, C, "I want to talk to a human")
    assert reply.handoff_case_id
    return o, reply.handoff_case_id


async def status_of(c: Container, case_id: str) -> CaseStatus:
    case = await c.cases.get(T, case_id)
    assert case is not None
    return case.status


async def test_claim_and_release(c: Container) -> None:
    o, case_id = await handed_off(c)
    await o.claim(case_id, "agent-1")
    case = await c.cases.get(T, case_id)
    assert case is not None and case.status is CaseStatus.CLAIMED and case.claimed_by == "agent-1"
    await o.release(case_id, "agent-1")
    assert await status_of(c, case_id) is CaseStatus.OPEN


async def test_a_reply_is_recorded_and_reaches_the_customer_live(c: Container) -> None:
    o, case_id = await handed_off(c)
    queue = c.events.subscribe(T, C)
    await o.human_reply(case_id, "agent-1", "Hello, I am here.")
    case = await c.cases.get(T, case_id)
    assert case is not None and case.status is CaseStatus.CLAIMED  # replying claims an open case
    assert [(e.actor, e.kind, e.note) for e in case.events if e.kind == "reply"] == [
        ("agent-1", "reply", "Hello, I am here.")
    ]
    session = await c.sessions.load(T, C)
    assert session is not None and [m.text for m in session.outbox] == ["Hello, I am here."]
    assert (await queue.get())["text"] == "Hello, I am here."


async def test_the_chat_goes_back_to_the_agent_after_return_to_agent(c: Container) -> None:
    o, case_id = await handed_off(c)
    await o.claim(case_id, "agent-1")
    await o.return_to_agent(case_id, "agent-1", "all sorted")
    assert await status_of(c, case_id) is CaseStatus.RETURNED_TO_AGENT
    reply = await o.handle_turn(T, C, "Hello again")
    assert reply.decision is Decision.ANSWER  # the assistant is back


async def test_resolve_closes_the_case(c: Container) -> None:
    o, case_id = await handed_off(c)
    await o.resolve(case_id, "agent-1", "done")
    assert await status_of(c, case_id) is CaseStatus.RESOLVED


async def test_the_wrong_moves_are_refused_and_unknown_cases_are_not_found(c: Container) -> None:
    from team_b.domain.handoff import IllegalCaseTransitionError

    o, case_id = await handed_off(c)
    with pytest.raises(IllegalCaseTransitionError):
        await o.return_to_agent(case_id, "agent-1")  # not claimed yet
    with pytest.raises(NotFoundError):
        await o.claim("case-nope", "agent-1")


async def test_human_decide_waits_for_track_a(c: Container) -> None:
    o, case_id = await handed_off(c)
    with pytest.raises(NotImplementedError, match="Track A"):
        await o.human_decide(case_id, "agent-1", True)


# ---- the alert model ----


def test_an_alert_opens_and_resolves() -> None:
    alert = Alert(
        alert_id="a1", tenant_id=T, rule="service_down", severity="critical", opened_at=NOW, details={"service": "shop"}
    )
    assert alert.is_open and alert.acknowledged_by is None
    alert.resolved_at = NOW
    alert.acknowledged_by = "manager-1"
    assert not alert.is_open
    with pytest.raises(ValueError):
        Alert(alert_id="a2", tenant_id=T, rule="x", severity="loud", opened_at=NOW)  # type: ignore[arg-type]
