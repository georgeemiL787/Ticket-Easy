"""Second domain: the local service-desk fixture, its AUTHORED TEST SUBSTITUTE and TEST-ONLY review helpers."""
import json
from types import SimpleNamespace
import httpx
from fastapi.testclient import TestClient
from conftest import requirement_findings
from fixtures.service_desk.app import create_app as create_fixture
from helpers.openapi import op
from team_c.config import Settings
from team_c.models import GenerationOutput, ReconciliationOutput, RepairOutput
from team_c.web import create_app

KEY = "harness-test-key"
LABEL = "TEST-ONLY (not a business decision): "


def desk_proposal(inv, n, defect=None, suffix=""):
    """AUTHORED: the intended lookup -> create chain, located by this variant's paths."""
    lookup, create = op(inv, "GET", n["prefix"] + n["lookup"]), op(inv, "POST", n["prefix"] + n["create"])
    runtime = lambda target, ref: dict(target=target, kind="runtime_argument", reference=ref, step_id=None, response_status=None)
    record = f'/{n["lookup_env"]}/{n["record"]}'
    carried = record + (f'/{n["holder"]}/{n["holder_id"]}' if defect == "wrong_pointer" else f'/{n["internal"]}')
    return dict(name="Service request for a booking", description="Find a booking by reference and file a service request" + suffix, business_purpose="Let customers report problems",
                steps=[dict(id="s1", operation_id=lookup["id"], purpose="Find the booking", bindings=[runtime(f'query.{n["ref"]}', "booking_reference")]),
                       dict(id="s2", operation_id=create["id"], purpose="File the request", bindings=[
                           dict(target=f'body.{n["body_id"]}', kind="previous_operation_output", reference=carried, step_id="s1", response_status="200"),
                           runtime(f'body.{n["category"]}', "category"), runtime(f'body.{n["text"]}', "details")])],
                configuration=[], outputs=[dict(name="ticket", step_id="s2", response_status="201", pointer=f'/{n["create_env"]}/{n["created"]}/{n["ticket"]}')],
                questions=[dict(id="q1", text="How will booking ownership be verified before a request is filed?", configuration_key=None)],
                expected_reads=["Booking"], expected_writes=["Service request"], assumptions=[], limitations=[], risk="medium", risk_rationale="Creates requests")


class DeskSubstitute:
    """AUTHORED TEST SUBSTITUTE: deterministic outputs; revisions follow a scripted queue."""
    def __init__(self, settings, store):
        self.store, self.names, self.defect, self.revisions, self.instructions = store, None, None, [], []

    def call(self, kind, payload, output_model, run):
        self.store.attempt(run, "TEST_SUBSTITUTE", "authored-test-only", "succeeded")
        inv = payload["inventory"]
        if kind == "generation":
            return GenerationOutput(proposals=[desk_proposal(inv, self.names, self.defect)], capability_gaps=[])
        if kind.startswith("repair"):
            self.instructions.append(payload["instruction"])
            action = self.revisions.pop(0)
            if action in ("gap", "cannot"):
                return RepairOutput(outcome="capability_gap" if action == "gap" else "cannot_repair", revised_proposal=None,
                                    explanation="No supported operation returns an attachment identifier" if action == "gap" else "No response field identifies the booking")
            if action == "unchanged":
                return RepairOutput(outcome="revised", explanation="Kept the wiring", revised_proposal=payload["proposal"])
            proposal = desk_proposal(inv, self.names, "wrong_pointer" if action == "still_wrong" else None, suffix=f" ({len(self.instructions)})")
            if action == "new_input":
                proposal["steps"][1]["bindings"][2]["reference"] = "account_to_bill"
            if action == "extra_step":
                proposal["steps"].append(dict(proposal["steps"][0], id="s3"))
            return RepairOutput(outcome="revised", explanation="Rewired the booking identifier", revised_proposal=proposal)
        answers = payload["proposal"]["answers"]
        findings = [dict(question_id=q["id"], status="resolved" if answers.get(q["id"], {}).get("text", "").startswith("TEST-ONLY") else "insufficient",
                         explanation="Test substitute assessment", answer_revision_ids=[answers[q["id"]]["id"]] if q["id"] in answers else [])
                    for q in payload["proposal"]["content"]["questions"]]
        return ReconciliationOutput(findings=findings + requirement_findings(payload), revised_proposal=None, capability_gaps=[])


def fixture_transport(target):
    """Executor traffic goes to the in-process fixture; only status, content type and body cross over."""
    client = TestClient(target)
    def handle(request):
        r = client.request(request.method, request.url.raw_path.decode(), headers={k: v for k, v in request.headers.items() if k.lower() != "host"}, content=request.content)
        return httpx.Response(r.status_code, headers={"content-type": r.headers.get("content-type", "")}, content=r.content)
    return httpx.MockTransport(handle), client


