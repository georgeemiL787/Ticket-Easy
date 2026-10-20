"""Tool batches: choose operations, review the prepared summaries, approve with explicit confirmations.

Model answers come from the deterministic TEST SUBSTITUTE (tests/helpers/lifecycle.py), not a live model."""
import time

import httpx
import pytest
from helpers.openapi import op

from test_artifacts import ALICE, BOB, FakeTarget, identity  # the MOCK target used by the artifact tests

FLAGS = dict(financial=False, irreversible=False, external_side_effects=True, idempotent=True)


def wait(client, batch_id, until, timeout=60):
    """Poll the batch until `until(view)` holds (the work runs on a background thread)."""
    end = time.time() + timeout
    while time.time() < end:
        view = client.get(f"/api/v1/tool-batches/{batch_id}").json()
        if until(view):
            return view
        time.sleep(0.2)
    raise AssertionError(f"timed out: {view}")


@pytest.fixture
def started(lifecycle):
    app, client, settings, spec, scope = lifecycle
    inventory = spec["inventory"]
    get_id, put_id = (op(inventory, m, "/api/v1/items/{id}")["id"] for m in ("GET", "PUT"))
    return app, client, settings, spec, get_id, put_id


def create(client, spec, put_id, get_id, **extra):
    body = dict(spec_id=spec["id"], operation_ids=[put_id], verify_operation_id=get_id, access=dict(callers="customer", own_records_only=True), **extra)
    business_id = client.get(f'/api/v1/specifications/{spec["id"]}/inventory').json()["business_id"]
    r = client.post(f"/api/v1/businesses/{business_id}/tool-batches", json=body)
    assert r.status_code == 200, r.text
    return business_id, r.json()


def test_operations_are_listed_with_their_class_and_paginated(started):
    _, client, _, spec, get_id, put_id = started
    page = client.get(f'/api/v1/specifications/{spec["id"]}/operations', params=dict(limit=2)).json()
    assert page["total"] >= 2 and len(page["operations"]) == 2 and page["limit"] == 2
    rows = {r["id"]: r for r in client.get(f'/api/v1/specifications/{spec["id"]}/operations', params=dict(limit=500)).json()["operations"]}
    assert rows[get_id]["class"] == "read" and rows[get_id]["selectable"]
    assert rows[put_id]["class"] == "write" and rows[put_id]["suggested_effects"]["external_side_effects"] is True
    only_reads = client.get(f'/api/v1/specifications/{spec["id"]}/operations', params={"class": "read", "limit": 500}).json()
    assert only_reads["matching"] == only_reads["counts"]["read"] and all(r["class"] == "read" for r in only_reads["operations"])
    searched = client.get(f'/api/v1/specifications/{spec["id"]}/operations', params=dict(q="items", limit=500)).json()
    assert 0 < searched["matching"] <= searched["total"]


def test_a_batch_prepares_a_summary_and_waits_for_the_owner(started):
    _, client, _, spec, get_id, put_id = started
    _, batch = create(client, spec, put_id, get_id)
    view = wait(client, batch["id"], lambda v: not v["busy"])
    item = view["items"][0]
    assert item["state"] == "needs_owner" and item["class"] == "write" and item["proposal_id"]
    assert item["summary"]["name"] and item["summary"]["purpose"] and "asks_for" in item["summary"]
    assert item["suggested_effects"]["external_side_effects"] is True
    assert client.get(f'/api/v1/proposals/{item["proposal_id"]}').json()["state"] != "approved_to_build"  # nothing was approved for the owner


def test_approving_a_tool_that_changes_data_needs_all_four_effect_flags(started):
    _, client, _, spec, get_id, put_id = started
    _, batch = create(client, spec, put_id, get_id)
    item = wait(client, batch["id"], lambda v: not v["busy"])["items"][0]
    pid = item["proposal_id"]
    for flags in ({}, {"financial": False}, dict(FLAGS, idempotent="yes")):
        r = client.post(f'/api/v1/tool-batches/{batch["id"]}/approve', json=dict(proposal_ids=[pid], confirmations={pid: flags}))
        assert r.status_code in (400, 422), r.text
    r = client.post(f'/api/v1/tool-batches/{batch["id"]}/approve', json=dict(proposal_ids=[pid], confirmations={}))
    assert r.status_code == 422 and r.json()["code"] == "effects_unconfirmed" and set(r.json()["details"]["missing"]) == set(FLAGS)
    assert client.get(f"/api/v1/tool-batches/{batch['id']}").json()["items"][0]["state"] == "needs_owner"


