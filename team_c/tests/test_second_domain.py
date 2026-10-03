"""Second domain: the local service-desk fixture and its renamed variant, plus the bounded repair loop.

MOCKED / AUTHORED: proposals come from an AUTHORED TEST SUBSTITUTE built from the discovered inventory,
and every review is a labeled TEST-ONLY decision. The live-model proof is scripts/domain_demo.py.
Expected outcomes come from the fixture's documented rules (fixtures/service_desk/app.py), and backend
state is read through the fixture's harness routes, independently of Team C.
"""
import json
from types import SimpleNamespace
import httpx
import pytest
from fastapi.testclient import TestClient
from conftest import requirement_findings
from fixtures.service_desk.app import create_app as create_fixture
from test_openapi_primary import op
from team_c.config import Settings
from team_c.models import GenerationOutput, ReconciliationOutput, RepairOutput
from team_c.repair import classify
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


@pytest.fixture(params=["base", "renamed"])
def desk(request, tmp_path):
    d = make_desk(tmp_path, request.param)
    yield d
    d.client.__exit__(None, None, None)


@pytest.fixture
def broken_desk(tmp_path):
    d = make_desk(tmp_path, "base", defect="wrong_pointer")
    yield d
    d.client.__exit__(None, None, None)


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


def test_mappings_and_requirements_follow_each_specification(desk):
    d = desk
    pid = generated(d)
    kinds = {(r["kind"], r["field"]) for r in current(d, pid)["requirements"]}
    assert kinds == {("caller_access", None), ("record_scope", f'query.{d.n["ref"]}'), ("record_scope", f'body.{d.n["body_id"]}')}
    a = review_and_build(d, pid)["content"]
    s1, s2 = a["steps"]
    assert (s1["method"], s1["path"], s2["method"], s2["path"]) == ("GET", d.n["prefix"] + d.n["lookup"], "POST", d.n["prefix"] + d.n["create"])
    assert s1["parameters"][0]["location"] == "query" and s1["parameters"][0]["name"] == d.n["ref"]
    carried = next(f for f in s2["body"]["fields"] if f["name"] == d.n["body_id"])
    assert carried["source"] == dict(kind="previous_operation_output", step_id="s1", response_status="200", reference=f'/{d.n["lookup_env"]}/{d.n["record"]}/{d.n["internal"]}')
    assert list(s2["responses"]) == ["201"] and a["connector"]["auth"]["type"] == "http_bearer"
    attach = op(d.spec["inventory"], "POST", d.n["prefix"] + d.n["attach"])
    assert not attach["supported"] and attach["id"] not in [s["operation_id"] for s in a["steps"]]
    checks = [r["enforcement"] for r in a["access_requirements"] if r["kind"] == "record_scope"]
    assert {c["context_field"] for c in checks} == {d.ctx} and all(c["step_id"] == "s1" for c in checks)


def test_owner_request_succeeds_and_backend_records_exactly_it(desk):
    d = desk
    a = review_and_build(d, generated(d))
    result = sandbox_test(d, a["id"], "owner_files_request", dict(status="succeeded", outputs_present=["ticket"]))
    assert result["verdict"] == "passed"
    requests = d.state()["requests"]
    assert requests == [dict(ticket="SR-0001", booking=d.booking["alice"]["id"], filed_by=d.people["alice"]["id"], category=d.n["categories"][0], text="Leaking tap")]


def test_cross_user_request_is_blocked_before_the_write_and_backend_unchanged(desk):
    d = desk
    a = review_and_build(d, generated(d))
    result = sandbox_test(d, a["id"], "cross_user", dict(status="failed", failure_step="s1", failure_outcome="blocked_by_access_check"), who="bob", **arguments(d, "alice"))
    assert result["verdict"] == "passed" and d.state()["requests"] == []
    # The backend rule holds independently: a direct cross-user filing is refused.
    field = {d.n["body_id"]: d.booking["alice"]["id"], d.n["category"]: d.n["categories"][0], d.n["text"]: "x"}
    direct = d.backend.post(d.n["prefix"] + d.n["create"], json=field, headers={"Authorization": "Bearer " + d.people["bob"]["token"]})
    assert direct.status_code == 403 and d.state()["requests"] == []


def test_lookup_failure_prevents_the_dependent_write(desk):
    d = desk
    a = review_and_build(d, generated(d))
    result = sandbox_test(d, a["id"], "unknown_reference", dict(status="failed", failure_step="s1", failure_outcome="rejected_by_api"), **dict(arguments(d), booking_reference="BK-UNKNOWN"))
    assert result["verdict"] == "passed" and d.state()["requests"] == []


