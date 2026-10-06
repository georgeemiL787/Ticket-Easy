"""The shop's published tools, cached per business.

OWNER: Track A.

CapabilityRegistry asks the shop for its tool list (list_tools) at most once per capability_ttl_s seconds per tenant.
If the shop cannot be asked, the last good list is served (marked stale) so a short outage does not stop the chat;
with no earlier list there is nothing to serve and the caller treats the shop as unavailable (dependency_unavailable).
A call that comes back TOOL_NOT_PUBLISHED proves the list is out of date, so drop() forgets it and the next lookup
asks the shop again. Time comes from the injected Clock, never from the real clock.
"""

from dataclasses import dataclass
from datetime import datetime

from team_b.contracts.errors import UpstreamError
from team_b.contracts.tools import ToolSpec
from team_b.observability import get_logger
from team_b.ports import CapabilityClient, Clock

log = get_logger(__name__)


@dataclass(frozen=True)
class Catalog:
    """The tools a tenant's shop publishes, by name. stale: the shop could not be asked, this is the last good list."""

    tools: dict[str, ToolSpec]
    fetched_at: datetime
    stale: bool = False

    def get(self, name: str | None) -> ToolSpec | None:
        return self.tools.get(name) if name else None


class CapabilityRegistry:
    def __init__(self, client: CapabilityClient, clock: Clock, ttl_s: float = 60.0) -> None:
        self._client = client
        self._clock = clock
        self._ttl_s = ttl_s
        self._cache: dict[str, Catalog] = {}

    async def catalog(self, tenant_id: str, *, refresh: bool = False) -> Catalog | None:
        """The published tools, or None when the shop cannot be asked and nothing is remembered."""
        now = self._clock.now()
        cached = self._cache.get(tenant_id)
        if (
            cached is not None
            and not refresh
            and not cached.stale
            and (now - cached.fetched_at).total_seconds() < self._ttl_s
        ):
            return cached
        try:
            tools = await self._client.list_tools(tenant_id)
        except UpstreamError as exc:
            log.warning(
                "capability_discovery_failed", tenant_id=tenant_id, code=exc.code, have_last_good=cached is not None
            )
            if cached is None:
                return None
            stale = Catalog(cached.tools, cached.fetched_at, stale=True)
            self._cache[tenant_id] = stale
            return stale
        fresh = Catalog({t.name: t for t in tools}, now)
        self._cache[tenant_id] = fresh
        return fresh

    def drop(self, tenant_id: str) -> None:
        """Forget the remembered list (a call said a tool is not published): the next catalog() asks the shop again."""
        self._cache.pop(tenant_id, None)
