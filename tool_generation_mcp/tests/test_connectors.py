"""In-app setup through real forms; sandbox calls use a deterministic mock API."""
import json
import re
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from helpers.lifecycle import LifecycleSubstitute, current, post, approve
from test_artifacts import sandbox, approved, enforcement, review, run, ITEM_A, ITEM_B, ALICE
from team_c.config import AppError, Settings
from team_c.connectors import base_url, load_connectors
from team_c.executor import destination
from team_c.services.connectors import server_candidates
from team_c.web import create_app


def csrf(client, path):
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


@pytest.mark.parametrize("document,source,expected", [
    ({}, "http://localhost:8080/api/v1/openapi.json", ["http://localhost:8080"]),
    ({"servers": [{"url": "/v2"}]}, "https://example.test/spec/api.json", ["https://example.test/v2"]),
    ({"servers": [{"url": "../v3"}]}, "https://example.test/spec/api.json", ["https://example.test/v3"]),
    ({"servers": [{"url": "https://{region}.test/v1", "variables": {"region": {"default": "sandbox"}}}]}, "", ["https://sandbox.test/v1"]),
    ({}, "", []),
    ({"servers": [{"url": "/v1"}]}, "", []),
    ({"servers": [{"url": "http://user:secret@host.test"}]}, "", []),
    ({"servers": [{"url": "http://{unknown}.test"}]}, "", []),
    ({"servers": [{"url": "http://[::1]:8080/"}]}, "", ["http://[::1]:8080"]),
])
def test_server_candidates(document, source, expected):
    assert server_candidates(document, source) == expected


def test_operation_servers_override_document_and_preserve_multiple_choices():
    document = {"servers": [{"url": "https://root.test"}], "paths": {"/items": {
        "servers": [{"url": "https://path.test"}], "get": {"servers": [{"url": "https://read.test"}, {"url": "https://backup.test"}]}, "post": {}}}}
    ops = [dict(path="/items", method="GET"), dict(path="/items", method="POST")]
    assert server_candidates(document, operations=ops) == ["https://read.test", "https://backup.test", "https://path.test"]


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://x.test:bad", "https://x.test:99999", "http://a.test?token=secret", "http://a.test/#x", "http://a.test/\npath", "http://a.test\\path"])
def test_invalid_destinations_are_rejected(url):
    with pytest.raises(AppError):
        base_url(url)


def test_managed_setup_build_identity_and_execution_without_env_or_restart(sandbox):
    app, client, settings, spec, pid, target = sandbox
    p = approved(client, pid)
    settings.connectors_file = settings.sandbox_hosts = ""
    with app.state.store.connect(write=True) as conn:
        inventory = json.loads(conn.execute("SELECT inventory FROM specifications WHERE id=?", (spec["id"],)).fetchone()[0])
        inventory["source"] = dict(kind="url", url="http://target.test/openapi.json")
        document = json.loads(conn.execute("SELECT document FROM specifications WHERE id=?", (spec["id"],)).fetchone()[0])
        document["servers"] = [{"url": "/"}]
        conn.execute("UPDATE specifications SET document=?,inventory=? WHERE id=?", (json.dumps(document), json.dumps(inventory), spec["id"]))
    path = f"/proposals/{pid}"
    page = client.get(path).text
    assert 'value="http://target.test"' in page
    assert load_connectors(settings) == {}  # Page views never authorize destinations.
    form = dict(csrf=csrf(client, path), proposal_id=pid, base_url="http://target.test", context_fields="user_id", sandbox="1")
    response = client.post("/actions/save_connector", data=form, follow_redirects=False)
    assert response.status_code == 303, response.text
    cid, connector = next(iter(load_connectors(settings).items()))
    assert connector["identities"] == {} and connector["business_id"] == p["business_id"]
    assert target.requests == [] and settings.connectors_file == settings.sandbox_hosts == ""
    e = client.post(f"/api/v1/proposals/{pid}/enforcement", json=dict(connector_id=cid, enforcement=enforcement(p)))
    assert e.status_code == 200, e.text
    assert review(client, e.json()["id"]).status_code == 200
    result = client.post(f"/api/v1/proposals/{pid}/artifacts", json=dict(connector_id=cid))
    assert result.status_code == 200, result.text
    artifact = result.json()
    aid = artifact["id"]
    page = client.get(f"/artifacts/{aid}").text
    assert 'action="/actions/save_test_identity"' in page
    assert 'disabled>Run sandbox test' in page
    identity_form = dict(csrf=csrf(client, f"/artifacts/{aid}"), artifact_id=aid, identity_name="alice", scope="end_user",
                         username="alice@test", password="alice-secret-pw", **{"a:user_id": ALICE})
    response = client.post("/actions/save_test_identity", data=identity_form, follow_redirects=False)
    assert response.status_code == 303, response.text
    assert target.requests == []
    assert client.post("/actions/save_connector", data=form, follow_redirects=False).status_code == 303
    assert load_connectors(settings)[cid]["identities"]["alice"]["password"] == "alice-secret-pw"
    assert run(client, aid, item_id=ITEM_A, title="Updated through managed connector").status_code == 200
    assert target.items[ITEM_A]["title"] == "Updated through managed connector"
    writes = len(target.sent("PUT"))
    denied = run(client, aid, item_id=ITEM_B, title="Must not update").json()
    assert denied["report"]["status"] == "failed"
    assert len(target.sent("PUT")) == writes
    for page in (client.get(path).text, client.get(f"/artifacts/{aid}").text, client.get(f"/api/v1/artifacts/{aid}").text):
        assert "alice-secret-pw" not in page
    # Confirmation belongs to this connector, business and exact destination.
    altered = deepcopy(artifact["content"])
    altered["proposal"]["business_id"] = "unrelated-business"
    with pytest.raises(AppError, match="SANDBOX_HOSTS"):
        destination(connector, altered, settings)
    with pytest.raises(AppError, match="SANDBOX_HOSTS"):
        destination(dict(connector, base_url="http://other.test"), artifact["content"], settings)
    with TestClient(create_app(settings, LifecycleSubstitute)) as restarted:
        page = restarted.get(f"/artifacts/{aid}").text
        assert '<option>alice</option>' in page and "alice-secret-pw" not in page


