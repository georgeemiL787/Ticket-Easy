from fastapi.testclient import TestClient

from team_a import service

client = TestClient(service.app)

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
    proposed = client.get("/v1/policy/rules", params={"tenant_id": "shop_001", "status": "proposed"}).json()
    assert [r["rule_id"] for r in proposed] == ["R-DEFECT-48H"]


def test_errors_use_stable_codes():
    bad = client.post("/v1/policy/check-action", json={**REFUND_DAY_20, "tenant_id": "../etc"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_REQUEST"

    missing = client.get("/v1/policy/rules/NOPE/explain", params={"tenant_id": "shop_001"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"

    unknown = client.post("/v1/knowledge/search", json={"request_id": "r", "tenant_id": "shop_404", "query": "x"})
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "TENANT_NOT_FOUND"
