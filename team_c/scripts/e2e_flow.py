#!/usr/bin/env python3
"""
End-to-end driver for TicketEasy tool-generation flow against AstroCommerce API.
Runs: Connect -> Discover -> Request -> Clarify -> Reconcile -> Policy -> Approve -> Build -> Test -> Publish
"""
import json
import re
import sys
import time
import secrets
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8101"
API = f"{BASE}/api/v1"

# AstroCommerce test accounts
OWNER_EMAIL = "ordertrack1791441195097@example.com"
OWNER_PASS = "E2e-Strong-Pass-123"
OWNER_CUST_ID = "69d79014-ffdb-42be-92fb-c094d9f06e1f"
OWNER_ORDER_ID = "024aba44-c125-464c-b6ac-768b629a1b52"

INTRUDER_EMAIL = "intruder1791441224915@example.com"
INTRUDER_PASS = "E2e-Strong-Pass-123"
INTRUDER_CUST_ID = "f3b19991-48bb-422a-b17c-4868a20953fe"
INTRUDER_ORDER_ID = "baa6c90c-3dcc-41bd-9b09-05499e164642"

BAD_EMAIL = "nonexistent@example.com"
BAD_PASS = "wrong"


class E2EClient:
    def __init__(self):
        self.client = httpx.Client(base_url=BASE, timeout=120.0, follow_redirects=True)
        self.csrf_token = None

    def _update_csrf(self, html: str):
        m = re.search(r'name="csrf"\s+value="([^"]+)"', html)
        if m:
            self.csrf_token = m.group(1)
        return self.csrf_token

    def get_html(self, path: str) -> str:
        r = self.client.get(path)
        r.raise_for_status()
        self._update_csrf(r.text)
        return r.text

    def post_form(self, path: str, data: dict) -> httpx.Response:
        data = dict(data)
        if self.csrf_token:
            data.setdefault("csrf", self.csrf_token)
        # Don't follow redirects for form posts - they return 303 with empty body
        r = self.client.post(path, data=data, follow_redirects=False)
        self._update_csrf(r.text)
        return r

    def post_json(self, path: str, json_data: dict) -> httpx.Response:
        r = self.client.post(path, json=json_data)
        return r

    def get_json(self, path: str) -> httpx.Response:
        r = self.client.get(path)
        return r


def log_step(step: str):
    print(f"\n{'='*60}")
    print(f"STEP: {step}")
    print(f"{'='*60}")


def assert_ok(r: httpx.Response, context: str):
    if not r.is_success and r.status_code != 303:
        print(f"FAIL {context}: {r.status_code} {r.text}")
        r.raise_for_status()
    # Some responses (like 303 redirects from form posts) have empty bodies
    if r.status_code == 303 or not r.content:
        return {}
    return r.json()


