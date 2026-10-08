"""Repository reads over the SQLite data layer (keyword-only build; no Ollama needed)."""

import dataclasses
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from team_a import config, db, service
from team_a.config import settings
from team_a.db import repository as repo
from team_a.knowledge.index import TenantNotFound
from team_a.knowledge.retrieval import get_passage, search_knowledge, search_past_tickets
from team_a.schemas import RetrievalResult, SearchKnowledgeRequest, SearchPastTicketsRequest


def _benchmark_questions() -> list[dict]:
    return [json.loads(line) for split in ("dev", "test")
            for line in (settings.data_dir / "benchmark" / f"retrieval_shop_001.{split}.jsonl")
            .read_text(encoding="utf-8").splitlines() if line.strip()]


# ------------------------------------------------------------------ structured lookups

def test_get_customer(built_db):
    c = repo.get_customer("noon_eg", "C-1001", db_path=built_db)
    assert c["name"] == "Nour Hassan" and c["verified"] is True and c["tenant_id"] == "noon_eg"
    assert repo.get_customer("noon_eg", "C-1011", db_path=built_db)["verified"] is False
    assert repo.get_customer("shop_001", "C-100", db_path=built_db)["verified"] is None  # no stored flag
    assert repo.get_customer("noon_eg", "C-9999", db_path=built_db) is None


def test_list_orders_for_customer(built_db):
    orders = repo.list_orders_for_customer("shop_001", "C-100", db_path=built_db)
    assert [o["order_id"] for o in orders] == ["NS-20512", "NS-20745", "NS-20877"]  # by placed_at
    assert repo.list_orders_for_customer("noon_eg", "C-9999", db_path=built_db) == []


def test_get_order_includes_items_in_line_order(built_db):
    order = repo.get_order("noon_eg", "ORD-30032", db_path=built_db)
    assert [i["item_id"] for i in order["items"]] == ["ORD-30032-I1", "ORD-30032-I2"]
    assert order["order_total"] == sum(i["qty"] * i["unit_price"] for i in order["items"]) + order["shipping_fee"]
    shop = repo.get_order("shop_001", "NS-20877", db_path=built_db)
    assert shop["delivery_address"]["city"] == "Cairo" and shop["items"][0]["item_id"] == "NS-20877-I1"
    assert repo.get_order("noon_eg", "ORD-00000", db_path=built_db) is None


def test_get_return(built_db):
    r = repo.get_return("noon_eg", "RET-5005", db_path=built_db)
    assert r["return_status"] == "rejected_in_hub" and r["delivery_attempts_failed"] == 2
    assert repo.get_return("noon_eg", "RET-0000", db_path=built_db) is None


def test_unknown_tenant_raises(built_db):
    with pytest.raises(TenantNotFound):
        repo.get_customer("nobody", "C-1001", db_path=built_db)


def test_missing_database_says_how_to_build(tmp_path):
    with pytest.raises(db.DatabaseNotBuilt, match="db build"):
        repo.get_customer("noon_eg", "C-1001", db_path=tmp_path / "none.sqlite")


# ------------------------------------------------------------------ check_action facts

def test_facts_match_the_scenario_shape(built_db):
    facts = repo.build_check_action_facts("noon_eg", "ORD-30001", "ORD-30001-I1", db_path=built_db)
    assert facts == {
        "order_status": "delivered", "delivered_at": "2026-10-03", "expected_delivery_date": "2026-10-03",
        "product_category": "electronics", "product_subcategory": "smartphone", "is_clearance": False,
        "item_condition": "sealed", "order_section": "noon", "return_eligible_tag": True,
        "warranty_tag": False, "payment_method": "credit_card",
    }


def test_derived_facts_use_the_tenant_as_of_not_the_clock(built_db):
    facts = repo.build_check_action_facts("noon_eg", "ORD-30001", "ORD-30001-I1", include_derived=True,
                                          db_path=built_db)
    assert facts["days_since_delivery"] == 5  # 2026-10-03 -> as_of 2026-10-08
    later = repo.build_check_action_facts("noon_eg", "ORD-30001", "ORD-30001-I1", as_of=date(2026, 10, 20),
                                          include_derived=True, db_path=built_db)
    assert later["days_since_delivery"] == 17
    assert repo.tenant_as_of("shop_001", db_path=built_db) == date(2026, 9, 28)


def test_facts_for_an_item_on_another_order_are_refused(built_db):
    assert repo.build_check_action_facts("noon_eg", "ORD-30001", "ORD-30002-I1", db_path=built_db) is None


def test_shop_facts_leave_out_fields_the_tenant_does_not_have(built_db):
    facts = repo.build_check_action_facts("shop_001", "NS-20877", "NS-20877-I1", db_path=built_db)
    assert facts == {"order_status": "shipped", "delivered_at": None, "expected_delivery_date": "2026-09-24",
                     "product_category": "clothing", "is_clearance": False, "item_condition": "unused",
                     "payment_method": "card"}


