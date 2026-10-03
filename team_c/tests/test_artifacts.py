"""Approved proposal -> artifact -> sandbox execution, against a MOCK target shaped like the real API.

Every approval here is a labeled TEST-ONLY decision. Expected outcomes come from the approved intent
(an owner looks up and updates their own item) and the API contract, not from the executor.
"""
import json
import uuid
import httpx
import pytest
from fastapi.testclient import TestClient
from test_review_lifecycle import LifecycleSubstitute, approve, answer_all, confirm_all, current, generate, lifecycle, post, supersede_q2  # noqa: F401
from team_c.artifacts import compile_steps
from team_c.executor import Run
from team_c.web import create_app

ALICE, BOB, ADMIN = (str(uuid.UUID(int=i)) for i in (1, 2, 3))
ITEM_A, ITEM_B = str(uuid.UUID(int=10)), str(uuid.UUID(int=20))
SECRETS = ("alice-secret-pw", "bob-secret-pw", "admin-secret-pw")


class FakeTarget:
    """MOCK target: password grant, per-owner items, 404 when missing and 403 for other owners."""
    def __init__(self):
        self.users = {"alice@test": ("alice-secret-pw", ALICE), "bob@test": ("bob-secret-pw", BOB), "admin@test": ("admin-secret-pw", ADMIN)}
        self.items = {ITEM_A: dict(id=ITEM_A, title="Alice item", description="a", owner_id=ALICE),
                      ITEM_B: dict(id=ITEM_B, title="Bob item", description="b", owner_id=BOB)}
        self.requests, self.mode = [], None

    def __call__(self, request):
        method, path = request.method, request.url.path
        self.requests.append((method, path))
        if path == "/api/v1/login/access-token":
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            user = self.users.get(form.get("username", "").replace("%40", "@"))
            if not user or user[0] != form.get("password"):
                return httpx.Response(400, json={"detail": "Incorrect email or password"})
            return httpx.Response(200, json={"access_token": "token-" + user[1], "token_type": "bearer"})
        caller = request.headers.get("authorization", "").removeprefix("Bearer token-")
        if caller not in (ALICE, BOB, ADMIN):
            return httpx.Response(401, json={"detail": "Not authenticated"})
        item = self.items.get(path.rsplit("/", 1)[-1])
        if method == "GET" and self.mode == "redirect":
            return httpx.Response(307, headers={"location": "http://elsewhere.test/"})
        if method == "GET" and self.mode == "oversized":
            return httpx.Response(200, json=dict(item, description="x" * 300_000))
        if item is None:
            return httpx.Response(404, json={"detail": "Item not found"})
        if item["owner_id"] != caller and self.mode != "leaky_read":
            return httpx.Response(403, json={"detail": "Not enough permissions"})
        if method == "GET":
            return httpx.Response(200, json=item)
        if self.mode == "put_conflict":
            return httpx.Response(409, json={"detail": "Conflict"})
        item.update({k: v for k, v in json.loads(request.content).items() if v is not None})
        if self.mode == "put_timeout":
            raise httpx.ReadTimeout("no response", request=request)
        if self.mode == "put_error":
            return httpx.Response(500, text="Internal Server Error")
        if self.mode == "put_bad_body":
            return httpx.Response(200, json={"title": item["title"]})
        return httpx.Response(200, json=item)

    def sent(self, method):
        return [r for r in self.requests if r[0] == method]


def identity(username, subject, scope="end_user"):
    return dict(username=username, password=next(p for u, (p, s) in FakeTarget().users.items() if u == username), scope=scope, context=dict(user_id=subject))


def write_connectors(settings, business_id, **overrides):
    connector = dict(business_id=business_id, base_url="http://target.test", sandbox=True, context_fields=["user_id"], identities=dict(
        alice=identity("alice@test", ALICE), bob=identity("bob@test", BOB), admin=identity("admin@test", ADMIN, scope="service"),
        no_credential=dict(scope="end_user", context=dict(user_id=ALICE))))
    connector.update(overrides)
    with open(settings.connectors_file, "w", encoding="utf-8") as f:
        json.dump(dict(connectors={"items-sandbox": connector}), f)


