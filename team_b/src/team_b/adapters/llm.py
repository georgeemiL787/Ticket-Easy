"""The AI model client: any server that speaks the OpenAI chat-completions format.

One class serves Ollama (base_url http://127.0.0.1:11434/v1, no key) and OpenRouter (https://openrouter.ai/api/v1 with a
key). Answers are JSON objects: the request asks for JSON mode, and the reply text is parsed (a ```json fence around it
is tolerated). Failures follow the plug rules: a service that cannot be reached, is too slow or refuses raises
UpstreamError; an answer that is not a JSON object raises InvalidLLMOutput, which the caller may repair or ignore.
The API key is never logged and never put in an error message.
"""

import json
import re
from typing import Any

import httpx

from team_b.contracts.errors import InvalidLLMOutput, UpstreamError
from team_b.observability import get_logger

SERVICE = "llm"
log = get_logger(__name__)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
_ERROR_PREVIEW = 200


def parse_json_object(text: str) -> dict[str, Any]:
    """The JSON object in a model reply, or InvalidLLMOutput."""
    body = text.strip()
    if (fenced := _FENCE.match(body)) is not None:
        body = fenced.group(1)
    try:
        value = json.loads(body)
    except json.JSONDecodeError as exc:
        raise InvalidLLMOutput(f"the reply is not valid JSON ({exc.msg})", text) from None
    if not isinstance(value, dict):
        raise InvalidLLMOutput("the reply is JSON but not an object", text)
    return value


class OpenAICompatibleLLM:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_s: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout = timeout_s
        self._transport = transport
        self._json_mode = True  # switched off for good when the server rejects response_format

    async def complete_json(
        self, *, system: str, user: str, schema_hint: dict[str, Any], temperature: float = 0.0
    ) -> dict[str, Any]:
        hint = json.dumps(schema_hint, ensure_ascii=False)
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": f"{system}\n\nReply with one JSON object shaped like: {hint}"},
                {"role": "user", "content": user},
            ],
        }
        response = await self._post(
            {**payload, "response_format": {"type": "json_object"}} if self._json_mode else payload
        )
        if response.status_code == 400 and self._json_mode and "response_format" in response.text:
            self._json_mode = False  # this server has no JSON mode: ask in words only
            response = await self._post(payload)
        return parse_json_object(self._content(response))

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.post(self._url, json=payload, headers=self._headers)
        except httpx.TimeoutException:
            raise UpstreamError(SERVICE, "TIMEOUT", "the AI model did not answer in time", retryable=True) from None
        except httpx.TransportError as exc:
            raise UpstreamError(
                SERVICE, "BACKEND_UNAVAILABLE", f"cannot reach the AI model ({type(exc).__name__})", retryable=True
            ) from None
        if response.status_code in (401, 403):
            raise UpstreamError(SERVICE, "UNAUTHORIZED", "the AI model refused the credentials")
        if response.status_code == 429 or response.status_code >= 500:
            raise UpstreamError(
                SERVICE, "BACKEND_UNAVAILABLE", f"the AI model answered HTTP {response.status_code}", retryable=True
            )
        if response.status_code >= 400 and not (response.status_code == 400 and "response_format" in response.text):
            raise UpstreamError(SERVICE, "BAD_REQUEST", f"the AI model answered HTTP {response.status_code}")
        return response

    @staticmethod
    def _content(response: httpx.Response) -> str:
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise UpstreamError(SERVICE, "BAD_RESPONSE", "the AI model answered in an unknown format") from None
        if not isinstance(content, str):
            raise UpstreamError(SERVICE, "BAD_RESPONSE", "the AI model answered without text")
        return content
