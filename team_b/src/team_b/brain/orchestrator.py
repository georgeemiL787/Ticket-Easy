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

from team_b.brain.nlu import NLU, RuleBasedNLU
from team_b.brain.pipeline import run_turn
from team_b.brain.rewrite import Rewriter
from team_b.brain.stages import handoff_handler, placeholder_handler, slot_handler, smalltalk_handler
from team_b.brain.summarizer import HistorySummarizer, TemplateHistorySummarizer
from team_b.brain.turn import Deps, Handler
from team_b.domain.reply import AgentReply
from team_b.domain.session import Message
from team_b.domain.tenant import TenantRegistry
from team_b.ports import (
    CapabilityClient,
    CaseStore,
    Clock,
    EvidenceProvider,
    NotFoundError,
    SessionConflictError,
    SessionStore,
    TraceStore,
)

CLARIFY_TEXT = "Could you tell me a little more about what you need help with?"  # the English ask_rephrase text

DEFAULT_HANDLERS: Mapping[str, Handler] = {
    "smalltalk": smalltalk_handler,
    "handoff": handoff_handler,
    "knowledge": placeholder_handler,
    "lookup": slot_handler,
    "action": slot_handler,
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
        events: Publisher | None = None,
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

    # ---- human actions (built with the handoff step) ----

    async def claim(self, tenant_id: str, case_id: str, *, actor: str, text: str = "") -> None:
        raise NotImplementedError("human actions arrive with the handoff step")

    async def reply(self, tenant_id: str, case_id: str, *, actor: str, text: str = "") -> None:
        raise NotImplementedError("human actions arrive with the handoff step")

    async def approve(self, tenant_id: str, case_id: str, *, actor: str, text: str = "") -> None:
        raise NotImplementedError("human actions arrive with the handoff step")

    async def reject(self, tenant_id: str, case_id: str, *, actor: str, text: str = "") -> None:
        raise NotImplementedError("human actions arrive with the handoff step")

    async def resolve(self, tenant_id: str, case_id: str, *, actor: str, text: str = "") -> None:
        raise NotImplementedError("human actions arrive with the handoff step")

    async def return_to_agent(self, tenant_id: str, case_id: str, *, actor: str, text: str = "") -> None:
        raise NotImplementedError("human actions arrive with the handoff step")
