"""The chat API: messages, validation, request ids, rate limit, session, outbox, live events, traces, the page."""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from team_b.api.app import create_app
from team_b.api.chat import event_stream
from team_b.api.ratelimit import RateLimiter
from team_b.brain.composer import render
from team_b.container import Container, build_container
from team_b.domain.understanding import Locale
from tests.support import make_settings

T = "shop_001"
URL = "/v1/conversations/{}/messages"


def body(text: str = "Hello", **over: object) -> dict[str, object]:
    return {"tenant_id": T, "text": text, **over}


# ---- POST messages ----


async def test_a_greeting_gets_the_agent_reply(chat: httpx.AsyncClient) -> None:
    response = await chat.post(URL.format("c1"), json=body())
    assert response.status_code == 200
    reply = response.json()
    assert reply["decision"] == "answer" and reply["text"] == render("greeting", Locale.EN)
    assert (reply["tenant_id"], reply["conversation_id"], reply["locale"], reply["awaiting"]) == (T, "c1", "en", None)
    assert reply["trace_id"] and reply["request_id"] and reply["citations"] == []


async def test_the_conversation_continues_across_messages(chat: httpx.AsyncClient) -> None:
    await chat.post(URL.format("c1"), json=body("Where is my order?"))
    second = (await chat.post(URL.format("c1"), json=body("NS-20877"))).json()
    assert second["awaiting"] == "slot:phone" and second["decision"] == "verify_identity"


async def test_a_request_for_a_person_returns_the_case_id(chat: httpx.AsyncClient) -> None:
    reply = (await chat.post(URL.format("c1"), json=body("I want to talk to a human"))).json()
    assert reply["decision"] == "handoff" and reply["handoff_case_id"] and reply["awaiting"] == "human"


async def test_the_channel_is_kept_on_the_session(chat: httpx.AsyncClient) -> None:
    await chat.post(URL.format("c1"), json=body(channel="whatsapp"))
    session = (await chat.get("/v1/conversations/c1", params={"tenant_id": T})).json()
    assert session["channel"] == "whatsapp"


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"tenant_id": T, "text": ""}, "text"),
        ({"tenant_id": T, "text": "   "}, "text"),
        ({"tenant_id": T, "text": "x" * 2001}, "text"),
        ({"tenant_id": T}, "text"),
        ({"text": "hi"}, "tenant_id"),
        ({"tenant_id": "Bad Tenant!", "text": "hi"}, "tenant_id"),
        ({"tenant_id": T, "text": "hi", "channel": "web site!"}, "channel"),
        ({"tenant_id": T, "text": "hi", "surprise": 1}, "surprise"),
    ],
)
async def test_invalid_messages_are_rejected_in_the_error_envelope(
    chat: httpx.AsyncClient, payload: dict[str, object], field: str
) -> None:
    response = await chat.post(URL.format("c1"), json=payload)
    error = response.json()["error"]
    assert response.status_code == 422 and error["code"] == "INVALID_REQUEST" and field in error["message"]
    assert response.json()["schema_version"] == "1.0"


async def test_the_longest_allowed_message_is_accepted(chat: httpx.AsyncClient) -> None:
    assert (await chat.post(URL.format("c1"), json=body("a" * 2000))).status_code == 200
    assert (await chat.post(URL.format("c1"), json=body("a"))).status_code == 200


async def test_an_unknown_tenant_is_a_404_and_nothing_is_stored(
    chat: httpx.AsyncClient, chat_container: Container
) -> None:
    response = await chat.post(URL.format("c1"), json=body(tenant_id="nope"))
    assert response.status_code == 404 and response.json()["error"]["code"] == "TENANT_NOT_FOUND"
    assert await chat_container.sessions.load("nope", "c1") is None


async def test_a_bad_conversation_id_is_rejected(chat: httpx.AsyncClient) -> None:
    response = await chat.post("/v1/conversations/has space/messages", json=body())
    assert response.status_code == 422


