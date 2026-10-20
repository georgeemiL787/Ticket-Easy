"""End-to-end check of the integrated TicketEasy with REAL services: the knowledge service, the agent, the tools service and the sandbox API.

Needs the four services of ops/local/serve.py running and the lifecycle done (ops/local/tools_lifecycle.py), so
the agent's get_order is bound to the tools service's published order-lookup tool. Every customer message goes through the agent's real
chat API; the business-tools calls go through the agent as a signed-in manager; the tools service's own records (executions) and the
sandbox's harness state are read independently. Prints PASS/FAIL per check and exits 1 when any check failed.

    services/agent/.venv/Scripts/python.exe ops/local/e2e_chat.py

It changes state on purpose: it disables the order-lookup publication through the agent, checks the agent
stops using it, then publishes the same tested artifact again through the tools service's API and checks the agent discovers it.
"""

import asyncio
import json
import os
import secrets
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
VAR = ROOT / "ops" / "local" / "var"
AGENT_URL, TOOLS_URL, KNOWLEDGE_URL, SANDBOX = "http://127.0.0.1:8010", "http://127.0.0.1:8101", "http://127.0.0.1:8001", "http://127.0.0.1:8102"
T = "shop_001"
RESULTS: list[tuple[str, bool, str]] = []


def tools_headers() -> dict[str, str]:
    """The tools service's admin token (ops/local/var/tools_admin.key, created when production.env is on): once the operator sets one, the tools service
    requires it on every change through its REST API."""
    key = VAR / "tools_admin.key"
    return {"x-tools-admin-token": key.read_text(encoding="utf-8").strip()} if key.exists() and (ROOT / "ops" / "local" / "production.env").exists() else {}


def check(name: str, ok: bool, detail: Any = "") -> bool:
    RESULTS.append((name, ok, str(detail)[:400]))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{str(detail)[:300]}]" if detail and not ok else ""), flush=True)
    return ok


class Chat:
    """One customer conversation through the agent's public chat API."""

    def __init__(self, http: httpx.Client) -> None:
        self.http, self.id = http, f"e2e-{uuid.uuid4().hex[:10]}"

    def say(self, text: str) -> dict[str, Any]:
        r = self.http.post(f"/v1/conversations/{self.id}/messages", json={"tenant_id": T, "text": text})
        r.raise_for_status()
        reply = r.json()
        print(f"   customer: {text}\n   agent ({reply['decision']}): {reply['text'][:220]}", flush=True)
        return reply


def manager(http: httpx.Client) -> dict[str, str]:
    """Sign in as a test manager (created once; its generated password lives in ops/local/var, never printed)."""
    key = VAR / "e2e_manager.key"
    if not key.exists():
        key.write_text(secrets.token_urlsafe(18), encoding="utf-8")
    password = key.read_text(encoding="utf-8").strip()
    python = ROOT / "services" / "agent" / ".venv" / "Scripts" / "python.exe"
    env = dict(os.environ, AGENT_STORE="sqlite", AGENT_DB_PATH=str(VAR / "agent.sqlite3"))
    base = [str(python if python.exists() else sys.executable), "-m", "agent_service", "create-user", "--email", "e2e-manager@example.com",
            "--name", "E2E Manager", "--role", "manager", "--tenants", T, "--password", password]  # fmt: skip
    for extra in ("--if-missing", "--reset-password"):
        subprocess.run([*base, extra], cwd=ROOT / "services" / "agent", env=env, capture_output=True, check=False)
    r = http.post("/v1/auth/login", json={"email": "e2e-manager@example.com", "password": password})
    r.raise_for_status()
    return {"X-CSRF-Token": r.json()["csrf_token"]}


def traces(http: httpx.Client, conversation: str) -> list[dict[str, Any]]:
    r = http.get(f"/v1/conversations/{conversation}/traces", params={"tenant_id": T})
    r.raise_for_status()
    body = r.json()
    return body if isinstance(body, list) else body.get("traces", [])


def tool_calls(http: httpx.Client, conversation: str, tool: str) -> list[dict[str, Any]]:
    return [c for t in traces(http, conversation) for c in t.get("tool_calls", []) if c["tool"] == tool]


def mcp_executions(tools_http: httpx.Client, artifact_id: str) -> list[dict[str, Any]]:
    executions = tools_http.get(f"/api/v1/artifacts/{artifact_id}").json()["executions"]
    return [e for e in executions if e.get("mode") == "mcp_sandbox"]