def make_desk(tmp_path, variant, defect=None):
    target = create_fixture(variant, KEY)
    n = target.state.names
    transport, backend = fixture_transport(target)
    h = {"x-harness-key": KEY}
    people = {name: backend.post("/_harness/accounts", json=dict(name=name), headers=h).json() for name in ("alice", "bob")}
    booking = {name: backend.post("/_harness/bookings", json=dict(account_id=a["id"]), headers=h).json() for name, a in people.items()}
    ctx = n["holder_id"]
    holder = lambda a: a["number"] if n["holder_int"] else a["id"]
    settings = Settings(_env_file=None, database_path=str(tmp_path / f"desk-{variant}.db"), session_secret="test-secret", openrouter_api_key="",
                        connectors_file=str(tmp_path / "connectors.json"), sandbox_hosts="desk.test:80")
    app = create_app(settings, DeskSubstitute)
    app.state.service.providers.names, app.state.service.providers.defect = n, defect
    app.state.service.execution_transport = transport
    client = TestClient(app).__enter__()
    biz = client.post("/api/v1/businesses", json=dict(name=f"Service desk ({variant}, test)", description="Customers report problems with their bookings")).json()
    spec = client.post(f'/api/v1/businesses/{biz["id"]}/specifications', files={"file": ("openapi.json", json.dumps(target.openapi()).encode())}).json()
    identities = {name: dict(token=a["token"], scope="end_user", context={ctx: holder(a)}) for name, a in people.items()}
    identities["expired"] = dict(token="not-a-valid-token", scope="end_user", context={ctx: holder(people["alice"])})
    with open(settings.connectors_file, "w", encoding="utf-8") as f:
        json.dump(dict(connectors={"desk": dict(business_id=biz["id"], base_url="http://desk.test", sandbox=True, context_fields=[ctx], identities=identities)}), f)
    state = lambda: backend.get("/_harness/state", headers=h).json()
    return SimpleNamespace(app=app, client=client, spec=spec, n=n, people=people, booking=booking, ctx=ctx, state=state, backend=backend, provider=app.state.service.providers)


def current(d, pid):
    return d.client.get(f"/api/v1/proposals/{pid}").json()


def post(d, pid, suffix, **body):
    p = current(d, pid)
    return d.client.post(f'/api/v1/proposals/{pid}/versions/{p["version"]}/{suffix}', json=dict(expected_revision=p["review_revision"], **body))


def review_and_build(d, pid):
    """TEST-ONLY review of the current version, operator enforcement plus its TEST-ONLY review, then a build."""
    p = current(d, pid)
    assert post(d, pid, "answers", answers={i: LABEL + "only the booking holder may file requests for a booking." for i in [q["id"] for q in p["content"]["questions"]] + [r["id"] for r in p["requirements"]]}).status_code == 200
    assert post(d, pid, "reconcile").json()["state"] == "ready_for_review"
    for r in current(d, pid)["requirements"]:
        assert post(d, pid, f'requirements/{r["id"]}/confirm').status_code == 200
    v = current(d, pid)["version"]
    assert post(d, pid, "decisions", action="approve_to_build", reason=LABEL + "approval", idempotency_key=f"desk-approval-{pid}-{v}").status_code == 200
    holder = f'/{d.n["lookup_env"]}/{d.n["record"]}/{d.n["holder"]}/{d.n["holder_id"]}'
    config = {r["id"]: dict(mechanism="delegated_user_credential") if r["kind"] == "caller_access" else
              dict(mechanism="response_field_matches_context", step_id="s1", response_status="200", pointer=holder, context_field=d.ctx, comparison="equals", check_point="after_step")
              for r in current(d, pid)["requirements"]}
    e = d.client.post(f"/api/v1/proposals/{pid}/enforcement", json=dict(connector_id="desk", enforcement=config))
    assert e.status_code == 200, e.text
    d.client.post(f'/api/v1/enforcement/{e.json()["id"]}/review', json=dict(note=LABEL + "enforcement review"))
    a = d.client.post(f"/api/v1/proposals/{pid}/artifacts", json=dict(connector_id="desk"))
    assert a.status_code == 200, a.text
    return a.json()


def generated(d):
    scope = [op(d.spec["inventory"], m, d.n["prefix"] + p)["id"] for m, p in (("GET", d.n["lookup"]), ("POST", d.n["create"]))]
    return d.client.post(f'/api/v1/inventories/{d.spec["id"]}/proposal-runs', json=dict(operation_ids=scope)).json()["proposal_ids"][0]


def arguments(d, who="alice", **extra):
    return dict(booking_reference=d.booking[who]["ref"], category=d.n["categories"][0], details="Leaking tap", **extra)


def sandbox_test(d, aid, name, expect, who="alice", **args):
    r = d.client.post(f"/api/v1/artifacts/{aid}/sandbox-tests", json=dict(name=name, identity=who, arguments=args or arguments(d), expect=expect))
    assert r.status_code == 200, r.text
    return r.json()
