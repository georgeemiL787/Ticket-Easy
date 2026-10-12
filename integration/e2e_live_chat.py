"""Live Demo through the real TicketEasy chat: Team A + Team B + Team C, calling a Live Demo API with real HTTP.

The Live Demo target here is the separate demo-shop deployment (integration/serve.py demo-shop, port 8103: its own
process, data and credentials, synthetic data on THIS machine), allowed by the explicit LIVE_DEMO_PRIVATE_HOSTS
exception. It is NOT an external API; integration/live_demo_check.py covers a real external API. What this proves is
the chat path: the assistant's Live Demo tool calls reach that deployment (never the sandbox), reads and writes are
real and verified independently, an uncertain live write is never retried, and disabling stops the next call.

Needs: integration/serve.py up (with demo-shop), integration/team_c_lifecycle.py --tool order|ticket --environment
live_demo, and Team B restarted after that. Prints PASS/FAIL per check; exits 1 when any failed.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "integration"))
from e2e_chat import RESULTS, Chat, call_old_name, check, manager, mcp_executions, sandbox_state, traces  # noqa: E402

VAR = ROOT / "integration" / "var"
TEAM_B, TEAM_C, DEMO_SHOP = "http://127.0.0.1:8010", "http://127.0.0.1:8101", "http://127.0.0.1:8103"
T = "shop_001"


def shop_state() -> dict[str, Any]:
    key = (VAR / "demo_shop_harness.key").read_text(encoding="utf-8").strip()
    return httpx.get(DEMO_SHOP + "/_harness/state", headers={"x-harness-key": key}, timeout=10, trust_env=False).json()


def shop_switch(**switches: bool) -> None:
    key = (VAR / "demo_shop_harness.key").read_text(encoding="utf-8").strip()
    httpx.post(DEMO_SHOP + "/_harness/switches", json=switches, headers={"x-harness-key": key}, timeout=10, trust_env=False)


def live_runs(team_c: httpx.Client, artifact_id: str) -> list[dict[str, Any]]:
    return [e for e in team_c.get(f"/api/v1/artifacts/{artifact_id}").json()["executions"] if e.get("mode") == "mcp_live_demo"]


def main() -> int:
    binding = json.loads((VAR / "team_c.json").read_text(encoding="utf-8"))
    tenant = binding["tenants"][T]
    order = json.loads((VAR / "lifecycle_order_live.json").read_text(encoding="utf-8"))
    ticket = json.loads((VAR / "lifecycle_ticket_live.json").read_text(encoding="utf-8"))
    sandbox_order = json.loads((VAR / "lifecycle_order.json").read_text(encoding="utf-8"))
    http = httpx.Client(base_url=TEAM_B, timeout=120, trust_env=False)
    team_c = httpx.Client(base_url=TEAM_C, timeout=60, trust_env=False)
    headers = manager(http)

    print("\n== The Business tools page tells the truth about Live Demo")
    page = http.post("/v1/dashboard/business-tools/refresh", params={"tenant_id": T}, headers=headers).json()
    tools = {t["name"]: t for t in page["tools"]}
    for name in ("get_order", "create_ticket"):
        check(f"{name} is served by Team C as LIVE DEMO against {DEMO_SHOP.split('//')[1]}",
              tools[name]["environment"] == "live_demo" and tools[name]["api_host"] == "127.0.0.1:8103" and tools[name]["status"] == "available", tools[name])  # fmt: skip
    check("the page counts the live tools and no longer says TEST-ONLY", page["live_tools"] == 2 and page["test_only"] is False, page["live_tools"])
    live_pubs = [p for p in page["publications"] if p["environment"] == "live_demo" and p["status"] == "published"]
    check("its Live Demo publications are executable and never production-ready",
          len(live_pubs) >= 2 and all(p["execution_enabled"] and not p["production_ready"] for p in live_pubs), live_pubs)  # fmt: skip

    print("\n== Team A + Team B still answer on their own")
    reply = Chat(http).say("What is your return policy?")
    check("a policy question is answered from Team A's passages", reply["decision"] == "answer" and bool(reply["citations"]), reply)

    print("\n== A read through the Live Demo tool, from the chat")
    sandbox_before = len(mcp_executions(team_c, sandbox_order["artifact_id"]))
    before = len(live_runs(team_c, order["artifact_id"]))
    alice = Chat(http)
    alice.say("Where is my order NS-20877?")
    reply = alice.say("01012345601")
    check("the order status comes back through the Live Demo tool", reply["decision"] == "answer" and "on its way" in reply["text"], reply)
    runs = live_runs(team_c, order["artifact_id"])
    check("Team C recorded an mcp_live_demo execution as alice, and it succeeded",
          len(runs) == before + 1 and runs[-1]["identity"] == "alice" and runs[-1]["status"] == "succeeded", runs[before:])  # fmt: skip
    check("nothing was sent to the sandbox tool (no silent fallback)", len(mcp_executions(team_c, sandbox_order["artifact_id"])) == sandbox_before)

    print("\n== A write through the Live Demo tool, confirmed by the customer, verified at the API")
    live_before, sandbox_tickets = len(shop_state()["tickets"]), len(sandbox_state()["tickets"])
    chat = Chat(http)
    chat.say("I have a complaint about order NS-20877, the parcel arrived late")
    confirm = chat.say("01012345601")
    check("the customer is asked to confirm before anything is written", confirm["decision"] == "confirm", confirm)
    done = chat.say("yes")
    tickets = shop_state()["tickets"]
    check("exactly one ticket now exists in the Live Demo deployment, for C-100",
          len(tickets) == live_before + 1 and tickets[-1]["customer_id"] == "C-100", tickets[live_before:])  # fmt: skip
    check("the reply quotes that deployment's own reference", done["decision"] == "execute" and tickets[-1]["ticket_id"] in done["text"], done)
    check("the sandbox received no ticket", len(sandbox_state()["tickets"]) == sandbox_tickets)

    print("\n== An uncertain Live Demo write is never retried and never reported as done")
    shop_switch(fail_next_ticket_after_commit=True)
    live_before, runs_before = len(shop_state()["tickets"]), len(live_runs(team_c, ticket["artifact_id"]))
    chat = Chat(http)
    chat.say("I have a complaint about order NS-20877, the parcel arrived late")
    chat.say("01012345601")
    reply = chat.say("yes")
    runs = live_runs(team_c, ticket["artifact_id"])
    check("Team C classifies it outcome_unknown", len(runs) == runs_before + 1 and runs[-1]["status"] == "outcome_unknown", runs[runs_before:])
    check("the write happened once at the API and was not repeated", len(shop_state()["tickets"]) == live_before + 1)
    reasons = [t.get("escalation_reason") for t in traces(http, chat.id)]
    check("the customer is handed over as unverified_result", reply["decision"] == "handoff" and "unverified_result" in reasons, (reply, reasons))
    shop_switch(fail_next_ticket_after_commit=False)

    print("\n== The connection check, through Team B")
    connector = order["connector_id"]
    r = http.post(f"/v1/dashboard/business-tools/connectors/{connector}/check", params={"tenant_id": T}, headers=headers)
    card = next((c for c in r.json().get("connectors", []) if c["id"] == connector), {}) if r.status_code == 200 else {}
    steps = {c["check"]: c["status"] for c in card.get("checks", [])}
    check("the Live Demo connector's check: configuration, reachable, permissions and executable proven",
          card.get("environment") == "live_demo" and steps.get("configuration") == "ok" and steps.get("reachable") == "ok"
          and steps.get("permissions") == "ok" and steps.get("executable") == "ok", steps)  # fmt: skip

    print("\n== Disabling the Live Demo tool stops the next call")
    r = http.post(f"/v1/dashboard/business-tools/publications/{order['publication_id']}/disable", params={"tenant_id": T},
                  json={"note": "Live Demo e2e: check the next call is refused"}, headers=headers)  # fmt: skip
    check("a manager disables it through Team B and it is listed as missing", r.status_code == 200
          and {t["name"]: t["status"] for t in r.json()["tools"]}["get_order"] == "missing", r.text[:200])  # fmt: skip
    old = asyncio.run(call_old_name(binding, tenant, order["tool_name"]))
    check("a client holding the old tool name is refused by Team C", old == {"rpc_error": -32602, "data": {"code": "tool_not_published"}}, old)
    count = len(live_runs(team_c, order["artifact_id"]))
    late = Chat(http)
    late.say("Where is my order NS-20877?")
    reply = late.say("01012345601")
    check("the assistant hands over honestly and Team C runs nothing", reply["decision"] == "handoff" and len(live_runs(team_c, order["artifact_id"])) == count, reply)
    r = team_c.post(f"/api/v1/artifacts/{order['artifact_id']}/publications", json={"note": "Live Demo e2e: republished", "environment": "live_demo"})
    if r.status_code == 200:
        order["publication_id"] = r.json()["id"]
        (VAR / "lifecycle_order_live.json").write_text(json.dumps(order, indent=2), encoding="utf-8")
    page = http.post("/v1/dashboard/business-tools/refresh", params={"tenant_id": T}, headers=headers).json()
    check("republished and discovered again after a refresh", r.status_code == 200 and {t["name"]: t["status"] for t in page["tools"]}["get_order"] == "available")

    print("\n== A revoked Live Demo credential fails honestly (no data, no retry)")
    from team_c_lifecycle import IDENTITIES, Owner, add_identities

    owner = Owner()
    owner.form(f"/artifacts/{order['artifact_id']}", "/actions/save_test_identity",
               {"artifact_id": order["artifact_id"], "identity_name": "alice", "scope": "end_user", "token": "revoked-credential",
                "a:customer_id": IDENTITIES["alice"][0]})  # fmt: skip
    try:
        before = len(live_runs(team_c, order["artifact_id"]))
        chat = Chat(http)
        chat.say("Where is my order NS-20877?")
        reply = chat.say("01012345601")
        runs = live_runs(team_c, order["artifact_id"])
        calls = [c for t in traces(http, chat.id) for c in t.get("tool_calls", []) if c["tool"] == "get_order"]
        check("the API refused the revoked credential and Team C reports it as failed (never succeeded)",
              len(runs) > before and all(r["status"] == "failed" for r in runs[before:]), [r["status"] for r in runs[before:]])  # fmt: skip
        check("the customer gets no order data, and the trace keeps the failure",
              reply["decision"] != "answer" and "on its way" not in reply["text"] and bool(calls) and calls[-1]["error_code"] == "TEAM_C_FAILED", (reply, calls[-1:]))  # fmt: skip
    finally:
        add_identities(owner, order["artifact_id"], DEMO_SHOP, "demo_shop_harness.key")  # the demo account's real token again

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed  (Live Demo target: the local demo-shop deployment, NOT an external API)")
    (VAR / "e2e_live_chat_results.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
