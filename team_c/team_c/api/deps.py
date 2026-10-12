"""Route dependencies: the objects create_app wires onto app.state."""
from typing import Annotated
from fastapi import Depends, Request
from ..config import Settings
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


ServiceDep=Annotated[Service,Depends(get_service)]
StoreDep=Annotated[Store,Depends(get_store)]
SettingsDep=Annotated[Settings,Depends(get_settings)]
JobsDep=Annotated[dict,Depends(get_jobs)]
