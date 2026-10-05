"""Retention: old conversations, decision logs and finished cases are deleted on a schedule.

Never deleted: cases that are still open or claimed, and the session and traces of any conversation that has one (a
human may still need the full history to continue).
"""

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from team_b.container import Container
from team_b.domain.handoff import CaseStatus
from team_b.observability import get_logger

log = get_logger(__name__)
DAY_SECONDS = 24 * 60 * 60
_OPEN = (CaseStatus.OPEN, CaseStatus.CLAIMED)


@dataclass(frozen=True)
class PurgeResult:
    sessions: int
    traces: int
    cases: int


async def conversations_with_open_cases(container: Container) -> set[tuple[str, str]]:
    kept: set[tuple[str, str]] = set()
    for tenant_id in container.tenants.tenant_ids():
        for status in _OPEN:
            kept.update((c.tenant_id, c.conversation_id) for c in await container.cases.list(tenant_id, status=status))
    return kept


async def purge_expired(container: Container, retention_days: int | None = None) -> PurgeResult:
    """Delete everything older than retention_days (default: the setting), except what open cases still need."""
    days = retention_days if retention_days is not None else container.settings.retention_days
    cutoff = container.clock.now() - timedelta(days=days)
    keep = await conversations_with_open_cases(container)
    result = PurgeResult(
        sessions=await container.sessions.purge_older_than(cutoff, keep=keep),
        traces=await container.traces.purge_older_than(cutoff, keep=keep),
        cases=await container.cases.purge_older_than(cutoff),
    )
    log.info("retention_purge", days=days, sessions=result.sessions, traces=result.traces, cases=result.cases)
    return result


async def retention_loop(container: Container, *, interval_s: float = DAY_SECONDS) -> None:
    """Purge now, then every interval, until cancelled. A failed purge is logged and tried again next time."""
    while True:
        try:
            await purge_expired(container)
        except Exception:
            log.exception("retention_purge_failed")
        await asyncio.sleep(interval_s)
