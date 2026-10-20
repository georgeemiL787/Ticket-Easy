"""Swagger 2.0 -> OpenAPI 3.0.3, in-house and standard library only.

Discovery works on OpenAPI 3.x. A Swagger 2.0 description is converted first, keeping every operation, parameter, schema and
security scheme that has a 3.0 equivalent. Nothing is fetched and nothing is guessed: a construct with no equivalent is
left as it is, so the 3.0 validator and the tools service's per-operation checks report it against that operation.
"""
import copy

REF_MAP = {"#/definitions/": "#/components/schemas/", "#/parameters/": "#/components/parameters/", "#/responses/": "#/components/responses/"}
SCHEMA_FIELDS = ("type", "format", "enum", "default", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
                 "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems", "description", "title", "example")
METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
FLOWS = {"implicit": "implicit", "password": "password", "application": "clientCredentials", "accessCode": "authorizationCode"}
FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


def is_swagger2(document):
    return isinstance(document, dict) and document.get("swagger") == "2.0"


def rewrite_refs(node):
    """`#/definitions/X` -> `#/components/schemas/X` everywhere; other references are left for the validator to judge."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                for old, new in REF_MAP.items():
                    if value.startswith(old):
                        value = new + value[len(old):]
                        break
            out[key] = rewrite_refs(value)
        return out
    if isinstance(node, list):
        return [rewrite_refs(item) for item in node]
    return node


def schema(node):
    """A Swagger 2.0 schema object as an OpenAPI 3.0 one (only the differences)."""
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key == "x-nullable":
            if value is True:
                out["nullable"] = True
        elif key == "discriminator" and isinstance(value, str):
            out[key] = {"propertyName": value}
        elif key == "type" and value == "file":
            out["type"], out["format"] = "string", "binary"
        elif key in ("properties", "definitions") and isinstance(value, dict):
            out[key] = {name: schema(child) for name, child in value.items()}
        elif key in ("items", "additionalProperties") and isinstance(value, dict):
            out[key] = schema(value)
        elif key in ("allOf", "anyOf", "oneOf") and isinstance(value, list):
            out[key] = [schema(child) for child in value]
        else:
            out[key] = value
    return out


def parameter_schema(param):
    """The schema of a non-body parameter: its type, format, items and constraints move under `schema`."""
    out = {k: copy.deepcopy(param[k]) for k in SCHEMA_FIELDS if k in param and k not in ("description", "title")}
    if param.get("type") == "file":
        out["type"], out["format"] = "string", "binary"
    if isinstance(param.get("items"), dict):
        out["items"] = parameter_schema(param["items"])
    return out


def convert_parameter(param, consumes, body_holder, form_holder):
    """A non-body parameter as a 3.0 one, or None when it moved into the request body (body / formData)."""
    if not isinstance(param, dict) or "$ref" in param:
        return param
    location = param.get("in")
    if location == "body":
        body_holder.append(param)
        return None
    if location == "formData":
        form_holder.append(param)
        return None
    out = {k: param[k] for k in ("name", "in", "description", "required", "allowEmptyValue", "deprecated") if k in param}
    out["schema"] = parameter_schema(param)
    fmt = param.get("collectionFormat")
    if param.get("type") == "array" and fmt:
        if fmt == "csv":
            out["style"], out["explode"] = ("simple", False) if location in ("path", "header") else ("form", False)
        elif fmt == "multi" and location == "query":
            out["style"], out["explode"] = "form", True
        elif fmt == "ssv" and location == "query":
            out["style"], out["explode"] = "spaceDelimited", False
        elif fmt == "pipes" and location == "query":
            out["style"], out["explode"] = "pipeDelimited", False
        # tsv has no 3.0 equivalent: the style is left out and the tools service reports the serialization for that operation
    return out


def request_body(bodies, forms, consumes):
    if bodies:
        body = bodies[0]
        types = [t for t in consumes if t not in FORM_TYPES] or ["application/json"]
        out = {"content": {t: {"schema": schema(body.get("schema", {}))} for t in types}}
        if "description" in body:
            out["description"] = body["description"]
        if body.get("required"):
            out["required"] = True
        return out
    if forms:
        properties, required = {}, []
        for field in forms:
            properties[field["name"]] = dict(parameter_schema(field), **({"description": field["description"]} if "description" in field else {}))
            if field.get("required"):
                required.append(field["name"])
        form_schema = {"type": "object", "properties": properties}
        if required:
            form_schema["required"] = required
        types = [t for t in consumes if t in FORM_TYPES] or ["application/x-www-form-urlencoded"]
        out = {"content": {t: {"schema": copy.deepcopy(form_schema)} for t in types}}
        if required:
            out["required"] = True
        return out
    return None


def convert_response(response, produces):
    if not isinstance(response, dict) or "$ref" in response:
        return response
    out = {"description": response.get("description", "")}
    if "schema" in response:
        out["content"] = {t: {"schema": schema(response["schema"])} for t in produces or ["application/json"]}
    if isinstance(response.get("headers"), dict):
        out["headers"] = {name: {"description": h.get("description", ""), "schema": parameter_schema(h)} for name, h in response["headers"].items() if isinstance(h, dict)}
    return out


def convert_operation(op, path_params, consumes, produces):
    if not isinstance(op, dict):
        return op
    consumes = op.get("consumes") or consumes
    produces = op.get("produces") or produces
    bodies, forms, parameters = [], [], []
    for param in list(path_params) + list(op.get("parameters") or []):
        converted = convert_parameter(param, consumes, bodies, forms)
        if converted is not None:
            parameters.append(converted)
    out = {k: copy.deepcopy(v) for k, v in op.items() if k not in ("parameters", "consumes", "produces", "responses", "schemes")}
    if parameters:
        out["parameters"] = parameters
    body = request_body(bodies[-1:], forms, consumes)
    if body:
        out["requestBody"] = body
    out["responses"] = {str(code): convert_response(r, produces) for code, r in (op.get("responses") or {}).items()}
    return out


def security_schemes(definitions):
    out = {}
    for name, scheme in (definitions or {}).items():
        if not isinstance(scheme, dict):
            continue
        kind = scheme.get("type")
        if kind == "basic":
            out[name] = {"type": "http", "scheme": "basic"}
        elif kind == "apiKey":
            out[name] = {k: scheme[k] for k in ("type", "name", "in") if k in scheme}
        elif kind == "oauth2":
            flow = FLOWS.get(scheme.get("flow"), "")
            entry = {k: scheme[k] for k in ("authorizationUrl", "tokenUrl") if k in scheme}
            entry["scopes"] = scheme.get("scopes") or {}
            out[name] = {"type": "oauth2", "flows": {flow: entry} if flow else {}}
        else:
            out[name] = scheme
        if "description" in scheme:
            out[name]["description"] = scheme["description"]
    return out


def servers(document):
    host, base = document.get("host"), document.get("basePath") or ""
    if not host:
        return [{"url": base or "/"}]
    schemes = document.get("schemes") or ["https"]
    return [{"url": f"{scheme}://{host}{base}"} for scheme in schemes]


def convert(document):
    """The OpenAPI 3.0.3 equivalent of a Swagger 2.0 document (a new object; the input is not changed)."""
    source = rewrite_refs(copy.deepcopy(document))
    consumes, produces = source.get("consumes") or ["application/json"], source.get("produces") or ["application/json"]
    out = {"openapi": "3.0.3", "info": source.get("info") or {"title": "API", "version": "1"}, "servers": servers(source), "paths": {}}
    for key in ("security", "tags", "externalDocs"):
        if key in source:
            out[key] = source[key]
    components = {}
    if isinstance(source.get("definitions"), dict):
        components["schemas"] = {name: schema(node) for name, node in source["definitions"].items()}
    if isinstance(source.get("parameters"), dict):
        parameters = {}
        for name, param in source["parameters"].items():
            if isinstance(param, dict) and param.get("in") not in ("body", "formData"):
                parameters[name] = convert_parameter(param, consumes, [], [])
        if parameters:
            components["parameters"] = parameters
    if isinstance(source.get("responses"), dict):
        components["responses"] = {name: convert_response(r, produces) for name, r in source["responses"].items()}
    if source.get("securityDefinitions"):
        components["securitySchemes"] = security_schemes(source["securityDefinitions"])
    if components:
        out["components"] = components
    for route, item in (source.get("paths") or {}).items():
        if not isinstance(item, dict):
            out["paths"][route] = item
            continue
        shared = [p for p in item.get("parameters") or []]
        converted = {key: value for key, value in item.items() if key not in METHODS and key != "parameters"}
        for method in METHODS:
            if method in item:
                converted[method] = convert_operation(item[method], shared, consumes, produces)
        out["paths"][route] = converted
    return out
