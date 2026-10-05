from datetime import UTC, datetime, timedelta

import pytest

from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.handoff import CaseStatus, HandoffCase, HandoffPackage
from team_b.domain.trace import DecisionTrace
from team_b.ports import AlreadyExistsError, CaseStore, NotFoundError, TraceStore

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def trace(trace_id: str, *, tenant: str = "shop_001", conversation: str = "conv-1", **over: object) -> DecisionTrace:
    base: dict[str, object] = {
        "trace_id": trace_id,
        "request_id": f"req-{trace_id}",
        "tenant_id": tenant,
        "conversation_id": conversation,
        "turn_index": 0,
        "decision": Decision.ANSWER,
    }
    return DecisionTrace.model_validate({**base, **over})


def case(case_id: str, *, tenant: str = "shop_001", at: datetime = NOW) -> HandoffCase:
    package = HandoffPackage(
        summary="s", reason=EscalationReason.POLICY_DENIED, priority="normal", suggested_next_step="look"
    )
    return HandoffCase(
        case_id=case_id,
        tenant_id=tenant,
        conversation_id=f"conv-{case_id}",
        package=package,
        created_at=at,
        updated_at=at,
    )


# --- trace store ---


async def test_trace_add_and_get(trace_store: TraceStore) -> None:
    store = trace_store
    t = trace("t1")
    await store.add(t)
    assert await store.get("shop_001", "t1") == t
    assert await store.get("shop_001", "nope") is None
    assert await store.get("shop_002", "t1") is None  # another tenant cannot read it


async def test_trace_duplicate_id_is_rejected(trace_store: TraceStore) -> None:
    store = trace_store
    await store.add(trace("t1"))
    with pytest.raises(AlreadyExistsError):
        await store.add(trace("t1"))
    await store.add(trace("t1", tenant="shop_002"))  # the same id in another tenant is fine


async def test_for_conversation_is_oldest_first_and_scoped(trace_store: TraceStore) -> None:
    store = trace_store
    for n in range(3):
        await store.add(trace(f"t{n}", turn_index=n))
    await store.add(trace("other", conversation="conv-2"))
    await store.add(trace("foreign", tenant="shop_002"))
    found = await store.for_conversation("shop_001", "conv-1")
    assert [t.trace_id for t in found] == ["t0", "t1", "t2"]
    assert await store.for_conversation("shop_001", "missing") == []


async def test_query_filters_and_orders_newest_first(trace_store: TraceStore) -> None:
    store = trace_store
    await store.add(trace("a"))
    await store.add(trace("b", decision=Decision.HANDOFF, escalation_reason=EscalationReason.POLICY_DENIED))
    await store.add(trace("c", decision=Decision.HANDOFF, escalation_reason=EscalationReason.NO_EVIDENCE))
    await store.add(trace("d", conversation="conv-2"))
    await store.add(trace("x", tenant="shop_002"))
    assert [t.trace_id for t in await store.query("shop_001")] == ["d", "c", "b", "a"]
    assert [t.trace_id for t in await store.query("shop_001", decision=Decision.HANDOFF)] == ["c", "b"]
    only = await store.query("shop_001", escalation_reason=EscalationReason.POLICY_DENIED)
    assert [t.trace_id for t in only] == ["b"]
    assert [t.trace_id for t in await store.query("shop_001", conversation_id="conv-2")] == ["d"]
    assert [t.trace_id for t in await store.query("shop_001", limit=2)] == ["d", "c"]


# --- case store ---


async def test_case_add_get_and_isolation(case_store: CaseStore) -> None:
    store = case_store
    await store.add(case("c1"))
    got = await store.get("shop_001", "c1")
    assert got is not None and got.case_id == "c1"
    assert await store.get("shop_002", "c1") is None
    assert await store.get("shop_001", "nope") is None


async def test_case_add_twice_and_save_missing(case_store: CaseStore) -> None:
    store = case_store
    await store.add(case("c1"))
    with pytest.raises(AlreadyExistsError):
        await store.add(case("c1"))
    with pytest.raises(NotFoundError):
        await store.save(case("never-added"))


async def test_case_save_persists_changes_and_get_returns_copies(case_store: CaseStore) -> None:
    store = case_store
    await store.add(case("c1"))
    c = await store.get("shop_001", "c1")
    assert c is not None
    c.transition(CaseStatus.CLAIMED, actor="staff-1", at=NOW + timedelta(minutes=1))
    unchanged = await store.get("shop_001", "c1")
    assert unchanged is not None and unchanged.status is CaseStatus.OPEN  # not saved yet
    await store.save(c)
    saved = await store.get("shop_001", "c1")
    assert saved is not None and saved.status is CaseStatus.CLAIMED and saved.claimed_by == "staff-1"


async def test_case_list_filters_and_is_oldest_first(case_store: CaseStore) -> None:
    store = case_store
    await store.add(case("late", at=NOW + timedelta(hours=2)))
    await store.add(case("early", at=NOW))
    await store.add(case("mid", at=NOW + timedelta(hours=1)))
    await store.add(case("foreign", tenant="shop_002"))
    resolved = case("done", at=NOW + timedelta(hours=3))
    resolved.transition(CaseStatus.RESOLVED, actor="staff-1", at=NOW + timedelta(hours=4))
    await store.add(resolved)
    assert [c.case_id for c in await store.list("shop_001")] == ["early", "mid", "late", "done"]
    assert [c.case_id for c in await store.list("shop_001", status=CaseStatus.OPEN)] == ["early", "mid", "late"]
    assert [c.case_id for c in await store.list("shop_001", status=CaseStatus.RESOLVED)] == ["done"]
    assert await store.list("shop_003") == []
