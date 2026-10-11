"""ActionProposal: one attempt to change something in the shop, and the only legal ways it can move forward.

This is safety-critical. State can only change through transition(), every change is recorded, and a proposal
can only reach "executed" while execution_authorized() is true.
"""

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field

from team_b.contracts.policy import HumanApproval, PolicyDecision
from team_b.contracts.tools import ToolResult
from team_b.domain.base import FrozenModel, MutableModel


class ActionState(StrEnum):
    PROPOSED = "proposed"
    BLOCKED = "blocked"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_HUMAN = "awaiting_human"
    APPROVED = "approved"
    CANCELLED = "cancelled"
    EXECUTED = "executed"
    CONFIRMED = "confirmed"
    FAILED = "failed"


S = ActionState
ALLOWED_TRANSITIONS: Mapping[ActionState, frozenset[ActionState]] = {
    S.PROPOSED: frozenset({S.BLOCKED, S.AWAITING_CONFIRMATION, S.AWAITING_HUMAN, S.APPROVED, S.FAILED}),
    S.AWAITING_CONFIRMATION: frozenset({S.APPROVED, S.CANCELLED, S.BLOCKED, S.AWAITING_HUMAN, S.FAILED}),
    S.AWAITING_HUMAN: frozenset({S.APPROVED, S.CANCELLED, S.BLOCKED, S.FAILED}),
    S.APPROVED: frozenset({S.EXECUTED, S.FAILED, S.BLOCKED}),
    S.EXECUTED: frozenset({S.CONFIRMED, S.FAILED}),
    S.BLOCKED: frozenset(),
    S.CANCELLED: frozenset(),
    S.CONFIRMED: frozenset(),
    S.FAILED: frozenset(),
}
TERMINAL_STATES = frozenset(state for state, targets in ALLOWED_TRANSITIONS.items() if not targets)
# States in which the shop must never be touched.
NEVER_EXECUTE_STATES = frozenset({S.BLOCKED, S.CANCELLED, S.FAILED})


class IllegalTransitionError(ValueError):
    """The requested move is not in ALLOWED_TRANSITIONS."""


class ExecutionNotAuthorizedError(IllegalTransitionError):
    """Tried to execute without an allow (or a human approval of a require_human)."""


class StateChange(FrozenModel):
    from_state: ActionState
    to_state: ActionState
    at: datetime
    note: str = ""


class ActionProposal(MutableModel):
    proposal_id: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    facts_snapshot: dict[str, Any] = Field(default_factory=dict)  # the real facts the decision was based on
    policy_decisions: list[PolicyDecision] = Field(default_factory=list)  # oldest first, latest last
    human_approval: HumanApproval | None = None
    idempotency_key: str = Field(min_length=1)
    result: ToolResult | None = None
    state: ActionState = Field(default=ActionState.PROPOSED, frozen=True)  # change only via transition()
    history: list[StateChange] = Field(default_factory=list)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def execution_authorized(self) -> bool:
        """May the shop be touched now?

        Never after a deny (any deny in the history blocks, not only the latest), never in a blocked, cancelled or
        failed state, never without a policy decision. Otherwise the latest decision must be allow, or
        require_human together with a recorded human approval.
        """
        if self.state in NEVER_EXECUTE_STATES or not self.policy_decisions:
            return False
        if any(d.decision == "deny" for d in self.policy_decisions):
            return False
        latest = self.policy_decisions[-1]
        if latest.decision == "allow":
            return True
        return latest.decision == "require_human" and self.human_approval is not None

    def transition(self, to: ActionState, *, at: datetime, note: str = "") -> None:
        """Move to a new state, or raise. Appends to history on success; changes nothing on failure."""
        if to not in ALLOWED_TRANSITIONS[self.state]:
            raise IllegalTransitionError(f"{self.state.value} -> {to.value} is not allowed")
        if to is ActionState.EXECUTED and not self.execution_authorized():
            raise ExecutionNotAuthorizedError("no allow (or human approval) on record: refusing to execute")
        self.history.append(StateChange(from_state=self.state, to_state=to, at=at, note=note))
        self.__dict__["state"] = to
