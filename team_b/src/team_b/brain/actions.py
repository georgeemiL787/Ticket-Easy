"""Actions that change something in the shop: return, refund, cancel, address change.

OWNER: Track A. SAFETY-CRITICAL: needs a second reviewer.

handle() runs an action request through the gates in this fixed order, and stops at the first one that fails:
  required details -> identity verified -> order read from the shop -> ownership -> permission gate (also checked early,
  so a person-only tool is refused before anything is asked) -> risk screen answered this turn -> rule check
  (check_action) -> customer confirmation (or a human's approval) -> the rule check again -> execute once -> verify.
The rule checker answers:  allow -> ask the customer to confirm (for the kinds the tenant lists);  require_human -> the
proposal waits for a person (handoff approval_required);  deny -> a final refusal or a handoff policy_denied, never an
execution.  A person's approval can turn require_human into allow, never a deny (see the rule checker and
ActionProposal.execution_authorized).
on_confirmation() runs when the customer says yes: it reads the order again, checks the rules again with the fresh
facts, and only then executes. Whatever came back is verified before anything is called done:
  success + audit id + every promised output field + a reference id for creates -> action_done;
  a clear error -> action_failed (counted; handoff at the limit); anything unclear -> handoff unverified_result, and the
  reply says neither done nor failed. Writes are never retried.
Every failure of a check is fail-closed: no rule answer, no safety answer, no shop answer means no write and a handoff.
"""

import asyncio
import copy
import hashlib
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any, Literal

from team_b.brain import gates, identity
from team_b.brain.composer import PLACEHOLDERS, default_composer
from team_b.brain.lookup import load_own_order
from team_b.brain.redaction import redact
from team_b.brain.slots import resolve_arguments
from team_b.brain.turn import PlannedIntent, Step, TurnContext, locale_of
from team_b.contracts.base import OperationKind
from team_b.contracts.errors import UpstreamError
from team_b.contracts.policy import CheckActionRequest, HumanApproval, IdentityContext, PolicyDecision, ToolContext
from team_b.contracts.tools import ToolCallRequest, ToolResult, ToolSpec
from team_b.domain.actions import ActionProposal, ActionState, ExecutionNotAuthorizedError
from team_b.domain.decision import Decision, EscalationReason
from team_b.domain.tenant import IntentSpec
from team_b.domain.trace import PolicyRecord, ToolCallRecord
from team_b.domain.understanding import NLUResult
from team_b.observability import get_logger

log = get_logger(__name__)
Actor = Literal["customer", "human"]


def _handoff(reason: EscalationReason, why: str, *, citations: tuple[str, ...] = ()) -> Step:
    return Step(
        Decision.HANDOFF,
        reason=why,
        reply_key=f"handoff_{reason.value}",
        escalation=reason,
        awaiting="human",
        citations=citations,
    )


# ---- verifying what the shop answered ----


class Verdict(StrEnum):
    VERIFIED = "verified"  # the change happened and the shop proved it
    FAILED = "failed"  # the shop clearly refused or could not be reached: nothing changed
    UNCERTAIN = "uncertain"  # it may or may not have happened: a person must look


def verify_result(result: ToolResult, tool: ToolSpec | None) -> Verdict:
    """success AND audit id AND every required output field AND a reference id for creates; unclear is UNCERTAIN."""
    if result.write_may_have_applied:
        return Verdict.UNCERTAIN
    if result.status == "error":
        return Verdict.FAILED
    if tool is None or not result.audit_id:
        return Verdict.UNCERTAIN
    if any(field not in result.data for field in tool.output_schema.get("required", ())):
        return Verdict.UNCERTAIN
    if tool.operation_kind == "create" and not result.reference_id:
        return Verdict.UNCERTAIN
    return Verdict.VERIFIED


# ---- asking the rule checker ----


