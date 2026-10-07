"""HTML pages and the live progress page of background AI jobs."""
import secrets
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from .. import capabilities
from ..config import AppError
from ..executor import load_connectors
from ..jobs import cancel, live_view, title
from ..api import queries
from ..api.deps import JobsDep, ServiceDep, SettingsDep, StoreDep
from .forms import check_csrf
from .render import page

router=APIRouter()


def connector_choices(settings,business_id):
    return {cid:sorted(c.get("identities") or {}) for cid,c in load_connectors(settings).items() if c.get("business_id")==business_id}


def connector_labels(settings, business_id):
    return {cid: c["base_url"] + " (" + cid + ")" for cid, c in load_connectors(settings).items() if c.get("business_id") == business_id}


def active_areas(service,bid):
    try:
        return service.areas(service.request_spec(bid)["id"])
    except AppError:
        return None


@router.get("/")
def home(request:Request,store:StoreDep):
    return page(request,screen="home",businesses=queries.businesses(store))


@router.get("/businesses/{bid}")
def business_page(request:Request,bid:str,service:ServiceDep,store:StoreDep,settings:SettingsDep,saved:str=""):
    return page(request,screen="business",business=queries.business(store,bid),specs=queries.specifications(store,bid),proposals=queries.proposals(store,service,bid),runs=queries.runs(store,bid),
                tool_requests=service.tool_requests(bid),suggested=service.suggestions(bid),request_key=secrets.token_urlsafe(24),suggestion_count=settings.suggestion_count,
                tools={t["proposal_id"]:t for t in service.existing_tools(bid)},
                fetch_hosts=[h.strip() for h in settings.openapi_fetch_hosts.split(",") if h.strip()],
                active_areas=active_areas(service,bid),saved=saved)


@router.get("/specifications/{sid}")
def spec_page(request:Request,sid:str,service:ServiceDep,store:StoreDep,batch:str="",saved:str=""):
    spec=service.spec(sid)
    openapi=spec["inventory"].get("source_kind")!="code"
    next_batch=service.next_generation_batch(sid) if openapi and spec["inventory"]["proposal_generation_ready"] else None
    return page(request,screen="inventory",spec=spec,business=queries.business(store,spec["business_id"]),areas=service.areas(sid) if openapi else None,
                ops={o["id"]:o for o in spec["inventory"]["operations"]},reasons={o["id"]:capabilities.reason(o) for o in spec["inventory"]["operations"]},used=service.generation_used(sid),
                next_batch=next_batch,preselect=set(next_batch["operation_ids"]) if next_batch and batch=="next" else set(),saved=saved)


@router.get("/proposals/{pid}")
def proposal_page(request:Request,pid:str,service:ServiceDep,store:StoreDep,settings:SettingsDep,version:int|None=None):
    view=service.view(pid,version)
    previous=service.view(pid,view["version"]-1)["content"] if view["version"]>1 else None
    operations={op["id"]:op for op in service.spec(view["spec_id"])["inventory"]["operations"]}
    artifacts=queries.artifacts(store,pid)
    return page(request,screen="proposal",p=view,previous=previous,operations=operations,decision_key=secrets.token_urlsafe(24),artifacts=artifacts,connectors=connector_choices(settings,view["business_id"]),business=queries.business(store,view["business_id"]),connector_draft=service.connectors.draft(pid),connector_labels=connector_labels(settings,view["business_id"]))


@router.get("/artifacts/{aid}")
def artifact_page(request:Request,aid:str,service:ServiceDep,store:StoreDep,settings:SettingsDep):
    _,problems,_=service.publication_problems(aid)
    a=service.artifact(aid)
    bid=a["content"]["proposal"]["business_id"]
    return page(request,screen="artifact",a=a,connectors=connector_choices(settings,bid),publications=service.publications(artifact_id=aid),publication_problems=problems,business=queries.business(store,bid),identity_setup=service.connectors.identity_setup(a["content"]))


@router.get("/runs/{rid}")
def run_page(request:Request,rid:str,store:StoreDep):
    r=queries.run(store,rid)
    return page(request,screen="run",run=r,business=queries.business(store,r["business_id"]))


@router.get("/live/{jid}")
def live_page(request:Request,jid:str,jobs:JobsDep,settings:SettingsDep,busy:str=""):
    return page(request,screen="live",job=live_view(jobs,jid,settings),title=title(jobs[jid]),busy=busy=="1")


@router.post("/live/{jid}/cancel")
async def live_cancel(request:Request,jid:str,jobs:JobsDep):
    form=await request.form()
    check_csrf(request,form)
    cancel(jobs,jid)
    return RedirectResponse("/live/"+jid,status_code=303)
