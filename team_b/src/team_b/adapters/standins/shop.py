"""Stand-in for Team C shop actions: the fake Nile Style shop (placeholder: Phase 2 fills it)."""

from team_b.contracts.tools import ToolCallRequest, ToolResult, ToolSpec

_MSG = "fake shop stand-in is not built yet (Phase 2)"


class ShopStandin:
    async def list_tools(self, tenant_id: str) -> list[ToolSpec]:
        raise NotImplementedError(_MSG)

    async def call_tool(self, tenant_id: str, request: ToolCallRequest) -> ToolResult:
        raise NotImplementedError(_MSG)
