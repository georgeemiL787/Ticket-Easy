import json
import pytest
from fastapi.testclient import TestClient
from conftest import ROOT, DeterministicModelSubstitute, setup_proposal, answer_reconcile, confirm_requirements
from team_c.web import create_app


def decide(client,pid,action="approve_to_build",key="decision-key-123",reason=""):
    p=client.get(f"/api/v1/proposals/{pid}").json()
    return client.post(f'/api/v1/proposals/{pid}/versions/{p["version"]}/decisions',json=dict(expected_revision=p["review_revision"],action=action,reason=reason,idempotency_key=key))


def ready(client,pid):
    r=answer_reconcile(client,pid,"Use support.")
    assert r.status_code==200,r.text
    assert r.json()["material_change"] and r.json()["version"]==2
    p=client.get(f"/api/v1/proposals/{pid}").json()
    assert p["state"]=="needs_reconciliation"
    r=client.post(f"/api/v1/proposals/{pid}/versions/2/reconcile",json=dict(expected_revision=p["review_revision"]))
    assert r.status_code==200,r.text
    assert r.json()["state"]=="ready_for_review"
    assert decide(client,pid).status_code==422
    confirm_requirements(client,pid)


@pytest.mark.parametrize("example",["ecommerce.json","room-booking.yaml"])
def test_complete_flow_versions_and_restart(env,example):
    app,client,settings=env
    b,s,g=setup_proposal(env,example)
    pid=g["proposal_ids"][0]
    assert decide(client,pid).status_code==422
    ready(client,pid)
    d=decide(client,pid)
    assert d.status_code==200,d.text
    assert decide(client,pid).json()["id"]==d.json()["id"]
    assert decide(client,pid,key="new-key-same-action").json()["id"]==d.json()["id"]
    assert decide(client,pid,"reject",key="different-decision").status_code==409
    with TestClient(create_app(settings,DeterministicModelSubstitute)) as restarted:
        p=restarted.get(f"/api/v1/proposals/{pid}").json()
        assert p["state"]=="approved_to_build"
        assert len(p["decisions"])==1 and len(p["versions"])==2
        assert p["answer_history"]
        assert restarted.get(f'/api/v1/specifications/{s["id"]}/inventory').json()["checksum"]==s["checksum"]
        rev=restarted.post(f"/api/v1/proposals/{pid}/revisions",json=dict(expected_revision=p["review_revision"],instruction="Use a clearer name"))
        assert rev.status_code==200,rev.text
        assert rev.json()["version"]==3
        assert decide(restarted,pid,key="new-version-decision").status_code==422


@pytest.mark.parametrize("answer,status",[("yes","insufficient"),("whatever","insufficient"),("Use both support and sales.","contradictory"),("","insufficient")])
def test_nonempty_does_not_resolve(env,answer,status):
    _,client,_=env
    pid=setup_proposal(env)[2]["proposal_ids"][0]
    r=answer_reconcile(client,pid,answer)
    assert r.status_code==200,r.text
    p=client.get(f"/api/v1/proposals/{pid}").json()
    assert p["state"]=="needs_clarification"
    assert p["reconciliations"][-1]["result"]["findings"][0]["status"]==status
    assert decide(client,pid).status_code==422


def test_answers_invalidate_reconciliation_stale_review_and_independence(env):
    app,client,_=env
    app.state.service.providers.count=2
    ids=setup_proposal(env)[2]["proposal_ids"]
    ready(client,ids[0])
    assert decide(client,ids[1],"reject",key="reject-second").status_code==200
    p=client.get(f"/api/v1/proposals/{ids[0]}").json()
    base=f'/api/v1/proposals/{ids[0]}/versions/2'
    r=client.post(base+"/answers",json=dict(expected_revision=p["review_revision"],answers={"q1":"Use both support and sales."}))
    assert r.status_code==200
    assert r.json()["state"]=="needs_reconciliation"
    assert client.post(base+"/decisions",json=dict(expected_revision=p["review_revision"],action="approve_to_build",idempotency_key="stale-version-key")).status_code==409
    assert decide(client,ids[0]).status_code==422
    assert client.get(f"/api/v1/proposals/{ids[1]}").json()["state"]=="rejected"


