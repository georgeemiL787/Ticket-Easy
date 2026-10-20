"""Read access to the decision log: why the agent did what it did."""

from typing import Annotated

from fastapi import APIRouter, Path, Request

from team_b.api.chat import CONVERSATION, TENANT, checked_tenant
from team_b.api.errors import not_found
from team_b.domain.trace import DecisionTrace

router = APIRouter(tags=["traces"])


@router.get("/v1/traces/{trace_id}", response_model=DecisionTrace)
async def get_trace(
    trace_id: Annotated[str, Path(pattern=r"^[A-Za-z0-9_.:-]{1,128}$")], tenant_id: TENANT, request: Request
) -> DecisionTrace:
    """One decision trace. The tenant id is required: a business can only read its own traces."""
    built = checked_tenant(request, tenant_id)
    trace = await built.traces.get(tenant_id, trace_id)
    if trace is None:
        raise not_found("trace not found")
    return trace


@router.get("/v1/conversations/{conversation_id}/traces", response_model=list[DecisionTrace])
async def get_conversation_traces(
    conversation_id: CONVERSATION, tenant_id: TENANT, request: Request
) -> list[DecisionTrace]:
    """Every trace of a conversation, oldest first."""
    built = checked_tenant(request, tenant_id)
    traces = await built.traces.for_conversation(tenant_id, conversation_id)
    if not traces:
        raise not_found("conversation not found")
    return traces
