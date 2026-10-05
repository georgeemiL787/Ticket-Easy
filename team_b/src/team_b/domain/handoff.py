"""Handoff to a human: the briefing a support person reads, and the case they work on."""

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from team_b.domain.base import FrozenModel, MutableModel
from team_b.domain.decision import EscalationReason
from team_b.domain.session import Message
from team_b.domain.trace import PolicyRecord

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


class CustomerSnapshot(FrozenModel):
    verified: bool = False
    customer_id: str | None = None
    phone_masked: str | None = None  # only the last digits are shown to staff
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
    reason: EscalationReason
    priority: Priority
    suggested_next_step: str
    customer: CustomerSnapshot = Field(default_factory=CustomerSnapshot)
    details: dict[str, str] = Field(default_factory=dict)  # details collected from the customer
    order_facts: dict[str, Any] = Field(default_factory=dict)
    safety_flags: tuple[str, ...] = ()
    policy_quotes: tuple[PolicyQuote, ...] = ()
    rule_answers: tuple[PolicyRecord, ...] = ()
    attempted_actions: tuple[AttemptedAction, ...] = ()
    failures: tuple[str, ...] = ()
    transcript: tuple[Message, ...] = ()
    ai_summary: str | None = None  # shown labelled as AI-written
    ai_suggestion: str | None = None  # shown labelled "suggestion, not approved"


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

    def add_event(self, *, actor: str, kind: str, at: datetime, note: str = "") -> None:
        """Record something that is not a status change (a reply, an approval, a rejection)."""
        self.events.append(CaseEvent(actor=actor, kind=kind, at=at, note=note))
        self.updated_at = at
