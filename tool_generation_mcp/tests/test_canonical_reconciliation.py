from conftest import setup_proposal,answer_reconcile
from team_c.models import ProposalContent
from team_c.models import GenerationOutput


def test_reordering_bindings_is_not_a_material_change(env):
    app,client,_=env
    pid=setup_proposal(env)[2]["proposal_ids"][0]
    original=app.state.service.providers.call
    def reorder(kind,payload,model,run):
        result=original(kind,payload,model,run)
        if kind=="reconciliation":
            result.revised_proposal=ProposalContent.model_validate(payload["proposal"]["content"])
            result.revised_proposal.steps[1].bindings.reverse()
        return result
    app.state.service.providers.call=reorder
    r=answer_reconcile(client,pid,"yes")
    assert r.status_code==200,r.text
    assert r.json()["version"]==1 and not r.json()["material_change"]


def test_model_cannot_resolve_unset_configuration(env):
    app,client,_=env
    pid=setup_proposal(env)[2]["proposal_ids"][0]
    original=app.state.service.providers.call
    def misleading(kind,payload,model,run):
        result=original(kind,payload,model,run)
        if kind=="reconciliation":
            result.findings[0].status="resolved"
        return result
    app.state.service.providers.call=misleading
    r=answer_reconcile(client,pid,"yes")
    assert r.status_code==200
    p=app.state.service.view(pid)
    assert p["state"]=="needs_clarification"
    assert p["reconciliations"][-1]["result"]["findings"][0]["status"]=="insufficient"


def test_unchanged_model_revision_is_an_explicit_failure(env):
    app,client,_=env
    pid=setup_proposal(env)[2]["proposal_ids"][0]
    p=app.state.service.view(pid)
    def unchanged(kind,payload,model,run):
        return GenerationOutput(proposals=[ProposalContent.model_validate(payload["proposal"])],capability_gaps=[])
    app.state.service.providers.call=unchanged
    r=client.post(f"/api/v1/proposals/{pid}/revisions",json={"expected_revision":p["review_revision"],"instruction":"Correct the description"})
    assert r.status_code==422,r.text
    assert r.json()["code"]=="revision_not_changed"
    preserved=app.state.service.view(pid)
    assert preserved["version"]==1 and preserved["content"]==p["content"]
