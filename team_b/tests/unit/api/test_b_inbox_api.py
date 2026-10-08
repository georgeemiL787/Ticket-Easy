"""The inbox API: listing, filtering, paging, and every rule of the case life cycle."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import FastAPI

from team_b.container import Container
from team_b.domain.actions import ActionProposal, ActionState
from team_b.domain.decision import EscalationReason
from team_b.domain.handoff import CaseStatus, HandoffCase, HandoffPackage, PendingApproval

T = "shop_001"
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
BASE = "/v1/handoff/cases"


async def hand_off(chat: httpx.AsyncClient, conversation: str = "c1", text: str = "I want to talk to a human") -> str:
    reply = await chat.post(f"/v1/conversations/{conversation}/messages", json={"tenant_id": T, "text": text})
    assert reply.status_code == 200, reply.text
    case_id: str = reply.json()["handoff_case_id"]
    assert case_id
    return case_id


async def post(chat: httpx.AsyncClient, case_id: str, action: str, **body: Any) -> httpx.Response:
    return await chat.post(f"{BASE}/{case_id}/{action}", json=body)


def error_code(response: httpx.Response) -> str:
    code: str = response.json()["error"]["code"]
    return code


async def add_case(
    container: Container, case_id: str, priority: str, minutes: int, reason: EscalationReason, **over: Any
) -> HandoffCase:
    package = HandoffPackage(
        summary=f"case {case_id}",
        reason=reason,
        priority=priority,
        suggested_next_step="look",  # type: ignore[arg-type]
    )
    case = HandoffCase(
        case_id=case_id, tenant_id=T, conversation_id=f"conv-{case_id}", package=package,
        created_at=NOW + timedelta(minutes=minutes), updated_at=NOW + timedelta(minutes=minutes), **over,
    )  # fmt: skip
    await container.cases.add(case)
    return case


# ---- listing ----


async def test_the_list_is_sorted_by_priority_then_age_and_can_be_filtered(
    chat: httpx.AsyncClient, chat_container: Container
) -> None:
    R = EscalationReason
    await add_case(chat_container, "n-old", "normal", 0, R.NO_EVIDENCE)
    await add_case(chat_container, "n-new", "normal", 5, R.POLICY_DENIED)
    await add_case(chat_container, "u-new", "urgent", 9, R.MANDATORY_RISK)
    await add_case(chat_container, "h-old", "high", 1, R.DEPENDENCY_UNAVAILABLE)
    page = (await chat.get(BASE, params={"tenant_id": T})).json()
    assert [c["case_id"] for c in page["items"]] == ["u-new", "h-old", "n-old", "n-new"]
    assert page["next_cursor"] is None
    row = page["items"][0]
    assert (row["reason"], row["priority"], row["status"], row["claimed_by"]) == (
        "mandatory_risk",
        "urgent",
        "open",
        None,
    )

    async def listed(**params: str) -> list[str]:
        found = (await chat.get(BASE, params={"tenant_id": T, **params})).json()["items"]
        return [c["case_id"] for c in found]

    assert await listed(priority="normal") == ["n-old", "n-new"]
    assert await listed(reason="policy_denied") == ["n-new"]
    assert await listed(status="claimed") == []
    assert await post(chat, "h-old", "claim", agent="sara")  # claim one, then filter by claimer and status
    assert await listed(claimed_by="sara") == ["h-old"] and await listed(status="claimed") == ["h-old"]
    assert await listed(status="open") == ["u-new", "n-old", "n-new"]


async def test_the_list_pages_with_a_cursor_and_does_not_skip_or_repeat(
    chat: httpx.AsyncClient, chat_container: Container
) -> None:
    for number in range(5):
        await add_case(chat_container, f"p{number}", "normal", number, EscalationReason.NO_EVIDENCE)
    seen, cursor = [], None
    for _ in range(5):
        params: dict[str, Any] = {"tenant_id": T, "limit": 2, **({"cursor": cursor} if cursor else {})}
        page = (await chat.get(BASE, params=params)).json()
        seen += [c["case_id"] for c in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert seen == ["p0", "p1", "p2", "p3", "p4"]
    bad = await chat.get(BASE, params={"tenant_id": T, "cursor": "not-a-cursor"})
    assert (bad.status_code, error_code(bad)) == (422, "INVALID_REQUEST")


async def test_the_list_checks_its_parameters(chat: httpx.AsyncClient) -> None:
    assert (await chat.get(BASE, params={"tenant_id": "nope"})).status_code == 404
    assert (await chat.get(BASE)).status_code == 422
    assert (await chat.get(BASE, params={"tenant_id": T, "status": "weird"})).status_code == 422
    assert (await chat.get(BASE, params={"tenant_id": T, "limit": 0})).status_code == 422


async def test_one_case_shows_the_briefing_and_unknown_cases_are_404(chat: httpx.AsyncClient) -> None:
    case_id = await hand_off(chat)
    case = (await chat.get(f"{BASE}/{case_id}")).json()
    assert case["case_id"] == case_id and case["package"]["reason"] == "customer_request"
    assert case["package"]["transcript"][0]["text"] == "I want to talk to a human"
    missing = await chat.get(f"{BASE}/case-nope")
    assert (missing.status_code, error_code(missing)) == (404, "NOT_FOUND")
    assert (await post(chat, "case-nope", "claim", agent="sara")).status_code == 404


# ---- the life cycle ----


async def test_claim_reply_and_resolve_record_every_step(chat: httpx.AsyncClient) -> None:
    case_id = await hand_off(chat)
    claimed = (await post(chat, case_id, "claim", agent="sara")).json()
    assert (claimed["status"], claimed["claimed_by"]) == ("claimed", "sara")
    replied = (await post(chat, case_id, "reply", agent="sara", text="Hello, I will check this.")).json()
    resolved = (await post(chat, case_id, "resolve", agent="sara", note="done")).json()
    assert replied["status"] == "claimed" and resolved["status"] == "resolved"
    events = [(e["actor"], e["kind"], e["note"]) for e in resolved["events"]]
    assert events == [
        ("sara", "claimed", ""), ("sara", "reply", "Hello, I will check this."), ("sara", "resolved", "done"),
    ]  # fmt: skip
    again = await post(chat, case_id, "claim", agent="sara")
    assert (again.status_code, error_code(again)) == (409, "INVALID_STATE")  # a resolved case never reopens


async def test_only_the_claimer_can_reply_decide_resolve_or_return(chat: httpx.AsyncClient) -> None:
    case_id = await hand_off(chat)
    for action, body in [("reply", {"text": "hi"}), ("resolve", {}), ("return-to-agent", {}), ("release", {})]:
        early = await post(chat, case_id, action, agent="sara", **body)  # nobody has claimed it yet
        assert (early.status_code, error_code(early)) == (409, "INVALID_STATE"), action
    assert (await post(chat, case_id, "claim", agent="sara")).status_code == 200
    taken = await post(chat, case_id, "claim", agent="omar")
    assert (taken.status_code, error_code(taken)) == (409, "INVALID_STATE")
    for action, body in [
        ("reply", {"text": "hi"}), ("resolve", {}), ("return-to-agent", {}), ("release", {}),
        ("decision", {"approve": True}),
    ]:  # fmt: skip
        other = await post(chat, case_id, action, agent="omar", **body)
        assert (other.status_code, error_code(other)) == (409, "INVALID_STATE"), action
        assert "claimed by sara" in other.json()["error"]["message"]
    assert (await chat.get(f"{BASE}/{case_id}")).json()["claimed_by"] == "sara"


async def test_release_puts_the_case_back_in_the_queue(chat: httpx.AsyncClient) -> None:
    case_id = await hand_off(chat)
    await post(chat, case_id, "claim", agent="sara")
    released = (await post(chat, case_id, "release", agent="sara")).json()
    assert (released["status"], released["claimed_by"]) == ("open", None)
    assert [e["kind"] for e in released["events"]] == ["claimed", "released"]
    assert (await post(chat, case_id, "claim", agent="omar")).json()["claimed_by"] == "omar"


async def test_a_human_reply_reaches_the_customers_outbox_and_stream(
    chat: httpx.AsyncClient, chat_container: Container
) -> None:
    case_id = await hand_off(chat)
    queue = chat_container.events.subscribe(T, "c1")
    await post(chat, case_id, "claim", agent="sara")
    await post(chat, case_id, "reply", agent="sara", text="I am here.")
    outbox = (await chat.get("/v1/conversations/c1/outbox", params={"tenant_id": T})).json()
    assert [m["text"] for m in outbox] == ["I am here."] and outbox[0]["role"] == "human_agent"
    assert (await queue.get())["text"] == "I am here."


async def test_returning_the_chat_lets_the_agent_continue_with_its_memory(chat: httpx.AsyncClient) -> None:
    await chat.post("/v1/conversations/c1/messages", json={"tenant_id": T, "text": "Where is order NS-20877?"})
    case_id = await hand_off(chat)
    before = (await chat.get("/v1/conversations/c1", params={"tenant_id": T})).json()
    assert before["status"] == "handed_off"
    await post(chat, case_id, "claim", agent="sara")
    returned = (await post(chat, case_id, "return-to-agent", agent="sara", note="back to you")).json()
    assert returned["status"] == "returned_to_agent"
    after = (await chat.get("/v1/conversations/c1", params={"tenant_id": T})).json()
    assert (after["status"], after["handoff_case_id"]) == ("active", None)
    assert after["slots"] == before["slots"] and after["intents_seen"] == before["intents_seen"]
    answer = await chat.post(
        "/v1/conversations/c1/messages", json={"tenant_id": T, "text": "How many days do I have to return an item?"}
    )
    assert answer.json()["decision"] == "answer"


async def test_the_customer_is_told_a_colleague_will_reply_while_a_person_owns_the_case(
    chat: httpx.AsyncClient,
) -> None:
    case_id = await hand_off(chat)
    await post(chat, case_id, "claim", agent="sara")
    again = await chat.post("/v1/conversations/c1/messages", json={"tenant_id": T, "text": "hello??"})
    assert again.json()["decision"] == "handoff"
    events = (await chat.get(f"{BASE}/{case_id}")).json()["events"]
    assert [e["kind"] for e in events] == ["claimed", "customer_message"]


async def test_bad_bodies_are_rejected_with_the_error_format(chat: httpx.AsyncClient) -> None:
    case_id = await hand_off(chat)
    for action, body in [
        ("claim", {}), ("claim", {"agent": "  "}), ("claim", {"agent": "x" * 81}),
        ("claim", {"agent": "sara", "extra": 1}),
        ("reply", {"agent": "sara"}), ("reply", {"agent": "sara", "text": "   "}), ("decision", {"agent": "sara"}),
    ]:  # fmt: skip
        response = await post(chat, case_id, action, **body)
        assert (response.status_code, error_code(response)) == (422, "INVALID_REQUEST"), (action, body)


# ---- the decision endpoint (approval is Track A's) ----


async def with_waiting_action(container: Container, case_id: str) -> None:
    case = await container.cases.get(T, case_id)
    assert case is not None
    case.pending_approval = PendingApproval(
        proposal_id="p1", tool="create_refund", capability="create_refund", arguments={"amount": 3450}
    )
    await container.cases.save(case)


async def test_the_decision_needs_an_action_that_is_waiting(chat: httpx.AsyncClient) -> None:
    case_id = await hand_off(chat)
    await post(chat, case_id, "claim", agent="sara")
    response = await post(chat, case_id, "decision", agent="sara", approve=True)
    assert (response.status_code, error_code(response)) == (409, "INVALID_STATE")


class FakeOrchestrator:
    """Stands in for the real orchestrator to test the decision endpoint on its own."""

    def __init__(self, real: Any) -> None:
        self.real, self.decisions = real, []

    async def human_decide(self, case_id: str, agent: str, approve: bool, note: str | None = None) -> None:
        self.decisions.append((case_id, agent, approve, note))

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real, name)


async def test_a_decision_from_the_claimer_reaches_the_orchestrator(
    chat_container: Container, chat: httpx.AsyncClient, chat_app: FastAPI
) -> None:
    case_id = await hand_off(chat)
    await post(chat, case_id, "claim", agent="sara")
    await with_waiting_action(chat_container, case_id)
    fake = FakeOrchestrator(chat_container.orchestrator)
    chat_app.state.container = replace(chat_container, orchestrator=fake)  # type: ignore[arg-type]
    response = await post(chat, case_id, "decision", agent="sara", approve=False, note="too late")
    assert response.status_code == 200 and fake.decisions == [(case_id, "sara", False, "too late")]
    other = await post(chat, case_id, "decision", agent="omar", approve=True)
    assert other.status_code == 409 and len(fake.decisions) == 1


def test_the_pending_proposal_model_is_what_the_case_carries() -> None:
    proposal = ActionProposal(proposal_id="p1", tool="t", capability="c", idempotency_key="k")
    assert proposal.state is ActionState.PROPOSED and CaseStatus.OPEN.value == "open"


async def test_active_keeps_only_cases_that_still_need_work(chat: httpx.AsyncClient, chat_container: Container) -> None:
    R = EscalationReason
    await add_case(chat_container, "done", "normal", 0, R.NO_EVIDENCE, status=CaseStatus.RESOLVED)
    await add_case(chat_container, "waiting", "normal", 1, R.NO_EVIDENCE)
    await add_case(chat_container, "mine", "normal", 2, R.NO_EVIDENCE, status=CaseStatus.CLAIMED, claimed_by="sara")
    everything = (await chat.get(BASE, params={"tenant_id": T})).json()["items"]
    active = (await chat.get(BASE, params={"tenant_id": T, "active": "true"})).json()["items"]
    assert [c["case_id"] for c in everything] == ["done", "waiting", "mine"]
    assert [c["case_id"] for c in active] == ["waiting", "mine"]


async def test_new_shows_untouched_cases_newest_first(chat: httpx.AsyncClient, chat_container: Container) -> None:
    R = EscalationReason
    await add_case(chat_container, "old", "normal", 0, R.NO_EVIDENCE)
    await add_case(chat_container, "taken", "urgent", 1, R.NO_EVIDENCE, status=CaseStatus.CLAIMED, claimed_by="sara")
    await add_case(chat_container, "fresh", "low", 9, R.NO_EVIDENCE)
    new = (await chat.get(BASE, params={"tenant_id": T, "new": "true"})).json()["items"]
    assert [c["case_id"] for c in new] == ["fresh", "old"]