async def check_policy(
    ctx: TurnContext, tool: ToolSpec, arguments: dict[str, Any], approval: HumanApproval | None = None
) -> PolicyDecision | None:
    """The rule checker's answer for this action, or None when it cannot answer (the caller hands off, writes nothing).

    The facts are the ones read from the shop this turn, never what the customer said. A retryable failure is retried
    once (a check changes nothing, so asking again is safe)."""
    gate = ctx.deps.policy
    if gate is None:
        ctx.errors.append("no rule checker is configured")
        return None
    session = ctx.session
    request = CheckActionRequest(
        request_id=uuid.uuid4().hex,
        tenant_id=ctx.tenant.tenant_id,
        conversation_id=session.conversation_id,
        action=tool.capability,
        tool=ToolContext(
            name=tool.name,
            operation_kind=tool.operation_kind,
            side_effects=tool.operation_kind != "read",
            personal_data=tool.exposes_personal_data,
            risk=tool.risk,
        ),
        identity=IdentityContext(
            verified=session.identity.verified, customer_id=session.identity.customer_id, method=session.identity.method
        ),
        facts=copy.deepcopy(session.facts),
        arguments=dict(arguments),
        risk_categories=tuple(session.risk_categories),
        as_of=ctx.deps.clock.today(),
        resource_tenant_id=ctx.tenant.tenant_id,
        human_approval=approval,
    )
    for _ in range(2):
        try:
            decision = await gate.check_action(request)
        except UpstreamError as exc:
            ctx.errors.append(f"rule checker failed: {exc.code}")
            if not exc.retryable:
                return None
            continue
        except NotImplementedError:
            ctx.errors.append("rule checker is not built")
            return None
        ctx.policy.append(
            PolicyRecord(
                request_id=decision.request_id,
                action=decision.action or tool.capability,
                decision=decision.decision,
                reason_code=decision.reason_code,
                citations=decision.citations,
            )
        )
        return decision
    return None


# ---- what to say for each answer ----


def _deny_step(ctx: TurnContext, decision: PolicyDecision) -> Step:
    """A deny is never executed. A final one (the tenant's final_deny_rules) is told as a refusal; any other goes to a
    person (when the tenant escalates denies), with the rule's own wording and its citation."""
    locale = locale_of(ctx)
    message = default_composer().policy_message(locale, decision)
    denying = [o.rule_id for o in decision.rule_outcomes if o.effect_applied == "deny"]
    final = (
        decision.reason_code == "RULE_BLOCKED"
        and bool(denying)
        and all(rule in ctx.tenant.escalation.final_deny_rules for rule in denying)
    )
    why = f"policy said no ({decision.reason_code}: {', '.join(denying) or decision.rationale})"
    if final or not ctx.tenant.escalation.escalate_on_deny:
        ctx.session.active_intent = None  # told no: a later message does not start the request again
        return Step(
            Decision.REFUSE,
            reason=why,
            reply_key="policy_refusal",
            values={"message": message},
            citations=decision.citations,
        )
    return Step(
        Decision.HANDOFF,
        reason=why,
        reply_key="policy_refusal",
        values={"message": message},
        citations=decision.citations,
        escalation=EscalationReason.POLICY_DENIED,
        awaiting="human",
    )


def _needs_human_step(decision: PolicyDecision) -> Step:
    return _handoff(
        EscalationReason.APPROVAL_REQUIRED,
        f"a person must approve this ({decision.reason_code}: {decision.rationale})",
        citations=decision.citations,
    )


def _confirm_step(ctx: TurnContext, tool: ToolSpec, proposal: ActionProposal, decision: PolicyDecision) -> Step:
    """Ask the customer to confirm, stating what will be done with the real values (amounts come from the shop)."""
    composer, locale = default_composer(), locale_of(ctx)
    key = f"confirm_action_{tool.capability}"
    if not composer.has(locale, key):
        key = "confirm_action_default"
    args = proposal.arguments
    pool = {
        "order_id": str(args.get("order_id", "")),
        "amount": str(args.get("amount", "")),
        "address": str(args.get("new_address", "")),
        "action": tool.title or tool.capability,
    }
    return Step(
        Decision.CONFIRM,
        reason=f"{tool.name} is allowed ({decision.reason_code}); waiting for the customer's yes",
        reply_key=key,
        values={name: pool[name] for name in PLACEHOLDERS.get(key, frozenset())},
        citations=decision.citations,
        awaiting="confirmation",
    )


# ---- the flow ----