def test_an_unusable_operation_is_listed_and_skipped_with_its_reason(started):
    _, client, _, spec, get_id, put_id = started
    inventory = client.get(f'/api/v1/specifications/{spec["id"]}/operations', params=dict(limit=500)).json()
    unusable = [r for r in inventory["operations"] if not r["selectable"]]
    if not unusable:
        pytest.skip("the test API has no unusable operation")
    business_id, batch = create(client, spec, unusable[0]["id"], get_id)
    assert batch["items"][0]["state"] == "skipped" and batch["items"][0]["message"] and not batch["busy"]


def test_a_batch_rejects_unknown_operations_and_empty_choices(started):
    _, client, _, spec, get_id, put_id = started
    business_id = client.get(f'/api/v1/specifications/{spec["id"]}/inventory').json()["business_id"]
    assert client.post(f"/api/v1/businesses/{business_id}/tool-batches", json=dict(spec_id=spec["id"], operation_ids=["nope"])).status_code == 422
    assert client.post(f"/api/v1/businesses/{business_id}/tool-batches", json=dict(spec_id=spec["id"], operation_ids=[])).status_code == 422


def test_the_approval_reviews_builds_and_tests_in_the_owners_name(started, tmp_path, monkeypatch):
    # the deterministic substitute only accepts answers labelled sandbox-only; a live model judges the real wording
    monkeypatch.setattr("tools_service.services.batches.owner_statement", lambda access, actor: f"sandbox-only (recorded for {actor}): only the authenticated owner of an item may read or update it.")
    app, client, settings, spec, get_id, put_id = started
    settings.sandbox_hosts = "target.test:80"
    target = FakeTarget()
    app.state.service.execution_transport = httpx.MockTransport(target)
    accounts = [dict(name="alice", scope="end_user", username="alice@test", password="alice-secret-pw", context=dict(owner_id=ALICE)),
                dict(name="bob", scope="end_user", username="bob@test", password="bob-secret-pw", context=dict(owner_id=BOB))]
    _, batch = create(client, spec, put_id, get_id, connection=dict(base_url="http://target.test", accounts=accounts))
    assert "alice-secret-pw" not in client.get(f"/api/v1/tool-batches/{batch['id']}").text  # credentials are never returned
    item = wait(client, batch["id"], lambda v: not v["busy"])["items"][0]
    pid = item["proposal_id"]
    r = client.post(f'/api/v1/tool-batches/{batch["id"]}/approve', json=dict(proposal_ids=[pid], confirmations={pid: FLAGS}, actor="Owner Ana"))
    assert r.status_code == 200, r.text
    view = wait(client, batch["id"], lambda v: not v["busy"], timeout=120)
    item = view["items"][0]
    proposal = client.get(f"/api/v1/proposals/{pid}").json()
    assert proposal["state"] == "approved_to_build", (item["state"], item["message"], item["problems"], [c["label"] for c in proposal["checklist"] if not c["done"]], [(f["question_id"], f["status"]) for r in proposal["reconciliations"][-1:] for f in r["result"]["findings"]], [(q["id"], q["text"][:40]) for q in proposal["content"]["questions"]], [(r["id"], r["kind"]) for r in proposal["requirements"]])  # reviewed and approved by the batch
    assert any("Owner Ana" in d["reason"] for d in proposal["decisions"]) and item["confirmed_by"] == "Owner Ana"
    assert item["artifact_id"], (item["state"], item["message"], item["problems"])  # built
    assert item["state"] in ("ready_to_publish", "needs_attention")
    assert item["state"] != "published"  # publishing is a separate owner step
    stored = open(settings.database_path, "rb").read()
    for secret in (b"alice-secret-pw", b"bob-secret-pw"):
        assert secret not in stored  # sealed at rest, in the database file and in the batch record