async def direct_mcp(binding: dict[str, Any], tenant: dict[str, Any], tool: str) -> dict[str, Any]:
    """What the tools service's MCP server itself answers (the agent's own client, real server process): the contract checks."""
    sys.path.insert(0, str(ROOT / "services" / "agent" / "src"))
    from agent_service.adapters.tools_mcp_client import McpRemoteError, McpStdioSession

    command = [*binding["command"], "--business-id", tenant["business_id"], "--identity", "alice",
               "--database", binding["database"], "--sandbox-hosts", binding["sandbox_hosts"]]  # fmt: skip
    session = McpStdioSession(command, cwd=binding["cwd"], env=binding.get("env"))
    await session.start()
    out: dict[str, Any] = {"tools": [t["name"] for t in await session.list_tools(timeout_s=30)]}
    try:
        extra = await session.call_tool(tool, {"order_id": "NS-20877", "customer_id": "C-101"}, timeout_s=60)
        out["extra_argument"] = extra.get("structuredContent")
        try:
            await session.call_tool("not_a_tool_v1_00", {}, timeout_s=30)
        except McpRemoteError as exc:
            out["unknown_tool"] = {"code": exc.code, "data": exc.data}
    finally:
        session.close()
    return out


async def call_old_name(binding: dict[str, Any], tenant: dict[str, Any], tool: str) -> Any:
    sys.path.insert(0, str(ROOT / "services" / "agent" / "src"))
    from agent_service.adapters.tools_mcp_client import McpRemoteError, McpStdioSession

    command = [*binding["command"], "--business-id", tenant["business_id"], "--identity", "alice",
               "--database", binding["database"], "--sandbox-hosts", binding["sandbox_hosts"]]  # fmt: skip
    session = McpStdioSession(command, cwd=binding["cwd"], env=binding.get("env"))
    await session.start()
    try:
        return (await session.call_tool(tool, {"order_id": "NS-20877"}, timeout_s=60)).get("structuredContent")
    except McpRemoteError as exc:
        return {"rpc_error": exc.code, "data": exc.data}
    finally:
        session.close()


def sandbox_state() -> dict[str, Any]:
    key = (VAR / "sandbox_harness.key").read_text(encoding="utf-8").strip()
    return httpx.get(SANDBOX + "/_harness/state", headers={"x-harness-key": key}, timeout=10, trust_env=False).json()


def sandbox_switch(**switches: bool) -> None:
    key = (VAR / "sandbox_harness.key").read_text(encoding="utf-8").strip()
    httpx.post(SANDBOX + "/_harness/switches", json=switches, headers={"x-harness-key": key}, timeout=10, trust_env=False)


def writes(http: httpx.Client, tools_http: httpx.Client, artifact_id: str) -> None:
    print("\n== TEST B/C: a write through Team C (open a support ticket)")
    before = len(sandbox_state()["tickets"])
    runs_before = len(mcp_executions(tools_http, artifact_id))
    chat = Chat(http)
    chat.say("I have a complaint about order NS-20877, the parcel arrived late")
    confirm = chat.say("01012345601")
    check("the complaint is confirmed with the customer before anything is written", confirm["decision"] == "confirm", confirm)
    done = chat.say("yes")
    tickets = sandbox_state()["tickets"]
    check("after the yes, exactly one ticket exists in the sandbox, for C-100",
          len(tickets) == before + 1 and tickets[-1]["customer_id"] == "C-100" and tickets[-1]["order_id"] == "NS-20877", tickets[before:])  # fmt: skip
    check("the agent reports it done with the sandbox's own ticket reference",
          done["decision"] == "execute" and tickets[-1]["ticket_id"] in done["text"], done)  # fmt: skip
    runs = mcp_executions(tools_http, artifact_id)
    check("Team C ran the write once, as alice, and it succeeded",
          len(runs) == runs_before + 1 and runs[-1]["identity"] == "alice" and runs[-1]["status"] == "succeeded", [(r["identity"], r["status"]) for r in runs[runs_before:]])  # fmt: skip

    print("\n== TEST C: an uncertain write is never retried and never reported as done or failed")
    sandbox_switch(fail_next_ticket_after_commit=True)  # the sandbox stores the ticket, then answers 500
    before, runs_before = len(sandbox_state()["tickets"]), len(mcp_executions(tools_http, artifact_id))
    chat = Chat(http)
    chat.say("I have a complaint about order NS-20877, the parcel arrived late")
    chat.say("01012345601")
    reply = chat.say("yes")
    tickets, runs = sandbox_state()["tickets"], mcp_executions(tools_http, artifact_id)
    check("Team C classifies it outcome_unknown (a 5xx after the write)",
          len(runs) == runs_before + 1 and runs[-1]["status"] == "outcome_unknown", [(r["identity"], r["status"]) for r in runs[runs_before:]])  # fmt: skip
    check("the write really happened once in the sandbox and was not repeated", len(tickets) == before + 1, tickets[before:])
    traces_ = traces(http, chat.id)
    reasons = [t.get("escalation_reason") for t in traces_]
    check("Team B hands over as unverified_result instead of claiming success or failure",
          reply["decision"] == "handoff" and "unverified_result" in reasons and tickets[-1]["ticket_id"] not in reply["text"], (reply, reasons))  # fmt: skip
    calls = [c for t in traces_ for c in t.get("tool_calls", []) if c["tool"] == "create_ticket"]
    check("the trace has exactly one create_ticket call, marked OUTCOME_UNKNOWN",
          len(calls) == 1 and calls[0]["error_code"] == "OUTCOME_UNKNOWN", calls)  # fmt: skip
    sandbox_switch(fail_next_ticket_after_commit=False)


