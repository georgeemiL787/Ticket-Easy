"""Real MCP smoke run: the official SDK client launches the real stdio server, for both domains.

REAL: stdio server process, SDK client, HTTP to the running items target (127.0.0.1:8001, read-only code)
and to the service-desk fixture started here as a separate process. Backend state is checked by
independent harness clients.
REUSED / TEST-ONLY: saved artifacts from copies of the TEST-ONLY demo databases (no generation, no model
calls); fresh disposable accounts; sandbox tests and publications are recorded only in isolated copies.
Credentials live in local connectors files and are asserted absent from the printed log.

    $env:PYTHONPATH="."; .venv/Scripts/python.exe scripts/mcp_smoke.py
"""
import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
import httpx
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.exceptions import MCPError
sys.path.insert(0, str(Path(__file__).parent))
from domain_demo import Backend, start_fixture
from sandbox_demo import Harness
from team_c.config import AppError, Settings
from team_c.models import PublicationDisableSubmission, PublicationSubmission, SandboxTestSubmission
from team_c.service import Service
from team_c.storage import Store

NOTE = "TEST-ONLY sandbox publication (MCP smoke run, isolated copy)"


def copy(source, target):
    for suffix in ("", "-wal", "-shm"):
        Path(target + suffix).unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{Path(source).resolve().as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    src.backup(dst)
    dst.close()
    src.close()


class Domain:
    def __init__(self, label, db, connectors, hosts, business_id):
        self.label, self.business_id = label, business_id
        self.settings = Settings(database_path=db, connectors_file=connectors, sandbox_hosts=hosts)
        self.service = Service(self.settings, Store(db), None)

    def test(self, aid, name, identity, arguments, scenario="general", record_owner=None, **expect):
        r = self.service.run_sandbox_test(aid, SandboxTestSubmission(name=name, identity=identity, arguments=arguments, expect=expect, scenario=scenario, record_owner=record_owner))
        return dict(test=name, scenario=scenario, verdict=r["verdict"], status=r["status"])

    def publish(self, aid):
        try:
            p = self.service.publish(aid, PublicationSubmission(note=NOTE))
            return p, dict(publication_id=p["id"], tool=p["tool_name"], artifact_sha256=p["artifact_sha256"][:12], version=p["version"],
                           evidence=p["evidence"], production_ready=p["production_ready"])
        except AppError as exc:
            return None, dict(refused=exc.code, problems=exc.details.get("problems"))

    @asynccontextmanager
    async def session(self, identity):
        params = StdioServerParameters(command=sys.executable, env=dict(os.environ, PYTHONPATH="."),
                                       args=["-m", "team_c.mcp_server", "--business-id", self.business_id, "--identity", identity, "--database", self.settings.database_path,
                                             "--connectors", self.settings.connectors_file, "--sandbox-hosts", self.settings.sandbox_hosts])
        async with AsyncExitStack() as stack:
            read, write = await stack.enter_async_context(stdio_client(params))
            s = await stack.enter_async_context(ClientSession(read, write))
            await s.initialize()
            yield s


async def listed(s):
    return [dict(name=t.name, required=t.input_schema.get("required"), properties=sorted(t.input_schema["properties"]),
                 additional_properties=t.input_schema.get("additionalProperties"), artifact=t.meta["team_c"]["artifact_id"][:8])
            for t in (await s.list_tools()).tools]


async def call(s, name, arguments):
    try:
        r = await s.call_tool(name, arguments)
    except MCPError as exc:
        return dict(mcp_error=exc.error.code, data=exc.error.data)
    sc = r.structured_content
    return dict(is_error=r.is_error, status=sc["status"], error=(sc["error"] or {}).get("code"), errors=(sc["error"] or {}).get("errors"),
                output=sc["output"], execution_id=bool(sc["execution_id"]), writes=sc["writes"])


async def exercise(d, aid, owner, other, valid, cross, extra, state, log):
    """The same sequence for either domain; `state` reads backend state independently of Team C."""
    pub, record = d.publish(aid)
    log.append(dict(step="publish", **record))
    name = pub["tool_name"]
    async with d.session(owner) as s:
        log.append(dict(step="tools_list", identity=owner, tools=await listed(s)))
        before = state()
        log.append(dict(step="valid_call", result=await call(s, name, valid), backend_before=before, backend_after=state()))
        before = state()
        missing = {k: v for k, v in valid.items() if k != next(iter(sorted(valid)))}
        log.append(dict(step="missing_argument", result=await call(s, name, missing)))
        log.append(dict(step="extra_argument", result=await call(s, name, dict(valid, **extra))))
        log.append(dict(step="identity_injection", result=await call(s, name, dict(valid, identity=other, token="attacker-supplied"))))
        log.append(dict(step="backend_unchanged_after_rejections", unchanged=state() == before))
    async with d.session(other) as s:
        before = state()
        log.append(dict(step="cross_user_call", identity=other, result=await call(s, name, cross), backend_unchanged=state() == before))
    async with d.session(owner) as s:
        log.append(dict(step="after_restart", tools=[t["name"] for t in await listed(s)]))
        d.service.disable_publication(pub["id"], PublicationDisableSubmission(note="TEST-ONLY: smoke run finished"))
        before = state()
        log.append(dict(step="call_after_disable_same_session", result=await call(s, name, valid), tools=await listed(s), backend_unchanged=state() == before))


async def items(args, log):
    copy(args.items_source, "data/mcp-items.sqlite3")
    harness = Harness(args.items_url)
    alice, bob = harness.signup("alice"), harness.signup("bob")
    try:
        conn = sqlite3.connect("data/mcp-items.sqlite3")
        business = conn.execute("SELECT business_id FROM proposals WHERE id=?", (args.items_proposal,)).fetchone()[0]
        conn.close()
        identity = lambda a: dict(username=a["email"], password=a["password"], scope="end_user", context=dict(user_id=a["id"]))
        Path("data/mcp-items-connectors.json").write_text(json.dumps(dict(connectors={"target-sandbox": dict(
            business_id=business, base_url=args.items_url, sandbox=True, context_fields=["user_id"], identities=dict(alice=identity(alice), bob=identity(bob)))})), encoding="utf-8")
        d = Domain("items (real target)", "data/mcp-items.sqlite3", "data/mcp-items-connectors.json", httpx.URL(args.items_url).netloc.decode(), business)
        log.append(dict(step="unenforced_artifact_publish", artifact=args.items_unenforced[:8], **d.publish(args.items_unenforced)[1]))
        log.append(dict(step="publish_before_tests", artifact=args.items_artifact[:8], **d.publish(args.items_artifact)[1]))
        log.append(dict(step="sandbox_tests", results=[
            d.test(args.items_artifact, "owner_update", "alice", dict(item_id=alice["item"], new_title="alice title (sandbox test)"), "own_record", status="succeeded", outputs_present=["updated_item"]),
            d.test(args.items_artifact, "bob_owner_update", "bob", dict(item_id=bob["item"], new_description="bob description (sandbox test)"), "own_record", status="succeeded", outputs_present=["updated_item"]),
            d.test(args.items_artifact, "cross_user_update", "alice", dict(item_id=bob["item"], new_title="hijacked"), "cross_user", "bob", status="failed", failure_step="s1", failure_outcome="rejected_by_api")]))
        state = lambda: dict(alice=harness.item("alice").get("title"), bob=harness.item("bob").get("title"))
        await exercise(d, args.items_artifact, "alice", "bob", dict(item_id=alice["item"], new_title="alice title (via MCP)"),
                       dict(item_id=alice["item"], new_title="hijacked by bob"), dict(owner_id=bob["id"]), state, log)
    finally:
        log.append(dict(step="items_cleanup", result=harness.cleanup()))


async def desk(args, log):
    copy(args.desk_source, "data/mcp-desk.sqlite3")
    proc = start_fixture("base", args.desk_port, args.desk_key)
    try:
        backend = Backend(f"http://127.0.0.1:{args.desk_port}", args.desk_key)
        people = {n: backend.account(n) for n in ("alice", "bob")}
        booking = {n: backend.booking(a["id"]) for n, a in people.items()}
        conn = sqlite3.connect("data/mcp-desk.sqlite3")
        business = conn.execute("SELECT business_id FROM proposals WHERE id=?", (args.desk_proposal,)).fetchone()[0]
        conn.close()
        Path("data/mcp-desk-connectors.json").write_text(json.dumps(dict(connectors={"desk-sandbox": dict(
            business_id=business, base_url=f"http://127.0.0.1:{args.desk_port}", sandbox=True, context_fields=["account_id"],
            identities={n: dict(token=a["token"], scope="end_user", context=dict(account_id=a["id"])) for n, a in people.items()})})), encoding="utf-8")
        d = Domain("service desk (fixture)", "data/mcp-desk.sqlite3", "data/mcp-desk-connectors.json", f"127.0.0.1:{args.desk_port}", business)
        log.append(dict(step="unrelated_artifact_publish", artifact=args.desk_other[:8], **d.publish(args.desk_other)[1]))
        valid = lambda who: dict(reference=booking[who]["ref"], category="cleaning", details="Leaking tap (MCP smoke)")
        log.append(dict(step="sandbox_tests", results=[
            d.test(args.desk_artifact, "owner_files_request", "alice", valid("alice"), "own_record", status="succeeded", outputs_present=["created_response"]),
            d.test(args.desk_artifact, "bob_files_own_request", "bob", valid("bob"), "own_record", status="succeeded", outputs_present=["created_response"]),
            d.test(args.desk_artifact, "cross_user_request", "bob", valid("alice"), "cross_user", "alice", status="failed", failure_step="s1", failure_outcome="blocked_by_access_check")]))
        state = lambda: [dict(ticket=r["ticket"], filed_by=[n for n, a in people.items() if a["id"] == r["filed_by"]]) for r in backend.requests()]
        await exercise(d, args.desk_artifact, "alice", "bob", valid("alice"), valid("alice"), dict(account_id=people["bob"]["id"]), state, log)
    finally:
        proc.kill()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--items-source", default="data/sandbox-demo.sqlite3")
    parser.add_argument("--items-proposal", default="b6d2b7da-4438-46a1-9917-cedd2f98b4ff")
    parser.add_argument("--items-artifact", default="104d4b07-d4b3-410a-9565-6da7acfd0c8c")
    parser.add_argument("--items-unenforced", default="ec532937-6730-48e5-b081-872f8e6f03d0")
    parser.add_argument("--items-url", default="http://127.0.0.1:8001")
    parser.add_argument("--desk-source", default="data/desk-repair.sqlite3")
    parser.add_argument("--desk-proposal", default="c01d752f-8101-4737-9ff2-1d4b482e054f")
    parser.add_argument("--desk-artifact", default="05e9c1bd-813d-44a2-af10-0416c4945fa3")
    parser.add_argument("--desk-other", default="1faab1ab-9566-497a-8230-5a2799dbf399")
    parser.add_argument("--desk-port", type=int, default=8004)
    parser.add_argument("--desk-key", default=os.urandom(12).hex())
    args = parser.parse_args()
    real = Path("data/openapi-live.sqlite3")
    real_hash = hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None
    log = {"items": [], "desk": []}
    asyncio.run(items(args, log["items"]))
    asyncio.run(desk(args, log["desk"]))
    log["real_database_unchanged"] = real_hash is None or hashlib.sha256(real.read_bytes()).hexdigest() == real_hash
    text = json.dumps(log, indent=2, ensure_ascii=False)
    secrets = [json.loads(Path(f).read_text(encoding="utf-8")) for f in ("data/mcp-items-connectors.json", "data/mcp-desk-connectors.json")]
    values = [v for c in secrets for conn in c["connectors"].values() for i in conn["identities"].values() for k, v in i.items() if k in ("token", "password")]
    assert values and not any(v in text for v in values) and args.desk_key not in text
    print(text)


if __name__ == "__main__":
    main()
