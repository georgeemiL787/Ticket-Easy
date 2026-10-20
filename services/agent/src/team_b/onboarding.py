"""From what the owner told us to a tenant configuration: the tenant id, a first draft, and the final configuration.

No model is involved. A new business gets a short, valid configuration at the first step (so every later step has
something
to build on). At "Go live" the configuration is generated from the business profile and the tools the owner
published: one
intent per tool (a lookup for a read, an action for a change), each argument filled from what the customer says. These
businesses use the agent (an AI model reads the message and proposes moves; code still checks every move): rule-based
understanding only knows the intents of the shipped shops.
"""

import re
from typing import Any

from team_b.contracts.base import RiskLevel
from team_b.domain.bundle import BundleProfile, BundleTool
from team_b.domain.onboarding import BusinessProfile
from team_b.domain.tenant import (
    EscalationConfig,
    IdentityConfig,
    IntentSpec,
    PermissionsConfig,
    TenantConfig,
)

PLACEHOLDER_VERIFY_TOOL = "verify_customer"
IDENTITY_ARGUMENTS = {
    "customer_id": "identity:customer_id"
}  # an argument the verified customer fills, never the customer's words
RISK_ORDER: tuple[RiskLevel, ...] = ("low", "medium", "high")


def slug(name: str) -> str:
    """A tenant id from a business name: lowercase letters, digits and underscores, at most 56 characters."""
    text = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return (text if text and text[0].isalpha() else f"shop_{text}".strip("_"))[:56].rstrip("_") or "shop"


def unique_id(name: str, taken: set[str]) -> str:
    base = slug(name)
    candidate, number = base, 2
    while candidate in taken:
        candidate, number = f"{base[:52]}_{number}", number + 1
    return candidate


def bundle_profile(profile: BusinessProfile) -> BundleProfile:
    return BundleProfile(
        display_name=profile.name, locales=profile.locales, default_locale=profile.default_locale, tone=profile.tone
    )


def draft_config(tenant_id: str, profile: BusinessProfile) -> TenantConfig:
    """The first configuration of a business: valid, answers policy questions and greetings, uses no tools yet."""
    return TenantConfig(
        tenant_id=tenant_id,
        display_name=profile.name,
        default_locale=profile.default_locale,
        order_id_pattern=profile.order_id_pattern,
        order_id_prefix=profile.order_id_prefix,
        identity=IdentityConfig(verify_tool=PLACEHOLDER_VERIFY_TOOL),
        permissions=PermissionsConfig(allowed_tools=(), max_risk="medium"),
        escalation=EscalationConfig(),
        intents=generic_intents(),
        managers=profile.contacts,
        agent_mode="on",
    )


def generic_intents() -> dict[str, IntentSpec]:
    return {
        "policy_question": IntentSpec(
            kind="knowledge",
            description="A question about the business's policies (returns, refunds, shipping, payment, FAQ).",
            examples=("What is your return policy?", "How long does delivery take?"),
        ),
        "greeting": IntentSpec(
            kind="smalltalk", description="A greeting or thanks with no request.", examples=("Hello",)
        ),
    }


def tool_intent(tool: BundleTool) -> tuple[str, IntentSpec] | None:
    """The intent for one tool the owner published, or None for a tool without a declaration."""
    spec = tool.binding.spec if tool.binding is not None else None
    if spec is None:
        return None
    properties: dict[str, Any] = (spec.get("input_schema") or {}).get("properties") or {}
    required = [str(r) for r in (spec.get("input_schema") or {}).get("required") or []]
    argument_map = {name: IDENTITY_ARGUMENTS.get(name, f"slot:{name}") for name in properties}
    slots = tuple(name for name in required if argument_map.get(name, "").startswith("slot:"))
    title = str(spec.get("title") or tool.name)
    examples = tuple(dict.fromkeys(x for x in (title, str(spec.get("description") or "")[:120]) if x))
    if spec.get("operation_kind") == "read":
        return tool.name, IntentSpec(
            kind="lookup",
            description=title,
            examples=examples,
            lookup_tool=tool.name,
            required_slots=slots,
            argument_map=argument_map,
        )
    return tool.name, IntentSpec(
        kind="action",
        description=title,
        examples=examples,
        action_tool=tool.name,
        required_slots=slots,
        argument_map=argument_map,
    )


def generate_config(
    base: TenantConfig, profile: BusinessProfile, tools: tuple[BundleTool, ...], verify_tool: str | None
) -> TenantConfig:
    """The final configuration: the profile, the tools the owner published and an intent for each of them."""
    intents = dict(generic_intents())
    for tool in tools:
        if tool.name == verify_tool:
            continue  # it checks who the customer is; it is not something a customer asks for
        made = tool_intent(tool)
        if made is not None:
            intents[made[0]] = made[1]
    riskiest: RiskLevel = max((t.risk for t in tools), key=RISK_ORDER.index, default="medium")
    allowed_risk: RiskLevel = "high" if riskiest == "high" else "medium"
    return base.model_copy(
        update={
            "display_name": profile.name,
            "default_locale": profile.default_locale,
            "order_id_pattern": profile.order_id_pattern,
            "order_id_prefix": profile.order_id_prefix,
            "identity": IdentityConfig(verify_tool=verify_tool or PLACEHOLDER_VERIFY_TOOL),
            "permissions": PermissionsConfig(
                allowed_tools=tuple(t.name for t in tools),
                max_risk=allowed_risk,
            ),
            "intents": intents,
            "managers": profile.contacts,
            "agent_mode": "on",
        }
    )
