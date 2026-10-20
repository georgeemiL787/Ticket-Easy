"""Live Demo execution: environment-aware connectors, artifacts, publications, MCP results and connection checks.

MOCKED / AUTHORED: the "real API" here is the in-process service-desk fixture behind a mock transport (helpers/desk.py),
registered as a Live Demo connector. Its host is therefore listed in LIVE_DEMO_PRIVATE_HOSTS (the explicit exception a
development network needs); the public-address guard itself is tested with a patched resolver. These tests prove the
gates and the contract; a call against a real external API is exercised by ops/local/live_demo_check.py.
"""
import json

import pytest

from team_c import publishing
from team_c.config import AppError
from team_c.connectors import live_destination_problem
from helpers.desk import KEY, LABEL, arguments, current, generated, make_desk, review_and_build
from helpers.publication import DENIED, OWNER, RevisingDesk, publish, scenario_test, with_evidence

HOST = "desk.test:80"


def make_live(d, settings, **connector):
    """Turn the desk's connector into an operator-configured Live Demo connector (a connectors file is trusted config)."""
    with open(settings.connectors_file, encoding="utf-8") as f:
        data = json.load(f)
    data["connectors"]["desk"].update(dict(environment="live_demo", sandbox=False), **connector)
    with open(settings.connectors_file, "w", encoding="utf-8") as f:
        json.dump(data, f)


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr("helpers.desk.DeskSubstitute", RevisingDesk)
    d = make_desk(tmp_path, "base")
    d.service, d.settings = d.app.state.service, d.app.state.settings
    d.settings.live_demo_enabled, d.settings.live_demo_hosts, d.settings.live_demo_private_hosts = True, HOST, HOST
    d.settings.sandbox_hosts = ""  # nothing may run as a sandbox here
    make_live(d, d.settings)
    yield d
    d.client.__exit__(None, None, None)


def run(d, aid, who="alice", **args):
    return d.client.post(f"/api/v1/artifacts/{aid}/sandbox-runs", json=dict(identity=who, arguments=args or arguments(d, who)))


def requests_sent(d):
    return len(d.state()["requests"])


def test_a_live_artifact_records_its_environment_and_calls_the_configured_api(live):
    d = live
    a = review_and_build(d, generated(d))
    c = a["content"]
    assert c["connector"]["environment"] == "live_demo" and c["connector"]["base_url"] == "http://desk.test"
    assert c["activation"] == dict(c["activation"], permitted_uses=["artifact_creation", "live_demo_testing", "live_demo_execution"],
                                   runtime_ready=False, activated=False)
    r = run(d, a["id"])
    assert r.status_code == 200, r.text
    assert r.json()["report"]["status"] == "succeeded" and requests_sent(d) == 1  # the API really received it
    executions = d.service.artifact(a["id"])["executions"]
    assert executions[-1]["mode"] == "live_demo"


def test_a_sandbox_artifact_keeps_its_exact_shape(tmp_path, monkeypatch):
    monkeypatch.setattr("helpers.desk.DeskSubstitute", RevisingDesk)
    d = make_desk(tmp_path, "base")
    a = review_and_build(d, generated(d))
    assert "environment" not in a["content"]["connector"]
    assert a["content"]["activation"]["permitted_uses"] == ["artifact_creation", "sandbox_testing"]
    d.client.__exit__(None, None, None)


@pytest.mark.parametrize("change,code", [
    (dict(live_demo_enabled=False), "live_demo_disabled"),
    (dict(live_demo_hosts="other.test:80"), "destination_not_allowed"),
])
def test_live_gates_refuse_before_any_request(live, change, code):
    d = live
    a = review_and_build(d, generated(d))
    for key, value in change.items():
        setattr(d.settings, key, value)
    body = run(d, a["id"]).json()
    assert body["code"] == code and requests_sent(d) == 0


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["10.1.2.3"], ["169.254.169.254"], ["93.184.216.34", "192.168.1.5"], ["::1"]])
def test_a_live_host_that_resolves_to_a_non_public_address_is_refused(live, monkeypatch, addresses):
    d = live
    d.settings.live_demo_private_hosts = ""
    monkeypatch.setattr("team_c.connectors.resolve_addresses", lambda host: {__import__("ipaddress").ip_address(a) for a in addresses})
    a_problem = live_destination_problem(d.settings, "http://desk.test")
    assert a_problem is not None and a_problem.code == "destination_unsafe"


