"""Second domain: the local service-desk fixture and its renamed variant, plus the bounded repair loop.

MOCKED / AUTHORED: proposals come from an AUTHORED TEST SUBSTITUTE built from the discovered inventory,
and every review is a labeled TEST-ONLY decision. The live-model proof is scripts/domain_demo.py.
Expected outcomes come from the fixture's documented rules (fixtures/service_desk/app.py), and backend
state is read through the fixture's harness routes, independently of Team C.
"""
import json
import pytest
from helpers.desk import KEY, arguments, current, generated, make_desk, review_and_build, sandbox_test
from helpers.openapi import op
from team_c.repair import classify


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
