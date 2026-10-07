"""Sandbox publication records, evidence rules and the MCP result mapping (in process).

MOCKED / AUTHORED: proposals come from the AUTHORED TEST SUBSTITUTE, reviews are TEST-ONLY decisions and
executor traffic goes to the in-process service-desk fixture through a mock transport. The real SDK
client over stdio is exercised in test_mcp_stdio.py.
"""
import json
import re
import pytest
from mcp.shared.tool_name_validation import TOOL_NAME_REGEX
from team_c import publishing
from team_c.config import AppError
from team_c.mcp_server import result
from team_c.models import PublicationDisableSubmission
from helpers.desk import KEY, LABEL, arguments, generated, make_desk, review_and_build, sandbox_test
from helpers.publication import DENIED, OWNER, RevisingDesk, publish, revise, scenario_test, with_evidence


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setattr("helpers.desk.DeskSubstitute", RevisingDesk)
    d = make_desk(tmp_path, "base")
    d.service = d.app.state.service
    yield d
    d.client.__exit__(None, None, None)


def test_publication_requires_this_artifacts_own_evidence_and_records_references(desk):
    d = desk
    pid = generated(d)
    a = review_and_build(d, pid)
    refused = publish(d, a["id"])
    assert refused.status_code == 409 and refused.json()["code"] == "publication_not_allowed"
    assert any("expects success" in p for p in refused.json()["details"]["problems"])
    assert scenario_test(d, a["id"], "owner_files_request", OWNER, "alice", "own_record")["verdict"] == "passed"
    assert any("Record-scope requirement" in p for p in publish(d, a["id"]).json()["details"]["problems"])
    # A refused cross-user run alone is not enough: bob's credential and own-record use are not yet shown.
    assert scenario_test(d, a["id"], "cross_user_request", DENIED, "bob", "cross_user", "alice", **arguments(d, "alice"))["verdict"] == "passed"
    assert any("caller bob has no passing own_record test" in p for p in publish(d, a["id"]).json()["details"]["problems"])
    assert scenario_test(d, a["id"], "bob_files_own_request", OWNER, "bob", "own_record")["verdict"] == "passed"
    r = publish(d, a["id"])
    assert r.status_code == 200, r.text
    pub = r.json()
    tests = {t["id"] for t in d.service.artifact(a["id"])["tests"]}
    assert pub == dict(pub, artifact_id=a["id"], artifact_sha256=a["sha256"], version=1, decision_id=a["content"]["proposal"]["decision_id"],
                       enforcement_config_id=a["content"]["enforcement_config"]["id"], enforcement_sha256=a["content"]["enforcement_config"]["sha256"],
                       environment="sandbox", status="published", effective_status="published", publisher="local-owner",
                       production_activation=False, production_ready=False)
    assert set(pub["evidence"]["test_ids"]) == tests and pub["evidence"]["access_denial_tests"] == ["cross_user_request"]
    assert TOOL_NAME_REGEX.match(pub["tool_name"]) and pub["tool_name"] == publishing.tool_name(a["id"], a["content"])
    assert publish(d, a["id"]).json()["id"] == pub["id"]
    assert d.client.get(f"/api/v1/artifacts/{a['id']}").json()["content"]["activation"]["activated"] is False
    # A later version is a different artifact: the old artifact's passing tests do not carry over.
    revise(d, pid)
    b = review_and_build(d, pid)
    problems = publish(d, b["id"]).json()["details"]["problems"]
    assert b["id"] != a["id"] and any("expects success" in p for p in problems)
    assert d.client.get(f"/api/v1/publications/{pub['id']}").json()["problem"]["code"] == "approval_not_current"


def test_a_failing_rerun_withdraws_the_evidence(desk):
    d = desk
    a = review_and_build(d, generated(d))
    with_evidence(d, a["id"])
    pub = publish(d, a["id"]).json()
    assert sandbox_test(d, a["id"], "owner_files_request", dict(status="rejected"))["verdict"] == "failed"
    assert d.client.get(f"/api/v1/publications/{pub['id']}").json()["problem"]["code"] == "publication_evidence"
    assert d.service.published_tools(pub["business_id"], "alice") == []


