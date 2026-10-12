"""Nile Style sandbox shop API: the business API Team C compiles tools against in the integrated demo (TEST-ONLY).

It serves the same customers and orders as Team B's demo shop (team_b/fixtures/shop_001/backend.json, copied in memory
at start), so the customer Team B verifies by phone is the same customer whose sandbox credential Team C uses.

Behavior and access rules, fixed before any tool is generated:
- Every business operation needs a bearer token; each token belongs to exactly one customer.
- GET /api/orders/{order_id} returns one order with its customer_id. Another customer's order is 403, an unknown one 404.
- POST /api/tickets opens a support ticket about one of the caller's own orders (201); another customer's order is 403.
- The after-sales operations (list orders, return, exchange, refund, cancel, delivery address, voucher) follow the same
  ownership rule and check order facts (delivered, not yet shipped, amount <= total) with 409/422; the shop POLICY is not here.
- Harness routes (hidden from the OpenAPI document, protected by the x-harness-key header) issue tokens, read state for
  independent verification, and make the next ticket answer 500 AFTER storing it (an outcome the caller cannot know).
- A customer's token is derived from the harness key (HMAC), so it stays valid when the sandbox restarts.

Run: python -m uvicorn integration.nile_sandbox.app:create_app --factory --port 8102   (from the repository root)
Environment: NILE_SANDBOX_HARNESS_KEY (required for the harness routes), NILE_SANDBOX_DATA (default: Team B's fixture).
"""

import copy
import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = ROOT / "team_b" / "fixtures" / "shop_001" / "backend.json"


class Order(BaseModel):
    order_id: str = Field(min_length=1, max_length=32)
    customer_id: str
    order_status: str
    payment_status: str
    order_total: float
    placed_at: str
    shipped_at: str | None
    delivered_at: str | None
    expected_delivery_date: str | None
    item_name: str
    product_category: str
    is_clearance: bool
    item_condition: str
    tracking_number: str | None


class TicketCreate(BaseModel):
    order_id: str = Field(min_length=1, max_length=32, description="The order the ticket is about")
    subject: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1000)


class Ticket(BaseModel):
    ticket_id: str
    order_id: str
    status: str


class OrderSummary(BaseModel):
    order_id: str
    order_status: str
    order_total: float
    placed_at: str


class OrderList(BaseModel):
    orders: list[OrderSummary]


class ChangeRequest(BaseModel):
    order_id: str = Field(min_length=1, max_length=32, description="The caller's order the request is about")
    reason: str = Field(min_length=1, max_length=500)


class ExchangeCreate(ChangeRequest):
    new_size: str | None = Field(default=None, max_length=16, description="The size the customer wants instead")


class RefundCreate(BaseModel):
    order_id: str = Field(min_length=1, max_length=32)
    amount: float = Field(gt=0, description="EGP to refund; at most the order total")
    reason: str | None = Field(default=None, max_length=500)


class AddressChange(BaseModel):
    new_address: str = Field(min_length=1, max_length=300, description="The full new delivery address")


class VoucherCreate(BaseModel):
    order_id: str = Field(min_length=1, max_length=32, description="The order the goodwill voucher is about")
    amount: float = Field(gt=0, le=1000, description="EGP value of the voucher")
    reason: str = Field(min_length=1, max_length=500)


class OrderChange(BaseModel):
    """What the shop records for a return, exchange, refund, cancellation or address change."""

    reference_id: str = Field(description="The shop's reference for the change, e.g. RET-30001")
    order_id: str
    status: str


class Refund(OrderChange):
    amount: float


class Voucher(BaseModel):
    reference_id: str
    voucher_code: str
    amount: float
    order_id: str


