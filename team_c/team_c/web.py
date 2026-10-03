import json
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, ConfigDict, ValidationError
from .config import Settings, AppError
from .diagnostics import check_errors
from .models import (AnswerSubmission, ArtifactBuildSubmission, DecisionSubmission, EnforcementReviewSubmission, EnforcementSubmission, RepairSubmission,
                     PublicationDisableSubmission, PublicationSubmission, RevisionSubmission, ReviewSubmission, SandboxRunSubmission, SandboxTestSubmission, SupersedeSubmission,
                     ClarificationSubmission, SuggestionDecisionSubmission, SuggestionRunSubmission, ToolRequestSubmission, AreaSelectionSubmission)
from . import capabilities
from .executor import load_connectors
from .providers import LIVE, Providers
from .storage import Store
from .service import Service


class BusinessInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    name: str
    description: str


class FetchInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    url: str


class GenerationInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    operation_ids: list[str] = []


LIVE_ACTIONS={"generate","request_tool","clarify_request","request_next_batch","suggest","areas_organize","reconcile","revise"}
LIVE_WAIT_SECONDS=2
ACTION_LABELS={"generate":"Generating a tool proposal","request_tool":"Working on your tool request","clarify_request":"Working on your tool request",
               "request_next_batch":"Looking in the next batch","suggest":"Looking for useful tools","areas_organize":"Organizing business areas",
               "reconcile":"Checking your answers","revise":"Revising the proposal","decide_suggestion":"Turning the suggestion into a tool request"}
STEP_LABELS={"request_triage":"Checking whether your API can do this","generation":"Writing the tool proposal","suggestion":"Thinking of useful tools",
             "area_naming":"Naming and ranking business areas","area_assignment":"Placing API groups in areas","reconciliation":"Checking your answers",
             "revision":"Revising the proposal","repair":"Repairing the proposal"}


def retired():
    raise AppError("code_discovery_retired","Local-code discovery is retired; existing code inventories remain read-only. Upload or fetch an OpenAPI description instead",410)


