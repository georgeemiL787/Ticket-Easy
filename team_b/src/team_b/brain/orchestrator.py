"""The orchestrator: handles one customer message or one human action at a time.

A customer message runs through the turn pipeline (brain/pipeline.py): load, handed_off_check, understand, risk_screen,
human_request, pending_confirmation, merge, frustration, plan, handler, queue, handoff, finish. Greetings and thanks are
answered from templates, a request for a person or a risky message becomes a handoff, and requests that need the
knowledge, lookup or action handlers get a placeholder "tell me more" until those are built (they plug in through
`handlers`, one per intent kind). Memory is real: one session per conversation, the last history_max_turns messages
kept, older ones folded into history_summary, the redacted transcript stored in the traces.

The human methods share one signature so the scenario runner can call them by name:
(tenant_id, case_id, *, actor, text=""). They are built with the handoff step.
"""

from collections.abc import Mapping
from typing import Any, Protocol

from team_b.brain import actions, approval, knowledge, lookup
from team_b.brain.nlu import NLU, RuleBasedNLU
from team_b.brain.pipeline import run_turn
from team_b.brain.registry import CapabilityRegistry
from team_b.brain.rewrite import Rewriter
from team_b.brain.stages import handoff_handler, smalltalk_handler
from team_b.brain.summarizer import HistorySummarizer, TemplateHistorySummarizer
from team_b.brain.turn import MAX_QUEUED_RUNS, Deps, Handler, PlannedIntent, Step, TurnContext
from team_b.domain.handoff import CaseStatus, HandoffCase, NotManagerError
from team_b.domain.reply import AgentReply
from team_b.domain.session import Message
from team_b.domain.tenant import TenantRegistry
from team_b.ports import (
    CapabilityClient,
    CaseStore,
    Clock,
    EvidenceProvider,
    LLMClient,
    NotFoundError,
    PolicyGate,
    SessionConflictError,
    SessionStore,
    TraceStore,
)

CLARIFY_TEXT = "Could you tell me a little more about what you need help with?"  # the English ask_rephrase text


async def _knowledge(ctx: TurnContext, planned: PlannedIntent) -> Step:
    return await knowledge.answer(ctx)


async def _lookup(ctx: TurnContext, planned: PlannedIntent) -> Step:
    return await lookup.answer(ctx, ctx.tenant.intents[planned.name])


async def _action(ctx: TurnContext, planned: PlannedIntent) -> Step:
    return await actions.handle(ctx, ctx.tenant.intents[planned.name])


# One handler per intent kind. Knowledge is Track B's module, lookups and actions are Track A's.
DEFAULT_HANDLERS: Mapping[str, Handler] = {
    "smalltalk": smalltalk_handler,
    "handoff": handoff_handler,
    "knowledge": _knowledge,
    "lookup": _lookup,
    "action": _action,
}


class Publisher(Protocol):
    """Where messages for the customer go to reach an open chat page (the in-process event hub)."""

    def publish(self, tenant_id: str, conversation_id: str, event: dict[str, Any]) -> int: ...


