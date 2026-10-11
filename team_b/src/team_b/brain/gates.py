"""The permission gate: may the agent use this tool at all? (before any rule about this particular order)

OWNER: Track A.

A tool passes only if the shop has it enabled, it is not reserved for people (human_only), the business allows it
(allowed_tools names it, or contains "*"), and its risk is not above the business's max_risk. The default allows
nothing. A failing tool is never used: the conversation goes to a person (handoff "unsupported") with the reason.
"""

from dataclasses import dataclass

from team_b.contracts.tools import ToolSpec
from team_b.domain.tenant import TenantConfig

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
REASONS = ("disabled", "human_only", "not_allowed", "risk_too_high")


@dataclass(frozen=True)
class GateResult:
    allowed: bool
    reason: str | None = None  # one of REASONS when not allowed
    detail: str = ""


def check_tool(tenant: TenantConfig, tool: ToolSpec) -> GateResult:
    """The first failing check decides (shop switches first, then the business's permissions)."""
    permissions = tenant.permissions
    if not tool.enabled:
        return GateResult(False, "disabled", f"{tool.name} is switched off in the shop")
    if tool.human_only:
        return GateResult(False, "human_only", f"{tool.name} may only be used by a person")
    if "*" not in permissions.allowed_tools and tool.name not in permissions.allowed_tools:
        return GateResult(False, "not_allowed", f"{tool.name} is not in this business's allowed tools")
    if RISK_ORDER[tool.risk] > RISK_ORDER[permissions.max_risk]:
        return GateResult(
            False, "risk_too_high", f"{tool.name} is {tool.risk} risk; the limit is {permissions.max_risk}"
        )
    return GateResult(True)


def configured_capabilities(tenant: TenantConfig) -> dict[str, list[str]]:
    """Every tool the configuration relies on, with the intents that use it ("identity" for the identity check)."""
    used: dict[str, list[str]] = {tenant.identity.verify_tool: ["identity"]}
    for name, spec in tenant.intents.items():
        for tool in (spec.lookup_tool, spec.action_tool):
            if tool:
                used.setdefault(tool, [])
                if name not in used[tool]:
                    used[tool].append(name)
    for tool in tenant.permissions.allowed_tools:
        if tool != "*":
            used.setdefault(tool, [])
    return dict(sorted(used.items()))
