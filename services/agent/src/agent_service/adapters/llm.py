"""The AI model client: any server that speaks the OpenAI chat-completions format.

One class serves Ollama (base_url http://127.0.0.1:11434/v1, no key) and OpenRouter (https://openrouter.ai/api/v1 with a
key). Answers are JSON objects: the request asks for JSON mode, and the reply text is parsed (a ```json fence around it
is tolerated). Failures follow the plug rules: a service that cannot be reached, is too slow or refuses raises
UpstreamError; an answer that is not a JSON object raises InvalidLLMOutput, which the caller may repair or ignore.
The API key is never logged and never put in an error message.
"""

import asyncio
import itertools
import json
import re
import time
from typing import Any

import httpx

from agent_service.adapters.llm_limits import RateLimiter, Reservation
from agent_service.adapters.usage_log import UsageLog, provider_host
from agent_service.contracts.errors import InvalidLLMOutput, UpstreamError
from agent_service.observability import get_logger

SERVICE = "llm"
log = get_logger(__name__)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
_ERROR_PREVIEW = 200
_MODEL_GONE = re.compile(r"model.{0,60}(not found|does not exist|decommission|deprecated|no longer)|decommission", re.I)
CHARS_PER_TOKEN = 3  # a cautious guess before the server tells the real count
ANSWER_TOKENS = 400  # room kept in the budget for the answer
RATE_LIMIT_ROUNDS = 3
RATE_LIMIT_WAIT_S = 6.0


