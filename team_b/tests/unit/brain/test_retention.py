import asyncio
from datetime import timedelta

import pytest

from team_b.config import Settings
from team_b.container import Container
from team_b.domain.decision import EscalationReason
from team_b.domain.handoff import CaseStatus, HandoffCase, HandoffPackage
from team_b.retention import purge_expired, retention_loop

T = "shop_001"


def make_case(container: Container, conversation_id: str) -> HandoffCase:
    now = container.clock.now()
    package = HandoffPackage(
        summary="s", reason=EscalationReason.CUSTOMER_REQUEST, priority="normal", suggested_next_step="reply"
    )
    return HandoffCase(
        case_id=f"case-{conversation_id}",
        tenant_id=T,
        conversation_id=conversation_id,
        package=package,
        created_at=now,
        updated_at=now,
    )


def test_retention_defaults_to_90_days_and_reads_the_environment() -> None:
    assert Settings().retention_days == 90
    assert Settings.from_env({"TEAM_B_RETENTION_DAYS": "30"}).retention_days == 30


def test_retention_must_be_at_least_one_day() -> None:
    with pytest.raises(ValueError, match="TEAM_B_RETENTION_DAYS"):
        Settings.from_env({"TEAM_B_RETENTION_DAYS": "0"})


async def test_old_conversations_are_purged_and_recent_ones_stay(c: Container) -> None:
    assert c.orchestrator is not None
    await c.orchestrator.handle_turn(T, "old", "hi")
    c.clock.advance(60)  # type: ignore[attr-defined]
    await c.orchestrator.handle_turn(T, "recent", "hi")
    c.clock.advance(40)  # type: ignore[attr-defined]  # "old" is 100 days old, "recent" 40
    result = await purge_expired(c)  # default: 90 days
    assert (result.sessions, result.traces, result.cases) == (1, 1, 0)
    assert await c.sessions.load(T, "old") is None and await c.sessions.load(T, "recent") is not None
    assert await c.traces.for_conversation(T, "old") == []


async def test_a_conversation_with_an_open_case_is_never_purged(c: Container) -> None:
    assert c.orchestrator is not None
    for conversation in ("open", "claimed", "done"):
        await c.orchestrator.handle_turn(T, conversation, "hi")
        await c.cases.add(make_case(c, conversation))
    claimed = await c.cases.get(T, "case-claimed")
    done = await c.cases.get(T, "case-done")
    assert claimed is not None and done is not None
    claimed.transition(CaseStatus.CLAIMED, actor="agent", at=c.clock.now())
    done.transition(CaseStatus.RESOLVED, actor="agent", at=c.clock.now())
    await c.cases.save(claimed)
    await c.cases.save(done)

    c.clock.advance(400)  # type: ignore[attr-defined]
    result = await purge_expired(c)
    assert result.cases == 1  # only the resolved one
    for conversation in ("open", "claimed"):
        assert await c.sessions.load(T, conversation) is not None
        assert len(await c.traces.for_conversation(T, conversation)) == 1
        assert await c.cases.get(T, f"case-{conversation}") is not None
    assert await c.sessions.load(T, "done") is None and await c.traces.for_conversation(T, "done") == []


async def test_a_shorter_retention_can_be_given_explicitly(c: Container) -> None:
    assert c.orchestrator is not None
    await c.orchestrator.handle_turn(T, "x", "hi")
    c.clock.advance(3)  # type: ignore[attr-defined]
    assert (await purge_expired(c, retention_days=7)).sessions == 0
    assert (await purge_expired(c, retention_days=2)).sessions == 1
    assert timedelta(days=2) < timedelta(days=3)


async def test_the_loop_purges_repeatedly_and_survives_a_failure(c: Container, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def flaky(container: Container) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("disk full")

    monkeypatch.setattr("team_b.retention.purge_expired", flaky)
    task = asyncio.create_task(retention_loop(c, interval_s=0.01))
    await asyncio.sleep(0.2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert calls >= 2  # the first run failed and it carried on
