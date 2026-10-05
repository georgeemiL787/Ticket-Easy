from datetime import UTC, datetime
from itertools import product

import pytest
from pydantic import ValidationError

from team_b.contracts.policy import HumanApproval, PolicyDecision
from team_b.domain.actions import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    ActionProposal,
    ActionState,
    ExecutionNotAuthorizedError,
    IllegalTransitionError,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
S = ActionState

# The state machine written out by hand, so an accidental edit of ALLOWED_TRANSITIONS fails a test.
LEGAL = {
    (S.PROPOSED, S.BLOCKED),
    (S.PROPOSED, S.AWAITING_CONFIRMATION),
    (S.PROPOSED, S.AWAITING_HUMAN),
    (S.PROPOSED, S.APPROVED),
    (S.PROPOSED, S.FAILED),
    (S.AWAITING_CONFIRMATION, S.APPROVED),
    (S.AWAITING_CONFIRMATION, S.CANCELLED),
    (S.AWAITING_CONFIRMATION, S.BLOCKED),
    (S.AWAITING_CONFIRMATION, S.AWAITING_HUMAN),
    (S.AWAITING_CONFIRMATION, S.FAILED),
    (S.AWAITING_HUMAN, S.APPROVED),
    (S.AWAITING_HUMAN, S.CANCELLED),
    (S.AWAITING_HUMAN, S.BLOCKED),
    (S.AWAITING_HUMAN, S.FAILED),
    (S.APPROVED, S.EXECUTED),
    (S.APPROVED, S.FAILED),
    (S.APPROVED, S.BLOCKED),
    (S.EXECUTED, S.CONFIRMED),
    (S.EXECUTED, S.FAILED),
}
ILLEGAL = set(product(ActionState, ActionState)) - LEGAL


def decision(effect: str) -> PolicyDecision:
    return PolicyDecision.model_validate({"request_id": "r1", "decision": effect, "reason_code": "X"})


APPROVAL = HumanApproval(approved_by="staff-1", case_id="case-1", approved_at=NOW)


def proposal(state: ActionState = S.PROPOSED, *decisions: str, approval: HumanApproval | None = None) -> ActionProposal:
    return ActionProposal(
        proposal_id="p1",
        tool="refund_tool",
        capability="create_refund",
        arguments={"order_id": "NS-20877", "amount": 450},
        idempotency_key="idem-1",
        policy_decisions=[decision(d) for d in decisions],
        human_approval=approval,
        state=state,
    )


def test_transition_table_matches_the_spec() -> None:
    table = {(a, b) for a, targets in ALLOWED_TRANSITIONS.items() for b in targets}
    assert table == LEGAL
    assert set(ALLOWED_TRANSITIONS) == set(ActionState)  # every state is described


def test_terminal_states_are_blocked_cancelled_confirmed_failed() -> None:
    assert TERMINAL_STATES == {S.BLOCKED, S.CANCELLED, S.CONFIRMED, S.FAILED}


@pytest.mark.parametrize(("source", "target"), sorted(LEGAL, key=lambda p: (p[0].value, p[1].value)))
def test_every_legal_transition_works_and_is_recorded(source: ActionState, target: ActionState) -> None:
    p = proposal(source, "allow")  # an allow is on record so that approved -> executed is authorised
    p.transition(target, at=NOW, note="because")
    assert p.state is target
    assert len(p.history) == 1
    change = p.history[0]
    assert (change.from_state, change.to_state, change.at, change.note) == (source, target, NOW, "because")


@pytest.mark.parametrize(("source", "target"), sorted(ILLEGAL, key=lambda p: (p[0].value, p[1].value)))
def test_every_illegal_transition_raises_and_changes_nothing(source: ActionState, target: ActionState) -> None:
    p = proposal(source, "allow")
    with pytest.raises(IllegalTransitionError):
        p.transition(target, at=NOW)
    assert p.state is source
    assert p.history == []


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES, key=lambda s: s.value))
def test_nothing_leaves_a_terminal_state(terminal: ActionState) -> None:
    p = proposal(terminal, "allow")
    assert p.is_terminal
    for target in ActionState:
        with pytest.raises(IllegalTransitionError):
            p.transition(target, at=NOW)


