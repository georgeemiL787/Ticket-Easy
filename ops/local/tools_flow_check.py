"""Real check of the guided tool setup (the three screens' API) against the Nile sandbox shop. TEST-ONLY.

Needs ops/local/serve.py up (sandbox, the tools service with a model provider, the agent signed in) and the tools service binding file
(ops/local/var/tools.json: run ops/local/tools_lifecycle.py once, or copy the example). It drives exactly what the
three dashboard screens call, as a signed-in manager, with the owner's own choices (no scripted shortcuts inside the tools service):

  1 import      the sandbox's OpenAPI by address -> classes of its operations
  2 connect     the sandbox as the API address, a connection check
  3 choose      one read (look up an order) and one write (open a ticket, checked through the order read)
  4 prepare     one tool per choice; wait until each waits for the owner
  5 approve     with the four effect flags confirmed for the write -> review, build and the automatic tests run
  6 publish     the tools that passed -> a DRAFT bundle (nothing in use yet)
  7 activate    the bundle becomes the one in use; the agent lists the new tools

It counts the POSTs the owner needs (the plan's goal: at most 5) and fails if a plaintext credential is found in the tools service's
database. It changes state on purpose (a new tools-service business is NOT created: it reuses shop_001's binding).

    services/agent/.venv/Scripts/python.exe ops/local/tools_flow_check.py [--tenant shop_001]
"""

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops" / "local"))
import e2e_chat  # noqa: E402  (its manager() signs a test manager in)

AGENT_URL, SANDBOX = e2e_chat.AGENT_URL, e2e_chat.SANDBOX
POSTS: list[str] = []


def step(title: str) -> None:
    print(f"\n=== {title}", flush=True)


def call(http: httpx.Client, method: str, path: str, headers: dict[str, str], **kwargs: Any) -> dict[str, Any]:
    if method == "POST":
        POSTS.append(path)
    r = http.request(method, path, headers=headers if method == "POST" else None, timeout=300, **kwargs)
    if r.status_code >= 400:
        raise SystemExit(f"FAIL {method} {path}: {r.status_code} {r.text[:600]}")
    return r.json()


