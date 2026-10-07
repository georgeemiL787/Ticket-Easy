"""Prepare connector setup from source facts and save explicit local-owner choices."""
import json
import re
from urllib.parse import urljoin

from ..config import AppError
from ..connectors import base_url, external_connectors, load_connectors
from ..persistence.util import digest, dump, now


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
                conn.execute("UPDATE sandbox_connectors SET content=? WHERE id=?", (dump(content), cid))

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

    def save(self, pid, url, context_fields, confirmed=False):
        view = self.proposals.view(pid)
        if not confirmed:
            raise AppError("sandbox_confirmation", "Confirm that this destination is a sandbox you want to use for tests")
        url = base_url(url)
        fields = sorted(set(x.strip() for x in context_fields if x.strip()))
        if len(fields) > 100 or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", x) for x in fields):
            raise AppError("connector_context", "Use field names separated by commas (letters, numbers, dots, underscores or hyphens)")
        cid = "sandbox-" + digest([view["business_id"], url, fields])[:20]
        if cid in external_connectors(self.settings):
            raise AppError("connector_conflict", "This connector ID is already managed by the external configuration", 409)
        content = dict(business_id=view["business_id"], base_url=url, sandbox=True, context_fields=fields,
                       identities={}, confirmed_by=self.settings.dev_reviewer_id, confirmed_at=now())
        with self.store.connect(write=True) as conn:
            # Repeating setup is idempotent and never erases existing credentials.
            conn.execute("INSERT OR IGNORE INTO sandbox_connectors(id,business_id,content,created_at) VALUES(?,?,?,?)",
                         (cid, view["business_id"], dump(content), now()))
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
                    auth=artifact["connector"].get("auth"))

    def save_identity(self, artifact, name, scope, credential, context):
        cid = artifact["connector"]["id"]
        if cid in external_connectors(self.settings):
            raise AppError("connector_external", "This connector's identities are managed in its external configuration", 409)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
            raise AppError("identity_name", "Use a test identity name with letters, numbers, dots, underscores or hyphens")
        if scope not in {"end_user", "admin", "service", "public", "guest"}:
            raise AppError("identity_scope", "Choose the identity's actual account type")
        auth = artifact["connector"].get("auth")
        keys = ("token",) if auth and auth["type"] == "http_bearer" else ("username", "password") if auth else ()
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
            conn.execute("UPDATE sandbox_connectors SET content=? WHERE id=?", (dump(saved), cid))
