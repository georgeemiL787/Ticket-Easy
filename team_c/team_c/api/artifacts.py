"""Artifacts, sandbox runs and tests, repairs and sandbox publications."""
from fastapi import APIRouter
from ..models import ArtifactBuildSubmission, PublicationDisableSubmission, PublicationSubmission, RepairSubmission, SandboxRunSubmission, SandboxTestSubmission
from .deps import ServiceDep

router=APIRouter(prefix="/api/v1")


@router.post("/proposals/{pid}/artifacts")
def build_artifact(pid:str,body:ArtifactBuildSubmission,service:ServiceDep):
    return service.build_artifact(pid,body)


@router.get("/artifacts/{aid}")
def artifact(aid:str,service:ServiceDep):
    return service.artifact(aid)


@router.post("/artifacts/{aid}/sandbox-runs")
def sandbox_run(aid:str,body:SandboxRunSubmission,service:ServiceDep):
    return service.run_sandbox(aid,body)


@router.post("/artifacts/{aid}/sandbox-tests")
def sandbox_test(aid:str,body:SandboxTestSubmission,service:ServiceDep):
    return service.run_sandbox_test(aid,body)


@router.post("/artifacts/{aid}/publications")
def publish(aid:str,body:PublicationSubmission,service:ServiceDep):
    return service.publish(aid,body)


@router.get("/publications")
def publications(service:ServiceDep,business_id:str|None=None):
    return service.publications(business_id)


@router.get("/publications/{pub_id}")
def publication(pub_id:str,service:ServiceDep):
    return service.publication(pub_id)


@router.post("/publications/{pub_id}/disable")
def disable_publication(pub_id:str,body:PublicationDisableSubmission,service:ServiceDep):
    return service.disable_publication(pub_id,body)


@router.post("/sandbox-tests/{tid}/repairs")
def repair(tid:str,body:RepairSubmission,service:ServiceDep):
    return service.repair(tid,body)


@router.post("/artifacts/{aid}/policy-tests")
def policy_tests(aid: str, service: ServiceDep):
    return service.run_policy_tests(aid)
