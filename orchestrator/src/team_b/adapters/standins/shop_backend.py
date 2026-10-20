"""The fake Nile Style backend: its data and what each tool does to it.

Only physical constraints live here (a shipped order cannot be cancelled, a refund cannot exceed the order total).
Business policy (the 14-day window, the EGP 3,000 limit) is NOT enforced here on purpose: it belongs to the rule
checker. If the shop enforced it too, a brain that forgot to ask the rule checker would go unnoticed in tests.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

ORDER_FIELDS = (
    "order_id", "customer_id", "order_status", "payment_status", "order_total", "placed_at", "shipped_at",
    "delivered_at", "expected_delivery_date", "item_name", "product_category", "is_clearance", "item_condition",
    "tracking_number",
)  # fmt: skip
RECORD_KINDS = ("return", "exchange", "refund", "voucher", "ticket", "deletion")
BEFORE_SHIPPING = ("pending", "processing")
OPEN_ORDER = ("pending", "processing", "shipped")


@dataclass
class Ok:
    data: dict[str, Any]
    reference_id: str | None = None
    applied: bool = False  # did this call change the backend?


@dataclass
class Fail:
    code: str
    message: str


Outcome = Ok | Fail


@dataclass
class Backend:
    customers: dict[str, dict[str, Any]]
    orders: dict[str, dict[str, Any]]
    today: Callable[[], date]
    counters: dict[str, int] = field(default_factory=dict)
    records: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {k: [] for k in RECORD_KINDS})

    def next_reference(self, prefix: str) -> str:
        self.counters[prefix] = self.counters.get(prefix, 30000) + 1
        return f"{prefix}-{self.counters[prefix]}"

    def order(self, order_id: str) -> dict[str, Any] | None:
        return self.orders.get(order_id)


def _not_found(what: str, key: str) -> Fail:
    return Fail("NOT_FOUND", f"{what} {key} does not exist")


def _phone_digits(phone: str) -> str:
    return re.sub(r"[\s\-()]", "", phone)


def verify_customer(b: Backend, a: dict[str, Any]) -> Outcome:
    order = b.order(a["order_id"])
    customer = b.customers.get(order["customer_id"]) if order else None
    ok = customer is not None and _phone_digits(customer["phone"]) == _phone_digits(a["phone"])
    # An unknown order and a wrong phone look the same: the shop never reveals which orders exist.
    return Ok({"verified": ok, "customer_id": customer["customer_id"] if ok and customer else None})


def get_order(b: Backend, a: dict[str, Any]) -> Outcome:
    order = b.order(a["order_id"])
    return Ok({k: order[k] for k in ORDER_FIELDS}) if order else _not_found("order", a["order_id"])


def list_customer_orders(b: Backend, a: dict[str, Any]) -> Outcome:
    if a["customer_id"] not in b.customers:
        return _not_found("customer", a["customer_id"])
    mine = sorted((o for o in b.orders.values() if o["customer_id"] == a["customer_id"]), key=lambda o: o["placed_at"])
    keys = ("order_id", "order_status", "order_total", "placed_at")
    return Ok({"orders": [{k: o[k] for k in keys} for o in mine]})


def _open_return_or_exchange(b: Backend, order_id: str) -> bool:
    return any(r["order_id"] == order_id for kind in ("return", "exchange") for r in b.records[kind])


def _after_sale(b: Backend, a: dict[str, Any], kind: str, prefix: str, extra: dict[str, Any]) -> Outcome:
    order = b.order(a["order_id"])
    if order is None:
        return _not_found("order", a["order_id"])
    if order["order_status"] != "delivered":
        return Fail("REJECTED", f"only delivered orders can have a {kind} (status is {order['order_status']})")
    if _open_return_or_exchange(b, a["order_id"]):
        return Fail("REJECTED", "this order already has an open return or exchange")
    ref = b.next_reference(prefix)
    b.records[kind].append({"reference_id": ref, "order_id": a["order_id"], "reason": a["reason"], **extra})
    return Ok({"reference_id": ref, "order_id": a["order_id"], "status": "created"}, ref, True)


def create_return(b: Backend, a: dict[str, Any]) -> Outcome:
    return _after_sale(b, a, "return", "RET", {})


def create_exchange(b: Backend, a: dict[str, Any]) -> Outcome:
    return _after_sale(b, a, "exchange", "EXC", {"new_size": a.get("new_size")})


def create_refund(b: Backend, a: dict[str, Any]) -> Outcome:
    order = b.order(a["order_id"])
    if order is None:
        return _not_found("order", a["order_id"])
    if a["amount"] <= 0:
        return Fail("REJECTED", "the refund amount must be positive")
    if a["amount"] > order["order_total"]:
        return Fail("REJECTED", f"the refund amount exceeds the order total ({order['order_total']})")
    if order["payment_status"] == "refunded":
        return Fail("REJECTED", "this order was already refunded")
    if order["payment_status"] != "paid":
        return Fail("REJECTED", "nothing was paid for this order yet")
    order["payment_status"] = "refunded"
    ref = b.next_reference("REF")
    b.records["refund"].append({"reference_id": ref, "order_id": a["order_id"], "amount": a["amount"]})
    return Ok({"reference_id": ref, "order_id": a["order_id"], "amount": a["amount"], "status": "refunded"}, ref, True)


def cancel_order(b: Backend, a: dict[str, Any]) -> Outcome:
    order = b.order(a["order_id"])
    if order is None:
        return _not_found("order", a["order_id"])
    if order["order_status"] not in BEFORE_SHIPPING:
        return Fail(
            "REJECTED", f"only an order that has not shipped can be cancelled (status is {order['order_status']})"
        )
    order["order_status"] = "cancelled"
    order["cancelled_at"] = b.today().isoformat()
    ref = b.next_reference("CAN")
    return Ok({"reference_id": ref, "order_id": a["order_id"], "status": "cancelled"}, ref, True)


def update_delivery_address(b: Backend, a: dict[str, Any]) -> Outcome:
    order = b.order(a["order_id"])
    if order is None:
        return _not_found("order", a["order_id"])
    if order["order_status"] not in BEFORE_SHIPPING:
        return Fail("REJECTED", f"the address of a shipped order cannot change (status is {order['order_status']})")
    if not a["new_address"].strip():
        return Fail("REJECTED", "the new address is empty")
    order["delivery_address"]["street"] = a["new_address"].strip()
    ref = b.next_reference("ADR")
    return Ok({"reference_id": ref, "order_id": a["order_id"], "status": "updated"}, ref, True)


def apply_voucher(b: Backend, a: dict[str, Any]) -> Outcome:
    if b.order(a["order_id"]) is None:
        return _not_found("order", a["order_id"])
    if a["amount"] <= 0:
        return Fail("REJECTED", "the voucher amount must be positive")
    if any(v["order_id"] == a["order_id"] for v in b.records["voucher"]):
        return Fail("REJECTED", "a voucher was already issued for this order")
    ref = b.next_reference("VCH")
    code = "NILE-" + ref.split("-")[1]
    record = {"reference_id": ref, "order_id": a["order_id"], "amount": a["amount"], "voucher_code": code}
    b.records["voucher"].append(record)
    return Ok({**record}, ref, True)


def create_ticket(b: Backend, a: dict[str, Any]) -> Outcome:
    if a.get("order_id") and b.order(a["order_id"]) is None:
        return _not_found("order", a["order_id"])
    ref = b.next_reference("TKT")
    b.records["ticket"].append({"reference_id": ref, "subject": a["subject"], "order_id": a.get("order_id")})
    return Ok({"reference_id": ref, "status": "open"}, ref, True)


def delete_customer(b: Backend, a: dict[str, Any]) -> Outcome:
    if a["customer_id"] not in b.customers:
        return _not_found("customer", a["customer_id"])
    if any(o["customer_id"] == a["customer_id"] and o["order_status"] in OPEN_ORDER for o in b.orders.values()):
        return Fail("REJECTED", "the customer still has open orders")
    del b.customers[a["customer_id"]]
    ref = b.next_reference("DEL")
    b.records["deletion"].append({"reference_id": ref, "customer_id": a["customer_id"]})
    return Ok({"reference_id": ref, "status": "deleted"}, ref, True)


Handler = Callable[[Backend, dict[str, Any]], Outcome]
HANDLERS: dict[str, Handler] = {
    f.__name__: f
    for f in (verify_customer, get_order, list_customer_orders, create_return, create_exchange, create_refund,
              cancel_order, update_delivery_address, apply_voucher, create_ticket, delete_customer)
}  # fmt: skip
