"""The shop's tool catalog (cache) and the permission gate, then how actions and lookups use them."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from team_b.brain import gates
from team_b.brain.gates import check_tool, configured_capabilities
from team_b.brain.orchestrator import Orchestrator
from team_b.brain.registry import CapabilityRegistry
from team_b.config import Settings
from team_b.container import Container, build_container, inject
from team_b.contracts.errors import UpstreamError
from team_b.contracts.tools import ToolCallRequest, ToolResult, ToolSpec
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.reply import AgentReply
from team_b.domain.tenant import PermissionsConfig, TenantConfig, TenantRegistry
from tests.support import make_settings

T, C = "shop_001", "conv-a4"
PHONE_C101 = "01123456702"  # C-101, who owns NS-20790


class StepClock:
    """A clock tests can move by seconds."""

    def __init__(self) -> None:
        self._now = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def today(self) -> date:
        return self._now.date()

    def tick(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


def spec(name: str = "t", **kw: Any) -> ToolSpec:
    fields: dict[str, Any] = {"name": name, "capability": name, "operation_kind": "read", "risk": "low", **kw}
    return ToolSpec(**fields)


class CountingShop:
    """A shop whose tool list can change or fail, counting how often it was asked."""

    def __init__(self, tools: list[ToolSpec]) -> None:
        self.tools, self.asked, self.down = tools, 0, False

    async def list_tools(self, tenant_id: str) -> list[ToolSpec]:
        self.asked += 1
        if self.down:
            raise UpstreamError("shop", "BACKEND_UNAVAILABLE", "down", retryable=True)
        return list(self.tools)

    async def call_tool(self, tenant_id: str, request: ToolCallRequest) -> ToolResult:
        raise AssertionError("not used")


class Switched:
    """The real shop, with one tool listed as switched off."""

    def __init__(self, inner: Any, off: str) -> None:
        self._inner, self._off = inner, off

    async def list_tools(self, tenant_id: str) -> list[ToolSpec]:
        tools: list[ToolSpec] = await self._inner.list_tools(tenant_id)
        return [t.model_copy(update={"enabled": False}) if t.name == self._off else t for t in tools]

    async def call_tool(self, tenant_id: str, request: ToolCallRequest) -> ToolResult:
        result: ToolResult = await self._inner.call_tool(tenant_id, request)
        return result


@pytest.fixture
def container(tmp_path: Path) -> Container:
    return build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))


@pytest.fixture
def tenant(container: Container) -> TenantConfig:
    return container.tenants.get(T)


def allowing(tenant: TenantConfig, *names: str, max_risk: str = "high") -> TenantConfig:
    permissions = PermissionsConfig(allowed_tools=names, max_risk=max_risk)  # type: ignore[arg-type]
    return tenant.model_copy(update={"permissions": permissions})


def bot_for(container: Container, tenant: TenantConfig | None = None, shop: Any = None) -> Orchestrator:
    """An orchestrator on the container's stores with a changed tenant configuration and/or shop."""
    return Orchestrator(
        clock=container.clock,
        tenants=TenantRegistry({T: tenant}) if tenant is not None else container.tenants,
        sessions=container.sessions,
        traces=container.traces,
        cases=container.cases,
        capabilities=shop if shop is not None else container.capabilities,
    )


async def say_to(bot: Orchestrator, text: str, conversation: str = C) -> AgentReply:
    return await bot.handle_turn(T, conversation, text)


async def trace_of(container: Container, reply: AgentReply):  # type: ignore[no-untyped-def]
    trace = await container.traces.get(T, reply.trace_id)
    assert trace is not None
    return trace


# ---- the permission gate: one test per reason ----


def test_a_listed_enabled_low_risk_tool_passes(tenant: TenantConfig) -> None:
    assert check_tool(allowing(tenant, "t"), spec("t")).allowed


def test_gate_reason_disabled(tenant: TenantConfig) -> None:
    result = check_tool(allowing(tenant, "t"), spec("t", enabled=False))
    assert (result.allowed, result.reason) == (False, "disabled")


def test_gate_reason_human_only(tenant: TenantConfig) -> None:
    result = check_tool(allowing(tenant, "t"), spec("t", human_only=True))
    assert (result.allowed, result.reason) == (False, "human_only")


def test_gate_reason_not_allowed(tenant: TenantConfig) -> None:
    result = check_tool(allowing(tenant, "other"), spec("t"))
    assert (result.allowed, result.reason) == (False, "not_allowed") and "t" in result.detail


def test_the_default_allows_nothing(tenant: TenantConfig) -> None:
    bare = tenant.model_copy(update={"permissions": PermissionsConfig()})
    assert check_tool(bare, spec("t")).reason == "not_allowed"


