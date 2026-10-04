"""Record real success / empty / failure responses for every Team A endpoint into contracts/examples/.

Needs the hybrid index (python -m team_a ingest) and Ollama running.
Run: python scripts/make_contract_examples.py
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from team_a.service import app

OUT = Path(__file__).resolve().parents[1] / "contracts" / "examples"
client = TestClient(app)

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
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, cases in EXAMPLES.items():
        for label, method, path, payload in cases:
            if method == "post":
                resp = client.post(path, json=payload)
                record = {"request": {"method": "POST", "path": path, "body": payload}}
            else:
                resp = client.get(path, params=payload)
                record = {"request": {"method": "GET", "path": path, "query": payload}}
            record["response"] = {"status": resp.status_code, "body": resp.json()}
            file = OUT / f"{name}.{label}.json"
            file.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"{resp.status_code} {file.name}")


if __name__ == "__main__":
    main()
