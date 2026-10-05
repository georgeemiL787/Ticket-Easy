"""Logging: structlog, JSON or console, with the request ids attached to every line automatically."""

import logging
import sys

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, merge_contextvars
from structlog.typing import FilteringBoundLogger, Processor

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
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
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


def clear_context() -> None:
    clear_contextvars()