def enforcement(p):
    return {r["id"]: dict(mechanism="delegated_user_credential") if r["kind"] == "caller_access"
            else dict(mechanism="response_field_matches_context", step_id="s1", response_status="200", pointer="/owner_id", context_field="user_id",
                      comparison="equals", check_point="after_step") for r in p["requirements"]}


def submit(client, pid, config):
    return client.post(f"/api/v1/proposals/{pid}/enforcement", json=dict(connector_id="items-sandbox", enforcement=config))


def review(client, eid):
    return client.post(f"/api/v1/enforcement/{eid}/review", json=dict(note="TEST-ONLY enforcement review"))


@pytest.fixture
def sandbox(lifecycle, tmp_path):
    app, client, settings, spec, scope = lifecycle
    settings.connectors_file, settings.sandbox_hosts = str(tmp_path / "connectors.json"), "target.test:80"
    pid = generate(client, spec, scope)
    write_connectors(settings, current(client, pid)["business_id"])
    target = FakeTarget()
    app.state.service.execution_transport = httpx.MockTransport(target)
    return app, client, settings, spec, pid, target


def approved(client, pid):
    """TEST-ONLY review: labeled answers, confirmations and approval."""
    supersede_q2(client, pid)
    answer_all(client, pid)
    post(client, pid, "reconcile")
    assert post(client, pid, "reconcile").json()["state"] == "ready_for_review"
    confirm_all(client, pid)
    assert approve(client, pid).status_code == 200
    return current(client, pid)


def build(client, pid, enforce=True):
    if enforce and current(client, pid)["state"] == "approved_to_build":
        e = submit(client, pid, enforcement(current(client, pid)))
        assert e.status_code == 200, e.text
        assert review(client, e.json()["id"]).json()["status"] == "approved"
    return client.post(f"/api/v1/proposals/{pid}/artifacts", json=dict(connector_id="items-sandbox"))


def run(client, aid, who="alice", **arguments):
    return client.post(f"/api/v1/artifacts/{aid}/sandbox-runs", json=dict(identity=who, arguments=arguments))


@pytest.fixture
def built(sandbox):
    app, client, settings, spec, pid, target = sandbox
    p = approved(client, pid)
    r = build(client, pid)
    assert r.status_code == 200, r.text
    return app, client, settings, spec, p, r.json(), target


def test_build_requires_current_approval(sandbox):
    _, client, _, _, pid, target = sandbox
    r = build(client, pid)
    assert r.status_code == 409 and r.json()["code"] == "approval_not_current"
    assert target.requests == []


def test_artifact_is_deterministic_complete_and_secret_free(built):
    _, client, _, spec, p, a, _ = built
    c = a["content"]
    assert build(client, p["proposal_id"]).json()["id"] == a["id"]
    assert c["proposal"] == dict(c["proposal"], id=p["proposal_id"], version=p["version"], decision_id=p["decisions"][0]["id"])
    assert c["source"]["spec_sha256"] == spec["checksum"] and [o["method"] for o in c["source"]["operations"]] == ["GET", "PUT"]
    assert c["input_schema"]["required"] == ["item_id"] and c["input_schema"]["additionalProperties"] is False
    s1, s2 = c["steps"]
    assert (s1["effect"], s2["effect"]) == ("read", "write")
    assert s2["parameters"] == [dict(name="id", location="path", type="string", style="simple", explode=False, source=dict(kind="runtime_argument", reference="item_id"))]
    assert sorted(f["name"] for f in s2["body"]["fields"]) == ["description", "title"] and list(s2["responses"]) == ["200"]
    assert c["connector"] == dict(id="items-sandbox", base_url="http://target.test", auth=dict(c["connector"]["auth"], type="oauth2_password", token_path="/api/v1/login/access-token"))
    assert {r["enforcement"]["mechanism"]: r["enforcement"]["enforced_by"] for r in c["access_requirements"]} == {"delegated_user_credential": "target_api", "response_field_matches_context": "team_c_executor"}
    assert c["enforcement_config"]["reviewed_by"] and c["enforcement_config"]["submitted_by"]
    assert c["execution_blockers"] == [] and c["limits"]["follow_redirects"] is False and c["limits"]["retries"] == 0
    assert c["activation"] == dict(c["activation"], runtime_ready=False, activated=False, permitted_uses=["artifact_creation", "sandbox_testing"])
    text = json.dumps(a)
    assert not any(s in text for s in SECRETS + ("alice@test", "token-"))


