import dataclasses

import pytest
from fastapi.testclient import TestClient

from team_a import config, service

client = TestClient(service.app)
ADMIN = {"X-Admin-Key": "test-admin-key"}


@pytest.fixture(autouse=True)
def admin_key(monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, admin_api_key=ADMIN["X-Admin-Key"]))

REFUND_DAY_20 = {
    "request_id": "req-1",
    "tenant_id": "shop_001",
    "conversation_id": "conv-1",
    "as_of": "2026-09-28",
    "tool": {"name": "create_refund", "operation_kind": "create", "risk": "high"},
    "facts": {"order_status": "delivered", "delivered_at": "2026-09-08"},
    "arguments": {"amount": 800},
    "identity": {"verified": True, "customer_id": "C-1"},
}


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_check_action_endpoint():
    body = client.post("/v1/policy/check-action", json=REFUND_DAY_20).json()
    assert body["decision"] == "deny" and body["citations"] == ["refund_policy@v1#s1"]
    assert body["conversation_id"] == "conv-1" and body["schema_version"] == "1.0"


def test_classify_risk_endpoint():
    body = client.post("/v1/policy/classify-risk", json={
        "request_id": "r", "tenant_id": "shop_001", "message": "عايز تعويض", "use_llm": False,
    }).json()
    assert body["categories"] == ["compensation_demand"] and body["mandatory_escalation"]


def test_explain_and_list_rules():
    assert client.get("/v1/policy/rules/R-RETURN-14D/explain", params={"tenant_id": "shop_001"}).json()["active"]
    proposed = client.get(
        "/v1/policy/rules", params={"tenant_id": "shop_001", "status": "proposed"}, headers=ADMIN
    ).json()
    # The live queue also holds whatever `rules extract` proposed; only check the seed entry and status filter.
    assert "R-DEFECT-48H" in [r["rule_id"] for r in proposed]
    assert all(r["approval_status"] == "proposed" for r in proposed)


def test_errors_use_stable_codes():
    bad = client.post("/v1/policy/check-action", json={**REFUND_DAY_20, "tenant_id": "../etc"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_REQUEST"

    missing = client.get("/v1/policy/rules/NOPE/explain", params={"tenant_id": "shop_001"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"

    unknown = client.post("/v1/knowledge/search", json={"request_id": "r", "tenant_id": "shop_404", "query": "x"})
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "TENANT_NOT_FOUND"


@pytest.mark.parametrize("headers", [{}, {"X-Admin-Key": "wrong"}])
@pytest.mark.parametrize("method,path,body", [
    ("get", "/v1/policy/rules?tenant_id=shop_001", None),
    ("post", "/v1/policy/rules/R-DEFECT-48H/approve", {"tenant_id": "shop_001", "reviewer": "x"}),
    ("post", "/v1/policy/rules/R-DEFECT-48H/reject", {"tenant_id": "shop_001", "reviewer": "x"}),
    ("patch", "/v1/policy/rules/R-DEFECT-48H", {"tenant_id": "shop_001", "changes": {}}),
    ("post", "/v1/admin/reload", None),
    ("post", "/v1/knowledge/resolutions", {
        "request_id": "r", "tenant_id": "shop_001", "category": "refund_exception",
        "redacted_summary": "Refund requested on day 16.", "resolution": "Declined.",
        "escalation_reason": "policy_deny", "risk_categories": []}),
    ("post", "/v1/knowledge/resolutions/search",
     {"request_id": "r", "tenant_id": "shop_001", "query": "refund", "risk_categories": []}),
])
def test_admin_endpoints_reject_missing_or_wrong_key(headers, method, path, body):
    resp = client.request(method, path, json=body, headers=headers)
    assert resp.status_code == 401 and resp.json()["error"]["code"] == "UNAUTHORIZED"


def test_admin_endpoints_fail_closed_when_key_unset(monkeypatch):
    monkeypatch.setattr(config, "settings", dataclasses.replace(config.settings, admin_api_key=""))
    resp = client.get("/v1/policy/rules", params={"tenant_id": "shop_001"}, headers={"X-Admin-Key": ""})
    assert resp.status_code == 401


def test_admin_endpoint_accepts_valid_key(monkeypatch, rule_store):
    monkeypatch.setattr(service, "_store", lambda _tenant: rule_store)
    resp = client.post(
        "/v1/policy/rules/R-DEFECT-48H/approve", json={"tenant_id": "shop_001", "reviewer": "ops"}, headers=ADMIN
    )
    assert resp.status_code == 200 and resp.json()["approval_status"] == "approved"


def test_approve_endpoint_refuses_rule_that_breaks_guardrails(monkeypatch, rule_store):
    monkeypatch.setattr(service, "_store", lambda _tenant: rule_store)
    block_all = rule_store.get("R-RETURN-14D").model_copy(update={
        "rule_id": "P-BLOCK-RETURNS", "conditions": [], "effect": "deny",
        "approval_status": "proposed", "approved_by": None, "approved_at": None,
    })
    rule_store.add_proposed([block_all])
    resp = client.post(
        "/v1/policy/rules/P-BLOCK-RETURNS/approve", json={"tenant_id": "shop_001", "reviewer": "ops"}, headers=ADMIN
    )
    assert resp.status_code == 422 and "guardrail" in resp.json()["error"]["message"]
    assert rule_store.get("P-BLOCK-RETURNS").approval_status == "proposed"


def test_agent_endpoints_need_no_admin_key():
    assert client.post("/v1/policy/check-action", json=REFUND_DAY_20).status_code == 200
    assert client.get("/v1/policy/rules/R-RETURN-14D/explain", params={"tenant_id": "shop_001"}).status_code == 200
