"""Tenant-scoped reads over the SQLite data layer.

Every function takes tenant_id and every query filters on it; an unknown tenant raises TenantNotFound and a
missing record returns None. Connections are read-only.

Search does not re-implement retrieval: it rebuilds the existing TenantIndex from database rows and calls
knowledge_service.search.retrieval, so hybrid fusion, MIN_COSINE / MIN_BM25 and the RetrievalResult / empty-result
contracts are exactly those of the file-based index.
"""

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

from knowledge_service.config import settings
from knowledge_service.db import connect
from knowledge_service.search import retrieval
from knowledge_service.search.bm25 import BM25
from knowledge_service.search.embeddings import Embedder
from knowledge_service.search.index import TenantIndex, TenantNotFound
from knowledge_service.policy.check import derive_facts
from knowledge_service.schemas import Passage, PastTicketResult, RetrievalResult, SearchKnowledgeRequest, SearchPastTicketsRequest

_BOOL_COLUMNS = {"verified", "is_clearance", "return_eligible_tag", "warranty_tag", "current", "fictional"}
# check_action facts in the order the scenarios list them. The first three are always present (None when
# the backend has no value); the rest are left out when the tenant's data has no such field.
_ALWAYS_FACTS = ("order_status", "delivered_at", "expected_delivery_date")
_OPTIONAL_FACTS = ("product_category", "product_subcategory", "is_clearance", "item_condition", "order_section",
                   "return_eligible_tag", "warranty_tag", "payment_method")


def _row(row: sqlite3.Row | None, drop: tuple[str, ...] = ()) -> dict | None:
    if row is None:
        return None
    out = {}
    for key in row.keys():
        if key in drop:
            continue
        value = row[key]
        out[key] = bool(value) if key in _BOOL_COLUMNS and value is not None else value
    return out


