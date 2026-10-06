"""Reading from the shop: one call helper that retries once, records every attempt, and never hides a failure.

OWNER: Track A.

call_read() is for read tools only (a write is never retried: see brain/actions.py). It tries the call, tries once more
when the failure says it may work next time (retryable), and records each attempt in the turn's tool calls. It returns
what happened; the caller decides what to tell the customer.
"""

import time
import uuid
from dataclasses import dataclass
from typing import Any

from team_b.brain.redaction import redact
from team_b.brain.turn import Step, TurnContext
from team_b.contracts.errors import UpstreamError
from team_b.contracts.tools import ToolCallRequest, ToolResult, ToolSpec
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.trace import ToolCallRecord

ATTEMPTS = 2  # the call, and one retry in the same turn


@dataclass(frozen=True)
class ReadOutcome:
    """What a read came to. result is the shop's answer (success or error); None when the shop could not be reached."""

    result: ToolResult | None
    error_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.result is not None and self.result.status == "success"

    @property
    def data(self) -> dict[str, Any]:
        return self.result.data if self.result is not None and self.result.status == "success" else {}


def _record(
    ctx: TurnContext, request: ToolCallRequest, started: float, result: ToolResult | None, code: str | None
) -> None:
    ctx.tool_calls.append(
        ToolCallRecord(
            request_id=request.request_id,
            tool=request.tool,
            operation_kind="read",
            arguments={k: redact(v) if isinstance(v, str) else v for k, v in request.arguments.items()},
            status="success" if result is not None and result.status == "success" else "error",
            error_code=code,
            audit_id=result.audit_id if result is not None else None,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
    )


async def call_read(ctx: TurnContext, tool: str, arguments: dict[str, Any]) -> ReadOutcome:
    """Call a read tool; retry once if the failure is retryable. Never raises for a shop failure."""
    capabilities = ctx.deps.capabilities
    if capabilities is None:
        return ReadOutcome(None, "NO_SHOP")
    outcome = ReadOutcome(None, "NO_ATTEMPT")
    for _ in range(ATTEMPTS):
        request = ToolCallRequest(
            request_id=uuid.uuid4().hex, tool=tool, arguments=arguments, idempotency_key=uuid.uuid4().hex
        )
        started = time.perf_counter()
        retryable = False
        try:
            result = await capabilities.call_tool(ctx.tenant.tenant_id, request)
        except UpstreamError as exc:
            outcome, retryable = ReadOutcome(None, exc.code), exc.retryable
            _record(ctx, request, started, None, exc.code)
        else:
            code = result.error_code if result.status == "error" else None
            _record(ctx, request, started, result, code)
            outcome, retryable = ReadOutcome(result, code), result.retryable
            if result.status == "success":
                return outcome
        ctx.errors.append(f"{tool} failed: {outcome.error_code}")
        if outcome.error_code == "TOOL_NOT_PUBLISHED" and ctx.deps.registry is not None:
            ctx.deps.registry.drop(ctx.tenant.tenant_id)  # the remembered tool list is out of date
        if not retryable:
            break
    return outcome


def output_complete(tool: ToolSpec | None, data: dict[str, Any]) -> bool:
    """Does the answer carry every field the tool promises? An answer with fields missing is not trusted."""
    required = tool.output_schema.get("required", ()) if tool is not None else ()
    return all(field in data for field in required)


def failed_read(ctx: TurnContext, what: str) -> Step:
    """A read failed even after the retry: say so honestly (never guess), count it, hand off at the limit."""
    session = ctx.session
    session.tool_failures += 1
    if session.tool_failures >= ctx.tenant.escalation.max_tool_failures:
        return Step(
            Decision.HANDOFF,
            reason=f"{what} failed {session.tool_failures} times",
            reply_key="handoff_repeated_tool_failure",
            escalation=EscalationReason.REPEATED_TOOL_FAILURE,
            awaiting="human",
        )
    return Step(Decision.CLARIFY, reason=f"{what} failed; told the customer honestly", reply_key="lookup_failed")
