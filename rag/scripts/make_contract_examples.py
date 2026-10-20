"""Record real success / empty / failure responses for every Team A endpoint into contracts/examples/.

Needs the hybrid index (python -m team_a ingest) and Ollama running.
Run: python scripts/make_contract_examples.py
"""

import dataclasses
import json
from pathlib import Path

from fastapi.testclient import TestClient

from team_a import config
from team_a.knowledge.embeddings import OllamaEmbedder
from team_a.knowledge.index import index_resolutions
from team_a.service import app

OUT = Path(__file__).resolve().parents[1] / "contracts" / "examples"
client = TestClient(app)
EXAMPLE_ADMIN_KEY = "example-admin-key"  # recorded as a placeholder, never a real key
ADMIN = {"X-Admin-Key": EXAMPLE_ADMIN_KEY}

RESOLVED = {
    "request_id": "req-0020",
    "tenant_id": "shop_001",
    "conversation_id": "conv-42",
    "category": "refund_exception",
    "redacted_summary": "Refund requested on day 17 after delivery for an unused jacket; outside the 14-day window.",
    "resolution": "Supervisor declined the exception and offered a voucher for the same value instead.",
    "cited_rule_id": "R-REFUND-14D",
    "tags": ["refund", "outside_window"],
    "escalation_reason": "policy_deny",
    "risk_categories": [],
    "redaction_check": {"customer_names": ["Mona Adel"], "phones": ["01012345678"], "order_ids": ["NS-20877"]},
}

REFUND = {
    "request_id": "req-0001",
    "tenant_id": "shop_001",
    "conversation_id": "conv-42",
    "as_of": "2026-09-28",
    "tool": {"name": "create_refund", "operation_kind": "create", "risk": "high"},
    "facts": {"order_status": "delivered", "delivered_at": "2026-09-08"},
    "arguments": {"order_id": "NS-20877", "amount": 800},
    "identity": {"verified": True, "customer_id": "C-100", "method": "phone+order_id"},
}

