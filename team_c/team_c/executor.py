"""Generic sandbox executor for compiled tool artifacts.

Destinations come only from operator connector configuration, never from arguments or models.
Credentials come from the trusted execution context and never enter reports. Execution stops at
the first failure, and the report says truthfully whether each write was applied, rejected or unknown.
"""
import json
import time
from urllib.parse import quote, unquote, urlencode, urlparse
import httpx
from jsonschema.exceptions import ValidationError
from openapi_schema_validator import OAS30Validator, OAS30WriteValidator, oas30_format_checker
from .config import AppError
from .connectors import load_connectors, confirmed_destination
from .contracts import Artifact, CompiledStep, ExecutionReport
from .persistence.util import digest

MISSING = object()


def checked_path(path):
    """Reject traversal even if a proxy/router decodes percent escapes again."""
    decoded = path
    for _ in range(8):
        if "\\" in decoded or any(ord(c) < 32 or ord(c) == 127 for c in decoded):
            raise ValueError("ambiguous path")
        if any(part in (".", "..") for part in decoded.split("/")):
            raise ValueError("dot segment")
        next_path = unquote(decoded)
        if next_path == decoded:
            return decoded
        decoded = next_path
    raise ValueError("excessively encoded path")


class StepFailure(Exception):
    def __init__(self, outcome, detail, write_state="not_applied"):
        self.outcome, self.detail, self.write_state = outcome, detail, write_state
        super().__init__(detail)