async def handle(ctx: TurnContext, intent_spec: IntentSpec) -> Step:
    """The next step of an action request: a question, a confirmation, a refusal or a handoff."""
    from team_b.brain.stages import _tools, ask_for_details  # imported here: stages imports this module

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

    if (step := await ask_for_details(ctx, planned)) is not None:
        return step
    if (step := await load_own_order(ctx, intent_spec, tools)) is not None:
        return step
    if ctx.risk is None:  # the screen must have answered in this very turn
        return _handoff(EscalationReason.DEPENDENCY_UNAVAILABLE, "the safety screen did not answer this turn")

    resolution = resolve_arguments(intent_spec, tool, ctx.session, ctx.session.facts)
    if resolution.missing or resolution.missing_facts or resolution.unsourced:
        gaps = ", ".join([*resolution.missing, *resolution.missing_facts, *resolution.unsourced])
        return _handoff(EscalationReason.UNSUPPORTED, f"the request cannot be filled: {gaps}")
    decision = await check_policy(ctx, tool, resolution.arguments)
    if decision is None:
        return _handoff(EscalationReason.DEPENDENCY_UNAVAILABLE, "the rule checker did not answer")
    return await _act_on(ctx, intent_spec, tool, decision)


async def _act_on(ctx: TurnContext, intent_spec: IntentSpec, tool: ToolSpec, decision: PolicyDecision) -> Step:
    """What the first rule check means: refuse or hand off a deny, hold for a person, or ask the customer to confirm."""
    if decision.decision == "deny":
        return _deny_step(ctx, decision)
    try:
        proposal = await COORDINATOR.propose(ctx, intent_spec)
    except ProposalError as exc:
        return _handoff(EscalationReason.UNSUPPORTED, str(exc))
    proposal.policy_decisions.append(decision)
    if decision.decision == "require_human":
        proposal.transition(ActionState.AWAITING_HUMAN, at=ctx.now, note=decision.reason_code)
        return _needs_human_step(decision)
    if tool.operation_kind in ctx.tenant.permissions.confirm_operation_kinds:
        proposal.transition(ActionState.AWAITING_CONFIRMATION, at=ctx.now, note=decision.reason_code)
        ctx.session.pending_action_id = proposal.proposal_id
        return _confirm_step(ctx, tool, proposal, decision)
    proposal.transition(ActionState.APPROVED, at=ctx.now, note=decision.reason_code)
    return await _execute_and_verify(ctx, proposal, tool)


def _block(ctx: TurnContext, proposal: ActionProposal, note: str, step: Step) -> Step:
    """The proposal can no longer go ahead: close it (nothing was written) and say what the step says."""
    if not proposal.is_terminal:
        try:
            proposal.transition(ActionState.BLOCKED, at=ctx.now, note=note)
        except ValueError:
            log.warning("proposal_not_blockable", state=proposal.state.value)
    ctx.proposal_ids.append(proposal.proposal_id)
    if ctx.session.pending_action_id == proposal.proposal_id:
        ctx.session.pending_action_id = None
    return step


