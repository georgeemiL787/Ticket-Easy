"""Automatic tests: plan derivation (pure), provisioning through a fixture provider, state trying, owner questions, safety.

MOCKED / AUTHORED: the proposal comes from the AUTHORED TEST SUBSTITUTE, owner decisions are sandbox-only and the target is the
in-process service-desk fixture with its (sandbox-only) /_fixtures provider. Nothing here is specific to one business API.
"""
import json
import re
import pytest
from team_c import autotest as plans
from team_c.config import AppError
from helpers.desk import KEY, generated, make_desk, review_and_build
from helpers.publication import RevisingDesk

FIXTURE_ENV = "DESK_FIXTURE_KEY"
KINDS = [dict(kind="booking", states=["cancelled", "confirmed"], id_field="reference", selects_by=["booking_reference"])]


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setattr("helpers.desk.DeskSubstitute", RevisingDesk)
    monkeypatch.setenv(FIXTURE_ENV, KEY)
    d = make_desk(tmp_path, "base")
    d.service = d.app.state.service
    yield d
    d.client.__exit__(None, None, None)


def managed(d, pid, fixtures=True):
    """A managed sandbox connector (like one saved in the UI) that declares the fixture provider, and the artifact built on it."""
    cid = d.service.connectors.save(pid, "http://desk.test", [d.ctx], confirmed=True,
                                    fixtures=dict(base_url="http://desk.test", key_ref=FIXTURE_ENV) if fixtures else None)
    return cid, review_and_build(d, pid, cid)


def test_plan_follows_the_access_requirements_and_the_schema(desk):
    d = desk
    a = review_and_build(d, generated(d))
    p = plans.plan(a["content"], ["booking_reference"], KINDS)
    assert [(t["name"], t["scenario"], t["expect"]) for t in p["tests"] if t["purpose"] == "evidence"] == [
        ("auto.own_record.a", "own_record", "succeeded"), ("auto.own_record.b", "own_record", "succeeded"),
        ("auto.cross_user.b-on-a", "cross_user", "failed")]
    assert p["accounts"] == ["a", "b"] and p["record_kind"] == "booking" and p["record_inputs"] == ["booking_reference"]
    assert p["states_to_try"] == ["cancelled", "confirmed"] and p["writes"] and not p["unresolved"]
    assert p["tests"][-1]["scenario"] == "invalid_arguments" and p["tests"][-1]["purpose"] == "diagnostic"
    assert p["tests"][0]["argument_sources"]["category"] == "enum" and p["tests"][0]["arguments"]["category"] == "cleaning"
    # no record scope: one success test; a provider that cannot select the input is a blocker, not a guess
    plain = json.loads(json.dumps(a["content"]))
    plain["access_requirements"] = []
    assert [t["name"] for t in plans.plan(plain, [], [])["tests"] if t["purpose"] == "evidence"] == ["auto.success.a"]
    assert "selected by booking_reference" in plans.plan(a["content"], ["booking_reference"], [dict(kind="x", states=["s"], selects_by=["other"])])["blockers"][0]
    # data scope: the isolation pair
    scoped = json.loads(json.dumps(plain))
    scoped["access_requirements"] = [dict(id="r1", kind="data_scope", enforcement=dict(api_filtered=True))]
    assert [t["name"] for t in plans.plan(scoped, [], KINDS)["tests"] if t["purpose"] == "evidence"] == ["auto.success.a", "auto.success.b"]


def test_schema_values_use_enum_default_example_format_then_constraints():
    v = plans.schema_value
    assert v("x", dict(enum=["q", "r"])) == ("q", "enum") and v("x", dict(default=3, type="integer")) == (3, "default")
    assert v("x", dict(type="string", format="date")) == ("2026-01-15", "format")
    assert v("x", dict(type="integer", minimum=5)) == (5, "placeholder") and v("x", dict(type="string", maxLength=4)) == ("Auto", "placeholder")
    assert v("x", dict(type="string", pattern="^[A-Z]{3}$"))[1] == "unresolved"


