import json
from pathlib import Path

from team_c.classify import operation_class, operation_row
from team_c.discovery import discover

SPECS = Path(__file__).parent / "fixtures" / "specs"


def spec(operations):
    paths = {}
    for route, method, op_id, extra in operations:
        paths.setdefault(route, {})[method] = {
            "operationId": op_id,
            "security": [{"key": []}],
            "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object", "properties": {"id": {"type": "string"}}}}}}},
            **extra,
        }
    document = {"openapi": "3.0.3", "info": {"title": "T", "version": "1"}, "paths": paths,
                "components": {"securitySchemes": {"key": {"type": "apiKey", "name": "X-Key", "in": "header"}}}}
    return discover(json.dumps(document).encode(), "t.json", "biz")[1]


def body(*names):
    return {"requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "required": list(names), "properties": {n: {"type": "string"} for n in names}}}}}}


def classes(inventory):
    return {f'{o["method"]} {o["path"]}': operation_class(o) for o in inventory["operations"]}


def test_every_operation_gets_one_class():
    found = classes(spec([
        ("/orders/{id}", "get", "getOrder", {"parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}]}),
        ("/tickets", "post", "createTicket", body("subject")),
        ("/refunds", "post", "createRefund", body("order_id", "amount")),
        ("/orders/{id}/cancel", "post", "cancelOrder", {"parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}]}),
        ("/items/{id}", "delete", "deleteItem", {"parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}]}),
        ("/auth/login", "post", "login", body("username", "password")),
    ]))
    assert {k: v["class"] for k, v in found.items()} == {
        "GET /orders/{id}": "read", "POST /tickets": "write", "POST /refunds": "risky", "POST /orders/{id}/cancel": "risky",
        "DELETE /items/{id}": "risky", "POST /auth/login": "restricted",
    }
    assert found["POST /refunds"]["suggested_effects"]["financial"] is True
    assert found["POST /orders/{id}/cancel"]["suggested_effects"]["irreversible"] is True
    assert found["GET /orders/{id}"]["suggested_effects"] == dict(financial=False, irreversible=False, external_side_effects=False, idempotent=True)
    assert found["POST /tickets"]["suggested_effects"]["external_side_effects"] is True and found["POST /tickets"]["selectable"]
    assert not found["POST /auth/login"]["selectable"]


def test_an_unusable_operation_says_why_and_stays_listed():
    inventory = discover((SPECS / "swagger2_shop.json").read_bytes(), "swagger2_shop.json", "biz")[1]
    rows = {r["path"]: r for r in map(operation_row, inventory["operations"])}
    assert len(rows) == 3
    assert rows["/uploads"]["selectable"] is False and "file upload" in rows["/uploads"]["reason_unusable"]
    assert rows["/orders/{order_id}"]["selectable"] and rows["/orders/{order_id}"]["class"] == "read"
    assert set(rows["/orders/{order_id}"]) >= {"id", "method", "path", "title", "summary", "tags", "class", "selectable", "why"}
