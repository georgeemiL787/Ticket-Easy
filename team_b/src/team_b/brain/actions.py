"""Actions that change something in the shop: return, refund, cancel, address change.

OWNER: Track A (fills this module).

handle() runs an action request through the gates; on_confirmation() continues after the customer says yes (re-check,
execute once, verify). Built so far: the action's tool must be published by the shop and pass the permission gate
(brain/gates.py), the customer must be verified, and the details are collected. The rest of the gate order (facts,
ownership, rules, confirmation) and execution come in later steps; until then on_confirmation() never executes anything.
"""

import asyncio
import copy
import hashlib
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

from team_b.brain import gates, identity
from team_b.brain.redaction import redact
from team_b.brain.slots import resolve_arguments
from team_b.brain.turn import PlannedIntent, Step, TurnContext
from team_b.contracts.base import OperationKind
from team_b.contracts.errors import UpstreamError
from team_b.contracts.policy import HumanApproval
from team_b.contracts.tools import ToolCallRequest, ToolResult
from team_b.domain.actions import ActionProposal, ActionState, ExecutionNotAuthorizedError
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.tenant import IntentSpec
from team_b.domain.trace import PolicyRecord, ToolCallRecord
from team_b.domain.understanding import NLUResult

Actor = Literal["customer", "human"]


def _handoff(reason: EscalationReason, why: str) -> Step:
    return Step(Decision.HANDOFF, reason=why, reply_key=f"handoff_{reason.value}", escalation=reason, awaiting="human")


async def handle(ctx: TurnContext, intent_spec: IntentSpec) -> Step:
    """The next step of an action request: a question, a confirmation, a refusal or a handoff."""
    from team_b.brain.stages import _tools, ask_for_details, placeholder_handler  # imported here: stages imports us

    planned = ctx.plan or PlannedIntent(name="action", kind="action")
    tools = await _tools(ctx)
    tool = tools.get(intent_spec.action_tool or "") if tools is not None else None
    if tool is not None and not (gate := gates.check_tool(ctx.tenant, tool)).allowed:
        return _handoff(EscalationReason.UNSUPPORTED, f"permission gate: {gate.reason}: {gate.detail}")

    if tool is None or tool.requires_identity:
        if (step := await identity.ensure_verified(ctx)) is not None:
            return step
    # Checked after identity: an unverified customer learns nothing about what the shop offers.
    if tools is None:
        return _handoff(EscalationReason.DEPENDENCY_UNAVAILABLE, "the shop's tool list is not available")
    if tool is None:
        return _handoff(EscalationReason.CAPABILITY_MISSING, f"the shop does not publish {intent_spec.action_tool}")
    return await ask_for_details(ctx, planned) or await placeholder_handler(ctx, planned)


async def on_confirmation(ctx: TurnContext, nlu: NLUResult) -> Step:
    """The customer answered yes to a pending action. Never executes until the checked path is built."""
    return Step(
        Decision.CLARIFY,
        reason="the customer confirmed; executing actions is not built yet",
        reply_key="ask_rephrase",
        awaiting="detail",
    )


# ---- proposals and the "do it once" key (safety-critical: needs a second reviewer) ----


class ProposalError(ValueError):
    """The proposal cannot be made: the tool is unknown or a required argument has no value."""


def idempotency_key_for(tenant_id: str, conversation_id: str, proposal_id: str) -> str:
    """The do-it-once key: the same proposal always has the same key, so the shop can recognise a repeat."""
    return hashlib.sha256(f"{tenant_id}|{conversation_id}|{proposal_id}".encode()).hexdigest()


def approval_id_of(approval: HumanApproval) -> str:
    """The id sent to the shop for a human approval (the case it was given on)."""
    return approval.case_id


def _unknown_outcome(code: str, message: str) -> ToolResult:
    return ToolResult(status="error", error_code=code, error_message=message, write_may_have_applied=True)


def _result_of_failure(exc: UpstreamError) -> ToolResult:
    """The shop could not be reached. BACKEND_UNAVAILABLE means the request never got in (nothing changed); anything
    else (a timeout, an unknown code) may have happened, so it is reported as uncertain and never retried."""
    clear = exc.code == "BACKEND_UNAVAILABLE"
    return ToolResult(
        status="error",
        error_code=exc.code,
        error_message=exc.message,
        retryable=False,
        write_may_have_applied=not clear,
    )