def _wait_seconds(response: httpx.Response | None, round_: int) -> float:
    try:
        asked = float(response.headers.get("retry-after", "")) if response is not None else 0.0
    except ValueError:
        asked = 0.0
    return min(RATE_LIMIT_WAIT_S, max(asked, 1.0 + round_))


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
        reasoning_effort: str | None = None,
        limiter: RateLimiter | None = None,
        usage_log: UsageLog | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._model = model
        keys = [k.strip() for k in (api_key or "").split(",") if k.strip()]  # several keys allowed
        self._headers = {"Authorization": f"Bearer {keys[0]}"} if keys else {}  # the first key's header
        self._keys = itertools.cycle(enumerate(keys or [""]))  # (position in the list, key)
        self._usage = usage_log  # one line per request when set (no text, no key)
        self._host = provider_host(base_url)
        self._key_count = max(1, len(keys))
        self._timeout = timeout_s
        self._transport = transport
        self._json_mode = True  # switched off for good when the server rejects response_format
        self._reasoning_effort = reasoning_effort  # sent only when set; dropped for good if the server rejects it
        self._limiter = limiter  # the (server, model) budget shared with every job on this model, or None
        self.model = model

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
        tokens = (len(system) + len(user) + len(hint)) // CHARS_PER_TOKEN + ANSWER_TOKENS
        response = await self._post(self._body(payload), tokens)
        if response.status_code == 400 and self._json_mode and "response_format" in response.text:
            self._json_mode = False  # this server has no JSON mode: ask in words only
            response = await self._post(self._body(payload), tokens)
        if response.status_code == 400 and self._reasoning_effort and "reasoning_effort" in response.text:
            self._reasoning_effort = None  # this model has no such setting: stop sending it
            response = await self._post(self._body(payload), tokens)
        return parse_json_object(self._content(response))

    async def complete_text(self, *, system: str, user: str, temperature: float = 0.0) -> str:
        """The answer as plain text, with no JSON format asked for (for a small model that cannot keep one)."""
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": temperature,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        tokens = (len(system) + len(user)) // CHARS_PER_TOKEN + ANSWER_TOKENS
        body = dict(payload)
        if self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort
        response = await self._post(body, tokens)
        if response.status_code == 400 and self._reasoning_effort and "reasoning_effort" in response.text:
            self._reasoning_effort = None
            response = await self._post(payload, tokens)
        return self._content(response)

    def _body(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = dict(payload)
        if self._json_mode:
            body["response_format"] = {"type": "json_object"}
        if self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort
        return body

    async def _post(self, payload: dict[str, Any], tokens: int = 0) -> httpx.Response:
        try:
            response = await self._send(payload, tokens)
        except httpx.TimeoutException:
            raise UpstreamError(SERVICE, "TIMEOUT", "the AI model did not answer in time", retryable=True) from None
        except httpx.TransportError as exc:
            raise UpstreamError(
                SERVICE, "BACKEND_UNAVAILABLE", f"cannot reach the AI model ({type(exc).__name__})", retryable=True
            ) from None
        if response.status_code in (401, 403):
            raise UpstreamError(SERVICE, "UNAUTHORIZED", "the AI model refused the credentials")
        if response.status_code in (400, 404, 410) and _MODEL_GONE.search(response.text[:2000]):
            raise UpstreamError(SERVICE, "MODEL_NOT_FOUND", f"the AI model {self.model!r} is not available any more")
        if response.status_code == 429 or response.status_code >= 500:
            raise UpstreamError(
                SERVICE, "BACKEND_UNAVAILABLE", f"the AI model answered HTTP {response.status_code}", retryable=True
            )
        if response.status_code >= 400 and not (
            response.status_code == 400 and ("response_format" in response.text or "reasoning_effort" in response.text)
        ):
            raise UpstreamError(SERVICE, "BAD_REQUEST", f"the AI model answered HTTP {response.status_code}")
        return response

    async def _send(self, payload: dict[str, Any], tokens: int = 0) -> httpx.Response:
        """One request; on HTTP 429 the next key is tried at once, and after a full round the call waits (Retry-After,
        at most RATE_LIMIT_WAIT_S) and goes round again, RATE_LIMIT_ROUNDS times in all. Every request first takes a
        slot from the model's per-minute budget (RateBudgetExceeded when none frees up soon), and gives the real token
        count back when the server states it."""
        response: httpx.Response | None = None
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            for round_ in range(RATE_LIMIT_ROUNDS):
                for _ in range(self._key_count):
                    position, key = next(self._keys)
                    headers = {"Authorization": f"Bearer {key}"} if key else {}
                    slot = await self._limiter.acquire(tokens) if self._limiter is not None else None
                    started = time.perf_counter()
                    try:
                        response = await client.post(self._url, json=payload, headers=headers)
                    except BaseException as exc:
                        if slot is not None and self._limiter is not None:
                            self._limiter.release(slot)  # it never reached the server
                        self._note(None, started, position if key else None, exc)
                        raise
                    self._settle(slot, response)
                    self._note(response, started, position if key else None)
                    if response.status_code != 429:
                        return response
                if round_ + 1 < RATE_LIMIT_ROUNDS:
                    await asyncio.sleep(_wait_seconds(response, round_))
        assert response is not None
        return response

    def _note(
        self, response: httpx.Response | None, started: float, key_index: int | None, error: BaseException | None = None
    ) -> None:
        """One line in the usage log for this request (a no-op when the log is off)."""
        if self._usage is None:
            return
        code = response.status_code if response is not None else None
        if response is None:
            status = "timeout" if isinstance(error, httpx.TimeoutException) else "error"
        elif code == 429:
            status = "rate_limited"
        else:
            status = "ok" if code == 200 else "error"
        self._usage.record(
            model=self._model,
            host=self._host,
            status=status,
            http_status=code,
            latency_ms=round((time.perf_counter() - started) * 1000),
            key_index=key_index,
            response=response,
        )

    def _settle(self, slot: Reservation | None, response: httpx.Response) -> None:
        """Replace the estimated tokens of a request with the count the server reports (a refused request used none)."""
        if slot is None or self._limiter is None:
            return
        used: int | None = 0 if response.status_code == 429 else None
        if response.status_code == 200:
            try:
                total = response.json()["usage"]["total_tokens"]
                used = int(total) if isinstance(total, int | float) and not isinstance(total, bool) else None
            except (ValueError, KeyError, TypeError):
                used = None
        self._limiter.settle(slot, used)

    @staticmethod
    def _content(response: httpx.Response) -> str:
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise UpstreamError(SERVICE, "BAD_RESPONSE", "the AI model answered in an unknown format") from None
        if not isinstance(content, str):
            raise UpstreamError(SERVICE, "BAD_RESPONSE", "the AI model answered without text")
        return content