EXAMPLES = {
    "search_knowledge": [
        ("success", "post", "/v1/knowledge/search",
         {"request_id": "req-0002", "tenant_id": "shop_001", "conversation_id": "conv-42",
          "query": "momken araga3 el 7aga ba3d 20 yom?", "top_k": 3}),
        ("empty", "post", "/v1/knowledge/search",
         {"request_id": "req-0003", "tenant_id": "shop_001", "query": "Do you sell laptops?"}),
        ("failure", "post", "/v1/knowledge/search",
         {"request_id": "req-0004", "tenant_id": "shop_404", "query": "return policy"}),
    ],
    "get_passage": [
        ("success", "get", "/v1/knowledge/passages", {"tenant_id": "shop_001", "citation": "return_policy@v2#s2"}),
        ("failure", "get", "/v1/knowledge/passages", {"tenant_id": "shop_001", "citation": "return_policy@v9#s2"}),
    ],
    "search_past_tickets": [
        ("success", "post", "/v1/knowledge/past-tickets/search",
         {"request_id": "req-0005", "tenant_id": "shop_001", "query": "3ayez a8ayar el 3onwan"}),
        ("empty", "post", "/v1/knowledge/past-tickets/search",
         {"request_id": "req-0006", "tenant_id": "shop_001", "query": "What is the price of bitcoin?"}),
    ],
    "check_action": [
        ("deny", "post", "/v1/policy/check-action", REFUND),
        ("allow", "post", "/v1/policy/check-action",
         {**REFUND, "request_id": "req-0007", "facts": {"order_status": "delivered", "delivered_at": "2026-09-23"}}),
        ("require_human", "post", "/v1/policy/check-action",
         {**REFUND, "request_id": "req-0008", "facts": {"order_status": "delivered", "delivered_at": "2026-09-23"},
          "arguments": {"order_id": "NS-20877", "amount": 4500}}),
        ("missing_context", "post", "/v1/policy/check-action",
         {**REFUND, "request_id": "req-0009", "arguments": {"order_id": "NS-20877"},
          "facts": {"order_status": "delivered", "delivered_at": "2026-09-23"}}),
        ("failure", "post", "/v1/policy/check-action", {**REFUND, "tool": {"name": "create_refund"}}),
    ],
    "classify_risk": [
        ("flagged", "post", "/v1/policy/classify-risk",
         {"request_id": "req-0010", "tenant_id": "shop_001", "message": "في عملية دفع على بطاقتي معملتهاش",
          "use_llm": False}),
        ("clear", "post", "/v1/policy/classify-risk",
         {"request_id": "req-0011", "tenant_id": "shop_001", "message": "fein el order bta3y?", "use_llm": False}),
    ],
    "explain_rule": [
        ("success", "get", "/v1/policy/rules/R-REFUND-14D/explain", {"tenant_id": "shop_001"}),
        ("failure", "get", "/v1/policy/rules/R-NOPE/explain", {"tenant_id": "shop_001"}),
    ],
    "add_resolution": [
        ("stored", "post", "/v1/knowledge/resolutions", RESOLVED),
        ("rejected_mandatory_risk", "post", "/v1/knowledge/resolutions",
         {**RESOLVED, "request_id": "req-0021", "category": "card_dispute", "escalation_reason": "mandatory_category",
          "risk_categories": ["fraud_suspected"],
          "redacted_summary": "Customer reported a card payment they did not make.",
          "resolution": "Handed to the payments team."}),
        ("rejected_personal_data", "post", "/v1/knowledge/resolutions",
         {**RESOLVED, "request_id": "req-0022",
          "redacted_summary": "Mona Adel asked for a refund on order NS-20877; call back on 01012345678."}),
        ("failure", "post", "/v1/knowledge/resolutions",
         {k: v for k, v in RESOLVED.items() if k != "risk_categories"}),
    ],
    "search_resolutions": [
        ("success", "post", "/v1/knowledge/resolutions/search",
         {"request_id": "req-0023", "tenant_id": "shop_001",
          "query": "customer wants a refund 16 days after delivery, item unused", "risk_categories": []}),
        ("empty", "post", "/v1/knowledge/resolutions/search",
         {"request_id": "req-0024", "tenant_id": "shop_001", "query": "What is the price of bitcoin?",
          "risk_categories": []}),
        ("mandatory_risk", "post", "/v1/knowledge/resolutions/search",
         {"request_id": "req-0025", "tenant_id": "shop_001",
          "query": "customer says there is a payment on the card they did not make",
          "risk_categories": ["fraud_suspected"]}),
    ],
}
NEEDS_ADMIN = {"add_resolution", "search_resolutions"}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    config.settings = dataclasses.replace(config.settings, admin_api_key=EXAMPLE_ADMIN_KEY)
    # The "stored" example really writes a precedent; snapshot the corpus and restore it afterwards.
    corpus = config.settings.resolutions_file("shop_001")
    snapshot = corpus.read_bytes() if corpus.exists() else None
    try:
        record_all()
    finally:
        if snapshot is None:
            corpus.unlink(missing_ok=True)
        else:
            corpus.write_bytes(snapshot)
        index_resolutions("shop_001", OllamaEmbedder(), strict=False)


def record_all() -> None:
    for name, cases in EXAMPLES.items():
        headers = ADMIN if name in NEEDS_ADMIN else {}
        for label, method, path, payload in cases:
            if method == "post":
                resp = client.post(path, json=payload, headers=headers)
                record = {"request": {"method": "POST", "path": path, "body": payload}}
            else:
                resp = client.get(path, params=payload, headers=headers)
                record = {"request": {"method": "GET", "path": path, "query": payload}}
            if headers:
                record["request"]["headers"] = {"X-Admin-Key": "<ADMIN_API_KEY>"}
            record["response"] = {"status": resp.status_code, "body": resp.json()}
            file = OUT / f"{name}.{label}.json"
            file.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"{resp.status_code} {file.name}")


if __name__ == "__main__":
    main()
