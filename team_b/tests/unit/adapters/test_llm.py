"""The OpenAI-compatible client against a fake HTTP server (httpx.MockTransport): no network."""

import json
from collections.abc import Callable

import httpx
import pytest

from team_b.adapters.llm import OpenAICompatibleLLM, parse_json_object
from team_b.contracts.errors import InvalidLLMOutput, UpstreamError
from team_b.ports import LLMClient

Handler = Callable[[httpx.Request], httpx.Response]


def reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


def client(handler: Handler, **kw: object) -> OpenAICompatibleLLM:
    options: dict[str, object] = {"base_url": "http://llm.test/v1/", "model": "m1", "api_key": "sk-secret", **kw}
    return OpenAICompatibleLLM(transport=httpx.MockTransport(handler), **options)  # type: ignore[arg-type]


async def ask(llm: OpenAICompatibleLLM) -> dict[str, object]:
    return await llm.complete_json(system="be brief", user="hello", schema_hint={"a": "int"})


def test_it_satisfies_the_llm_port() -> None:
    assert isinstance(client(lambda r: reply("{}")), LLMClient)


async def test_the_request_has_the_expected_shape() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return reply('{"a": 1}')

    result = await client(handler).complete_json(
        system="be brief", user="hello", schema_hint={"a": "int"}, temperature=0.2
    )
    assert result == {"a": 1}
    request = seen[0]
    body = json.loads(request.content)
    assert str(request.url) == "http://llm.test/v1/chat/completions"
    assert request.headers["authorization"] == "Bearer sk-secret"
    assert body["model"] == "m1" and body["temperature"] == 0.2
    assert body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["messages"][0]["content"].startswith("be brief") and '"a": "int"' in body["messages"][0]["content"]
    assert body["messages"][1]["content"] == "hello"


async def test_no_key_means_no_authorization_header() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return reply("{}")

    await ask(client(handler, api_key=None))
    assert "authorization" not in seen[0].headers


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('  {"a": 1}\n', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('```\n{"a": 1}\n```', {"a": 1}),
        ('{"text": "عايز ارجع"}', {"text": "عايز ارجع"}),
    ],
)
async def test_json_is_parsed_even_inside_a_code_fence(content: str, expected: dict[str, object]) -> None:
    assert await ask(client(lambda r: reply(content))) == expected


@pytest.mark.parametrize("content", ["sure, here you go", '{"a": ', "[1, 2]", '"just text"', ""])
async def test_an_answer_that_is_not_a_json_object_is_invalid_output(content: str) -> None:
    with pytest.raises(InvalidLLMOutput) as caught:
        await ask(client(lambda r: reply(content)))
    assert caught.value.raw == content


def test_parse_json_object_directly() -> None:
    assert parse_json_object('{"x": [1]}') == {"x": [1]}
    with pytest.raises(InvalidLLMOutput, match="not an object"):
        parse_json_object("[]")


HTTP_FAILURES = [
    (500, "BACKEND_UNAVAILABLE", True),
    (503, "BACKEND_UNAVAILABLE", True),
    (429, "BACKEND_UNAVAILABLE", True),
    (401, "UNAUTHORIZED", False),
    (403, "UNAUTHORIZED", False),
    (404, "BAD_REQUEST", False),
    (422, "BAD_REQUEST", False),
]


@pytest.mark.parametrize(("status", "code", "retryable"), HTTP_FAILURES)
async def test_http_errors_become_upstream_errors(status: int, code: str, retryable: bool) -> None:
    with pytest.raises(UpstreamError) as caught:
        await ask(client(lambda r: httpx.Response(status, text="nope")))
    assert (caught.value.service, caught.value.code, caught.value.retryable) == ("llm", code, retryable)


async def test_a_timeout_is_a_retryable_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(UpstreamError) as caught:
        await ask(client(handler))
    assert (caught.value.code, caught.value.retryable) == ("TIMEOUT", True)


async def test_a_connection_failure_is_backend_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(UpstreamError) as caught:
        await ask(client(handler))
    assert (caught.value.code, caught.value.retryable) == ("BACKEND_UNAVAILABLE", True)


@pytest.mark.parametrize(
    "body", [{}, {"choices": []}, {"choices": [{}]}, {"choices": [{"message": {"content": None}}]}]
)
async def test_an_unknown_response_format_is_bad_response(body: dict[str, object]) -> None:
    with pytest.raises(UpstreamError) as caught:
        await ask(client(lambda r: httpx.Response(200, json=body)))
    assert caught.value.code == "BAD_RESPONSE"


async def test_a_non_json_http_body_is_bad_response() -> None:
    with pytest.raises(UpstreamError) as caught:
        await ask(client(lambda r: httpx.Response(200, text="<html>")))
    assert caught.value.code == "BAD_RESPONSE"


async def test_a_server_without_json_mode_is_retried_once_without_it_and_remembered() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": "unknown field response_format"})
        return reply('{"ok": true}')

    llm = client(handler)
    assert await ask(llm) == {"ok": True}
    assert await ask(llm) == {"ok": True}
    assert ["response_format" in b for b in bodies] == [True, False, False]  # the second question skips it at once


async def test_the_key_never_appears_in_an_error() -> None:
    with pytest.raises(UpstreamError) as caught:
        await ask(client(lambda r: httpx.Response(401, text="bad key sk-secret")))
    assert "sk-secret" not in str(caught.value) and "sk-secret" not in caught.value.message