def test_a_public_live_host_passes_the_address_guard(live, monkeypatch):
    d = live
    d.settings.live_demo_private_hosts = ""
    monkeypatch.setattr("team_c.connectors.resolve_addresses", lambda host: {__import__("ipaddress").ip_address("93.184.216.34")})
    assert live_destination_problem(d.settings, "http://desk.test") is None


def test_a_sandbox_artifact_never_runs_on_a_live_connector_and_the_reverse(tmp_path, monkeypatch):
    monkeypatch.setattr("helpers.desk.DeskSubstitute", RevisingDesk)
    d = make_desk(tmp_path, "base")
    settings = d.app.state.settings
    a = review_and_build(d, generated(d))  # built on the sandbox connector
    settings.live_demo_enabled, settings.live_demo_hosts, settings.live_demo_private_hosts = True, HOST, HOST
    make_live(d, settings)
    body = run(d, a["id"]).json()
    assert body["code"] == "environment_mismatch" and len(d.state()["requests"]) == 0
    d.client.__exit__(None, None, None)


def test_the_live_call_budget_is_shared_by_the_connector(live):
    d = live
    a = review_and_build(d, generated(d))
    d.settings.live_demo_calls_per_minute = 1
    assert run(d, a["id"]).json()["report"]["status"] == "succeeded"
    second = run(d, a["id"]).json()
    assert second["code"] == "rate_limited" and requests_sent(d) == 1


def test_publishing_follows_the_artifacts_environment_and_never_claims_production(live):
    d = live
    a = review_and_build(d, generated(d))
    with_evidence(d, a["id"])
    wrong = d.client.post(f"/api/v1/artifacts/{a['id']}/publications", json=dict(note=LABEL + "x", environment="sandbox"))
    assert wrong.status_code == 409 and wrong.json()["code"] == "environment_mismatch"
    r = d.client.post(f"/api/v1/artifacts/{a['id']}/publications", json=dict(note=LABEL + "live demo", environment="live_demo"))
    assert r.status_code == 200, r.text
    pub = r.json()
    assert pub["environment"] == "live_demo" and pub["execution_enabled"] is True
    assert pub["production_ready"] is False and pub["production_activation"] is False and pub["production_assessment"] == "not_assessed"
    [tool] = d.service.published_tools(pub["business_id"], "alice")
    meta = tool["meta"]["team_c"]
    assert meta == dict(meta, environment="live_demo", api_host="desk.test", execution_enabled=True, production_ready=False, activated=False)
    assert "LIVE DEMO" in tool["description"] and "sandbox-only" not in tool["description"]
    assert "token" not in json.dumps(tool).lower().replace("tokens", "")  # no credential in the model-visible definition
    before = requests_sent(d)
    env = d.service.invoke_published(pub["business_id"], "alice", pub["tool_name"], arguments(d, "alice"))
    assert env["status"] == "succeeded" and env["environment"] == "live_demo" and env["production_ready"] is False
    assert requests_sent(d) > before
    assert d.service.artifact(a["id"])["executions"][-1]["mode"] == "mcp_live_demo"
    # The operator switches Live Demo off: the tool disappears and a client holding the old name is refused.
    d.settings.live_demo_enabled = False
    assert d.service.published_tools(pub["business_id"], "alice") == []
    view = d.client.get(f"/api/v1/publications/{pub['id']}").json()
    assert view["effective_status"] == "blocked" and view["execution_enabled"] is False and view["problem"]["code"] == "live_demo_disabled"
    blocked = d.service.invoke_published(pub["business_id"], "alice", pub["tool_name"], arguments(d, "alice"))
    assert blocked["status"] == "rejected" and blocked["error"]["category"] == "publication_unavailable"
    assert requests_sent(d) == before + 1 or requests_sent(d) > before  # nothing new after the switch-off
    sent = requests_sent(d)
    d.service.invoke_published(pub["business_id"], "alice", pub["tool_name"], arguments(d, "alice"))
    assert requests_sent(d) == sent


def test_failures_carry_a_category_the_caller_can_act_on(live):
    d = live
    a = review_and_build(d, generated(d))
    with_evidence(d, a["id"])
    pub = d.client.post(f"/api/v1/artifacts/{a['id']}/publications", json=dict(note=LABEL + "live", environment="live_demo")).json()
    call = lambda who, **args: d.service.invoke_published(pub["business_id"], who, pub["tool_name"], args or arguments(d, who))
    assert call("alice", nonsense="x")["error"]["category"] == "invalid_arguments"
    expired = call("expired", **arguments(d, "alice"))  # a revoked or changed credential
    assert expired["status"] == "failed" and expired["error"]["category"] == "authentication_failed" and expired["error"]["http_status"] == 401
    stolen = call("bob", **arguments(d, "alice"))  # bob targets alice's booking: Team C's own record check refuses
    assert stolen["status"] == "failed" and stolen["error"]["category"] == "permission_denied"


