"""Live Demo, end to end against a REAL external API: configure, check, generate, approve, build, test, publish, call.

Needs the tools service running with Live Demo switched on (ops/local/live_demo.env, see live_demo.example.env) and a model
provider for the tools service (services/tools/.env). Every owner decision is made through the tools service's own API and labelled LIVE DEMO
(owner decision recorded by ops/local/live_demo_check.py). Nothing here is simulated:

  1. a Live Demo business and the owner's OpenAPI file           6. the owner's identity (credential) for that API
  2. the owner's tool request; the model triages and proposes    7. The tools service's real connection check
  3. answers, access policy, reconciliation (model), approval    8. a live test: a real call, judged by the tools service
  4. a Live Demo connector to the real base URL (operator's      9. publish as live_demo (never production)
     TOOLS_LIVE_DEMO_HOSTS + owner confirmation)                       10. The tools service's real MCP server, through the agent's MCP client:
  5. the artifact build (records environment live_demo)              discover, call, compare with an independent read of
                                                                     the same API, invalid arguments, a real not-found,
                                                                     disable -> refused, republish (unless --leave-disabled)

    services/agent/.venv/Scripts/python.exe ops/local/live_demo_check.py               # GitHub's public repository read
    services/agent/.venv/Scripts/python.exe ops/local/live_demo_check.py --api custom  # your API, from ops/local/live_demo.env

The default API is GitHub's public REST API (unauthenticated reads of public repositories: real responses, no account,
no personal data; GitHub allows 60 such calls per hour per address). --api custom uses LIVE_DEMO_BASE_URL,
LIVE_DEMO_SPEC, LIVE_DEMO_GOAL, LIVE_DEMO_TEST_ARGUMENTS and the demo account's LIVE_DEMO_TOKEN (or USERNAME/PASSWORD).
Credentials are only written into the tools service's identity form; this script never prints them. Writes it never makes.
Progress is kept in ops/local/var/live_demo_<api>.json; --fresh starts over.
"""

import argparse
import asyncio
import json
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "ops" / "local"))
sys.path.insert(0, str(ROOT / "services" / "agent" / "src"))
from serve import live_settings  # noqa: E402
from tools_lifecycle import TOOLS_URL, Owner, step, wait_for_proposal  # noqa: E402

VAR = ROOT / "ops" / "local" / "var"
LABEL = "LIVE DEMO owner decision recorded by ops/local/live_demo_check.py:"
RESULTS: list[tuple[str, bool, str]] = []

APIS = {
    "github": dict(
        business="GitHub public data (LIVE DEMO)",
        description="Live Demo against GitHub's real public REST API: read public repository facts. Unauthenticated, read-only.",
        base_url="https://api.github.com",
        spec=ROOT / "ops" / "local" / "live_specs" / "github_public_repo.openapi.json",
        goal="Look up one public GitHub repository by its owner and name and return its full name, description, stars, "
        "forks, open issue count and default branch. Read only; change nothing.",
        policy=f"{LABEL} anyone may call this read-only tool (public principal, no authentication): it returns only public "
        "facts of public repositories. Any public repository may be read (no record ownership applies); GitHub itself "
        "answers 404 for private or missing repositories. No writes, no personal data, no administrator access.",
        test_arguments={"owner": "octocat", "repo": "Hello-World"},
        missing_arguments={"owner": "octocat", "repo": "this-repository-does-not-exist-ticketeasy"},
        independent=lambda args: f"https://api.github.com/repos/{args['owner']}/{args['repo']}",
        compare=("full_name", "default_branch", "description"),  # stable facts the approved outputs include
        scope="public",
    ),
}


def check(name: str, ok: bool, detail: Any = "") -> bool:
    RESULTS.append((name, ok, str(detail)[:300]))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{str(detail)[:300]}]" if detail and not ok else ""), flush=True)
    return ok


