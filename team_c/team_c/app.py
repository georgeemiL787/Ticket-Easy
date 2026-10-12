"""Application construction: wires Store, Providers and Service onto app.state, middleware, error handling and routers."""
import secrets
from urllib.parse import urlparse
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import ValidationError
from .config import Settings, AppError
from .llm.router import Providers
from .persistence.db import Store
from .service import Service
from . import api
from .web import actions, pages
from .web.render import page, templates


def create_app(settings=None, provider_factory=None):
    settings=settings or Settings()
    store=Store(settings.database_path)
    providers=provider_factory(settings,store) if provider_factory else Providers(settings,store)
    service=Service(settings,store,providers)
    app=FastAPI(title="Ticket-Easy Team C",version="0.1.0",description="Local development review. Approval means approved to build, never active or published.")
    app.state.settings=settings
    app.state.service=service
    app.state.store=store
    app.state.jobs={}
    app.state.templates=templates()
    app.add_middleware(SessionMiddleware,secret_key=settings.session_secret or secrets.token_hex(32),same_site="strict")
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=["127.0.0.1","localhost","testserver"])

    @app.middleware("http")
    async def same_origin(request,call_next):
        if request.method in {"POST","PUT","PATCH","DELETE"}:
            origin=request.headers.get("origin")
            if origin and urlparse(origin).netloc!=request.headers.get("host"):
                return JSONResponse({"code":"origin_rejected","message":"Cross-origin mutations are forbidden"},status_code=403)
        return await call_next(request)

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

    app.include_router(api.router)
    app.include_router(pages.router)
    app.include_router(actions.router)
    return app