def create_app(data_path: str | None = None, harness_key: str | None = None) -> FastAPI:
    source = Path(data_path or os.environ.get("NILE_SANDBOX_DATA") or DEFAULT_DATA)
    data = json.loads(source.read_text(encoding="utf-8"))
    key = harness_key or os.environ.get("NILE_SANDBOX_HARNESS_KEY") or secrets.token_urlsafe(24)
    orders = {o["order_id"]: copy.deepcopy(o) for o in data["orders"]}
    customers = {c["customer_id"] for c in data["customers"]}
    lock = threading.Lock()
    def token_of(customer: str) -> str:
        return hmac.new(key.encode(), f"nile-sandbox-token:{customer}".encode(), hashlib.sha256).hexdigest()

    tokens: dict[str, str] = {token_of(c): c for c in customers}
    tickets: list[dict[str, str]] = []
    switches = {"fail_next_ticket_after_commit": False}
    bearer = HTTPBearer(auto_error=False)
    app = FastAPI(
        title=os.environ.get("NILE_SANDBOX_TITLE") or "Nile Style sandbox shop",
        version="1.0.0",
        description="TEST-ONLY sandbox of the Nile Style shop API. Synthetic data; no real customer or payment.",
    )

    def caller(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
        customer = tokens.get(credentials.credentials) if credentials else None
        if customer is None:
            raise HTTPException(401, "Not authenticated")
        return customer

    def own_order(order_id: str, customer: str) -> dict:
        order = orders.get(order_id)
        if order is None:
            raise HTTPException(404, "Order not found")
        if order["customer_id"] != customer:
            raise HTTPException(403, "This order belongs to another customer")
        return order

    def harness(x_harness_key: str = Header("")) -> None:
        if not secrets.compare_digest(x_harness_key, key):
            raise HTTPException(403, "harness key required")

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/orders/{order_id}", response_model=Order, tags=["orders"], operation_id="get_order",
             summary="Look up one of the caller's orders")  # fmt: skip
    def get_order(order_id: str, customer: str = Depends(caller)) -> dict:
        """Status, dates, total and item facts of one order. Only the customer who placed it may read it."""
        return own_order(order_id, customer)

    @app.post("/api/tickets", response_model=Ticket, status_code=201, tags=["support"], operation_id="create_ticket",
              summary="Open a support ticket about one of the caller's orders")  # fmt: skip
    def create_ticket(body: TicketCreate, customer: str = Depends(caller)) -> dict:
        own_order(body.order_id, customer)
        with lock:
            ticket = {"ticket_id": f"TCK-{5001 + len(tickets)}", "order_id": body.order_id, "status": "open",
                      "customer_id": customer, "subject": body.subject}  # fmt: skip
            tickets.append(ticket)
            fail = switches["fail_next_ticket_after_commit"]
            switches["fail_next_ticket_after_commit"] = False
        if fail:
            raise HTTPException(500, "The ticket was stored, then the server failed before answering")
        return ticket

    # ---- the rest of what the shop does for a customer. The shop checks facts about the order (whose it is, whether
    # it was delivered or shipped, the total); the shop's POLICY (14-day window, exclusions, approvals) is not here.
    changes: list[dict] = []

    def record(kind: str, prefix: str, customer: str, order_id: str, status: str, **extra: object) -> dict:
        with lock:
            row = {"kind": kind, "reference_id": f"{prefix}-{30001 + sum(c['kind'] == kind for c in changes)}",
                   "order_id": order_id, "status": status, "customer_id": customer, **extra}  # fmt: skip
            changes.append(row)
        return row

    def delivered(order: dict) -> dict:
        if order["order_status"] not in ("delivered", "returned") or not order.get("delivered_at"):
            raise HTTPException(409, "The order has not been delivered")
        return order

    def not_shipped(order: dict) -> dict:
        if order["order_status"] not in ("pending", "processing"):
            raise HTTPException(409, f"The order is {order['order_status']} and can no longer be changed")
        return order

    @app.get("/api/orders", response_model=OrderList, tags=["orders"], operation_id="list_my_orders",
             summary="List all of the caller's orders")  # fmt: skip
    def list_orders(customer: str = Depends(caller)) -> dict:
        """Order id, status, total and placement date of every order the caller placed, newest first."""
        mine = [o for o in orders.values() if o["customer_id"] == customer]
        return {"orders": sorted(mine, key=lambda o: o["placed_at"], reverse=True)}

    @app.post("/api/returns", response_model=OrderChange, status_code=201, tags=["after-sales"],
              operation_id="create_return", summary="Request a return of a delivered order")  # fmt: skip
    def create_return(body: ChangeRequest, customer: str = Depends(caller)) -> dict:
        """Opens a return for one of the caller's delivered orders. Returns the return reference (RET-...)."""
        delivered(own_order(body.order_id, customer))
        return record("return", "RET", customer, body.order_id, "requested", reason=body.reason)

    @app.post("/api/exchanges", response_model=OrderChange, status_code=201, tags=["after-sales"],
              operation_id="create_exchange", summary="Request an exchange of a delivered order")  # fmt: skip
    def create_exchange(body: ExchangeCreate, customer: str = Depends(caller)) -> dict:
        """Opens an exchange (optionally for another size) for one of the caller's delivered orders (EXC-...)."""
        delivered(own_order(body.order_id, customer))
        return record("exchange", "EXC", customer, body.order_id, "requested", reason=body.reason, new_size=body.new_size)

    @app.post("/api/refunds", response_model=Refund, status_code=201, tags=["payments"],
              operation_id="create_refund", summary="Refund money for a paid order")  # fmt: skip
    def create_refund(body: RefundCreate, customer: str = Depends(caller)) -> dict:
        """Refunds an amount (EGP, at most the order total) of a paid, delivered or returned order (REF-...)."""
        order = delivered(own_order(body.order_id, customer))
        if order["payment_status"] != "paid":
            raise HTTPException(409, "Nothing was paid for this order")
        if body.amount > order["order_total"]:
            raise HTTPException(422, "The amount is higher than the order total")
        return record("refund", "REF", customer, body.order_id, "pending", amount=body.amount, reason=body.reason)

    @app.post("/api/orders/{order_id}/cancel", response_model=OrderChange, tags=["orders"],
              operation_id="cancel_order", summary="Cancel an order that has not shipped")  # fmt: skip
    def cancel_order(order_id: str, customer: str = Depends(caller)) -> dict:
        """Cancels one of the caller's orders while it is still pending or processing (CAN-...)."""
        order = not_shipped(own_order(order_id, customer))
        row = record("cancel", "CAN", customer, order_id, "cancelled")
        order["order_status"] = "cancelled"
        return row

    @app.patch("/api/orders/{order_id}/delivery-address", response_model=OrderChange, tags=["orders"],
               operation_id="update_delivery_address", summary="Change the delivery address of an unshipped order")  # fmt: skip
    def update_delivery_address(order_id: str, body: AddressChange, customer: str = Depends(caller)) -> dict:
        """Replaces the delivery address of one of the caller's orders while it is still pending or processing (ADR-...)."""
        order = not_shipped(own_order(order_id, customer))
        row = record("address", "ADR", customer, order_id, "updated", new_address=body.new_address)
        order["delivery_address"] = {"street": body.new_address}
        return row

    @app.post("/api/vouchers", response_model=Voucher, status_code=201, tags=["payments"],
              operation_id="apply_voucher", summary="Issue a goodwill voucher for an order")  # fmt: skip
    def apply_voucher(body: VoucherCreate, customer: str = Depends(caller)) -> dict:
        """Issues a goodwill voucher (EGP, up to 1000) to the caller about one of their orders (VCH-...)."""
        own_order(body.order_id, customer)
        row = record("voucher", "VCH", customer, body.order_id, "issued", amount=body.amount, reason=body.reason)
        return {**row, "voucher_code": f"NILE-{row['reference_id'][-5:]}"}

    @app.post("/_harness/tokens", include_in_schema=False, dependencies=[Depends(harness)])
    def issue_token(body: dict) -> dict:
        customer = str(body.get("customer_id", ""))
        if customer not in customers:
            raise HTTPException(404, "unknown customer")
        return {"customer_id": customer, "token": token_of(customer)}

    @app.post("/_harness/switches", include_in_schema=False, dependencies=[Depends(harness)])
    def set_switches(body: dict) -> dict:
        with lock:
            switches.update({k: bool(v) for k, v in body.items() if k in switches})
        return dict(switches)

    @app.get("/_harness/state", include_in_schema=False, dependencies=[Depends(harness)])
    def state() -> dict:
        with lock:
            return {"tickets": copy.deepcopy(tickets), "changes": copy.deepcopy(changes), "switches": dict(switches)}

    return app
