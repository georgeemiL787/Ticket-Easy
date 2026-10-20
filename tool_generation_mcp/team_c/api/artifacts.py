"""Artifacts, sandbox runs and tests, repairs and sandbox publications."""
from typing import Literal
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from ..models import ArtifactBuildSubmission, AutoTestSubmission, PublicationDisableSubmission, PublicationSubmission, RepairSubmission, SandboxRunSubmission, SandboxTestSubmission
from ..config import AppError
from .deps import AdminDep, ServiceDep, require_admin

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


@router.post("/artifacts/{aid}/auto-test")
def auto_test(aid:str,body:AutoTestSubmission,service:ServiceDep):
    """Provision test accounts and records, derive and run the scenarios, and report whether the tool is ready to publish.
    Starts in the background and answers with the run id (poll GET /auto-test-runs/{id}); `wait` answers when it is done."""
    if body.wait:
        return service.auto_test(aid,body.publish_when_ready,body.cleanup)
    return service.start_auto_test(aid,body.publish_when_ready,body.cleanup)


class BusinessSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_publish_sandbox: bool  # publish to the sandbox MCP when automatic tests pass (never Live Demo or production)


@router.get("/businesses/{bid}/settings")
def business_settings(bid:str,service:ServiceDep):
    return service.business_settings(bid)


@router.put("/businesses/{bid}/settings")
def set_business_settings(bid:str,body:BusinessSettingsInput,service:ServiceDep):
    return service.set_business_settings(bid,body.auto_publish_sandbox)


@router.get("/businesses/{bid}/artifacts")
def business_artifacts(bid:str,service:ServiceDep):
    """The newest artifact of each proposal with its readiness_state and latest automatic test."""
    return service.artifact_summaries(bid)


@router.get("/artifacts/{aid}/auto-test-runs")
def auto_test_runs(aid:str,service:ServiceDep):
    return service.auto_test_runs(aid)


@router.get("/auto-test-runs/{rid}")
def auto_test_run(rid:str,service:ServiceDep):
    return service.auto_test_run(rid)


@router.post("/sandbox-tests/{tid}/repairs")
def repair(tid:str,body:RepairSubmission,service:ServiceDep):
    return service.repair(tid,body)


@router.post("/artifacts/{aid}/policy-tests")
def policy_tests(aid: str, service: ServiceDep):
    return service.run_policy_tests(aid)


class FixturesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    key_ref: str  # the NAME of the environment variable that holds the fixture key, never the key itself


class ConnectorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str
    context_fields: list[str] = []
    environment: Literal["sandbox", "live_demo", "production"] = "sandbox"
    secret_ref: str | None = None  # production only: "env:NAME", the environment variable holding the service credential (never the credential)
    confirmed: bool = False  # the owner's confirmation: a sandbox for tests, or the real API they may call (live_demo)
    fixtures: FixturesInput | None = None  # sandbox only: the endpoints automatic tests use to create accounts and records


@router.get("/businesses/{bid}/connectors")
def connectors(bid: str, service: ServiceDep):
    """This business's sandbox and Live Demo connectors: environment, host, identity names, last check. No credentials."""
    return service.connectors_of(bid)


@router.post("/proposals/{pid}/connectors")
def save_connector(pid: str, body: ConnectorInput, service: ServiceDep, request: Request):
    if body.environment == "production":
        require_admin(request)
    cid = service.connectors.save(pid, body.base_url, body.context_fields, confirmed=body.confirmed, environment=body.environment,
                                  fixtures=body.fixtures.model_dump() if body.fixtures else None, secret_ref=body.secret_ref)
    return next(c for c in service.connectors_of(service.view(pid)["business_id"]) if c["id"] == cid)


@router.post("/connectors/{cid}/check")
def check_connector(cid: str, service: ServiceDep):
    """A real connection check: configuration, reachability, authentication, proven permissions, executable tools."""
    return service.check_connector(cid)


@router.put("/businesses/{bid}/connectors/{cid}/fixtures")
def set_fixtures(bid: str, cid: str, body: FixturesInput | None, service: ServiceDep):
    """Declare (or with null remove) the test-fixture endpoints of a sandbox connector."""
    service.connectors.set_fixtures(cid, bid, body.model_dump() if body else None)
    return next(c for c in service.connectors_of(bid) if c["id"] == cid)


class PromotionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str  # who decided it: Team B passes the signed-in admin's name
    note: str
    rollback_contact: str  # the person to reach when this has to be switched off
    production_connector_id: str | None = None
    accept_no_idempotency: bool = False  # the admin accepts that the API has no idempotency mechanism for these writes


class RollbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str
    note: str


@router.post("/artifacts/{aid}/promotions", dependencies=[AdminDep])
def promote(aid: str, body: PromotionInput, service: ServiceDep):
    """Promote a tested sandbox or Live Demo tool to production (admin token). Every checklist item must pass; a refused attempt is kept."""
    return service.promote(aid, body.production_connector_id, body.actor, body.note, body.rollback_contact, body.accept_no_idempotency)


@router.get("/businesses/{bid}/promotions")
def business_promotions(bid: str, service: ServiceDep):
    return service.promotions(bid)


@router.get("/promotions/{pid}")
def get_promotion(pid: str, service: ServiceDep):
    return service.promotion(pid)


@router.post("/promotions/{pid}/rollback", dependencies=[AdminDep])
def rollback_promotion(pid: str, body: RollbackInput, service: ServiceDep):
    """Switch a production tool off (admin token). Sandbox and Live Demo publications are left as they are."""
    return service.rollback_promotion(pid, body.actor, body.note)
