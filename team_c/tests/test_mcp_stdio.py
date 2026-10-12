"""Real MCP client over stdio against the real server process, for both service-desk variants.

REAL: the official SDK client launches `python -m team_c.mcp_server`; the fixture runs as a separate
uvicorn process and the executor reaches it over HTTP. Backend state is read through the fixture's
harness routes, independently of Team C.
AUTHORED / TEST-ONLY: proposals come from the authored test substitute (no model call), every review
and publication is a labeled TEST-ONLY decision, and all records live in a temporary database.
"""
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
import anyio
import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError
from conftest import ROOT
from fixtures.service_desk.app import VARIANTS
from team_c import publishing
from team_c.config import Settings
from team_c.web import create_app
from helpers.desk import KEY, LABEL, arguments, current, generated, review_and_build
from helpers.publication import RevisingDesk, publish, revise, with_evidence


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(params=["base", "renamed"])
def desk(request, tmp_path):
    variant, port = request.param, free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, FIXTURE_VARIANT=variant, FIXTURE_HARNESS_KEY=KEY, PYTHONPATH=str(ROOT))
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "fixtures.service_desk.app:create_app", "--factory", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"], env=env, cwd=str(ROOT))
    backend, h = httpx.Client(base_url=base, timeout=10, trust_env=False), {"x-harness-key": KEY}
    for _ in range(100):
        try:
            if backend.get("/health").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)
    n = VARIANTS[variant]
    ctx = n["holder_id"]
    people = {name: backend.post("/_harness/accounts", json=dict(name=name), headers=h).json() for name in ("alice", "bob")}
    booking = {name: backend.post("/_harness/bookings", json=dict(account_id=a["id"]), headers=h).json() for name, a in people.items()}
    settings = Settings(_env_file=None, database_path=str(tmp_path / "mcp.db"), session_secret="test-secret",
                        connectors_file=str(tmp_path / "connectors.json"), sandbox_hosts=f"127.0.0.1:{port}")
    app = create_app(settings, RevisingDesk)
    app.state.service.providers.names = n
    client = TestClient(app).__enter__()
    biz = client.post("/api/v1/businesses", json=dict(name=f"Service desk ({variant}, MCP test)", description="Customers report problems with their bookings")).json()
    spec = client.post(f'/api/v1/businesses/{biz["id"]}/specifications', files={"file": ("openapi.json", backend.get("/openapi.json").content)}).json()
    identities = {name: dict(token=a["token"], scope="end_user", context={ctx: a["number"] if n["holder_int"] else a["id"]}) for name, a in people.items()}
    with open(settings.connectors_file, "w", encoding="utf-8") as f:
        json.dump(dict(connectors={"desk": dict(business_id=biz["id"], base_url=base, sandbox=True, context_fields=[ctx], identities=identities)}), f)
    d = SimpleNamespace(client=client, spec=spec, n=n, people=people, booking=booking, ctx=ctx, biz=biz["id"], settings=settings,
                        requests=lambda: backend.get("/_harness/state", headers=h).json()["requests"])
    yield d
    client.__exit__(None, None, None)
    proc.kill()
    proc.wait()


@asynccontextmanager
async def session(d, who):
    params = StdioServerParameters(command=sys.executable, cwd=str(ROOT), env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                   args=["-m", "team_c.mcp_server", "--business-id", d.biz, "--identity", who, "--database", d.settings.database_path,
                                         "--connectors", d.settings.connectors_file, "--sandbox-hosts", d.settings.sandbox_hosts])
    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(params))
        s = await stack.enter_async_context(ClientSession(read, write))
        await s.initialize()
        yield s


async def names(s):
    return [t.name for t in (await s.list_tools()).tools]


async def unpublished(s, name, args):
    with pytest.raises(MCPError) as exc:
        await s.call_tool(name, args)
    assert exc.value.error.data == {"code": "tool_not_published"}


