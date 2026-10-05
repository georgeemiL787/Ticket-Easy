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
from typing import Any

from team_b.brain.language import LANGUAGE_TRUST
from team_b.brain.nlu import NLU, RuleBasedNLU
from team_b.brain.redaction import redact
from team_b.brain.summarizer import HistorySummarizer, TemplateHistorySummarizer
from team_b.domain.decision import Decision
from team_b.domain.reply import AgentReply
from team_b.domain.session import Message, SessionState
from team_b.domain.tenant import TenantConfig, TenantRegistry
from team_b.domain.trace import DecisionTrace, TraceStep
from team_b.domain.understanding import Language, Locale, NLUResult
from team_b.observability import get_logger
from team_b.ports import CaseStore, Clock, SessionConflictError, SessionStore, TraceStore

log = get_logger(__name__)
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
        nlu: NLU | None = None,
    ) -> None:
        self._clock = clock
        self._tenants = tenants
        self._sessions = sessions
        self._traces = traces
        self._cases = cases
        self._summarizer = summarizer or TemplateHistorySummarizer()
        self._nlu: NLU = nlu or RuleBasedNLU()

    async def handle_turn(self, tenant_id: str, conversation_id: str, text: str) -> AgentReply:
        """Answer one customer message. Raises UnknownTenantError for a tenant that is not configured.

        If another writer saved the session first (a second process on the same database), the turn is redone once on
        the fresh session; a second conflict is raised to the caller. Nothing is stored for a failed attempt."""
        tenant = self._tenants.get(tenant_id)
        try:
            return await self._handle_turn_once(tenant, conversation_id, text)
        except SessionConflictError:
            return await self._handle_turn_once(tenant, conversation_id, text)

    async def _handle_turn_once(self, tenant: TenantConfig, conversation_id: str, text: str) -> AgentReply:
        started = time.perf_counter()
        tenant_id = tenant.tenant_id
        async with self._sessions.lock(tenant_id, conversation_id):
            now = self._clock.now()
            session = await self._load_or_create(tenant, conversation_id, now)
            understanding = await self._understand(text, session, tenant)
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
                **self._understanding_fields(understanding),
                decision=decision,
                decision_reason="first version: no understanding yet, so ask for more detail",
                response_text=reply_text,
                steps=(TraceStep(stage="orchestrator", status=decision.value),),
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            if understanding is not None and understanding.language_confidence >= LANGUAGE_TRUST:
                session.language = understanding.language  # a message with no language content keeps the old one
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

    async def _understand(self, text: str, session: SessionState, tenant: TenantConfig) -> NLUResult | None:
        """Read the message. Understanding never stops a turn: if even the fallback fails, the turn goes on."""
        try:
            return await self._nlu.understand(text, session, tenant)
        except Exception:
            log.exception("understanding_failed")
            return None

    @staticmethod
    def _understanding_fields(result: NLUResult | None) -> dict[str, Any]:
        """What the trace records about how the message was read (values redacted; details are not copied raw)."""
        if result is None:
            return {}
        return {
            "language": result.language,
            "intents": result.intents,
            "entities": {key: redact(value) for key, value in result.entities.items()},
            "nlu_method": result.method,
            "frustration": result.frustration,
            "risk_categories": result.safety_flags,
            "versions": {"prompt": result.prompt_version} if result.prompt_version else {},
        }

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
