"""Contract tests for the Nile sandbox's /_fixtures routes, the provider Team C's automatic tests use for that shop."""
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from ops.local.nile_sandbox.app import create_app  # noqa: E402

FX = {"x-fixture-key": "fixture-test-key"}


@pytest.fixture
def shop():
    with TestClient(create_app(harness_key="harness-test-key", fixture_key="fixture-test-key")) as c:
        yield c


def bearer(account):
    return {"Authorization": "Bearer " + account["credential"]["token"]}


def test_fixture_routes_need_the_fixture_key_and_are_not_in_the_openapi_document(shop):
    assert shop.get("/_fixtures/kinds").status_code == 403
    assert shop.get("/_fixtures/kinds", headers={"x-fixture-key": "harness-test-key"}).status_code == 403
    assert not [p for p in shop.get("/openapi.json").json()["paths"] if p.startswith("/_fixtures")]
    kinds = shop.get("/_fixtures/kinds", headers=FX).json()
    assert kinds[0]["kind"] == "order" and kinds[0]["selects_by"] == ["order_id"] and "delivered" in kinds[0]["states"]


def test_accounts_and_records_in_a_requested_state_are_owned_and_isolated(shop):
    a = shop.post("/_fixtures/accounts", json=dict(label="a", run_id="r1"), headers=FX).json()
    b = shop.post("/_fixtures/accounts", json=dict(label="b", run_id="r1"), headers=FX).json()
    assert a["context"] == {"customer_id": a["account_id"]} and a["credential"]["token"] != b["credential"]["token"]
    pending = shop.post("/_fixtures/records", json=dict(account_id=a["account_id"], kind="order", state="pending", run_id="r1"), headers=FX).json()
    delivered = shop.post("/_fixtures/records", json=dict(account_id=b["account_id"], kind="order", state="delivered", run_id="r1"), headers=FX).json()
    assert (pending["state"], delivered["state"]) == ("pending", "delivered")
    mine = shop.get(f"/api/orders/{pending['record_id']}", headers=bearer(a)).json()
    assert mine["order_status"] == "pending" and mine["delivered_at"] is None and mine["customer_id"] == a["account_id"]
    assert shop.get(f"/api/orders/{pending['record_id']}", headers=bearer(b)).status_code == 403
    assert [o["order_id"] for o in shop.get("/api/orders", headers=bearer(a)).json()["orders"]] == [pending["record_id"]]
    # the state decides what the shop accepts: a pending order cannot be returned, a delivered one can
    assert shop.post("/api/returns", json=dict(order_id=pending["record_id"], reason="x"), headers=bearer(a)).status_code == 409
    assert shop.post("/api/returns", json=dict(order_id=delivered["record_id"], reason="x"), headers=bearer(b)).status_code == 201
    state = shop.get("/_fixtures/state", params=dict(run_id="r1"), headers=FX).json()
    assert len(state["records"]) == 2 and [c["kind"] for c in state["changes"]] == ["return"]
    bad = shop.post("/_fixtures/records", json=dict(account_id=a["account_id"], kind="order", state="exploded"), headers=FX)
    assert bad.status_code == 422
    assert shop.post("/_fixtures/records", json=dict(account_id="C-100", kind="order"), headers=FX).status_code == 404  # only fixture accounts


def test_revoke_and_reset(shop):
    a = shop.post("/_fixtures/accounts", json=dict(label="a", run_id="r2"), headers=FX).json()
    rec = shop.post("/_fixtures/records", json=dict(account_id=a["account_id"], kind="order", run_id="r2"), headers=FX).json()
    keep = shop.post("/_fixtures/records", json=dict(account_id=a["account_id"], kind="order", run_id="r2"), headers=FX).json()
    assert shop.post("/_fixtures/revoke", json=dict(account_id=a["account_id"]), headers=FX).json()["revoked"]
    assert shop.get(f"/api/orders/{rec['record_id']}", headers=bearer(a)).status_code == 401
    assert shop.post("/_fixtures/revoke", json=dict(account_id="C-100"), headers=FX).status_code == 404
    one = shop.post("/_fixtures/reset", json=dict(run_id="r2", record_ids=[rec["record_id"]]), headers=FX).json()
    assert one["orders_removed"] == 1 and one["accounts_removed"] == 0
    everything = shop.post("/_fixtures/reset", json=dict(run_id="r2"), headers=FX).json()
    assert everything == dict(run_id="r2", orders_removed=1, accounts_removed=1)
    assert shop.get("/_fixtures/state", params=dict(run_id="r2"), headers=FX).json()["records"] == []
    # the shop's own demo data is untouched
    token = shop.post("/_harness/tokens", json=dict(customer_id="C-100"), headers={"x-harness-key": "harness-test-key"}).json()["token"]
    assert shop.get("/api/orders/NS-20877", headers={"Authorization": "Bearer " + token}).status_code == 200


def test_a_service_token_acts_for_any_customer_and_nothing_in_the_api_separates_them():
    with TestClient(create_app(harness_key="harness-test-key", fixture_key="fixture-test-key", service_token="service-token-0123456789")) as shop:
        service = {"Authorization": "Bearer service-token-0123456789"}
        # an ordinary customer is still held to their own orders
        token = shop.post("/_harness/tokens", json=dict(customer_id="C-101"), headers={"x-harness-key": "harness-test-key"}).json()["token"]
        assert shop.get("/api/orders/NS-20877", headers={"Authorization": "Bearer " + token}).status_code == 403
        # the service credential is not: C-100's order is readable, and a ticket is recorded for C-100, the order's customer
        assert shop.get("/api/orders/NS-20877", headers=service).json()["customer_id"] == "C-100"
        made = shop.post("/api/tickets", json=dict(order_id="NS-20877", subject="s", description="d"), headers=service)
        assert made.status_code == 201
        tickets = shop.get("/_harness/state", headers={"x-harness-key": "harness-test-key"}).json()["tickets"]
        assert tickets[-1]["customer_id"] == "C-100"
        assert shop.get("/api/orders", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert len(shop.get("/api/orders", headers=service).json()["orders"]) > 5  # a list is not separated either: Team C refuses such tools
    with TestClient(create_app(harness_key="harness-test-key")) as plain:
        assert plain.get("/api/orders/NS-20877", headers={"Authorization": "Bearer service-token-0123456789"}).status_code == 401
