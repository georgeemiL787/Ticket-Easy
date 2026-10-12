"""Tool requests, clarifications and tool suggestions."""
from fastapi import APIRouter
from ..models import ClarificationSubmission, SuggestionDecisionSubmission, SuggestionRunSubmission, ToolRequestSubmission
from .deps import ServiceDep

router=APIRouter(prefix="/api/v1")


@router.post("/businesses/{bid}/tool-requests")
def request_tool(bid:str,body:ToolRequestSubmission,service:ServiceDep):
    return service.request_tool(bid,body)


@router.get("/businesses/{bid}/tool-requests")
def tool_requests(bid:str,service:ServiceDep):
    return service.tool_requests(bid)


@router.get("/tool-requests/{rid}")
def tool_request(rid:str,service:ServiceDep):
    return service.tool_request(rid)


@router.post("/tool-requests/{rid}/clarifications")
def clarify_request(rid:str,body:ClarificationSubmission,service:ServiceDep):
    return service.clarify_request(rid,body)


@router.post("/tool-requests/{rid}/next-batch")
def request_next_batch(rid:str,service:ServiceDep):
    return service.request_next_batch(rid)


@router.post("/businesses/{bid}/suggestion-runs")
def suggest(bid:str,service:ServiceDep,body:SuggestionRunSubmission|None=None):
    return service.suggest(bid,body or SuggestionRunSubmission())


@router.get("/businesses/{bid}/suggestions")
def suggestions(bid:str,service:ServiceDep):
    return service.suggestions(bid)


@router.post("/suggestions/{sid}/decision")
def decide_suggestion(sid:str,body:SuggestionDecisionSubmission,service:ServiceDep):
    return service.decide_suggestion(sid,body)