def test_capability_gap_revision_and_invented_operation(env):
    app,client,_=env
    b,s,g=setup_proposal(env)
    pid=g["proposal_ids"][0]
    assert decide(client,pid,"request_changes",reason="Add refund",key="request-changes").status_code==200
    p=client.get(f"/api/v1/proposals/{pid}").json()
    r=client.post(f"/api/v1/proposals/{pid}/revisions",json=dict(expected_revision=p["review_revision"],instruction="Refund order"))
    assert r.status_code==200 and r.json()["capability_gaps"]
    assert client.get(f"/api/v1/proposals/{pid}").json()["state"]=="changes_requested"
    app.state.service.providers.mode="invented"
    r=client.post(f'/api/v1/inventories/{s["id"]}/proposal-runs')
    assert r.status_code==422 and r.json()["code"]=="invalid_bindings"
    assert len(client.get(f'/api/v1/businesses/{b["id"]}/proposals').json())==1


def test_ui_csrf_escape_and_identity(env):
    _,client,_=env
    r=client.post("/actions/business",data=dict(name="bad",description="bad"))
    assert r.status_code==403
    r=client.post("/api/v1/businesses",json=dict(name="<script>alert(1)</script>",description="text",reviewer="spoof"))
    assert r.status_code==422
    r=client.post("/api/v1/businesses",json=dict(name="<script>alert(1)</script>",description="text"))
    assert r.status_code==200
    page=client.get("/").text
    assert "&lt;script&gt;" in page and "local-owner" in page
    assert client.post("/api/v1/businesses",headers={"origin":"https://evil.invalid"},json=dict(name="x",description="y")).status_code==403
    b,s,g=setup_proposal(env)
    for path in [f'/businesses/{b["id"]}',f'/specifications/{s["id"]}',f'/proposals/{g["proposal_ids"][0]}',f'/runs/{g["run_id"]}']:
        r=client.get(path)
        assert r.status_code==200,(path,r.text)


def test_html_upload_form_with_csrf(env):
    import re
    _,client,_=env
    page=client.get("/").text
    csrf=re.search(r'name="csrf" value="([^"]+)"',page).group(1)
    r=client.post("/actions/business",data=dict(csrf=csrf,name="Form upload",description="Example"))
    assert r.status_code==200
    bid=str(r.url).rsplit("/",1)[-1]
    r=client.post("/actions/upload",data=dict(csrf=csrf,business_id=bid),files={"file":("ecommerce.json",(ROOT/"examples/ecommerce.json").read_bytes())})
    assert r.status_code==200 and "Capability inventory" in r.text and "2 operations" in r.text


def test_restart_preserves_pending_and_marks_interrupted(env):
    app,client,settings=env
    b,s,g=setup_proposal(env)
    pid=g["proposal_ids"][0]
    run=app.state.store.start_run(b["id"],"generation",{})
    live=app.state.store.start_run(b["id"],"generation",{})
    with app.state.store.connect(write=True) as c:
        c.execute("UPDATE runs SET owner=? WHERE id=?",("0"*32,run))  # owner process has exited
    with TestClient(create_app(settings,DeterministicModelSubstitute)) as new:
        assert new.get(f"/api/v1/proposal-runs/{live}").json()["status"]=="running"
        assert new.get(f"/api/v1/proposals/{pid}").json()["state"]=="needs_clarification"
        assert new.get(f"/api/v1/proposal-runs/{run}").json()["status"]=="interrupted"


def test_concurrent_identical_decisions_are_single_record(env):
    from concurrent.futures import ThreadPoolExecutor
    from team_c.models import DecisionSubmission
    app,client,_=env
    pid=setup_proposal(env)[2]["proposal_ids"][0]
    ready(client,pid)
    p=app.state.service.view(pid)
    data=DecisionSubmission(expected_revision=p["review_revision"],action="approve_to_build",idempotency_key="concurrent-decision")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:app.state.service.decide(pid,2,data),range(2)))
    assert results[0]["id"]==results[1]["id"]
    assert len(app.state.service.view(pid)["decisions"])==1