def test_state_cannot_be_assigned_directly() -> None:
    p = proposal()
    with pytest.raises(ValidationError):
        p.state = S.EXECUTED
    assert p.state is S.PROPOSED


def test_history_keeps_every_step_in_order() -> None:
    p = proposal(S.PROPOSED, "allow")
    for target in (S.AWAITING_CONFIRMATION, S.APPROVED, S.EXECUTED, S.CONFIRMED):
        p.transition(target, at=NOW)
    assert [(c.from_state, c.to_state) for c in p.history] == [
        (S.PROPOSED, S.AWAITING_CONFIRMATION),
        (S.AWAITING_CONFIRMATION, S.APPROVED),
        (S.APPROVED, S.EXECUTED),
        (S.EXECUTED, S.CONFIRMED),
    ]
    assert p.is_terminal


# --- execution_authorized: the gate in front of the shop ---


def test_no_policy_decision_means_not_authorized() -> None:
    assert proposal(S.APPROVED).execution_authorized() is False


def test_allow_authorizes() -> None:
    assert proposal(S.APPROVED, "allow").execution_authorized() is True


def test_deny_never_authorizes() -> None:
    assert proposal(S.APPROVED, "deny").execution_authorized() is False
    assert proposal(S.APPROVED, "deny", approval=APPROVAL).execution_authorized() is False  # a human cannot override


def test_require_human_needs_a_recorded_approval() -> None:
    assert proposal(S.APPROVED, "require_human").execution_authorized() is False
    assert proposal(S.APPROVED, "require_human", approval=APPROVAL).execution_authorized() is True


def test_a_later_deny_overrides_an_earlier_allow() -> None:
    assert proposal(S.APPROVED, "allow", "deny").execution_authorized() is False


def test_never_after_a_deny_even_if_a_later_check_allows() -> None:
    assert proposal(S.APPROVED, "deny", "allow").execution_authorized() is False


def test_renewed_check_that_requires_a_human_again_blocks_without_approval() -> None:
    assert proposal(S.APPROVED, "allow", "require_human").execution_authorized() is False


@pytest.mark.parametrize("state", [S.BLOCKED, S.CANCELLED, S.FAILED])
def test_dead_states_are_never_authorized(state: ActionState) -> None:
    assert proposal(state, "allow").execution_authorized() is False


# --- the guard on approved -> executed ---


@pytest.mark.parametrize(
    ("decisions", "approval"),
    [
        ((), None),
        (("deny",), None),
        (("deny",), APPROVAL),
        (("require_human",), None),
        (("allow", "deny"), None),
    ],
)
def test_executing_without_authorization_raises_and_changes_nothing(
    decisions: tuple[str, ...], approval: HumanApproval | None
) -> None:
    p = proposal(S.APPROVED, *decisions, approval=approval)
    with pytest.raises(ExecutionNotAuthorizedError):
        p.transition(S.EXECUTED, at=NOW)
    assert p.state is S.APPROVED and p.history == []


def test_execution_not_authorized_is_an_illegal_transition() -> None:
    assert issubclass(ExecutionNotAuthorizedError, IllegalTransitionError)


def test_customer_path_executes_after_allow() -> None:
    p = proposal(S.PROPOSED, "allow")
    for target in (S.AWAITING_CONFIRMATION, S.APPROVED, S.EXECUTED, S.CONFIRMED):
        p.transition(target, at=NOW)
    assert p.state is S.CONFIRMED


def test_human_path_executes_after_approval_of_require_human() -> None:
    p = proposal(S.PROPOSED, "require_human")
    p.transition(S.AWAITING_HUMAN, at=NOW)
    with pytest.raises(ExecutionNotAuthorizedError):  # approved by the state machine, but no human approval yet
        p.transition(S.APPROVED, at=NOW)
        p.transition(S.EXECUTED, at=NOW)
    assert p.state is S.APPROVED
    p.human_approval = APPROVAL
    p.transition(S.EXECUTED, at=NOW)
    assert p.state is S.EXECUTED