def test_mapping_defect_is_repaired_through_fresh_review(broken_desk):
    d = broken_desk
    pid = generated(d)
    a = review_and_build(d, pid)
    failed = sandbox_test(d, a["id"], "owner_files_request", dict(status="succeeded", outputs_present=["ticket"]))
    report = failed["failure_report"]
    assert failed["verdict"] == "failed" and report["classification"] == "mapping_suspect" and report["repairable"] and report["requires_verification"]
    assert report["step"]["id"] == "s2" and report["actual"]["trace"][1]["write_state"] == "rejected_unverified"
    assert any(b["target"] == f'body.{d.n["body_id"]}' for b in report["bindings"])
    # A 4xx on a write is not proof that nothing changed: repair waits for independent evidence.
    blocked = d.client.post(f'/api/v1/sandbox-tests/{failed["test_id"]}/repairs', json={})
    assert blocked.status_code == 409 and blocked.json()["code"] == "verification_required" and d.provider.instructions == []
    assert d.state()["requests"] == []
    d.provider.revisions = ["fix"]
    r = d.client.post(f'/api/v1/sandbox-tests/{failed["test_id"]}/repairs', json=dict(verification="TEST harness: backend request list unchanged (0 requests)"))
    assert r.status_code == 200 and r.json() == dict(r.json(), outcome="revised", attempt=1, version=2, state="needs_clarification")
    instruction = d.provider.instructions[0]
    assert "AUTOMATED REPAIR REQUEST" in instruction and "earlier_attempts" not in instruction
    assert all(secret not in instruction for secret in (d.people["alice"]["token"], d.booking["alice"]["ref"], d.booking["alice"]["id"], d.people["alice"]["id"], KEY))
    contract = json.loads(instruction[instruction.index("{"):])["contract_evidence"]
    assert contract["observed_status"] == 404 and contract["documented_meaning_of_observed_status"] == "Unknown booking"
    assert list(contract["bound_input_schemas"]) == [f'body.{d.n["body_id"]}', f'body.{d.n["category"]}', f'body.{d.n["text"]}']
    assert d.n["internal"] in json.dumps(contract["source_response_schemas"])
    # The repaired version starts unapproved, without enforcement or confirmed requirements; the old artifact cannot run.
    p = current(d, pid)
    assert p["state"] == "needs_clarification" and p["enforcement"] is None and all(r["status"] != "owner_confirmed" for r in p["requirements"])
    assert any(f'input body.{d.n["body_id"]}' in line and d.n["internal"] in line for line in r.json()["changes"])
    assert d.client.post(f'/api/v1/artifacts/{a["id"]}/sandbox-runs', json=dict(identity="alice", arguments=arguments(d))).json()["code"] == "approval_not_current"
    repaired = review_and_build(d, pid)
    assert repaired["id"] != a["id"] and repaired["content"]["proposal"]["version"] == 2
    assert sandbox_test(d, repaired["id"], "owner_files_request", dict(status="succeeded", outputs_present=["ticket"]))["verdict"] == "passed"
    assert len(d.state()["requests"]) == 1
    p = current(d, pid)
    assert [v["state"] for v in p["versions"]] == ["superseded", "approved_to_build"] and [x["outcome"] for x in p["repairs"]] == ["verification_required", "revised"]


def test_repairs_stop_after_two_attempts_and_keep_history(broken_desk):
    d = broken_desk
    pid = generated(d)
    d.provider.revisions = ["still_wrong", "still_wrong"]
    evidence = dict(verification="TEST harness: no request was created")
    for attempt in (1, 2):
        a = review_and_build(d, pid)
        failed = sandbox_test(d, a["id"], "owner_files_request", dict(status="succeeded"))
        r = d.client.post(f'/api/v1/sandbox-tests/{failed["test_id"]}/repairs', json=evidence).json()
        assert r["outcome"] == "revised" and r["attempt"] == attempt
    a = review_and_build(d, pid)
    failed = sandbox_test(d, a["id"], "owner_files_request", dict(status="succeeded"))
    r = d.client.post(f'/api/v1/sandbox-tests/{failed["test_id"]}/repairs', json=evidence)
    assert r.status_code == 409 and r.json()["code"] == "repair_exhausted" and len(d.provider.instructions) == 2
    earlier = json.loads(d.provider.instructions[1][d.provider.instructions[1].index("{"):])["earlier_attempts"]
    assert [(e["attempt"], e["version"], e["result"]) for e in earlier] == [(1, 1, "revised")] and "Version 2 created" in earlier[0]["feedback"]
    p = current(d, pid)
    assert [x["outcome"] for x in p["repairs"]] == ["revised", "revised", "exhausted"] and len(p["versions"]) == 3
    assert d.state()["requests"] == []


def test_missing_capability_ends_repair_without_a_rewrite(desk):
    d = desk
    pid = generated(d)
    a = review_and_build(d, pid)
    failed = sandbox_test(d, a["id"], "attachment_reference", dict(status="succeeded", outputs_present=["attachment_id"]))
    assert failed["failure_report"]["classification"] == "mapping_suspect" and not failed["failure_report"]["requires_verification"]
    d.provider.revisions = ["gap"]
    r = d.client.post(f'/api/v1/sandbox-tests/{failed["test_id"]}/repairs', json={}).json()
    assert r["outcome"] == "capability_gap" and "attachment" in r["detail"]
    p = current(d, pid)
    assert p["version"] == 1 and p["state"] == "approved_to_build" and p["repairs"][0]["outcome"] == "capability_gap"


