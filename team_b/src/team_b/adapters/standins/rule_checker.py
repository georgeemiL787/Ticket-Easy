"""Stand-in for Team A rule checker (placeholder: Phase 2 fills it)."""

from team_b.contracts.policy import CheckActionRequest, PolicyDecision


class RuleCheckerStandin:
    async def check_action(self, request: CheckActionRequest) -> PolicyDecision:
        raise NotImplementedError("rule checker stand-in is not built yet (Phase 2)")
