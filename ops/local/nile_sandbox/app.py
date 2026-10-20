"""Nile Style sandbox shop API: the business API the tools service compiles tools against in the integrated demo (TEST-ONLY).

It serves the same customers and orders as the agent's demo shop (services/agent/fixtures/shop_001/backend.json, copied in memory
at start), so the customer the agent verifies by phone is the same customer whose sandbox credential the tools service uses.

Behavior and access rules, fixed before any tool is generated:
- Every business operation needs a bearer token; each token belongs to exactly one customer.
- GET /api/orders/{order_id} returns one order with its customer_id. Another customer's order is 403, an unknown one 404.
- POST /api/tickets opens a support ticket about one of the caller's own orders (201); another customer's order is 403.
- The after-sales operations (list orders, return, exchange, refund, cancel, delivery address, voucher) follow the same
  ownership rule and check order facts (delivered, not yet shipped, amount <= total) with 409/422; the shop POLICY is not here.
- Harness routes (hidden from the OpenAPI document, protected by the x-harness-key header) issue tokens, read state for
  independent verification, and make the next ticket answer 500 AFTER storing it (an outcome the caller cannot know).
- A customer's token is derived from the harness key (HMAC), so it stays valid when the sandbox restarts.
- Production-like deployment: with NILE_SANDBOX_SERVICE_TOKEN set, that ONE bearer token is a business SERVICE credential. The
  API then answers it for ANY customer's order (no ownership refusal: it is the shop's own systems) and records the change for
  the order's customer, exactly as a real API behaves for a back-office token. Nothing in the API separates customers for it, so
  whoever holds it must (the tools service does, with a check against the verified customer). Never set it on the sandbox.
- Fixture routes (/_fixtures/*, hidden from the OpenAPI document, protected by the x-fixture-key header) are what the tools service's
  automatic tests use: create a disposable test customer, create an order owned by that customer in a requested state,
  list the record kinds and states, read back what a run changed, and delete everything a run created.

Run: python -m uvicorn ops.local.nile_sandbox.app:create_app --factory --port 8102   (from the repository root)
Environment: NILE_SANDBOX_HARNESS_KEY (required for the harness routes), NILE_SANDBOX_FIXTURE_KEY (the fixture routes;
defaults to the harness key), NILE_SANDBOX_DATA (default: the agent's fixture).
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

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA = ROOT / "services" / "agent" / "fixtures" / "shop_001" / "backend.json"


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


SERVICE = "__service__"  # the caller of a request that carries the service token


def create_app(
    data_path: str | None = None,
    harness_key: str | None = None,
    fixture_key: str | None = None,
    service_token: str | None = None,
) -> FastAPI:
    source = Path(data_path or os.environ.get("NILE_SANDBOX_DATA") or DEFAULT_DATA)
    data = json.loads(source.read_text(encoding="utf-8"))
    key = harness_key or os.environ.get("NILE_SANDBOX_HARNESS_KEY") or secrets.token_urlsafe(24)
    service_secret = service_token or os.environ.get("NILE_SANDBOX_SERVICE_TOKEN") or ""
    fixture_secret = fixture_key or os.environ.get("NILE_SANDBOX_FIXTURE_KEY") or key
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
        if credentials and service_secret and secrets.compare_digest(credentials.credentials, service_secret):
            return SERVICE
        customer = tokens.get(credentials.credentials) if credentials else None
        if customer is None:
            raise HTTPException(401, "Not authenticated")
        return customer

    def own_order(order_id: str, customer: str) -> dict:
        order = orders.get(order_id)
        if order is None:
            raise HTTPException(404, "Order not found")
        if customer != SERVICE and order["customer_id"] != customer:
            raise HTTPException(403, "This order belongs to another customer")
        return order

    def acting(customer: str, order_id: str) -> str:
        """The customer a change is recorded for: the caller, or for the service token the order's own customer."""
        return orders[order_id]["customer_id"] if customer == SERVICE else customer

    def harness(x_harness_key: str = Header("")) -> None:
        if not secrets.compare_digest(x_harness_key, key):
            raise HTTPException(403, "harness key required")

    def fixture_auth(x_fixture_key: str = Header("")) -> None:
        if not secrets.compare_digest(x_fixture_key, fixture_secret):
            raise HTTPException(403, "fixture key required")

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
                      "customer_id": acting(customer, body.order_id), "subject": body.subject}  # fmt: skip
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
        mine = [o for o in orders.values() if customer == SERVICE or o["customer_id"] == customer]
        return {"orders": sorted(mine, key=lambda o: o["placed_at"], reverse=True)}

    @app.post("/api/returns", response_model=OrderChange, status_code=201, tags=["after-sales"],
              operation_id="create_return", summary="Request a return of a delivered order")  # fmt: skip
    def create_return(body: ChangeRequest, customer: str = Depends(caller)) -> dict:
        """Opens a return for one of the caller's delivered orders. Returns the return reference (RET-...)."""
        delivered(own_order(body.order_id, customer))
        return record("return", "RET", acting(customer, body.order_id), body.order_id, "requested", reason=body.reason)

    @app.post("/api/exchanges", response_model=OrderChange, status_code=201, tags=["after-sales"],
              operation_id="create_exchange", summary="Request an exchange of a delivered order")  # fmt: skip
    def create_exchange(body: ExchangeCreate, customer: str = Depends(caller)) -> dict:
        """Opens an exchange (optionally for another size) for one of the caller's delivered orders (EXC-...)."""
        delivered(own_order(body.order_id, customer))
        return record("exchange", "EXC", acting(customer, body.order_id), body.order_id, "requested", reason=body.reason, new_size=body.new_size)

    @app.post("/api/refunds", response_model=Refund, status_code=201, tags=["payments"],
              operation_id="create_refund", summary="Refund money for a paid order")  # fmt: skip
    def create_refund(body: RefundCreate, customer: str = Depends(caller)) -> dict:
        """Refunds an amount (EGP, at most the order total) of a paid, delivered or returned order (REF-...)."""
        order = delivered(own_order(body.order_id, customer))
        if order["payment_status"] != "paid":
            raise HTTPException(409, "Nothing was paid for this order")
        if body.amount > order["order_total"]:
            raise HTTPException(422, "The amount is higher than the order total")
        return record("refund", "REF", acting(customer, body.order_id), body.order_id, "pending", amount=body.amount, reason=body.reason)

    @app.post("/api/orders/{order_id}/cancel", response_model=OrderChange, tags=["orders"],
              operation_id="cancel_order", summary="Cancel an order that has not shipped")  # fmt: skip
    def cancel_order(order_id: str, customer: str = Depends(caller)) -> dict:
        """Cancels one of the caller's orders while it is still pending or processing (CAN-...)."""
        order = not_shipped(own_order(order_id, customer))
        row = record("cancel", "CAN", acting(customer, order_id), order_id, "cancelled")
        order["order_status"] = "cancelled"
        return row

    @app.patch("/api/orders/{order_id}/delivery-address", response_model=OrderChange, tags=["orders"],
               operation_id="update_delivery_address", summary="Change the delivery address of an unshipped order")  # fmt: skip
    def update_delivery_address(order_id: str, body: AddressChange, customer: str = Depends(caller)) -> dict:
        """Replaces the delivery address of one of the caller's orders while it is still pending or processing (ADR-...)."""
        order = not_shipped(own_order(order_id, customer))
        row = record("address", "ADR", acting(customer, order_id), order_id, "updated", new_address=body.new_address)
        order["delivery_address"] = {"street": body.new_address}
        return row

    @app.post("/api/vouchers", response_model=Voucher, status_code=201, tags=["payments"],
              operation_id="apply_voucher", summary="Issue a goodwill voucher for an order")  # fmt: skip
    def apply_voucher(body: VoucherCreate, customer: str = Depends(caller)) -> dict:
        """Issues a goodwill voucher (EGP, up to 1000) to the caller about one of their orders (VCH-...)."""
        own_order(body.order_id, customer)
        row = record("voucher", "VCH", acting(customer, body.order_id), body.order_id, "issued", amount=body.amount, reason=body.reason)
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

    # ---- test fixtures (TEST-ONLY): disposable customers and orders in a requested state, grouped by test run ----
    fixture_runs: dict[str, dict[str, list[str]]] = {}
    fixture_counter = {"orders": 0}
    order_states = ("delivered", "pending", "processing", "shipped")
    overrides = {"order_total": float, "item_name": str, "payment_status": str, "is_clearance": bool}

    def fixture_run(run_id: str) -> dict[str, list[str]]:
        return fixture_runs.setdefault(run_id, {"accounts": [], "orders": []})

    def fixture_accounts() -> set[str]:
        return {a for r in fixture_runs.values() for a in r["accounts"]}

    @app.post("/_fixtures/accounts", include_in_schema=False, dependencies=[Depends(fixture_auth)])
    def fixture_account(body: dict) -> dict:
        label = str(body.get("label", "test"))[:40]
        with lock:
            customer = f"F-{secrets.token_hex(4).upper()}"
            customers.add(customer)
            tokens[token_of(customer)] = customer
            fixture_run(str(body.get("run_id") or "default"))["accounts"].append(customer)
        return {"account_id": customer, "label": label, "credential": {"token": token_of(customer)}, "context": {"customer_id": customer}}

    @app.get("/_fixtures/kinds", include_in_schema=False, dependencies=[Depends(fixture_auth)])
    def fixture_kinds() -> list[dict]:
        return [{"kind": "order", "states": list(order_states), "id_field": "order_id", "selects_by": ["order_id"],
                 "attributes": sorted(overrides)}]

    @app.post("/_fixtures/records", include_in_schema=False, dependencies=[Depends(fixture_auth)])
    def fixture_record(body: dict) -> dict:
        account, kind, state = str(body.get("account_id", "")), str(body.get("kind", "order")), str(body.get("state") or "delivered")
        if kind != "order":
            raise HTTPException(422, "unknown record kind")
        if state not in order_states:
            raise HTTPException(422, f"state must be one of {', '.join(order_states)}")
        attributes = body.get("attributes") or {}
        if not isinstance(attributes, dict) or set(attributes) - set(overrides):
            raise HTTPException(422, "unsupported attributes")
        with lock:
            run = fixture_run(str(body.get("run_id") or "default"))
            if account not in fixture_accounts():
                raise HTTPException(404, "unknown fixture account")
            fixture_counter["orders"] += 1
            order_id = f"NS-F{fixture_counter['orders']:05d}"
            order = {"order_id": order_id, "customer_id": account, "order_status": state, "payment_status": "paid", "order_total": 500.0,
                     "placed_at": "2026-09-01", "shipped_at": None, "delivered_at": None, "expected_delivery_date": "2026-11-30",
                     "item_name": "Fixture linen shirt", "product_category": "clothing", "is_clearance": False, "item_condition": "unused",
                     "tracking_number": None, "delivery_address": {"street": "1 Fixture Street"}}  # fmt: skip
            if state in ("shipped", "delivered"):
                order.update(shipped_at="2026-09-03", tracking_number=f"TRK-{order_id[3:]}")
            if state == "delivered":
                order.update(delivered_at="2026-09-10", expected_delivery_date="2026-09-10")
            for k, v in attributes.items():
                order[k] = overrides[k](v)
            orders[order_id] = order
            run["orders"].append(order_id)
        return {"record_id": order_id, "kind": "order", "state": state}

    @app.post("/_fixtures/revoke", include_in_schema=False, dependencies=[Depends(fixture_auth)])
    def fixture_revoke(body: dict) -> dict:
        """Invalidates a fixture account's credential (for the bad-credential diagnostic). Not reversible."""
        with lock:
            account = str(body.get("account_id", ""))
            if account not in fixture_accounts():
                raise HTTPException(404, "unknown fixture account")
            tokens.pop(token_of(account), None)
        return {"account_id": account, "revoked": True}

    @app.post("/_fixtures/reset", include_in_schema=False, dependencies=[Depends(fixture_auth)])
    def fixture_reset(body: dict) -> dict:
        run_id = str(body.get("run_id") or "default")
        with lock:
            run = fixture_runs.get(run_id) or {"accounts": [], "orders": []}
            only = body.get("record_ids")
            doomed = [i for i in run["orders"] if only is None or i in only]
            removed_orders = len(doomed)
            for order_id in doomed:
                orders.pop(order_id, None)
            run["orders"] = [i for i in run["orders"] if i not in doomed]
            removed_accounts = 0
            if only is None and not body.get("keep_accounts"):
                for account in run["accounts"]:
                    tokens.pop(token_of(account), None)
                    customers.discard(account)
                    removed_accounts += 1
                run["accounts"] = []
            changes[:] = [c for c in changes if c["order_id"] in orders]
        return {"run_id": run_id, "orders_removed": removed_orders, "accounts_removed": removed_accounts}

    @app.get("/_fixtures/state", include_in_schema=False, dependencies=[Depends(fixture_auth)])
    def fixture_state(run_id: str = "default") -> dict:
        with lock:
            run = fixture_runs.get(run_id) or {"accounts": [], "orders": []}
            return {"run_id": run_id, "accounts": list(run["accounts"]),
                    "records": [copy.deepcopy(orders[i]) for i in run["orders"] if i in orders],
                    "changes": [copy.deepcopy(c) for c in changes if c["order_id"] in run["orders"]]}

    return app
