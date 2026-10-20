"""Test fixtures for automatic sandbox tests: disposable accounts and records created by the business's own sandbox.

A sandbox connector may declare `fixtures: {base_url, key_ref}`: a few endpoints the sandbox exposes for test setup only
(see docs/plan/TOOLS_AUTOTEST_AND_PRODUCTION_PLAN.md, section 1.1). They are never part of the OpenAPI document tools are
generated from. Only sandbox connectors may declare fixtures; a Live Demo (or any later production) connector never gets
account creation. The key is read from the environment variable `key_ref` names and is never stored, logged or returned.
Same safety as the executor: an allowed sandbox destination, no redirects, a timeout and a response size limit.
"""
import os
import re

import httpx

from .config import AppError
from .connectors import SANDBOX, base_url, environment_of, host_port, listed

MAX_RESPONSE_BYTES = 256 * 1024
KEY_REF = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def fixtures_problem(settings, connector, fixtures):
    """Why this connector may not use this fixtures declaration, or None."""
    if environment_of(connector) != SANDBOX:
        return AppError("fixtures_sandbox_only", "Only a sandbox connector may declare test fixtures; Live Demo and production connectors never create accounts or records", 403)
    if not isinstance(fixtures, dict) or not fixtures.get("base_url") or not KEY_REF.fullmatch(str(fixtures.get("key_ref", ""))):
        return AppError("fixtures_invalid", "Fixtures need a base_url and key_ref, the NAME of the environment variable that holds the fixture key", 422)
    try:
        url = base_url(fixtures["base_url"])
    except AppError as exc:
        return exc
    target = host_port(url)
    if target != host_port(connector.get("base_url", "")) and target not in listed(settings.sandbox_hosts):
        return AppError("fixtures_destination", f"{target} is neither the connector's own host nor listed in SANDBOX_HOSTS", 403)
    return None


def public_view(fixtures):
    """What may be shown about fixtures: whether they are configured and where, never the key."""
    return dict(base_url=fixtures["base_url"], key_ref=fixtures["key_ref"]) if fixtures else None


class FixtureClient:
    def __init__(self, connector, settings, transport=None, environ=None):
        self.connector, self.settings, self.transport = connector, settings, transport
        self.environ = os.environ if environ is None else environ
        self.fixtures = (connector or {}).get("fixtures")
        if not self.fixtures:
            raise AppError("fixtures_not_configured", "This connector has no test fixtures configured", 409)
        problem = fixtures_problem(settings, connector, self.fixtures)
        if problem:
            raise problem
        self.base = base_url(self.fixtures["base_url"])

    def key(self):
        value = self.environ.get(self.fixtures["key_ref"], "")
        if not value:
            raise AppError("fixtures_key_missing", f"The environment variable {self.fixtures['key_ref']} is not set in Team C's environment, so no fixture request was sent", 409)
        return value

    def call(self, method, path, body=None, query=None):
        headers = {"x-fixture-key": self.key(), "Accept": "application/json"}
        try:
            with httpx.Client(transport=self.transport, follow_redirects=False, trust_env=False, timeout=self.settings.connection_check_timeout) as client:
                with client.stream(method, self.base + path, json=body, params=query, headers=headers) as response:
                    raw = b""
                    for chunk in response.iter_bytes():
                        raw += chunk
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise AppError("fixtures_response_too_large", "The fixture endpoint answered with too much data", 502)
                    status = response.status_code
        except httpx.TimeoutException:
            raise AppError("fixtures_unreachable", f"No answer from the fixture endpoint within {self.settings.connection_check_timeout:g} s", 502) from None
        except httpx.HTTPError as exc:
            raise AppError("fixtures_unreachable", f"Could not reach the fixture endpoint ({type(exc).__name__})", 502) from None
        if 300 <= status < 400:
            raise AppError("fixtures_redirect", "The fixture endpoint redirected; redirects are never followed", 502)
        try:
            data = httpx.Response(status, content=raw).json() if raw else None
        except ValueError:
            data = None
        if status >= 400:
            detail = data.get("detail") if isinstance(data, dict) else None
            raise AppError("fixtures_refused", f"The fixture endpoint refused {method} {path} (HTTP {status}){': ' + detail if isinstance(detail, str) else ''}",
                           422 if status < 500 else 502, dict(http_status=status))
        return data

    def kinds(self):
        data = self.call("GET", "/_fixtures/kinds")
        if not isinstance(data, list) or not all(isinstance(k, dict) and k.get("kind") and isinstance(k.get("states"), list) for k in data):
            raise AppError("fixtures_invalid_answer", "GET /_fixtures/kinds did not answer a list of {kind, states, id_field, selects_by}", 502)
        return data

    def accounts(self, label, run_id):
        data = self.call("POST", "/_fixtures/accounts", dict(label=label, run_id=run_id))
        credential = data.get("credential") if isinstance(data, dict) else None
        if not isinstance(credential, dict) or not data.get("account_id"):
            raise AppError("fixtures_invalid_answer", "POST /_fixtures/accounts did not answer an account with a credential", 502)
        return data

    def record(self, account_id, kind, state=None, attributes=None, run_id=None):
        body = dict(account_id=account_id, kind=kind, run_id=run_id)
        if state:
            body["state"] = state
        if attributes:
            body["attributes"] = attributes
        data = self.call("POST", "/_fixtures/records", body)
        if not isinstance(data, dict) or data.get("record_id") in (None, ""):
            raise AppError("fixtures_invalid_answer", "POST /_fixtures/records did not answer a record_id", 502)
        return data

    def revoke(self, account_id):
        return self.call("POST", "/_fixtures/revoke", dict(account_id=account_id))

    def reset(self, run_id, keep_accounts=False, record_ids=None):
        """Delete what one run created (all of it, or only `record_ids`); accounts are kept when asked."""
        body = dict(run_id=run_id, keep_accounts=keep_accounts)
        if record_ids is not None:
            body["record_ids"] = list(record_ids)
        return self.call("POST", "/_fixtures/reset", body)

    def state(self, run_id):
        return self.call("GET", "/_fixtures/state", query=dict(run_id=run_id))
