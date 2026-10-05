"""Stand-in for Team A policy search (placeholder: Phase 2 fills it)."""

from team_b.contracts.evidence import Passage, PastTicketResult, RetrievalResult

_MSG = "policy search stand-in is not built yet (Phase 2)"


class PolicySearchStandin:
    async def search_knowledge(
        self,
        tenant_id: str,
        query: str,
        *,
        request_id: str,
        conversation_id: str | None = None,
        top_k: int = 5,
    ) -> RetrievalResult:
        raise NotImplementedError(_MSG)

    async def get_passage(self, tenant_id: str, citation: str) -> Passage | None:
        raise NotImplementedError(_MSG)

    async def search_past_tickets(
        self, tenant_id: str, query: str, *, request_id: str, top_k: int = 3
    ) -> PastTicketResult:
        raise NotImplementedError(_MSG)