def test_setup_requires_csrf_and_explicit_confirmation_and_leaves_external_config_alone(sandbox):
    app, client, settings, _, pid, target = sandbox
    original = load_connectors(settings)
    path = f"/proposals/{pid}"
    token = csrf(client, path)
    form = dict(proposal_id=pid, base_url="http://new.test", context_fields="")
    assert client.post("/actions/save_connector", data=form).status_code == 403
    form["csrf"] = token
    assert client.post("/actions/save_connector", data=form).status_code == 422
    form.update(sandbox="1", base_url="http://user:secret@new.test")
    assert client.post("/actions/save_connector", data=form).status_code == 422
    assert load_connectors(settings) == original and target.requests == []
    cid = app.state.service.connectors.save(pid, "http://new.test", [], confirmed=True)
    assert load_connectors(settings)["items-sandbox"] == original["items-sandbox"]
    assert cid in load_connectors(settings)
    with pytest.raises(AppError):
        destination(original["items-sandbox"], dict(connector=dict(id="items-sandbox", base_url="http://target.test"), proposal=dict(business_id=current(client,pid)["business_id"])), Settings(_env_file=None, database_path=settings.database_path, connectors_file=settings.connectors_file))


def test_accepted_policy_prefills_context_and_builds_with_generated_enforcement(lifecycle, tmp_path):
    from test_capability_policy_lifecycle import reviewed
    app, client, settings, pid, policy = reviewed(lifecycle, tmp_path)
    settings.connectors_file = settings.sandbox_hosts = ""
    assert post(client, pid, "policy", policy=policy).status_code == 200
    assert post(client, pid, "reconcile").json()["state"] == "ready_for_review"
    assert approve(client, pid).status_code == 200
    draft = app.state.service.connectors.draft(pid)
    assert draft["context_fields"] == ["user_id"]
    page = client.get(f"/proposals/{pid}").text
    assert 'value="user_id"' in page
    assert '/actions/submit_enforcement' not in page
    form = dict(csrf=csrf(client, f"/proposals/{pid}"), proposal_id=pid, base_url="http://target.test", context_fields="", sandbox="1")
    assert client.post("/actions/save_connector", data=form, follow_redirects=False).status_code == 303
    cid = next(iter(load_connectors(settings)))
    result = client.post(f"/api/v1/proposals/{pid}/artifacts", json=dict(connector_id=cid))
    assert result.status_code == 200, result.text
    artifact = result.json()
    assert artifact["policy_tests"]["passed"]
    assert artifact["content"]["access_policy"]["implementation_state"] == "GENERATED"
    assert load_connectors(settings)[cid]["context_fields"] == ["user_id"]
    assert load_connectors(settings)[cid]["identities"] == {}
    assert current(client, pid)["enforcement"] is None