def test_a_record_scope_tool_reaches_ready_to_publish_with_no_accounts_configured_beforehand(desk):
    d = desk
    cid, a = managed(d, generated(d))
    bid = a["content"]["proposal"]["business_id"]
    assert next(c for c in d.service.connectors_of(bid) if c["id"] == cid)["identities"] == []
    assert d.service.artifact(a["id"])["readiness_state"] == "not_tested"
    result = d.service.auto_test(a["id"])
    assert result["outcome"] == "ready_to_publish", (result["needs_owner"], result["problems"], result["attempts"])
    assert not result["needs_owner"]
    # the write tool was tried on the wrong state first (409), then on a fresh record in the state that works
    tries = [x for x in result["attempts"] if x.get("test") == "auto.own_record.a"]
    assert [(x["state"], x["verdict"]) for x in tries] == [("cancelled", "failed"), ("confirmed", "passed")]
    assert {x["name"]: x["verdict"] for x in result["diagnostics"]} == {"auto.invalid_arguments": "passed", "auto.bad_credential": "passed"}
    assert d.service.artifact(a["id"])["readiness_state"] == "ready_to_publish"
    assert not d.service.publications(artifact_id=a["id"])  # ready is not published
    names = {t["name"]: t["verdict"] for t in d.service.artifact(a["id"])["tests"] if t["current"]}
    assert names == {"auto.own_record.a": "passed", "auto.own_record.b": "passed", "auto.cross_user.b-on-a": "passed"}
    # the API can wait for the result; a re-run supersedes by test name instead of duplicating evidence
    again = d.client.post(f"/api/v1/artifacts/{a['id']}/auto-test", json=dict(wait=True))
    assert again.status_code == 200 and again.json()["outcome"] == "ready_to_publish"
    assert d.client.get(f"/api/v1/auto-test-runs/{again.json()['run_id']}").json()["status"] == "succeeded"
    assert len([t for t in d.service.artifact(a["id"])["tests"] if t["current"]]) == 3
    page = d.client.get(f"/artifacts/{a['id']}").text
    assert "Automatic tests" in page and "Ready to publish" in page


def test_publish_when_ready_publishes_to_the_sandbox_only(desk):
    d = desk
    _, a = managed(d, generated(d))
    result = d.service.auto_test(a["id"], publish_when_ready=True)
    assert result["outcome"] == "published"
    pub = d.service.publication(result["published"])
    assert pub["environment"] == "sandbox" and pub["production_ready"] is False
    assert d.service.artifact(a["id"])["readiness_state"] == "published"


def test_a_wiring_defect_stops_at_needs_owner_and_the_model_repair_is_not_called(tmp_path, monkeypatch):
    monkeypatch.setattr("helpers.desk.DeskSubstitute", RevisingDesk)
    monkeypatch.setenv(FIXTURE_ENV, KEY)
    d = make_desk(tmp_path, "base", defect="wrong_pointer")
    d.service = d.app.state.service
    try:
        _, a = managed(d, generated(d))
        result = d.service.auto_test(a["id"])
        assert result["outcome"] == "needs_owner" and result["needs_owner"]
        # a rejected write must be verified by a person before any repair, and the repair itself is never started automatically
        ask = result["needs_owner"][0]
        assert ask["diagnosis"] in ("owner", "repair") and ask["failure"]["classification"] == "mapping_suspect" and "repair" in ask["question"]
        assert d.provider.instructions == []  # the repair model was never invoked
        assert d.service.artifact(a["id"])["readiness_state"] == "needs_owner"
    finally:
        d.client.__exit__(None, None, None)


def test_without_fixtures_the_owner_is_told_exactly_what_to_add(desk):
    d = desk
    _, a = managed(d, generated(d), fixtures=False)
    result = d.service.auto_test(a["id"])
    assert result["outcome"] == "needs_owner" and result["source"] == "existing_accounts"
    assert "Add 2 more test accounts of type end_user" in result["needs_owner"][0]["question"]