def test_successful_lookup_and_update_changes_only_the_owned_item(built):
    _, client, _, _, _, a, target = built
    before_b = dict(target.items[ITEM_B])
    r = run(client, a["id"], item_id=ITEM_A, title="Renamed by sandbox test")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["report"]["status"] == "succeeded" and body["outputs"]["updated_item"] == "Renamed by sandbox test"
    assert target.items[ITEM_A]["title"] == "Renamed by sandbox test" and target.items[ITEM_A]["description"] == "a"
    assert target.items[ITEM_B] == before_b
    assert [e["write_state"] for e in body["report"]["trace"]] == [None, "applied"]
    text = json.dumps(body) + json.dumps(client.get(f'/api/v1/artifacts/{a["id"]}').json())
    assert not any(s in text for s in SECRETS + ("token-",))


@pytest.mark.parametrize("arguments,error", [
    ({}, "item_id: required"),
    ({"item_id": 5}, "item_id: violates type"),
    ({"item_id": ITEM_A, "owner_id": BOB}, "owner_id: unknown argument"),
    ({"item_id": ITEM_A, "title": ""}, "title: violates minLength"),
])
def test_invalid_arguments_are_rejected_before_any_request(built, arguments, error):
    _, client, _, _, _, a, target = built
    r = run(client, a["id"], **arguments)
    assert r.status_code == 422 and error in r.json()["details"]["errors"]
    assert target.requests == []
    e = client.get(f'/api/v1/artifacts/{a["id"]}').json()["executions"][-1]
    assert e["status"] == "rejected" and e["report"]["requests_sent"] == 0 and BOB not in json.dumps(e)


@pytest.mark.parametrize("who,code", [("no_credential", "credential_missing"), ("admin", "credential_scope"), ("mallory", "identity_unknown")])
def test_missing_or_privileged_credentials_are_refused(built, who, code):
    _, client, _, _, _, a, target = built
    r = run(client, a["id"], who=who, item_id=ITEM_A, title="x")
    assert r.json()["code"] == code and r.json()["details"]["execution_id"]
    assert target.requests == [] and target.items[ITEM_A]["title"] == "Alice item"


def test_cross_user_read_is_denied_and_the_update_never_sent(built):
    _, client, _, _, _, a, target = built
    before = dict(target.items[ITEM_B])
    report = run(client, a["id"], item_id=ITEM_B, title="Hijacked").json()["report"]
    assert report["status"] == "failed" and report["failure"] == dict(step_id="s1", outcome="rejected_by_api", detail="HTTP 403")
    assert "No write was sent" in report["message"] and report["trace"][1]["write_state"] == "not_attempted"
    assert target.sent("PUT") == [] and target.items[ITEM_B] == before


def test_ownership_check_blocks_even_when_the_target_leaks_a_record(built):
    _, client, _, _, _, a, target = built
    target.mode = "leaky_read"
    before = dict(target.items[ITEM_B])
    report = run(client, a["id"], item_id=ITEM_B, title="Hijacked").json()["report"]
    assert report["failure"]["outcome"] == "blocked_by_access_check" and report["status"] == "failed"
    assert target.sent("PUT") == [] and target.items[ITEM_B] == before


def test_failed_lookup_prevents_update(built):
    _, client, _, _, _, a, target = built
    report = run(client, a["id"], item_id=str(uuid.UUID(int=99)), title="x").json()["report"]
    assert report["failure"]["detail"] == "HTTP 404" and target.sent("PUT") == []


@pytest.mark.parametrize("mode,status,state,puts", [
    ("put_timeout", "outcome_unknown", "unknown", 1),
    ("put_error", "outcome_unknown", "unknown", 1),
    ("put_conflict", "failed", "rejected_unverified", 1),
    ("put_bad_body", "partial", "applied", 1),
    ("redirect", "failed", None, 0),
    ("oversized", "failed", None, 0),
])
def test_failures_are_reported_truthfully_without_retry(built, mode, status, state, puts):
    _, client, _, _, _, a, target = built
    target.mode = mode
    report = run(client, a["id"], item_id=ITEM_A, title="Changed").json()["report"]
    assert report["status"] == status and len(target.sent("PUT")) == puts
    assert report["trace"][1]["write_state"] == (state or "not_attempted")
    if status == "outcome_unknown":
        assert "may or may not have been applied" in report["message"] and "Nothing was retried" in report["message"]
    if mode == "put_conflict":
        assert "has not been verified" in report["message"] and "no change" not in report["message"].replace("no change occurred has not", "")
    if mode == "redirect":
        assert report["failure"]["outcome"] == "redirect_rejected" and ("GET", "/") not in target.requests