async def on_confirmation(ctx: TurnContext, nlu: NLUResult) -> Step:
    """The customer said yes to the pending action: read the order again, check the rules again, then execute once."""
    from team_b.brain.stages import _tools  # imported here: stages imports this module

    session = ctx.session
    proposal = next((a for a in session.actions if a.proposal_id == session.pending_action_id), None)
    if proposal is None or proposal.state is not ActionState.AWAITING_CONFIRMATION:
        session.pending_action_id = None
        return Step(
            Decision.CLARIFY, reason="no action is waiting for a yes", reply_key="ask_rephrase", awaiting="detail"
        )
    ctx.proposal_ids.append(proposal.proposal_id)
    tools = await _tools(ctx)
    tool = tools.get(proposal.tool) if tools is not None else None
    spec = next((s for s in ctx.tenant.intents.values() if s.action_tool == proposal.tool), None)
    if tools is None:
        return _block(ctx, proposal, "no tool list", _handoff(EscalationReason.DEPENDENCY_UNAVAILABLE, "no tool list"))
    if tool is None or spec is None:
        why = f"the shop does not publish {proposal.tool}"
        return _block(ctx, proposal, why, _handoff(EscalationReason.CAPABILITY_MISSING, why))
    if not (gate := gates.check_tool(ctx.tenant, tool)).allowed:
        why = f"permission gate: {gate.reason}: {gate.detail}"
        return _block(ctx, proposal, why, _handoff(EscalationReason.UNSUPPORTED, why))

    if (step := await load_own_order(ctx, spec, tools)) is not None:
        # Could not read the order again, or it is not theirs. Nothing is executed. A read that merely failed leaves the
        # proposal waiting so the customer can say yes again; a handoff closes it.
        return _block(ctx, proposal, step.reason, step) if step.decision is Decision.HANDOFF else step
    if ctx.risk is None:
        why = "the safety screen did not answer this turn"
        return _block(ctx, proposal, why, _handoff(EscalationReason.DEPENDENCY_UNAVAILABLE, why))

    resolution = resolve_arguments(spec, tool, session, session.facts)
    if resolution.arguments != proposal.arguments:  # the facts moved: the customer confirmed something else
        step = Step(
            Decision.CLARIFY, reason="the order changed since the customer was asked", reply_key="action_changed"
        )
        return _block(ctx, proposal, "the details changed after the confirmation was asked", step)
    decision = await check_policy(ctx, tool, proposal.arguments)
    if decision is None:
        why = "the rule checker did not answer the renewed check"
        return _block(ctx, proposal, why, _handoff(EscalationReason.DEPENDENCY_UNAVAILABLE, why))
    proposal.policy_decisions.append(decision)
    if decision.decision == "deny":
        return _block(ctx, proposal, f"denied on the renewed check ({decision.reason_code})", _deny_step(ctx, decision))
    session.pending_action_id = None
    if decision.decision == "require_human":
        proposal.transition(ActionState.AWAITING_HUMAN, at=ctx.now, note=f"renewed check: {decision.reason_code}")
        return _needs_human_step(decision)
    proposal.transition(ActionState.APPROVED, at=ctx.now, note="the customer confirmed; the renewed check allows it")
    return await _execute_and_verify(ctx, proposal, tool)


async def _execute_and_verify(ctx: TurnContext, proposal: ActionProposal, tool: ToolSpec) -> Step:
    """Execute once, then say what the shop proved: done, failed, or "a colleague will check"."""
    session = ctx.session
    try:
        result = await COORDINATOR.execute(ctx, proposal)
    except ExecutionNotAuthorizedError as exc:  # cannot happen after an allow; if it does, fail closed
        log.error("execution_refused_after_allow", reason=str(exc))
        return _block(ctx, proposal, str(exc), _handoff(EscalationReason.UNSUPPORTED, f"execution refused: {exc}"))
    citations = proposal.policy_decisions[-1].citations
    verdict = verify_result(result, tool)
    if verdict is Verdict.VERIFIED:
        proposal.transition(ActionState.CONFIRMED, at=ctx.now, note=f"verified: {result.reference_id}")
        session.write_failures = 0
        session.active_intent = None  # done: a later "ok" must not start the same request again
        return Step(
            Decision.EXECUTE,
            reason=f"{tool.name} done and verified ({result.reference_id})",
            reply_key="action_done",
            values={"reference": result.reference_id or ""},
            citations=citations,
        )
    if verdict is Verdict.FAILED:
        proposal.transition(ActionState.FAILED, at=ctx.now, note=result.error_code or "failed")
        session.write_failures += 1
        if session.write_failures >= ctx.tenant.escalation.max_tool_failures:
            return _handoff(
                EscalationReason.REPEATED_TOOL_FAILURE, f"{tool.name} failed {session.write_failures} times"
            )
        session.active_intent = None
        return Step(
            Decision.REFUSE,
            reason=f"{tool.name} failed ({result.error_code}); nothing changed",
            reply_key="action_failed",
        )
    return _handoff(  # the proposal stays EXECUTED: neither confirmed nor failed
        EscalationReason.UNVERIFIED_RESULT,
        f"{tool.name} came back unclear ({result.error_code or 'incomplete success'}); not claimed as done or failed",
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
