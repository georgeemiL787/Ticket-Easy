"""Read a tenant's mock backend files and map them to the schema's column names.

Two layouts are supported:
  data/mock/<tenant>/backend.json             Team B's fixture (one item per order), e.g. shop_001
  data/mock/<tenant>/noon_*.json              customers/orders/returns/scenarios files with `_meta`, e.g. noon_eg
Unknown fields fail loudly, so a new source field is mapped on purpose instead of silently dropped.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from team_a.config import settings


@dataclass
class MockData:
    as_of: str | None = None
    currency: str = "EGP"
    files: list[Path] = field(default_factory=list)
    customers: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    items: list[dict] = field(default_factory=list)
    returns: list[dict] = field(default_factory=list)
    scenarios: list[dict] = field(default_factory=list)


def _bool(value: Any) -> int | None:
    return None if value is None else int(bool(value))


def _check_keys(kind: str, record: dict, known: set[str]) -> None:
    extra = set(record) - known
    if extra:
        raise ValueError(f"Unmapped {kind} field(s) {sorted(extra)} in record {record.get(kind + '_id', record)}")


def _check_tenant(tenant_id: str, record: dict, what: str) -> None:
    if record.get("tenant_id", tenant_id) != tenant_id:
        raise ValueError(f"{what} belongs to tenant {record['tenant_id']!r}, not {tenant_id!r}")


# ------------------------------------------------------------------ Team B backend.json

_TB_CUSTOMER = {"customer_id", "name", "phone", "preferred_language"}
_TB_ORDER = {
    "order_id", "customer_id", "order_status", "payment_status", "payment_method", "placed_at", "shipped_at",
    "delivered_at", "expected_delivery_date", "cancelled_at", "returned_at", "order_total", "currency",
    "item_name", "size", "product_category", "is_clearance", "item_condition", "delivery_address",
    "tracking_number",
}


def _read_backend_fixture(tenant_id: str, path: Path) -> MockData:
    raw = json.loads(path.read_text(encoding="utf-8"))
    _check_tenant(tenant_id, raw, str(path))
    data = MockData(as_of=raw.get("as_of"), files=[path])
    currencies = {o.get("currency", "EGP") for o in raw["orders"]}
    if len(currencies) > 1:
        raise ValueError(f"Mixed currencies in {path}: {sorted(currencies)}")
    data.currency = currencies.pop() if currencies else "EGP"
    for c in raw["customers"]:
        _check_keys("customer", c, _TB_CUSTOMER)
        data.customers.append({
            "customer_id": c["customer_id"], "name": c["name"], "phone": c.get("phone"),
            "preferred_language": c.get("preferred_language"),
            "verified": None,  # the fixture verifies per call (phone + order id), it has no stored flag
        })
    for o in raw["orders"]:
        _check_keys("order", o, _TB_ORDER)
        address = o.get("delivery_address")
        data.orders.append({
            "order_id": o["order_id"], "customer_id": o["customer_id"], "order_status": o["order_status"],
            "payment_method": o.get("payment_method"), "payment_status": o.get("payment_status"),
            "placed_at": o.get("placed_at"), "shipped_at": o.get("shipped_at"),
            "expected_delivery_date": o.get("expected_delivery_date"), "delivered_at": o.get("delivered_at"),
            "cancelled_at": o.get("cancelled_at"), "returned_at": o.get("returned_at"),
            "shipping_fee": 0, "cod_fee": 0, "order_total": o["order_total"],
            "tracking_number": o.get("tracking_number"),
            "delivery_address": json.dumps(address, ensure_ascii=False, sort_keys=True) if address else None,
        })
        # One item per order in this fixture: its price is the whole order total.
        data.items.append({
            "item_id": f"{o['order_id']}-I1", "order_id": o["order_id"], "line_no": 1, "sku": None,
            "name": o["item_name"], "product_category": o["product_category"], "size": o.get("size"),
            "qty": 1, "unit_price": o["order_total"], "item_condition": o.get("item_condition"),
            "is_clearance": _bool(o.get("is_clearance")),
        })
    return data


# ------------------------------------------------------------------ noon-style files with _meta

_N_CUSTOMER = {"customer_id", "tenant_id", "name_en", "name_ar", "phone", "email", "city", "preferred_language",
               "verified", "verified_via", "noon_wallet_egp", "member_since"}
_N_ORDER = {"order_id", "tenant_id", "customer_id", "section", "status", "placed_at", "expected_delivery_date",
            "delivered_at", "payment_method", "shipping_fee_egp", "cod_fee_egp", "items", "total_egp"}
_N_ITEM = {"item_id", "sku", "name", "category", "product_subcategory", "qty", "price_egp", "return_eligible_tag",
           "warranty_tag", "item_condition", "is_clearance"}
_N_RETURN = {"return_id", "tenant_id", "order_id", "customer_id", "payment_method", "status", "requested_at",
             "received_at", "qc_result", "refund_amount_egp", "refund_destination", "refund_initiated_at",
             "refund_completed_at", "notes", "delivery_attempts_failed", "hub_hold_started_at",
             "hub_hold_business_days"}


def _read_meta_file(tenant_id: str, path: Path, key: str, data: MockData) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    meta = raw.get("_meta", {})
    _check_tenant(tenant_id, meta, f"{path.name} _meta")
    for name, value in (("as_of", meta.get("as_of")), ("currency", meta.get("currency"))):
        if value is None:
            continue
        current = getattr(data, name)
        if name == "as_of" and current is not None and current != value:
            raise ValueError(f"{path.name}: as_of {value} differs from {current} in the other files")
        setattr(data, name, value)
    data.files.append(path)
    rows = raw[key]
    for r in rows:
        _check_tenant(tenant_id, r, f"{key[:-1]} {r.get(key[:-1] + '_id', '?')}")
    return rows


def _read_noon_style(tenant_id: str, folder: Path, prefix: str) -> MockData:
    data = MockData()
    for c in _read_meta_file(tenant_id, folder / f"{prefix}_customers.json", "customers", data):
        _check_keys("customer", c, _N_CUSTOMER)
        data.customers.append({
            "customer_id": c["customer_id"], "name": c["name_en"], "name_ar": c.get("name_ar"),
            "phone": c.get("phone"), "email": c.get("email"), "city": c.get("city"),
            "preferred_language": c.get("preferred_language"), "verified": _bool(c.get("verified")),
            "verified_via": c.get("verified_via"), "wallet_balance": c.get("noon_wallet_egp"),
            "member_since": c.get("member_since"),
        })
    for o in _read_meta_file(tenant_id, folder / f"{prefix}_orders.json", "orders", data):
        _check_keys("order", o, _N_ORDER)
        data.orders.append({
            "order_id": o["order_id"], "customer_id": o["customer_id"], "order_section": o.get("section"),
            "order_status": o["status"], "payment_method": o.get("payment_method"),
            "placed_at": o.get("placed_at"), "expected_delivery_date": o.get("expected_delivery_date"),
            "delivered_at": o.get("delivered_at"), "shipping_fee": o.get("shipping_fee_egp", 0),
            "cod_fee": o.get("cod_fee_egp", 0), "order_total": o["total_egp"],
        })
        for line_no, i in enumerate(o["items"], start=1):
            _check_keys("item", i, _N_ITEM)
            data.items.append({
                "item_id": i["item_id"], "order_id": o["order_id"], "line_no": line_no, "sku": i.get("sku"),
                "name": i["name"], "product_category": i["category"],
                "product_subcategory": i.get("product_subcategory"), "qty": i["qty"],
                "unit_price": i["price_egp"], "item_condition": i.get("item_condition"),
                "is_clearance": _bool(i.get("is_clearance")),
                "return_eligible_tag": _bool(i.get("return_eligible_tag")),
                "warranty_tag": _bool(i.get("warranty_tag")),
            })
    for r in _read_meta_file(tenant_id, folder / f"{prefix}_returns.json", "returns", data):
        _check_keys("return", r, _N_RETURN)
        data.returns.append({
            "return_id": r["return_id"], "order_id": r["order_id"], "customer_id": r["customer_id"],
            "payment_method": r.get("payment_method"), "return_status": r["status"],
            "requested_at": r.get("requested_at"), "received_at": r.get("received_at"),
            "qc_result": r.get("qc_result"), "refund_amount": r.get("refund_amount_egp"),
            "refund_destination": r.get("refund_destination"),
            "refund_initiated_at": r.get("refund_initiated_at"),
            "refund_completed_at": r.get("refund_completed_at"),
            "delivery_attempts_failed": r.get("delivery_attempts_failed"),
            "hub_hold_started_at": r.get("hub_hold_started_at"),
            "hub_hold_business_days": r.get("hub_hold_business_days"), "notes": r.get("notes"),
        })
    scenarios_file = folder / f"{prefix}_scenarios.json"
    if scenarios_file.exists():
        data.scenarios = _read_meta_file(tenant_id, scenarios_file, "scenarios", data)
    return data


def read_mock(tenant_id: str) -> MockData:
    folder = settings.mock_dir(tenant_id)
    if (folder / "backend.json").exists():
        return _read_backend_fixture(tenant_id, folder / "backend.json")
    customers = sorted(folder.glob("*_customers.json"))
    if len(customers) > 1:
        raise ValueError(f"More than one *_customers.json in {folder}")
    if customers:
        return _read_noon_style(tenant_id, folder, customers[0].name.removesuffix("_customers.json"))
    return MockData()  # a tenant with documents only