class Orchestrator:
    def __init__(
        self,
        *,
        clock: Clock,
        tenants: TenantRegistry,
        sessions: SessionStore,
        traces: TraceStore,
        cases: CaseStore,
        summarizer: HistorySummarizer | None = None,
        nlu: NLU | None = None,
        evidence: EvidenceProvider | None = None,
        handlers: Mapping[str, Handler] | None = None,
        capabilities: CapabilityClient | None = None,
        rewriter: Rewriter | None = None,
        llm: LLMClient | None = None,
        events: Publisher | None = None,
        registry: CapabilityRegistry | None = None,
        policy: PolicyGate | None = None,
        max_queued_runs: int | None = None,
    ) -> None:
        self._tenants = tenants
        self._events = events
        self._clock = clock
        self._deps = Deps(
            clock=clock,
            sessions=sessions,
            traces=traces,
            cases=cases,
            evidence=evidence,
            nlu=nlu or RuleBasedNLU(),
            summarizer=summarizer or TemplateHistorySummarizer(),
            handlers={**DEFAULT_HANDLERS, **(handlers or {})},
            capabilities=capabilities,
            rewriter=rewriter,
            llm=llm,
            registry=registry or (CapabilityRegistry(capabilities, clock) if capabilities is not None else None),
            policy=policy,
            max_queued_runs=MAX_QUEUED_RUNS if max_queued_runs is None else max_queued_runs,
        )

    async def handle_turn(
        self, tenant_id: str, conversation_id: str, text: str, *, request_id: str | None = None, channel: str = "web"
    ) -> AgentReply:
        """Answer one customer message. Raises UnknownTenantError for a tenant that is not configured.

        If another writer saved the session first (a second process on the same database), the turn is redone once on
        the fresh session; a second conflict is raised to the caller. Nothing is stored for a failed attempt."""
        tenant = self._tenants.get(tenant_id)
        try:
            return await run_turn(self._deps, tenant, conversation_id, text, request_id=request_id, channel=channel)
        except SessionConflictError:
            return await run_turn(self._deps, tenant, conversation_id, text, request_id=request_id, channel=channel)

    async def push_to_customer(
        self, tenant_id: str, conversation_id: str, text: str, *, role: str = "human_agent"
    ) -> Message:
        """Put a message in the customer's outbox and send it live to their open chat pages.

        Used when a person writes to the customer (built into the human actions by the handoff step)."""
        self._tenants.get(tenant_id)
        deps = self._deps
        async with deps.sessions.lock(tenant_id, conversation_id):
            session = await deps.sessions.load(tenant_id, conversation_id)
            if session is None:
                raise NotFoundError(f"conversation {conversation_id} does not exist")
            message = Message(role=role, text=text, at=self._clock.now())  # type: ignore[arg-type]
            session.outbox.append(message)
            await deps.sessions.save(session)
        if self._events is not None:
            self._events.publish(tenant_id, conversation_id, message.model_dump(mode="json"))
        return message

    # ---- human actions. Final signatures; Track B finishes them, Track A fills human_decide. ----

    async def _case(self, case_id: str) -> HandoffCase:
        for tenant_id in self._tenants.tenant_ids():
            found = await self._deps.cases.get(tenant_id, case_id)
            if found is not None:
                return found
        raise NotFoundError(f"case {case_id} does not exist")

    async def _reopen_conversation(self, case: HandoffCase) -> None:
        """The case is over: the assistant owns the conversation again, its memory (slots, identity) intact."""
        deps = self._deps
        async with deps.sessions.lock(case.tenant_id, case.conversation_id):
            session = await deps.sessions.load(case.tenant_id, case.conversation_id)
            if session is None or session.handoff_case_id != case.case_id:
                return  # gone, or already linked to a newer case
            session.status, session.handoff_case_id, session.handoff_notice_sent = "active", None, False
            session.awaiting = None
            session.updated_at = self._clock.now()
            await deps.sessions.save(session)

    async def claim(self, case_id: str, agent: str) -> None:
        """The agent takes the case: from now on they own the conversation."""
        case = await self._case(case_id)
        case.transition(CaseStatus.CLAIMED, actor=agent, at=self._clock.now())
        await self._deps.cases.save(case)

    async def assign(self, case_id: str, manager: str, assignee: str, *, verified_manager: bool = False) -> None:
        """A manager gives the case to someone (or takes it from the person who has it).

        verified_manager: the caller already checked that this is a signed-in manager or admin (the API does); otherwise
        the name must be in the tenant's `managers`."""
        case = await self._case(case_id)
        if not verified_manager and manager not in self._tenants.get(case.tenant_id).managers:
            raise NotManagerError(f"{manager} is not a manager of {case.tenant_id}")
        case.reassign(assignee, by=manager, at=self._clock.now())
        await self._deps.cases.save(case)

    async def release(self, case_id: str, agent: str) -> None:
        """The agent gives the case back to the queue."""
        case = await self._case(case_id)
        case.require_claimer(agent)
        case.transition(CaseStatus.OPEN, actor=agent, at=self._clock.now())
        await self._deps.cases.save(case)

    async def human_reply(self, case_id: str, agent: str, text: str) -> None:
        """The agent writes to the customer: recorded on the case and delivered live through the outbox."""
        case = await self._case(case_id)
        case.require_claimer(agent)
        case.add_event(actor=agent, kind="reply", at=self._clock.now(), note=text)
        await self._deps.cases.save(case)
        await self.push_to_customer(case.tenant_id, case.conversation_id, text)

    async def human_decide(self, case_id: str, agent: str, approve: bool, note: str | None = None) -> None:
        """Approve or reject the action waiting on this case. It never overrides a deny (see brain/approval.py)."""
        case = await self._case(case_id)
        message = await approval.decide_case(self._deps, self._tenants.get(case.tenant_id), case, agent, approve, note)
        if message is not None:
            await self.push_to_customer(case.tenant_id, case.conversation_id, message, role="agent")

    async def return_to_agent(self, case_id: str, agent: str, note: str | None = None) -> None:
        """The agent hands the conversation back to the assistant."""
        case = await self._case(case_id)
        case.require_claimer(agent)
        case.transition(CaseStatus.RETURNED_TO_AGENT, actor=agent, at=self._clock.now(), note=note or "")
        await self._deps.cases.save(case)
        await self._reopen_conversation(case)

    async def resolve(self, case_id: str, agent: str, note: str | None = None) -> None:
        """The agent closes the case."""
        case = await self._case(case_id)
        case.require_claimer(agent)
        case.transition(CaseStatus.RESOLVED, actor=agent, at=self._clock.now(), note=note or "")
        await self._deps.cases.save(case)
        await self._reopen_conversation(case)
