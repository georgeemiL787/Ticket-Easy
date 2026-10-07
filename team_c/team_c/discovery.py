"""Offline, bounded OpenAPI discovery. Never executes API operations or fetches references."""
import copy
import json
import re
import uuid
from urllib.parse import urlparse
import yaml
from openapi_spec_validator import OpenAPIV30SpecValidator, OpenAPIV31SpecValidator
from .contracts import Inventory

PARSER_VERSION = "2"
METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
VERSIONS = {**{v: ("3.0", OpenAPIV30SpecValidator) for v in ("3.0.0", "3.0.1", "3.0.2", "3.0.3", "3.0.4")},
            **{v: ("3.1", OpenAPIV31SpecValidator) for v in ("3.1.0", "3.1.1", "3.1.2")}}
ROOT_KEYS = {"openapi", "info", "jsonSchemaDialect", "servers", "paths", "webhooks", "components", "security", "tags", "externalDocs"}
OAS31_DIALECT = "https://spec.openapis.org/oas/3.1/dialect/base"
SCHEMA_KEYS = {"$ref", "title", "description", "type", "format", "default", "example", "enum", "nullable", "properties", "required", "items", "additionalProperties", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems", "minProperties", "maxProperties", "readOnly", "writeOnly", "deprecated", "xml", "externalDocs"}
SCHEMA_KEYS_31 = (SCHEMA_KEYS - {"nullable"}) | {"examples", "$comment", "const", "anyOf", "oneOf"}
ANNOTATIONS_31 = {"title", "description", "default", "example", "examples", "$comment", "deprecated", "readOnly", "writeOnly", "xml", "externalDocs"}
COMPOSITION = {"allOf", "oneOf", "anyOf", "not", "discriminator", "if", "then", "else", "prefixItems", "dependentSchemas"}
# Review signals only: names never establish or deny a permission.
AUTH_WORDS = {"login", "logout", "signin", "signup", "password", "passwords", "passwd", "token", "tokens", "oauth", "oauth2", "auth", "authenticate", "authorize", "credential", "credentials", "otp", "mfa", "apikey"}
PRIVILEGED_WORDS = {"admin", "admins", "administrator", "superuser", "superusers", "internal", "private", "staff", "sudo", "impersonate"}
ACCOUNT_WORDS = {"user", "users", "account", "accounts", "member", "members", "profile", "profiles", "me"}
SECRET_WORDS = {"password", "passwd", "passphrase", "secret", "token", "apikey", "credential", "credentials"}


def esc(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def pointer(document, ref):
    node = document
    for part in ref.removeprefix("#").split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        node = node[int(key)] if isinstance(node, list) else node[key]
    return node


def words(text):
    text = re.sub(r"\{[^}]*\}", " ", str(text))
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    parts = [w for w in re.split(r"[^A-Za-z0-9]+", text.lower()) if w]
    return set(parts) | {a + b for a, b in zip(parts, parts[1:]) if (a, b) == ("api", "key")}


class UniqueLoader(yaml.SafeLoader):
    pass


def mapping(loader, node, deep=False):
    result = {}
    for k, v in node.value:
        key = loader.construct_object(k, deep=deep)
        if key in result:
            raise ValueError(f"Duplicate key: {key}")
        result[key] = loader.construct_object(v, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate key: {key}")
        result[key] = value
    return result


class Scope:
    """Problems, normalization notes and source pointers for one operation (or one error response)."""
    def __init__(self):
        self.problems, self.notes, self.sources = [], [], {}

    def problem(self, code, message, ptr):
        self.problems.append(dict(code=code, message=message, pointer=ptr))

    def note(self, ptr, rule):
        if dict(pointer=ptr, rule=rule) not in self.notes:
            self.notes.append(dict(pointer=ptr, rule=rule))


class Normalizer:
    """Maps declared schemas to the internal OpenAPI 3.0-style subset only where semantics are preserved."""
    def __init__(self, document, family):
        self.doc, self.family = document, family

    def deref(self, node, ptr, scope):
        seen = []
        while isinstance(node, dict) and "$ref" in node:
            extra = set(node) - {"$ref"}
            if extra and (self.family == "3.0" or extra - {"summary", "description"}):
                scope.problem("reference_siblings", "Reference siblings are unsupported here", ptr)
                return None, ptr
            if node["$ref"] in seen:
                scope.problem("reference_resolution", f"Recursive reference: {node['$ref']}", ptr)
                return None, ptr
            seen.append(node["$ref"])
            ptr = node["$ref"]
            node = pointer(self.doc, ptr)
        return node, ptr

    def schema(self, node, ptr, loc, scope, stack=()):
        if not isinstance(node, dict):
            scope.problem("unsupported_schema", "Boolean or non-object schemas are unsupported", ptr)
            return None
        if "$ref" in node:
            ref = node["$ref"]
            extra = {k: v for k, v in node.items() if k != "$ref"}
            if extra and self.family == "3.0":
                scope.problem("reference_siblings", "Reference siblings are unsupported (OpenAPI 3.0 ignores them)", ptr)
                return None
            if set(extra) - ANNOTATIONS_31:
                scope.problem("reference_siblings", "A $ref with constraining siblings is unsupported", ptr)
                return None
            if ref in stack or len(stack) > 40:
                scope.problem("reference_resolution", f"Recursive reference: {ref}", ptr)
                return None
            result = self.schema(pointer(self.doc, ref), ref, loc, scope, (*stack, ref))
            if result is not None and extra:
                scope.note(ptr, "annotations beside $ref kept")
                result = {**result, **copy.deepcopy(extra)}
            return result
        scope.sources[loc] = ptr
        allowed = SCHEMA_KEYS if self.family == "3.0" else SCHEMA_KEYS_31
        unknown = [k for k in node if k not in allowed and not k.startswith("x-")]
        for key in unknown:
            code = "unsupported_construct" if key in COMPOSITION else "unsupported_schema_keyword"
            message = "nullable is not an OpenAPI 3.1 keyword; use type [T, \"null\"]" if key == "nullable" else f"Unsupported schema keyword: {key}"
            scope.problem(code, message, ptr + "/" + esc(key))
        if unknown:
            return None
        unions = [k for k in ("anyOf", "oneOf") if k in node]
        if unions:
            return self.nullable_union(node, unions, ptr, loc, scope, stack)
        return self.keywords(node, ptr, loc, scope, stack)

    def keywords(self, node, ptr, loc, scope, stack):
        out, failed = {}, False
        for key, value in node.items():
            if key == "type" and isinstance(value, list):
                non_null = [t for t in value if t != "null"]
                if len(set(value)) != len(value) or len(non_null) != 1:
                    scope.problem("unsupported_union", f"Type union {value} is unsupported", ptr + "/type")
                    return None
                out["type"] = non_null[0]
                if "null" in value:
                    out["nullable"] = True
                    scope.note(ptr + "/type", "type [T, null] -> nullable")
            elif key == "type" and value == "null":
                scope.problem("unsupported_union", "A null-only schema is unsupported", ptr + "/type")
                return None
            elif key == "const":
                if "enum" in node:
                    scope.problem("conflicting_constraints", "const together with enum is unsupported", ptr + "/const")
                    return None
                out["enum"] = [copy.deepcopy(value)]
                scope.note(ptr + "/const", "const -> single-value enum")
            elif key in ("exclusiveMinimum", "exclusiveMaximum") and self.family == "3.1":
                bound = "minimum" if key == "exclusiveMinimum" else "maximum"
                if bound in node:
                    scope.problem("conflicting_constraints", f"{key} together with {bound} is unsupported", ptr + "/" + key)
                    return None
                out[bound], out[key] = value, True
                scope.note(ptr + "/" + key, f"numeric {key} -> {bound} with boolean {key}")
            elif key == "properties" and isinstance(value, dict):
                out[key] = {}
                for name, child in value.items():
                    result = self.schema(child, f"{ptr}/properties/{esc(name)}", f"{loc}/properties/{esc(name)}", scope, stack)
                    failed = failed or result is None
                    out[key][name] = result
            elif key == "items" or (key == "additionalProperties" and not isinstance(value, bool)):
                out[key] = self.schema(value, f"{ptr}/{key}", f"{loc}/{key}", scope, stack)
                failed = failed or out[key] is None
            else:
                out[key] = copy.deepcopy(value)
        return None if failed else out

    def nullable_union(self, node, unions, ptr, loc, scope, stack):
        key = unions[0]
        branches = node[key]
        nulls = [i for i, b in enumerate(branches) if b == {"type": "null"}] if isinstance(branches, list) else []
        if len(unions) > 1 or "type" in node or len(branches) != 2 or len(nulls) != 1:
            scope.problem("unsupported_union", f"{key} is supported only as [schema, {{type: null}}]", ptr + "/" + key)
            return None
        index = 1 - nulls[0]
        inner = self.schema(branches[index], f"{ptr}/{key}/{index}", loc, scope, stack)
        holder = self.keywords({k: v for k, v in node.items() if k != key}, ptr, loc, scope, stack)
        if inner is None or holder is None:
            return None
        if key == "oneOf" and (not inner.get("type") or inner.get("nullable")):
            scope.problem("unsupported_union", "oneOf whose schema branch also admits null is unsupported", ptr + "/oneOf")
            return None
        merged = dict(inner)
        for k, v in holder.items():
            if k in merged and merged[k] != v and k not in ANNOTATIONS_31:
                scope.problem("conflicting_constraints", f"{k} is declared both beside and inside {key}", ptr + "/" + k)
                return None
            merged[k] = v
        merged["nullable"] = True
        scope.sources[loc] = ptr
        scope.note(ptr + "/" + key, f"{key} [T, null] -> nullable")
        return merged


def ref_closure(document, roots):
    """Local references transitively used by the given nodes (never fetched)."""
    found, pending = set(), list(roots)
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/") and ref not in found:
                found.add(ref)
                pending.append(pointer(document, ref))
            pending.extend(v for k, v in node.items() if k != "$ref")
        elif isinstance(node, list):
            pending.extend(node)
    return found


def subdocument(document, route, method, refs):
    """Unmodified operation plus only what it references; used to attribute validation errors."""
    item = document["paths"][route]
    sub = {k: document[k] for k in ("openapi", "info", "jsonSchemaDialect", "servers", "security") if k in document}
    sub["paths"] = {route: {**{k: v for k, v in item.items() if k not in METHODS}, method: item[method]}}
    for ref in sorted(refs):
        parts = [p.replace("~1", "/").replace("~0", "~") for p in ref[2:].split("/")]
        if parts[0] == "paths":
            continue
        source, target = document, sub
        for i, part in enumerate(parts):
            if isinstance(source, list) or i == len(parts) - 1:
                target[part] = copy.deepcopy(source[int(part)] if isinstance(source, list) else source[part])
                break
            source = source[part]
            target = target.setdefault(part, {})
    return sub


def discover(raw: bytes, filename: str, business_id: str, max_operations=50) -> tuple[dict | None, Inventory]:
    diagnostics = []
    operations = []
    document = None
    traversal_nodes = [0]

    def issue(code, message, path="#", severity="error", operation=None, scope="document"):
        entry = dict(code=code, message=message, pointer=path, severity=severity, operation=operation, scope=scope)
        if entry not in diagnostics:
            diagnostics.append(entry)

    def walk(node, path="#", ancestors=(), depth=0):
        traversal_nodes[0] += 1
        if traversal_nodes[0] > 50_000:
            raise ValueError("Document traversal node limit exceeded")
        if depth > 60:
            raise ValueError("Maximum document nesting (60) exceeded")
        if isinstance(node, (dict, list)):
            if id(node) in ancestors:
                raise ValueError("Recursive YAML aliases are unsupported")
            ancestors = (*ancestors, id(node))
        if isinstance(node, dict):
            for key, value in node.items():
                yield path + "/" + esc(key), key, value, node
                yield from walk(value, path + "/" + esc(key), ancestors, depth + 1)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                yield from walk(value, path + "/" + str(i), ancestors, depth + 1)

    def result(version=None, family=None):
        document_valid = not any(d["severity"] == "error" and d["scope"] == "document" for d in diagnostics)
        count = lambda f: sum(1 for op in operations if f(op))
        summary = dict(operations=len(operations), technically_supported=count(lambda o: o["supported"]),
                       proposal_eligible=count(lambda o: o.get("proposal_eligible")),
                       restricted=count(lambda o: o.get("exposure", {}).get("classification") == "restricted"),
                       requires_clarification=count(lambda o: o.get("exposure", {}).get("classification") == "requires_clarification"))
        ready = document_valid and summary["proposal_eligible"] > 0
        # valid is a deprecated alias of proposal_generation_ready, kept for stored records and existing consumers.
        return dict(valid=ready, document_valid=document_valid, eligible_operation_count=summary["proposal_eligible"], proposal_generation_ready=ready, parser_version=PARSER_VERSION,
                    openapi_version=version, openapi_family=family, operations=operations, diagnostics=diagnostics, summary=summary)

    try:
        text = raw.decode("utf-8-sig")
        document = json.loads(text, object_pairs_hook=unique_pairs, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"Invalid JSON constant {x}"))) if filename.lower().endswith(".json") else yaml.load(text, Loader=UniqueLoader)
        if not isinstance(document, dict):
            raise ValueError("The document root must be an object")
        # JSON roundtrip also rejects non-JSON YAML values, e.g. dates and arbitrary keys.
        entries = []
        for entry in walk(document):
            entries.append(entry)
            if len(entries) > 50_000:
                raise ValueError("Document node limit exceeded")
        document = json.loads(json.dumps(document, allow_nan=False))
    except Exception as exc:
        issue("malformed_input", str(exc)[:500])
        return None, result()

    version = document.get("openapi")
    family, validator_cls = VERSIONS.get(version, (None, None)) if isinstance(version, str) else (None, None)
    if not family:
        issue("unsupported_version", "Supported versions are 3.0.0-3.0.4 and 3.1.0-3.1.2; the source version is never rewritten", "#/openapi")
    if family == "3.1" and document.get("jsonSchemaDialect", OAS31_DIALECT) != OAS31_DIALECT:
        issue("unsupported_dialect", "Only the default OpenAPI 3.1 schema dialect is supported", "#/jsonSchemaDialect")

    # No validator or resolver sees a document with an external or dangling reference.
    for path, key, value, parent in entries:
        if key == "$ref":
            if not isinstance(value, str) or not value.startswith("#/"):
                issue("external_reference", "Only document-local #/ references are supported; the document cannot be validated offline", path)
            else:
                try:
                    pointer(document, value)
                except (KeyError, IndexError, ValueError, TypeError):
                    issue("unresolved_reference", f"Unresolved reference: {value}", path)
        if key in {"callbacks", "links"} and path.startswith("#/paths/"):
            issue("unsupported_construct", f"{key} are retained but not discovered", path, "warning", scope="operation")
    if family == "3.1" and document.get("webhooks"):
        issue("webhooks_not_discovered", "Webhooks are retained but not discovered as callable operations", "#/webhooks", "warning")

    source_paths = document.get("paths") if isinstance(document.get("paths"), dict) else {}
    listed = [(route, method) for route, item in source_paths.items() if isinstance(item, dict) for method in item if method in METHODS]
    if len(listed) > max_operations:
        issue("operation_limit", f"Maximum {max_operations} operations supported")
    if not listed:
        issue("no_operations", "No operations found")
    label_of = lambda route, method: f"{method.upper()} {route}"
    op_errors = {}
    blocked = any(d["severity"] == "error" for d in diagnostics)

    if not blocked:
        # The original document is validated with its declared version before any normalization.
        try:
            whole = [(e.message[:300], "#" + "".join("/" + esc(p) for p in e.path)) for e in validator_cls(document).iter_errors()]
        except Exception as exc:
            whole = [(f"Validator failure: {str(exc)[:300]}", "#")]
        if whole:
            # Root-level errors stay document-level; path/component errors (and schema-relative ones) may be attributed.
            messages = {m for m, p in whole if p.startswith(("#/paths/", "#/components/")) or (p != "#" and p.split("/")[1] not in ROOT_KEYS)}
            explained = set()
            for route, method in listed:
                item = source_paths[route]
                roots = [item[method], item.get("parameters", [])]
                refs = ref_closure(document, roots)
                security = item[method].get("security", document.get("security", [])) if isinstance(item[method], dict) else []
                for req in security if isinstance(security, list) else []:
                    for name in req if isinstance(req, dict) else []:
                        refs.add("#/components/securitySchemes/" + esc(name))
                refs = {r for r in refs if _exists(document, r)}
                try:
                    found = {e.message[:300] for e in validator_cls(subdocument(document, route, method, refs)).iter_errors()} & messages
                except Exception:
                    found = set(messages)
                if found:
                    op_errors[label_of(route, method)] = sorted(found)
                    explained |= found
            for message, path in whole:
                if message not in explained:
                    issue("invalid_openapi", message, path)
            where = dict((m, p) for m, p in reversed(whole))
            for label, found in op_errors.items():
                for message in found[:5]:
                    issue("invalid_openapi", message, where[message], operation=label, scope="operation")

    blocked = any(d["severity"] == "error" and d["scope"] == "document" for d in diagnostics)
    normalizer = Normalizer(document, family)
    schemes = (document.get("components") or {}).get("securitySchemes") or {}
    oauth_paths = set()
    for scheme in schemes.values() if isinstance(schemes, dict) else []:
        for flow in (scheme.get("flows") or {}).values() if isinstance(scheme, dict) else []:
            for key in ("tokenUrl", "refreshUrl", "authorizationUrl"):
                if isinstance(flow, dict) and isinstance(flow.get(key), str):
                    oauth_paths.add(urlparse(flow[key]).path.rstrip("/"))

    for route, item in source_paths.items():
        if not isinstance(item, dict):
            continue
        if "$ref" in item:
            issue("referenced_path_item", "Referenced Path Items are unsupported", "#/paths/" + esc(route), scope="path")
        for method, original in item.items():
            if method not in METHODS:
                continue
            label = label_of(route, method)
            ref = f"#/paths/{esc(route)}/{method}"
            op_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{business_id}:{label}"))
            record = dict(id=op_id, source_pointer=ref, method=method.upper(), path=route, original=copy.deepcopy(original), supported=False, proposal_eligible=False)
            operations.append(record)
            if blocked or "$ref" in item or not isinstance(original, dict):
                continue
            try:
                record.update(extract(document, normalizer, family, route, item, method, original, ref, schemes, oauth_paths, issue, label))
            except (KeyError, TypeError, AttributeError, IndexError, ValueError) as exc:
                issue("invalid_operation", f"Cannot extract operation: {exc}", ref, operation=label, scope="operation")
            errors = [d for d in diagnostics if d["severity"] == "error" and d.get("operation") == label]
            record["supported"] = not errors
            if "exposure" in record:
                binding = dict(status="supported" if not errors else "unsupported", errors=[f'{d["code"]}: {d["message"]}' for d in errors][:10])
                record["binding"] = binding
                if record["exposure"]["classification"] != "restricted":
                    record["exposure"]["classification"] = "requires_clarification" if not errors else "technically_unsupported"
                record["proposal_eligible"] = not errors and record["exposure"]["classification"] != "restricted"
    return document, result(version, family)


def _exists(document, ref):
    try:
        pointer(document, ref)
        return True
    except (KeyError, IndexError, ValueError, TypeError):
        return False


def extract(document, normalizer, family, route, item, method, op, ref, schemes, oauth_paths, issue, label):
    scope = Scope()
    params = {}
    for base, raw_params in ((f"#/paths/{esc(route)}/parameters", item.get("parameters", [])), (ref + "/parameters", op.get("parameters", []))):
        for i, raw_param in enumerate(raw_params):
            param, pptr = normalizer.deref(raw_param, f"{base}/{i}", scope)
            if param is not None:
                params[(param["name"], param["in"])] = (param, pptr)
    inputs = {}
    for (name, location), (param, pptr) in params.items():
        if "content" in param:
            scope.problem("parameter_content", "Parameter content is unsupported", pptr)
            continue
        schema = normalizer.schema(param.get("schema", {}), pptr + "/schema", f"inputs/{location}.{name}", scope)
        inputs[f"{location}.{name}"] = dict(schema=schema, required=bool(param.get("required", False)), description=param.get("description", ""))

    request, request_body = None, None
    if "requestBody" in op:
        request, rptr = normalizer.deref(op["requestBody"], ref + "/requestBody", scope)
    if request:
        content = request.get("content", {})
        if content and "application/json" not in content:
            scope.problem("unsupported_media", "A nonempty payload must offer application/json", rptr + "/content")
        elif content:
            if len(content) > 1:
                issue("alternative_media", "JSON selected; other declared media types are preserved but not bound", rptr + "/content", "warning", label, "operation")
            raw_schema = content["application/json"].get("schema")
            if raw_schema is None:
                scope.problem("missing_payload_schema", "JSON payload has no schema", rptr + "/content/application~1json")
            else:
                body = normalizer.schema(raw_schema, rptr + "/content/application~1json/schema", "inputs/body", scope)
                request_body = dict(required=bool(request.get("required", False)), description=request.get("description", ""), content={"application/json": {"schema": body}})
                if body and body.get("type") == "object" and body.get("properties"):
                    for name, schema in body["properties"].items():
                        scope.sources[f"inputs/body.{name}"] = scope.sources.get(f"inputs/body/properties/{esc(name)}")
                        if schema is not None and not schema.get("readOnly"):
                            inputs["body." + name] = dict(schema=schema, required=bool(request.get("required") and name in body.get("required", [])), description=schema.get("description", ""))
                            if not request.get("required") and name in body.get("required", []):
                                inputs["body." + name]["required_when_body_sent"] = True
                elif body is not None:
                    inputs["body"] = dict(schema=body, required=bool(request.get("required", False)), description=request.get("description", ""))

    responses = {}
    for code, raw_response in op.get("responses", {}).items():
        code = str(code)
        success = len(code) == 3 and code.isdigit() and code.startswith("2")
        # Non-success responses are metadata: they cannot be selected as outputs, so their problems do not block binding.
        target = scope if success else Scope()
        response, rsptr = normalizer.deref(raw_response, f"{ref}/responses/{esc(code)}", target)
        schema, status = None, "none"
        content = (response or {}).get("content", {})
        if response is None:
            status = "unsupported"
        elif content and "application/json" not in content:
            target.problem("unsupported_media", "A nonempty payload must offer application/json", rsptr + "/content")
            status = "unsupported"
        elif content:
            if len(content) > 1:
                issue("alternative_media", "JSON selected; other declared media types are preserved but not bound", rsptr + "/content", "warning", label, "operation")
            raw_schema = content["application/json"].get("schema")
            if raw_schema is None:
                target.problem("missing_payload_schema", "JSON payload has no schema", rsptr + "/content/application~1json")
                status = "unsupported"
            else:
                schema = normalizer.schema(raw_schema, rsptr + "/content/application~1json/schema", f"responses/{code}", target)
                status = "normalized" if schema is not None else "unsupported"
        if not success:
            for p in target.problems:
                issue("error_response_unsupported", f"Response {code} is not bindable: {p['message']}", p["pointer"], "warning", label, "operation")
            scope.sources.update(target.sources)
        responses[code] = dict(schema=schema, schema_status=status, description=(response or {}).get("description", ""), headers=(response or {}).get("headers", {}), content=content)

    if "security" in op:
        security, source = op["security"], "operation"
    elif "security" in document:
        security, source = document["security"], "document"
    else:
        security, source = [], "absent"
    alternatives = []
    for requirement in security:
        option = []
        for name, scopes in requirement.items():
            scheme = schemes.get(name) if isinstance(schemes, dict) else None
            if scheme is None:
                scope.problem("undeclared_security", f"Security scheme {name} is not declared", ref)
                continue
            scheme, _ = normalizer.deref(scheme, "#/components/securitySchemes/" + esc(name), scope)
            detail = {("http_scheme" if k == "scheme" else k): scheme[k] for k in ("scheme", "bearerFormat", "in", "name", "openIdConnectUrl") if isinstance(scheme, dict) and k in scheme}
            if isinstance(scheme, dict) and scheme.get("type") == "oauth2":
                detail["flows"] = sorted(scheme.get("flows", {}))
            option.append(dict(scheme=name, type=(scheme or {}).get("type"), scopes=list(scopes), **detail))
        alternatives.append(option)
    if not security:
        auth_status = "none_declared" if source == "absent" else "explicitly_none"
    else:
        auth_status = "optional" if any(req == {} for req in security) else "required"
    declared_auth = dict(status=auth_status, source=source, alternatives=alternatives,
                         note="Declared authentication is metadata; it does not prove identity, permission or data scoping.")

    for problem in scope.problems:
        issue(problem["code"], problem["message"], problem["pointer"], operation=label, scope="operation")

    signals = []
    def signal(kind, basis, effect, detail):
        signals.append(dict(kind=kind, basis=basis, effect=effect, detail=detail))
    names = words(route) | words(op.get("operationId", "")) | set().union(*[words(t) for t in op.get("tags", [])] or [set()])
    if route.rstrip("/") in oauth_paths:
        signal("credential_exchange", "declared", "restricts", "Path is an OAuth2 flow URL declared by a security scheme")
    for field, schema in fields(inputs, responses):
        if schema.get("format") == "password":
            signal("credential_field", "declared", "restricts", f"{field} declares format: password")
        elif words(field.rsplit("/", 1)[-1].rsplit(".", 1)[-1]) & SECRET_WORDS:
            signal("credential_field", "field_name_heuristic", "restricts", f"{field} looks credential-bearing")
    if names & AUTH_WORDS:
        signal("auth_lifecycle", "name_heuristic", "restricts", "Path/operationId/tags mention " + ", ".join(sorted(names & AUTH_WORDS)))
    if names & PRIVILEGED_WORDS:
        signal("privileged", "name_heuristic", "restricts", "Path/operationId/tags mention " + ", ".join(sorted(names & PRIVILEGED_WORDS)))
    if names & ACCOUNT_WORDS:
        signal("account_records", "name_heuristic", "review", "Touches user/account records; confirm it is not administrative")
    if method == "delete":
        signal("destructive", "declared", "review", "DELETE method")

    unresolved = []
    if auth_status in ("required", "optional"):
        unresolved.append("Which callers may use this operation is not declared (OpenAPI declares authentication, not roles or permissions).")
        scopes = sorted({s for option in alternatives for a in option for s in a["scopes"]})
        if scopes:
            unresolved.append("Declared scopes " + ", ".join(scopes) + " apply to the connector credential; which end users may exercise them is not declared.")
    elif auth_status == "none_declared":
        unresolved.append("No security requirement is declared; this is not evidence of public access. Confirm whether this operation is public.")
    else:
        unresolved.append("security: [] declares no authentication; confirm this operation is intended to be public.")
    for key in inputs:
        if key.startswith("path."):
            unresolved.append(f"Whether a caller may access the record identified by {key} (ownership/data scoping) is not declared.")
    unresolved.extend(s["detail"] for s in signals if s["effect"] == "review")

    restricted = any(s["effect"] == "restricts" for s in signals)
    return dict(operation_id=op.get("operationId"), description=op.get("description", ""), summary=op.get("summary", ""), tags=op.get("tags", []),
                parameters=[p for p, _ in params.values()], request_body=request_body, inputs=inputs, responses=responses,
                security=security, security_schemes=schemes, access_status="unverified", declared_auth=declared_auth,
                authorization=dict(status="not_declared", unresolved_requirements=unresolved,
                                   note="Path, tag and field-name heuristics are review signals, not verified permissions."),
                exposure=dict(classification="restricted" if restricted else "requires_clarification", signals=signals),
                owner_approval=dict(state="not_reviewed", note="Owner approval applies to proposals (approved to build) and is never runtime authorization."),
                normalization=dict(notes=scope.notes[:100], source_pointers=scope.sources))


def fields(inputs, responses):
    """Input fields and success-response properties, recursively, for credential signals."""
    def walk(schema, path):
        if not isinstance(schema, dict):
            return
        yield path, schema
        for name, child in (schema.get("properties") or {}).items():
            yield from walk(child, f"{path}/{name}")
        if isinstance(schema.get("items"), dict):
            yield from walk(schema["items"], path + "/items")
    for key, field in inputs.items():
        yield from walk(field["schema"], key)
    for code, response in responses.items():
        if code.startswith("2") and response["schema"]:
            yield from walk(response["schema"], f"response.{code}")