def custom_api() -> dict[str, Any]:
    env = live_settings()
    missing = [k for k in ("LIVE_DEMO_BASE_URL", "LIVE_DEMO_SPEC", "LIVE_DEMO_GOAL", "LIVE_DEMO_TEST_ARGUMENTS") if not env.get(k)]
    if missing:
        raise SystemExit("ops/local/live_demo.env needs: " + ", ".join(missing) + " (see ops/local/live_demo.example.env)")
    return dict(
        business="Live Demo business", description="Live Demo against the owner's real API.", base_url=env["LIVE_DEMO_BASE_URL"].rstrip("/"),
        spec=ROOT / env["LIVE_DEMO_SPEC"], goal=env["LIVE_DEMO_GOAL"],
        policy=f"{LABEL} only the dedicated demo account (an end user) may call this tool with its own credential, and only for "
        "its own records; no administrator access, no service account.",
        test_arguments=json.loads(env["LIVE_DEMO_TEST_ARGUMENTS"]), missing_arguments=None, independent=None, compare=(),
        scope="end_user", token=env.get("LIVE_DEMO_TOKEN", ""), username=env.get("LIVE_DEMO_USERNAME", ""),
        password=env.get("LIVE_DEMO_PASSWORD", ""),
    )


def owner_review(owner: Owner, pid: str, sid: str, api: dict[str, Any]) -> None:
    p = owner.get(f"/proposals/{pid}")
    answers = {q["id"]: api["policy"] for q in p["content"]["questions"]} | {r["id"]: api["policy"] for r in p.get("requirements", [])}
    if answers:
        owner.post(f"/proposals/{pid}/versions/{p['version']}/answers", {"expected_revision": p["review_revision"], "answers": answers})
    p = owner.get(f"/proposals/{pid}")
    owner.post(f"/proposals/{pid}/versions/{p['version']}/reconcile", {"expected_revision": p["review_revision"]})
    p = owner.get(f"/proposals/{pid}")
    public = api["scope"] == "public"
    scopes = [{"requirement_id": r["id"], "mode": "unrestricted", "reason": f"{LABEL} public data of any public repository"}
              for r in p["requirements"] if r["kind"] == "record_scope"] if public else []
    scopes += [{"requirement_id": r["id"], "mode": "unrestricted" if public else "api_filtered", "reason": f"{LABEL} see the answers"}
               for r in p["requirements"] if r["kind"] == "data_scope"]
    if not public and not (p.get("policy") or {}).get("accepted_at") and any(r["kind"] == "record_scope" for r in p["requirements"]):
        # Which response field proves ownership is the owner's decision; this script never guesses it.
        raise SystemExit(f"Accept the access policy (who may use the tool, which records) at {TOOLS_URL}/proposals/{pid}#structured-policy, "
                         "then run this script again: it continues from there.")
    if not (p.get("policy") or {}).get("accepted_at"):
        policy = {"principals": ["public"] if public else ["end_user", "resource_owner"], "authentication": "none" if public else "required",
                  "role_source": None, "roles": [], "permission_source": None, "permissions": [], "scopes": scopes, "checks": [],
                  "inputs": (p.get("policy_form") or {}).get("inputs", []), "financial": False, "irreversible": False,
                  "external_side_effects": False, "idempotent": True, "explanation": api["policy"]}  # fmt: skip
        owner.post(f"/proposals/{pid}/versions/{p['version']}/policy", {"expected_revision": p["review_revision"], "policy": policy})
    for i in range(1, 6):
        p = owner.get(f"/proposals/{pid}")
        if p["state"] == "ready_for_review":
            break
        last = (p.get("reconciliations") or [{}])[-1].get("result") or {}
        ids = {q["id"] for q in p["content"]["questions"]}
        open_findings = [f for f in last.get("findings", []) if f["status"] != "resolved" and f["question_id"] in ids]
        if open_findings:
            owner.post(f"/proposals/{pid}/versions/{p['version']}/answers",
                       {"expected_revision": p["review_revision"], "answers": {f["question_id"]: api["policy"] for f in open_findings}})
            p = owner.get(f"/proposals/{pid}")
        owner.post(f"/proposals/{pid}/versions/{p['version']}/reconcile", {"expected_revision": p["review_revision"]})
        print(f"  reconcile #{i}: {owner.get(f'/proposals/{pid}')['state']}")
    p = owner.get(f"/proposals/{pid}")
    if p["state"] != "ready_for_review":
        raise SystemExit(f"the proposal is {p['state']}; open {TOOLS_URL}/proposals/{pid}")
    for req in p.get("requirements", []):
        if req.get("status") == "answer_sufficient":
            p = owner.get(f"/proposals/{pid}")
            owner.post(f"/proposals/{pid}/versions/{p['version']}/requirements/{req['id']}/confirm", {"expected_revision": p["review_revision"]})
    p = owner.get(f"/proposals/{pid}")
    owner.post(f"/proposals/{pid}/versions/{p['version']}/decisions",
               {"expected_revision": p["review_revision"], "action": "approve_to_build", "reason": f"{LABEL} approved for the Live Demo",
                "idempotency_key": secrets.token_urlsafe(16)})  # fmt: skip