def test_invalid_output_binding_stops_before_the_dependent_write(built):
    app, _, _, _, _, a, target = built
    artifact = json.loads(json.dumps(a["content"]))
    artifact["steps"][1]["parameters"][0]["source"] = dict(kind="previous_operation_output", step_id="s1", response_status="200", reference="/missing")
    identity_ = identity("alice@test", ALICE)
    report, outputs = Run(artifact, dict(item_id=ITEM_A, title="x"), identity_, "http://target.test", httpx.MockTransport(target)).execute()
    assert report["failure"]["outcome"] == "invalid_binding" and outputs is None and target.sent("PUT") == []


def test_unsupported_semantics_are_rejected_at_compile_time(built):
    app, _, _, spec, p, _, _ = built
    inv = app.state.service.spec(spec["id"])["inventory"]
    ops = {o["id"]: json.loads(json.dumps(o)) for o in inv["operations"]}
    put = ops[p["content"]["steps"][1]["operation_id"]]
    put["inputs"]["header.X-Tenant"] = dict(schema=dict(type="string"), required=False)
    content = json.loads(json.dumps(p["content"]))
    content["steps"][1]["bindings"].append(dict(target="header.X-Tenant", kind="runtime_argument", reference="tenant"))
    content["steps"][1]["bindings"] = [b for b in content["steps"][1]["bindings"] if b["target"] != "path.id"]
    missing = []
    compile_steps(content, ops, missing)
    assert any("header" in m for m in missing) and any("path parameter id has no executable binding" in m for m in missing)


def test_missing_enforcement_blocks_execution(sandbox):
    _, client, _, _, pid, target = sandbox
    approved(client, pid)
    a = build(client, pid, enforce=False).json()
    assert len(a["content"]["execution_blockers"]) == 2
    r = run(client, a["id"], item_id=ITEM_A, title="x")
    assert r.status_code == 409 and r.json()["code"] == "access_enforcement_missing" and target.requests == []


@pytest.mark.parametrize("change", [dict(mechanism="owner_confirmed_checkbox"), dict(pointer="/nope"), dict(step_id="s9"), dict(context_field="username"),
                                    dict(step_id="s2"), dict(comparison="contains"), dict(check_point="after_all_steps"), dict(extra="x")])
def test_unenforceable_mechanisms_are_refused(sandbox, change):
    _, client, _, _, pid, _ = sandbox
    p = approved(client, pid)
    config = enforcement(p)
    rid = next(r["id"] for r in p["requirements"] if r["kind"] == "record_scope")
    config[rid].update(change)
    r = submit(client, pid, config)
    assert r.status_code == 422 and r.json()["code"] == "enforcement_invalid"
    # Enforcement never arrives with the build request (so it cannot bypass review).
    assert client.post(f"/api/v1/proposals/{pid}/artifacts", json=dict(connector_id="items-sandbox", enforcement=config)).status_code == 422


def test_enforcement_needs_review_and_a_change_invalidates_built_artifacts(built):
    _, client, _, _, p, a, target = built
    pid = p["proposal_id"]
    caller_only = {r["id"]: dict(mechanism="delegated_user_credential") for r in p["requirements"] if r["kind"] == "caller_access"}
    e = submit(client, pid, caller_only).json()
    assert e["status"] == "awaiting_review" and e["submitted_by"] and e["reviewed_at"] is None
    assert run(client, a["id"], item_id=ITEM_A, title="x").json()["code"] == "enforcement_not_current"
    assert client.post(f"/api/v1/proposals/{pid}/artifacts", json=dict(connector_id="items-sandbox")).json()["code"] == "enforcement_unreviewed"
    assert review(client, e["id"]).json()["status"] == "approved"
    rebuilt = client.post(f"/api/v1/proposals/{pid}/artifacts", json=dict(connector_id="items-sandbox")).json()
    assert rebuilt["id"] != a["id"] and len(rebuilt["content"]["execution_blockers"]) == 1
    assert run(client, rebuilt["id"], item_id=ITEM_A, title="x").json()["code"] == "access_enforcement_missing"
    assert target.requests == []