def test_invocation_returns_permitted_output_and_keeps_the_audit_sanitized(desk):
    d = desk
    a = review_and_build(d, generated(d))
    with_evidence(d, a["id"])
    pub = publish(d, a["id"]).json()
    [tool] = d.service.published_tools(pub["business_id"], "alice")
    assert tool["input_schema"] == publishing.json_schema(a["content"]["input_schema"]) and tool["input_schema"]["additionalProperties"] is False
    assert tool["meta"]["team_c"] == dict(publication_id=pub["id"], artifact_id=a["id"], artifact_sha256=a["sha256"], proposal_id=a["proposal_id"],
                                          proposal_version=1, environment="sandbox", production_ready=False, activated=False)
    assert d.service.published_tools(pub["business_id"], "nobody") == [] and d.service.published_tools("other-business", "alice") == []
    before = len(d.state()["requests"])
    out = d.service.invoke_published(pub["business_id"], "alice", pub["tool_name"], arguments(d))
    ticket = d.state()["requests"][-1]["ticket"]
    assert out["status"] == "succeeded" and out["output"] == dict(ticket=ticket) and len(d.state()["requests"]) == before + 1
    assert out["artifact"] == dict(id=a["id"], sha256=a["sha256"], proposal_id=a["proposal_id"], proposal_version=1) and out["writes"] == [dict(step_id="s2", write_state="applied")]
    execution = next(e for e in d.service.artifact(a["id"])["executions"] if e["id"] == out["execution_id"])
    assert execution["mode"] == "mcp_sandbox" and execution["report"]["publication_id"] == pub["id"] and execution["report"]["channel"] == "mcp_stdio"
    audit = json.dumps(execution)
    assert ticket not in audit and all(s not in audit for s in (d.people["alice"]["token"], d.people["bob"]["token"], KEY, d.booking["alice"]["ref"]))
    injected = d.service.invoke_published(pub["business_id"], "alice", pub["tool_name"], arguments(d, token=d.people["bob"]["token"]))
    assert injected["status"] == "rejected" and injected["error"]["errors"] == ["token: unknown argument"] and injected["execution_id"]
    assert d.people["bob"]["token"] not in json.dumps(d.service.artifact(a["id"])["executions"])
    d.service.disable_publication(pub["id"], PublicationDisableSubmission(note=LABEL + "withdrawn"))
    with pytest.raises(AppError) as exc:
        d.service.invoke_published(pub["business_id"], "alice", pub["tool_name"], arguments(d))
    assert exc.value.code == "tool_not_published" and len(d.state()["requests"]) == before + 1


@pytest.mark.parametrize("status,code", [("partial", "partial_write"), ("outcome_unknown", "outcome_unknown"), ("failed", "rejected_by_api")])
def test_unfinished_executions_are_never_reported_as_success(status, code):
    pub = dict(id="p", tool_name="t", artifact_id="a", artifact_sha256="h", proposal_id="x", version=1)
    report = dict(status=status, message="m", failure=dict(step_id="s2", outcome="no_response" if status == "outcome_unknown" else "rejected_by_api", detail="d"),
                  trace=[dict(step_id="s2", effect="write", write_state="unknown" if status == "outcome_unknown" else "applied")])
    envelope = publishing.executed(pub, dict(execution_id="e", report=report, outputs={"ticket": "SR-1"}))
    mcp = result(envelope)
    assert envelope["status"] == status and envelope["output"] is None and envelope["error"]["code"] in (code, "no_response")
    assert mcp.is_error is True and mcp.structured_content == envelope and json.loads(mcp.content[0].text) == envelope


def failed_run(step, outcome, status, write="not_attempted"):
    trace = [dict(step_id="s1", effect="read", http_status=status if step == "s1" else 200, write_state=None),
             dict(step_id="s2", effect="write", http_status=status if step == "s2" else None, write_state=write)]
    return dict(status="failed", failure=dict(step_id=step, outcome=outcome), trace=trace)


@pytest.mark.parametrize("step,outcome,status,controls,expected", [
    ("s1", "blocked_by_access_check", 200, True, "record_scope_enforced"), ("s1", "rejected_by_api", 403, True, "target_record_denial"),
    ("s1", "rejected_by_api", 403, False, "general_access_denial"), ("s1", "rejected_by_api", 401, True, "authentication_failure"),
    ("connector_auth", "connector_auth_failed", None, True, "authentication_failure"), ("s1", "rejected_by_api", 400, True, "other_failure")])
def test_refusals_keep_authentication_access_and_record_scope_apart(step, outcome, status, controls, expected):
    assert publishing.refusal(failed_run(step, outcome, status), controls) == expected


def evidence_for(requirements, tests, runs):
    content = dict(access_requirements=[dict(id=f"{k}-1", kind=k, enforcement={}) for k in requirements])
    return publishing.evidence(content, [dict(dict(id=t[0], execution_id=t[0], name=t[0], verdict="passed", expectation=dict(status=t[1])), **t[2]) for t in tests],
                               [dict(id=i, identity=who, report=r) for i, (who, r) in runs.items()])


