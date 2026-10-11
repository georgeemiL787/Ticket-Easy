"""Minimal OpenRouter chat client restricted to free models.

Used only for candidate-rule extraction and as a second opinion in risk classification. Nothing
that controls execution depends on it.
"""

import json
import logging
import re

import httpx

from team_a.config import settings

log = logging.getLogger(__name__)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class LLMUnavailable(RuntimeError):
    pass


def _free_models() -> list[str]:
    models = [m for m in settings.openrouter_models if m.endswith(":free")]
    ignored = set(settings.openrouter_models) - set(models)
    if ignored:
        log.warning("Ignoring non-free OpenRouter models: %s", sorted(ignored))
    return models


def is_configured() -> bool:
    return bool(settings.openrouter_api_key and _free_models())


_http: httpx.Client | None = None


def _client() -> httpx.Client:
    """One reused client: building one per call reloads the TLS bundle (~250 ms on Windows)."""
    global _http
    if _http is None:
        _http = httpx.Client()
    return _http


def complete_json(system: str, user: str, timeout: float = 90) -> dict:
    """Ask for a JSON object; try each configured free model in turn."""
    if not is_configured():
        raise LLMUnavailable("Set OPENROUTER_API_KEY and at least one ':free' model in OPENROUTER_MODELS")
    errors = []
    for model in _free_models():
        try:
            resp = _client().post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "X-Title": "Ticket-Easy Team A",
                },
                json={
                    "model": model,
                    "temperature": 0,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"] or ""
            return _extract_json(content)
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            log.warning("OpenRouter model %s failed: %s", model, exc)
            errors.append(f"{model}: {exc}")
    raise LLMUnavailable("All free models failed: " + " | ".join(errors))


def _extract_json(content: str) -> dict:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, flags=re.S)
    candidate = fenced.group(1) if fenced else content[content.find("{"): content.rfind("}") + 1]
    if not candidate:
        raise ValueError("No JSON object in model output")
    return json.loads(candidate)
