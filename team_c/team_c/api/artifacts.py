"""Artifacts, sandbox runs and tests, repairs and sandbox publications."""
from typing import Literal
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict
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


@router.post("/artifacts/{aid}/fix-and-retest")
def fix_and_retest(aid:str,body:dict,service:ServiceDep):
    return service.fix_and_retest(aid,str(body.get("identity","")),str(body.get("scenario","general")),body.get("record_owner"),body.get("arguments") or {},int(body.get("attempts") or 3))


@router.post("/sandbox-tests/{tid}/repairs")
def repair(tid:str,body:RepairSubmission,service:ServiceDep):
    return service.repair(tid,body)


@router.post("/artifacts/{aid}/policy-tests")
def policy_tests(aid: str, service: ServiceDep):
    return service.run_policy_tests(aid)


class ConnectorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    context_fields: list[str] = []
    environment: Literal["sandbox", "live_demo"] = "sandbox"
    confirmed: bool = False  # the owner's confirmation: a sandbox for tests, or the real API they may call (live_demo)


@router.get("/businesses/{bid}/connectors")
def connectors(bid: str, service: ServiceDep):
    """This business's sandbox and Live Demo connectors: environment, host, identity names, last check. No credentials."""
    return service.connectors_of(bid)


@router.post("/proposals/{pid}/connectors")
def save_connector(pid: str, body: ConnectorInput, service: ServiceDep):
    cid = service.connectors.save(pid, body.base_url, body.context_fields, confirmed=body.confirmed, environment=body.environment)
    return next(c for c in service.connectors_of(service.view(pid)["business_id"]) if c["id"] == cid)


@router.post("/connectors/{cid}/check")
def check_connector(cid: str, service: ServiceDep):
    """A real connection check: configuration, reachability, authentication, proven permissions, executable tools."""
    return service.check_connector(cid)