@pytest.mark.parametrize(
    ("limit", "risk", "allowed"),
    [
        ("low", "low", True),
        ("low", "medium", False),
        ("medium", "medium", True),
        ("medium", "high", False),
        ("high", "high", True),
    ],
)
def test_gate_reason_risk_too_high(tenant: TenantConfig, limit: str, risk: str, allowed: bool) -> None:
    result = check_tool(allowing(tenant, "t", max_risk=limit), spec("t", risk=risk))
    assert result.allowed is allowed and (allowed or result.reason == "risk_too_high")


def test_the_star_allows_every_name_but_the_other_checks_still_apply(tenant: TenantConfig) -> None:
    star = allowing(tenant, "*", max_risk="medium")
    assert check_tool(star, spec("anything")).allowed
    assert check_tool(star, spec("x", human_only=True)).reason == "human_only"
    assert check_tool(star, spec("x", enabled=False)).reason == "disabled"
    assert check_tool(star, spec("x", risk="high")).reason == "risk_too_high"


def test_the_first_failing_check_decides(tenant: TenantConfig) -> None:
    strict = allowing(tenant, "other", max_risk="low")
    assert check_tool(strict, spec("t", enabled=False, human_only=True, risk="high")).reason == "disabled"
    assert check_tool(strict, spec("t", human_only=True, risk="high")).reason == "human_only"
    assert check_tool(strict, spec("t", risk="high")).reason == "not_allowed"


def test_configured_capabilities_lists_what_the_config_relies_on(tenant: TenantConfig) -> None:
    used = configured_capabilities(tenant)
    assert used["verify_customer"] == ["identity"] and "order_status" in used["get_order"]
    assert used["delete_customer"] == ["delete_account"]
    assert list(used) == sorted(used)


# ---- the registry ----


async def test_the_list_is_remembered_for_the_ttl_then_asked_again() -> None:
    shop, clock = CountingShop([spec("a")]), StepClock()
    registry = CapabilityRegistry(shop, clock, ttl_s=60)  # type: ignore[arg-type]
    first = await registry.catalog(T)
    assert first is not None and set(first.tools) == {"a"} and not first.stale
    clock.tick(59)
    await registry.catalog(T)
    assert shop.asked == 1
    clock.tick(2)
    await registry.catalog(T)
    assert shop.asked == 2


async def test_a_new_tool_shows_up_after_the_ttl_or_a_refresh() -> None:
    shop, clock = CountingShop([spec("a")]), StepClock()
    registry = CapabilityRegistry(shop, clock, ttl_s=60)  # type: ignore[arg-type]
    await registry.catalog(T)
    shop.tools = [spec("a"), spec("b")]
    remembered = await registry.catalog(T)
    assert remembered is not None and set(remembered.tools) == {"a"}
    refreshed = await registry.catalog(T, refresh=True)
    assert refreshed is not None and set(refreshed.tools) == {"a", "b"}
    shop.tools = [spec("a")]
    clock.tick(61)
    later = await registry.catalog(T)
    assert later is not None and set(later.tools) == {"a"}


async def test_the_last_good_list_is_served_when_the_shop_is_down() -> None:
    shop, clock = CountingShop([spec("a")]), StepClock()
    registry = CapabilityRegistry(shop, clock, ttl_s=60)  # type: ignore[arg-type]
    await registry.catalog(T)
    shop.down = True
    clock.tick(120)
    stale = await registry.catalog(T)
    assert stale is not None and stale.stale and set(stale.tools) == {"a"}
    shop.down = False
    fresh = await registry.catalog(T)
    assert fresh is not None and not fresh.stale


async def test_with_no_earlier_list_and_the_shop_down_there_is_nothing() -> None:
    shop = CountingShop([spec("a")])
    shop.down = True
    assert await CapabilityRegistry(shop, StepClock(), ttl_s=60).catalog(T) is None  # type: ignore[arg-type]


async def test_dropping_forgets_the_list() -> None:
    shop = CountingShop([spec("a")])
    registry = CapabilityRegistry(shop, StepClock(), ttl_s=60)  # type: ignore[arg-type]
    await registry.catalog(T)
    registry.drop(T)
    registry.drop("never_seen")  # harmless
    await registry.catalog(T)
    assert shop.asked == 2


async def test_each_tenant_has_its_own_list() -> None:
    shop = CountingShop([spec("a")])
    registry = CapabilityRegistry(shop, StepClock(), ttl_s=60)  # type: ignore[arg-type]
    await registry.catalog("shop_001")
    await registry.catalog("shop_002")
    assert shop.asked == 2


def test_the_ttl_comes_from_the_settings(tmp_path: Path) -> None:
    built = build_container(make_settings(tmp_path, capability_ttl_s=5.0))
    assert built.registry is not None and built.registry._ttl_s == 5.0  # type: ignore[attr-defined]
    assert Settings().capability_ttl_s == 60.0


