"""Stand-in for Team A safety screen (placeholder: Phase 2 fills it)."""

from team_b.contracts.evidence import RiskAssessment


class SafetyScreenStandin:
    async def classify_risk(
        self, tenant_id: str, message: str, *, request_id: str, conversation_id: str | None = None
    ) -> RiskAssessment:
        raise NotImplementedError("safety screen stand-in is not built yet (Phase 2)")