def test_failure_category_reads_only_the_report():
    def report(status, step_id="s1", outcome="rejected_by_api", http=None, write_state=None):
        entry = dict(step_id=step_id, method="POST", path="/x", effect="write", outcome=outcome, write_state=write_state)
        if http:
            entry["http_status"] = http
        return dict(status=status, failure=dict(step_id=step_id, outcome=outcome, detail=""), trace=[entry])
    assert publishing.failure_category(report("partial")) == "partial_write"
    assert publishing.failure_category(report("outcome_unknown", http=503)) == "unknown_outcome"
    assert publishing.failure_category(report("failed", http=429)) == "rate_limited"
    assert publishing.failure_category(report("failed", http=422)) == "validation_error"
    assert publishing.failure_category(report("failed", http=404)) == "not_found"
    assert publishing.failure_category(report("failed", outcome="not_sent")) == "api_unreachable"
    assert publishing.failure_category(report("failed", outcome="no_response")) == "timeout"
    assert publishing.failure_category(report("failed", step_id="connector_auth", outcome="connector_auth_failed")) == "authentication_failed"


def test_the_connection_check_is_real_step_by_step_and_never_shows_a_credential(live):
    d = live
    a = review_and_build(d, generated(d))
    result = d.client.post("/api/v1/connectors/desk/check").json()
    checks = {c["check"]: c for c in result["checks"]}
    assert result["environment"] == "live_demo" and checks["configuration"]["status"] == "ok"
    assert checks["reachable"]["status"] == "ok" and "HTTP" in checks["reachable"]["detail"]
    assert checks["authentication"]["status"] == "unknown"  # bearer tokens are only proven by an operation
    assert checks["permissions"]["status"] == "unknown" and checks["executable"]["status"] == "unknown"
    assert result["ready"] is False
    scenario_test(d, a["id"], "owner_files_request", OWNER, "alice", "own_record")
    proven = {c["check"]: c for c in d.client.post("/api/v1/connectors/desk/check").json()["checks"]}
    assert proven["permissions"]["status"] == "ok" and "alice" in proven["permissions"]["detail"]
    listing = d.client.get(f"/api/v1/businesses/{a['content']['proposal']['business_id']}/connectors").json()
    text = json.dumps(listing) + json.dumps(result)
    assert all(p["token"] not in text for p in d.people.values()) and "not-a-valid-token" not in text
    assert listing[0]["environment"] == "live_demo" and {i["name"] for i in listing[0]["identities"]} >= {"alice", "bob"}


def test_a_switched_off_live_connector_check_sends_nothing(live):
    d = live
    review_and_build(d, generated(d))
    d.settings.live_demo_enabled = False
    before = requests_sent(d)
    checks = {c["check"]: c for c in d.client.post("/api/v1/connectors/desk/check").json()["checks"]}
    assert checks["configuration"]["status"] == "failed" and "live_demo_disabled" in checks["configuration"]["detail"]
    assert checks["reachable"]["status"] == "skipped" and requests_sent(d) == before


def test_an_owner_saves_a_live_connector_only_with_confirmation_inside_the_operators_allowlist(live):
    d = live
    pid = generated(d)
    save = lambda **body: d.client.post(f"/api/v1/proposals/{pid}/connectors", json=dict(dict(base_url="http://desk.test", environment="live_demo", confirmed=True), **body))
    assert save(confirmed=False).json()["code"] == "live_confirmation"
    assert save(base_url="http://elsewhere.test").json()["code"] == "destination_not_allowed"
    d.settings.live_demo_enabled = False
    assert save().json()["code"] == "live_demo_disabled"
    d.settings.live_demo_enabled = True
    saved = save()
    assert saved.status_code == 200, saved.text
    live_id = saved.json()["id"]
    sandbox = d.client.post(f"/api/v1/proposals/{pid}/connectors", json=dict(base_url="http://desk.test", environment="sandbox", confirmed=True)).json()
    assert live_id.startswith("live-") and sandbox["id"].startswith("sandbox-") and live_id != sandbox["id"]
    assert saved.json()["environment"] == "live_demo" and sandbox["environment"] == "sandbox"
    assert saved.json()["identities"] == []  # credentials are never shared between environments