def test_identity_without_the_checked_context_field_is_refused(built):
    _, client, settings, _, p, a, target = built
    write_connectors(settings, p["business_id"], identities=dict(alice=dict(identity("alice@test", ALICE), context={})))
    assert run(client, a["id"], item_id=ITEM_A, title="x").json()["code"] == "context_incomplete" and target.requests == []


@pytest.mark.parametrize("overrides,hosts,code", [
    (dict(base_url="http://other.test"), "target.test:80,other.test:80", "connector_changed"),
    (dict(sandbox=False), "target.test:80", "not_sandbox"),
    ({}, "somewhere.test:80", "destination_not_allowed"),
])
def test_destination_must_be_the_configured_sandbox(built, overrides, hosts, code):
    _, client, settings, _, p, a, target = built
    write_connectors(settings, p["business_id"], **overrides)
    settings.sandbox_hosts = hosts
    assert run(client, a["id"], item_id=ITEM_A, title="x").json()["code"] == code and target.requests == []


def test_revised_or_altered_versions_cannot_reuse_the_approval(built):
    app, client, _, _, p, a, target = built
    pid = p["proposal_id"]
    with app.state.store.connect(write=True) as c:
        original = c.execute("SELECT content FROM versions WHERE proposal_id=? AND version=?", (pid, p["version"])).fetchone()[0]
        altered = json.loads(original)
        altered["steps"][1]["purpose"] = "Also change the owner"
        c.execute("UPDATE versions SET content=? WHERE proposal_id=? AND version=?", (json.dumps(altered), pid, p["version"]))
    assert build(client, pid, enforce=False).json()["code"] == "approval_not_current"
    assert run(client, a["id"], item_id=ITEM_A, title="x").json()["code"] == "approval_not_current"
    with app.state.store.connect(write=True) as c:
        c.execute("UPDATE versions SET content=? WHERE proposal_id=? AND version=?", (original, pid, p["version"]))
    rev = client.post(f"/api/v1/proposals/{pid}/revisions", json=dict(expected_revision=current(client, pid)["review_revision"], instruction="Use a clearer name"))
    assert rev.status_code == 200, rev.text
    assert run(client, a["id"], item_id=ITEM_A, title="x").json()["code"] == "approval_not_current"
    assert build(client, pid).json()["code"] == "approval_not_current"
    assert target.requests == []


def test_tampered_artifact_is_refused(built):
    app, client, _, _, _, a, _ = built
    with app.state.store.connect(write=True) as c:
        c.execute("UPDATE artifacts SET content=replace(content,'target.test','evil.test') WHERE id=?", (a["id"],))
    assert client.get(f'/api/v1/artifacts/{a["id"]}').json()["code"] == "artifact_integrity"


def test_history_survives_restart_and_running_becomes_interrupted(built):
    app, client, settings, _, _, a, _ = built
    run(client, a["id"], item_id=ITEM_A, title="Persisted")
    run(client, a["id"], item_id=ITEM_A)
    with app.state.store.connect(write=True) as c:
        c.execute("INSERT INTO executions(id,artifact_id,mode,identity,status,report,created_at,completed_at) VALUES('stuck',?,'sandbox','alice','running',NULL,'2026-01-01T00:00:00Z',NULL)", (a["id"],))
    with TestClient(create_app(settings, LifecycleSubstitute)) as restarted:
        executions = restarted.get(f'/api/v1/artifacts/{a["id"]}').json()["executions"]
        assert sorted(e["status"] for e in executions) == ["interrupted", "succeeded", "succeeded"]
        assert restarted.get(f'/artifacts/{a["id"]}').status_code == 200


def test_ui_shows_artifacts_and_sandbox_controls(built):
    _, client, _, _, p, a, _ = built
    page = client.get(f'/proposals/{p["proposal_id"]}').text
    assert "Executable artifacts" in page and f'/artifacts/{a["id"]}' in page and "items-sandbox" in page
    detail = client.get(f'/artifacts/{a["id"]}').text
    assert "Runtime-ready: no" in detail and "Run sandbox test" in detail and "response_field_matches_context" in detail
    assert "Access enforcement" in page and "approved" in page
    assert not any(s in detail for s in SECRETS)