# ---- the flow: gate and catalog in the conversation ----


async def test_a_person_only_tool_is_refused_at_once_and_nothing_is_asked(container: Container) -> None:
    reply = await say_to(bot_for(container), "Please delete my account and all my data")
    trace = await trace_of(container, reply)
    assert (reply.decision, trace.escalation_reason) == (Decision.HANDOFF, EscalationReason.UNSUPPORTED)
    assert "human_only" in trace.decision_reason and trace.tool_calls == () and reply.locale.value == "en"


async def test_a_switched_off_tool_is_unsupported(container: Container) -> None:
    bot = bot_for(container, shop=Switched(container.capabilities, "create_return"))
    reply = await say_to(bot, "I want to return order NS-20790, the size is wrong")
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.UNSUPPORTED and "disabled" in trace.decision_reason


async def test_a_tool_the_business_does_not_allow_is_unsupported(container: Container, tenant: TenantConfig) -> None:
    narrowed = allowing(tenant, "verify_customer", "get_order")
    reply = await say_to(bot_for(container, narrowed), "I want to return order NS-20790, the size is wrong")
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.UNSUPPORTED and "not_allowed" in trace.decision_reason


async def test_a_risk_above_the_limit_is_unsupported(container: Container, tenant: TenantConfig) -> None:
    capped = tenant.model_copy(update={"permissions": tenant.permissions.model_copy(update={"max_risk": "medium"})})
    reply = await say_to(bot_for(container, capped), "I want a refund for order NS-20790")
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.UNSUPPORTED
    assert "risk_too_high" in trace.decision_reason  # create_refund is high risk


async def test_a_tool_missing_from_the_catalog_is_found_out_after_identity(container: Container) -> None:
    inject(container, "shop", {"switch": "unpublish", "tool": "create_return"})
    bot = bot_for(container)
    first = await say_to(bot, "I want to return order NS-20790, the size is wrong")
    assert (first.decision, first.awaiting) == (Decision.VERIFY_IDENTITY, "slot:phone")  # nothing revealed yet
    second = await say_to(bot, PHONE_C101)
    trace = await trace_of(container, second)
    assert (second.decision, trace.escalation_reason) == (Decision.HANDOFF, EscalationReason.CAPABILITY_MISSING)


async def test_an_unreachable_shop_with_no_list_hands_off_after_identity(container: Container) -> None:
    class Down:
        async def list_tools(self, tenant_id: str) -> list[ToolSpec]:
            raise UpstreamError("shop", "BACKEND_UNAVAILABLE", "down", retryable=True)

        async def call_tool(self, tenant_id: str, request: ToolCallRequest) -> ToolResult:
            raise AssertionError("no call may be made without a tool list")

    bot = bot_for(container, shop=Down())
    asked = await say_to(bot, "I want to return order NS-20790, the size is wrong")
    assert asked.decision is Decision.VERIFY_IDENTITY  # asking costs nothing
    reply = await say_to(bot, PHONE_C101)
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.DEPENDENCY_UNAVAILABLE and trace.tool_calls == ()


async def test_a_lookup_for_a_tool_the_business_blocks_is_unsupported(
    container: Container, tenant: TenantConfig
) -> None:
    reply = await say_to(
        bot_for(container, allowing(tenant, "verify_customer")), "Where is my order NS-20877? My phone is 01012345601"
    )
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.UNSUPPORTED
    assert all(t.tool != "get_order" for t in trace.tool_calls)


async def test_the_identity_tool_is_gated_too(container: Container, tenant: TenantConfig) -> None:
    reply = await say_to(
        bot_for(container, allowing(tenant, "get_order")), "Where is my order NS-20877? My phone is 01012345601"
    )
    trace = await trace_of(container, reply)
    assert trace.escalation_reason is EscalationReason.UNSUPPORTED and trace.tool_calls == ()


async def test_a_call_that_says_not_published_drops_the_remembered_list(container: Container) -> None:
    assert container.registry is not None and container.orchestrator is not None
    await container.registry.catalog(T)  # remembered, get_order is in it
    inject(container, "shop", {"switch": "unpublish", "tool": "get_order"})
    reply = await say_to(container.orchestrator, "Where is my order NS-20877? My phone is 01012345601")
    assert reply.decision is Decision.CLARIFY  # the stale list let the call through; the shop said no
    fresh = await container.registry.catalog(T)
    assert fresh is not None and "get_order" not in fresh.tools  # asked again, the shop no longer lists it
    again = await say_to(container.orchestrator, "please try again")
    trace = await trace_of(container, again)
    assert again.decision is Decision.HANDOFF and trace.escalation_reason is EscalationReason.CAPABILITY_MISSING


def test_gates_module_names_its_owner() -> None:
    assert "OWNER: Track A" in (gates.__doc__ or "")
