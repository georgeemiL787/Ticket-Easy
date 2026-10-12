"""A rating written before the owner answered must not stay the verdict after they did.

Generation rates a proposal with the questions still open, so security_posture is honestly "revise".
Reconciliation is the point at which the answers and access requirements exist, so it re-rates. The
superseded rating stays in the content and in the reconciliation history for audit, but approval
reads the current one.
"""
from helpers.lifecycle import answer_all, approve, confirm_all, current, generate, post
from team_c.models import CRITERIA
from team_c.quality import assess


def start(lifecycle):
    _, client, _, spec, scope = lifecycle
    return client, generate(client, spec, scope)


def reconcile(client, pid, limit=3):
    """Reconcile until the answers stop requiring a content change.

    A material change starts a new version whose evidence has to be checked again, so one pass is
    not always enough to reach a reviewable state.
    """
    for _ in range(limit):
        r = post(client, pid, "reconcile")
        assert r.status_code == 200, r.text
        if r.json()["state"] != "needs_reconciliation":
            return r.json()
    raise AssertionError("reconciliation kept producing a material change")


def test_1_generation_rates_the_proposal_before_the_owner_has_answered(lifecycle):
    client, pid = start(lifecycle)
    p = current(client, pid)
    assert p["content"]["self_review"]["verdict"] == "revise"
    assert p["quality"]["review_source"] == "generation"
    assert p["quality"]["ready"] is False
    assert "self_verdict_revise" in {f["key"] for f in p["quality"]["findings"]}


def test_2_to_6_answering_and_reconciling_refreshes_the_review_and_unblocks_approval(lifecycle):
    client, pid = start(lifecycle)
    original = current(client, pid)["content"]["self_review"]

    answer_all(client, pid)
    result = reconcile(client, pid)
    assert result["state"] in ("ready_for_review", "needs_reconciliation")

    p = current(client, pid)
    assert p["quality"]["review_source"] == "reconciliation"
    assert p["quality"]["verdict"] == "proceed", p["quality"]["blockers"]
    assert p["quality"]["ready"] is True, p["quality"]["findings"]

    # The generation-time rating survives as audit evidence; it is just no longer the verdict.
    assert p["content"]["self_review"] == original
    assert p["content"]["self_review"]["verdict"] == "revise"
    assert p["quality"]["superseded_review"]["verdict"] == "revise"

    confirm_all(client, pid)
    approved = approve(client, pid, key="refresh-approval-1")
    assert approved.status_code == 200, approved.text
    assert current(client, pid)["state"] == "approved_to_build"


def test_7_answers_that_do_not_resolve_the_concern_leave_the_review_at_revise(lifecycle):
    """Refreshing is not a formality: an owner answer that fails to establish the decision still blocks."""
    client, pid = start(lifecycle)
    answer_all(client, pid, text="Only the owner, I suppose.", requirement_text="Only the owner, I suppose.")
    reconcile(client, pid)

    p = current(client, pid)
    assert p["quality"]["review_source"] == "reconciliation"
    assert p["quality"]["verdict"] == "revise"
    assert p["quality"]["ready"] is False

    approved = approve(client, pid, key="refresh-approval-2")
    assert approved.status_code == 422
    assert approved.json()["code"] == "approval_blocked"


def test_8_a_deterministic_defect_is_still_blocked_when_the_current_review_says_proceed():
    """A good rating never overrides what the application can establish for itself."""
    content = dict(
        name="Two reads of the same field", description="d", business_purpose="p",
        steps=[dict(id="s1", operation_id="op1", purpose="read", bindings=[])],
        configuration=[], questions=[],
        outputs=[dict(name="a", step_id="s1", response_status="200", pointer="/items"),
                 dict(name="b", step_id="s1", response_status="200", pointer="/items")],
        expected_reads=[], expected_writes=[], assumptions=[], limitations=[], risk="low", risk_rationale="r",
        self_review=dict(criteria=[dict(criterion=c, score=2, reason="stale") for c in CRITERIA],
                         verdict="revise", summary="superseded"))
    inventory = {"operations": [{"id": "op1", "method": "GET", "path": "/things",
                                 "inputs": {"path.id": {"required": True}}}]}

    refreshed = dict(criteria=[dict(criterion=c, score=5, reason="excellent") for c in CRITERIA],
                     verdict="proceed", summary="nothing outstanding")
    report = assess(content, inventory, current_review=refreshed)
    assert report["review_source"] == "reconciliation"
    assert report["verdict"] == "revise" and report["ready"] is False
    assert "duplicate_output" in {f["key"] for f in report["findings"]}
    assert "unbound_required_input" in {f["key"] for f in report["findings"]}
    # The superseded rating is still reported so the owner can see what changed.
    assert report["superseded_review"]["verdict"] == "revise"


def test_9_a_decision_closes_the_version_so_approval_cannot_rest_on_later_answers(lifecycle):
    client, pid = start(lifecycle)
    answer_all(client, pid)
    reconcile(client, pid)
    confirm_all(client, pid)
    assert approve(client, pid, key="refresh-approval-3").status_code == 200
    assert current(client, pid)["state"] == "approved_to_build"

    # The approval is a snapshot of the evidence it was granted on: the answers behind it cannot be
    # edited in place. Changing them means a new version, which starts unapproved.
    p = current(client, pid)
    r = client.post(f'/api/v1/proposals/{pid}/versions/{p["version"]}/answers',
                    json=dict(expected_revision=p["review_revision"],
                              answers={q["id"]: "TEST-ONLY: changed my mind, anyone may use it."
                                       for q in p["content"]["questions"]}))
    assert r.status_code == 409
    assert r.json()["code"] == "closed_version"
    assert current(client, pid)["state"] == "approved_to_build"
