"""Nile Style sandbox shop API: the business API Team C compiles tools against in the integrated demo (TEST-ONLY).

It serves the same customers and orders as Team B's demo shop (team_b/fixtures/shop_001/backend.json, copied in memory
at start), so the customer Team B verifies by phone is the same customer whose sandbox credential Team C uses.

Behavior and access rules, fixed before any tool is generated:
- Every business operation needs a bearer token; each token belongs to exactly one customer.
- GET /api/orders/{order_id} returns one order with its customer_id. Another customer's order is 403, an unknown one 404.
- POST /api/tickets opens a support ticket about one of the caller's own orders (201); another customer's order is 403.
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
            return {"tickets": copy.deepcopy(tickets), "switches": dict(switches)}

    return app
