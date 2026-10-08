"""Deterministic MODEL SUBSTITUTES: these are not evidence of live AI behavior."""
import json
from pathlib import Path
import pytest
from team_c.config import Settings
from team_c.models import CRITERIA, GenerationOutput, ReconciliationOutput
from team_c.web import create_app
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]


def candidate(inventory, configured=False):
    read,write=inventory["operations"]
    booking="reservations" in read["path"]
    runtime_id="reservation_id" if booking else "order_id"
    previous="room_id" if booking else "customer_id"
    config="team_id" if booking else "queue_id"
    message="message" if booking else "summary"
    def binding(target,kind,ref,step=None,status=None):
        return dict(target=target,kind=kind,reference=ref,step_id=step,response_status=status)
    bindings=[binding("body."+previous,"previous_operation_output","/"+previous,"s1","200"),binding("body."+message,"runtime_argument",message),binding("body."+config,"business_configuration",config)]
    if not booking:
        bindings.append(binding("body.order_id","runtime_argument","order_id"))
    return dict(name="Open a service ticket",description="Look up the record then create a request",business_purpose="Route a customer service request",steps=[dict(id="s1",operation_id=read["id"],purpose="Look up the record",bindings=[binding("path."+runtime_id,"runtime_argument",runtime_id)]),dict(id="s2",operation_id=write["id"],purpose="Create request after successful lookup",bindings=bindings)],outputs=[dict(name="request",step_id="s2",response_status="201",pointer="")],expected_reads=["Record and associated identifier"],expected_writes=["Creates a request"],configuration=[dict(key=config,value_json='"support"' if configured else None)],assumptions=["Customer authorization is a future prerequisite"],questions=[dict(id="q1",text=f"Which {config} should receive requests?",configuration_key=config)],limitations=["Access is unverified; no runtime execution exists"],risk="medium",risk_rationale="Creates a record")


class DeterministicModelSubstitute:
    def __init__(self,settings,store):
        self.store=store
        self.mode="normal"
        self.count=1

    def call(self,kind,payload,output_model,run):
        self.store.attempt(run,"TEST_SUBSTITUTE","deterministic-test-only","succeeded")
        if kind=="generation":
            p=candidate(payload["inventory"])
            if self.mode=="invented": p["steps"][0]["operation_id"]="made-up"
            return GenerationOutput(proposals=[p for _ in range(self.count)],capability_gaps=[])
        if kind.startswith("revision"):
            if "refund" in payload["instruction"].lower():
                return GenerationOutput(proposals=[],capability_gaps=[dict(requested_capability="refund",explanation="No refund operation exists")])
            p=payload["proposal"].copy()
            p["name"]="Revised request tool"
            return GenerationOutput(proposals=[p],capability_gaps=[])
        v=payload["proposal"]
        answers=v["answers"]
        a=answers.get("q1")
        text=a["text"] if a else ""
        status="resolved" if text=="Use support." else "contradictory" if "both" in text else "insufficient"
        revised=None
        if status=="resolved" and v["content"]["configuration"][0]["value_json"] is None:
            revised=json.loads(json.dumps(v["content"]))
            revised["configuration"][0]["value_json"]='"support"'
        return ReconciliationOutput(findings=[dict(question_id="q1",status=status,explanation="Explicit queue required; evaluated answer against the design",answer_revision_ids=[a["id"]] if a else [])]+requirement_findings(payload),revised_proposal=revised,capability_gaps=[],self_review=refreshed_review("proceed" if status=="resolved" else "revise"))


def refreshed_review(verdict="proceed", summary="The owner's answers resolved the concerns this proposal was rated on."):
    """A reconciliation-time rating: the CURRENT verdict, replacing the generation-time one."""
    score = 4 if verdict == "proceed" else 2
    return dict(criteria=[dict(criterion=c, score=score, reason="Assessed against the reconciled answers") for c in CRITERIA],
                verdict=verdict, summary=summary)


TEST_REQUIREMENT_ANSWER="TEST-ONLY (not a business decision): only the authenticated record owner may use these operations."