def test_existing_accounts_without_a_way_to_find_their_records_ask_the_owner(desk):
    d = desk
    _, a = managed(d, generated(d), fixtures=False)
    for who in ("alice", "bob"):
        person = d.people[who]
        d.service.connectors.save_identity(a["content"], who, "end_user", dict(token=person["token"]), {d.ctx: person["number"] if d.n["holder_int"] else person["id"]})
    result = d.service.auto_test(a["id"])
    assert result["outcome"] == "needs_owner" and "has no record this tool can use" in result["needs_owner"][0]["question"]


def test_only_a_sandbox_connector_may_declare_fixtures_and_the_key_is_never_stored(desk):
    d = desk
    pid = generated(d)
    with pytest.raises(AppError) as exc:
        d.service.connectors.save(pid, "http://desk.test", [d.ctx], confirmed=True, environment="live_demo",
                                  fixtures=dict(base_url="http://desk.test", key_ref=FIXTURE_ENV))
    assert exc.value.code == "fixtures_sandbox_only"
    with pytest.raises(AppError) as exc:
        d.service.connectors.save(pid, "http://desk.test", [d.ctx], confirmed=True, fixtures=dict(base_url="http://elsewhere.test", key_ref=FIXTURE_ENV))
    assert exc.value.code == "fixtures_destination"
    cid, a = managed(d, pid)
    d.service.auto_test(a["id"])
    bid = a["content"]["proposal"]["business_id"]
    everything = (json.dumps(d.service.connectors_of(bid)) + json.dumps(d.service.auto_test_runs(a["id"])) + d.client.get(f"/artifacts/{a['id']}").text
                  + json.dumps(d.service.artifact(a["id"]), default=str))
    assert KEY not in everything and FIXTURE_ENV in everything
    check = d.service.check_connector(cid)
    assert next(c for c in check["checks"] if c["check"] == "fixtures")["status"] == "ok"
    assert next(c for c in d.service.connectors.listing(bid) if c["id"] == cid)["fixtures"] == "configured"


def test_a_missing_fixture_key_sends_nothing(desk, monkeypatch):
    d = desk
    _, a = managed(d, generated(d))
    monkeypatch.delenv(FIXTURE_ENV)
    with pytest.raises(AppError) as exc:
        d.service.auto_test(a["id"])
    assert exc.value.code == "fixtures_key_missing"


def test_the_business_artifact_list_carries_the_readiness_state_for_the_dashboard(desk):
    d = desk
    _, a = managed(d, generated(d))
    bid = a["content"]["proposal"]["business_id"]
    [row] = d.client.get(f"/api/v1/businesses/{bid}/artifacts").json()
    assert row["id"] == a["id"] and row["readiness_state"] == "not_tested" and row["auto_test"] is None
    d.service.auto_test(a["id"])
    [row] = d.client.get(f"/api/v1/businesses/{bid}/artifacts").json()
    assert row["readiness_state"] == "ready_to_publish" and row["auto_test"]["outcome"] == "ready_to_publish"


def test_a_run_in_progress_is_shown_as_testing_and_is_not_started_twice(desk):
    d = desk
    _, a = managed(d, generated(d))
    rid = d.service.store.start_run(a["content"]["proposal"]["business_id"], "auto_test", a["id"])  # as start() records it
    assert d.service.artifact(a["id"])["readiness_state"] == "testing"
    assert d.service.start_auto_test(a["id"]) == dict(run_id=rid, status="running", already_running=True)
    with pytest.raises(AppError) as exc:
        d.service.auto_test(a["id"])
    assert exc.value.code == "auto_test_running"


def test_a_decorated_input_name_still_selects_the_record_kind():
    content = dict(name="t", input_schema=dict(type="object", required=["order_id_input", "reason"], properties=dict(order_id_input={"type": "string"}, reason={"type": "string"})),
                   steps=[dict(effect="write")], access_requirements=[], access_policy=None)
    p = plans.plan(content, [], [dict(kind="order", states=["delivered"], selects_by=["order_id"])])
    assert p["record_inputs"] == ["order_id_input"] and p["record_kind"] == "order" and not p["blockers"]
    assert p["tests"][0]["arguments"] == {"reason": "Automatic test"}


