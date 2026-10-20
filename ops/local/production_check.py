"""Production, end to end, with REAL services: a tested tool is promoted by an admin and a verified customer uses it (LOCAL deployment).

The "production" API here is the Nile shop started as a SEPARATE deployment on this machine (ops/local/serve.py nile-prod, port 8104,
synthetic data), with ONE service token that may act for any customer, which is how a real API treats a back-office credential. It is
NOT a real production API; it proves the part that matters: nothing in the API keeps customers apart, the tools service does. To start it copy
ops/local/production.example.env to ops/local/production.env and restart the services (serve.py up). Run the phases in order:

  services/agent/.venv/Scripts/python.exe ops/local/production_check.py --phase promote   # The tools service only: connector, promotion, the real production MCP process
  services/agent/.venv/Scripts/python.exe ops/local/production_check.py --phase bind      # write the agent's binding for production; then RESTART the agent
  services/agent/.venv/Scripts/python.exe ops/local/production_check.py --phase chat      # the customer chat, the dashboard promote/rollback, rollback

Needs: sandbox, nile-prod, the tools service (with the PRODUCTION_* settings), the knowledge service and the agent running, and the sandbox lifecycle done for the order and
ticket tools (ops/local/tools_lifecycle.py), whose sandbox-published, tested artifacts are what gets promoted. Every owner decision is
labelled sandbox-only. Credentials are read from ops/local/var and never printed. Prints PASS/FAIL per check; exits 1 when any failed.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
VAR = ROOT / "ops" / "local" / "var"
sys.path.insert(0, str(ROOT / "ops" / "local"))
sys.path.insert(0, str(ROOT / "services" / "agent" / "src"))
import serve  # noqa: E402
import tools_lifecycle as lifecycle  # noqa: E402
from e2e_chat import RESULTS, Chat, check  # noqa: E402

AGENT_URL, TOOLS_URL, NILE_PROD = "http://127.0.0.1:8010", "http://127.0.0.1:8101", "http://127.0.0.1:8104"
T = "shop_001"
ACTOR = "Production check (ops/local/production_check.py, sandbox-only)"
CONTACT = "oncall@example.com"
ALICE_ORDER, ALICE_PHONE = "NS-20877", "01012345601"


def read(name: str) -> str:
    path = VAR / name
    if not path.exists():
        raise SystemExit(f"ops/local/var/{name} is missing: copy ops/local/production.example.env to ops/local/production.env and restart (serve.py up)")
    return path.read_text(encoding="utf-8").strip()


def team_c() -> httpx.Client:
    return httpx.Client(base_url=TOOLS_URL + "/api/v1", headers={"x-team-c-admin-token": read("team_c_admin.key")}, timeout=300, trust_env=False)


def prod_get(path: str) -> httpx.Response:
    return httpx.get(NILE_PROD + path, headers={"Authorization": "Bearer " + read("prod_service.key")}, timeout=10, trust_env=False)


def prod_state() -> dict[str, Any]:
    return httpx.get(NILE_PROD + "/_harness/state", headers={"x-harness-key": read("nile_prod_harness.key")}, timeout=10, trust_env=False).json()


def production_executions(tc: httpx.Client, artifact_id: str) -> list[dict[str, Any]]:
    return [e for e in tc.get(f"/artifacts/{artifact_id}").json()["executions"] if e.get("mode") == "mcp_production"]


def promote_phase() -> None:
    tc = team_c()
    states = {t: lifecycle.load_state(False, t) for t in ("order", "ticket")}
    if not all("publication_id" in s for s in states.values()):
        raise SystemExit("run the sandbox lifecycle for the order and ticket tools first (ops/local/tools_lifecycle.py --tool order|ticket)")
    business = states["order"]["business_id"]
    check("the services are up (nile-prod, Team C)", httpx.get(NILE_PROD + "/health", timeout=5, trust_env=False).status_code == 200 and tc.get("/businesses").status_code == 200)
    no_token = httpx.post(f"{TOOLS_URL}/api/v1/artifacts/{states['order']['artifact_id']}/promotions", json={}, timeout=10, trust_env=False)
    check("production actions are refused without the operator's admin token", no_token.status_code == 403, no_token.text[:200])

    for old in tc.get(f"/businesses/{business}/promotions").json():  # a clean slate: promotions an earlier run left active
        if old["status"] == "active":
            tc.post(f"/promotions/{old['id']}/rollback", json=dict(actor=ACTOR, note="sandbox-only: clean slate before a new production check"))
    r = tc.post(f"/proposals/{states['order']['proposal_id']}/connectors", json=dict(
        base_url=NILE_PROD, context_fields=["customer_id"], environment="production", confirmed=True, secret_ref="file:" + str(VAR / "prod_service.key")))
    check("a production connector is saved with a secret REFERENCE (the credential never enters Team C's database)", r.status_code == 200 and r.json()["environment"] == "production", r.text[:300])
    connector = r.json()["id"]
    leak = read("prod_service.key")
    check("the connector listing shows the reference and that it is available, never the credential",
          r.json().get("secret_set") is True and leak not in json.dumps(tc.get(f"/businesses/{business}/connectors").json()))
    check_result = tc.post(f"/connectors/{connector}/check")
    parts = {c["check"]: c["status"] for c in check_result.json().get("checks", [])}
    check("Team C's connection check reaches the production API and finds the secret", parts.get("reachable") == "ok" and parts.get("secret") == "ok", parts)

    promoted: dict[str, dict[str, Any]] = {}
    for tool, state in states.items():
        write = tool == "ticket"
        refused = tc.post(f"/artifacts/{state['artifact_id']}/promotions", json=dict(actor=ACTOR, note="sandbox-only", rollback_contact=CONTACT, accept_no_idempotency=False,
                                                                                      production_connector_id=connector))
        if write:
            check("a write is refused until an admin explicitly accepts that the API has no idempotency",
                  refused.status_code == 409 and refused.json()["code"] == "promotion_refused" and "idempotency" in refused.json()["message"], refused.text[:300])
        done = refused if refused.status_code == 200 else tc.post(f"/artifacts/{state['artifact_id']}/promotions", json=dict(
            actor=ACTOR, note=f"sandbox-only: promote the {tool} tool for the local production check", rollback_contact=CONTACT, accept_no_idempotency=True, production_connector_id=connector))
        ok = done.status_code == 200 and done.json()["status"] == "active" and all(v["ok"] for v in done.json()["checklist"].values())
        check(f"the {tool} tool is promoted: every checklist item passes, including the smoke check", ok, done.text[:500])
        if ok:
            promoted[tool] = done.json()
            (VAR / f"production_{tool}.json").write_text(json.dumps(dict(promotion=done.json()["id"], business_id=business, connector=connector), indent=2), encoding="utf-8")
    if len(promoted) < 2:
        return

    # the REAL production MCP process, started the way the agent starts it
    binding = json.loads((VAR / "team_c.json").read_text(encoding="utf-8"))
    serve.sync_live_settings(VAR / "team_c.json")
    binding = json.loads((VAR / "team_c.json").read_text(encoding="utf-8"))
    asyncio.run(mcp_phase(tc, binding, business, promoted))


async def mcp_phase(tc: httpx.Client, binding: dict[str, Any], business: str, promoted: dict[str, dict[str, Any]]) -> None:
    from agent_service.adapters.tools_mcp_client import McpRemoteError, McpStdioSession

    base = [*binding["command"], "--business-id", business, "--database", binding["database"]]
    if binding.get("sandbox_hosts"):
        base += ["--sandbox-hosts", binding["sandbox_hosts"]]
    production = McpStdioSession([*base, "--identity", "service", "--context-from-client"], cwd=binding["cwd"], env=binding.get("env"))
    ordinary = McpStdioSession([*base, "--identity", "alice"], cwd=binding["cwd"], env=binding.get("env"))
    await production.start()
    await ordinary.start()
    try:
        tools = {t["name"]: t for t in await production.list_tools(timeout_s=60)}
        names = {tool: promoted[tool]["production_artifact_id"] for tool in promoted}
        meta = {n: (t.get("_meta") or t.get("meta") or {}).get("team_c", {}) for n, t in tools.items()}
        check("the production server lists exactly the promoted tools, marked production_ready", len(tools) == 2 and all(m.get("environment") == "production" and m.get("production_ready") is True for m in meta.values()), list(meta.values()))
        sandbox_tools = {t["name"] for t in await ordinary.list_tools(timeout_s=60)}
        check("an ordinary (identity) server does not list or run the production tools", not (set(tools) & sandbox_tools))
        by_artifact = {m["artifact_id"]: n for n, m in meta.items()}
        order_tool, ticket_tool = by_artifact.get(names["order"]), by_artifact.get(names["ticket"])
        if not (order_tool and ticket_tool):
            check("both promoted tools are listed by the production server", False, list(by_artifact))
            return
        ctx = lambda customer, **extra: {"ticketeasy": {"context": {"customer_id": customer}, **extra}}  # noqa: E731

        async def call(tool: str, args: dict[str, Any], meta_value: Any = None) -> dict[str, Any]:
            try:
                return (await production.call_tool(tool, args, timeout_s=90, meta=meta_value)).get("structuredContent") or {}
            except McpRemoteError as exc:
                return {"rpc_error": exc.code, "data": exc.data}

        mine = await call(order_tool, {"order_id": ALICE_ORDER}, ctx("C-100"))
        independent = prod_get(f"/api/orders/{ALICE_ORDER}").json()
        check("a verified customer reads their own order through the service credential, and it matches an independent read",
              mine.get("status") == "succeeded" and mine.get("environment") == "production" and mine.get("production_ready") is True
              and independent["order_status"] in json.dumps(mine["output"]) and independent["customer_id"] in json.dumps(mine["output"]), mine)
        stolen = await call(order_tool, {"order_id": ALICE_ORDER}, ctx("C-101"))
        check("the API ITSELF would have given another customer that order (service token)", prod_get(f"/api/orders/{ALICE_ORDER}").status_code == 200)
        check("...but Team C refuses it for the other customer: blocked_by_access_check, no data returned",
              stolen.get("status") == "failed" and stolen["error"]["code"] == "blocked_by_access_check" and stolen.get("output") is None, stolen)
        check("a call with no context is rejected before any request", (await call(order_tool, {"order_id": ALICE_ORDER}))["error"]["code"] == "context_missing")
        undeclared = await call(order_tool, {"order_id": ALICE_ORDER}, {"ticketeasy": {"context": {"customer_id": "C-100", "role": "admin"}}})
        check("context with an undeclared field is rejected", undeclared["error"]["code"] == "context_undeclared", undeclared)
        extra = await call(order_tool, {"order_id": ALICE_ORDER, "customer_id": "C-101"}, ctx("C-100"))
        check("a customer id smuggled in as an argument is rejected", extra["error"]["category"] == "invalid_arguments", extra)
        try:
            await ordinary.call_tool(order_tool, {"order_id": ALICE_ORDER}, timeout_s=60, meta=ctx("C-100"))
            refused_here = False
        except McpRemoteError as exc:
            refused_here = (exc.data or {}).get("code") == "tool_not_published"
        check("an ordinary server refuses the production tool by name (tool_not_published), ignoring any context", refused_here)

        before = len(prod_state()["tickets"])
        args = {"order_id": ALICE_ORDER, "subject": "sandbox-only production check", "description": "Filed by ops/local/production_check.py"}
        ticket_args = {k: v for k, v in args.items() if k in (tools[ticket_tool].get("inputSchema") or tools[ticket_tool].get("input_schema") or {}).get("properties", {})}
        filed = await call(ticket_tool, ticket_args, ctx("C-100", idempotency_key="prodcheck-0123456789abcdef"))
        state = prod_state()["tickets"]
        check("a write for the verified customer's own order succeeds and exactly one ticket exists for C-100",
              filed.get("status") == "succeeded" and len(state) == before + 1 and state[-1]["customer_id"] == "C-100" and state[-1]["order_id"] == ALICE_ORDER, (filed.get("status"), state[before:]))
        denied = await call(ticket_tool, ticket_args, ctx("C-101"))
        check("the same write for another customer is refused before it is sent: no new ticket",
              denied.get("status") == "failed" and denied["error"]["code"] == "blocked_by_access_check" and len(prod_state()["tickets"]) == before + 1, denied)
        rows = production_executions(tc, names["ticket"]) + production_executions(tc, names["order"])
        check("Team C recorded every production call as the service identity, with a context hash and no customer value",
              len(rows) >= 5 and all(r["identity"] == "service" and "C-100" not in json.dumps(r["report"]) for r in rows)
              and all(r["report"].get("context_sha256") for r in rows if r["report"].get("code") != "context_missing"))
        audit = list((VAR / "audit").glob("production-*.jsonl"))
        text = "".join(p.read_text(encoding="utf-8") for p in audit)
        check("the append-only audit log has one line per call: ids and hashes, no values", len(text.splitlines()) >= 5 and "C-100" not in text and read("prod_service.key") not in text)
    finally:
        production.close()
        ordinary.close()


def bind_phase() -> None:
    path = VAR / "team_c.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    tenant = config["tenants"][T]
    for tool, key in (("get_order", "order"), ("create_ticket", "ticket")):
        binding = tenant["tools"][tool]
        state = json.loads((VAR / f"production_{key}.json").read_text(encoding="utf-8"))
        binding["environments"] = ["production"]
        tenant["business_id"] = state["business_id"]
    tenant["production_context_field"] = "customer_id"
    serve.sync_live_settings(path)
    config = json.loads(path.read_text(encoding="utf-8"))
    config["tenants"][T] = tenant
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print("Team B's binding now serves get_order and create_ticket from PRODUCTION only. RESTART Team B, then run --phase chat.")


def admin(http: httpx.Client) -> dict[str, str]:
    """Sign in as a test admin (created once; its generated password lives in ops/local/var, never printed)."""
    import secrets as _secrets

    key = VAR / "e2e_admin.key"
    if not key.exists():
        key.write_text(_secrets.token_urlsafe(18), encoding="utf-8")
    password = key.read_text(encoding="utf-8").strip()
    python = ROOT / "services" / "agent" / ".venv" / "Scripts" / "python.exe"
    env = dict(os.environ, TEAM_B_STORE="sqlite", TEAM_B_DB_PATH=str(VAR / "team_b.sqlite3"))
    base = [str(python if python.exists() else sys.executable), "-m", "agent_service", "create-user", "--email", "e2e-admin@example.com",
            "--name", "E2E Admin", "--role", "admin", "--tenants", T, "--password", password]  # fmt: skip
    for extra in ("--if-missing", "--reset-password"):
        subprocess.run([*base, extra], cwd=ROOT / "services" / "agent", env=env, capture_output=True, check=False)
    r = http.post("/v1/auth/login", json={"email": "e2e-admin@example.com", "password": password})
    r.raise_for_status()
    return {"X-CSRF-Token": r.json()["csrf_token"]}


def chat_phase() -> None:
    tc = team_c()
    http = httpx.Client(base_url=AGENT_URL, timeout=300, trust_env=False)
    csrf = admin(http)
    tools = http.get("/v1/dashboard/business-tools", params={"tenant_id": T}).json()
    served = {t["name"]: t for t in tools["tools"] if t["source"] == "team_c"}
    check("the Business tools page shows get_order and create_ticket served as PRODUCTION",
          all(served.get(n, {}).get("environment") == "production" and served[n]["status"] == "available" for n in ("get_order", "create_ticket")), served)
    check("...with a banner count and no 'sandbox only' claim", tools["production_tools"] == 2 and tools["sandbox_only"] is False, {k: tools[k] for k in ("production_tools", "sandbox_only")})
    promotions = {p["id"]: p for p in tools["promotions"] if p["status"] == "active"}
    check("...and the active promotions with who decided them and their checklists", len(promotions) >= 2 and all(p["actor"] and p["checklist"] for p in promotions.values()))

    print("\n== a verified customer asks about their own order (production tool, service credential)")
    before = {t: len(production_executions(tc, promoted_artifact(tc, t))) for t in ("order", "ticket")}
    chat = Chat(http)
    first = chat.say(f"where is my order {ALICE_ORDER}")
    verified = chat.say(ALICE_PHONE) if first["decision"] == "verify_identity" else first
    check("the order status comes back from the production tool", verified["decision"] == "answer" and ("on its way" in verified["text"] or "shipped" in verified["text"].lower()), verified["text"][:200])
    runs = production_executions(tc, promoted_artifact(tc, "order"))
    check("Team C ran it once in production as the service identity", len(runs) == before["order"] + 1 and runs[-1]["identity"] == "service" and runs[-1]["status"] == "succeeded", [(r["identity"], r["status"]) for r in runs[before["order"]:]])

    print("\n== another customer asks for that order: Team B's ownership check, and Team C's if it were bypassed")
    other = Chat(http)
    other.say(f"where is my order {ALICE_ORDER}")
    stolen = other.say("01012345602")  # bob's phone (C-101) against alice's order
    check("the other customer gets no order data", "on its way" not in stolen["text"] and "Linen" not in stolen["text"], stolen["text"][:200])
    check("...and production was never asked to run it for them", len(production_executions(tc, promoted_artifact(tc, "order"))) in (before["order"] + 1, before["order"] + 2))

    print("\n== a write: a complaint becomes one ticket in the production shop, after the customer confirms")
    tickets = len(prod_state()["tickets"])
    wc = Chat(http)
    wc.say(f"I have a complaint about order {ALICE_ORDER}, the parcel arrived late")
    confirm = wc.say(ALICE_PHONE)
    check("the complaint is confirmed with the customer before anything is written", confirm["decision"] == "confirm", confirm["text"][:200])
    done = wc.say("yes")
    state = prod_state()["tickets"]
    check("after the yes exactly one ticket exists in the production shop, for C-100", len(state) == tickets + 1 and state[-1]["customer_id"] == "C-100", state[tickets:])
    check("the agent reports it done with the shop's own ticket reference", done["decision"] == "execute" and state[-1]["ticket_id"] in done["text"], done["text"][:200])

    print("\n== rollback through Team B (admin): the next call is refused and the agent stops using the tool")
    ids = [p["id"] for p in promotions.values()]
    for pid in ids:
        r = http.post(f"/v1/dashboard/business-tools/promotions/{pid}/rollback", params={"tenant_id": T}, json={"note": "sandbox-only rollback of the production check"}, headers=csrf)
        check(f"promotion {pid[:8]} is rolled back", r.status_code == 200, r.text[:200])
    after = http.post("/v1/dashboard/business-tools/refresh", params={"tenant_id": T}, headers=csrf).json()
    check("Team B no longer lists the tools as available", {t["name"]: t["status"] for t in after["tools"] if t["source"] == "team_c"}.get("get_order") != "available")
    count = len(production_executions(tc, promoted_artifact(tc, "order")))
    gone = Chat(http)
    gone.say(f"where is my order {ALICE_ORDER}")
    reply = gone.say(ALICE_PHONE)
    check("the customer is not served by production any more", len(production_executions(tc, promoted_artifact(tc, "order"))) == count and "on its way" not in reply["text"], reply["text"][:200])
    check("Team C shows the promotions as rolled back, with who and why",
          all(tc.get(f"/promotions/{pid}").json()["status"] == "rolled_back" and tc.get(f"/promotions/{pid}").json()["rolled_back_by"] == "E2E Admin" for pid in ids))


def promoted_artifact(tc: httpx.Client, tool: str) -> str:
    state = json.loads((VAR / f"production_{tool}.json").read_text(encoding="utf-8"))
    return str(tc.get(f"/promotions/{state['promotion']}").json()["production_artifact_id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--phase", choices=["promote", "bind", "chat"], required=True)
    args = parser.parse_args()
    {"promote": promote_phase, "bind": bind_phase, "chat": chat_phase}[args.phase]()
    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed  (LOCAL production-like deployment, NOT a real production API)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