def _open(tenant_id: str, db_path: Path | None) -> sqlite3.Connection:
    conn = connect(db_path, readonly=True)
    if conn.execute("SELECT 1 FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone() is None:
        conn.close()
        raise TenantNotFound(f"Unknown tenant '{tenant_id}' in the database")
    return conn


def tenant_as_of(tenant_id: str, db_path: Path | None = None) -> date:
    """The tenant's fixed "today" (mock data), or the real date for a tenant without one."""
    conn = _open(tenant_id, db_path)
    try:
        value = conn.execute("SELECT as_of FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone()[0]
    finally:
        conn.close()
    return date.fromisoformat(value) if value else date.today()


# ------------------------------------------------------------------ structured lookups

def get_customer(tenant_id: str, customer_id: str, db_path: Path | None = None) -> dict | None:
    conn = _open(tenant_id, db_path)
    try:
        return _row(conn.execute("SELECT * FROM customers WHERE tenant_id = ? AND customer_id = ?",
                                 (tenant_id, customer_id)).fetchone())
    finally:
        conn.close()


def list_orders_for_customer(tenant_id: str, customer_id: str, db_path: Path | None = None) -> list[dict]:
    conn = _open(tenant_id, db_path)
    try:
        rows = conn.execute(
            "SELECT order_id, order_section, order_status, order_total, placed_at, delivered_at FROM orders "
            "WHERE tenant_id = ? AND customer_id = ? ORDER BY placed_at, order_id", (tenant_id, customer_id))
        return [_row(r) for r in rows]
    finally:
        conn.close()


def get_order(tenant_id: str, order_id: str, db_path: Path | None = None) -> dict | None:
    """The order with its items (in line order). delivery_address is decoded from JSON."""
    conn = _open(tenant_id, db_path)
    try:
        order = _row(conn.execute("SELECT * FROM orders WHERE tenant_id = ? AND order_id = ?",
                                  (tenant_id, order_id)).fetchone())
        if order is None:
            return None
        if order["delivery_address"]:
            order["delivery_address"] = json.loads(order["delivery_address"])
        order["items"] = [_row(r, drop=("tenant_id", "order_id")) for r in conn.execute(
            "SELECT * FROM order_items WHERE tenant_id = ? AND order_id = ? ORDER BY line_no",
            (tenant_id, order_id))]
        return order
    finally:
        conn.close()


def get_return(tenant_id: str, return_id: str, db_path: Path | None = None) -> dict | None:
    conn = _open(tenant_id, db_path)
    try:
        return _row(conn.execute("SELECT * FROM returns WHERE tenant_id = ? AND return_id = ?",
                                 (tenant_id, return_id)).fetchone())
    finally:
        conn.close()


def build_check_action_facts(tenant_id: str, order_id: str, item_id: str, as_of: date | None = None,
                             include_derived: bool = False, db_path: Path | None = None) -> dict[str, Any] | None:
    """Verified backend facts for one order item, in the shape check_action and the scenarios use.

    Returns None if the order or item does not exist for this tenant (or the item is on another order).
    With include_derived, days_since_delivery / days_late are added by check_action's own derive_facts as
    of `as_of`, which defaults to the tenant's as_of (2026-10-08 for noon_eg), never the wall clock for a
    mock tenant.
    """
    conn = _open(tenant_id, db_path)
    try:
        row = conn.execute(
            "SELECT o.order_status, o.delivered_at, o.expected_delivery_date, i.product_category, "
            "i.product_subcategory, i.is_clearance, i.item_condition, o.order_section, i.return_eligible_tag, "
            "i.warranty_tag, o.payment_method "
            "FROM orders o JOIN order_items i ON i.tenant_id = o.tenant_id AND i.order_id = o.order_id "
            "WHERE o.tenant_id = ? AND o.order_id = ? AND i.item_id = ?", (tenant_id, order_id, item_id)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    values = _row(row)
    facts = {k: values[k] for k in _ALWAYS_FACTS}
    facts.update({k: values[k] for k in _OPTIONAL_FACTS if values[k] is not None})
    if include_derived:
        facts = derive_facts(facts, as_of or tenant_as_of(tenant_id, db_path))
    return facts


# ------------------------------------------------------------------ telecom lookups (category 'telecom')

_TEL_BOOL = {"roaming_enabled", "ported_in", "auto_renew", "is_duplicate", "device_locked", "ntra_referral",
             "compensation_eligible", "marketing_opt_out", "is_demo"}


def _tel_row(row: sqlite3.Row | None, drop: tuple[str, ...] = ()) -> dict | None:
    out = _row(row, drop)
    if out:
        for key in _TEL_BOOL & out.keys():
            out[key] = None if out[key] is None else bool(out[key])
    return out


def _all(conn, sql: str, *params) -> list[dict]:
    return [_tel_row(r) for r in conn.execute(sql, params)]


def get_line(tenant_id: str, msisdn: str | None = None, line_id: str | None = None,
             db_path: Path | None = None) -> dict | None:
    """A line by its number, home landline or id, with its plan and its active bundles."""
    if not (msisdn or line_id):
        raise ValueError("pass msisdn or line_id")
    conn = _open(tenant_id, db_path)
    try:
        if line_id:
            row = conn.execute("SELECT * FROM tel_lines WHERE tenant_id = ? AND line_id = ?", (tenant_id, line_id))
        else:
            row = conn.execute("SELECT * FROM tel_lines WHERE tenant_id = ? AND (msisdn = ? OR home_landline = ?)",
                               (tenant_id, msisdn, msisdn))
        line = _tel_row(row.fetchone())
        if line is None:
            return None
        line["plan"] = _tel_row(conn.execute("SELECT plan_id, plan_type, name_en, name_ar, monthly_fee FROM tel_plans "
                                             "WHERE tenant_id = ? AND plan_id = ?",
                                             (tenant_id, line["plan_id"])).fetchone(), drop=())
        line["active_bundles"] = _all(conn, """
            SELECT lb.subscription_id, lb.bundle_id, b.name_en, b.name_ar, lb.subscribed_at, lb.expires_at,
                   lb.auto_renew, lb.data_mb_total, lb.data_mb_used, lb.minutes_total, lb.minutes_used, lb.price
            FROM tel_line_bundles lb JOIN tel_bundles b ON b.tenant_id = lb.tenant_id AND b.bundle_id = lb.bundle_id
            WHERE lb.tenant_id = ? AND lb.line_id = ? AND lb.status = 'active' ORDER BY lb.subscribed_at""",
                                      tenant_id, line["line_id"])
        return line
    finally:
        conn.close()


def list_lines_for_customer(tenant_id: str, customer_id: str, db_path: Path | None = None) -> list[dict]:
    conn = _open(tenant_id, db_path)
    try:
        return _all(conn, "SELECT line_id, account_id, msisdn, home_landline, line_type, plan_id, status, "
                          "prepaid_balance FROM tel_lines WHERE tenant_id = ? AND customer_id = ? "
                          "ORDER BY activated_at, line_id", tenant_id, customer_id)
    finally:
        conn.close()


def get_account(tenant_id: str, account_id: str, db_path: Path | None = None) -> dict | None:
    conn = _open(tenant_id, db_path)
    try:
        account = _tel_row(conn.execute("SELECT * FROM tel_accounts WHERE tenant_id = ? AND account_id = ?",
                                        (tenant_id, account_id)).fetchone())
        if account:
            account["lines"] = _all(conn, "SELECT line_id, msisdn, home_landline, line_type, status FROM tel_lines "
                                          "WHERE tenant_id = ? AND account_id = ? ORDER BY line_id",
                                    tenant_id, account_id)
        return account
    finally:
        conn.close()


def list_invoices(tenant_id: str, account_id: str, limit: int = 6, db_path: Path | None = None) -> list[dict]:
    """Newest first."""
    conn = _open(tenant_id, db_path)
    try:
        return _all(conn, "SELECT * FROM tel_invoices WHERE tenant_id = ? AND account_id = ? "
                          "ORDER BY issued_at DESC, invoice_id DESC LIMIT ?", tenant_id, account_id, limit)
    finally:
        conn.close()


def get_invoice(tenant_id: str, invoice_id: str, db_path: Path | None = None) -> dict | None:
    conn = _open(tenant_id, db_path)
    try:
        invoice = _tel_row(conn.execute("SELECT * FROM tel_invoices WHERE tenant_id = ? AND invoice_id = ?",
                                        (tenant_id, invoice_id)).fetchone())
        if invoice:
            invoice["items"] = _all(conn, "SELECT item_id, line_id, item_type, description, amount FROM "
                                          "tel_invoice_items WHERE tenant_id = ? AND invoice_id = ? ORDER BY item_id",
                                    tenant_id, invoice_id)
        return invoice
    finally:
        conn.close()


def list_transactions(tenant_id: str, line_id: str, since: str | None = None, limit: int = 50,
                      db_path: Path | None = None) -> list[dict]:
    """A line's money movements, newest first; `since` is an ISO date or datetime."""
    conn = _open(tenant_id, db_path)
    try:
        return _all(conn, "SELECT * FROM tel_transactions WHERE tenant_id = ? AND line_id = ? AND created_at >= ? "
                          "ORDER BY created_at DESC, txn_id DESC LIMIT ?", tenant_id, line_id, since or "", limit)
    finally:
        conn.close()


def get_complaint(tenant_id: str, complaint_id: str, db_path: Path | None = None) -> dict | None:
    conn = _open(tenant_id, db_path)
    try:
        complaint = _tel_row(conn.execute("SELECT * FROM tel_complaints WHERE tenant_id = ? AND complaint_id = ?",
                                          (tenant_id, complaint_id)).fetchone())
        if complaint:
            complaint["refunds"] = _all(conn, "SELECT refund_id, reason, amount, method, status, processed_at FROM "
                                              "tel_refunds WHERE tenant_id = ? AND complaint_id = ? ORDER BY refund_id",
                                        tenant_id, complaint_id)
        return complaint
    finally:
        conn.close()


def verify_identity(tenant_id: str, number: str, nid_last4: str, db_path: Path | None = None) -> str | None:
    """Telecom identity check: line number (or home landline) plus the last 4 digits of the national ID.

    Returns the customer_id when both match, else None. An unknown number and a wrong ID look the same, and the
    stored digits are never returned.
    """
    import hmac

    conn = _open(tenant_id, db_path)
    try:
        row = conn.execute("""
            SELECT l.customer_id, p.nid_last4 FROM tel_lines l
            JOIN tel_customer_profiles p ON p.tenant_id = l.tenant_id AND p.customer_id = l.customer_id
            WHERE l.tenant_id = ? AND (l.msisdn = ? OR l.home_landline = ?)""", (tenant_id, number, number)).fetchone()
    finally:
        conn.close()
    supplied = "".join(ch for ch in str(nid_last4) if ch.isdigit())
    if row is None or not row[1] or not hmac.compare_digest(row[1], supplied):
        return None
    return row[0]


# ------------------------------------------------------------------ knowledge

_index_cache: dict[tuple, TenantIndex] = {}


def _vectors(conn, table: str, key: str, tenant_id: str, model: str | None, ids: list[str]) -> np.ndarray | None:
    """Vectors in `ids` order, or None (keyword-only) unless every row has one for this model."""
    if model is None or not ids:
        return None
    found = {r[0]: r[1] for r in conn.execute(
        f"SELECT {key}, vector FROM {table} WHERE tenant_id = ? AND model = ?", (tenant_id, model))}
    if set(found) != set(ids):
        return None
    return np.stack([np.frombuffer(found[i], dtype="<f4") for i in ids])


def load_index(tenant_id: str, embed_model: str | None = None, db_path: Path | None = None) -> TenantIndex:
    """The tenant's knowledge as a TenantIndex built from the database (cached until the file changes)."""
    path = Path(db_path or settings.db_path)
    if path.exists():  # cache hit without opening a connection; a rebuild changes the mtime
        key = (str(path.resolve()), path.stat().st_mtime_ns, tenant_id, embed_model)
        if key in _index_cache:
            return _index_cache[key]
    conn = _open(tenant_id, path)
    try:
        key = (str(path.resolve()), path.stat().st_mtime_ns, tenant_id, embed_model)
        passages, passage_tokens = [], []
        for r in conn.execute("SELECT * FROM policy_passages WHERE tenant_id = ? ORDER BY ordinal", (tenant_id,)):
            passages.append({
                "passage_id": r["passage_id"], "tenant_id": r["tenant_id"], "document_id": r["document_id"],
                "version": r["version"], "current": bool(r["current"]), "effective_date": r["effective_date"],
                "section": r["section"], "language": r["language"], "text": r["text"], "citation": r["passage_id"],
            })
            passage_tokens.append(r["tokens"].split())
        tickets, ticket_tokens = [], []
        for r in conn.execute("SELECT * FROM past_tickets WHERE tenant_id = ? ORDER BY ordinal", (tenant_id,)):
            tickets.append({k: r[k] for k in ("ticket_id", "tenant_id", "category", "customer_message",
                                               "resolution", "created_at")})
            ticket_tokens.append(r["tokens"].split())
        meta = _row(conn.execute("SELECT * FROM tenants WHERE tenant_id = ?", (tenant_id,)).fetchone())
        index = TenantIndex(
            tenant_id=tenant_id,
            meta={**meta, "source": "sqlite", "embed_model": embed_model},
            passages=passages,
            passage_vectors=_vectors(conn, "passage_embeddings", "passage_id", tenant_id, embed_model,
                                     [p["passage_id"] for p in passages]),
            passage_bm25=BM25(passage_tokens),
            tickets=tickets,
            ticket_vectors=_vectors(conn, "ticket_embeddings", "ticket_id", tenant_id, embed_model,
                                    [t["ticket_id"] for t in tickets]),
            ticket_bm25=BM25(ticket_tokens),
            resolutions=[],  # precedents stay on the file index behind their own gated endpoints
            resolution_vectors=None,
            resolution_bm25=BM25([]),
            by_citation={p["citation"]: p for p in passages},
        )
    finally:
        conn.close()
    for stale in [k for k in _index_cache if k[:2] != key[:2]]:  # the database file was rebuilt
        del _index_cache[stale]
    _index_cache[key] = index
    return index


def search_knowledge(req: SearchKnowledgeRequest, embedder: Embedder | None,
                     db_path: Path | None = None, **thresholds) -> RetrievalResult:
    index = load_index(req.tenant_id, embedder.model if embedder else None, db_path)
    return retrieval.search_knowledge(req, index, embedder, **thresholds)


def get_passage(tenant_id: str, citation: str, db_path: Path | None = None) -> Passage | None:
    return retrieval.get_passage(load_index(tenant_id, None, db_path), citation)


def search_past_tickets(req: SearchPastTicketsRequest, embedder: Embedder | None,
                        db_path: Path | None = None) -> PastTicketResult:
    index = load_index(req.tenant_id, embedder.model if embedder else None, db_path)
    return retrieval.search_past_tickets(req, index, embedder)