def test_requirements_decide_which_scenarios_are_needed():
    ok = dict(status="succeeded", trace=[])
    # No record-scope requirement (public or caller-only tool): a success test suffices; no cross-user test is invented.
    for reqs in ([], ["caller_access"], ["public_access"]):
        problems, ev = evidence_for(reqs, [("t1", "succeeded", {})], dict(t1=("alice", ok)))
        assert problems == [] and ev["required"] == ["success"]
    own = lambda who, sel: dict(scenario="own_record", selector_sha256=sel)
    cross = dict(scenario="cross_user", record_owner="alice", selector_sha256="A")
    base = [("a_own", "succeeded", own("alice", "A")), ("b_own", "succeeded", own("bob", "B")), ("x", "failed", cross)]
    runs = dict(a_own=("alice", ok), b_own=("bob", ok))
    assert evidence_for(["record_scope"], base, dict(runs, x=("bob", failed_run("s1", "blocked_by_access_check", 200))))[0] == []
    assert evidence_for(["record_scope"], base, dict(runs, x=("bob", failed_run("s1", "rejected_by_api", 403))))[1]["record_scope"][0]["refused_by"] == "target_record_denial"
    for run, reason in ((failed_run("s1", "rejected_by_api", 401), "authentication_failure"), (failed_run("s2", "rejected_by_api", 403, "rejected_unverified"), "a write was attempted")):
        problems, _ = evidence_for(["record_scope"], base, dict(runs, x=("bob", run)))
        assert any(reason in p for p in problems)
    # The targeted record must be one its owner was shown to use.
    elsewhere = [base[0], base[1], ("x", "failed", dict(cross, selector_sha256="OTHER"))]
    problems, _ = evidence_for(["record_scope"], elsewhere, dict(runs, x=("bob", failed_run("s1", "blocked_by_access_check", 200))))
    assert any("no passing own_record test by alice on the same record" in p for p in problems)


def test_scenarios_are_validated_before_anything_runs(desk):
    d = desk
    a = review_and_build(d, generated(d))
    for body in (dict(scenario="own_record", expect=DENIED), dict(scenario="cross_user", expect=DENIED), dict(scenario="cross_user", record_owner="alice", expect=DENIED),
                 dict(scenario="cross_user", record_owner="mallory", expect=DENIED), dict(scenario="general", record_owner="bob", expect=OWNER)):
        r = d.client.post(f"/api/v1/artifacts/{a['id']}/sandbox-tests", json=dict(name="t", identity="alice", arguments=arguments(d), **body))
        assert r.status_code == 422 and r.json()["code"] == "invalid_scenario", body
    assert d.service.artifact(a["id"])["executions"] == [] and d.state()["requests"] == []


def test_nullable_and_names():
    converted = publishing.json_schema({"type": "object", "properties": {"t": {"type": "string", "nullable": True, "maxLength": 3}, "e": {"enum": ["a"], "type": "string", "nullable": True}}})
    # nullable permits the type null, but an explicit enum still constrains its allowed values.
    assert converted["properties"] == {"t": {"type": ["string", "null"], "maxLength": 3}, "e": {"enum": ["a"], "type": ["string", "null"]}}
    content = dict(name="Élan: Booking / Update!!", proposal=dict(version=3))
    a, b = publishing.tool_name("0e68d01a-1c26-4d82-b09c-aad99d67be21", content), publishing.tool_name("0e68d01a-1c26-4d82-b09c-aad99d67be22", content)
    assert a == "lan_booking_update_v3_0e68d01a1c264d82b09caad99d67be21" and TOOL_NAME_REGEX.match(a) and a != b
    assert publishing.tool_name("0e68d01a-1c26-4d82-b09c-aad99d67be21", dict(content, name="x" * 300)).startswith("x" * 48 + "_v3_")


def test_ui_controls_publish_and_disable(desk):
    d = desk
    a = review_and_build(d, generated(d))
    page = d.client.get(f"/artifacts/{a['id']}").text
    assert "Cannot publish yet" in page and "Publish to sandbox MCP</button>" in page
    csrf = re.search(r'name="csrf" value="([^"]+)"', page).group(1)
    with_evidence(d, a["id"])
    r = d.client.post("/actions/publish", data=dict(csrf=csrf, artifact_id=a["id"], note=LABEL + "via form"), follow_redirects=False)
    assert r.status_code == 303
    [pub] = d.client.get("/api/v1/publications", params=dict(business_id=a["content"]["proposal"]["business_id"])).json()
    page = d.client.get(f"/artifacts/{a['id']}").text
    assert pub["tool_name"] in page and "Disable publication" in page and "cannot undo writes" in page
    d.client.post("/actions/disable_publication", data=dict(csrf=csrf, publication_id=pub["id"], note=LABEL + "stop"), follow_redirects=False)
    assert d.client.get(f"/api/v1/publications/{pub['id']}").json()["status"] == "disabled"
