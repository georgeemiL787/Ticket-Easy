"""The EvidenceProvider plug: policy search and the safety screen behind one interface."""

from team_b.adapters.standins.policy_search import PolicySearchStandin
from team_b.adapters.standins.safety_screen import SafetyScreenStandin
from team_b.contracts.evidence import Passage, PastTicketResult, RetrievalResult, RiskAssessment


class StandinEvidenceProvider:
    def __init__(self, search: PolicySearchStandin, safety: SafetyScreenStandin) -> None:
        self._search = search
        self._safety = safety

    async def search_knowledge(
        self,
        tenant_id: str,
        query: str,
        *,
        request_id: str,
        conversation_id: str | None = None,
        top_k: int = 5,
    ) -> RetrievalResult:
        return await self._search.search_knowledge(
            tenant_id, query, request_id=request_id, conversation_id=conversation_id, top_k=top_k
        )

    async def get_passage(self, tenant_id: str, citation: str) -> Passage | None:
        return await self._search.get_passage(tenant_id, citation)

    async def search_past_tickets(
        self, tenant_id: str, query: str, *, request_id: str, top_k: int = 3
    ) -> PastTicketResult:
        return await self._search.search_past_tickets(tenant_id, query, request_id=request_id, top_k=top_k)

    async def classify_risk(
        self, tenant_id: str, message: str, *, request_id: str, conversation_id: str | None = None
    ) -> RiskAssessment:
        return await self._safety.classify_risk(
            tenant_id, message, request_id=request_id, conversation_id=conversation_id
        )
