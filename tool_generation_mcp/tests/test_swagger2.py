import json
import time
from pathlib import Path

from team_c.discovery import discover
from team_c.swagger2 import convert, is_swagger2

SPECS = Path(__file__).parent / "fixtures" / "specs"


def shop():
    return json.loads((SPECS / "swagger2_shop.json").read_text(encoding="utf-8"))


def test_the_conversion_gives_servers_components_and_security_schemes():
    out = convert(shop())
    assert is_swagger2(shop()) and out["openapi"] == "3.0.3"
    assert out["servers"] == [{"url": "https://api.shop.example/v1"}]
    assert out["components"]["schemas"]["Order"]["properties"]["status"] == {"type": "string", "nullable": True}
    assert out["components"]["securitySchemes"] == {"key": {"type": "apiKey", "name": "X-Api-Key", "in": "header"}, "basic": {"type": "http", "scheme": "basic"}}
    get = out["paths"]["/orders/{order_id}"]["get"]
    assert [p["name"] for p in get["parameters"]] == ["order_id", "expand"]
    assert get["parameters"][1]["style"] == "form" and get["parameters"][1]["explode"] is True
    assert get["responses"]["200"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/Order"}
    post = out["paths"]["/tickets"]["post"]
    assert post["requestBody"]["required"] is True
    assert post["requestBody"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/NewTicket"}


def test_the_input_document_is_not_changed():
    before = shop()
    convert(before)
    assert before == shop()


def test_discovery_reads_a_swagger_2_description_and_lists_what_it_cannot_use():
    document, inventory = discover((SPECS / "swagger2_shop.json").read_bytes(), "swagger2_shop.json", "biz")
    assert inventory["converted_from"] == "swagger_2.0" and inventory["openapi_version"] == "3.0.3"
    assert inventory["document_valid"], inventory["diagnostics"]
    by_id = {op["original"].get("operationId"): op for op in inventory["operations"]}
    assert by_id["getOrder"]["supported"] and by_id["createTicket"]["supported"]
    assert not by_id["upload"]["supported"]  # multipart is not an executable media type here: listed, with its reason
    assert any(d["code"] == "unsupported_media" and d["operation"] == "POST /uploads" for d in inventory["diagnostics"])
    assert any(d["code"] == "converted_from_swagger2" and d["severity"] == "warning" for d in inventory["diagnostics"])
    assert inventory["summary"]["technically_supported"] == 2 and inventory["summary"]["operations"] == 3
    assert document["openapi"] == "3.0.3"


def test_a_malformed_swagger_description_is_reported_not_raised():
    _, inventory = discover(b'{"swagger": "2.0", "paths": 5}', "x.json", "biz")
    assert not inventory["valid"]


def test_a_large_description_is_read_once_in_a_bounded_time():
    paths = {f"/items{i}/{{id}}": {"get": {"operationId": f"get{i}", "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                                          "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object", "properties": {"id": {"type": "string"}}}}}}}}}
             for i in range(2000)}
    raw = json.dumps({"openapi": "3.0.3", "info": {"title": "Big", "version": "1"}, "paths": paths}).encode()
    started = time.monotonic()
    _, inventory = discover(raw, "big.json", "biz", max_operations=5000)
    assert len(inventory["operations"]) == 2000 and inventory["summary"]["technically_supported"] == 2000
    assert time.monotonic() - started < 60
