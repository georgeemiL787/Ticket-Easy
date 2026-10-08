import json
import re
import pytest
from conftest import setup_proposal,answer_reconcile
from team_c.config import AppError
from team_c.models import ReconciliationOutput


def test_compact_check_prompt_preserves_all_answers_and_grounding_evidence():
    from team_c.llm.prompts import SYSTEM, RECONCILIATION_SYSTEM, model_messages
    payload = dict(proposal=dict(content=dict(questions=[dict(id="q1", text="Which setting?")]),
                                 answers={"q1": dict(id=7, text="explicit correction")}),
                   earlier_answers=[dict(id=3, question_id="q1", text="earlier contradictory value")],
                   grounding_feedback=dict(errors=["A binding is invalid"]))
    data, messages = model_messages("reconciliation", payload)
    assert json.loads(data) == payload
    assert messages[0]["content"] == RECONCILIATION_SYSTEM
    assert len(RECONCILIATION_SYSTEM) < len(SYSTEM) / 2
    assert "latest nonempty answer" in RECONCILIATION_SYSTEM and "UNTRUSTED_DATA" in messages[1]["content"]


@pytest.mark.parametrize("answer", [None, "", "   ", "cleared"])
def test_no_saved_nonempty_answers_blocks_reconciliation_before_any_model_call(env, answer):
    app, client, _ = env
    pid = setup_proposal(env)[2]["proposal_ids"][0]
    view = app.state.service.view(pid)
    if answer == "cleared":
        saved = client.post(f'/api/v1/proposals/{pid}/versions/1/answers', json=dict(expected_revision=view["review_revision"], answers={"q1": "Earlier answer"}))
        assert saved.status_code == 200
        view = saved.json()
        answer = ""
    if answer is not None:
        saved = client.post(f'/api/v1/proposals/{pid}/versions/1/answers', json=dict(expected_revision=view["review_revision"], answers={"q1": answer}))
        assert saved.status_code == 200
        view = saved.json()
    assert not view["can_check_answers"]
    before = app.state.store.all("SELECT id FROM runs")
    app.state.service.providers.call = lambda *args, **kwargs: pytest.fail("must not contact a model without saved answers")
    response = client.post(f'/api/v1/proposals/{pid}/versions/1/reconcile', json=dict(expected_revision=view["review_revision"]))
    assert response.status_code == 422 and response.json()["code"] == "answers_required"
    assert "Save answers" in response.json()["message"]
    assert app.state.store.all("SELECT id FROM runs") == before
    assert app.state.service.view(pid)["review_revision"] == view["review_revision"]
    page = client.get(f"/proposals/{pid}").text
    assert "No answers have been saved yet" in page
    assert re.search(r'<button\s+disabled[^>]*>Check answers</button>', page)


def test_partial_saved_answers_can_still_be_checked(env):
    app, client, _ = env
    pid = setup_proposal(env)[2]["proposal_ids"][0]
    view = app.state.service.view(pid)
    saved = client.post(f'/api/v1/proposals/{pid}/versions/1/answers', json=dict(expected_revision=view["review_revision"], answers={"q1": "yes"})).json()
    assert saved["can_check_answers"]
    page = client.get(f"/proposals/{pid}").text
    assert not re.search(r'<button\s+disabled[^>]*>Check answers</button>', page)
    response = client.post(f'/api/v1/proposals/{pid}/versions/1/reconcile', json=dict(expected_revision=saved["review_revision"]))
    assert response.status_code == 200 and response.json()["state"] == "needs_clarification"


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