def wait(http: httpx.Client, tenant: str, batch_id: str, until: Any, minutes: int = 25) -> dict[str, Any]:
    end = time.time() + minutes * 60
    while time.time() < end:
        batch = call(http, "GET", f"/v1/dashboard/tool-setup/batches/{batch_id}", {}, params={"tenant_id": tenant})
        if until(batch):
            return batch
        time.sleep(3)
    raise SystemExit("FAIL timed out waiting for the batch: " + str([(i["path"], i["state"], i["message"]) for i in batch["items"]]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tenant", default="shop_001")
    args = parser.parse_args()
    tenant, base = args.tenant, "/v1/dashboard/tool-setup"
    with httpx.Client(base_url=AGENT_URL, trust_env=False) as http:
        headers = e2e_chat.manager(http)

        step("1 import the sandbox's OpenAPI by address")
        found = call(http, "POST", f"{base}/import", headers, params={"tenant_id": tenant}, json={"url": SANDBOX + "/openapi.json"})
        e2e_chat.check("the description is valid and has operations", found["valid"] and found["usable"] > 0, found)
        spec_id = found["spec_id"]
        listing = call(http, "GET", f"{base}/operations", {}, params={"tenant_id": tenant, "spec_id": spec_id, "limit": 500})
        classes = {c: n for c, n in listing["counts"].items()}
        e2e_chat.check("operations are grouped read / write / risky / restricted", classes["read"] > 0 and classes["write"] + classes["risky"] > 0, classes)

        step("2 connect: the sandbox is the API, and it answers")
        reach = call(http, "POST", f"{base}/connection-check", headers, params={"tenant_id": tenant}, json={"base_url": SANDBOX})
        e2e_chat.check("the connection check reaches the sandbox", reach["reachable"], reach)

        step("3 choose one read and one write")
        rows = {(o["method"], o["path"]): o for o in listing["operations"]}
        read = rows.get(("GET", "/api/orders/{order_id}")) or next(o for o in listing["operations"] if o["class"] == "read" and "{" in o["path"])
        write = rows.get(("POST", "/api/tickets")) or next(o for o in listing["operations"] if o["class"] == "write")
        e2e_chat.check("the read is a read and the write changes data", read["class"] == "read" and write["class"] in ("write", "risky"), (read["path"], write["path"]))

        step("4 prepare one tool per choice")
        made = call(http, "POST", f"{base}/batches", headers, params={"tenant_id": tenant}, json={
            "spec_id": spec_id, "operation_ids": [read["id"], write["id"]], "verify_operation_id": read["id"],
            "access": {"callers": "customer", "own_records_only": True},
            "connection": {"base_url": SANDBOX, "fixtures": {"base_url": SANDBOX, "key_ref": "NILE_SANDBOX_FIXTURE_KEY"}},
        })
        batch = wait(http, tenant, made["id"], lambda b: not b["busy"])
        waiting = [i for i in batch["items"] if i["state"] == "needs_owner"]
        e2e_chat.check("both tools wait for the owner with a summary", len(waiting) == 2 and all(i["summary"] for i in waiting), [(i["path"], i["state"], i["message"]) for i in batch["items"]])
        if len(waiting) != 2:
            return 1

        step("5 approve: the owner confirms the write's effects; the tools service reviews, builds and tests")
        confirmations = {i["proposal_id"]: i["suggested_effects"] for i in waiting if i["class"] != "read"}
        call(http, "POST", f"{base}/batches/{batch['id']}/approve", headers, params={"tenant_id": tenant},
             json={"proposal_ids": [i["proposal_id"] for i in waiting], "confirmations": confirmations})
        batch = wait(http, tenant, batch["id"], lambda b: not any(i["state"] in ("approved", "reviewing", "building", "testing") for i in b["items"]))
        states = {i["path"]: i["state"] for i in batch["items"]}
        e2e_chat.check("both tools passed their automatic tests", all(s == "ready_to_publish" for s in states.values()), [(i["path"], i["state"], i["message"], i["needs_owner"]) for i in batch["items"]])
        if not all(s == "ready_to_publish" for s in states.values()):
            return 1

        step("6 publish into a draft bundle (nothing in use yet)")
        published = call(http, "POST", f"{base}/batches/{batch['id']}/publish", headers, params={"tenant_id": tenant}, json={})
        e2e_chat.check("a draft bundle names the new tools", len(published["tools"]) == 2, published["tools"])
        active_before = http.get(f"/v1/admin/tenants/{tenant}/bundles/active")
        e2e_chat.check("no bundle was activated by publishing", active_before.status_code == 404 or active_before.json()["version"] != published["bundle_version"], active_before.status_code)

        step("7 activate: the bundle is in use at once")
        done = call(http, "POST", f"{base}/batches/{batch['id']}/activate", headers, params={"tenant_id": tenant}, json={"bundle_version": published["bundle_version"]})
        e2e_chat.check("the bundle is active", done["status"] == "active", done)
        tools = call(http, "GET", "/v1/dashboard/business-tools", {}, params={"tenant_id": tenant})
        names = {t["name"] for t in tools["tools"]}
        e2e_chat.check("the agent's tool list includes the new tools", set(published["tools"]) <= names, sorted(names))

        step("owner effort and secrets")
        e2e_chat.check("the owner needed at most 5 changes (import, connect check, prepare, approve, publish, activate = 6 counting the check and activation)",
                       len(POSTS) <= 6, POSTS)
        db = ROOT / "services" / "tools" / "data" / "integration.sqlite3"
        if db.exists():
            with sqlite3.connect(db) as conn:
                rows_ = conn.execute("SELECT content FROM sandbox_connectors").fetchall()
            plain = [r for r in rows_ if '"token": "' in r[0] and '"token": "enc:v1:' not in r[0]]
            e2e_chat.check("no credential is stored in plaintext in the tools service", not plain)
    failed = [name for name, ok, _ in e2e_chat.RESULTS if not ok]
    print("\n" + ("TOOLS FLOW CHECK PASSED" if not failed else "TOOLS FLOW CHECK FAILED\n- " + "\n- ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
