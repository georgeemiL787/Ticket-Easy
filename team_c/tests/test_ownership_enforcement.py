"""A structured ownership rule compiles into enforcement and is exercised by generated guard tests.

The guard cases are simulated evidence about the compiled policy. They are deliberately kept
separate from sandbox runs, which are the only evidence about the target API's behavior.
"""
from helpers.lifecycle import approve, current, post
from test_capability_policy_lifecycle import reviewed


def built(lifecycle, tmp_path):
    app, client, settings, pid, policy = reviewed(lifecycle, tmp_path)
    assert post(client, pid, "policy", policy=policy).status_code == 200
    assert post(client, pid, "reconcile").status_code == 200
    assert approve(client, pid).status_code == 200
    response = client.post(f"/api/v1/proposals/{pid}/artifacts", json=dict(connector_id="items-sandbox"))
    assert response.status_code == 200, response.text
    record_scope = next(r["id"] for r in current(client, pid)["requirements"] if r["kind"] == "record_scope")
    accepted = current(client, pid)["policy"]["content"]
    return client, pid, accepted, record_scope, response.json()


def test_10_the_accepted_ownership_rule_compiles_into_runtime_enforcement(lifecycle, tmp_path):
    client, pid, policy, record_scope, artifact = built(lifecycle, tmp_path)
    check = policy["checks"][0]
    requirement = next(r for r in artifact["content"]["access_requirements"] if r["id"] == record_scope)
    rule = requirement["enforcement"]

    assert rule["mechanism"] and rule["status"] == "configured"
    assert rule["enforced_by"] == "team_c_executor"
    assert rule["scope_kind"] == check["kind"]
    # Every value comes from the accepted policy: nothing about a particular target API is assumed.
    assert rule["pointer"] == check["resource_field"]
    assert rule["context_field"] == check["identity_source"]
    assert rule["comparison"] == check["rule"]
    assert rule["step_id"] == check["step_id"]
    assert rule["response_status"] == check["response_status"]


def test_11_the_generated_positive_ownership_guard_passes(lifecycle, tmp_path):
    client, pid, policy, record_scope, artifact = built(lifecycle, tmp_path)
    report = artifact["policy_tests"]
    ownership = [c for c in report["cases"] if c["kind"] == "positive" and "ownership" in c["name"]]
    assert ownership, [c["name"] for c in report["cases"]]
    assert all(c["passed"] and c["expected"] == "allow" for c in ownership)


def test_12_the_generated_cross_owner_guard_denies(lifecycle, tmp_path):
    client, pid, policy, record_scope, artifact = built(lifecycle, tmp_path)
    report = artifact["policy_tests"]
    cross = [c for c in report["cases"] if c["kind"] == "negative" and "ownership" in c["name"] and "cross" in c["name"]]
    assert cross, [c["name"] for c in report["cases"]]
    assert all(c["expected"] == "deny" and c["passed"] for c in cross)
    missing = [c for c in report["cases"] if "missing_scope" in c["name"]]
    assert missing and all(c["expected"] == "deny" and c["passed"] for c in missing)


def test_9_guard_evidence_disappears_when_its_policy_or_enforcement_changes(lifecycle, tmp_path):
    """Derived readiness must not survive the evidence it was derived from."""
    client, pid, policy, record_scope, artifact = built(lifecycle, tmp_path)
    view = current(client, pid)
    version = view["version"]
    proposals = client.app.state.service.proposals

    found, guards = proposals.build_evidence(pid, version, view["policy"], view["enforcement"])
    assert found is not None and guards is not None
    assert view["readiness"]["score"] == 100

    changed_policy = dict(view["policy"], sha256="0" * 64)
    assert proposals.build_evidence(pid, version, changed_policy, view["enforcement"]) == (None, None)
    assert proposals.build_evidence(pid, version, view["policy"], {"id": "some-other-enforcement"}) == (None, None)
    assert proposals.build_evidence(pid, version + 1, view["policy"], view["enforcement"]) == (None, None)
    assert proposals.build_evidence(pid, version, None, None) == (None, None)


def test_guard_evidence_is_simulation_and_does_not_grant_publication(lifecycle, tmp_path):
    """Compiler evidence and target-API evidence stay separate: a simulated pass publishes nothing."""
    client, pid, policy, record_scope, artifact = built(lifecycle, tmp_path)
    report = artifact["policy_tests"]
    assert report["scope"] == "runtime_guard_simulation"
    assert any("do not verify target API behavior" in text for text in report["limitations"])
    refused = client.post(f'/api/v1/artifacts/{artifact["id"]}/publications', json=dict(note="TEST-ONLY"))
    assert refused.status_code == 409
    body = refused.json()
    problems = list((body.get("details") or {}).get("problems", [])) + [body.get("message", "")]
    assert any("cross_user" in problem or "sandbox test" in problem for problem in problems)