# ------------------------------------------------------------------ knowledge: same results as the file index

def test_db_search_equals_file_index_search_on_the_whole_benchmark(built_db, keyword_index):
    for q in _benchmark_questions():
        req = SearchKnowledgeRequest(request_id=q["id"], tenant_id="shop_001", query=q["question"])
        from_db = repo.search_knowledge(req, embedder=None, db_path=built_db)
        from_files = search_knowledge(req, keyword_index, embedder=None)
        assert from_db.model_dump() == from_files.model_dump(), q["id"]


def test_db_ticket_search_equals_file_index(built_db, keyword_index):
    for query in ("3ayez a8ayar el 3onwan", "الاوردر متأخر", "refund to my card", "bitcoin price"):
        req = SearchPastTicketsRequest(request_id="t", tenant_id="shop_001", query=query)
        assert (repo.search_past_tickets(req, None, db_path=built_db).model_dump()
                == search_past_tickets(req, keyword_index, None).model_dump())


def test_get_passage(built_db, keyword_index):
    assert repo.get_passage("shop_001", "return_policy@v2#s2", built_db) == get_passage(keyword_index,
                                                                                        "return_policy@v2#s2")
    assert repo.get_passage("noon_eg", "noon_return_policy@v1#s28", built_db).section == "Refunds: credit card EMI"
    assert repo.get_passage("noon_eg", "return_policy@v2#s2", built_db) is None


def test_noon_search_keeps_the_explicit_empty_contract(built_db):
    req = SearchKnowledgeRequest(request_id="t", tenant_id="noon_eg", query="What is the price of bitcoin today?")
    result = repo.search_knowledge(req, embedder=None, db_path=built_db)
    assert isinstance(result, RetrievalResult)
    assert result.passages == [] and result.empty_reason == "below_threshold"
    assert result.retrieval_mode == "keyword_only"
    tickets = repo.search_past_tickets(SearchPastTicketsRequest(request_id="t", tenant_id="noon_eg",
                                                                query="What is the price of bitcoin today?"),
                                       None, db_path=built_db)
    assert tickets.tickets == [] and tickets.empty_reason == "below_threshold"
    found = repo.search_past_tickets(SearchPastTicketsRequest(request_id="t", tenant_id="noon_eg",
                                                              query="Tabby refund still not received"),
                                     None, db_path=built_db)
    assert found.tickets and found.tickets[0].ticket_id == "NT-2010"


# ------------------------------------------------------------------ HTTP (read-only, admin only)

ADMIN = {"X-Admin-Key": "test-admin-key"}


@pytest.fixture
def client(built_db, monkeypatch):
    patched = dataclasses.replace(config.settings, admin_api_key=ADMIN["X-Admin-Key"], db_path=built_db)
    monkeypatch.setattr(config, "settings", patched)
    monkeypatch.setattr(db, "settings", patched)
    monkeypatch.setattr(repo, "settings", patched)
    return TestClient(service.app)


@pytest.mark.parametrize("url", [
    "/v1/data/customers/C-1001?tenant_id=noon_eg",
    "/v1/data/customers/C-1001/orders?tenant_id=noon_eg",
    "/v1/data/orders/ORD-30001?tenant_id=noon_eg",
    "/v1/data/orders/ORD-30001/items/ORD-30001-I1/facts?tenant_id=noon_eg",
    "/v1/data/returns/RET-5001?tenant_id=noon_eg",
])
def test_data_endpoints_require_the_admin_key(client, url):
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"X-Admin-Key": "wrong"}).status_code == 401
    assert client.get(url, headers=ADMIN).status_code == 200


def test_data_endpoints(client):
    order = client.get("/v1/data/orders/ORD-30032?tenant_id=noon_eg", headers=ADMIN).json()
    assert len(order["items"]) == 2
    facts = client.get("/v1/data/orders/ORD-30001/items/ORD-30001-I1/facts?tenant_id=noon_eg&include_derived=true",
                       headers=ADMIN).json()
    assert facts["days_since_delivery"] == 5
    orders = client.get("/v1/data/customers/C-1001/orders?tenant_id=noon_eg", headers=ADMIN).json()
    assert orders["customer_id"] == "C-1001" and orders["orders"]


@pytest.mark.parametrize("url,code", [
    ("/v1/data/orders/ORD-99999?tenant_id=noon_eg", "NOT_FOUND"),
    ("/v1/data/customers/C-1001?tenant_id=nobody", "TENANT_NOT_FOUND"),
    ("/v1/data/customers/C-9999/orders?tenant_id=noon_eg", "NOT_FOUND"),
])
def test_data_endpoint_errors_use_the_error_contract(client, url, code):
    resp = client.get(url, headers=ADMIN)
    assert resp.status_code == 404 and resp.json()["error"]["code"] == code


def test_no_write_endpoints_on_the_data_layer():
    data_routes = [r for r in service.app.routes if getattr(r, "path", "").startswith("/v1/data/")]
    assert data_routes and all(r.methods == {"GET"} for r in data_routes)