def create_app(settings=None, provider_factory=None):
    settings=settings or Settings()
    store=Store(settings.database_path)
    providers=provider_factory(settings,store) if provider_factory else Providers(settings,store)
    service=Service(settings,store,providers)
    app=FastAPI(title="Ticket-Easy Team C",version="0.1.0",description="Local development review. Approval means approved to build, never active or published.")
    app.state.service=service
    app.state.store=store
    jobs={}
    app.state.jobs=jobs
    app.add_middleware(SessionMiddleware,secret_key=settings.session_secret or secrets.token_hex(32),same_site="strict")
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=["127.0.0.1","localhost","testserver"])
    templates=Jinja2Templates(directory=str(Path(__file__).parent/"templates"))

    @app.middleware("http")
    async def same_origin(request,call_next):
        if request.method in {"POST","PUT","PATCH","DELETE"}:
            origin=request.headers.get("origin")
            if origin and urlparse(origin).netloc!=request.headers.get("host"):
                return JSONResponse({"code":"origin_rejected","message":"Cross-origin mutations are forbidden"},status_code=403)
        return await call_next(request)

    def running_job():
        return next((j for j in jobs.values() if j["status"]=="running"),None)

    def page(request,**context):
        if "csrf" not in request.session:
            request.session["csrf"]=secrets.token_urlsafe(32)
        running=running_job()
        ai_job=dict(id=running["id"],title=ACTION_LABELS.get(running["action"],"Working"),minutes=int((time.time()-running["started"])//60)) if running else None
        return templates.TemplateResponse(request=request,name=context["screen"]+".html",context=dict(csrf=request.session["csrf"],reviewer=settings.dev_reviewer_id,provider=settings.llm_primary,model=getattr(settings,settings.llm_primary+"_model",""),fallback=settings.llm_fallback,ai_job=ai_job,**context))

    @app.exception_handler(AppError)
    async def error(request,exc):
        payload=dict(code=exc.code,message=exc.message,details=exc.details)
        if request.url.path.startswith("/api/"):
            return JSONResponse(payload,status_code=exc.status)
        response=page(request,screen="error",error=payload)
        response.status_code=exc.status
        return response

    @app.exception_handler(ValidationError)
    async def form_error(request,exc):
        return await error(request,AppError("invalid_input","Submitted form is invalid",details={"errors":str(exc)[:500]}))

    @app.get("/health")
    def health():
        return {"status":"ok","scope":"approved_to_build_only"}

    @app.post("/api/v1/businesses")
    def business(body:BusinessInput):
        return service.business(body.name,body.description)

    @app.get("/api/v1/businesses")
    def businesses():
        return store.all("SELECT * FROM businesses ORDER BY created_at DESC")

    @app.post("/api/v1/businesses/{bid}/specifications")
    def upload(bid:str,file:UploadFile=File(...)):
        return service.upload(bid,file.filename or "upload",file.file.read(settings.max_upload_bytes+1))

    @app.post("/api/v1/businesses/{bid}/specifications/fetch")
    def fetch(bid:str,body:FetchInput):
        return service.fetch(bid,body.url)

    @app.post("/api/v1/businesses/{bid}/local-projects")
    def local_project(bid:str):
        retired()

    @app.post("/api/v1/specifications/{sid}/code-analysis")
    def code_analysis(sid:str):
        retired()

    @app.get("/api/v1/specifications/{sid}/evidence/{eid}")
    def evidence(sid:str,eid:str):
        entry=service.spec(sid)["inventory"].get("evidence",{}).get(eid)
        if not entry: raise AppError("evidence_not_found","Evidence is not part of this source snapshot",404)
        return entry

    @app.get("/api/v1/specifications/{sid}/inventory")
    def inventory(sid:str):
        return service.spec(sid)

    @app.get("/api/v1/specifications/{sid}/areas")
    def areas(sid:str):
        return service.areas(sid)

    @app.post("/api/v1/specifications/{sid}/areas")
    def select_areas(sid:str,body:AreaSelectionSubmission):
        return service.select_areas(sid,body)

    @app.post("/api/v1/specifications/{sid}/areas/organize")
    def organize_areas(sid:str):
        return service.organize_areas(sid)

    @app.get("/api/v1/specifications/{sid}/generation-batch")
    def generation_batch(sid:str):
        return service.next_generation_batch(sid)

    @app.post("/api/v1/inventories/{sid}/proposal-runs")
    def generate(sid:str,body:GenerationInput|None=None):
        return service.generate(sid,body.operation_ids if body else None)

    @app.get("/api/v1/proposal-runs/{rid}")
    def run(rid:str):
        result=store.one("SELECT * FROM runs WHERE id=?",(rid,))
        for key in ("result","error"):
            result[key]=json.loads(result[key]) if result[key] else None
        result["attempts"]=store.all("SELECT * FROM attempts WHERE run_id=? ORDER BY id",(rid,))
        result["diagnostics"]=store.all("SELECT * FROM diagnostics WHERE run_id=? ORDER BY id",(rid,))
        for diagnostic in result["diagnostics"]:
            diagnostic["payload"]=json.loads(diagnostic["payload"])
        return result

    @app.get("/api/v1/businesses/{bid}/proposals")
    def proposals(bid:str):
        return [service.view(p["id"]) for p in store.all("SELECT id FROM proposals WHERE business_id=?",(bid,))]

    @app.get("/api/v1/proposals/{pid}")
    def proposal(pid:str,version:int|None=None):
        return service.view(pid,version)

    @app.post("/api/v1/proposals/{pid}/versions/{version}/answers")
    def answers(pid:str,version:int,body:AnswerSubmission):
        return service.answers(pid,version,body)

    @app.post("/api/v1/proposals/{pid}/versions/{version}/reconcile")
    def reconcile(pid:str,version:int,body:ReviewSubmission):
        return service.reconcile(pid,version,body.expected_revision)

    @app.post("/api/v1/proposals/{pid}/versions/{version}/decisions")
    def decision(pid:str,version:int,body:DecisionSubmission):
        return service.decide(pid,version,body)

    @app.post("/api/v1/proposals/{pid}/versions/{version}/requirements/{rid}/confirm")
    def confirm_requirement(pid:str,version:int,rid:str,body:ReviewSubmission):
        return service.confirm_requirement(pid,version,rid,body)

    @app.post("/api/v1/proposals/{pid}/versions/{version}/questions/{qid}/supersede")
    def supersede_question(pid:str,version:int,qid:str,body:SupersedeSubmission):
        return service.supersede_question(pid,version,qid,body)

    @app.post("/api/v1/proposals/{pid}/revisions")
    def revision(pid:str,body:RevisionSubmission):
        return service.revise(pid,body)

    @app.post("/api/v1/proposals/{pid}/enforcement")
    def submit_enforcement(pid:str,body:EnforcementSubmission):
        return service.submit_enforcement(pid,body)

    @app.get("/api/v1/enforcement/{eid}")
    def enforcement(eid:str):
        return service.enforcement(eid)

    @app.post("/api/v1/enforcement/{eid}/review")
    def review_enforcement(eid:str,body:EnforcementReviewSubmission):
        return service.review_enforcement(eid,body)

    @app.post("/api/v1/proposals/{pid}/artifacts")
    def build_artifact(pid:str,body:ArtifactBuildSubmission):
        return service.build_artifact(pid,body)

    @app.get("/api/v1/artifacts/{aid}")
    def artifact(aid:str):
        return service.artifact(aid)

    @app.post("/api/v1/artifacts/{aid}/sandbox-runs")
    def sandbox_run(aid:str,body:SandboxRunSubmission):
        return service.run_sandbox(aid,body)

    @app.post("/api/v1/artifacts/{aid}/sandbox-tests")
    def sandbox_test(aid:str,body:SandboxTestSubmission):
        return service.run_sandbox_test(aid,body)

    @app.post("/api/v1/artifacts/{aid}/publications")
    def publish(aid:str,body:PublicationSubmission):
        return service.publish(aid,body)

    @app.get("/api/v1/publications")
    def publications(business_id:str|None=None):
        return service.publications(business_id)

    @app.get("/api/v1/publications/{pub_id}")
    def publication(pub_id:str):
        return service.publication(pub_id)

    @app.post("/api/v1/publications/{pub_id}/disable")
    def disable_publication(pub_id:str,body:PublicationDisableSubmission):
        return service.disable_publication(pub_id,body)

    @app.post("/api/v1/sandbox-tests/{tid}/repairs")
    def repair(tid:str,body:RepairSubmission):
        return service.repair(tid,body)

    @app.post("/api/v1/businesses/{bid}/tool-requests")
    def request_tool(bid:str,body:ToolRequestSubmission):
        return service.request_tool(bid,body)

    @app.get("/api/v1/businesses/{bid}/tool-requests")
    def tool_requests(bid:str):
        return service.tool_requests(bid)

    @app.get("/api/v1/tool-requests/{rid}")
    def tool_request(rid:str):
        return service.tool_request(rid)

    @app.post("/api/v1/tool-requests/{rid}/clarifications")
    def clarify_request(rid:str,body:ClarificationSubmission):
        return service.clarify_request(rid,body)

    @app.post("/api/v1/tool-requests/{rid}/next-batch")
    def request_next_batch(rid:str):
        return service.request_next_batch(rid)

    @app.post("/api/v1/businesses/{bid}/suggestion-runs")
    def suggest(bid:str,body:SuggestionRunSubmission|None=None):
        return service.suggest(bid,body or SuggestionRunSubmission())

    @app.get("/api/v1/businesses/{bid}/suggestions")
    def suggestions(bid:str):
        return service.suggestions(bid)

    @app.post("/api/v1/suggestions/{sid}/decision")
    def decide_suggestion(sid:str,body:SuggestionDecisionSubmission):
        return service.decide_suggestion(sid,body)

    def connector_choices(business_id):
        return {cid:sorted(c.get("identities") or {}) for cid,c in load_connectors(settings).items() if c.get("business_id")==business_id}

    @app.get("/")
    def home(request:Request):
        return page(request,screen="home",businesses=businesses())

    @app.get("/businesses/{bid}")
    def business_page(request:Request,bid:str,saved:str=""):
        return page(request,screen="business",business=store.one("SELECT * FROM businesses WHERE id=?",(bid,)),specs=store.all("SELECT id,filename,created_at FROM specifications WHERE business_id=? ORDER BY created_at",(bid,)),proposals=proposals(bid),runs=store.all("SELECT * FROM runs WHERE business_id=? ORDER BY created_at DESC",(bid,)),
                    tool_requests=service.tool_requests(bid),suggested=service.suggestions(bid),request_key=secrets.token_urlsafe(24),suggestion_count=settings.suggestion_count,
                    tools={t["proposal_id"]:t for t in service.existing_tools(bid)},
                    fetch_hosts=[h.strip() for h in settings.openapi_fetch_hosts.split(",") if h.strip()],
                    active_areas=active_areas(bid),saved=saved)

    def active_areas(bid):
        try:
            return service.areas(service.request_spec(bid)["id"])
        except AppError:
            return None

    def business_of(bid):
        return store.one("SELECT * FROM businesses WHERE id=?",(bid,))

    @app.get("/specifications/{sid}")
    def spec_page(request:Request,sid:str,batch:str="",saved:str=""):
        spec=service.spec(sid)
        openapi=spec["inventory"].get("source_kind")!="code"
        next_batch=service.next_generation_batch(sid) if openapi and spec["inventory"]["proposal_generation_ready"] else None
        return page(request,screen="inventory",spec=spec,business=business_of(spec["business_id"]),areas=service.areas(sid) if openapi else None,
                    ops={o["id"]:o for o in spec["inventory"]["operations"]},reasons={o["id"]:capabilities.reason(o) for o in spec["inventory"]["operations"]},used=service.generation_used(sid),
                    next_batch=next_batch,preselect=set(next_batch["operation_ids"]) if next_batch and batch=="next" else set(),saved=saved)

    @app.get("/proposals/{pid}")
    def proposal_page(request:Request,pid:str,version:int|None=None):
        view=service.view(pid,version)
        previous=service.view(pid,view["version"]-1)["content"] if view["version"]>1 else None
        operations={op["id"]:op for op in service.spec(view["spec_id"])["inventory"]["operations"]}
        artifacts=store.all("SELECT id,version,sha256,created_at FROM artifacts WHERE proposal_id=? ORDER BY created_at",(pid,))
        return page(request,screen="proposal",p=view,previous=previous,operations=operations,decision_key=secrets.token_urlsafe(24),artifacts=artifacts,connectors=connector_choices(view["business_id"]),business=business_of(view["business_id"]))

    @app.get("/artifacts/{aid}")
    def artifact_page(request:Request,aid:str):
        _,problems,_=service.publication_problems(aid)
        a=service.artifact(aid)
        bid=a["content"]["proposal"]["business_id"]
        return page(request,screen="artifact",a=a,connectors=connector_choices(bid),publications=service.publications(artifact_id=aid),publication_problems=problems,business=business_of(bid))

    @app.get("/runs/{rid}")
    def run_page(request:Request,rid:str):
        r=run(rid)
        return page(request,screen="run",run=r,business=business_of(r["business_id"]))

    # The route itself is synchronous: slow provider calls do not block the ASGI loop.
    @app.post("/actions/{action}")
    async def action(request:Request,action:str):
        from starlette.concurrency import run_in_threadpool
        form=await request.form()
        if not secrets.compare_digest(str(form.get("csrf","")),request.session.get("csrf","missing")):
            raise AppError("csrf_rejected","Reload the page and try again",403)
        def process():
            if action=="business":
                b=service.business(str(form.get("name","")),str(form.get("description","")))
                return "/businesses/"+b["id"]
            if action=="upload":
                f=form["file"]
                s=service.upload(str(form["business_id"]),f.filename,f.file.read(settings.max_upload_bytes+1))
                return "/specifications/"+s["id"]
            if action=="fetch":
                s=service.fetch(str(form["business_id"]),str(form.get("url","")))
                return "/specifications/"+s["id"]
            if action in ("local_project","code_analysis"):
                retired()
            if action=="generate":
                selected=[str(v) for v in form.getlist("operation_ids")]
                if not selected: raise AppError("operation_scope","Select at least one operation")
                r=service.generate(str(form["spec_id"]),selected)
                return "/runs/"+r["run_id"]
            if action=="review_enforcement":
                e=service.review_enforcement(str(form["enforcement_id"]),EnforcementReviewSubmission(note=str(form.get("note",""))))
                return "/proposals/"+e["proposal_id"]
            if action=="build_artifact":
                a=service.build_artifact(str(form["proposal_id"]),ArtifactBuildSubmission(connector_id=str(form.get("connector_id",""))))
                return "/artifacts/"+a["id"]
            if action=="publish":
                service.publish(str(form["artifact_id"]),PublicationSubmission(note=str(form.get("note",""))))
                return "/artifacts/"+str(form["artifact_id"])
            if action=="request_tool":
                bid=str(form["business_id"])
                service.request_tool(bid,ToolRequestSubmission(goal=str(form.get("goal","")),examples=str(form.get("examples","")),idempotency_key=str(form.get("request_key",""))))
                return "/businesses/"+bid
            if action=="clarify_request":
                r=service.clarify_request(str(form["request_id"]),ClarificationSubmission(text=str(form.get("text",""))))
                return "/businesses/"+r["business_id"]
            if action=="suggest":
                bid=str(form["business_id"])
                service.suggest(bid,SuggestionRunSubmission(count=int(form.get("count") or settings.suggestion_count),next_batch=form.get("next_batch")=="1"))
                return "/businesses/"+bid
            if action=="request_next_batch":
                r=service.request_next_batch(str(form["request_id"]))
                return "/businesses/"+r["business_id"]
            if action=="areas_organize":
                sid=str(form["spec_id"])
                service.organize_areas(sid)
                return "/specifications/"+sid
            if action=="areas_select":
                sid=str(form["spec_id"])
                service.select_areas(sid,AreaSelectionSubmission(area_ids=[str(v) for v in form.getlist("area_ids")]))
                bid=service.spec(sid)["business_id"]
                if service.request_spec(bid)["id"]==sid:
                    return "/businesses/"+bid+"?saved=areas#get-tools"
                return "/specifications/"+sid+"?saved=areas#areas"
            if action=="decide_suggestion":
                r=service.decide_suggestion(str(form["suggestion_id"]),SuggestionDecisionSubmission(action=str(form.get("decision","")),note=str(form.get("note","")),clarification=str(form.get("clarification","")),
                                                                                             title=str(form.get("title","")) or None,purpose=str(form.get("purpose","")) or None))
                return "/businesses/"+r["suggestion"]["business_id"]
            if action=="disable_publication":
                p=service.disable_publication(str(form["publication_id"]),PublicationDisableSubmission(note=str(form.get("note",""))))
                return "/artifacts/"+p["artifact_id"]
            if action in ("submit_enforcement","sandbox_run"):
                try:
                    data=json.loads(str(form.get("json","") or "{}"))
                except ValueError:
                    raise AppError("invalid_input","Enter valid JSON")
                if action=="submit_enforcement":
                    service.submit_enforcement(str(form["proposal_id"]),EnforcementSubmission(connector_id=str(form.get("connector_id","")),enforcement=data))
                    return "/proposals/"+str(form["proposal_id"])
                aid=str(form["artifact_id"])
                try:
                    service.run_sandbox(aid,SandboxRunSubmission(identity=str(form.get("identity","")),arguments=data))
                except AppError as exc:
                    if "execution_id" not in exc.details: raise
                return "/artifacts/"+aid
            pid=str(form["proposal_id"])
            version=int(form["version"])
            rev=int(form["revision"])
            if action=="answers":
                service.answers(pid,version,AnswerSubmission(expected_revision=rev,answers={k[2:]:str(v) for k,v in form.items() if k.startswith("q:")}))
            elif action=="reconcile":
                service.reconcile(pid,version,rev)
            elif action=="decision":
                service.decide(pid,version,DecisionSubmission(expected_revision=rev,action=str(form["decision"]),reason=str(form.get("reason","")),idempotency_key=str(form["idempotency_key"])))
            elif action=="confirm_requirement":
                service.confirm_requirement(pid,version,str(form["requirement_id"]),ReviewSubmission(expected_revision=rev))
            elif action=="supersede_question":
                service.supersede_question(pid,version,str(form["question_id"]),SupersedeSubmission(expected_revision=rev,reason=str(form.get("reason",""))))
            elif action=="revise":
                r=service.revise(pid,RevisionSubmission(expected_revision=rev,instruction=str(form.get("instruction",""))))
                if r.get("capability_gaps"):
                    return "/runs/"+r["run_id"]
            else:
                raise AppError("unknown_action","Unknown action",404)
            return "/proposals/"+pid
        if action not in LIVE_ACTIONS and not (action=="decide_suggestion" and form.get("decision")=="accept"):
            return RedirectResponse(await run_in_threadpool(process),status_code=303)
        # Ollama answers one request at a time: a second AI action would only queue, so show the running one instead.
        running=running_job()
        if running:
            return RedirectResponse("/live/"+running["id"]+"?busy=1",status_code=303)
        # AI actions run in the background; anything still running after a moment moves to the live page.
        now=time.time()
        for old in [k for k,j in jobs.items() if now-j["started"]>3600]:
            jobs.pop(old,None)
        referer=urlparse(request.headers.get("referer",""))
        job=dict(id=secrets.token_urlsafe(16),action=action,status="running",steps=[],started=now,url=None,error=None,
                 back=(referer.path+("?"+referer.query if referer.query else "")) if referer.path.startswith("/") else "/")
        jobs[job["id"]]=job
        def work():
            LIVE.job=job
            try:
                job["url"]=process()
                job["status"]="done"
            except AppError as exc:
                job["error"]=exc
            except ValidationError as exc:
                job["error"]=AppError("invalid_input","Submitted form is invalid",details={"errors":str(exc)[:500]})
            except Exception:
                job["error"]=AppError("internal_error","Unexpected processing failure",500)
            finally:
                LIVE.job=None
                if job["error"]:
                    job["status"]="failed"
                else:
                    for s in job["steps"]:
                        s["thinking"]=""
        worker=threading.Thread(target=work,daemon=True)
        worker.start()
        await run_in_threadpool(worker.join,LIVE_WAIT_SECONDS)
        if job["status"]=="done":
            return RedirectResponse(job["url"],status_code=303)
        if job["status"]=="failed":
            raise job["error"]
        return RedirectResponse("/live/"+job["id"],status_code=303)

    def live_view(jid):
        job=jobs.get(jid)
        if not job:
            raise AppError("not_found","This progress page has expired; check the business page for the result",404)
        now=time.time()
        return dict(id=job["id"],action=job["action"],status=job["status"],url=job["url"],back=job["back"],elapsed=round(now-job["started"]),cancelled=bool(job.get("cancelled")),
                    error=dict(code=job["error"].code,message=job["error"].message,run_id=job["error"].details.get("run_id"),
                               errors=check_errors(job["error"].details["errors"],(settings.openrouter_api_key,settings.session_secret)) if isinstance(job["error"].details.get("errors"),list) else []) if job["error"] else None,
                    steps=[dict(label=STEP_LABELS.get(s["kind"],s["kind"])+(" (fixing what the check found)" if s.get("fixing") else "")+(" (again, without thinking)" if s.get("retry") else ""),thinking_on=s["thinking_on"],retry=bool(s.get("retry")),
                                thinking=s["thinking"],answer_tokens=s["answer_tokens"],seconds=round((s["finished"] or now)-s["started"]),done=s["finished"] is not None) for s in job["steps"]])

    @app.get("/api/v1/live/{jid}")
    def live_api(jid:str):
        return live_view(jid)

    @app.get("/live/{jid}")
    def live_page(request:Request,jid:str,busy:str=""):
        return page(request,screen="live",job=live_view(jid),title=ACTION_LABELS.get(jobs[jid]["action"],"Working"),busy=busy=="1")

    @app.post("/live/{jid}/cancel")
    async def live_cancel(request:Request,jid:str):
        form=await request.form()
        if not secrets.compare_digest(str(form.get("csrf","")),request.session.get("csrf","missing")):
            raise AppError("csrf_rejected","Reload the page and try again",403)
        job=jobs.get(jid)
        if job and job["status"]=="running":
            job["cancelled"]=True
        return RedirectResponse("/live/"+jid,status_code=303)

    return app