def test_published_artifact_through_the_real_sdk_client(desk):
    d = desk
    pid = generated(d)
    a = review_and_build(d, pid)
    name = publishing.tool_name(a["id"], a["content"])
    alice_booking = arguments(d, "alice")

    async def scenario():
        async with session(d, "alice") as alice:
            # Built and approved is not published: nothing is listed and a call by name is refused.
            assert await names(alice) == []
            await unpublished(alice, name, alice_booking)
            assert publish(d, a["id"]).json()["code"] == "publication_not_allowed"
            with_evidence(d, a["id"])
            pub = publish(d, a["id"]).json()
            # Re-listing picks up the new publication without restarting the server.
            [tool] = (await alice.list_tools()).tools
            assert tool.name == name and tool.title == a["content"]["name"] and tool.annotations.read_only_hint is False
            assert tool.input_schema == publishing.json_schema(a["content"]["input_schema"])
            assert set(tool.input_schema["properties"]) == {"booking_reference", "category", "details"} and tool.input_schema["additionalProperties"] is False
            assert tool.meta["team_c"]["artifact_sha256"] == a["sha256"] and tool.meta["team_c"]["production_ready"] is False
            assert tool.output_schema["properties"]["output"]["anyOf"][0]["properties"] == {"ticket": a["content"]["output_schema"]["properties"]["ticket"]}

            before = len(d.requests())
            ok = await alice.call_tool(name, alice_booking)
            filed = d.requests()
            assert ok.is_error is False and len(filed) == before + 1
            assert ok.structured_content["output"] == {"ticket": filed[-1]["ticket"]} and filed[-1]["filed_by"] == d.people["alice"]["id"]
            assert ok.structured_content["artifact"]["proposal_version"] == 1 and ok.structured_content["execution_id"]

            missing = await alice.call_tool(name, {k: v for k, v in alice_booking.items() if k != "category"})
            extra = await alice.call_tool(name, dict(alice_booking, priority="high"))
            assert missing.is_error and missing.structured_content["error"]["errors"] == ["category: required"]
            assert extra.is_error and extra.structured_content["error"]["errors"] == ["priority: unknown argument"]
            # Identity or credentials supplied as arguments never select a caller.
            for injected in (dict(identity="bob"), dict(token=d.people["bob"]["token"]), {d.ctx: d.people["bob"]["id"]}, dict(credential="bob")):
                r = await alice.call_tool(name, dict(alice_booking, **injected))
                assert r.is_error and r.structured_content["status"] == "rejected" and r.structured_content["error"]["code"] == "invalid_arguments"
            assert len(d.requests()) == before + 1

            async with session(d, "bob") as bob:
                assert await names(bob) == [name]
                denied = await bob.call_tool(name, alice_booking)
                assert denied.is_error and denied.structured_content["status"] == "failed"
                assert denied.structured_content["error"]["code"] == "blocked_by_access_check" and denied.structured_content["output"] is None
                assert denied.structured_content["writes"] == [dict(step_id="s2", write_state="not_attempted")]
            assert len(d.requests()) == before + 1

            # Disabling takes effect for a client that still holds the old tool list; no restart.
            assert d.client.post(f'/api/v1/publications/{pub["id"]}/disable', json=dict(note=LABEL + "withdrawn")).json()["status"] == "disabled"
            await unpublished(alice, name, alice_booking)
            assert await names(alice) == []
            assert d.client.post(f"/api/v1/artifacts/{a['id']}/publications", json=dict(note=LABEL + "again")).json()["id"] != pub["id"]

        # Publication persists across a server restart.
        async with session(d, "alice") as alice:
            assert await names(alice) == [name]
            assert (await alice.call_tool(name, alice_booking)).is_error is False
            count = len(d.requests())
            # Changed enforcement: the existing tool stops working at once and is no longer listed.
            config = {r["id"]: dict(mechanism="delegated_user_credential") for r in current(d, pid)["requirements"] if r["kind"] == "caller_access"}
            assert d.client.post(f"/api/v1/proposals/{pid}/enforcement", json=dict(connector_id="desk", enforcement=config)).status_code == 200
            stale = await alice.call_tool(name, alice_booking)
            assert stale.is_error and stale.structured_content["error"]["code"] == "enforcement_not_current" and stale.structured_content["execution_id"]
            assert await names(alice) == [] and len(d.requests()) == count

            # A new build needs its own passing tests before it can be published.
            b = review_and_build_again(d, pid)
            assert publish(d, b["id"]).json()["code"] == "publication_not_allowed"
            with_evidence(d, b["id"])
            publish(d, b["id"])
            new_name = publishing.tool_name(b["id"], b["content"])
            assert await names(alice) == [new_name] and new_name != name
            assert (await alice.call_tool(new_name, alice_booking)).is_error is False
            count = len(d.requests())
            # Superseded approval: a new proposal version blocks the published tool.
            revise(d, pid)
            stale = await alice.call_tool(new_name, alice_booking)
            assert stale.is_error and stale.structured_content["error"]["code"] == "approval_not_current"
            assert await names(alice) == [] and len(d.requests()) == count

    anyio.run(scenario)
    audit = d.client.get(f"/api/v1/artifacts/{a['id']}").json()["executions"]
    mcp = [e for e in audit if e["mode"] == "mcp_sandbox"]
    assert mcp and all(e["report"]["channel"] == "mcp_stdio" and e["report"]["publication_id"] for e in mcp)
    text = json.dumps(audit)
    assert all(s not in text for s in (d.people["alice"]["token"], d.people["bob"]["token"], KEY)) and "SR-0001" not in text


def review_and_build_again(d, pid):
    """TEST-ONLY review of the replacement enforcement for the same approved version, then a rebuild."""
    config = {r["id"]: dict(mechanism="delegated_user_credential") if r["kind"] == "caller_access" else
              dict(mechanism="response_field_matches_context", step_id="s1", response_status="200", pointer=f'/{d.n["lookup_env"]}/{d.n["record"]}/{d.n["holder"]}/{d.n["holder_id"]}',
                   context_field=d.ctx, comparison="equals", check_point="after_step") for r in current(d, pid)["requirements"]}
    e = d.client.post(f"/api/v1/proposals/{pid}/enforcement", json=dict(connector_id="desk", enforcement=config)).json()
    d.client.post(f'/api/v1/enforcement/{e["id"]}/review', json=dict(note=LABEL + "enforcement review"))
    r = d.client.post(f"/api/v1/proposals/{pid}/artifacts", json=dict(connector_id="desk"))
    assert r.status_code == 200, r.text
    return r.json()
