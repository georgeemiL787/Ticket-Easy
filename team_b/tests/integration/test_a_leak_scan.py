"""Leak scan: no order data in any reply, and no identity-gated read, before the customer is verified.

Every active scenario is run; afterwards every trace of its conversation is checked. A trace counts as "before
verification" until the first turn whose end state is verified (that turn may show the data it just earned).
"""

import json
from pathlib import Path

import pytest

from team_b.container import Container
from team_b.domain.trace import DecisionTrace
from tests.integration.scenario_format import discover, load_scenario
from tests.integration.scenario_runner import conversation_id_of, run_scenario

BACKEND = json.loads((Path(__file__).parents[2] / "fixtures" / "shop_001" / "backend.json").read_text(encoding="utf-8"))
GATED_TOOLS = {"get_order", "list_customer_orders"}


def order_data_tokens() -> list[str]:
    """Strings that only a verified customer may see: item, tracking number, street, the customer's name and phone."""
    tokens: list[str] = []
    for order in BACKEND["orders"]:
        tokens += [order["item_name"], order["delivery_address"]["street"]]
        if order.get("tracking_number"):
            tokens.append(order["tracking_number"])
    for customer in BACKEND["customers"]:
        tokens += [customer["name"], customer["phone"]]
    return sorted(set(tokens))


TOKENS = order_data_tokens()
ACTIVE = [(tenant, path) for tenant, path in discover() if load_scenario(path).status == "active"]


def test_the_scan_has_scenarios_and_tokens_to_work_with() -> None:
    assert len(ACTIVE) >= 5 and len(TOKENS) > 30


@pytest.mark.parametrize(("tenant_id", "path"), ACTIVE, ids=[f"{t}/{p.stem}" for t, p in ACTIVE])
async def test_no_order_data_before_identity_is_verified(tenant_id: str, path: Path) -> None:
    scenario = load_scenario(path)
    stored: list[DecisionTrace] = []

    def record(container: Container) -> Container:
        """Keep every trace as it is written (the run's database is gone once run_scenario returns)."""
        original = container.traces.add

        async def add(trace: DecisionTrace) -> None:
            stored.append(trace)
            await original(trace)

        container.traces.add = add  # type: ignore[method-assign]
        return container

    await run_scenario(scenario, tenant_id, customize=record)
    traces = [t for t in stored if t.conversation_id == conversation_id_of(scenario)]
    verified = False
    for number, trace in enumerate(traces, start=1):
        verified = verified or trace.identity.verified
        if verified:
            continue
        label = f"{scenario.id} turn {number}"
        found = [t for t in TOKENS if t.lower() in trace.response_text.lower()]
        assert not found, f"{label}: order data in a reply before verification: {found}"
        gated = [c.tool for c in trace.tool_calls if c.tool in GATED_TOOLS and c.status == "success"]
        assert not gated, f"{label}: read {gated} before verification"
