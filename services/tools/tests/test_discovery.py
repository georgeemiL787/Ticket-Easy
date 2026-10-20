import copy
import json
from pathlib import Path
import pytest
from conftest import ROOT
from team_c.discovery import discover


def source():
    return json.loads((ROOT/"examples/ecommerce.json").read_text())


def inspect(doc):
    return discover(json.dumps(doc).encode(),"test.json","business")[1]


@pytest.mark.parametrize("filename",["ecommerce.json","room-booking.yaml"])
def test_generic_inventory_and_required_refs(filename):
    raw=(ROOT/"examples"/filename).read_bytes()
    doc,inv=discover(raw,filename,"b")
    assert inv["valid"],inv["diagnostics"]
    assert len(inv["operations"])==2
    assert all(op["source_pointer"].startswith("#/paths/") for op in inv["operations"])
    assert inv["operations"][0]["inputs"][next(iter(inv["operations"][0]["inputs"]))]["required"]
    assert inv["operations"][0]["original"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    assert discover(raw,filename,"b")[1]["operations"][0]["id"]==inv["operations"][0]["id"]
    assert inv["operations"][0]["security"] and inv["operations"][0]["access_status"]=="unverified"


@pytest.mark.parametrize("raw,name",[(b'{"openapi":',"x.json"),(b'{"a":1,"a":2}',"x.json"),(b'a: 1\na: 2',"x.yaml"),(b'!!python/object/apply:os.system [echo nope]',"x.yaml"),(b'a: &x [*x]',"x.yaml")])
def test_malformed(raw,name):
    assert not discover(raw,name,"b")[1]["valid"]


@pytest.mark.parametrize("ref",["https://example.invalid/schema.json","file:///etc/passwd","#/components/schemas/Missing"])
def test_bad_references_never_fetch(ref,monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request,"urlopen",lambda *a,**k:pytest.fail("network forbidden"))
    doc=source()
    doc["paths"]["/orders/{order_id}"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]={"$ref":ref}
    assert not inspect(doc)["valid"]


def test_recursive_reference_and_unsupported_version():
    doc=source()
    doc["components"]["schemas"]["Order"]["properties"]["child"]={"$ref":"#/components/schemas/Order"}
    assert "reference_resolution" in {d["code"] for d in inspect(doc)["diagnostics"]}
    doc=source();doc["openapi"]="3.1.0"
    assert inspect(doc)["openapi_family"]=="3.1"
    doc=source();doc["openapi"]="3.2.0"
    assert not inspect(doc)["valid"] and doc["openapi"]=="3.2.0"


@pytest.mark.parametrize("version",[None,3,["3.0.3"],{"version":"3.0.3"}])
def test_malformed_version_is_reported_without_crashing(version):
    doc=source();doc["openapi"]=version
    result=inspect(doc)
    assert not result["valid"]
    assert "unsupported_version" in {d["code"] for d in result["diagnostics"]}


def test_overrides_security_and_media():
    doc=source();item=doc["paths"]["/orders/{order_id}"]
    item["parameters"]=[{"name":"order_id","in":"path","required":True,"schema":{"type":"integer"}}]
    item["get"]["security"]=[]
    item["get"]["responses"]["200"]["content"]["application/xml"]={"schema":{"type":"string"}}
    inv=inspect(doc)
    assert inv["valid"],inv["diagnostics"]
    assert inv["operations"][0]["inputs"]["path.order_id"]["schema"]["type"]=="string"
    assert inv["operations"][0]["security"]==[]
    assert any(d["code"]=="alternative_media" for d in inv["diagnostics"])


@pytest.mark.parametrize("mutation,blocked",[("path_required",{"GET /orders/{order_id}"}),("security",{"GET /orders/{order_id}","POST /tickets"}),("composition",{"GET /orders/{order_id}"}),("binary",{"POST /tickets"}),("keyword",{"GET /orders/{order_id}"})])
def test_unsupported_and_invalid_block_only_affected_operations(mutation,blocked):
    doc=source()
    if mutation=="path_required":doc["paths"]["/orders/{order_id}"]["get"]["parameters"][0]["required"]=False
    if mutation=="security":doc["security"]=[{"missing":[]}]
    if mutation=="composition":doc["components"]["schemas"]["Order"]["allOf"]=[{"type":"object"}]
    if mutation=="keyword":doc["components"]["schemas"]["Order"]["const"]="bad"
    if mutation=="binary":doc["paths"]["/tickets"]["post"]["requestBody"]["content"]={"application/octet-stream":{"schema":{"type":"string","format":"binary"}}}
    inv=inspect(doc)
    assert len(inv["operations"])==2 and inv["document_valid"],inv["diagnostics"]
    assert {f'{o["method"]} {o["path"]}' for o in inv["operations"] if not o["supported"]}==blocked
    assert not any(o["proposal_eligible"] for o in inv["operations"] if not o["supported"])
    assert inv["valid"]==(len(blocked)<2)


def test_representative_fixture():
    fixture=ROOT/"tests/fixtures/petstore-original.json"
    assert fixture.exists()
    inv=discover(fixture.read_bytes(),fixture.name,"representative")[1]
    assert inv["openapi_version"]=="3.0.4"
    assert len(inv["operations"])==19
    assert inv["valid"]
    assert [o["path"] for o in inv["operations"] if not o["supported"]]==["/pet/{petId}/uploadImage"]
    assert any(d["code"]=="unsupported_media" and "uploadImage" in d["operation"] for d in inv["diagnostics"])
    restricted=[o for o in inv["operations"] if o["exposure"]["classification"]=="restricted"]
    assert {"/user/login","/user/logout","/user"}<={o["path"] for o in restricted} and not any(o["proposal_eligible"] for o in restricted)
    assert not any(d["code"]=="invalid_openapi" for d in inv["diagnostics"]),inv["diagnostics"]
    subset=ROOT/"tests/fixtures/petstore-json-subset.json"
    inv=discover(subset.read_bytes(),subset.name,"representative")[1]
    assert inv["valid"],inv["diagnostics"]
