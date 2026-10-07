"""Background AI jobs started from browser actions: one runs at a time and slow ones continue on the live page.

The registry is a plain dict (app.state.jobs) keyed by job id; jobs expire an hour after they start.
"""
import secrets
import threading
import time
from urllib.parse import urlparse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from .config import AppError
from .diagnostics import check_errors
from .progress import LIVE

LIVE_ACTIONS={"generate","request_tool","clarify_request","request_next_batch","suggest","areas_organize","reconcile","revise"}
LIVE_WAIT_SECONDS=2
EXPIRY_SECONDS=3600
ACTION_LABELS={"generate":"Generating a tool proposal","request_tool":"Working on your tool request","clarify_request":"Working on your tool request",
               "request_next_batch":"Looking in the next batch","suggest":"Looking for useful tools","areas_organize":"Organizing business areas",
               "reconcile":"Checking your answers","revise":"Revising the proposal","decide_suggestion":"Turning the suggestion into a tool request"}
STEP_LABELS={"request_triage":"Checking whether your API can do this","generation":"Writing the tool proposal","suggestion":"Thinking of useful tools",
             "area_naming":"Naming and ranking business areas","area_assignment":"Placing API groups in areas","reconciliation":"Checking your answers",
             "revision":"Revising the proposal","repair":"Repairing the proposal"}


def is_live(action,form):
    return action in LIVE_ACTIONS or (action=="decide_suggestion" and form.get("decision")=="accept")


def running(jobs):
    return next((j for j in jobs.values() if j["status"]=="running"),None)


def title(job):
    return ACTION_LABELS.get(job["action"],"Working")


def banner(jobs):
    job=running(jobs)
    return dict(id=job["id"],title=title(job),minutes=int((time.time()-job["started"])//60)) if job else None


async def start(jobs,action,referer,process):
    """Run process() on a worker thread and return where to redirect: its result if it ends quickly, otherwise the live page."""
    # Ollama answers one request at a time: a second AI action would only queue, so show the running one instead.
    current=running(jobs)
    if current:
        return "/live/"+current["id"]+"?busy=1"
    now=time.time()
    for old in [k for k,j in jobs.items() if now-j["started"]>EXPIRY_SECONDS]:
        jobs.pop(old,None)
    referer=urlparse(referer)
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
        return job["url"]
    if job["status"]=="failed":
        raise job["error"]
    return "/live/"+job["id"]


def cancel(jobs,jid):
    job=jobs.get(jid)
    if job and job["status"]=="running":
        job["cancelled"]=True


def live_view(jobs,jid,settings):
    job=jobs.get(jid)
    if not job:
        raise AppError("not_found","This progress page has expired; check the business page for the result",404)
    now=time.time()
    return dict(id=job["id"],action=job["action"],status=job["status"],url=job["url"],back=job["back"],elapsed=round(now-job["started"]),cancelled=bool(job.get("cancelled")),
                error=dict(code=job["error"].code,message=job["error"].message,run_id=job["error"].details.get("run_id"),
                           errors=check_errors(job["error"].details["errors"],settings.model_secrets) if isinstance(job["error"].details.get("errors"),list) else []) if job["error"] else None,
                steps=[dict(label=STEP_LABELS.get(s["kind"],s["kind"])+(" (fixing what the check found)" if s.get("fixing") else "")+(" (again, without thinking)" if s.get("retry") else ""),thinking_on=s["thinking_on"],retry=bool(s.get("retry")),
                            thinking=s["thinking"],answer_tokens=s["answer_tokens"],seconds=round((s["finished"] or now)-s["started"]),done=s["finished"] is not None) for s in job["steps"]])
