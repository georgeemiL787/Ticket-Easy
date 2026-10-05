"""The Nile Style fixtures must agree with each other: every name used in one file exists in the file it points to."""

from typing import Any

from team_b.contracts.tools import ToolSpec
from team_b.domain.tenant import TenantConfig
from tests.contract.conftest import Rule, read

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
DERIVED_FACTS = {"days_since_delivery", "days_late", "hours_since_delivery"}
KNOWN_KNOWLEDGE_WHEN = {"order_late"}


def rules() -> list[Rule]:
    return [Rule.model_validate(r) for r in read("rules.json")["rules"]]


def by_name(tools: list[ToolSpec]) -> dict[str, ToolSpec]:
    return {t.name: t for t in tools}


def test_every_rule_action_is_a_tool_capability(tools: list[ToolSpec]) -> None:
    capabilities = {t.capability for t in tools}
    assert {r.action for r in rules()} <= capabilities


def test_every_rule_citation_exists_in_the_current_policies(policies: dict[str, Any]) -> None:
    current = {p["passage_id"] for p in policies["passages"] if not p["superseded"]}
    for rule in rules():
        assert rule.citation in current, f"{rule.rule_id} cites {rule.citation}"
    for p in policies["passages"]:  # the quote in the rule is really in that passage
        for rule in rules():
            if rule.citation == p["passage_id"]:
                assert rule.quote.rstrip(".") in p["text"], rule.rule_id


def test_rule_conditions_use_known_facts_and_real_arguments(tools: list[ToolSpec]) -> None:
    get_order = by_name(tools)["get_order"]
    known_facts = set(get_order.output_schema["properties"]) | DERIVED_FACTS
    for rule in rules():
        arguments = set(by_name(tools)[rule.action].input_schema["properties"])
        for cond in rule.applies_if + rule.conditions:
            allowed = known_facts if cond.from_ == "facts" else arguments
            assert cond.field in allowed, f"{rule.rule_id}: {cond.from_}.{cond.field}"


def test_every_side_effect_tool_the_agent_may_use_has_an_approved_rule(
    tenant: TenantConfig, tools: list[ToolSpec]
) -> None:
    approved_actions = {r.action for r in rules() if r.status == "approved"}
    for tool in tools:
        if tool.name in tenant.permissions.allowed_tools and tool.operation_kind != "read":
            assert tool.capability in approved_actions, tool.name


def test_allowed_tools_exist_are_not_human_only_and_fit_max_risk(tenant: TenantConfig, tools: list[ToolSpec]) -> None:
    known = by_name(tools)
    for name in tenant.permissions.allowed_tools:
        assert name in known, name
        assert not known[name].human_only, name
        assert known[name].enabled, name
        assert RISK_ORDER[known[name].risk] <= RISK_ORDER[tenant.permissions.max_risk], name
    assert known["delete_customer"].human_only and "delete_customer" not in tenant.permissions.allowed_tools


def test_identity_check_uses_a_real_tool_with_matching_slots(tenant: TenantConfig, tools: list[ToolSpec]) -> None:
    verify = by_name(tools)[tenant.identity.verify_tool]
    assert verify.requires_identity is False  # it is the identity check itself
    assert set(tenant.identity.required_slots) == set(verify.input_schema["required"])


def test_intent_tools_exist_and_are_allowed(tenant: TenantConfig, tools: list[ToolSpec]) -> None:
    known = by_name(tools)
    for name, spec in tenant.intents.items():
        for tool in (spec.lookup_tool, spec.action_tool):
            if tool:
                assert tool in known and tool in tenant.permissions.allowed_tools, f"{name}: {tool}"
        if spec.lookup_tool:
            assert known[spec.lookup_tool].operation_kind == "read", name
        if spec.action_tool:
            assert known[spec.action_tool].operation_kind != "read", name
        assert spec.knowledge_when is None or spec.knowledge_when in KNOWN_KNOWLEDGE_WHEN, name


def test_argument_maps_fit_the_tool_inputs(tenant: TenantConfig, tools: list[ToolSpec]) -> None:
    known = by_name(tools)
    order_facts = set(known["get_order"].output_schema["properties"])
    for name, spec in tenant.intents.items():
        if spec.lookup_tool:  # the lookup needs its inputs from the slots
            assert set(known[spec.lookup_tool].input_schema["required"]) <= set(spec.required_slots), name
        if not spec.action_tool:
            assert not spec.argument_map, name
            continue
        inputs = known[spec.action_tool].input_schema
        assert set(spec.argument_map) <= set(inputs["properties"]), name
        assert set(inputs["required"]) <= set(spec.argument_map), f"{name}: a required input has no source"
        for argument, source in spec.argument_map.items():
            kind, _, value = source.partition(":")
            assert kind in {"slot", "fact", "const"} and value, f"{name}.{argument}: {source}"
            if kind == "slot":
                assert value in spec.required_slots, f"{name}.{argument}: slot {value} is never asked for"
            if kind == "fact":
                assert spec.lookup_tool == "get_order" and value.removeprefix("order_") in {
                    f.removeprefix("order_") for f in order_facts
                }, f"{name}.{argument}: fact {value}"