def origin(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AppError("connector_invalid", "Connector base_url must be an http(s) URL without credentials, query or fragment")
    return f"{parsed.scheme}://{parsed.hostname.lower()}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}", parsed.path.rstrip("/")


def destination(connector, artifact: Artifact, settings):
    base = connector["base_url"].rstrip("/")
    host, _ = origin(base)
    allowed = {h.strip().lower() for h in settings.sandbox_hosts.split(",") if h.strip()}
    if host.split("://", 1)[1] not in allowed and not confirmed_destination(settings, connector, artifact):
        raise AppError("destination_not_allowed", f"{host} is not listed in SANDBOX_HOSTS", 403)
    if not connector.get("sandbox"):
        raise AppError("not_sandbox", "Only connectors explicitly marked sandbox may execute in this milestone", 403)
    if base != artifact["connector"]["base_url"].rstrip("/"):
        raise AppError("connector_changed", "The connector destination differs from the one recorded in the artifact; rebuild the artifact", 409)
    return base


def credentials(identity, artifact: Artifact):
    """Pre-flight: the trusted execution context must hold a usable end-user credential."""
    from .compiler.policies import authorize
    authorize(artifact.get("access_policy"), identity)
    auth = artifact["connector"]["auth"]
    mechanisms = {a["enforcement"].get("mechanism") for a in artifact["access_requirements"]}
    if identity is None:
        raise AppError("identity_unknown", "The sandbox identity is not configured for this connector", 404)
    if auth and not (identity.get("token") if auth["type"] == "http_bearer" else identity.get("username") and identity.get("password")):
        raise AppError("credential_missing", "The trusted execution context has no credential for this connector; no request was sent")
    if "delegated_user_credential" in mechanisms and identity.get("scope") != "end_user":
        raise AppError("credential_scope", "Only identities the operator registered as individual end users may run this tool; service or administrator credentials are refused", 403)
    needed = sorted({a["enforcement"]["context_field"] for a in artifact["access_requirements"] if a["enforcement"].get("mechanism") == "response_field_matches_context"})
    context = identity.get("context") or {}
    if any(isinstance(context.get(f), bool) or not isinstance(context.get(f), (str, int)) or context.get(f) == "" for f in needed):
        raise AppError("context_incomplete", "The trusted execution context lacks a value required by an access check: " + ", ".join(needed))
    context_values = dict(context, business_id=artifact["proposal"]["business_id"])
    for key, schema in artifact.get("compiler", {}).get("context_schemas", {}).items():
        if key not in context_values or list(OAS30Validator(schema).iter_errors(context_values[key])):
            raise AppError("context_incomplete", "Trusted context is missing or incompatible: " + key)


def validate_arguments(schema, arguments):
    errors = []
    if not isinstance(arguments, dict):
        raise AppError("invalid_arguments", "Arguments must be a JSON object")
    errors += [f"{k}: unknown argument" for k in sorted(set(arguments) - set(schema["properties"]))]
    errors += [f"{k}: required" for k in schema["required"] if k not in arguments]
    for name, value in sorted(arguments.items()):
        if name in schema["properties"]:
            errors += [f"{name}: violates {e.validator}" for e in OAS30Validator(schema["properties"][name], format_checker=oas30_format_checker).iter_errors(value)]
    if errors:
        raise AppError("invalid_arguments", "Arguments do not match the tool input schema; no request was sent", details={"errors": errors})


def resolve(document, pointer):
    for raw in pointer.split("/")[1:] if pointer else []:
        key = raw.replace("~1", "/").replace("~0", "~")
        if not isinstance(document, dict) or key not in document:
            raise KeyError(pointer)
        document = document[key]
    return document


def scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None or isinstance(value, (dict, list)):
        raise StepFailure("invalid_request", "A path/query value must be a primitive")
    return str(value)


class Run:
    def __init__(self, artifact: Artifact, arguments, identity, base, transport):
        self.artifact, self.arguments, self.identity, self.base = artifact, arguments, identity, base
        self.origin, self.base_path = origin(base)
        self.limits = artifact["limits"]
        self.context = dict(identity.get("context") or {}, business_id=artifact["proposal"]["business_id"])
        self.identity_context = identity.get("context") or {}
        self.results, self.trace = {}, []
        self.client = httpx.Client(transport=transport, follow_redirects=False, trust_env=False, timeout=self.limits["timeout_seconds"])

    def value(self, source):
        kind = source["kind"]
        if kind == "runtime_argument":
            return self.arguments.get(source["reference"], MISSING)
        if kind == "business_configuration":
            return json.loads(self.artifact["configuration"][source["reference"]])
        if kind == "trusted_application_context":
            if source["reference"] not in self.context:
                raise StepFailure("invalid_binding", "Required trusted context is missing")
            return self.context[source["reference"]]
        earlier = self.results[source["step_id"]]
        if earlier["status"] != int(source["response_status"]):
            raise StepFailure("invalid_binding", f"{source['step_id']} returned {earlier['status']}, not the bound {source['response_status']}")
        try:
            return resolve(earlier["json"], source.get("reference", ""))
        except KeyError:
            raise StepFailure("invalid_binding", f"{source['step_id']} response lacks the bound field {source.get('reference')}")

    def url(self, path, query=()):
        try:
            if not path.startswith("/") or "?" in path or "#" in path:
                raise ValueError("operation path must be absolute and contain no query or fragment")
            base_path = checked_path(self.base_path).rstrip("/")
            checked_path(path)
            # Inspect the same normalized URL the HTTP client will send, not the raw concatenation.
            target = httpx.URL(self.base + path)
            target_origin, _ = origin(str(target))
            target_path = checked_path(target.path)
            if target_origin != self.origin or not (target_path == base_path or target_path.startswith(base_path + "/")):
                raise ValueError("destination boundary")
            return str(target) + ("?" + urlencode(list(query)) if query else "")
        except (ValueError, httpx.InvalidURL, AppError):
            raise StepFailure("invalid_request", "The request path is unsafe or leaves the configured destination") from None

    def request(self, step: CompiledStep):
        path, query = step["path"], []
        for p in step["parameters"]:
            v = self.value(p["source"])
            if v is MISSING:
                continue
            if p["location"] == "path":
                text = scalar(v)
                if text in ("", ".", ".."):
                    raise StepFailure("invalid_request", f"Path parameter {p['name']} would change the path structure")
                path = path.replace("{" + p["name"] + "}", quote(text, safe=""))
            elif p["location"] == "query":
                query.extend((p["name"], scalar(x)) for x in (v if isinstance(v, list) else [v]))
        content = None
        spec = step["body"]
        if spec:
            if spec["whole"]:
                body = self.value(spec["whole"]["source"])
            else:
                values = {f["name"]: self.value(f["source"]) for f in spec["fields"]}
                body = {k: v for k, v in values.items() if v is not MISSING} or MISSING
            # Bodies without "required" (format /1 artifacts) always sent one; they keep that behavior.
            if body is MISSING and not spec.get("required", True):
                return self.url(path, query), None
            if body is MISSING:
                body = {}
            if spec.get("schema") is not None:
                errors = sorted({e.validator for e in OAS30WriteValidator(spec["schema"], format_checker=oas30_format_checker).iter_errors(body)})
                if errors:
                    raise StepFailure("invalid_request", "The assembled request body does not match the declared request schema (violates " + ", ".join(errors) + "); it was not sent")
            content = json.dumps(body, ensure_ascii=False).encode()
            if len(content) > self.limits["max_request_bytes"]:
                raise StepFailure("invalid_request", "Request body exceeds the artifact limit")
        return self.url(path, query), content

    def transport_headers(self, step, auth_headers):
        headers = dict(auth_headers)
        cookies = []
        for p in step["parameters"]:
            if p["location"] not in ("header", "cookie"):
                continue
            value = self.value(p["source"])
            if value is MISSING:
                continue
            text = scalar(value)
            if any(ord(c) < 32 or ord(c) == 127 for c in text) or any(ord(c) < 33 or ord(c) > 126 or c in ":;=" for c in p["name"]):
                raise StepFailure("invalid_request", "Invalid transport context")
            if p["location"] == "header" and not text.isascii():
                raise StepFailure("invalid_request", "Header context requires an ASCII value")
            if p["location"] == "cookie":
                cookies.append(p["name"] + "=" + quote(text, safe=""))
            else:
                if p["name"].lower() in {k.lower() for k in headers}:
                    raise StepFailure("invalid_request", "Transport context cannot override connector headers")
                headers[p["name"]] = text
        if cookies:
            headers["Cookie"] = "; ".join(cookies)
        return headers

    def send(self, method, url, content=None, form=None, write=False, headers=None):
        headers = {"Accept": "application/json", **(headers or {})}
        if content is not None:
            headers["Content-Type"] = "application/json"
        try:
            with self.client.stream(method, url, headers=headers, content=content, data=form) as response:
                if 300 <= response.status_code < 400:
                    raise StepFailure("redirect_rejected", f"HTTP {response.status_code} redirect was not followed", "unknown" if write else "not_applied")
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > self.limits["max_response_bytes"]:
                        if 200 <= response.status_code < 300:
                            raise StepFailure("invalid_response", "Response exceeds the artifact size limit", "applied" if write else "not_applied")
                        break
                return response.status_code, response.headers.get("content-type", ""), bytes(data)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise StepFailure("not_sent", f"Connection failed before sending ({type(exc).__name__})")
        except httpx.TimeoutException as exc:
            raise StepFailure("no_response", f"Sent, but no complete response arrived ({type(exc).__name__})", "unknown" if write else "not_applied")
        except httpx.HTTPError as exc:
            raise StepFailure("no_response", f"Transport failure after sending ({type(exc).__name__})", "unknown" if write else "not_applied")

    def authenticate(self):
        auth = self.artifact["connector"]["auth"]
        if not auth:
            return {}
        if auth["type"] == "http_bearer":
            return {"Authorization": "Bearer " + self.identity["token"]}
        form = dict(grant_type="password", username=self.identity["username"], password=self.identity["password"])
        if auth["scopes"]:
            form["scope"] = " ".join(auth["scopes"])
        status, ctype, data = self.send("POST", self.url(auth["token_path"]), form=form)
        try:
            token = json.loads(data) if status == 200 else {}
        except ValueError:
            token = {}
        if not isinstance(token, dict) or not isinstance(token.get("access_token"), str) or str(token.get("token_type", "bearer")).lower() != "bearer":
            raise StepFailure("connector_auth_failed", f"The connector could not obtain a bearer token (HTTP {status})")
        return {"Authorization": "Bearer " + token["access_token"]}

    def step(self, step: CompiledStep, headers):
        write = step["effect"] == "write"
        entry = dict(step_id=step["id"], method=step["method"], path=step["path"], effect=step["effect"])
        self.trace.append(entry)
        started = time.monotonic()
        try:
            url, content = self.request(step)
            status, ctype, data = self.send(step["method"], url, content, write=write, headers=self.transport_headers(step, headers))
            entry.update(http_status=status, response_bytes=len(data))
            if not 200 <= status < 300:
                # An HTTP rejection is observed; absence of side effects is not. A server error may follow a committed change.
                raise StepFailure("rejected_by_api", f"HTTP {status}", ("unknown" if status >= 500 else "rejected_unverified") if write else "not_applied")
            applied = "applied" if write else "not_applied"
            declared = step["responses"].get(str(status))
            if declared is None:
                raise StepFailure("invalid_response", f"Undeclared success status {status}", applied)
            body = None
            if declared["schema"] is not None:
                if "application/json" not in ctype.lower():
                    raise StepFailure("invalid_response", f"Expected application/json, got {ctype.split(';')[0] or 'no content type'}", applied)
                try:
                    body = json.loads(data)
                    OAS30Validator(declared["schema"]).validate(body)
                except (ValueError, ValidationError) as exc:
                    raise StepFailure("invalid_response", f"Response does not match the declared schema ({type(exc).__name__})", applied)
            self.results[step["id"]] = dict(status=status, json=body)
            entry.update(outcome="succeeded", write_state=applied if write else None)
            for req in self.artifact["access_requirements"]:
                check = req["enforcement"]
                if check.get("mechanism") == "response_field_matches_context" and check["step_id"] == step["id"]:
                    try:
                        found = resolve(body, check["pointer"]) if str(status) == check["response_status"] else None
                    except KeyError:
                        found = None
                    expected = self.identity_context.get(check["context_field"])
                    # comparison "equals": string/integer values compared by canonical text; anything else fails closed.
                    from .compiler.security_tests import scope_matches
                    if not scope_matches(found, expected):
                        raise StepFailure("blocked_by_access_check", f"{req['id']}: {check['pointer']} does not equal trusted context field {check['context_field']}", applied)
        except StepFailure as failure:
            entry.update(outcome=failure.outcome, detail=failure.detail, write_state=failure.write_state if write else None)
            raise
        finally:
            entry["duration_ms"] = round((time.monotonic() - started) * 1000)

    def outputs(self):
        result = {}
        for o in self.artifact["outputs"]:
            earlier = self.results[o["step_id"]]
            try:
                if earlier["status"] != int(o["response_status"]):
                    raise KeyError(o["pointer"])
                result[o["name"]] = resolve(earlier["json"], o["pointer"])
            except KeyError:
                if o["name"] in self.artifact["output_schema"]["required"]:
                    raise StepFailure("invalid_response", f"Output {o['name']} is missing from {o['step_id']}'s response")
        return result

    def execute(self) -> tuple[ExecutionReport, dict | None]:
        if self.artifact.get("access_policy"):
            credentials(self.identity, self.artifact)
            validate_arguments(self.artifact["input_schema"], self.arguments)
        failure, outputs, failed_step = None, None, None
        with self.client:
            try:
                headers = self.authenticate()
            except StepFailure as exc:
                failure = exc
                failed_step = "connector_auth"
            if failure is None:
                for step in self.artifact["steps"]:
                    try:
                        self.step(step, headers)
                    except StepFailure as exc:
                        failure, failed_step = exc, step["id"]
                        break
            if failure is None:
                try:
                    outputs = self.outputs()
                except StepFailure as exc:
                    failure, failed_step = exc, "outputs"
        done = {e["step_id"] for e in self.trace}
        self.trace += [dict(step_id=s["id"], method=s["method"], path=s["path"], effect=s["effect"], outcome="not_attempted", write_state="not_attempted" if s["effect"] == "write" else None)
                       for s in self.artifact["steps"] if s["id"] not in done]
        states = [e["write_state"] for e in self.trace if e["effect"] == "write"]
        applied = [e["step_id"] for e in self.trace if e.get("write_state") == "applied"]
        if failure is None:
            status, message = "succeeded", "All steps succeeded and outputs were extracted."
        elif "unknown" in states:
            status = "outcome_unknown"
            message = f"{failed_step} failed: {failure.detail}. A write may or may not have been applied; check the target before retrying. Nothing was retried."
        elif applied:
            status = "partial"
            message = f"{', '.join(applied)} applied a change before {failed_step} failed: {failure.detail}. Earlier changes were not rolled back."
        else:
            status = "failed"
            rejected = "rejected_unverified" in states
            message = f"{failed_step} failed: {failure.detail}. " + ("The API rejected the write; that no change occurred has not been verified." if rejected else "No write was sent.")
        report = dict(status=status, message=message, failure=dict(step_id=failed_step, outcome=failure.outcome, detail=failure.detail) if failure else None,
                      trace=self.trace, arguments=sorted(self.arguments),
                      outputs_sha256={k: digest(v) for k, v in (outputs or {}).items()}, runtime_ready=False)
        return report, outputs
