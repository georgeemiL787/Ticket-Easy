"""Proposal run records and live progress of background AI jobs."""
from fastapi import APIRouter
from ..jobs import live_view
from . import queries
from .deps import JobsDep, SettingsDep, StoreDep

router=APIRouter(prefix="/api/v1")


@router.get("/proposal-runs/{rid}")
def run(rid:str,store:StoreDep):
    return queries.run(store,rid)


@router.get("/live/{jid}")
def live_api(jid:str,jobs:JobsDep,settings:SettingsDep):
    return live_view(jobs,jid,settings)
