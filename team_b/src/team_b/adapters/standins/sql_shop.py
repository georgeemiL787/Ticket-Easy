"""A stand-in shop that keeps its data in SQLite: used for Noon (noon_eg). Same tool contract as the JSON stand-in.

The StandinShop (shop.py) handles everything that is not about data: the published tools, idempotency, the failure
switches, the audit log. For a tenant whose folder has a `seed.json` it uses this module instead of the in-memory
Backend of shop_backend.py: the tables are loaded from the seed, every tool reads and writes real rows, and the audit
log and the idempotency memory are rows too, so a change survives a restart (when a database folder is given; without
one the database lives in memory and every shop starts from the seed, which is what tests want).

Like the JSON stand-in this is only the shop's PHYSICAL rules (a return needs a delivered order, one open return per
order). The business policy (return window, excluded categories, refund destinations) belongs to the rule checker.
The data belongs to Team A's Noon mock data (scripts/build_noon_fixtures.py copies it); the real shop is Team C's.
"""

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from team_b.adapters.standins.shop_backend import Fail, Ok, Outcome

SCHEMA = """
CREATE TABLE customers (
    customer_id TEXT PRIMARY KEY, name_en TEXT, name_ar TEXT, phone TEXT NOT NULL, email TEXT, city TEXT,
    preferred_language TEXT, verified INTEGER, verified_via TEXT, wallet_egp REAL, member_since TEXT
);
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(customer_id), section TEXT,
    status TEXT NOT NULL, placed_at TEXT, expected_delivery_date TEXT, delivered_at TEXT, payment_method TEXT,
    shipping_fee_egp REAL, cod_fee_egp REAL, total_egp REAL NOT NULL
);
CREATE TABLE order_items (
    item_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(order_id), sku TEXT, name TEXT, category TEXT,
    product_subcategory TEXT, qty INTEGER, price_egp REAL, return_eligible_tag INTEGER, warranty_tag INTEGER,
    item_condition TEXT, is_clearance INTEGER
);
CREATE TABLE returns (
    return_id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(order_id), customer_id TEXT NOT NULL,
    item_id TEXT, payment_method TEXT, status TEXT NOT NULL, reason TEXT, requested_at TEXT, received_at TEXT,
    qc_result TEXT, refund_amount_egp REAL, refund_destination TEXT, refund_initiated_at TEXT
);
CREATE TABLE tickets (
    ticket_id TEXT PRIMARY KEY, order_id TEXT, subject TEXT NOT NULL, description TEXT, created_at TEXT
);
CREATE TABLE counters (prefix TEXT PRIMARY KEY, last INTEGER NOT NULL);
CREATE TABLE audit_log (seq INTEGER PRIMARY KEY, entry TEXT NOT NULL);
CREATE TABLE idempotency (key TEXT PRIMARY KEY, tool TEXT NOT NULL, arguments TEXT NOT NULL, result TEXT NOT NULL);
CREATE INDEX orders_by_customer ON orders(customer_id);
CREATE INDEX items_by_order ON order_items(order_id);
CREATE INDEX returns_by_order ON returns(order_id);
"""
START = {"RET": 6000, "TKT": 7000}  # the seed's returns are RET-5001 and up
ORDER_FIELDS = (
    "order_id", "customer_id", "order_status", "payment_status", "order_total", "placed_at", "shipped_at",
    "delivered_at", "expected_delivery_date", "item_id", "item_name", "product_category", "product_subcategory",
    "is_clearance", "item_condition", "return_eligible_tag", "warranty_tag", "order_section", "payment_method",
    "tracking_number",
)  # fmt: skip
OPEN_RETURN = ("requested", "return_in_progress", "received", "refund_approved", "refund_initiated")


def _digits(phone: str) -> str:
    return re.sub(r"[\s\-()]", "", phone)


def _not_found(what: str, key: str) -> Fail:
    return Fail("NOT_FOUND", f"{what} {key} does not exist")


