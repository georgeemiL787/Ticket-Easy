"""Copy a telecom tenant's backend data (its telecom.db) into the unified database.

The source is data/telecom/<tenant>/telecom.db, a generated SQLite file (scripts/generate.py there rebuilds it
identically). Rows are copied with INSERT ... SELECT over an attached connection, with tenant_id added. Only
the columns our schema declares are selected, so the fields we leave out on purpose (full national ID, birth
date, gender, home address, SIM secrets) are never read.
"""

import sqlite3
from pathlib import Path

from team_a.config import settings

# (destination, source table), parents before children so every foreign key resolves as rows arrive.
TABLES = (
    ("tel_plans", "plans"), ("tel_bundles", "bundles"), ("tel_vas_services", "vas_services"),
    ("tel_devices", "devices"), ("tel_stores", "stores"),
    ("customers", "customers"), ("tel_customer_profiles", "customers"),
    ("tel_accounts", "accounts"), ("tel_lines", "lines"), ("tel_line_bundles", "line_bundles"),
    ("tel_vas_subscriptions", "vas_subscriptions"), ("tel_transactions", "transactions"),
    ("tel_invoices", "invoices"), ("tel_invoice_items", "invoice_items"), ("tel_usage_daily", "usage_daily"),
    ("tel_device_installments", "device_installments"), ("tel_sim_events", "sim_events"),
    ("tel_port_requests", "port_requests"), ("tel_network_outages", "network_outages"),
    ("tel_technician_appointments", "technician_appointments"), ("tel_complaints", "complaints"),
    ("tel_refunds", "refunds"),
)
# Destination columns whose source has a different name or needs an expression.
_EXPRESSIONS = {
    "customers": {
        "name": "first_name_en || ' ' || last_name_en", "name_ar": "full_name_ar", "phone": "contact_msisdn",
        "verified": "CASE kyc_status WHEN 'verified' THEN 1 ELSE 0 END", "verified_via": "'kyc_' || id_type",
        "wallet_balance": "NULL", "member_since": "customer_since",
    },
    "tel_technician_appointments": {"time_window": '"window"'},
}
SOURCE_ALIAS = "telecom_src"


class TelecomSourceMissing(FileNotFoundError):
    pass


def source_path(tenant_id: str, override: Path | None = None) -> Path:
    path = Path(override or settings.tenant_dir(tenant_id) / "telecom.db")
    if not path.exists():
        raise TelecomSourceMissing(
            f"{path} not found. Generate it (about 15 s): python data/telecom/{tenant_id}/scripts/generate.py")
    return path


def source_meta(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        as_of, currency = conn.execute("SELECT as_of, currency FROM tenant").fetchone()
        customers = conn.execute("SELECT count(*) FROM customers").fetchone()[0]
    finally:
        conn.close()
    return {"as_of": as_of, "currency": currency or "EGP", "customers": customers}


def attach(conn: sqlite3.Connection, path: Path) -> None:
    """Attach the source. Must run outside a transaction (SQLite does not allow ATTACH inside one).
    The loader only ever SELECTs from it."""
    conn.execute(f"ATTACH DATABASE ? AS {SOURCE_ALIAS}", (str(path.resolve()),))


def detach(conn: sqlite3.Connection) -> None:
    conn.execute(f"DETACH DATABASE {SOURCE_ALIAS}")


def copy_rows(conn: sqlite3.Connection, tenant_id: str) -> dict[str, int]:
    """Insert the tenant's rows from the attached source. Runs inside the caller's transaction."""
    counts = {}
    for dest, src in TABLES:
        columns = [r[1] for r in conn.execute(f"PRAGMA table_info({dest})") if r[1] != "tenant_id"]
        exprs = _EXPRESSIONS.get(dest, {})
        select = ", ".join(exprs.get(c, f'"{c}"') for c in columns)
        cur = conn.execute(
            f"INSERT INTO {dest} (tenant_id, {', '.join(columns)}) "
            f"SELECT ?, {select} FROM {SOURCE_ALIAS}.{src} ORDER BY rowid", (tenant_id,))
        counts[dest] = cur.rowcount
    return counts
