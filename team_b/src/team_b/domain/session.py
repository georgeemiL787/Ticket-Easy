"""Conversation memory: everything the brain remembers between messages."""

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from team_b.domain.actions import ActionProposal
from team_b.domain.base import FrozenModel, MutableModel
from team_b.domain.decision import EscalationReason
from team_b.domain.understanding import Language

MessageRole = Literal["customer", "agent", "human_agent", "system"]
SessionStatus = Literal["active", "handed_off", "closed"]


class Message(FrozenModel):
    role: MessageRole
    text: str
    at: datetime
    trace_id: str | None = None


class SessionIdentity(MutableModel):
    """Has this customer proved who they are? Set only by the identity check, never from what the customer says."""

    verified: bool = False
    customer_id: str | None = None
    method: str | None = None
    attempts: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _verified_needs_customer(self) -> Self:
        if self.verified and not self.customer_id:
            raise ValueError("a verified identity must name the customer")
        return self


class SessionState(MutableModel):
    tenant_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    channel: str = "web"
    status: SessionStatus = "active"
    language: Language | None = None
    history: list[Message] = Field(default_factory=list)  # only the last few messages; older ones go into the summary
    history_summary: str = ""
    turn_index: int = Field(default=0, ge=0)
    active_intent: str | None = None
    intent_queue: list[str] = Field(default_factory=list)
    queue_slots: dict[str, dict[str, str]] = Field(
        default_factory=dict
    )  # queued intent -> details that are its own (its order id)
    intents_seen: list[str] = Field(default_factory=list)  # every intent of this conversation, in order, no repeats
    last_escalation: EscalationReason | None = None
    slots: dict[str, str] = Field(default_factory=dict)  # details collected so far (order_id, phone, ...)
    awaiting: str | None = None  # what the agent waits for next, e.g. slot:phone, confirmation, human
    facts: dict[str, Any] = Field(default_factory=dict)  # real facts from the shop, never customer-supplied
    identity: SessionIdentity = Field(default_factory=SessionIdentity)
    pending_action_id: str | None = None
    actions: list[ActionProposal] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    risk_categories: list[str] = Field(default_factory=list)
    clarifications: int = Field(default=0, ge=0)
    tool_failures: int = Field(default=0, ge=0)  # reads that failed in a row
    write_failures: int = Field(default=0, ge=0)  # actions the shop clearly refused, since the last success
    no_evidence_count: int = Field(default=0, ge=0)  # policy questions the documents could not answer, in a row
    denied_actions: list[str] = Field(
        default_factory=list
    )  # actions the policy refused once; a second try goes to a person
    handoff_case_id: str | None = None
    choice_options: list[str] = Field(default_factory=list)  # intents offered in a "do you mean A or B?" question
    order_choices: list[str] = Field(default_factory=list)  # order ids offered in a "which order?" question
    handoff_notice_sent: bool = False  # the customer was already told that a colleague will reply
    outbox: list[Message] = Field(default_factory=list)  # replies waiting to be shown to the customer (human replies)
    version: int = Field(default=0, ge=0)  # bumped on every save: detects two writers at once
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def _pending_action_exists(self) -> Self:
        if self.pending_action_id is not None and all(a.proposal_id != self.pending_action_id for a in self.actions):
            raise ValueError("pending_action_id does not match any action in this session")
        return self
