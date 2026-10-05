"""The orchestrator: handles one customer message or one human action at a time.

Minimal first version: every customer message is answered with a clarify reply and a valid trace, so the scenario
runner can be tested end to end. Understanding, gates and actions come with later steps. Memory is real: one session per
conversation, the last history_max_turns messages kept, older ones folded into history_summary, and the redacted
transcript stored in the traces. The human methods share one signature so the runner can call them by name:
(tenant_id, case_id, *, actor, text="").
"""

import time
import uuid
from datetime import datetime

from team_b.brain.redaction import redact
from team_b.brain.summarizer import HistorySummarizer, TemplateHistorySummarizer
from team_b.domain.decision import Decision
from team_b.domain.reply import AgentReply
from team_b.domain.session import Message, SessionState
from team_b.domain.tenant import TenantConfig, TenantRegistry
from team_b.domain.trace import DecisionTrace, TraceStep
from team_b.domain.understanding import Language, Locale
from team_b.ports import CaseStore, Clock, SessionStore, TraceStore

CLARIFY_TEXT = "Could you tell me a little more about what you need help with?"


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
    ) -> None:
        self._clock = clock
        self._tenants = tenants
        self._sessions = sessions
        self._traces = traces
        self._cases = cases
        self._summarizer = summarizer or TemplateHistorySummarizer()

    async def handle_turn(self, tenant_id: str, conversation_id: str, text: str) -> AgentReply:
        """Answer one customer message. Raises UnknownTenantError for a tenant that is not configured."""
        started = time.perf_counter()
        tenant = self._tenants.get(tenant_id)
        async with self._sessions.lock(tenant_id, conversation_id):
            now = self._clock.now()
            session = await self._load_or_create(tenant, conversation_id, now)
            trace_id, request_id = uuid.uuid4().hex, uuid.uuid4().hex
            session.history.append(Message(role="customer", text=text, at=now, trace_id=trace_id))

            reply_text, decision, awaiting = CLARIFY_TEXT, Decision.CLARIFY, "detail"  # processing goes here
            session.history.append(Message(role="agent", text=reply_text, at=now, trace_id=trace_id))
            trace = DecisionTrace(
                trace_id=trace_id,
                request_id=request_id,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                turn_index=session.turn_index,
                customer_message=redact(text),
                decision=decision,
                decision_reason="first version: no understanding yet, so ask for more detail",
                response_text=reply_text,
                steps=(TraceStep(stage="orchestrator", status=decision.value),),
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            session.awaiting = awaiting
            session.turn_index += 1
            session.updated_at = now
            await self._trim_history(session, tenant)
            await self._sessions.save(session)
            await self._traces.add(trace)
        return AgentReply(
            request_id=request_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            text=reply_text,
            locale=Locale.EN,  # the placeholder reply is English; the composer picks the locale later
            decision=decision,
            trace_id=trace_id,
            awaiting=awaiting,
        )

    async def _load_or_create(self, tenant: TenantConfig, conversation_id: str, now: datetime) -> SessionState:
        existing = await self._sessions.load(tenant.tenant_id, conversation_id)
        if existing is not None:
            return existing
        return SessionState(
            tenant_id=tenant.tenant_id,
            conversation_id=conversation_id,
            language=Language(tenant.default_locale.value),
            created_at=now,
            updated_at=now,
        )

    async def _trim_history(self, session: SessionState, tenant: TenantConfig) -> None:
        """Keep the last history_max_turns messages; the older ones are folded into history_summary."""
        excess = len(session.history) - tenant.history_max_turns
        if excess <= 0:
            return
        folded = session.history[:excess]
        session.history_summary = await self._summarizer.summarize(session, folded, tenant)
        del session.history[:excess]

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
