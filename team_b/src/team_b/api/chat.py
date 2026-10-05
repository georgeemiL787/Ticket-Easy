"""The chat API: send a message and get the agent's reply, read the conversation, and follow human replies live."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from team_b.api.errors import not_found, rate_limited, tenant_not_found
from team_b.api.ratelimit import RateLimiter
from team_b.brain.composer import default_composer
from team_b.brain.redaction import redact
from team_b.container import Container
from team_b.domain.reply import AgentReply
from team_b.domain.session import Message, SessionState
from team_b.domain.understanding import Locale

KEEPALIVE_S = 15.0  # an SSE comment is sent this often so proxies keep the connection open
MAX_REQUEST_ID = 128
TENANT = Annotated[str, Query(pattern=r"^[a-z0-9_]{1,64}$", description="the business (tenant) id")]
CONVERSATION = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.:-]{1,128}$", description="the conversation id")]

router = APIRouter(tags=["chat"])


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    text: str = Field(min_length=1, max_length=2000)
    channel: str = Field(default="web", pattern=r"^[a-z_]{1,32}$")

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


def container_of(request: Request) -> Container:
    built: Container = request.app.state.container
    return built


def checked_tenant(request: Request, tenant_id: str) -> Container:
    built = container_of(request)
    if tenant_id not in built.tenants:
        raise tenant_not_found(tenant_id)
    return built


async def load_session(request: Request, tenant_id: str, conversation_id: str) -> SessionState:
    built = checked_tenant(request, tenant_id)
    session = await built.sessions.load(tenant_id, conversation_id)
    if session is None:
        raise not_found("conversation not found")
    return session


@router.post("/v1/conversations/{conversation_id}/messages", response_model=AgentReply)
async def post_message(conversation_id: CONVERSATION, body: MessageIn, request: Request) -> AgentReply:
    """One customer message in, the agent's reply out. The X-Request-ID header (or a new id) tags the whole turn."""
    built = checked_tenant(request, body.tenant_id)
    limiter: RateLimiter = request.app.state.rate_limiter
    if (wait := limiter.hit((body.tenant_id, conversation_id))) is not None:
        raise rate_limited(wait)
    assert built.orchestrator is not None
    request_id = getattr(request.state, "request_id", None)
    return await built.orchestrator.handle_turn(
        body.tenant_id,
        conversation_id,
        body.text,
        request_id=request_id[:MAX_REQUEST_ID] if isinstance(request_id, str) else None,
        channel=body.channel,
    )


def _debug_view(session: SessionState) -> dict[str, Any]:
    """The session for debugging, with phones, emails and card numbers hidden."""
    view = session.model_dump(mode="json")
    view["slots"] = {k: redact(v) for k, v in session.slots.items()}
    for key in ("history", "outbox"):
        view[key] = [{**m, "text": redact(m["text"])} for m in view[key]]
    view["history_summary"] = redact(session.history_summary)
    return view


@router.get("/v1/conversations/{conversation_id}")
async def get_conversation(conversation_id: CONVERSATION, tenant_id: TENANT, request: Request) -> dict[str, Any]:
    """The stored session of a conversation (for debugging)."""
    return _debug_view(await load_session(request, tenant_id, conversation_id))


@router.get("/v1/conversations/{conversation_id}/outbox", response_model=list[Message])
async def get_outbox(conversation_id: CONVERSATION, tenant_id: TENANT, request: Request) -> list[Message]:
    """Replies that were written for the customer by a person and are waiting to be shown, oldest first."""
    return (await load_session(request, tenant_id, conversation_id)).outbox


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def event_stream(
    request: Request, tenant_id: str, conversation_id: str, max_events: int | None, keepalive_s: float = KEEPALIVE_S
) -> AsyncIterator[str]:
    """Server-Sent Events: a "message" event for every new human reply, comments in between to keep the line open."""
    hub = request.app.state.events
    queue = hub.subscribe(tenant_id, conversation_id)
    sent = 0
    try:
        yield ": connected\n\n"
        while max_events is None or sent < max_events:
            try:
                message = await asyncio.wait_for(queue.get(), timeout=keepalive_s)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            yield sse("message", message)
            sent += 1
    finally:
        hub.unsubscribe(tenant_id, conversation_id, queue)


@router.get("/v1/conversations/{conversation_id}/events")
async def get_events(
    conversation_id: CONVERSATION,
    tenant_id: TENANT,
    request: Request,
    max_events: Annotated[
        int | None, Query(ge=1, le=1000, description="close after this many events (for tools and tests)")
    ] = None,
) -> StreamingResponse:
    """Live stream (Server-Sent Events) of replies a person writes to this conversation."""
    checked_tenant(request, tenant_id)
    return StreamingResponse(
        event_stream(request, tenant_id, conversation_id, max_events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/v1/tenants/{tenant_id}/welcome")
async def get_welcome(
    tenant_id: Annotated[str, Path(pattern=r"^[a-z0-9_]{1,64}$")], request: Request
) -> dict[str, str]:
    """The greeting the chat page shows when it opens, in the business default language. No conversation is created."""
    tenant = checked_tenant(request, tenant_id).tenants.get(tenant_id)
    locale = Locale(tenant.default_locale.value)
    return {
        "display_name": tenant.display_name,
        "locale": locale.value,
        "text": default_composer().t(locale, "greeting"),
    }
