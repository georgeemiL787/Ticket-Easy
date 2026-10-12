"""Connector loading shared by the web app, compiler and MCP executor.

UI-managed destinations are explicitly confirmed and scoped to one connector.
External connector files retain their existing SANDBOX_HOSTS contract.

Every connector has one execution environment:
- sandbox: the existing test destinations (`"sandbox": true`, SANDBOX_HOSTS or an owner-confirmed sandbox URL).
- live_demo: an owner-configured REAL API (`"environment": "live_demo"`). It runs only while LIVE_DEMO_ENABLED is on,
  only to a host listed in LIVE_DEMO_HOSTS, and only when every address the host resolves to is public, unless the
  host is also listed in LIVE_DEMO_PRIVATE_HOSTS (a narrow exception for a development network). Live connectors keep
  their own credentials; a sandbox identity is never used against a live API, and the reverse.
Records saved before environments existed have no `environment` key and keep meaning sandbox.
"""
import ipaddress
import json
import socket
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
    return bool(environment_of(saved) == environment_of(connector) and environment_of(saved) is not None and saved.get("confirmed_by") and
                saved.get("business_id") == artifact["proposal"]["business_id"] and
                saved.get("base_url") == connector.get("base_url"))


SANDBOX, LIVE_DEMO = "sandbox", "live_demo"
ENVIRONMENTS = (SANDBOX, LIVE_DEMO)


def environment_of(connector):
    """sandbox, live_demo, or None when the connector declares neither (such a connector never executes)."""
    declared = (connector or {}).get("environment")
    if declared in ENVIRONMENTS:
        return declared
    return SANDBOX if (connector or {}).get("sandbox") and declared is None else None


def host_port(url):
    parsed = urlparse(url)
    return f"{(parsed.hostname or '').lower()}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"


def listed(value):
    return {h.strip().lower() for h in (value or "").split(",") if h.strip()}


def resolve_addresses(host):
    """Every address `host` resolves to (an IP literal resolves to itself)."""
    try:
        return {ipaddress.ip_address(host.strip("[]"))}
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return {ipaddress.ip_address(info[4][0].split("%", 1)[0]) for info in infos}


def live_destination_problem(settings, url):
    """Why a live connector may not call `url` now, or None. Checked when configuring and again before every run."""
    if not settings.live_demo_enabled:
        return AppError("live_demo_disabled", "Live Demo execution is switched off (LIVE_DEMO_ENABLED); no request was sent", 403)
    target = host_port(url)
    if target not in listed(settings.live_demo_hosts):
        return AppError("destination_not_allowed", f"{target} is not listed in LIVE_DEMO_HOSTS", 403)
    if target in listed(settings.live_demo_private_hosts):
        return None  # the operator's explicit exception for a development network
    host = urlparse(url).hostname or ""
    try:
        addresses = resolve_addresses(host)
    except (OSError, UnicodeError):
        return AppError("destination_unresolved", f"{host} could not be resolved; no request was sent", 502)
    unsafe = sorted(str(a) for a in addresses if not a.is_global or a.is_multicast)
    if not addresses or unsafe:
        return AppError("destination_unsafe", f"{host} resolves to a non-public address ({', '.join(unsafe) or 'none'}); "
                        "a live connector must reach a public API (or the host must be listed in LIVE_DEMO_PRIVATE_HOSTS)", 403)
    return None