def test_publish_when_ready_is_a_per_business_setting_that_is_off_by_default(desk):
    d = desk
    _, a = managed(d, generated(d))
    bid = a["content"]["proposal"]["business_id"]
    assert d.client.get(f"/api/v1/businesses/{bid}/settings").json()["auto_publish_sandbox"] is False
    assert d.service.auto_test(a["id"])["outcome"] == "ready_to_publish"  # nothing set: not published
    # an explicit choice in the request wins over the setting
    d.client.put(f"/api/v1/businesses/{bid}/settings", json=dict(auto_publish_sandbox=True))
    assert d.client.get(f"/api/v1/businesses/{bid}/settings").json()["auto_publish_sandbox"] is True
    assert d.service.auto_test(a["id"], publish_when_ready=False)["outcome"] == "ready_to_publish"
    result = d.client.post(f"/api/v1/artifacts/{a['id']}/auto-test", json=dict(wait=True)).json()
    assert result["outcome"] == "published" and d.service.publication(result["published"])["environment"] == "sandbox"
    assert d.client.put(f"/api/v1/businesses/nobody/settings", json=dict(auto_publish_sandbox=True)).status_code == 404


def test_repeated_runs_do_not_pile_up_test_identities_but_keep_the_ones_evidence_uses(desk):
    d = desk
    cid, a = managed(d, generated(d))
    bid = a["content"]["proposal"]["business_id"]
    names = lambda: sorted(next(c for c in d.service.connectors_of(bid) if c["id"] == cid)["identities"], key=lambda i: i["name"])
    first = d.service.auto_test(a["id"])
    kept = {i["name"] for i in names()}
    assert kept == {first["identities"]["a"], first["identities"]["b"]}  # the unused revoked account is gone
    assert first["pruned_identities"] == [first["identities"]["revoked"]]
    second = d.service.auto_test(a["id"])
    now = {i["name"] for i in names()}
    assert now == {second["identities"]["a"], second["identities"]["b"]} and not (kept & now)
    # the evidence of the latest run still stands and the tool is still ready
    assert d.service.artifact(a["id"])["readiness_state"] == "ready_to_publish"
    # an identity the owner added by hand is never pruned
    d.service.connectors.save_identity(a["content"], "alice", "end_user", dict(token=d.people["alice"]["token"]), {d.ctx: d.people["alice"]["number"] if d.n["holder_int"] else d.people["alice"]["id"]})
    d.service.auto_test(a["id"])
    assert "alice" in {i["name"] for i in names()}


def test_the_artifact_page_saves_the_business_setting_and_the_fixture_endpoints(desk):
    d = desk
    cid, a = managed(d, generated(d), fixtures=False)
    bid = a["content"]["proposal"]["business_id"]
    page = d.client.get(f"/artifacts/{a['id']}").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    assert "Save fixtures" in page and "Remove fixtures" not in page
    r = d.client.post("/actions/save_fixtures", data=dict(csrf=csrf, artifact_id=a["id"], base_url="http://desk.test", key_ref=FIXTURE_ENV), follow_redirects=False)
    assert r.status_code == 303
    assert next(c for c in d.service.connectors_of(bid) if c["id"] == cid)["fixtures"] == "configured"
    assert "Remove fixtures" in d.client.get(f"/artifacts/{a['id']}").text
    # a key value instead of a variable name is refused, not stored
    bad = d.client.post("/actions/save_fixtures", data=dict(csrf=csrf, artifact_id=a["id"], base_url="http://desk.test", key_ref="not a name!"), follow_redirects=False)
    assert bad.status_code == 422
    d.client.post("/actions/save_fixtures", data=dict(csrf=csrf, artifact_id=a["id"], remove="1"), follow_redirects=False)
    assert next(c for c in d.service.connectors_of(bid) if c["id"] == cid)["fixtures"] == "none"
    d.client.post("/actions/save_business_settings", data=dict(csrf=csrf, artifact_id=a["id"], auto_publish_sandbox="1"), follow_redirects=False)
    assert d.service.business_settings(bid)["auto_publish_sandbox"] is True
    assert "checked" in d.client.get(f"/artifacts/{a['id']}").text
