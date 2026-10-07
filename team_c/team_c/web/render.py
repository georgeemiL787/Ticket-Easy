"""Server-rendered pages: shared template context (CSRF token, reviewer, model, running AI job)."""
import secrets
from pathlib import Path
from fastapi.templating import Jinja2Templates
from .. import jobs
from .forms import field_kind

TEMPLATES=Path(__file__).parent/"templates"


def templates():
    result=Jinja2Templates(directory=str(TEMPLATES))
    result.env.globals["field_kind"]=field_kind
    return result


def page(request,**context):
    if "csrf" not in request.session:
        request.session["csrf"]=secrets.token_urlsafe(32)
    state=request.app.state
    settings=state.settings
    return state.templates.TemplateResponse(request=request,name=context["screen"]+".html",context=dict(csrf=request.session["csrf"],reviewer=settings.dev_reviewer_id,provider=settings.llm_primary,model=getattr(settings,settings.llm_primary+"_model",""),fallback=settings.llm_fallback,ai_job=jobs.banner(state.jobs),**context))
