"""Backend (customers, orders), tickets, tools and the tenant config of Nile Style."""

import re
from collections import Counter
from datetime import date

from team_b.contracts.tools import ToolSpec
from team_b.domain.tenant import TenantConfig
from tests.contract.conftest import AS_OF, Customer, Order, Ticket, read

ORDER_RULE_FIELDS = {
    "order_status",
    "delivered_at",
    "expected_delivery_date",
    "product_category",
    "is_clearance",
    "item_condition",
    "order_total",
    "customer_id",
}
ARABIC = re.compile(r"[\u0600-\u06ff]")


def test_backend_customers_and_orders() -> None:
    data = read("backend.json")
    customers = [Customer.model_validate(c) for c in data["customers"]]
    orders = [Order.model_validate(o) for o in data["orders"]]
    assert date.fromisoformat(data["as_of"]) == AS_OF and len(customers) >= 6
    assert {c.phone[:3] for c in customers} == {"010", "011", "012", "015"}
    assert len({c.phone for c in customers}) == len(customers)
    assert {o.order_status for o in orders} == {
        "pending",
        "processing",
        "shipped",
        "delivered",
        "returned",
        "cancelled",
    }
    assert "paid" in {o.payment_status for o in orders}  # a paid order exists (status processing)
    assert len({o.order_id for o in orders}) == len(orders)
    assert {o.customer_id for o in orders} <= {c.customer_id for c in customers}
    assert all(ORDER_RULE_FIELDS <= set(raw) for raw in data["orders"])  # the facts the rules need


def test_backend_has_the_planned_demo_cases() -> None:
    orders = [Order.model_validate(o) for o in read("backend.json")["orders"]]
    assert {(AS_OF - o.delivered_at).days for o in orders if o.delivered_at} >= {3, 10, 14, 15, 20}
    late = [o.order_id for o in orders if o.order_status == "shipped" and (AS_OF - o.expected_delivery_date).days == 4]
    assert late == ["NS-20877"]
    assert any(o.is_clearance for o in orders) and any(o.order_total > 3000 for o in orders)
    assert Counter(o.customer_id for o in orders).most_common(1)[0][1] == 3  # one customer with 3 orders
    assert set(read("backend.json")["demo_guide"]) == {o.order_id for o in orders}


def test_tickets_are_fictional_and_have_no_personal_data() -> None:
    tickets = [Ticket.model_validate(t) for t in read("tickets.json")["tickets"]]
    assert len(tickets) == 10 and len({t.ticket_id for t in tickets}) == 10
    for t in tickets:
        blob = f"{t.customer_message} {t.resolution}"
        assert not re.search(r"\b01[0125]\d{8}\b|@|\+20", blob), t.ticket_id  # no phone numbers or emails
        assert not re.search(r"\bNS-\d+", blob), t.ticket_id  # no order numbers


def test_tools_parse_as_toolspecs_with_schemas() -> None:
    tools = [ToolSpec.model_validate(t) for t in read("tools.json")["tools"]]
    assert [t.name for t in tools] == [
        "verify_customer",
        "get_order",
        "list_customer_orders",
        "create_return",
        "create_exchange",
        "create_refund",
        "cancel_order",
        "update_delivery_address",
        "apply_voucher",
        "create_ticket",
        "delete_customer",
    ]
    assert [t.name for t in tools if t.human_only] == ["delete_customer"]
    for t in tools:
        for schema in (t.input_schema, t.output_schema):
            assert schema["type"] == "object" and set(schema["required"]) <= set(schema["properties"]), t.name
        if t.operation_kind != "read":
            assert "reference_id" in t.output_schema["required"], t.name


def test_tenant_config_has_the_twelve_intents_with_three_examples_each(tenant: TenantConfig) -> None:
    assert (tenant.tenant_id, tenant.display_name, tenant.order_id_prefix) == ("shop_001", "Nile Style", "NS-")
    assert set(tenant.intents) == {
        "policy_question",
        "greeting",
        "order_status",
        "return_request",
        "exchange_request",
        "refund_request",
        "cancel_order",
        "change_address",
        "voucher_request",
        "complaint",
        "human_request",
        "delete_account",  # a person-only tool: the permission gate hands it to a human
    }
    for name, spec in tenant.intents.items():
        assert spec.description and len(spec.examples) == 3, name
        assert any(ARABIC.search(e) for e in spec.examples), name  # Egyptian Arabic
        assert any(not ARABIC.search(e) for e in spec.examples), name  # English / Arabizi
    assert re.fullmatch(tenant.order_id_pattern, "NS-20877") and not re.fullmatch(tenant.order_id_pattern, "XX-1")


JSON_TYPES = {"string", "number", "integer", "boolean", "object", "array", "null"}


def _types_are_valid(schema: dict, where: str) -> None:
    kinds = schema.get("type", "object")
    for kind in [kinds] if isinstance(kinds, str) else kinds:
        assert kind in JSON_TYPES, f"{where}: {kind!r} is not a JSON Schema type"
    for name, sub in schema.get("properties", {}).items():
        _types_are_valid(sub, f"{where}.{name}")
    if "items" in schema:
        _types_are_valid(schema["items"], f"{where}[]")


def test_every_tool_schema_uses_real_json_schema_types() -> None:
    for t in read("tools.json")["tools"]:
        for side in ("input_schema", "output_schema"):
            _types_are_valid(t[side], f"{t['name']}.{side}")
