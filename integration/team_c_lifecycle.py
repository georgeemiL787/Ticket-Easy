"""Run Team C's real tool lifecycle for the Nile Style sandbox shop and bind the result into Team B (TEST-ONLY).

Needs the sandbox API (integration/serve.py sandbox) and Team C (integration/serve.py team-c) running, and a model
provider configured for Team C (team_c/.env: Groq, OpenRouter or Ollama). It drives Team C's own API and forms exactly
as an owner would in its pages, with every owner decision labelled TEST-ONLY:

  business -> upload the sandbox OpenAPI -> request a tool (the model triages and generates a proposal)
  -> answer questions and access requirements -> accept the access policy -> reconcile (model) -> confirm requirements
  -> approve to build -> save the sandbox connector -> build the artifact -> add two sandbox identities (alice = C-100,
  bob = C-101, bearer tokens issued by the sandbox) -> generated policy tests -> sandbox tests (own record x2, a
  cross-customer refusal) -> publish to the TEST-ONLY sandbox MCP -> write Team B's binding (integration/var/team_c.json)

Nothing here approves on anyone's behalf without saying so: approvals are recorded by Team C with this script's notes.
Progress is kept in integration/var/lifecycle.json so a failed run resumes without asking the model again; --fresh
starts over. Credentials (sandbox tokens) are written only to Team C's database through its form, never printed.

    team_b/.venv/Scripts/python.exe integration/team_c_lifecycle.py [--fresh] [--tool order|ticket]
"""

import argparse
import json
import re
import secrets
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
VAR = ROOT / "integration" / "var"
TEAM_C = "http://127.0.0.1:8101"
API = TEAM_C + "/api/v1"
SANDBOX = "http://127.0.0.1:8102"
LABEL = "TEST-ONLY integration run (integration/team_c_lifecycle.py):"
# Team B's own customers (team_b/fixtures/shop_001/backend.json) and the sandbox identity each one acts as in Team C.
IDENTITIES = {"alice": ("C-100", "NS-20877"), "bob": ("C-101", "NS-20790")}

TOOLS = {
    "order": dict(
        team_b="get_order",
        goal="TEST ONLY: let a logged-in customer look up one of their own orders by its order id: order status, "
        "payment status, dates (placed, shipped, delivered, expected delivery), total, item name, product category, "
        "clearance flag, item condition and tracking number. Read only; change nothing.",
        method="GET",
    ),
    "ticket": dict(
        team_b="create_ticket",
        goal="TEST ONLY: let a logged-in customer open a support ticket about one of their own orders. First look up "
        "the order by its order id (GET /api/orders/{order_id}) to confirm it belongs to the caller, then open the ticket "
        "(POST /api/tickets) for that same order with a subject and a description, and return the new ticket id and "
        "its status.",
        method="POST",
    ),
}
OWNER_POLICY = (
    f"{LABEL} the caller is a logged-in customer (end_user) authenticated with their own bearer token issued by the "
    "Nile Style sandbox. A customer may only use their own orders: the order's customer_id must equal the "
    "authenticated customer's customer_id (trusted context field customer_id); the API itself answers 403 for another "
    "customer's order and 404 for an unknown one. No administrator or staff access; no service account."
)


class Owner:
    """Team C's API and its browser forms (which need the page's CSRF token)."""

    def __init__(self) -> None:
        self.http = httpx.Client(base_url=TEAM_C, timeout=600.0, trust_env=False)
        self.csrf: str | None = None

    def get(self, path: str) -> Any:
        r = self.http.get("/api/v1" + path)
        _ok(r, f"GET {path}")
        return r.json()

    def post(self, path: str, body: Any = None, **kw: Any) -> Any:
        r = self.http.post("/api/v1" + path, json=body, **kw) if body is not None or not kw else self.http.post("/api/v1" + path, **kw)
        _ok(r, f"POST {path}")
        return r.json() if r.content else {}

    def form(self, page: str, action: str, data: dict[str, str]) -> httpx.Response:
        html = self.http.get(page).text
        found = re.search(r'name="csrf"\s+value="([^"]+)"', html)
        if found:
            self.csrf = found.group(1)
        r = self.http.post(action, data={**data, "csrf": self.csrf or ""}, follow_redirects=False)
        if r.status_code not in (200, 303):
            raise SystemExit(f"FAIL form {action}: {r.status_code} {r.text[:500]}")
        return r


def _ok(r: httpx.Response, what: str) -> None:
    if r.status_code >= 400:
        raise SystemExit(f"FAIL {what}: {r.status_code} {r.text[:1500]}")