def requirement_findings(payload):
    """Substitute assessment: only explicitly labeled test answers resolve; 'both' is contradictory."""
    result=[]
    for r in payload.get("requirements",[]):
        a=payload["proposal"]["answers"].get(r["id"])
        text=a["text"] if a else ""
        status="resolved" if text.startswith("TEST-ONLY") and "both" not in text else "contradictory" if "both" in text else "insufficient"
        result.append(dict(question_id=r["id"],status=status,explanation="Test substitute assessment",answer_revision_ids=[a["id"]] if a else []))
    return result


@pytest.fixture
def env(tmp_path):
    settings=Settings(_env_file=None,database_path=str(tmp_path/"test.db"),session_secret="test-secret")
    app=create_app(settings,DeterministicModelSubstitute)
    with TestClient(app) as client:
        yield app,client,settings


def setup_proposal(env,example="ecommerce.json"):
    app,client,_=env
    b=client.post("/api/v1/businesses",json=dict(name="Test business",description="A generic business")).json()
    upload=client.post(f'/api/v1/businesses/{b["id"]}/specifications',files={"file":(example,(ROOT/"examples"/example).read_bytes())})
    assert upload.status_code==200,upload.text
    spec=upload.json()
    assert spec["inventory"]["valid"],spec
    generated=client.post(f'/api/v1/inventories/{spec["id"]}/proposal-runs')
    assert generated.status_code==200,generated.text
    return b,spec,generated.json()


def answer_reconcile(client,pid,text,requirement_answer=TEST_REQUIREMENT_ANSWER):
    p=client.get(f"/api/v1/proposals/{pid}").json()
    base=f'/api/v1/proposals/{pid}/versions/{p["version"]}'
    answers={"q1":text,**{r["id"]:requirement_answer for r in p["requirements"]}}
    saved=client.post(base+"/answers",json=dict(expected_revision=p["review_revision"],answers=answers))
    assert saved.status_code==200,saved.text
    return client.post(base+"/reconcile",json=dict(expected_revision=saved.json()["review_revision"]))


def confirm_requirements(client,pid):
    """Test-only owner confirmation of every requirement the current reconciliation found sufficient."""
    p=client.get(f"/api/v1/proposals/{pid}").json()
    for r in p["requirements"]:
        response=client.post(f'/api/v1/proposals/{pid}/versions/{p["version"]}/requirements/{r["id"]}/confirm',json=dict(expected_revision=p["review_revision"]))
        assert response.status_code==200,response.text
        p=response.json()
    return p


# Shared fixtures. The helpers import ROOT and requirement_findings from this module, so they are imported only after those are defined.
from helpers.desk import make_desk  # noqa: E402
from helpers.lifecycle import LifecycleSubstitute  # noqa: E402
from helpers.openapi import op, target_spec  # noqa: E402
from helpers.tool_requests import RequestDesk  # noqa: E402


@pytest.fixture
def desk(tmp_path, monkeypatch):
    monkeypatch.setattr("helpers.desk.DeskSubstitute", RequestDesk)
    d = make_desk(tmp_path, "base")
    d.service = d.app.state.service
    inv, n = d.spec["inventory"], d.n
    d.lookup, d.create = op(inv, "GET", n["prefix"] + n["lookup"])["id"], op(inv, "POST", n["prefix"] + n["create"])["id"]
    d.health = op(inv, "GET", "/health")["id"]
    d.attach = next(o["id"] for o in inv["operations"] if not o["proposal_eligible"])
    d.bid = d.service.spec(d.spec["id"])["business_id"]
    yield d
    d.client.__exit__(None, None, None)


@pytest.fixture
def lifecycle(tmp_path):
    settings = Settings(_env_file=None, database_path=str(tmp_path / "lifecycle.db"), session_secret="test-secret")
    app = create_app(settings, LifecycleSubstitute)
    with TestClient(app) as client:
        b = client.post("/api/v1/businesses", json=dict(name="Items (test)", description="Test business")).json()
        spec = client.post(f'/api/v1/businesses/{b["id"]}/specifications', files={"file": ("openapi.json", json.dumps(target_spec()).encode())}).json()
        scope = [op(spec["inventory"], m, "/api/v1/items/{id}")["id"] for m in ("GET", "PUT")]
        yield app, client, settings, spec, scope
