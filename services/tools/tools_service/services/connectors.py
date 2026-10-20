"""Prepare connector setup from source facts, save explicit local-owner choices, and check a connector for real.

A connector is either a sandbox (test destination, test identities) or a Live Demo connector (the owner-configured real
API, its own credentials). Credentials stay in the local database and never leave this module except to the API itself.
"""
import json
import re
from urllib.parse import urljoin

import httpx

from ..config import AppError
from ..connectors import (LIVE_DEMO, PRODUCTION, SANDBOX, base_url, environment_of, external_connectors, host_port, listed,
                          live_destination_problem, load_connectors, production_destination_problem, resolve_secret, secret_name)
from ..fixtures import FixtureClient, fixtures_problem, public_view
from ..persistence.util import digest, dump, now
from ..secretbox import has_plaintext, seal


def server_candidates(document, source_url="", operations=()):
    """Resolve OpenAPI server defaults and relative URLs without fetching anything."""
    groups = []
    for operation in operations:
        path = document.get("paths", {}).get(operation["path"], {})
        endpoint = path.get(operation["method"].lower(), {})
        groups.append(endpoint.get("servers") or path.get("servers") or document.get("servers") or [{"url": "/"}])
    if not groups:
        groups = [document.get("servers") or [{"url": "/"}]]
    candidates = []
    for servers in groups:
        for server in servers:
            value = server.get("url", "")
            for name, variable in server.get("variables", {}).items():
                if "default" in variable:
                    value = value.replace("{" + name + "}", str(variable["default"]))
            try:
                value = base_url(urljoin(source_url, value) if source_url else value)
            except AppError:
                continue
            if value not in candidates:
                candidates.append(value)
    return candidates


