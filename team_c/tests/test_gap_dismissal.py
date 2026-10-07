"""A missing capability reported by the answer check can be marked not needed, with a recorded reason, to reach review."""
from conftest import answer_reconcile, confirm_requirements, setup_proposal
from team_c.models import CapabilityGap
from team_c.providers import SYSTEM


def reporting_gaps(env, insufficient=False):
    app = env[0]
    original = app.state.service.providers.call
    def call(kind, payload, model, run):
        result = original(kind, payload, model, run)
        if kind == "reconciliation":
            result.capability_gaps = [CapabilityGap(requested_capability="Award achievements", explanation="No operation awards achievements")]
            if insufficient:
                result.findings[0].status = "insufficient"
        return result
    app.state.service.providers.call = call


def checked(client, pid):
    assert answer_reconcile(client, pid, "Use support.").json()["state"] == "needs_reconciliation"
    p = client.get(f"/api/v1/proposals/{pid}").json()
    r = client.post(f'/api/v1/proposals/{pid}/versions/{p["version"]}/reconcile', json=dict(expected_revision=p["review_revision"]))
    assert r.json()["state"] == "needs_clarification", r.text
    return client.get(f"/api/v1/proposals/{pid}").json()


def dismiss(client, p, index=0, reason="TEST-ONLY: achievements are shown, never awarded by this tool"):
    return client.post(f'/api/v1/proposals/{p["proposal_id"]}/versions/{p["version"]}/gaps/{index}/dismiss', json=dict(expected_revision=p["review_revision"], reason=reason))


def test_reconciliation_prompt_limits_capability_gaps():
    assert "capability_gaps item only when an owner answer requires an action that no supplied operation performs" in SYSTEM


def test_dismissing_every_reported_gap_reaches_review_and_approval(env):
    app, client, _ = env
    pid = setup_proposal(env)[2]["proposal_ids"][0]
    reporting_gaps(env)
    p = checked(client, pid)
    assert p["answers_resolved"] and p["open_gaps"] == [0]
    page = client.get(f"/proposals/{pid}").text
    assert "decide about the reported missing capabilities" in page and "Not needed for this tool" in page
    assert dismiss(client, p, reason="").status_code == 422
    assert dismiss(client, p, index=1).status_code == 404
    r = dismiss(client, p)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["state"] == "ready_for_review" and p["open_gaps"] == []
    rec = next(x for x in p["reconciliations"] if x["id"] == p["reconciliation_id"])
    assert rec["successful"] and rec["dismissals"]["0"]["reason"].startswith("TEST-ONLY")
    assert "achievements are shown" in client.get(f"/proposals/{pid}").text
    p = confirm_requirements(client, pid)
    approved = client.post(f"/api/v1/proposals/{pid}/versions/{p['version']}/decisions", json=dict(expected_revision=p["review_revision"], action="approve_to_build", idempotency_key="gap-dismissal-approval"))
    assert approved.status_code == 200, approved.text


def test_dismissing_a_gap_does_not_bypass_unresolved_answers(env):
    app, client, _ = env
    pid = setup_proposal(env)[2]["proposal_ids"][0]
    reporting_gaps(env, insufficient=True)
    p = checked(client, pid)
    assert not p["answers_resolved"]
    p = dismiss(client, p).json()
    assert p["state"] == "needs_clarification"
    assert not next(x for x in p["reconciliations"] if x["id"] == p["reconciliation_id"])["successful"]
    assert "answer the questions" in client.get(f"/proposals/{pid}").text