def main() -> int:
    binding = json.loads((VAR / "tools.json").read_text(encoding="utf-8"))
    tenant = binding["tenants"][T]
    state = json.loads((VAR / "lifecycle_order.json").read_text(encoding="utf-8"))
    artifact_id, tool_name = state["artifact_id"], state["tool_name"]
    http = httpx.Client(base_url=AGENT_URL, timeout=120, trust_env=False)
    tools_http = httpx.Client(base_url=TOOLS_URL, timeout=60, trust_env=False, headers=tools_headers())

    print("\n== Services")
    for name, url in (("Team A", KNOWLEDGE_URL), ("Team B", AGENT_URL), ("Team C", TOOLS_URL), ("sandbox", SANDBOX)):
        check(f"{name} is up", httpx.get(url + "/health", timeout=5, trust_env=False).status_code == 200)
    check("Team B runs against the real Team A (live mode)", http.get("/health").json().get("mode") == "live")

    print("\n== TEST A: the Team A + Team B flow is unchanged")
    reply = Chat(http).say("What is your return policy?")
    check("a policy question is answered from Team A's passages, with citations", reply["decision"] == "answer" and bool(reply["citations"]), reply)
    human = Chat(http)
    reply = human.say("I want to talk to a human please")
    check("asking for a person opens a handoff case", reply["decision"] == "handoff" and bool(reply["handoff_case_id"]), reply)
    reply = Chat(http).say("Someone made a payment on my card that I did not make, this is fraud")
    check("a fraud report goes to a person (safety screen)", reply["decision"] == "handoff", reply)

    headers = manager(http)

    print("\n== TEST B: the agent uses Team C's published tool as the verified customer")
    before = len(mcp_executions(tools_http, artifact_id))
    alice = Chat(http)
    first = alice.say("Where is my order NS-20877?")
    reply = alice.say("01012345601")
    check("identity is verified first (Team B's own check), nothing read before", first["decision"] == "verify_identity", first)
    check("the order status comes back from Team C's tool", reply["decision"] == "answer" and "on its way" in reply["text"], reply)
    calls = tool_calls(http, alice.id, "get_order")
    check("the trace shows get_order served by Team C (audit id tools:<execution>)",
          bool(calls) and calls[-1]["status"] == "success" and str(calls[-1]["audit_id"]).startswith("tools:"), calls)  # fmt: skip
    runs = mcp_executions(tools_http, artifact_id)
    check("Team C recorded one MCP sandbox execution as alice (C-100)",
          len(runs) == before + 1 and runs[-1]["identity"] == "alice" and runs[-1]["status"] == "succeeded",
          [(r["identity"], r["status"]) for r in runs[before:]])  # fmt: skip

    bob = Chat(http)
    bob.say("Where is my order NS-20790?")
    reply = bob.say("01123456702")
    runs = mcp_executions(tools_http, artifact_id)
    check("another verified customer runs as their own sandbox identity (bob = C-101)",
          reply["decision"] == "answer" and runs[-1]["identity"] == "bob" and runs[-1]["status"] == "succeeded", reply)  # fmt: skip

    count = len(runs)
    stranger = Chat(http)
    stranger.say("Where is my order NS-20611?")
    reply = stranger.say("01234567803")
    calls = tool_calls(http, stranger.id, "get_order")
    check("a customer with no sandbox identity gets no data and nothing is sent to Team C",
          reply["decision"] != "answer" and len(mcp_executions(tools_http, artifact_id)) == count
          and bool(calls) and calls[-1]["error_code"] == "IDENTITY_NOT_MAPPED", (reply["decision"], calls[-1:] if calls else None))  # fmt: skip

    print("\n== TEST C: Team C's contract, seen from the real MCP server")
    direct = asyncio.run(direct_mcp(binding, tenant, tool_name))
    check("the published tool is listed", tool_name in direct["tools"], direct["tools"])
    extra = direct.get("extra_argument") or {}
    check("an extra argument (customer_id) is rejected before any request",
          extra.get("status") == "rejected" and any("customer_id" in e for e in (extra.get("error") or {}).get("errors", [])), extra)  # fmt: skip
    check("an unknown tool is JSON-RPC -32602 with data.code tool_not_published",
          direct.get("unknown_tool") == {"code": -32602, "data": {"code": "tool_not_published"}}, direct.get("unknown_tool"))  # fmt: skip

    print("\n== TEST C: disabling through Team B stops the tool at once")
    page = http.get("/v1/dashboard/business-tools", params={"tenant_id": T}).json()
    tools = {t["name"]: t for t in page["tools"]}
    check("the business-tools page shows get_order served by Team C, sandbox-only",
          page["sandbox_only"] and tools["get_order"]["source"] == "tools" and tools["get_order"]["status"] == "available", tools.get("get_order"))  # fmt: skip
    pub_id = state["publication_id"]
    r = http.post(f"/v1/dashboard/business-tools/publications/{pub_id}/disable", params={"tenant_id": T},
                  json={"note": "sandbox-only e2e: check the agent stops using it"}, headers=headers)  # fmt: skip
    after = r.json() if r.status_code == 200 else {}
    check("a manager disables the publication through Team B", r.status_code == 200, r.text[:300])
    check("Team B lists the tool as missing straight after",
          {t["name"]: t["status"] for t in after.get("tools", [])}.get("get_order") == "missing", after.get("tools"))  # fmt: skip
    old = asyncio.run(call_old_name(binding, tenant, tool_name))
    check("a client holding the old tool name is refused by Team C (tool_not_published)",
          old == {"rpc_error": -32602, "data": {"code": "tool_not_published"}}, old)  # fmt: skip
    count = len(mcp_executions(tools_http, artifact_id))
    late = Chat(http)
    late.say("Where is my order NS-20877?")
    reply = late.say("01012345601")
    check("the agent no longer reads orders through the disabled tool and hands over honestly",
          reply["decision"] == "handoff" and len(mcp_executions(tools_http, artifact_id)) == count, reply)  # fmt: skip

    print("\n== TEST C: publishing again is discovered on refresh")
    r = tools_http.post(f"/api/v1/artifacts/{artifact_id}/publications", json={"note": "sandbox-only e2e: publish the tested artifact again"})
    check("Team C publishes the same tested artifact again (new publication)", r.status_code == 200, r.text[:300])
    if r.status_code == 200:
        state["publication_id"] = r.json()["id"]
        (VAR / "lifecycle_order.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    page = http.post("/v1/dashboard/business-tools/refresh", params={"tenant_id": T}, headers=headers).json()
    check("Team B finds it again after a refresh",
          {t["name"]: t["status"] for t in page.get("tools", [])}.get("get_order") == "available", page.get("tools"))  # fmt: skip
    again = Chat(http)
    again.say("Where is my order NS-20877?")
    reply = again.say("01012345601")
    check("the agent answers from Team C again", reply["decision"] == "answer" and "on its way" in reply["text"], reply)

    ticket_state = VAR / "lifecycle_ticket.json"
    if "create_ticket" in tenant.get("tools", {}) and ticket_state.exists():
        writes(http, tools_http, json.loads(ticket_state.read_text(encoding="utf-8"))["artifact_id"])

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    (VAR / "e2e_chat_results.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