def save_identity(owner: Owner, aid: str, api: dict[str, Any]) -> None:
    form = {"artifact_id": aid, "identity_name": "demo", "scope": api["scope"]}
    for key in ("token", "username", "password"):
        if api.get(key):
            form[key] = api[key]
    owner.form(f"/artifacts/{aid}", "/actions/save_test_identity", form)


async def through_mcp(state: dict[str, Any], api: dict[str, Any]) -> dict[str, Any]:
    """The tools service's real MCP server, through the agent's own MCP client (the path the assistant uses)."""
    from agent_service.adapters.tools_mcp_client import McpRemoteError, McpStdioSession

    python = ROOT / "services" / "tools" / ".venv" / ("Scripts/python.exe" if (ROOT / "services" / "tools" / ".venv" / "Scripts").exists() else "bin/python")
    env = {"PYTHONPATH": ".", **{k: v for k, v in live_settings().items() if k.startswith("LIVE_DEMO_") and k in (
        "TOOLS_LIVE_DEMO_ENABLED", "TOOLS_LIVE_DEMO_HOSTS", "TOOLS_LIVE_DEMO_PRIVATE_HOSTS", "TOOLS_LIVE_DEMO_CALLS_PER_MINUTE")}}  # fmt: skip
    session = McpStdioSession([str(python), "-m", "tools_service.mcp_server", "--business-id", state["business_id"], "--identity", "demo",
                               "--database", "data/integration.sqlite3"], cwd=str(ROOT / "services" / "tools"), env=env)  # fmt: skip
    await session.start()
    out: dict[str, Any] = {}
    try:
        tools = await session.list_tools(timeout_s=30)
        out["tools"] = tools
        mine = next((t for t in tools if t["name"] == state["tool_name"]), None)
        if mine is not None:
            out["call"] = (await session.call_tool(mine["name"], api["test_arguments"], timeout_s=60)).get("structuredContent")
            out["extra"] = (await session.call_tool(mine["name"], {**api["test_arguments"], "identity": "someone-else"}, timeout_s=60)).get("structuredContent")
            if api.get("missing_arguments"):
                out["missing"] = (await session.call_tool(mine["name"], api["missing_arguments"], timeout_s=60)).get("structuredContent")
        if state.get("disable_then_call"):
            try:
                out["after_disable"] = await session.call_tool(state["tool_name"], api["test_arguments"], timeout_s=60)
            except McpRemoteError as exc:
                out["after_disable"] = {"rpc_error": exc.code, "data": exc.data}
    finally:
        session.close()
    return out


