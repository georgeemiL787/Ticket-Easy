"""NileStay inventory, its authored candidate and a run through a deterministic HTTP transport substitute."""
import json
import httpx
from conftest import ROOT
from team_c.discovery import discover
from team_c.providers import Providers


def inventory():
    return discover((ROOT/"examples/nilestay.json").read_bytes(),"nilestay.json","nilestay-test")[1]


def candidate(inv):
    read,write=inv["operations"]
    def b(target,kind,reference,step_id=None,response_status=None):
        return dict(target=target,kind=kind,reference=reference,step_id=step_id,response_status=response_status)
    return dict(name="Submit a guest service request",description="Look up the booking and submit its service request",business_purpose="Help guests request service",configuration=[],questions=[dict(id="q1",text="How will reservation ownership be verified before private information is accessed?",configuration_key=None)],steps=[dict(id="s1",operation_id=read["id"],purpose="Look up booking",bindings=[b("path.booking_reference","runtime_argument","booking_reference")]),dict(id="s2",operation_id=write["id"],purpose="Create service request",bindings=[b("body.reservation_id","previous_operation_output","/reservation_id","s1","200"),b("body.category","runtime_argument","category"),b("body.message","runtime_argument","message")])],outputs=[dict(name="request",step_id="s2",response_status="201",pointer="")],expected_reads=["Reservation details"],expected_writes=["Creates a service request record"],assumptions=[],limitations=["Guest ownership verification is unresolved; service credentials are connector-managed and access remains unverified"],risk="medium",risk_rationale="Accesses private reservation information and creates a record")


def run_with_transport(env, mutate=None):
    app,client,settings=env
    service=app.state.service
    b=service.business("NileStay",(ROOT/"examples/nilestay-business.txt").read_text())
    spec=service.upload(b["id"],"nilestay.json",(ROOT/"examples/nilestay.json").read_bytes())
    p=candidate(spec["inventory"])
    if mutate:mutate(p)
    # The constrained schema makes the model echo each referenced step's operation.
    ops={s["id"]:s["operation_id"] for s in p["steps"]}
    for o in p["outputs"]:o["operation_id"]=ops[o["step_id"]]
    for b in (b for s in p["steps"] for b in s["bindings"] if b["kind"]=="previous_operation_output"):b["source_operation_id"]=ops[b["step_id"]]
    raw=json.dumps(dict(proposals=[p],capability_gaps=[]))
    service.providers=Providers(settings,app.state.store,httpx.MockTransport(lambda req:httpx.Response(200,json={"message":{"content":raw},"done":True,"prompt_eval_count":1000})))
    return client.post(f'/api/v1/inventories/{spec["id"]}/proposal-runs'),client