class _Guard:
    """One lock per do-it-once key, removed again when nobody uses it."""

    def __init__(self) -> None:
        self._locks: dict[str, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        lock, users = self._locks.get(key, (asyncio.Lock(), 0))
        self._locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            lock, users = self._locks[key]
            if users <= 1:
                del self._locks[key]
            else:
                self._locks[key] = (lock, users - 1)


class ActionCoordinator:
    """Creates action proposals and executes them, once.

    propose() records what is about to be done: the arguments (from the customer's slots, the shop's facts and the
    verified identity, see brain/slots.py), a copy of the facts, and the do-it-once key. execute() is the only place
    that calls a write tool. It refuses unless proposal.execution_authorized() (an allow, or require_human with a human
    approval, and never after any deny), moves the proposal to EXECUTED BEFORE calling the shop (so a second yes sees
    it), sends the key, the policy request id, the approval id and the actor, stores the result, and never retries:
    whatever comes back (success, a clear error or an unclear one) is the one result the proposal will ever have.
    """

    def __init__(self) -> None:
        self._guard = _Guard()

    async def propose(self, ctx: TurnContext, intent_spec: IntentSpec) -> ActionProposal:
        from team_b.brain.stages import _tools  # imported here: stages imports this module

        tools = await _tools(ctx)
        tool = tools.get(intent_spec.action_tool or "") if tools is not None else None
        if tool is None:
            raise ProposalError(f"the shop does not publish {intent_spec.action_tool}")
        session = ctx.session
        resolution = resolve_arguments(intent_spec, tool, session, session.facts)
        lacking = [a for a in tool.input_schema.get("required", ()) if a not in resolution.arguments]
        if lacking:
            raise ProposalError(f"no value for required argument(s): {', '.join(lacking)}")
        proposal_id = uuid.uuid4().hex
        proposal = ActionProposal(
            proposal_id=proposal_id,
            tool=tool.name,
            capability=tool.capability,
            arguments=dict(resolution.arguments),
            facts_snapshot=copy.deepcopy(session.facts),
            idempotency_key=idempotency_key_for(ctx.tenant.tenant_id, session.conversation_id, proposal_id),
        )
        session.actions.append(proposal)
        ctx.proposal_ids.append(proposal_id)
        return proposal

    async def execute(self, ctx: TurnContext, proposal: ActionProposal, actor: Actor = "customer") -> ToolResult:
        """Call the write tool once for this proposal and return the one result it will ever have."""
        async with self._guard.hold(proposal.idempotency_key):
            if proposal.state in (ActionState.EXECUTED, ActionState.CONFIRMED):
                ctx.proposal_ids.append(proposal.proposal_id)
                return proposal.result or _unknown_outcome(
                    "OUTCOME_UNKNOWN", "an earlier attempt did not return a result; it is not repeated"
                )
            if proposal.state is not ActionState.APPROVED:
                raise ExecutionNotAuthorizedError(f"a proposal in state {proposal.state.value} is not executed")
            if not proposal.execution_authorized():
                raise ExecutionNotAuthorizedError("no allow (or human approval) on record: refusing to execute")
            if actor == "human" and proposal.human_approval is None:
                raise ExecutionNotAuthorizedError("a person can only execute with a recorded human approval")
            capabilities = ctx.deps.capabilities
            if capabilities is None:
                raise ExecutionNotAuthorizedError("no shop is connected: nothing can be executed")

            decision = proposal.policy_decisions[-1]
            approval_id = (
                approval_id_of(proposal.human_approval)
                if decision.decision == "require_human" and proposal.human_approval is not None
                else None
            )
            kind = await self._operation_kind(ctx, proposal.tool)
            proposal.transition(
                ActionState.EXECUTED, at=ctx.now, note=f"executing as {actor}"
            )  # claimed before the call
            ctx.proposal_ids.append(proposal.proposal_id)
            request = ToolCallRequest(
                request_id=uuid.uuid4().hex,
                tool=proposal.tool,
                arguments=dict(proposal.arguments),
                idempotency_key=proposal.idempotency_key,
                policy_request_id=decision.request_id,
                approval_id=approval_id,
                actor=actor,
            )
            started = time.perf_counter()
            try:
                result = await capabilities.call_tool(ctx.tenant.tenant_id, request)  # exactly one attempt
            except UpstreamError as exc:
                result = _result_of_failure(exc)
            proposal.result = result
            self._record(ctx, proposal, request, kind, result, started)
            return result

    @staticmethod
    async def _operation_kind(ctx: TurnContext, tool_name: str) -> OperationKind:
        """create, update or delete as the shop says; a tool nobody knows is treated as a write (never as a read)."""
        from team_b.brain.stages import _tools

        tools = await _tools(ctx)
        spec = tools.get(tool_name) if tools is not None else None
        return spec.operation_kind if spec is not None and spec.operation_kind != "read" else "update"

    @staticmethod
    def _record(
        ctx: TurnContext, proposal: ActionProposal, request: ToolCallRequest, kind: OperationKind, result: ToolResult,
        started: float,
    ) -> None:  # fmt: skip
        decision = proposal.policy_decisions[-1]
        if all(p.request_id != decision.request_id for p in ctx.policy):
            ctx.policy.append(
                PolicyRecord(
                    request_id=decision.request_id,
                    action=proposal.capability,
                    decision=decision.decision,
                    reason_code=decision.reason_code,
                    citations=decision.citations,
                )  # fmt: skip
            )
        ctx.tool_calls.append(
            ToolCallRecord(
                request_id=request.request_id,
                tool=request.tool,
                operation_kind=kind,
                arguments={k: redact(v) if isinstance(v, str) else v for k, v in request.arguments.items()},
                status=result.status,
                error_code=result.error_code,
                audit_id=result.audit_id,
                latency_ms=(time.perf_counter() - started) * 1000,
                policy_request_id=request.policy_request_id,
                approval_id=request.approval_id,
                actor=request.actor,
            )
        )


COORDINATOR = ActionCoordinator()
