import json
import pytest
from conftest import setup_proposal,answer_reconcile
from team_c.config import AppError
from team_c.models import ReconciliationOutput


@pytest.mark.parametrize("mode",["omit_question","invent_evidence","no_evidence","invent_operation","remove_question","provider_failure"])
def test_bad_reconciliation_never_approves(env,mode):
    app,client,_=env
    pid=setup_proposal(env)[2]["proposal_ids"][0]
    original=app.state.service.providers.call
    def bad(kind,payload,model,run):
        result=original(kind,payload,model,run)
        if kind!="reconciliation":return result
        if mode=="provider_failure":raise AppError("provider_service_failure","Test substitute failure",502)
        if mode=="omit_question":result.findings=[]
        if mode=="invent_evidence":result.findings[0].answer_revision_ids=[99999]
        if mode=="no_evidence":result.findings[0].answer_revision_ids=[]
        if mode=="invent_operation":result.revised_proposal.steps[0].operation_id="invented"
        if mode=="remove_question":result.revised_proposal.questions=[]
        return result
    app.state.service.providers.call=bad
    r=answer_reconcile(client,pid,"Use support.")
    assert r.status_code in {422,502},r.text
    p=client.get(f"/api/v1/proposals/{pid}").json()
    assert p["state"]=="needs_reconciliation"
    assert p["version"]==1 and p["answers"]["q1"]["text"]=="Use support."
    assert not p["decisions"]
    run=client.get('/api/v1/proposal-runs/'+r.json()["details"]["run_id"]).json()
    assert run["status"]=="failed"


def test_generation_gap_cannot_disappear_through_reconciliation(env):
    app,client,_=env
    original=app.state.service.providers.call
    def mixed(kind,payload,model,run):
        result=original(kind,payload,model,run)
        if kind=="generation":
            from team_c.models import CapabilityGap
            result.capability_gaps=[CapabilityGap(requested_capability="Refund",explanation="No refund API exists")]
        return result
    app.state.service.providers.call=mixed
    pid=setup_proposal(env)[2]["proposal_ids"][0]
    assert answer_reconcile(client,pid,"Use support.").status_code==200
    p=client.get(f"/api/v1/proposals/{pid}").json()
    r=client.post(f'/api/v1/proposals/{pid}/versions/{p["version"]}/reconcile',json=dict(expected_revision=p["review_revision"]))
    assert r.status_code==200 and r.json()["state"]=="needs_clarification"


def test_stale_reconciliation_cannot_commit(env):
    app,client,_=env
    pid=setup_proposal(env)[2]["proposal_ids"][0]
    original=app.state.service.providers.call
    def intervening(kind,payload,model,run):
        result=original(kind,payload,model,run)
        if kind=="reconciliation":
            from team_c.models import AnswerSubmission
            p=app.state.service.view(pid)
            app.state.service.answers(pid,p["version"],AnswerSubmission(expected_revision=p["review_revision"],answers={"q1":"New intervening answer"}))
        return result
    app.state.service.providers.call=intervening
    r=answer_reconcile(client,pid,"Use support.")
    assert r.status_code==409
    p=app.state.service.view(pid)
    assert p["version"]==1 and p["answers"]["q1"]["text"]=="New intervening answer"