class Connectors:
    def __init__(self, settings, store, proposals, discovery):
        self.settings, self.store = settings, store
        self.proposals, self.discovery = proposals, discovery

    def seal_existing(self):
        """Encrypt any credential still stored as plaintext (rows written before encryption existed). Runs at start-up."""
        with self.store.connect(write=True) as conn:
            for cid, raw in conn.execute("SELECT id,content FROM sandbox_connectors").fetchall():
                content = json.loads(raw)
                if has_plaintext(content):
                    conn.execute("UPDATE sandbox_connectors SET content=? WHERE id=?", (dump(seal(content, self.settings)), cid))

    def apply_policy_context(self, cid, business_id, policy):
        """Register accepted field names on managed connectors, never fabricate values."""
        if cid in external_connectors(self.settings):
            return
        fields = {i["reference"] for i in policy.get("inputs", []) if i["source"] == "trusted_application_context"}
        fields.update(c["identity_source"] for c in policy.get("checks", []))
        fields.update(policy.get(k) for k in ("role_source", "permission_source"))
        fields -= {None, "", "business_id"}
        with self.store.connect(write=True) as conn:
            row = conn.execute("SELECT content FROM sandbox_connectors WHERE id=? AND business_id=?", (cid, business_id)).fetchone()
            if row:
                content = json.loads(row[0])
                content["context_fields"] = sorted(set(content.get("context_fields", [])) | fields)
                conn.execute("UPDATE sandbox_connectors SET content=? WHERE id=?", (dump(seal(content, self.settings)), cid))

    def draft(self, pid):
        view = self.proposals.view(pid)
        spec = self.discovery.spec(view["spec_id"])
        row = self.store.one("SELECT document FROM specifications WHERE id=?", (view["spec_id"],))
        document = json.loads(row["document"] or "{}")
        used = {s["operation_id"] for s in view["content"]["steps"]}
        operations = [op for op in spec["inventory"]["operations"] if op["id"] in used]
        urls = server_candidates(document, spec["inventory"].get("source", {}).get("url", ""), operations)
        fields = {b["reference"] for s in view["content"]["steps"] for b in s["bindings"]
                  if b["kind"] == "trusted_application_context"}
        policy = (view.get("policy") or {}).get("content", {})
        fields.update(i["reference"] for i in policy.get("inputs", []) if i["source"] == "trusted_application_context")
        fields.update(c["identity_source"] for c in policy.get("checks", []))
        fields.update(policy.get(k) for k in ("role_source", "permission_source"))
        fields.discard(None)
        fields.discard("")
        fields.discard("business_id")
        return dict(base_url=urls[0] if len(urls) == 1 else "", candidates=urls,
                    context_fields=sorted(fields), business_id=view["business_id"])

    def set_fixtures(self, cid, business_id, fixtures):
        """Declare (or with None remove) the test-fixture endpoints of a sandbox connector. The key is only a variable NAME."""
        if cid in external_connectors(self.settings):
            raise AppError("connector_external", "This connector is managed in its external configuration; add `fixtures` there", 409)
        with self.store.connect(write=True) as conn:
            row = conn.execute("SELECT content FROM sandbox_connectors WHERE id=? AND business_id=?", (cid, business_id)).fetchone()
            if row is None:
                raise AppError("connector_unknown", "No connector with this id serves this business", 404)
            content = json.loads(row[0])
            if fixtures is None:
                content.pop("fixtures", None)
            else:
                problem = fixtures_problem(self.settings, content, fixtures)
                if problem:
                    raise problem
                content["fixtures"] = dict(base_url=base_url(fixtures["base_url"]), key_ref=fixtures["key_ref"])
            conn.execute("UPDATE sandbox_connectors SET content=? WHERE id=?", (dump(seal(content, self.settings)), cid))

    def save(self, pid, url, context_fields, confirmed=False, environment=SANDBOX, fixtures=None, secret_ref=None):
        """Save the owner's destination for this business. A sandbox needs the sandbox confirmation; a Live Demo
        connector needs the owner's confirmation that it is the real API they may call, Live Demo switched on and the
        host in the operator's TOOLS_LIVE_DEMO_HOSTS (checked again before every call). Each environment keeps its own
        connector and credentials. Only a sandbox may declare test fixtures."""
        view = self.proposals.view(pid)
        if fixtures and environment != SANDBOX:
            raise AppError("fixtures_sandbox_only", "Only a sandbox connector may declare test fixtures; Live Demo and production connectors never create accounts or records", 403)
        if environment not in (SANDBOX, LIVE_DEMO, PRODUCTION):
            raise AppError("connector_environment", "Choose sandbox, live_demo or production")
        if environment == PRODUCTION and not secret_name(secret_ref):
            raise AppError("production_secret_ref", 'A production connector names its service credential by reference, "env:VARIABLE" or "file:/path/to/secret"; the credential itself is never entered here')
        if secret_ref and environment != PRODUCTION:
            raise AppError("secret_ref_production_only", "Only a production connector uses a service credential reference", 422)
        if not confirmed:
            if environment == PRODUCTION:
                raise AppError("production_confirmation", "Confirm that this is the real production API, that its service credential is the business's own, and that you may act on its customers' data")
            if environment == LIVE_DEMO:
                raise AppError("live_confirmation", "Confirm that this is the real API you are authorized to call for this demo")
            raise AppError("sandbox_confirmation", "Confirm that this destination is a sandbox you want to use for tests")
        url = base_url(url)
        if environment == LIVE_DEMO:
            problem = live_destination_problem(self.settings, url)
            if problem:
                raise problem
        if environment == PRODUCTION:
            problem = production_destination_problem(self.settings, url)
            if problem:
                raise problem
        fields = sorted(set(x.strip() for x in context_fields if x.strip()))
        if len(fields) > 100 or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", x) for x in fields):
            raise AppError("connector_context", "Use field names separated by commas (letters, numbers, dots, underscores or hyphens)")
        prefix = {LIVE_DEMO: "live-", PRODUCTION: "prod-"}.get(environment, "sandbox-")
        cid = prefix + digest([view["business_id"], url, fields] + ([environment] if environment != SANDBOX else []))[:20]
        if cid in external_connectors(self.settings):
            raise AppError("connector_conflict", "This connector ID is already managed by the external configuration", 409)
        content = dict(business_id=view["business_id"], base_url=url, sandbox=environment == SANDBOX, context_fields=fields,
                       identities={}, confirmed_by=self.settings.dev_reviewer_id, confirmed_at=now())
        if environment in (LIVE_DEMO, PRODUCTION):
            content["environment"] = environment
        if environment == PRODUCTION:
            content["secret_ref"] = secret_ref
        with self.store.connect(write=True) as conn:
            # Repeating setup is idempotent and never erases existing credentials.
            conn.execute("INSERT OR IGNORE INTO sandbox_connectors(id,business_id,content,created_at) VALUES(?,?,?,?)",
                         (cid, view["business_id"], dump(seal(content, self.settings)), now()))
        if environment == PRODUCTION:  # repeating setup may point the connector at another variable, but never erases anything else
            with self.store.connect(write=True) as conn:
                row = conn.execute("SELECT content FROM sandbox_connectors WHERE id=?", (cid,)).fetchone()
                saved = json.loads(row[0])
                if saved.get("secret_ref") != secret_ref:
                    saved["secret_ref"] = secret_ref
                    conn.execute("UPDATE sandbox_connectors SET content=? WHERE id=?", (dump(seal(saved, self.settings)), cid))
        if fixtures:
            self.set_fixtures(cid, view["business_id"], fixtures)
        return cid

    def identity_setup(self, artifact):
        cid = artifact["connector"]["id"]
        connector = load_connectors(self.settings).get(cid) or {}
        schemas = dict(artifact.get("compiler", {}).get("context_schemas", {}))
        policy = artifact.get("access_policy") or {}
        for key in ("role_source", "permission_source"):
            if policy.get(key):
                schemas.setdefault(policy[key], {"type": "array", "items": {"type": "string"}})
        return dict(managed=cid not in external_connectors(self.settings) and bool(connector.get("confirmed_by")),
                    context_fields=connector.get("context_fields", []),
                    context_schemas=schemas,
                    auth=artifact["connector"].get("auth"),
                    guide=self.identity_guide(artifact, connector),
                    saved={n: dict(scope=i.get("scope"), username=i.get("username", ""), context=i.get("context") or {}) for n, i in (connector.get("identities") or {}).items()})

    def identity_guide(self, artifact, connector):
        """What the next test identity should be and where its values come from, read from the policy and the API.

        Computed from facts, never from a model: credentials and context values are entered by the owner,
        stay in the local database and never reach a model. Nothing here is saved until the owner submits.
        """
        policy = artifact.get("access_policy") or {}
        principals = policy.get("principals") or []
        scope = next((s for p, s in (("resource_owner", "end_user"), ("end_user", "end_user"), ("admin", "admin"), ("service", "service"),
                                      ("guest", "guest"), ("public", "public")) if p in principals), "")
        existing = connector.get("identities") or {}
        same = [n for n, i in existing.items() if i.get("scope") == scope]
        ownership = any(c.get("kind") == "ownership" for c in policy.get("checks", []))
        if not same:
            name, why = ("owner-customer" if scope == "end_user" else f"test-{scope or 'account'}"), "the account whose own records the tool should reach"
        elif ownership and len(same) == 1:
            name, why = "other-customer", "a second account, so a test can prove one customer cannot reach another customer's record"
        else:
            name, why = f"test-{scope or 'account'}-{len(same) + 1}", "another test account"
        try:
            ops = self.discovery.spec(artifact["source"]["spec_id"])["inventory"]["operations"]
        except (AppError, KeyError, TypeError):
            ops = []
        signup = [o["method"] + " " + o["path"] for o in ops if o["method"] == "POST" and re.search(r"regist|sign-?up", o["path"], re.I)]
        fields = {}
        for field in connector.get("context_fields", []):
            for o in ops:
                if o["method"] != "GET" or "{" in o["path"] or not re.search(r"/(me|profile|account|current|whoami)/?$", o["path"], re.I):
                    continue
                success = next((r for code, r in sorted(o.get("responses", {}).items()) if code.startswith("2") and r.get("schema")), None)
                props = ((success or {}).get("schema") or {}).get("properties") or {}
                prefix = field[:-3] if field.endswith("_id") else None
                if field in props:
                    fields[field] = f'After signing in as this account, GET {o["path"]} returns {field}.'
                elif prefix and "id" in props and prefix in o["path"].lower():
                    fields[field] = f'After signing in as this account, GET {o["path"]} returns it as id.'
                if field in fields:
                    break
        return dict(name=name, scope=scope, why=why, existing=sorted(existing), signup=signup[:2], fields=fields)

    def save_identity(self, artifact, name, scope, credential, context):
        cid = artifact["connector"]["id"]
        if cid in external_connectors(self.settings):
            raise AppError("connector_external", "This connector's identities are managed in its external configuration", 409)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
            raise AppError("identity_name", "Use a test identity name with letters, numbers, dots, underscores or hyphens")
        if scope not in {"end_user", "admin", "service", "public", "guest"}:
            raise AppError("identity_scope", "Choose the identity's actual account type")
        auth = artifact["connector"].get("auth")
        keys = ("token",) if auth and auth["type"] in ("http_bearer", "api_key") else ("username", "password") if auth else ()
        current = ((load_connectors(self.settings).get(cid) or {}).get("identities") or {}).get(name) or {}
        # Editing an existing identity: a field left blank keeps its saved value, so a password is never retyped to change a name or scope.
        credential = {k: credential.get(k) or current.get(k, "") for k in keys}
        context = dict(current.get("context") or {}, **context)
        if any(not credential.get(k) for k in keys):
            raise AppError("credential_missing", "Enter the credentials required by this API")
        with self.store.connect(write=True) as conn:
            row = conn.execute("SELECT content FROM sandbox_connectors WHERE id=?", (cid,)).fetchone()
            if row is None:
                raise AppError("connector_unknown", "The sandbox connector no longer exists", 404)
            saved = json.loads(row[0])
            if saved["business_id"] != artifact["proposal"]["business_id"] or saved["base_url"] != artifact["connector"]["base_url"]:
                raise AppError("connector_changed", "The connector has changed; rebuild the artifact", 409)
            if set(context) - set(saved["context_fields"]):
                raise AppError("connector_context", "The identity contains undeclared context fields")
            saved["identities"][name] = dict(scope=scope, context=context, **{k: credential[k] for k in keys})
            conn.execute("UPDATE sandbox_connectors SET content=? WHERE id=?", (dump(seal(saved, self.settings)), cid))

    def remove_identity(self, cid, business_id, name):
        """Delete one saved test identity of a managed connector (its credential leaves the database with it)."""
        if cid in external_connectors(self.settings):
            raise AppError("connector_external", "This connector's identities are managed in its external configuration", 409)
        with self.store.connect(write=True) as conn:
            row = conn.execute("SELECT content FROM sandbox_connectors WHERE id=? AND business_id=?", (cid, business_id)).fetchone()
            if row is None:
                raise AppError("connector_unknown", "No connector with this id serves this business", 404)
            saved = json.loads(row[0])
            if saved.get("identities", {}).pop(name, None) is not None:
                conn.execute("UPDATE sandbox_connectors SET content=? WHERE id=?", (dump(seal(saved, self.settings)), cid))

    # ---- listing and checking connectors (never returns a credential) ----

    def listing(self, business_id):
        """This business's connectors with their environment and last check; identity names and account types only."""
        external = external_connectors(self.settings)
        out = []
        for cid, c in sorted(load_connectors(self.settings).items()):
            if c.get("business_id") != business_id:
                continue
            out.append(dict(id=cid, environment=environment_of(c), base_url=c.get("base_url", ""), host=host_port(c.get("base_url", "")),
                            context_fields=c.get("context_fields") or [], external=cid in external,
                            fixtures="configured" if c.get("fixtures") else "none", fixtures_endpoint=public_view(c.get("fixtures")),
                            secret_ref=c.get("secret_ref"), secret_set=resolve_secret(c.get("secret_ref")) is not None if environment_of(c) == PRODUCTION else None,
                            identities=[dict(name=n, scope=i.get("scope")) for n, i in sorted((c.get("identities") or {}).items())],
                            confirmed_by=c.get("confirmed_by"), confirmed_at=c.get("confirmed_at"), last_check=c.get("last_check")))
        return out

    def check(self, cid, artifacts=(), publications=(), transport=None):
        """A real connection check, step by step, each with its own result (ok / failed / unknown / skipped):

        configuration  the connector is complete and allowed in its environment (Live Demo: switched on, host listed,
                       public addresses) - no request is sent if this fails;
        reachable      one GET to the base URL, no credentials, no redirects: any HTTP answer proves the API answers;
        authentication OAuth2 password APIs: each identity's credentials obtain a token (the API's own token endpoint);
                       bearer tokens cannot be verified without calling an operation, so they stay unknown until a test;
        permissions    only what passing tests of this connector's artifacts proved, per identity;
        executable     published tools of this connector that pass every re-check now.
        A passing check never claims that every operation will succeed. The result is kept on the connector."""
        connector = load_connectors(self.settings).get(cid)
        if not connector:
            raise AppError("connector_unknown", "No configured connector with this id", 404)
        environment = environment_of(connector)
        checks = []

        def add(name, status, detail):
            checks.append(dict(check=name, status=status, detail=detail))

        problem = None
        if environment is None:
            problem = AppError("not_sandbox", "The connector is neither a sandbox, a Live Demo nor a production connector")
        elif environment == PRODUCTION:
            problem = production_destination_problem(self.settings, connector["base_url"])
        elif environment == LIVE_DEMO:
            problem = live_destination_problem(self.settings, connector["base_url"])
        elif host_port(connector["base_url"]) not in listed(self.settings.sandbox_hosts) and not connector.get("confirmed_by"):
            problem = AppError("destination_not_allowed", "The sandbox host is neither in TOOLS_SANDBOX_HOSTS nor confirmed by the owner")
        add("configuration", "failed" if problem else "ok", f"{problem.code}: {problem.message}" if problem else
            f"{environment} connector to {host_port(connector['base_url'])}")
        reachable = False
        if problem:
            add("reachable", "skipped", "No request was sent because the configuration check failed")
        else:
            try:
                with httpx.Client(transport=transport, follow_redirects=False, trust_env=False, timeout=self.settings.connection_check_timeout) as client:
                    answer = client.get(connector["base_url"] + "/", headers={"Accept": "application/json"})
                reachable = True
                add("reachable", "ok", f"The API answered HTTP {answer.status_code} at its base URL")
            except httpx.TimeoutException:
                add("reachable", "failed", f"No answer within {self.settings.connection_check_timeout:g} s")
            except httpx.HTTPError as exc:
                add("reachable", "failed", f"Could not connect ({type(exc).__name__})")
        auth = next((a["content"]["connector"].get("auth") for a in artifacts if a["content"]["connector"]["id"] == cid), "unknown")
        identities = connector.get("identities") or {}
        if environment == PRODUCTION:
            # one service credential, read from the named environment variable: shown as present or absent, never its value
            from ..executor import service_identity
            found = service_identity(connector, {}, None if auth in ("unknown", None) else auth)
            identities = {"service": found} if found else {}
            add("secret", "ok" if resolve_secret(connector.get("secret_ref")) is not None else "failed",
                f"{connector.get('secret_ref')} is " + ("available" if resolve_secret(connector.get("secret_ref")) is not None else "NOT available to Team C"))
        if not reachable:
            add("authentication", "skipped", "The API was not reached")
        elif auth == "unknown":
            add("authentication", "unknown", "Build a tool on this connector first: the login method comes from the API's specification")
        elif auth is None:
            add("authentication", "ok", "The specification declares no authentication for these operations")
        elif not identities:
            add("authentication", "failed", "The service credential is not set" if environment == PRODUCTION else "No identity (credential) is configured for this connector")
        elif auth["type"] == "oauth2_password":
            results = {}
            with httpx.Client(transport=transport, follow_redirects=False, trust_env=False, timeout=self.settings.connection_check_timeout) as client:
                for name, identity in sorted(identities.items()):
                    form = dict(grant_type="password", username=identity.get("username", ""), password=identity.get("password", ""))
                    try:
                        r = client.post(connector["base_url"] + auth["token_path"], data=form)
                        body = r.json() if r.status_code == 200 else {}
                        results[name] = "ok" if isinstance(body, dict) and isinstance(body.get("access_token"), str) else f"refused (HTTP {r.status_code})"
                    except (httpx.HTTPError, ValueError) as exc:
                        results[name] = f"failed ({type(exc).__name__})"
            bad = {n: r for n, r in results.items() if r != "ok"}
            add("authentication", "failed" if bad else "ok", "; ".join(f"{n}: {r}" for n, r in results.items()))
        else:
            add("authentication", "unknown", f"{len(identities)} bearer credential(s) configured; a bearer token is only proven by calling an "
                "operation: run a read test")
        proven = sorted({e["identity"] for a in artifacts if a["content"]["connector"]["id"] == cid
                         for t in a["tests"] if t["verdict"] == "passed" and t["expectation"]["status"] == "succeeded"
                         for e in a["executions"] if e["id"] == t["execution_id"]})
        add("permissions", "ok" if proven else "unknown", f"Passing tests proved access for: {', '.join(proven)}" if proven else
            "No passing test on this connector yet: run a read test to prove what each identity may do")
        if not connector.get("fixtures"):
            add("fixtures", "unknown", "No test fixtures declared: automatic tests need accounts and records added by hand (optional)")
        elif not reachable:
            add("fixtures", "skipped", "The API was not reached")
        else:
            try:
                kinds = FixtureClient(connector, self.settings, transport).kinds()
                add("fixtures", "ok", "Record kinds: " + ", ".join(f"{k['kind']} ({', '.join(map(str, k['states']))})" for k in kinds))
            except AppError as exc:
                add("fixtures", "failed", f"{exc.code}: {exc.message}")
        live = [p["tool_name"] for p in publications if p.get("execution_enabled")]
        add("executable", "ok" if live else "unknown", f"Published and executable now: {', '.join(live)}" if live else "No executable publication on this connector")
        result = dict(connector_id=cid, environment=environment, host=host_port(connector["base_url"]), checked_at=now(), checks=checks,
                      ready=all(c["status"] == "ok" for c in checks))
        if cid not in external_connectors(self.settings):
            with self.store.connect(write=True) as conn:
                row = conn.execute("SELECT content FROM sandbox_connectors WHERE id=?", (cid,)).fetchone()
                if row:
                    saved = json.loads(row[0])
                    saved["last_check"] = result
                    conn.execute("UPDATE sandbox_connectors SET content=? WHERE id=?", (dump(seal(saved, self.settings)), cid))
        return result
