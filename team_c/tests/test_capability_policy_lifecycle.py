"""Structured owner decisions through API, review, build and publication gates."""
import json
import httpx
import pytest
from helpers.lifecycle import generate, current, post, answer_all, supersede_q2, approve
from test_artifacts import FakeTarget, write_connectors, ITEM_A


def reviewed(lifecycle, tmp_path):
    app, client, settings, spec, scope = lifecycle
    pid = generate(client, spec, scope)
    supersede_q2(client, pid)
    answer_all(client, pid)
    post(client, pid, "reconcile")
    post(client, pid, "reconcile")
    p = current(client, pid)
    settings.connectors_file = str(tmp_path / "connectors.json")
    settings.sandbox_hosts = "target.test:80"
    write_connectors(settings, p["business_id"])
    policy = dict(principals=["resource_owner"], authentication="required", checks=[dict(requirement_id=r["id"], kind="ownership", resource="Reviewed resource", step_id="s1", response_status="200", resource_field="/owner_id", identity_source="user_id") for r in p["requirements"] if r["kind"] == "record_scope"], financial=False, irreversible=False, external_side_effects=False, idempotent=True)
    return app, client, settings, pid, policy


def test_policy_is_version_bound_and_generated_tests_do_not_grant_publication(lifecycle, tmp_path):
    app, client, settings, pid, policy = reviewed(lifecycle, tmp_path)
    p = current(client, pid)
    saved = post(client, pid, "policy", policy=policy)
    assert saved.status_code == 200, saved.text
    assert saved.json()["policy"]["sha256"]
    assert all(r["status"] == "owner_confirmed" for r in saved.json()["requirements"])
    assert saved.json()["reconciliation_id"] is None
    assert approve(client, pid).status_code == 422
    old = client.post(f'/api/v1/proposals/{pid}/versions/{p["version"]}/policy', json=dict(expected_revision=p["review_revision"], policy=policy))
    assert old.status_code == 409
    checked = post(client, pid, "reconcile")
    assert checked.status_code == 200 and checked.json()["state"] == "ready_for_review", checked.text
    assert approve(client, pid).status_code == 200
    build_page = client.get(f"/proposals/{pid}").text
    assert "Enforcement will be generated from the accepted structured policy" in build_page
    assert '/actions/submit_enforcement' not in build_page
    manual = client.post(f"/api/v1/proposals/{pid}/enforcement", json=dict(connector_id="items-sandbox", enforcement={}))
    assert manual.status_code == 409 and manual.json()["code"] == "policy_managed_enforcement"
    built = client.post(f"/api/v1/proposals/{pid}/artifacts", json=dict(connector_id="items-sandbox"))
    assert built.status_code == 200, built.text
    a = built.json()
    assert a["content"]["access_policy"]["sha256"] == saved.json()["policy"]["sha256"]
    assert a["policy_tests"]["passed"] and a["policy_tests"]["artifact_sha256"] == a["sha256"]
    assert client.post(f'/api/v1/artifacts/{a["id"]}/publications', json=dict(note="Test only")).status_code == 409
    assert post(client, pid, "policy", policy=policy).status_code == 409
    app.state.service.execution_transport = httpx.MockTransport(FakeTarget())
    result = client.post(f'/api/v1/artifacts/{a["id"]}/sandbox-runs', json=dict(identity="alice", arguments=dict(item_id=ITEM_A, title="change")))
    assert result.status_code == 200 and result.json()["report"]["status"] == "succeeded", result.text
    assert client.get(f'/artifacts/{a["id"]}').status_code == 200
    page = client.get(f"/proposals/{pid}")
    assert page.status_code == 200 and "Structured access and input policy" in page.text
    # A deliberately corrupted policy record cannot inherit the old approval or artifact.
    with app.state.store.connect(write=True) as c:
        c.execute("UPDATE capability_policies SET sha256='changed' WHERE proposal_id=?", (pid,))
    with pytest.raises(Exception) as error:
        app.state.service.approval(pid, p["version"])
    assert error.value.code == "policy_integrity"


def test_open_review_page_has_structured_choices_and_rejects_unknown_scope(lifecycle, tmp_path):
    _, client, _, pid, policy = reviewed(lifecycle, tmp_path)
    page = client.get(f"/proposals/{pid}")
    assert page.status_code == 200
    assert 'name="principals"' in page.text and 'name="authentication"' in page.text
    policy["checks"][0]["requirement_id"] = "unrelated"
    response = post(client, pid, "policy", policy=policy)
    assert response.status_code == 422 and response.json()["code"] == "policy_scope"


def test_policy_review_reopens_without_model_calls_and_preserves_answers(lifecycle, tmp_path):
    app, client, _, pid, policy = reviewed(lifecycle, tmp_path)
    assert post(client, pid, "policy", policy=policy).status_code == 200
    assert post(client, pid, "reconcile").status_code == 200
    assert approve(client, pid).status_code == 200
    before = current(client, pid)
    attempts = len(app.state.store.all("SELECT id FROM attempts"))
    result = post(client, pid, "policy-review")
    assert result.status_code == 200, result.text
    after = result.json()
    assert after["version"] == before["version"] + 1
    assert after["content"] == before["content"]
    assert {k: v["text"] for k, v in after["answers"].items()} == {k: v["text"] for k, v in before["answers"].items()}
    assert len(app.state.store.all("SELECT id FROM attempts")) == attempts
    assert after["policy"] is None and after["policy_review_pending"]
    assert after["policy_form"] == before["policy"]["content"]
    assert not any(d["version"] == after["version"] for d in after["decisions"])
    assert after["enforcement"] is None
    old = client.get(f'/api/v1/proposals/{pid}?version={before["version"]}').json()
    assert old["state"] == "superseded" and old["policy"] == before["policy"]
    assert client.post(f'/api/v1/proposals/{pid}/versions/{before["version"]}/policy-review', json=dict(expected_revision=before["review_revision"])).status_code == 409
    assert post(client, pid, "policy-review").status_code == 409
    assert approve(client, pid, key="new-policy-approval").json()["code"] == "policy_review_required"
    assert post(client, pid, "policy", policy=policy).status_code == 200
    assert post(client, pid, "reconcile").status_code == 200
    assert approve(client, pid, key="new-policy-approval").status_code == 200
    built = client.post(f"/api/v1/proposals/{pid}/artifacts", json=dict(connector_id="items-sandbox"))
    assert built.status_code == 200 and built.json()["policy_tests"]["passed"]