def test_unchanged_candidate_gives_the_second_attempt_its_history(broken_desk):
    d = broken_desk
    pid = generated(d)
    a = review_and_build(d, pid)
    failed = sandbox_test(d, a["id"], "owner_files_request", dict(status="succeeded"))
    d.provider.revisions = ["unchanged", "fix"]
    evidence = dict(verification="TEST harness: no request was created")
    first = d.client.post(f'/api/v1/sandbox-tests/{failed["test_id"]}/repairs', json=evidence)
    assert first.status_code == 422 and first.json()["code"] == "revision_not_changed"
    second = d.client.post(f'/api/v1/sandbox-tests/{failed["test_id"]}/repairs', json=evidence).json()
    assert second["outcome"] == "revised" and second["attempt"] == 2
    parts = json.loads(d.provider.instructions[1][d.provider.instructions[1].index("{"):])
    [attempt] = parts["earlier_attempts"]
    wrong = f'/{d.n["lookup_env"]}/{d.n["record"]}/{d.n["holder"]}/{d.n["holder_id"]}'
    assert attempt["result"] == "revision_not_changed" and "candidate changes: none" in attempt["feedback"]
    assert "HTTP 404" in attempt["feedback"] and f'body.{d.n["body_id"]} <- previous_operation_output {wrong} from s1 200' in attempt["feedback"]
    assert parts["failure_report"] == json.loads(d.provider.instructions[0][d.provider.instructions[0].index("{"):])["failure_report"]
    assert [x["outcome"] for x in current(d, pid)["repairs"]] == ["revision_not_changed", "revised"]


def test_scope_rejection_and_cannot_repair_are_recorded_distinctly(desk):
    d = desk
    pid = generated(d)
    a = review_and_build(d, pid)
    failed = sandbox_test(d, a["id"], "attachment_reference", dict(status="succeeded", outputs_present=["attachment_id"]))
    d.provider.revisions = ["extra_step", "cannot"]
    r = d.client.post(f'/api/v1/sandbox-tests/{failed["test_id"]}/repairs', json={})
    assert r.status_code == 422 and r.json()["code"] == "repair_out_of_scope"
    r = d.client.post(f'/api/v1/sandbox-tests/{failed["test_id"]}/repairs', json={}).json()
    assert r["outcome"] == "cannot_repair" and r["attempt"] == 2
    history = json.loads(d.provider.instructions[1][d.provider.instructions[1].index("{"):])["earlier_attempts"]
    assert history[0]["result"] == "repair_out_of_scope" and "Step s3 added" in history[0]["feedback"]
    assert d.client.post(f'/api/v1/sandbox-tests/{failed["test_id"]}/repairs', json={}).json()["code"] == "repair_exhausted" and len(d.provider.instructions) == 2
    p = current(d, pid)
    assert p["version"] == 1 and p["state"] == "approved_to_build" and [x["outcome"] for x in p["repairs"]] == ["repair_out_of_scope", "cannot_repair", "exhausted"]


def test_authentication_failure_requests_configuration_not_a_rewrite(desk):
    d = desk
    a = review_and_build(d, generated(d))
    failed = sandbox_test(d, a["id"], "owner_files_request", dict(status="succeeded"), who="expired", **arguments(d))
    assert failed["failure_report"]["classification"] == "access_or_authentication" and not failed["failure_report"]["repairable"]
    r = d.client.post(f'/api/v1/sandbox-tests/{failed["test_id"]}/repairs', json={})
    assert r.status_code == 422 and r.json()["code"] == "repair_not_applicable" and d.provider.instructions == []


def test_repair_that_adds_inputs_is_rejected_and_version_kept(broken_desk):
    d = broken_desk
    pid = generated(d)
    a = review_and_build(d, pid)
    failed = sandbox_test(d, a["id"], "owner_files_request", dict(status="succeeded"))
    d.provider.revisions = ["new_input"]
    r = d.client.post(f'/api/v1/sandbox-tests/{failed["test_id"]}/repairs', json=dict(verification="TEST harness: no request was created"))
    assert r.status_code == 422 and r.json()["code"] == "repair_out_of_scope"
    p = current(d, pid)
    assert p["version"] == 1 and p["repairs"][-1]["outcome"] == "repair_out_of_scope" and p["repairs"][-1]["run_id"]


@pytest.mark.parametrize("report,kind", [
    (dict(status="outcome_unknown", failure=dict(step_id="s2", outcome="no_response")), "uncertain_write"),
    (dict(status="partial", failure=dict(step_id="s3", outcome="invalid_response")), "partial_write"),
    (dict(status="failed", failure=dict(step_id="connector_auth", outcome="connector_auth_failed")), "authentication"),
    (dict(status="rejected", code="credential_missing"), "configuration"),
])
def test_uncertain_or_configuration_failures_are_never_repairable(report, kind):
    result = classify(dict(status="succeeded"), report, report.get("failure") or {}, [], [])
    assert result[0] == kind and result[1] is False
