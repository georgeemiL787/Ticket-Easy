"""The token usage log: one JSON line per request to an AI model, with the exact counts the provider returned.

Switched on by AGENT_USAGE_LOG (a file path; empty = off). The format is the same in the conversation, knowledge and
tool services (docs/reports/eval_runs/USAGE_LOG_FORMAT.md). A line never holds prompt or reply text, customer data or an
API key. Writing is append-only and never raises: on a failure one warning is logged and the run carries on.
"""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from agent_service.observability import get_logger

log = get_logger(__name__)
SERVICE_NAME = "conversation"
_CALL: ContextVar[tuple[str, bool]] = ContextVar("usage_call", default=("unknown", False))


@contextmanager
def tagged(job: str, backup: bool = False) -> Iterator[None]:
    """Everything the client sends inside this block is logged under this job (and as a backup-model call)."""
    token = _CALL.set((job, backup))
    try:
        yield
    finally:
        _CALL.reset(token)


def current_call() -> tuple[str, bool]:
    return _CALL.get()


def provider_host(base_url: str) -> str:
    """The server only: no path, no query, no credentials."""
    parts = urlsplit(base_url)
    host = parts.hostname or ""
    return f"{host}:{parts.port}" if parts.port else host


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def read_usage(response: httpx.Response | None) -> dict[str, int | None]:
    """The token counts of a reply; every count the provider did not give is None (never 0)."""
    empty: dict[str, int | None] = {
        "input_tokens": None, "output_tokens": None, "reasoning_tokens": None, "cached_tokens": None,
    }  # fmt: skip
    if response is None or response.status_code != 200:
        return empty
    try:
        usage = response.json()["usage"]
        if not isinstance(usage, dict):
            return empty
        completion = usage.get("completion_tokens_details")
        prompt = usage.get("prompt_tokens_details")
        return {
            "input_tokens": _count(usage.get("prompt_tokens")),
            "output_tokens": _count(usage.get("completion_tokens")),
            "reasoning_tokens": _count(completion.get("reasoning_tokens")) if isinstance(completion, dict) else None,
            "cached_tokens": _count(prompt.get("cached_tokens")) if isinstance(prompt, dict) else None,
        }
    except (ValueError, KeyError, TypeError, AttributeError):
        return empty


class UsageLog:
    def __init__(self, path: str | Path, run_label: str = "", service: str = SERVICE_NAME) -> None:
        self._path = Path(path)
        self._run_label = run_label
        self._service = service
        self._lock = threading.Lock()
        self._warned = False

    def record(
        self,
        *,
        model: str,
        host: str,
        status: str,
        http_status: int | None,
        latency_ms: int,
        key_index: int | None,
        response: httpx.Response | None = None,
    ) -> None:
        job, backup = current_call()
        line = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "service": self._service,
            "run_label": self._run_label,
            "job": job,
            "kind": "chat",
            "model": model,
            "provider": host,
            "status": status,
            "http_status": http_status,
            **read_usage(response),
            "latency_ms": latency_ms,
            "key_index": key_index,
            "backup_used": backup,
            "estimated": False,
        }
        try:
            data = json.dumps(line, ensure_ascii=False) + "\n"
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(data)
        except Exception as exc:  # never break a customer turn or a test run because of a log
            if not self._warned:
                self._warned = True
                log.warning("usage_log_write_failed", error=type(exc).__name__)