def step(title: str) -> None:
    print(f"\n=== {title}", flush=True)


def load_state(fresh: bool, tool: str) -> dict[str, Any]:
    path = VAR / f"lifecycle_{tool}.json"
    if fresh or not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any], tool: str) -> None:
    VAR.mkdir(parents=True, exist_ok=True)
    (VAR / f"lifecycle_{tool}.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def harness_key() -> str:
    path = VAR / "sandbox_harness.key"
    if not path.exists():
        raise SystemExit("integration/var/sandbox_harness.key is missing: start the sandbox with integration/serve.py")
    return path.read_text(encoding="utf-8").strip()


def wait_for_proposal(owner: Owner, pid: str) -> dict[str, Any]:
    for _ in range(120):
        p = owner.get(f"/proposals/{pid}")
        if p.get("state") != "generating":
            return p
        time.sleep(2)
    raise SystemExit("the proposal did not finish generating")


def answer_everything(owner: Owner, pid: str) -> None:
    p = owner.get(f"/proposals/{pid}")
    answers = {q["id"]: OWNER_POLICY for q in p["content"]["questions"]}
    answers.update({r["id"]: OWNER_POLICY for r in p.get("requirements", [])})
    if answers:
        owner.post(f"/proposals/{pid}/versions/{p['version']}/answers", {"expected_revision": p["review_revision"], "answers": answers})
    print(f"answered {len(answers)} questions/requirements")


def accept_policy(owner: Owner, pid: str, sid: str, write: bool) -> None:
    p = owner.get(f"/proposals/{pid}")
    if p.get("policy") and p["policy"].get("accepted_at"):
        return
    inventory = owner.get(f"/specifications/{sid}/inventory")["inventory"]
    ops = {o["id"]: o for o in inventory["operations"]}
    checks, scopes = [], []
    reads = [s for s in p["content"]["steps"] if (ops.get(s["operation_id"]) or {}).get("method") == "GET"]
    for req in [r for r in p["requirements"] if r["kind"] == "record_scope"]:
        named = [s for s in reads if f'{ops[s["operation_id"]]["method"]} {ops[s["operation_id"]]["path"]}' in req["operations"]]
        step_for = (named or reads or [None])[0]  # the order read that proves ownership before any write
        if step_for is None:
            # No read step to check on: Team C cannot check ownership itself. The sandbox API refuses another
            # customer's order (403); Team C records this as an unrestricted scope (no Team C record check).
            scopes.append({"requirement_id": req["id"], "mode": "unrestricted",
                           "reason": f"{LABEL} no read step; the sandbox API answers 403 for another customer's order."})
            continue
        checks.append({"requirement_id": req["id"], "kind": "ownership", "resource": "Order record", "step_id": step_for["id"],
                       "response_status": "200", "resource_field": "/customer_id", "identity_source": "customer_id"})
        scopes.append({"requirement_id": req["id"], "mode": "checked", "reason": "A customer may only read their own orders."})
    policy = {
        "principals": ["end_user", "resource_owner"], "authentication": "required", "role_source": None, "roles": [],
        "permission_source": None, "permissions": [], "scopes": scopes, "checks": checks,
        "inputs": (p.get("policy_form") or {}).get("inputs", []), "financial": False, "irreversible": False,
        "external_side_effects": write, "idempotent": not write,
        "explanation": f"{LABEL} {'opens a ticket' if write else 'read-only order lookup'} for the authenticated customer's own order.",
    }
    owner.post(f"/proposals/{pid}/versions/{p['version']}/policy", {"expected_revision": p["review_revision"], "policy": policy})
    print(f"policy accepted: {len(checks)} ownership check(s), scopes {[s['mode'] for s in scopes]}")


def reconcile_until_ready(owner: Owner, pid: str) -> dict[str, Any]:
    for i in range(1, 6):
        p = owner.get(f"/proposals/{pid}")
        if p.get("state") == "ready_for_review":
            return p
        last = (p.get("reconciliations") or [{}])[-1].get("result") or {}
        ids = {q["id"] for q in p["content"]["questions"]}
        open_findings = [f for f in last.get("findings", []) if f["status"] != "resolved" and f["question_id"] in ids]
        if open_findings:
            print(f"  re-answering {len(open_findings)} finding(s) the reviewer found insufficient")
            owner.post(f"/proposals/{pid}/versions/{p['version']}/answers",
                       {"expected_revision": p["review_revision"], "answers": {f["question_id"]: OWNER_POLICY for f in open_findings}})
            p = owner.get(f"/proposals/{pid}")
        owner.post(f"/proposals/{pid}/versions/{p['version']}/reconcile", {"expected_revision": p["review_revision"]})
        p = owner.get(f"/proposals/{pid}")
        print(f"  reconcile #{i}: state={p['state']}")
    p = owner.get(f"/proposals/{pid}")
    if p["state"] != "ready_for_review":
        raise SystemExit(f"the proposal is {p['state']} after 5 reconciliations; open {TEAM_C}/proposals/{pid} to see why")
    return p


def build_artifact(owner: Owner, pid: str) -> tuple[str, str]:
    p = owner.get(f"/proposals/{pid}")
    draft = p.get("connector_draft") or {}
    fields = sorted(set(draft.get("context_fields") or []) | {"customer_id"})
    owner.form(f"/proposals/{pid}", "/actions/save_connector",
               {"proposal_id": pid, "base_url": SANDBOX, "context_fields": ",".join(fields), "sandbox": "1"})
    found = re.findall(r"sandbox-[0-9a-f]{20}", owner.http.get(f"/proposals/{pid}").text)
    if not found:
        raise SystemExit("no sandbox connector after saving it")
    connector = found[-1]
    artifact = owner.post(f"/proposals/{pid}/artifacts", {"connector_id": connector})
    return artifact["id"], connector


def review_enforcement_if_needed(owner: Owner, pid: str, aid: str) -> str:
    a = owner.get(f"/artifacts/{aid}")
    if not a["content"].get("execution_blockers"):
        return aid
    p = owner.get(f"/proposals/{pid}")
    pending = [e for e in (p.get("enforcement") or []) if e.get("status") == "awaiting_review"]
    for e in pending:
        owner.post(f"/enforcement/{e['id']}/review", {"note": f"{LABEL} enforcement reviewed by the owner"})
        print(f"reviewed enforcement {e['id']}")
    if pending:
        aid = owner.post(f"/proposals/{pid}/artifacts", {"connector_id": a["content"]["connector"]["id"]})["id"]
    blockers = owner.get(f"/artifacts/{aid}")["content"].get("execution_blockers")
    if blockers:
        raise SystemExit(f"execution is still blocked: {blockers}; open {TEAM_C}/artifacts/{aid}")
    return aid


def add_identities(owner: Owner, aid: str) -> None:
    key = harness_key()
    with httpx.Client(base_url=SANDBOX, trust_env=False) as sandbox:
        for name, (customer, _) in IDENTITIES.items():
            token = sandbox.post("/_harness/tokens", json={"customer_id": customer}, headers={"x-harness-key": key}).json()["token"]
            owner.form(f"/artifacts/{aid}", "/actions/save_test_identity",
                       {"artifact_id": aid, "identity_name": name, "scope": "end_user", "token": token, "a:customer_id": customer})
            print(f"identity {name} = sandbox customer {customer} (token stored in Team C only)")


def arguments_for(content: dict[str, Any], order_id: str, write: bool) -> dict[str, Any]:
    props = (content.get("input_schema") or {}).get("properties") or {}
    args: dict[str, Any] = {}
    for name, schema in props.items():
        lowered = name.lower()
        if "order" in lowered:
            args[name] = order_id
        elif write and "subject" in lowered:
            args[name] = "TEST-ONLY sandbox ticket"
        elif write and ("description" in lowered or "body" in lowered or "detail" in lowered or "message" in lowered):
            args[name] = "Created by the TEST-ONLY integration run against the sandbox."
        elif schema.get("type") == "string" and name in ((content.get("input_schema") or {}).get("required") or []):
            args[name] = "TEST-ONLY"
    return args


def sandbox_tests(owner: Owner, aid: str, write: bool) -> None:
    content = owner.get(f"/artifacts/{aid}")["content"]
    owner.post(f"/artifacts/{aid}/policy-tests", {})

    def test(name: str, identity: str, order: str, status: str, scenario: str, record_owner: str | None = None) -> None:
        body = {"name": name, "identity": identity, "arguments": arguments_for(content, order, write),
                "expect": {"status": status}, "scenario": scenario, "record_owner": record_owner}
        result = owner.post(f"/artifacts/{aid}/sandbox-tests", body)
        print(f"  {name}: verdict={result['verdict']} status={result['status']}")

    alice_order, bob_order = IDENTITIES["alice"][1], IDENTITIES["bob"][1]
    test("alice_own_order", "alice", alice_order, "succeeded", "own_record")
    test("bob_own_order", "bob", bob_order, "succeeded", "own_record")
    test("bob_cannot_use_alices_order", "bob", alice_order, "failed", "cross_user", "alice")


def publish(owner: Owner, aid: str) -> dict[str, Any]:
    r = owner.http.post(f"/api/v1/artifacts/{aid}/publications", json={"note": f"{LABEL} sandbox publication for Team B"})
    if r.status_code == 409:
        problems = (r.json().get("details") or {}).get("problems") or [r.text]
        raise SystemExit("publication refused by Team C:\n- " + "\n- ".join(problems))
    _ok(r, "publish")
    return r.json()


def output_fields(content: dict[str, Any], wanted: list[str]) -> dict[str, str]:
    """Team B's output names -> Team C output name or /pointer, from the reviewed artifact's declared outputs."""
    fields: dict[str, str] = {}
    for o in content.get("outputs") or []:
        pointer = o.get("pointer") or ""
        if pointer in ("", "/"):
            for w in wanted:
                fields.setdefault(w, f"/{o['name']}/{w}")
        else:
            leaf = pointer.rstrip("/").rsplit("/", 1)[-1]
            if leaf in wanted:
                fields[leaf] = o["name"]
            elif o["name"] in wanted:
                fields[o["name"]] = o["name"]
    return fields


def write_binding(state: dict[str, Any], tool: str, content: dict[str, Any]) -> Path:
    path = VAR / "team_c.json"
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    team_c_dir = ROOT / "team_c"
    python = team_c_dir / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = team_c_dir / ".venv" / "bin" / "python"
    config.update(
        command=[str(python), "-m", "team_c.mcp_server"], cwd=str(team_c_dir), env={"PYTHONPATH": "."},
        database="data/integration.sqlite3", sandbox_hosts="127.0.0.1:8102", api_url=TEAM_C, builder_url=TEAM_C,
    )  # fmt: skip
    tenant = config.setdefault("tenants", {}).setdefault("shop_001", {})
    tenant["business_id"] = state["business_id"]
    tenant["identities"] = {customer: name for name, (customer, _) in IDENTITIES.items()}
    spec = json.loads((ROOT / "team_b" / "fixtures" / "shop_001" / "tools.json").read_text(encoding="utf-8"))
    declared = next(t for t in spec["tools"] if t["name"] == TOOLS[tool]["team_b"])
    wanted = list(declared["output_schema"].get("properties") or {})
    inputs = list((content.get("input_schema") or {}).get("properties") or {})
    binding: dict[str, Any] = {"proposal_id": state["proposal_id"], "fields": output_fields(content, wanted)}
    renames = {b: c for b in declared["input_schema"].get("properties", {}) for c in inputs if b != c and b.split("_")[0] in c}
    if renames:
        binding["arguments"] = renames
    if declared["operation_kind"] == "create":
        # the new record's reference: the created step's own *_id output (for the ticket tool: /ticket_id)
        writes = {s["id"] for s in content.get("steps") or [] if s.get("effect") == "write"}
        created = [o for o in content.get("outputs") or [] if o.get("step_id") in writes and (o.get("pointer") or "").endswith("id")]
        if created and "reference_id" not in binding["fields"]:
            binding["fields"]["reference_id"] = created[0]["name"]
        binding["reference_field"] = "reference_id"
    tenant.setdefault("tools", {})[TOOLS[tool]["team_b"]] = binding
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fresh", action="store_true", help="start over (a new business in Team C)")
    parser.add_argument("--tool", choices=sorted(TOOLS), default="order")
    parser.add_argument("--refresh-identities", action="store_true", help="only re-save alice/bob on the built artifact")
    args = parser.parse_args()
    tool, write = args.tool, TOOLS[args.tool]["method"] == "POST"
    state = load_state(args.fresh, tool)
    shared = load_state(False, "order") if tool != "order" else {}
    owner = Owner()

    if args.refresh_identities:
        step("Re-save the sandbox identities (tokens) of the published artifact")
        add_identities(owner, state["artifact_id"])
        return 0

    if "business_id" not in state:
        if shared.get("business_id"):
            step("Upload the current sandbox OpenAPI to the same business")
            spec = httpx.get(SANDBOX + "/openapi.json", timeout=10).content
            uploaded = owner.post(f"/businesses/{shared['business_id']}/specifications", files={"file": ("openapi.json", spec, "application/json")})
            state.update(business_id=shared["business_id"], spec_id=uploaded["id"])
            print(f"specification {uploaded['id']}: {uploaded['inventory']['eligible_operation_count']} eligible operations")
        else:
            step("Create the business and upload the sandbox OpenAPI")
            biz = owner.post("/businesses", {"name": "Nile Style (TEST-ONLY sandbox)",
                                             "description": "Online fashion shop in Egypt. Customers ask about their orders and open support tickets. TEST-ONLY sandbox API with synthetic data."})  # fmt: skip
            spec = httpx.get(SANDBOX + "/openapi.json", timeout=10).content
            uploaded = owner.post(f"/businesses/{biz['id']}/specifications", files={"file": ("openapi.json", spec, "application/json")})
            state.update(business_id=biz["id"], spec_id=uploaded["id"])
            print(f"business {biz['id']}, specification {uploaded['id']}: {uploaded['inventory']['eligible_operation_count']} eligible operations")
        save_state(state, tool)

    if "proposal_id" not in state:
        step(f"Request the tool ({tool}); the model triages and writes a proposal")
        req = owner.post(f"/businesses/{state['business_id']}/tool-requests",
                         {"goal": TOOLS[tool]["goal"], "examples": "", "spec_id": state["spec_id"], "idempotency_key": secrets.token_urlsafe(16)})  # fmt: skip
        print(f"tool request {req['id']}: {req['status']}")
        if req["status"] == "needs_clarification":
            owner.post(f"/tool-requests/{req['id']}/clarifications", {"text": OWNER_POLICY})
            req = owner.get(f"/tool-requests/{req['id']}")
            print(f"after clarification: {req['status']}")
        ids = req.get("proposal_ids") or req.get("existing_proposal_ids") or []
        if not ids:
            raise SystemExit(f"no proposal: {json.dumps(req)[:1500]}")
        state["proposal_id"] = ids[0]
        save_state(state, tool)
    pid = state["proposal_id"]
    p = wait_for_proposal(owner, pid)
    print(f"proposal {pid} v{p['version']}: {p['state']} - {p['content']['name']}")

    if p["state"] != "approved_to_build":
        step("Owner review: answers, access policy, reconciliation (model), requirement confirmation, approval")
        answer_everything(owner, pid)
        owner.post(f"/proposals/{pid}/versions/{p['version']}/reconcile", {"expected_revision": owner.get(f"/proposals/{pid}")["review_revision"]})
        accept_policy(owner, pid, state["spec_id"], write)
        p = reconcile_until_ready(owner, pid)
        for req in p.get("requirements", []):
            if req.get("status") == "answer_sufficient":
                p = owner.get(f"/proposals/{pid}")
                owner.post(f"/proposals/{pid}/versions/{p['version']}/requirements/{req['id']}/confirm", {"expected_revision": p["review_revision"]})
        p = owner.get(f"/proposals/{pid}")
        owner.post(f"/proposals/{pid}/versions/{p['version']}/decisions",
                   {"expected_revision": p["review_revision"], "action": "approve_to_build", "reason": f"{LABEL} approved to build and test in the sandbox",
                    "idempotency_key": secrets.token_urlsafe(16)})  # fmt: skip
        print("approved to build")

    if "artifact_id" not in state:
        step("Build: sandbox connector, artifact, enforcement")
        aid, connector = build_artifact(owner, pid)
        state.update(artifact_id=review_enforcement_if_needed(owner, pid, aid), connector_id=connector)
        save_state(state, tool)
    aid = state["artifact_id"]
    print(f"artifact {aid}")

    if "publication_id" not in state:
        step("Sandbox identities and tests")
        add_identities(owner, aid)
        sandbox_tests(owner, aid, write)
        step("Publish to the TEST-ONLY sandbox MCP")
        pub = publish(owner, aid)
        assert pub["environment"] == "sandbox" and pub["production_ready"] is False and pub["production_activation"] is False
        state.update(publication_id=pub["id"], tool_name=pub["tool_name"])
        save_state(state, tool)
    print(f"published {state['tool_name']} (publication {state['publication_id']}, environment sandbox, not production)")

    step("Bind it into Team B")
    content = owner.get(f"/artifacts/{aid}")["content"]
    path = write_binding(state, tool, content)
    print(f"wrote {path.relative_to(ROOT)}: {TOOLS[tool]['team_b']} -> proposal {pid}")
    print(json.dumps(json.loads(path.read_text(encoding="utf-8"))["tenants"]["shop_001"]["tools"][TOOLS[tool]["team_b"]], indent=2))
    print("\nLIFECYCLE COMPLETE (TEST-ONLY)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