async def test_the_request_id_header_tags_the_turn(chat: httpx.AsyncClient, chat_container: Container) -> None:
    response = await chat.post(URL.format("c1"), json=body(), headers={"X-Request-ID": "req-abc-123"})
    assert response.headers["x-request-id"] == "req-abc-123" and response.json()["request_id"] == "req-abc-123"
    (trace,) = await chat_container.traces.for_conversation(T, "c1")
    assert trace.request_id == "req-abc-123"


async def test_without_a_request_id_one_is_made_up(chat: httpx.AsyncClient) -> None:
    response = await chat.post(URL.format("c1"), json=body())
    assert response.json()["request_id"] == response.headers["x-request-id"] != ""


async def test_an_error_carries_the_request_id_too(chat: httpx.AsyncClient) -> None:
    response = await chat.post(URL.format("c1"), json={"tenant_id": T}, headers={"X-Request-ID": "req-err"})
    assert response.json()["error"]["request_id"] == "req-err"


# ---- the rate limit ----


@pytest.fixture
def limited_app(tmp_path) -> FastAPI:  # type: ignore[no-untyped-def]
    return create_app(container=build_container(make_settings(tmp_path, rate_limit_per_minute=3)))


async def test_too_many_messages_in_one_conversation_get_a_429(limited_app: FastAPI) -> None:
    async with limited_app.router.lifespan_context(limited_app):
        transport = httpx.ASGITransport(app=limited_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as chat:
            codes = [(await chat.post(URL.format("busy"), json=body())).status_code for _ in range(4)]
            blocked = await chat.post(URL.format("busy"), json=body())
            other = await chat.post(URL.format("quiet"), json=body())  # another conversation is not affected
    assert codes == [200, 200, 200, 429]
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "RATE_LIMITED"
    assert 1 <= int(blocked.headers["retry-after"]) <= 60 and blocked.json()["error"]["request_id"]
    assert other.status_code == 200


def test_the_limiter_is_a_sliding_window() -> None:
    now = [0.0]
    limiter = RateLimiter(2, window_s=60, clock=lambda: now[0])
    key = (T, "c")
    assert limiter.hit(key) is None and limiter.hit(key) is None
    assert limiter.hit(key) == 60
    now[0] = 30
    assert limiter.hit(key) == 30  # waits for the first hit to leave the window
    now[0] = 61
    assert limiter.hit(key) is None and limiter.hit(key) is None  # both early hits have left the window
    assert limiter.hit(key) == 60
    limiter.forget_idle()
    now[0] = 500
    limiter.forget_idle()
    assert limiter.hit(key) is None


def test_the_limit_is_a_setting() -> None:
    from team_b.config import Settings

    assert Settings().rate_limit_per_minute == 20
    assert Settings.from_env({"TEAM_B_RATE_LIMIT_PER_MIN": "5"}).rate_limit_per_minute == 5


# ---- GET conversation, outbox ----


async def test_the_session_can_be_read_for_debugging_with_personal_data_hidden(chat: httpx.AsyncClient) -> None:
    await chat.post(URL.format("c1"), json=body("Where is my order NS-20877? My phone is 01012345601"))
    response = await chat.get("/v1/conversations/c1", params={"tenant_id": T})
    session = response.json()
    assert response.status_code == 200 and session["conversation_id"] == "c1" and session["turn_index"] == 1
    assert session["slots"] == {"order_id": "NS-20877", "phone": "[phone]"}
    assert "01012345601" not in response.text and "[phone]" in session["history"][0]["text"]


async def test_reading_a_missing_conversation_or_the_wrong_tenant(chat: httpx.AsyncClient) -> None:
    await chat.post(URL.format("c1"), json=body())
    assert (await chat.get("/v1/conversations/zzz", params={"tenant_id": T})).status_code == 404
    assert (await chat.get("/v1/conversations/c1", params={"tenant_id": "nope"})).json()["error"][
        "code"
    ] == "TENANT_NOT_FOUND"
    assert (await chat.get("/v1/conversations/c1")).status_code == 422  # the tenant id is required


async def test_the_outbox_holds_replies_written_by_a_person(chat: httpx.AsyncClient, chat_container: Container) -> None:
    await chat.post(URL.format("c1"), json=body("I want to talk to a human"))
    assert (await chat.get("/v1/conversations/c1/outbox", params={"tenant_id": T})).json() == []
    assert chat_container.orchestrator is not None
    await chat_container.orchestrator.push_to_customer(T, "c1", "Hello, this is Mona from customer care.")
    items = (await chat.get("/v1/conversations/c1/outbox", params={"tenant_id": T})).json()
    assert [(m["role"], m["text"]) for m in items] == [("human_agent", "Hello, this is Mona from customer care.")]


async def test_pushing_to_a_conversation_that_does_not_exist_is_an_error(chat_container: Container) -> None:
    from team_b.ports import NotFoundError

    assert chat_container.orchestrator is not None
    with pytest.raises(NotFoundError):
        await chat_container.orchestrator.push_to_customer(T, "ghost", "hi")


# ---- live events ----


async def wait_for_subscriber(container: Container, conversation: str, count: int = 1) -> None:
    for _ in range(200):
        if container.events.subscribers(T, conversation) >= count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("nobody subscribed")


async def test_a_human_reply_arrives_live_on_the_event_stream(
    chat: httpx.AsyncClient, chat_container: Container
) -> None:
    await chat.post(URL.format("c1"), json=body("I want to talk to a human"))
    assert chat_container.orchestrator is not None
    stream = asyncio.create_task(chat.get("/v1/conversations/c1/events", params={"tenant_id": T, "max_events": 2}))
    await wait_for_subscriber(chat_container, "c1")
    await chat_container.orchestrator.push_to_customer(T, "c1", "First, hello.")
    await chat_container.orchestrator.push_to_customer(T, "c1", "عايز مساعدة؟")
    response = await asyncio.wait_for(stream, timeout=5)
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    events = [e for e in response.text.split("\n\n") if e.startswith("event: message")]
    payloads = [json.loads(e.split("data: ", 1)[1]) for e in events]
    assert [p["text"] for p in payloads] == ["First, hello.", "عايز مساعدة؟"] and payloads[0]["role"] == "human_agent"
    assert chat_container.events.subscribers(T, "c1") == 0  # the subscription is released


async def test_events_of_one_conversation_do_not_reach_another(
    chat: httpx.AsyncClient, chat_container: Container
) -> None:
    for conversation in ("a", "b"):
        await chat.post(URL.format(conversation), json=body("I want to talk to a human"))
    assert chat_container.orchestrator is not None
    stream = asyncio.create_task(chat.get("/v1/conversations/b/events", params={"tenant_id": T, "max_events": 1}))
    await wait_for_subscriber(chat_container, "b")
    await chat_container.orchestrator.push_to_customer(T, "a", "for a")
    await chat_container.orchestrator.push_to_customer(T, "b", "for b")
    text = (await asyncio.wait_for(stream, timeout=5)).text
    assert "for b" in text and "for a" not in text


async def test_every_open_page_of_a_conversation_gets_the_message(
    chat: httpx.AsyncClient, chat_container: Container
) -> None:
    await chat.post(URL.format("c1"), json=body("I want to talk to a human"))
    assert chat_container.orchestrator is not None
    params = {"tenant_id": T, "max_events": 1}
    streams = [asyncio.create_task(chat.get("/v1/conversations/c1/events", params=params)) for _ in range(2)]
    await wait_for_subscriber(chat_container, "c1", 2)
    await chat_container.orchestrator.push_to_customer(T, "c1", "hello both")
    responses = await asyncio.wait_for(asyncio.gather(*streams), timeout=5)
    assert all("hello both" in r.text for r in responses)


async def test_the_event_stream_needs_a_known_tenant(chat: httpx.AsyncClient) -> None:
    response = await chat.get("/v1/conversations/c1/events", params={"tenant_id": "nope"})
    assert response.status_code == 404


async def test_the_stream_sends_keepalive_comments_and_cleans_up(chat_app: FastAPI, chat_container: Container) -> None:
    class Req:
        app = chat_app

    chat_app.state.events = chat_container.events  # normally set when the app starts
    stream = event_stream(Req(), T, "c9", max_events=None, keepalive_s=0.01)  # type: ignore[arg-type]
    chunks = [await anext(stream) for _ in range(3)]
    assert chunks[0] == ": connected\n\n" and chunks[1:] == [": keepalive\n\n"] * 2
    assert chat_container.events.subscribers(T, "c9") == 1
    await stream.aclose()
    assert chat_container.events.subscribers(T, "c9") == 0


# ---- traces ----


async def test_a_trace_can_be_read_by_id(chat: httpx.AsyncClient) -> None:
    reply = (await chat.post(URL.format("c1"), json=body("Where is my order NS-20877?"))).json()
    response = await chat.get(f"/v1/traces/{reply['trace_id']}", params={"tenant_id": T})
    trace = response.json()
    assert (
        response.status_code == 200
        and trace["trace_id"] == reply["trace_id"]
        and trace["decision"] == "verify_identity"
    )
    assert trace["intents"][0]["name"] == "order_status" and [s["stage"] for s in trace["steps"]][0] == "load"


async def test_the_traces_of_a_conversation_come_oldest_first(chat: httpx.AsyncClient) -> None:
    for text in ("Hello", "Where is my order?"):
        await chat.post(URL.format("c1"), json=body(text))
    traces = (await chat.get("/v1/conversations/c1/traces", params={"tenant_id": T})).json()
    assert [t["turn_index"] for t in traces] == [0, 1] and [t["customer_message"] for t in traces][0] == "Hello"


async def test_missing_traces_and_other_tenants_are_404(chat: httpx.AsyncClient) -> None:
    reply = (await chat.post(URL.format("c1"), json=body())).json()
    assert (await chat.get("/v1/traces/does-not-exist", params={"tenant_id": T})).status_code == 404
    assert (await chat.get("/v1/conversations/zzz/traces", params={"tenant_id": T})).status_code == 404
    other = await chat.get(f"/v1/traces/{reply['trace_id']}", params={"tenant_id": "nope"})
    assert other.json()["error"]["code"] == "TENANT_NOT_FOUND"
    assert (await chat.get(f"/v1/traces/{reply['trace_id']}")).status_code == 422


async def test_a_trace_never_contains_the_raw_phone_number(chat: httpx.AsyncClient) -> None:
    reply = (await chat.post(URL.format("c1"), json=body("my phone is 01012345601"))).json()
    text = (await chat.get(f"/v1/traces/{reply['trace_id']}", params={"tenant_id": T})).text
    assert "01012345601" not in text


# ---- the page ----


async def test_the_welcome_greets_in_the_business_language(chat: httpx.AsyncClient, chat_container: Container) -> None:
    welcome = (await chat.get(f"/v1/tenants/{T}/welcome")).json()
    assert welcome == {"display_name": "Nile Style", "locale": "ar", "text": render("greeting", Locale.AR)}
    assert await chat_container.sessions.load(T, "any") is None  # no conversation was created
    assert (await chat.get("/v1/tenants/nope/welcome")).status_code == 404


async def test_the_chat_page_is_served_with_its_files(chat: httpx.AsyncClient) -> None:
    page = await chat.get("/chat", params={"tenant_id": T})
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    for needle in ('id="messages"', 'id="composer"', 'id="dev-toggle"', 'dir="auto"', "/chat/static/chat.js"):
        assert needle in page.text, needle
    script = await chat.get("/chat/static/chat.js")
    style = await chat.get("/chat/static/style.css")
    assert script.status_code == 200 and "EventSource" in script.text and "/welcome" in script.text
    assert style.status_code == 200 and ".chip" in style.text


async def test_the_page_script_never_writes_html_from_messages(chat: httpx.AsyncClient) -> None:
    script = (await chat.get("/chat/static/chat.js")).text
    assert "innerHTML" not in script and "insertAdjacentHTML" not in script and "document.write" not in script
    assert script.count("textContent") >= 5
