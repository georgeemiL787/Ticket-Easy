"""Handoff to a human: the briefing a support person reads, and the case they work on."""

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from team_b.domain.base import FrozenModel, MutableModel
from team_b.domain.decision import EscalationReason
from team_b.domain.trace import PolicyRecord
from team_b.domain.understanding import Language

Priority = Literal["urgent", "high", "normal", "low"]


class CaseStatus(StrEnum):
    OPEN = "open"
    CLAIMED = "claimed"
    RESOLVED = "resolved"
    RETURNED_TO_AGENT = "returned_to_agent"


C = CaseStatus
ALLOWED_CASE_TRANSITIONS: Mapping[CaseStatus, frozenset[CaseStatus]] = {
    C.OPEN: frozenset({C.CLAIMED, C.RESOLVED}),
    C.CLAIMED: frozenset({C.OPEN, C.RESOLVED, C.RETURNED_TO_AGENT}),  # claimed -> open is a release
    C.RESOLVED: frozenset(),
    C.RETURNED_TO_AGENT: frozenset(),
}


class IllegalCaseTransitionError(ValueError):
    """The requested move is not in ALLOWED_CASE_TRANSITIONS."""


class CaseOwnershipError(IllegalCaseTransitionError):
    """Only the person who claimed a case may work on it."""


class NotManagerError(PermissionError):
    """Only a manager may do this."""


class CustomerSnapshot(FrozenModel):
    verified: bool = False
    customer_id: str | None = None
    method: str | None = None  # how they were verified
    phone_masked: str | None = None  # only the last digits are shown to staff, like 010****5678
    orders: tuple[str, ...] = ()


class PolicyQuote(FrozenModel):
    """A policy passage quoted exactly as written, with its source."""

    citation: str
    text: str


class AttemptedAction(FrozenModel):
    proposal_id: str
    tool: str
    state: str  # how far it got
    error: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    history: tuple[str, ...] = ()  # "proposed -> awaiting_confirmation (note)", oldest first
    audit_id: str | None = None
    execution_id: str | None = None  # the shop's reference for what was done


class FailureRecord(FrozenModel):
    """A tool or system failure seen in this conversation."""

    source: str  # the tool or service
    error_code: str
    message: str = ""
    audit_id: str | None = None
    execution_id: str | None = None
    trace_id: str | None = None


class TranscriptLine(FrozenModel):
    role: Literal["customer", "agent"]
    text: str  # redacted: phones, emails, cards and codes are hidden
    trace_id: str
    turn_index: int


class SimilarTicket(FrozenModel):
    """A past ticket that looks like this one, so staff can see how it was solved."""

    ticket_id: str
    category: str
    resolution: str
    citation: str


class PendingApproval(FrozenModel):
    """An action that waits for a human to approve it."""

    proposal_id: str
    tool: str
    capability: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class HandoffPackage(FrozenModel):
    """Everything a human needs, built from recorded facts so the customer never has to repeat anything."""

    summary: str
    summary_source: Literal["template", "ai"] = "template"
    reason: EscalationReason
    detail: str = ""  # the specific cause, e.g. which rule or system
    priority: Priority
    suggested_next_step: str
    language: Language | None = None
    intents: tuple[str, ...] = ()
    customer: CustomerSnapshot = Field(default_factory=CustomerSnapshot)
    details: dict[str, str] = Field(default_factory=dict)  # details collected from the customer (not the phone)
    order_facts: dict[str, Any] = Field(default_factory=dict)
    safety_flags: tuple[str, ...] = ()
    policy_quotes: tuple[PolicyQuote, ...] = ()
    rule_answers: tuple[PolicyRecord, ...] = ()
    attempted_actions: tuple[AttemptedAction, ...] = ()
    failures: tuple[FailureRecord, ...] = ()
    pending_approval: PendingApproval | None = None
    transcript: tuple[TranscriptLine, ...] = ()
    trace_ids: tuple[str, ...] = ()
    similar_tickets: tuple[SimilarTicket, ...] = ()
    ai_summary: str | None = None  # English summary written by the AI model, shown labelled as AI-written
    ai_summary_local: str | None = None  # the same summary in the customer's language and style
    prompt_version: str | None = None  # the prompt that wrote the AI summary
    ai_suggestion: str | None = None  # "AI suggestion, not approved: ...": never replaces suggested_next_step


class CaseEvent(FrozenModel):
    actor: str = Field(min_length=1)
    kind: str = Field(min_length=1)  # claimed, released, resolved, returned_to_agent, reply, approved, rejected, ...
    at: datetime
    note: str = ""


class HandoffCase(MutableModel):
    case_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    status: CaseStatus = Field(default=CaseStatus.OPEN, frozen=True)  # change only via transition()
    claimed_by: str | None = Field(default=None, frozen=True)
    events: list[CaseEvent] = Field(default_factory=list)
    package: HandoffPackage
    pending_approval: PendingApproval | None = None
    created_at: datetime
    updated_at: datetime

    def transition(self, to: CaseStatus, *, actor: str, at: datetime, note: str = "") -> None:
        """Move the case, or raise. Claiming sets claimed_by, releasing clears it."""
        if to not in ALLOWED_CASE_TRANSITIONS[self.status]:
            raise IllegalCaseTransitionError(f"{self.status.value} -> {to.value} is not allowed")
        kind = "released" if (self.status is CaseStatus.CLAIMED and to is CaseStatus.OPEN) else to.value
        self.events.append(CaseEvent(actor=actor, kind=kind, at=at, note=note))
        self.__dict__["status"] = to
        if to is CaseStatus.CLAIMED:
            self.__dict__["claimed_by"] = actor
        elif to is CaseStatus.OPEN:
            self.__dict__["claimed_by"] = None
        self.updated_at = at

    def reassign(self, assignee: str, *, by: str, at: datetime, note: str = "") -> None:
        """Hand an open or claimed case to `assignee`, who then owns it. The history shows who moved it and to whom."""
        if self.status is CaseStatus.CLAIMED:
            if self.claimed_by == assignee:
                raise IllegalCaseTransitionError(f"the case is already assigned to {assignee}")
            self.transition(CaseStatus.OPEN, actor=by, at=at, note=f"reassigned from {self.claimed_by}")
        self.transition(CaseStatus.CLAIMED, actor=assignee, at=at, note=note or f"assigned by {by}")
        self.add_event(actor=by, kind="assigned", at=at, note=f"to {assignee}")

    def require_claimer(self, agent: str) -> None:
        """Raise unless the case is claimed by `agent` (replying, deciding, resolving and returning need this)."""
        if self.status is not CaseStatus.CLAIMED:
            raise IllegalCaseTransitionError(f"the case is {self.status.value}: claim it first")
        if self.claimed_by != agent:
            raise CaseOwnershipError(f"the case is claimed by {self.claimed_by}")

    def add_event(self, *, actor: str, kind: str, at: datetime, note: str = "") -> None:
        """Record something that is not a status change (a reply, an approval, a rejection)."""
        self.events.append(CaseEvent(actor=actor, kind=kind, at=at, note=note))
        self.updated_at = at