def main():
    ec = E2EClient()

    # --- 1. Create business ---
    log_step("Create business")
    biz = assert_ok(ec.post_json(f"{API}/businesses", {
        "name": "AstroCommerce storefront",
        "description": "TEST-ONLY target API: an Astro e-commerce backend with orders, customers, products, cart, payments, images, shipping and store configuration."
    }), "create business")
    bid = biz["id"]
    print(f"Business: {bid}")

    # --- 2. Fetch OpenAPI spec ---
    log_step("Fetch OpenAPI spec")
    spec = assert_ok(ec.post_json(f"{API}/businesses/{bid}/specifications/fetch", {
        "url": "http://localhost:8080/api/v1/openapi.json"
    }), "fetch spec")
    sid = spec["id"]
    print(f"Spec: {sid}, ops: {spec['inventory']['eligible_operation_count']} eligible")

    # --- 3. Tool Request A (main: order lookup) ---
    log_step("Tool Request A: customer order lookup")
    goal_a = "TEST ONLY: let a customer look up one of their own orders by its order id, read the order status, totals and items, and change nothing"
    req = assert_ok(ec.post_json(f"{API}/businesses/{bid}/tool-requests", {
        "goal": goal_a,
        "examples": "",
        "spec_id": sid,
        "idempotency_key": secrets.token_urlsafe(16)
    }), "tool request A")
    rid = req["id"]
    print(f"Request: {rid}, status: {req['status']}")
    
    # Handle needs_clarification: provide clarification and re-request
    if req["status"] == "needs_clarification":
        log_step("Provide clarification for needs_clarification")
        clarification = "The tool calls GET /api/v1/orders/{order_id} and the enforcement policy checks that the response's customer_id field matches the authenticated customer's identity (customer_id context field). This ownership check runs after the successful 200 response."
        clar_resp = assert_ok(ec.post_json(f"{API}/tool-requests/{rid}/clarifications", {
            "text": clarification
        }), "post clarification")
        print(f"Clarification: {clar_resp}")
        # Re-fetch request to get proposal_id
        req = assert_ok(ec.get_json(f"{API}/tool-requests/{rid}"), "get request after clarification")
        print(f"Request after clarification: status={req['status']}, proposal_ids={req.get('proposal_ids')}")
    
    # Handle existing_tool case
    if req["status"] == "existing_tool" and req.get("existing_proposal_ids"):
        pid = req["existing_proposal_ids"][0]
        print(f"Using existing proposal: {pid}")
    elif req.get("proposal_ids"):
        pid = req["proposal_ids"][0]
        print(f"New proposal: {pid}")
    else:
        raise ValueError(f"No proposal found in response: {req}")

    # --- 4. Wait for proposal generation ---
    log_step("Wait for proposal generation")
    for _ in range(30):
        p = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), "get proposal")
        if p.get("state") != "generating":
            break
        time.sleep(1)
    print(f"Proposal state: {p['state']}, version: {p['version']}, rev: {p['review_revision']}")

    # --- 5. Answer questions AND requirements (before policy) ---
    # Every question this proposal raises is about who may call it and how ownership is enforced, so
    # the standing TEST-ONLY decision also answers whatever the keyword pass below does not match;
    # a placeholder would correctly be judged insufficient by the reviewer.
    OWNER_POLICY = ("TEST-ONLY owner decision: the caller is a logged-in customer (end_user) authenticated via "
                    "the AstroCommerce customer OAuth2 password grant and must own the order. The enforcement "
                    "policy checks that the response's customer_id field equals the authenticated customer's "
                    "identity (customer_id context field) after the successful 200 response and denies access on mismatch.")

    def answer_all(pid, version, rev):
        p = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), "get proposal for answers")
        q_answers = {}
        for q in p["content"]["questions"]:
            if "caller" in q["text"].lower() or "authorized" in q["text"].lower() or "role" in q["text"].lower():
                q_answers[q["id"]] = "Logged-in customers (end_user) who authenticate via the AstroCommerce customer OAuth2 password grant. Only the customer who owns the order may invoke this operation."
            elif "verify" in q["text"].lower() or "belongs" in q["text"].lower() or "own" in q["text"].lower():
                q_answers[q["id"]] = "The tool calls GET /api/v1/orders/{order_id} and the enforcement policy checks that the response's customer_id field matches the authenticated customer's identity (customer_id context field). This ownership check runs after the successful 200 response."
            else:
                q_answers[q["id"]] = OWNER_POLICY
        # Also answer requirements
        for r in p["requirements"]:
            if r["kind"] == "caller_access":
                q_answers[r["id"]] = "Logged-in customers (end_user) authenticated via OAuth2 password grant. Only the customer who owns the order may invoke this operation."
            elif r["kind"] == "record_scope":
                q_answers[r["id"]] = "The tool calls GET /api/v1/orders/{order_id} and the enforcement policy checks that the response's customer_id field matches the authenticated customer's identity (customer_id context field). This ownership check runs after the successful 200 response."
            else:
                q_answers[r["id"]] = "TEST-ONLY answer provided by the owner for this capability."
        return assert_ok(ec.post_json(f"{API}/proposals/{pid}/versions/{version}/answers", {
            "expected_revision": rev,
            "answers": q_answers
        }), "post answers")

    log_step("Answer questions & requirements")
    p = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), "get proposal before answers")
    answer_all(pid, p["version"], p["review_revision"])

    # --- 6. Reconcile (once before policy) ---
    def reconcile(pid, version, rev):
        return assert_ok(ec.post_json(f"{API}/proposals/{pid}/versions/{version}/reconcile", {
            "expected_revision": rev
        }), "reconcile")

    log_step("Reconcile #1")
    p = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), "get proposal before reconcile1")
    reconcile(pid, p["version"], p["review_revision"])

    # --- 7. Accept structured policy ---
    log_step("Accept structured policy")
    p = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), "get proposal for policy form")
    # Build policy from form defaults + ownership checks
    form = p.get("policy_form", {})
    # Find record_scope requirements
    record_reqs = [r for r in p["requirements"] if r["kind"] == "record_scope"]
    # Find a SAFE read step for each record_scope req that has a guaranteed /customer_id in 200 response
    inventory = assert_ok(ec.get_json(f"{API}/specifications/{sid}/inventory"), "get inventory")
    ops_by_id = {o["id"]: o for o in inventory["inventory"]["operations"]}

    def find_check_step(req):
        # req["operations"] are labels like "GET /api/v1/orders/{order_id}"
        for step in p["content"]["steps"]:
            op = ops_by_id.get(step["operation_id"])
            if not op:
                continue
            label = f'{op["method"]} {op["path"]}'
            if label in req["operations"] and op["method"] == "GET":
                # Check if this op has guaranteed /customer_id in 200 response
                for status, resp in (op.get("responses") or {}).items():
                    if str(status).startswith("2"):
                        schema = resp.get("schema")
                        if schema:
                            props = schema.get("properties", {})
                            required = schema.get("required", [])
                            cust = props.get("customer_id")
                            if cust and cust.get("type") == "string" and cust.get("nullable") != True and "customer_id" in required:
                                return step
        return None

    checks = []
    scopes = []
    for req in record_reqs:
        step = find_check_step(req)
        if not step:
            # Fallback: use first GET step in requirement
            for s in p["content"]["steps"]:
                op = ops_by_id.get(s["operation_id"])
                if op and f'{op["method"]} {op["path"]}' in req["operations"] and op["method"] == "GET":
                    step = s
                    break
        if not step:
            print(f"WARNING: no check step found for {req['id']}")
            continue
        # Use /customer_id as resource_field
        checks.append({
            "requirement_id": req["id"],
            "kind": "ownership",
            "resource": "Order record",
            "step_id": step["id"],
            "response_status": "200",
            "resource_field": "/customer_id",
            "identity_source": "customer_id"
        })
        scopes.append({
            "requirement_id": req["id"],
            "mode": "checked",
            "reason": "Customer may only access their own order records."
        })

    # Collect form inputs (suggested + existing)
    policy_inputs = form.get("inputs", [])

    policy = {
        "principals": ["end_user", "resource_owner"],
        "authentication": "required",
        "role_source": None,
        "roles": [],
        "permission_source": None,
        "permissions": [],
        "scopes": scopes,
        "checks": checks,
        "inputs": policy_inputs,
        "financial": False,
        "irreversible": False,
        "external_side_effects": False,
        "idempotent": True,
        "explanation": "Read-only order lookup for the authenticated customer."
    }

    print("Policy: " + json.dumps(policy, indent=2)[:800].encode("ascii", "replace").decode() + "...")
    pol = assert_ok(ec.post_json(f"{API}/proposals/{pid}/versions/{p['version']}/policy", {
        "expected_revision": p["review_revision"],
        "policy": policy
    }), "accept policy")
    print(f"Policy accepted, sha256: {pol.get('policy', {}).get('sha256')[:16] if pol.get('policy') else 'n/a'}")

    # --- 9. Reconcile after policy until ready_for_review ---
    log_step("Reconcile after policy")
    max_reconciles = 5
    p = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), "get proposal to start the reconcile loop")
    question_ids = {q["id"] for q in p["content"]["questions"]}
    for i in range(1, max_reconciles + 1):
        p = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), f"get proposal for reconcile #{i}")
        if p.get("state") == "ready_for_review":
            print(f"Proposal ready for review after {i - 1} reconciliations")
            break
        # The owner restates the answer for anything the reviewer still finds insufficient; the
        # reviewer, not the driver, decides whether the new answer resolves it.
        last = (p.get("reconciliations") or [{}])[-1].get("result") or {}
        open_findings = [f for f in last.get("findings", [])
                         if f["status"] != "resolved" and f["question_id"] in question_ids]
        if open_findings:
            for f in open_findings:
                print(f"  reviewer still finds {f['question_id']} {f['status']}: "
                      f"{(f.get('explanation') or '')[:200].encode('ascii', 'replace').decode()}")
            assert_ok(ec.post_json(f"{API}/proposals/{pid}/versions/{p['version']}/answers", {
                "expected_revision": p["review_revision"],
                "answers": {f["question_id"]: OWNER_POLICY for f in open_findings}
            }), "re-answer questions the reviewer found insufficient")
            p = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), f"get proposal after re-answer #{i}")
        reconcile(pid, p["version"], p["review_revision"])
        p = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), f"get proposal after reconcile #{i}")
        print(f"Reconcile #{i}: state={p.get('state')}")
    else:
        raise AssertionError(f"Proposal did not reach ready_for_review after {max_reconciles} reconciliations")

    # --- 10. Confirm requirements (after policy reconciliation) ---
    log_step("Confirm requirements")
    p = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), "get proposal before confirm")
    for req in p.get("requirements", []):
        requirement_id = req["id"]
        if req.get("status") == "answer_sufficient":
            assert_ok(ec.post_json(f"{API}/proposals/{pid}/versions/{p['version']}/requirements/{requirement_id}/confirm", {
                "expected_revision": p["review_revision"]
            }), f"confirm requirement {requirement_id}")

    # --- 11. Approve to build ---
    log_step("Approve to build")
    p = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), "get proposal before approve")
    dec = assert_ok(ec.post_json(f"{API}/proposals/{pid}/versions/{p['version']}/decisions", {
        "expected_revision": p["review_revision"],
        "action": "approve_to_build",
        "reason": "TEST-ONLY approval for sandbox publication",
        "idempotency_key": secrets.token_urlsafe(16)
    }), "approve")
    print("Decision: " + json.dumps(dec).encode("ascii", "replace").decode())

    # --- 10. Save connector (web form) ---
    log_step("Save connector")
    p = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), "get proposal for connector")
    # Get CSRF by loading proposal page
    ec.get_html(f"/proposals/{pid}")
    # Get connector draft from proposal view
    draft = p.get("connector_draft", {})
    base_url = draft.get("base_url", "http://localhost:8080")
    context_fields = draft.get("context_fields", ["customer_id"])
    if "customer_id" not in context_fields:
        context_fields.append("customer_id")

    conn_resp = ec.post_form("/actions/save_connector", {
        "proposal_id": pid,
        "base_url": base_url,
        "context_fields": ",".join(context_fields),
        "sandbox": "1"
    })
    # Form posts return 303 redirect on success
    assert conn_resp.status_code in (200, 303), f"save_connector failed: {conn_resp.status_code} {conn_resp.text}"
    # Extract connector_id from the proposal page HTML (redirect target)
    ec.get_html(f"/proposals/{pid}")
    # Search HTML for sandbox connector ID
    cid_match = re.search(r'sandbox-[0-9a-f]{20}', ec.client.get(f"{BASE}/proposals/{pid}").text)
    if not cid_match:
        # Fallback: check the JSON API for connectors
        p2 = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), "get proposal after connector")
        sandbox_cids = [cid for cid in p2.get("connectors", {}) if cid.startswith("sandbox-")]
        if sandbox_cids:
            connector_id = sandbox_cids[-1]
        else:
            raise AssertionError("No sandbox connector found after save")
    else:
        connector_id = cid_match.group(0)
    print(f"Connector: {connector_id}")

    # --- 11. Build artifact ---
    log_step("Build artifact")
    art = assert_ok(ec.post_json(f"{API}/proposals/{pid}/artifacts", {
        "connector_id": connector_id
    }), "build artifact")
    aid = art["id"]
    print(f"Artifact: {aid}, sha: {art.get('sha256', '')[:16]}")

    # --- 12. Save test identities (web form) ---
    log_step("Save test identities")
    ec.get_html(f"/artifacts/{aid}")

    # Owner identity
    assert_ok(ec.post_form("/actions/save_test_identity", {
        "artifact_id": aid,
        "identity_name": "owner",
        "scope": "end_user",
        "username": OWNER_EMAIL,
        "password": OWNER_PASS,
        "a:customer_id": OWNER_CUST_ID,
    }), "save owner identity")

    # Refresh CSRF
    ec.get_html(f"/artifacts/{aid}")

    # Intruder identity
    assert_ok(ec.post_form("/actions/save_test_identity", {
        "artifact_id": aid,
        "identity_name": "intruder",
        "scope": "end_user",
        "username": INTRUDER_EMAIL,
        "password": INTRUDER_PASS,
        "a:customer_id": INTRUDER_CUST_ID,
    }), "save intruder identity")

    # Verify identities saved
    import sqlite3
    conn = sqlite3.connect("data/flow4.sqlite3")
    conn.row_factory = sqlite3.Row
    # Get business_id from the proposal
    p = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), "get proposal for business_id")
    biz_id = p.get("business_id")
    rows = conn.execute("SELECT * FROM sandbox_connectors WHERE business_id=?", (biz_id,)).fetchall()
    for row in rows:
        content = json.loads(row["content"])
        print(f"  Connector {row['id']} identities: {list(content.get('identities', {}).keys())}")

    # --- 13. Sandbox tests ---
    log_step("Run sandbox tests")

    def execution_detail(execution_id):
        """The target API's own answer for this run, read from the stored execution report."""
        a = assert_ok(ec.get_json(f"{API}/artifacts/{aid}"), "get artifact for the execution report")
        e = next((e for e in a.get("executions", []) if e["id"] == execution_id), {})
        return (((e.get("report") or {}).get("failure") or {}).get("detail")) or ""

    def sandbox_test(name, identity, arguments, expect_status, scenario="general", record_owner=None):
        body = {
            "name": name,
            "identity": identity,
            "arguments": arguments,
            "expect": {"status": expect_status},
            "scenario": scenario,
            "record_owner": record_owner
        }
        # The target API answers 5xx occasionally (an upstream fault, not a refusal this run can be
        # judged on), so the same test is re-run for that reason only. Denials, validation failures
        # and every other result are never retried, every run stays in the audit history, and the
        # publication gate still judges the latest run.
        for attempt in range(4):
            result = assert_ok(ec.post_json(f"{API}/artifacts/{aid}/sandbox-tests", body), f"sandbox test {name}")
            detail = execution_detail(result["execution_id"])
            if not detail.startswith("HTTP 5"):
                break
            print(f"  {name}: target API answered {detail} ({attempt + 1}/4); re-running the same test")
        print(f"  {name}: verdict={result.get('verdict')} status={result.get('status')} detail={detail or 'no upstream failure'}")
        return result

    # Owner own_record
    sandbox_test("owner_own", "owner", {"order_id": OWNER_ORDER_ID}, "succeeded", "own_record")

    # Intruder own_record
    sandbox_test("intruder_own", "intruder", {"order_id": INTRUDER_ORDER_ID}, "succeeded", "own_record")

    # Cross-user: intruder targets owner's order -> must be refused
    sandbox_test("cross_user_denied", "intruder", {"order_id": OWNER_ORDER_ID}, "failed", "cross_user", "owner")

    # Bad credentials (unknown identity -> rejected by executor)
    sandbox_test("bad_creds", "nonexistent", {"order_id": OWNER_ORDER_ID}, "rejected", "general", None)

    # --- 14. Publish to sandbox ---
    log_step("Publish to sandbox")
    pub = assert_ok(ec.post_json(f"{API}/artifacts/{aid}/publications", {
        "environment": "sandbox",
        "note": "TEST-ONLY sandbox publication for demo"
    }), "publish")
    pub_id = pub["id"]
    print(f"Published: {pub_id}")
    print(f"Activation: runtime_ready={pub.get('activation', {}).get('runtime_ready')}, activated={pub.get('activation', {}).get('activated')}")

    # --- 15. Verify activation flags ---
    log_step("Verify production activation NOT implemented")
    assert pub.get("environment") == "sandbox", "Publication environment must be sandbox"
    act = pub.get("activation")
    # Activation is None when no enforcement config -> production activation not implemented
    if act is not None:
        assert act.get("runtime_ready") is False, "runtime_ready must be False (not implemented)"
        assert act.get("activated") is False, "activated must be False (not implemented)"
    meta = pub.get("meta")
    if meta is not None:
        assert meta.get("team_c", {}).get("production_ready") is False, "production_ready must be False"
    print("[OK] Production activation correctly absent")

    # --- 16. Verify retrieval reaches beyond the first index slice ---
    log_step("Verify retrieval coverage")
    request_a = assert_ok(ec.get_json(f"{API}/tool-requests/{rid}"), "get request A")
    search_a = request_a["coverage"]
    print(f"Request A: {search_a['searched_operations']}/{search_a['total_operations']} operations in "
          f"{search_a['retrieval_rounds']} round(s), partial={search_a['partial_search']}")
    assert search_a["searched_operations"] < search_a["total_operations"], "Request A should stop at its first slice"

    # A request whose operation the first slice does not contain: retrieval has to widen the index.
    goal_b = "TEST ONLY: show delivery cost and available rates for an address"
    request_b = assert_ok(ec.post_json(f"{API}/businesses/{bid}/tool-requests", {
        "goal": goal_b,
        "examples": "",
        "spec_id": sid,
        "idempotency_key": secrets.token_urlsafe(16)
    }), "tool request B")
    search_b = request_b["coverage"]
    print(f"Request B: {search_b['searched_operations']}/{search_b['total_operations']} operations in "
          f"{search_b['retrieval_rounds']} round(s), partial={search_b['partial_search']}")
    assert search_b["retrieval_rounds"] >= 2, f"retrieval must expand past the first slice, got {search_b['retrieval_rounds']} round(s)"
    assert search_b["searched_operations"] > search_a["searched_operations"], (
        f"Request B must search further than Request A's {search_a['searched_operations']}-operation slice")
    print("[OK] Retrieval widened past the initial slice")

    p = assert_ok(ec.get_json(f"{API}/proposals/{pid}"), "final proposal")

    print("\n" + "="*60)
    print("E2E FLOW COMPLETE [OK]")
    print(f"Business: {bid}")
    print(f"Spec: {sid}")
    print(f"Proposal: {pid} (v{p['version']})")
    print(f"Artifact: {aid}")
    print(f"Connector: {connector_id}")
    print(f"Publication: {pub_id}")
    print("="*60)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n[FAIL] E2E FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)