def independent_read(api: dict[str, Any]) -> dict[str, Any] | None:
    if not api.get("independent"):
        return None
    request = urllib.request.Request(api["independent"](api["test_arguments"]), headers={"Accept": "application/json", "User-Agent": "ticketeasy-live-demo-check"})
    try:
        with urllib.request.urlopen(request, timeout=15) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", choices=["github", "custom"], default="github")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--leave-disabled", action="store_true", help="do not republish after the disable check")
    args = parser.parse_args()
    api = APIS["github"] if args.api == "github" else custom_api()
    path = VAR / f"live_demo_{args.api}.json"
    state: dict[str, Any] = {} if args.fresh or not path.exists() else json.loads(path.read_text(encoding="utf-8"))
    save = lambda: (VAR.mkdir(parents=True, exist_ok=True), path.write_text(json.dumps(state, indent=2), encoding="utf-8"))  # noqa: E731
    owner = Owner()

    if "business_id" not in state:
        step("1. Live Demo business and the owner's OpenAPI")
        biz = owner.post("/businesses", {"name": api["business"], "description": api["description"]})
        spec = owner.post(f"/businesses/{biz['id']}/specifications", files={"file": ("openapi.json", api["spec"].read_bytes(), "application/json")})
        state.update(business_id=biz["id"], spec_id=spec["id"])
        save()
        print(f"business {biz['id']}: {spec['inventory']['eligible_operation_count']} eligible operation(s)")
    if "proposal_id" not in state:
        step("2. The owner's tool request; the model proposes")
        req = owner.post(f"/businesses/{state['business_id']}/tool-requests",
                         {"goal": api["goal"], "examples": "", "spec_id": state["spec_id"], "idempotency_key": secrets.token_urlsafe(16)})  # fmt: skip
        if req["status"] == "needs_clarification":
            owner.post(f"/tool-requests/{req['id']}/clarifications", {"text": api["policy"]})
            req = owner.get(f"/tool-requests/{req['id']}")
        ids = req.get("proposal_ids") or req.get("existing_proposal_ids") or []
        if not ids:
            raise SystemExit(f"no proposal ({req['status']}); open {TOOLS_URL}/businesses/{state['business_id']}")
        state["proposal_id"] = ids[0]
        save()
    pid = state["proposal_id"]
    p = wait_for_proposal(owner, pid)
    print(f"proposal {pid}: {p['content']['name']} ({p['state']})")
    if p["state"] != "approved_to_build":
        step("3. Owner review and approval")
        owner_review(owner, pid, state["spec_id"], api)
        print("approved to build")

    if "connector_id" not in state:
        step("4. Live Demo connector (operator allowlist + owner confirmation)")
        r = owner.http.post(f"/api/v1/proposals/{pid}/connectors", json=dict(base_url=api["base_url"], environment="live_demo", confirmed=True))
        if r.status_code != 200:
            raise SystemExit(f"the Live Demo connector was refused: {r.text[:400]}\nSet TOOLS_LIVE_DEMO_ENABLED=true and add the API's host:port to "
                             "TOOLS_LIVE_DEMO_HOSTS in ops/local/live_demo.env, then restart Team C.")
        state["connector_id"] = r.json()["id"]
        save()
    check("the connector is a Live Demo connector to the real API", state["connector_id"].startswith("live-"), state["connector_id"])

    if "artifact_id" not in state:
        step("5. Build on the Live Demo connector")
        a = owner.post(f"/proposals/{pid}/artifacts", {"connector_id": state["connector_id"]})
        state["artifact_id"] = a["id"]
        save()
    aid = state["artifact_id"]
    content = owner.get(f"/artifacts/{aid}")["content"]
    check("the artifact records environment live_demo (part of its hash) and no production activation",
          content["connector"].get("environment") == "live_demo" and content["activation"]["activated"] is False, content["connector"])  # fmt: skip

    step("6-7. The owner's identity, then Team C's real connection check")
    save_identity(owner, aid, api)
    result = owner.post(f"/connectors/{state['connector_id']}/check")
    for c in result["checks"]:
        print(f"   {c['check']:<15} {c['status']:<8} {c['detail']}")
    checks = {c["check"]: c for c in result["checks"]}
    check("connection check: configuration allowed and the real API answered",
          checks["configuration"]["status"] == "ok" and checks["reachable"]["status"] == "ok", result["checks"][:2])  # fmt: skip

    if "publication_id" not in state:
        step("8. A live test: one real call, judged by Team C")
        owner.post(f"/artifacts/{aid}/policy-tests", {})
        test = owner.post(f"/artifacts/{aid}/sandbox-tests", {"name": "live_demo_read", "identity": "demo", "arguments": api["test_arguments"],
                                                             "expect": {"status": "succeeded"}, "scenario": "general"})  # fmt: skip
        check("the live test against the real API passed", test["verdict"] == "passed", test)
        step("9. Publish as Live Demo")
        r = owner.http.post(f"/api/v1/artifacts/{aid}/publications", json={"note": f"{LABEL} Live Demo publication", "environment": "live_demo"})
        if r.status_code != 200:
            raise SystemExit("publication refused by Team C: " + r.text[:800])
        pub = r.json()
        state.update(publication_id=pub["id"], tool_name=pub["tool_name"])
        save()
    pub = owner.get(f"/publications/{state['publication_id']}")
    check("the publication is live_demo, executable now, and not production",
          pub["environment"] == "live_demo" and pub["execution_enabled"] and pub["production_ready"] is False and pub["production_activation"] is False, pub)  # fmt: skip

    step("10. Team C's real MCP server through Team B's MCP client")
    seen = asyncio.run(through_mcp(state, api))
    tool = next((t for t in seen["tools"] if t["name"] == state["tool_name"]), None)
    meta = ((tool or {}).get("_meta") or {}).get("team_c", {})
    check("the tool is discovered with environment live_demo and its real API host",
          meta.get("environment") == "live_demo" and meta.get("api_host") == api["base_url"].split("//", 1)[1], meta)  # fmt: skip
    text = json.dumps(seen["tools"])
    check("no credential appears in the model-visible tool definitions",
          all(not api.get(k) or api[k] not in text for k in ("token", "password")) and "authorization" not in text.lower(), "")  # fmt: skip
    call = seen.get("call") or {}
    check("the MCP call ran against the real API and succeeded", call.get("status") == "succeeded" and call.get("environment") == "live_demo", call)
    truth = independent_read(api)
    if truth is not None and call.get("output"):
        output = call["output"]
        same = {k: (output.get(k), truth.get(k)) for k in api["compare"] if k in output}
        check("the tool's output matches an independent read of the same API",
              bool(same) and all(a == b for a, b in same.values()), same)  # fmt: skip
    extra = seen.get("extra") or {}
    check("an argument choosing an identity is rejected before any request",
          extra.get("status") == "rejected" and (extra.get("error") or {}).get("category") == "invalid_arguments", extra.get("error"))  # fmt: skip
    if "missing" in seen:
        missing = seen["missing"] or {}
        check("a real 404 from the API is a failed call with category not_found (never a success)",
              missing.get("status") == "failed" and (missing.get("error") or {}).get("category") == "not_found", missing.get("error"))  # fmt: skip

    step("11. Disable: the next call is refused")
    owner.post(f"/publications/{state['publication_id']}/disable", {"note": f"{LABEL} disable check"})
    after = asyncio.run(through_mcp(dict(state, disable_then_call=True), api))
    check("a disabled Live Demo tool is gone from the list and a call by its old name is refused (-32602 tool_not_published)",
          state["tool_name"] not in {t["name"] for t in after["tools"]}
          and after.get("after_disable") == {"rpc_error": -32602, "data": {"code": "tool_not_published"}}, after.get("after_disable"))  # fmt: skip
    if not args.leave_disabled:
        r = owner.http.post(f"/api/v1/artifacts/{aid}/publications", json={"note": f"{LABEL} republished after the disable check", "environment": "live_demo"})
        if r.status_code == 200:
            state["publication_id"] = r.json()["id"]
            save()
        check("republished for the demo", r.status_code == 200, r.text[:200])

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed  (REAL API: {api['base_url']})")
    (VAR / f"live_demo_{args.api}_results.json").write_text(json.dumps({"api": api["base_url"], "at": time.time(), "results": RESULTS}, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
