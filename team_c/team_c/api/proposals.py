from ..compiler.policies import PolicySubmission
"""Proposal generation, review (answers, reconciliation, decisions), revisions and enforcement."""
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
from ..models import (AnswerSubmission, DecisionSubmission, EnforcementReviewSubmission, EnforcementSubmission, GapDismissalSubmission, RevisionSubmission,
                      ReviewSubmission, SupersedeSubmission)
from . import queries
from .deps import ServiceDep, StoreDep

router=APIRouter(prefix="/api/v1")


class GenerationInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    operation_ids: list[str] = []


@router.post("/inventories/{sid}/proposal-runs")
def generate(sid:str,service:ServiceDep,body:GenerationInput|None=None):
    return service.generate(sid,body.operation_ids if body else None)


@router.get("/businesses/{bid}/proposals")
def proposals(bid:str,service:ServiceDep,store:StoreDep):
    return queries.proposals(store,service,bid)


@router.get("/proposals/{pid}")
def proposal(pid:str,service:ServiceDep,version:int|None=None):
    return service.view(pid,version)


@router.post("/proposals/{pid}/versions/{version}/answers")
def answers(pid:str,version:int,body:AnswerSubmission,service:ServiceDep):
    return service.answers(pid,version,body)


@router.post("/proposals/{pid}/versions/{version}/reconcile")
def reconcile(pid:str,version:int,body:ReviewSubmission,service:ServiceDep):
    return service.reconcile(pid,version,body.expected_revision)


@router.post("/proposals/{pid}/versions/{version}/decisions")
def decision(pid:str,version:int,body:DecisionSubmission,service:ServiceDep):
    return service.decide(pid,version,body)


@router.post("/proposals/{pid}/versions/{version}/requirements/{rid}/confirm")
def confirm_requirement(pid:str,version:int,rid:str,body:ReviewSubmission,service:ServiceDep):
    return service.confirm_requirement(pid,version,rid,body)


@router.post("/proposals/{pid}/versions/{version}/gaps/{index}/dismiss")
def dismiss_gap(pid:str,version:int,index:int,body:GapDismissalSubmission,service:ServiceDep):
    return service.dismiss_gap(pid,version,index,body)


@router.post("/proposals/{pid}/versions/{version}/questions/{qid}/supersede")
def supersede_question(pid:str,version:int,qid:str,body:SupersedeSubmission,service:ServiceDep):
    return service.supersede_question(pid,version,qid,body)


@router.post("/proposals/{pid}/revisions")
def revision(pid:str,body:RevisionSubmission,service:ServiceDep):
    return service.revise(pid,body)


@router.post("/proposals/{pid}/enforcement")
def submit_enforcement(pid:str,body:EnforcementSubmission,service:ServiceDep):
    return service.submit_enforcement(pid,body)


@router.get("/enforcement/{eid}")
def enforcement(eid:str,service:ServiceDep):
    return service.enforcement(eid)


@router.post("/enforcement/{eid}/review")
def review_enforcement(eid:str,body:EnforcementReviewSubmission,service:ServiceDep):
    return service.review_enforcement(eid,body)


@router.post("/proposals/{pid}/versions/{version}/policy")
def accept_policy(pid: str, version: int, body: PolicySubmission, service: ServiceDep):
    return service.accept_policy(pid, version, body)


@router.post("/proposals/{pid}/versions/{version}/policy-review")
def reopen_policy(pid: str, version: int, body: ReviewSubmission, service: ServiceDep):
    return service.reopen_policy(pid, version, body)