class SqlBackend:
    """The Noon tables in SQLite and what the tools do to them. `path` None: in memory (starts from the seed)."""

    def __init__(self, seed: Path, today: Callable[[], date], path: Path | None = None, *, fresh: bool = False) -> None:
        self._today = today
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            if fresh and path.exists():
                path.unlink()
        self._db = sqlite3.connect(str(path) if path is not None else ":memory:")
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if self._db.execute("SELECT name FROM sqlite_master WHERE name = 'orders'").fetchone() is None:
            self._db.executescript(SCHEMA)
            self._load(json.loads(seed.read_text(encoding="utf-8")))

    # ---- loading ----

    def _load(self, data: dict[str, Any]) -> None:
        db = self._db
        for c in data["customers"]:
            db.execute(
                "INSERT INTO customers VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (c["customer_id"], c.get("name_en"), c.get("name_ar"), c["phone"], c.get("email"), c.get("city"),
                 c.get("preferred_language"), int(bool(c.get("verified"))), c.get("verified_via"),
                 c.get("noon_wallet_egp"), c.get("member_since")),
            )  # fmt: skip
        for o in data["orders"]:
            db.execute(
                "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (o["order_id"], o["customer_id"], o.get("section"), o["status"], o.get("placed_at"),
                 o.get("expected_delivery_date"), o.get("delivered_at"), o.get("payment_method"),
                 o.get("shipping_fee_egp"), o.get("cod_fee_egp"), o["total_egp"]),
            )  # fmt: skip
            for i in o["items"]:
                db.execute(
                    "INSERT INTO order_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (i["item_id"], o["order_id"], i.get("sku"), i.get("name"), i.get("category"),
                     i.get("product_subcategory"), i.get("qty"), i.get("price_egp"),
                     int(bool(i.get("return_eligible_tag"))), int(bool(i.get("warranty_tag"))),
                     i.get("item_condition"), int(bool(i.get("is_clearance")))),
                )  # fmt: skip
        for r in data["returns"]:
            db.execute(
                "INSERT INTO returns VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["return_id"], r["order_id"], r["customer_id"], None, r.get("payment_method"), r["status"], None,
                 r.get("requested_at"), r.get("received_at"), r.get("qc_result"), r.get("refund_amount_egp"),
                 r.get("refund_destination"), r.get("refund_initiated_at")),
            )  # fmt: skip
        db.commit()

    # ---- what the stand-in shop needs besides the tools ----

    def next_reference(self, prefix: str) -> str:
        row = self._db.execute("SELECT last FROM counters WHERE prefix = ?", (prefix,)).fetchone()
        number = (row["last"] if row else START[prefix]) + 1
        self._db.execute("INSERT OR REPLACE INTO counters VALUES (?, ?)", (prefix, number))
        return f"{prefix}-{number}"

    def order(self, order_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT o.*, i.item_id, i.name AS item_name, i.category, i.product_subcategory, i.is_clearance, "
            "i.item_condition, i.return_eligible_tag, i.warranty_tag FROM orders o "
            "LEFT JOIN order_items i ON i.order_id = o.order_id WHERE o.order_id = ? ORDER BY i.item_id LIMIT 1",
            (order_id,),
        ).fetchone()
        if row is None:
            return None
        paid = row["status"] != "pending" and (row["payment_method"] != "cash_on_delivery" or row["delivered_at"])
        return {
            "order_id": row["order_id"], "customer_id": row["customer_id"], "order_status": row["status"],
            "payment_status": "paid" if paid else "pending", "order_total": row["total_egp"],
            "placed_at": row["placed_at"], "shipped_at": None, "delivered_at": row["delivered_at"],
            "expected_delivery_date": row["expected_delivery_date"], "item_id": row["item_id"],
            "item_name": row["item_name"] or "", "product_category": row["category"] or "",
            "product_subcategory": row["product_subcategory"] or "", "is_clearance": bool(row["is_clearance"]),
            "item_condition": row["item_condition"] or "", "return_eligible_tag": bool(row["return_eligible_tag"]),
            "warranty_tag": bool(row["warranty_tag"]), "order_section": row["section"] or "",
            "payment_method": row["payment_method"] or "", "tracking_number": None,
        }  # fmt: skip

    def records(self, kind: str) -> list[dict[str, Any]]:
        """Created returns and tickets (the kinds of the JSON stand-in that this shop has)."""
        if kind == "return":
            rows = self._db.execute("SELECT * FROM returns WHERE reason IS NOT NULL ORDER BY return_id").fetchall()
            return [{"reference_id": r["return_id"], "order_id": r["order_id"], "reason": r["reason"]} for r in rows]
        if kind == "ticket":
            rows = self._db.execute("SELECT * FROM tickets ORDER BY ticket_id").fetchall()
            return [{"reference_id": r["ticket_id"], "subject": r["subject"], "order_id": r["order_id"]} for r in rows]
        return []

    # ---- the audit log and the idempotency memory live in the database ----

    def save_audit(self, seq: int, entry_json: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO audit_log VALUES (?, ?)", (seq, entry_json))
        self._db.commit()

    def load_audit(self) -> list[str]:
        return [r["entry"] for r in self._db.execute("SELECT entry FROM audit_log ORDER BY seq")]

    def save_replay(self, key: str, tool: str, arguments: str, result_json: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO idempotency VALUES (?,?,?,?)", (key, tool, arguments, result_json))
        self._db.commit()

    def load_replay(self) -> dict[str, tuple[str, str, str]]:
        rows = self._db.execute("SELECT key, tool, arguments, result FROM idempotency")
        return {r["key"]: (r["tool"], r["arguments"], r["result"]) for r in rows}

    def commit(self) -> None:
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    # ---- helpers for the tools ----

    def customer(self, customer_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self._db.execute(
            "SELECT * FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()
        return row

    def rows(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        return list(self._db.execute(sql, args))

    def execute(self, sql: str, *args: Any) -> None:
        self._db.execute(sql, args)

    def today(self) -> str:
        return self._today().isoformat()


# ---- the tools (physical rules only) ----


def verify_customer(b: SqlBackend, a: dict[str, Any]) -> Outcome:
    order = b.order(a["order_id"])
    customer = b.customer(order["customer_id"]) if order else None
    ok = customer is not None and _digits(customer["phone"]) == _digits(a["phone"])
    # An unknown order and a wrong phone look the same: the shop never reveals which orders exist.
    return Ok({"verified": ok, "customer_id": customer["customer_id"] if ok and customer else None})


def get_order(b: SqlBackend, a: dict[str, Any]) -> Outcome:
    order = b.order(a["order_id"])
    return Ok({k: order[k] for k in ORDER_FIELDS}) if order else _not_found("order", a["order_id"])


def list_customer_orders(b: SqlBackend, a: dict[str, Any]) -> Outcome:
    if b.customer(a["customer_id"]) is None:
        return _not_found("customer", a["customer_id"])
    rows = b.rows("SELECT * FROM orders WHERE customer_id = ? ORDER BY placed_at, order_id", a["customer_id"])
    return Ok({"orders": [
        {"order_id": r["order_id"], "order_status": r["status"], "order_total": r["total_egp"],
         "placed_at": r["placed_at"]}
        for r in rows
    ]})  # fmt: skip


def get_return_status(b: SqlBackend, a: dict[str, Any]) -> Outcome:
    if b.order(a["order_id"]) is None:
        return _not_found("order", a["order_id"])
    rows = b.rows("SELECT * FROM returns WHERE order_id = ? ORDER BY return_id", a["order_id"])
    keys = ("return_id", "status", "requested_at", "received_at", "qc_result", "refund_amount_egp",
            "refund_destination", "refund_initiated_at")  # fmt: skip
    mine = {"order_id": a["order_id"], "customer_id": b.order(a["order_id"])["customer_id"]}  # type: ignore[index]
    return Ok({**mine, "returns": [{k: r[k] for k in keys} for r in rows]})


def create_return(b: SqlBackend, a: dict[str, Any]) -> Outcome:
    order = b.order(a["order_id"])
    if order is None:
        return _not_found("order", a["order_id"])
    if order["order_status"] != "delivered":
        return Fail("REJECTED", f"only delivered orders can have a return (status is {order['order_status']})")
    item_id = a.get("item_id") or order["item_id"]
    if any(r["status"] in OPEN_RETURN for r in b.rows("SELECT status FROM returns WHERE order_id = ?", a["order_id"])):
        return Fail("REJECTED", "this order already has an open return")
    ref = b.next_reference("RET")
    b.execute(
        "INSERT INTO returns VALUES (?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL)",
        ref, a["order_id"], order["customer_id"], item_id, order["payment_method"], "requested", a["reason"], b.today(),
    )  # fmt: skip
    b.execute("UPDATE orders SET status = 'return_in_progress' WHERE order_id = ?", a["order_id"])
    return Ok({"reference_id": ref, "order_id": a["order_id"], "status": "requested"}, ref, True)


def create_ticket(b: SqlBackend, a: dict[str, Any]) -> Outcome:
    if a.get("order_id") and b.order(a["order_id"]) is None:
        return _not_found("order", a["order_id"])
    ref = b.next_reference("TKT")
    b.execute(
        "INSERT INTO tickets VALUES (?,?,?,?,?)", ref, a.get("order_id"), a["subject"], a.get("description"), b.today()
    )
    return Ok({"reference_id": ref, "status": "open"}, ref, True)


Handler = Callable[[SqlBackend, dict[str, Any]], Outcome]
HANDLERS: dict[str, Handler] = {
    f.__name__: f
    for f in (verify_customer, get_order, list_customer_orders, get_return_status, create_return, create_ticket)
}
