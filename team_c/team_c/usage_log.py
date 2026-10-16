"""The token usage log: one JSON line per model request (one per provider attempt).

Switched on by TEAM_C_USAGE_LOG (a file path; empty = off); TEAM_C_USAGE_RUN_LABEL tags a run. The line format is the same
as in the conversation and knowledge services (Docs/output/eval_runs/USAGE_LOG_FORMAT.md). A line never holds prompt,
reply or document text, customer data or an API key (the key is only its position in the list). Writing is append-only and
never raises: on a failure one warning is logged and the work carries on.
"""
import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)
SERVICE_NAME = "tools"
_lock = threading.Lock()
_warned = False
_HTTP = re.compile(r"HTTP (\d{3})")


def provider_host(url):
    """The server only: no path, no query, no credentials."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    return f"{host}:{parts.port}" if parts.port else host


def count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def tokens(provider, usage):
    """(input, output, reasoning, cached) from a provider's usage dict; a count that was not given is None."""
    usage = usage if isinstance(usage, dict) else {}
    if provider == "ollama":
        return count(usage.get("prompt_eval_count")), count(usage.get("eval_count")), None, None
    completion, prompt = usage.get("completion_tokens_details"), usage.get("prompt_tokens_details")
    return (
        count(usage.get("prompt_tokens")),
        count(usage.get("completion_tokens")),
        count(completion.get("reasoning_tokens")) if isinstance(completion, dict) else None,
        count(prompt.get("cached_tokens")) if isinstance(prompt, dict) else None,
    )


def record_attempt(settings, *, kind, provider, url, model, started, response=None, error=None, timed_out=False, key_index=None):
    """One line for one provider attempt: ok with the provider's counts, or a failure with null tokens. Never raises."""
    global _warned
    path = getattr(settings, "usage_log", "").strip()
    if not path:
        return
    try:
        code = None
        if response is not None:
            code, status, usage = None, "ok", response.usage
        else:
            usage = None
            match = _HTTP.search(getattr(error, "message", "") or "")
            code = int(match.group(1)) if match else None
            status = "timeout" if timed_out else "rate_limited" if code in (429, 413) else "error"
        tok = tokens(provider, usage)
        if response is not None:
            code = 200
        line = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "service": SERVICE_NAME,
            "run_label": getattr(settings, "usage_run_label", ""),
            "job": kind.split(":", 1)[0],
            "kind": "chat",
            "model": model,
            "provider": provider_host(url),
            "status": status,
            "http_status": code,
            "input_tokens": tok[0],
            "output_tokens": tok[1],
            "reasoning_tokens": tok[2],
            "cached_tokens": tok[3],
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "key_index": key_index,
            "backup_used": False,
            "estimated": False,
        }
        data = json.dumps(line, ensure_ascii=False) + "\n"
        target = Path(path)
        with _lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(data)
    except Exception as exc:  # never break a run because of a log
        if not _warned:
            _warned = True
            log.warning("usage log could not be written (%s)", type(exc).__name__)
