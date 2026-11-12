"""Logging: structlog, JSON or console, with the request ids attached to every line automatically.

Langfuse tracing: optional LLM observability. When enabled (all three env vars set: LANGFUSE_PUBLIC_KEY,
LANGFUSE_SECRET_KEY, LANGFUSE_HOST), traces are sent to Langfuse. When disabled, the app runs normally.
"""

import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.contextvars import bind_contextvars, bound_contextvars, clear_contextvars, merge_contextvars
from structlog.typing import FilteringBoundLogger, Processor


class _LiveStdout:
    """Writes to whatever sys.stdout is right now (a test or a service manager may swap it after logging is set up)."""

    def write(self, text: str) -> int:
        return sys.stdout.write(text)

    def flush(self) -> None:
        sys.stdout.flush()


CONTEXT_KEYS = ("tenant_id", "conversation_id", "request_id", "trace_id")


def configure_logging(*, json_logs: bool = True, level: int = logging.INFO) -> None:
    """Set up structlog. Safe to call more than once."""
    renderer: Processor = structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=_LiveStdout()),  # type: ignore[arg-type]
        cache_logger_on_first_use=False,
    )


def get_logger(name: str | None = None) -> FilteringBoundLogger:
    logger: FilteringBoundLogger = structlog.get_logger(name)
    return logger


def bind_context(
    *,
    tenant_id: str | None = None,
    conversation_id: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> None:
    """Attach ids to every log line of the current request. Ids that are None are left as they are."""
    given = {
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "request_id": request_id,
        "trace_id": trace_id,
    }
    bind_contextvars(**{key: value for key, value in given.items() if value is not None})


@contextmanager
def turn_context(*, tenant_id: str, conversation_id: str, request_id: str, trace_id: str) -> Iterator[None]:
    """All four ids on every log line written inside the block (one customer turn); the ids are restored afterwards."""
    with bound_contextvars(
        tenant_id=tenant_id, conversation_id=conversation_id, request_id=request_id, trace_id=trace_id
    ):
        yield


# The events the core logs, one line each. Fields are ids, codes and counts only: never message text or personal data.
EVENTS = ("turn_start", "turn_complete", "policy_check", "tool_call", "handoff_created", "dependency_error")


def clear_context() -> None:
    clear_contextvars()


# ---- what the AI model did during one turn (which job used a backup model, which fell back to the rules) ----

_AI_EVENTS: ContextVar[list[tuple[str, str, str]] | None] = ContextVar("ai_events", default=None)


@contextmanager
def ai_events() -> Iterator[list[tuple[str, str, str]]]:
    """Collect the AI events of the code run inside the block: (role, kind, detail), kind "backup_model" or "failed"."""
    events: list[tuple[str, str, str]] = []
    token = _AI_EVENTS.set(events)
    try:
        yield events
    finally:
        _AI_EVENTS.reset(token)


def record_ai_event(role: str, kind: str, detail: str) -> None:
    """Note that an AI job used its backup model (backup_model) or could not be answered at all (failed)."""
    events = _AI_EVENTS.get()
    if events is not None:
        events.append((role, kind, detail))


# ---- Langfuse tracing ----

_LANGFUSE_ENABLED: bool = False  # Set once at startup, never changes

# PII masking patterns
_EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
# Egyptian phone numbers - both Western and Arabic-Indic digits
# Match 01xxxxxxxxx or ٠١xxxxxxxxx (10-11 digits starting with 01 or ٠١)
_PHONE_PATTERN = re.compile(r"(?<![0-9٠-٩])[0٠][1١][0-9٠-٩]{9,10}(?![0-9٠-٩])")
# Order IDs
_ORDER_ID_PATTERN = re.compile(r"\b(NS|ORDER|ORD)-[0-9٠-٩]{4,6}\b", re.IGNORECASE)
# Card numbers - both Western and Arabic-Indic digits (16 digits with optional separators)
_CARD_PATTERN = re.compile(r"(?<![0-9٠-٩])[0-9٠-٩]{4}[\s-]?[0-9٠-٩]{4}[\s-]?[0-9٠-٩]{4}[\s-]?[0-9٠-٩]{4}(?![0-9٠-٩])")
# Address keywords (English and Arabic)
_ADDRESS_KEYWORDS = re.compile(
    r"\b(street|st\.|avenue|ave\.|road|rd\.|building|bldg|apartment|apt|floor|unit|شارع|عمارة|دور|شقة)\b",
    re.IGNORECASE,
)


def mask_pii(text: str) -> str:
    """Mask emails, phone numbers, order IDs, card numbers, and addresses before sending to Langfuse."""
    if not isinstance(text, str):
        return text
    masked = _EMAIL_PATTERN.sub("[EMAIL]", text)
    masked = _PHONE_PATTERN.sub("[PHONE]", masked)
    masked = _ORDER_ID_PATTERN.sub("[ORDER_ID]", masked)
    masked = _CARD_PATTERN.sub("[CARD]", masked)
    # Basic address detection: if contains address keywords, mask the whole line
    if _ADDRESS_KEYWORDS.search(masked):
        lines = masked.split("\n")
        masked_lines = []
        for line in lines:
            if _ADDRESS_KEYWORDS.search(line):
                masked_lines.append("[ADDRESS]")
            else:
                masked_lines.append(line)
        masked = "\n".join(masked_lines)
    return masked


def mask_pii_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively mask PII in dictionary values."""
    masked = {}
    for key, value in data.items():
        if isinstance(value, str):
            masked[key] = mask_pii(value)
        elif isinstance(value, dict):
            masked[key] = mask_pii_dict(value)
        elif isinstance(value, list):
            masked[key] = [mask_pii(v) if isinstance(v, str) else v for v in value]
        else:
            masked[key] = value
    return masked


def init_langfuse(public_key: str, secret_key: str, host: str) -> bool:
    """Initialize Langfuse client. Returns True if successful, False if disabled or failed."""
    global _LANGFUSE_ENABLED
    
    if not public_key or not secret_key or not host:
        return False
    
    # Set environment variables for Langfuse SDK to pick up
    import os
    os.environ["LANGFUSE_PUBLIC_KEY"] = public_key
    os.environ["LANGFUSE_SECRET_KEY"] = secret_key
    os.environ["LANGFUSE_HOST"] = host
    
    try:
        # The Python SDK v5 uses OpenTelemetry internally - no manual setup needed!
        # Just importing langfuse and using @observe handles everything
        from langfuse import get_client
        
        # Test auth
        client = get_client()
        auth_result = client.auth_check()
        if not auth_result:
            log = get_logger(__name__)
            log.error("langfuse_auth_failed", message="Langfuse authentication failed")
            return False
        
        _LANGFUSE_ENABLED = True
        log = get_logger(__name__)
        log.info("langfuse_enabled", host=host)
        return True
    except ImportError:
        return False
    except Exception as exc:
        log = get_logger(__name__)
        log.error("langfuse_init_failed", error=str(exc), exception_type=type(exc).__name__)
        return False


def get_langfuse() -> bool:
    """Check if Langfuse tracing is enabled."""
    return _LANGFUSE_ENABLED


def flush_langfuse() -> None:
    """Flush pending Langfuse events. Call on shutdown."""
    if _LANGFUSE_ENABLED:
        try:
            from langfuse import get_client
            client = get_client()
            client.flush()
        except Exception:
            pass  # Silent failure on shutdown
