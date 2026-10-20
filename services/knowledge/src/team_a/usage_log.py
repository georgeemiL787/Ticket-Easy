"""The token usage log: one JSON line per request to an AI model or embedding server.

Switched on by TEAM_A_USAGE_LOG (a file path; empty = off); TEAM_A_USAGE_RUN_LABEL tags a run. The line format is the
same as in the conversation and tool services (docs/reports/eval_runs/USAGE_LOG_FORMAT.md). A line never holds prompt,
reply or document text, customer data or an API key. Writing is append-only and never raises: on a failure one warning
is logged and the work carries on.
"""

import json
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from team_a.config import settings

log = logging.getLogger(__name__)
SERVICE_NAME = "knowledge"
CHARS_PER_TOKEN = 3  # the estimate used only when an embedding server does not report its own token count
_JOB: ContextVar[str] = ContextVar("usage_job", default="unknown")
_lock = threading.Lock()
_warned = False


@contextmanager
def usage_job(job: str) -> Iterator[None]:
    """Requests made inside this block are logged under this job (rule_extraction, risk_second_opinion, embed_query...)."""
    token = _JOB.set(job)
    try:
        yield
    finally:
        _JOB.reset(token)


def current_job(default: str = "unknown") -> str:
    job = _JOB.get()
    return default if job == "unknown" else job


def enabled() -> bool:
    return bool(settings.usage_log.strip())


def provider_host(url: str) -> str:
    """The server only: no path, no query, no credentials."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    return f"{host}:{parts.port}" if parts.port else host


def count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class Stopwatch:
    def __init__(self) -> None:
        self._start = time.perf_counter()

    def ms(self) -> int:
        return round((time.perf_counter() - self._start) * 1000)


def record(
    *,
    job: str,
    kind: str,
    model: str,
    url: str,
    status: str,
    http_status: int | None,
    latency_ms: int,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    cached_tokens: int | None = None,
    key_index: int | None = None,
    estimated: bool = False,
) -> None:
    """Append one line. A no-op when the log is off; never raises."""
    global _warned
    if not enabled():
        return
    line = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "service": SERVICE_NAME,
        "run_label": settings.usage_run_label,
        "job": job,
        "kind": kind,
        "model": model,
        "provider": provider_host(url),
        "status": status,
        "http_status": http_status,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning_tokens,
        "cached_tokens": cached_tokens,
        "latency_ms": latency_ms,
        "key_index": key_index,
        "backup_used": False,
        "estimated": estimated,
    }
    try:
        path = Path(settings.usage_log)
        data = json.dumps(line, ensure_ascii=False) + "\n"
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(data)
    except Exception as exc:  # never break a run because of a log
        if not _warned:
            _warned = True
            log.warning("usage log could not be written (%s)", type(exc).__name__)


def chat_tokens(body: object) -> dict[str, int | None]:
    """The token counts of an OpenAI-style chat reply (usage.prompt_tokens, ...); every count not given is None."""
    empty: dict[str, int | None] = {
        "input_tokens": None, "output_tokens": None, "reasoning_tokens": None, "cached_tokens": None,
    }  # fmt: skip
    try:
        usage = body["usage"]  # type: ignore[index]
        if not isinstance(usage, dict):
            return empty
        completion, prompt = usage.get("completion_tokens_details"), usage.get("prompt_tokens_details")
        return {
            "input_tokens": count(usage.get("prompt_tokens")),
            "output_tokens": count(usage.get("completion_tokens")),
            "reasoning_tokens": count(completion.get("reasoning_tokens")) if isinstance(completion, dict) else None,
            "cached_tokens": count(prompt.get("cached_tokens")) if isinstance(prompt, dict) else None,
        }
    except (KeyError, TypeError):
        return empty
