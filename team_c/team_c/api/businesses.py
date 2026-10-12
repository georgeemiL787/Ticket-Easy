"""Businesses, specifications, inventories and business areas."""
from fastapi import APIRouter, UploadFile, File
from pydantic import BaseModel, ConfigDict
from ..config import AppError
from ..models import AreaSelectionSubmission
from . import queries
from .deps import ServiceDep, SettingsDep, StoreDep

router=APIRouter(prefix="/api/v1")


class BusinessInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    name: str
    description: str


class FetchInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    url: str


def retired():
    raise AppError("code_discovery_retired","Local-code discovery is retired; existing code inventories remain read-only. Upload or fetch an OpenAPI description instead",410)


@router.post("/businesses")
def business(body:BusinessInput,service:ServiceDep):
    return service.business(body.name,body.description)


@router.get("/businesses")
def businesses(store:StoreDep):
    return queries.businesses(store)


@router.post("/businesses/{bid}/specifications")
def upload(bid:str,service:ServiceDep,settings:SettingsDep,file:UploadFile=File(...)):
    return service.upload(bid,file.filename or "upload",file.file.read(settings.max_upload_bytes+1))


@router.post("/businesses/{bid}/specifications/fetch")
def fetch(bid:str,body:FetchInput,service:ServiceDep):
    return service.fetch(bid,body.url)


@router.post("/businesses/{bid}/local-projects")
def local_project(bid:str):
    retired()


@router.post("/specifications/{sid}/code-analysis")
def code_analysis(sid:str):
    retired()


@router.get("/specifications/{sid}/evidence/{eid}")
def evidence(sid:str,eid:str,service:ServiceDep):
    entry=service.spec(sid)["inventory"].get("evidence",{}).get(eid)
    if not entry: raise AppError("evidence_not_found","Evidence is not part of this source snapshot",404)
    return entry


@router.get("/specifications/{sid}/inventory")
def inventory(sid:str,service:ServiceDep):
    return service.spec(sid)


@router.get("/specifications/{sid}/areas")
def areas(sid:str,service:ServiceDep):
    return service.areas(sid)


@router.post("/specifications/{sid}/areas")
def select_areas(sid:str,body:AreaSelectionSubmission,service:ServiceDep):
    return service.select_areas(sid,body)


@router.post("/specifications/{sid}/areas/organize")
def organize_areas(sid:str,service:ServiceDep):
    return service.organize_areas(sid)


@router.get("/specifications/{sid}/generation-batch")
def generation_batch(sid:str,service:ServiceDep):
    return service.next_generation_batch(sid)
