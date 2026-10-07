"""Connector loading shared by the web app, compiler and MCP executor.

UI-managed destinations are explicitly confirmed and scoped to one connector.
External connector files retain their existing SANDBOX_HOSTS contract.
"""
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

from .config import AppError


def managed_connectors(settings):
    path = Path(getattr(settings, "database_path", ""))
    if not path.is_file():
        return {}
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sandbox_connectors'").fetchone():
            return {}
        return {cid: json.loads(content) for cid, content in conn.execute("SELECT id,content FROM sandbox_connectors")}
    finally:
        conn.close()


def external_connectors(settings):
    path = Path(settings.connectors_file) if settings.connectors_file else None
    if not path or not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("connectors", {})


def load_connectors(settings):
    # An explicitly configured file takes precedence; UI code never edits it.
    return {**managed_connectors(settings), **external_connectors(settings)}


def base_url(value):
    value = value.strip().rstrip("/")
    try:
        parsed = urlparse(value)
        valid = (parsed.scheme in ("http", "https") and parsed.hostname and
                 parsed.username is None and parsed.password is None and not parsed.query and
                 not parsed.fragment and (parsed.port is None or 0 < parsed.port <= 65535) and
                 not any(c.isspace() or ord(c) < 32 for c in value) and
                 not any(c in value for c in "{}\\"))
    except ValueError:
        valid = False
    if not valid:
        raise AppError("connector_invalid", "Enter an HTTP or HTTPS API base URL without credentials, variables, query or fragment")
    return value


def confirmed_destination(settings, connector, artifact):
    cid = artifact["connector"]["id"]
    if cid in external_connectors(settings):
        return False
    saved = managed_connectors(settings).get(cid) or {}
    return bool(saved.get("sandbox") and saved.get("confirmed_by") and
                saved.get("business_id") == artifact["proposal"]["business_id"] and
                saved.get("base_url") == connector.get("base_url"))
