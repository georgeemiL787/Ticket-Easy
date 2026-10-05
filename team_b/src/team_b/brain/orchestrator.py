"""The orchestrator: handles one customer message or one human action at a time.

This is the minimal first version: every customer message is answered with a clarify reply and a valid trace, so the
scenario runner can be tested end to end. Understanding, gates and actions are added by later steps. The human methods
share one signature so the runner can call them by name: (tenant_id, case_id, *, actor, text="").
"""

import time
import uuid

from team_b.domain.decision import Decision
from team_b.domain.reply import AgentReply
from team_b.domain.session import Message, SessionState
from team_b.domain.tenant import TenantRegistry
from team_b.domain.trace import DecisionTrace, TraceStep
from team_b.domain.understanding import Locale
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
    ) -> None:
        self._clock = clock
        self._tenants = tenants
        self._sessions = sessions
        self._traces = traces
        self._cases = cases

    async def handle_turn(self, tenant_id: str, conversation_id: str, text: str) -> AgentReply:
        """Answer one customer message. Raises UnknownTenantError for a tenant that is not configured."""
        started = time.perf_counter()
        self._tenants.get(tenant_id)
        async with self._sessions.lock(tenant_id, conversation_id):
            now = self._clock.now()
            session = await self._sessions.load(tenant_id, conversation_id) or SessionState(
                tenant_id=tenant_id, conversation_id=conversation_id, created_at=now, updated_at=now
            )
            trace_id = uuid.uuid4().hex
            request_id = uuid.uuid4().hex
            trace = DecisionTrace(
                trace_id=trace_id,
                request_id=request_id,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                turn_index=session.turn_index,
                # customer_message stays empty until the redaction step exists: raw text must not be stored.
                decision=Decision.CLARIFY,
                decision_reason="first version: no understanding yet, so ask for more detail",
                response_text=CLARIFY_TEXT,
                steps=(TraceStep(stage="orchestrator", status="clarify"),),
                latency_ms=(time.perf_counter() - started) * 1000,
            )
            await self._traces.add(trace)
            session.history.append(Message(role="customer", text=text, at=now, trace_id=trace_id))
            session.history.append(Message(role="agent", text=CLARIFY_TEXT, at=now, trace_id=trace_id))
            session.turn_index += 1
            session.awaiting = "detail"
            session.updated_at = now
            await self._sessions.save(session)
        return AgentReply(
            request_id=request_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            text=CLARIFY_TEXT,
            locale=Locale.EN,
            decision=Decision.CLARIFY,
            trace_id=trace_id,
            awaiting="detail",
        )

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
