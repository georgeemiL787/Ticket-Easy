"""Route dependencies: the objects create_app wires onto app.state."""
from typing import Annotated
from fastapi import Depends, Request
from ..config import AppError, Settings
from ..service import Service
from ..persistence.db import Store


def get_service(request:Request):
    return request.app.state.service


def get_store(request:Request):
    return request.app.state.store


def get_settings(request:Request):
    return request.app.state.settings


def get_jobs(request:Request):
    return request.app.state.jobs


def require_admin(request:Request):
    """Production actions need the operator's service token (TOOLS_ADMIN_TOKEN, header x-team-c-admin-token). Team B's signed-in
    admins reach them through Team B, which holds the token; the tools service's own pages have no sign-in and cannot do them."""
    import secrets
    expected=request.app.state.settings.admin_token
    if not expected:
        raise AppError("admin_token_required","Production actions are disabled until the operator sets TOOLS_ADMIN_TOKEN",403)
    if not secrets.compare_digest(request.headers.get("x-team-c-admin-token",""),expected):
        raise AppError("admin_token_invalid","This production action needs the operator's admin token",403)


ServiceDep=Annotated[Service,Depends(get_service)]
AdminDep=Depends(require_admin)
StoreDep=Annotated[Store,Depends(get_store)]
SettingsDep=Annotated[Settings,Depends(get_settings)]
JobsDep=Annotated[dict,Depends(get_jobs)]
