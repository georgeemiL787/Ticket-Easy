from datetime import UTC, datetime
from itertools import product

import pytest
from pydantic import ValidationError

from team_b.domain.decision import EscalationReason
from team_b.domain.handoff import (
    ALLOWED_CASE_TRANSITIONS,
    CaseStatus,
    HandoffCase,
    HandoffPackage,
    IllegalCaseTransitionError,
)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 9, 28, 13, 0, tzinfo=UTC)
C = CaseStatus

LEGAL = {
    (C.OPEN, C.CLAIMED),
    (C.OPEN, C.RESOLVED),
    (C.CLAIMED, C.OPEN),
    (C.CLAIMED, C.RESOLVED),
    (C.CLAIMED, C.RETURNED_TO_AGENT),
}
ILLEGAL = set(product(CaseStatus, CaseStatus)) - LEGAL


def package() -> HandoffPackage:
    return HandoffPackage(
        summary="Refund on day 20",
        reason=EscalationReason.POLICY_DENIED,
        priority="normal",
        suggested_next_step="Review the exception request",
    )


def case(status: CaseStatus = C.OPEN, claimed_by: str | None = None) -> HandoffCase:
    return HandoffCase(
        case_id="case-1",
        tenant_id="shop_001",
        conversation_id="conv-1",
        package=package(),
        created_at=NOW,
        updated_at=NOW,
        status=status,
        claimed_by=claimed_by,
    )


def test_transition_table_matches_the_spec() -> None:
    assert {(a, b) for a, targets in ALLOWED_CASE_TRANSITIONS.items() for b in targets} == LEGAL


@pytest.mark.parametrize(("source", "target"), sorted(LEGAL, key=lambda p: (p[0].value, p[1].value)))
def test_legal_case_transitions(source: CaseStatus, target: CaseStatus) -> None:
    c = case(source, claimed_by="staff-1" if source is C.CLAIMED else None)
    c.transition(target, actor="staff-1", at=LATER)
    assert c.status is target
    assert len(c.events) == 1 and c.events[0].actor == "staff-1" and c.updated_at == LATER


@pytest.mark.parametrize(("source", "target"), sorted(ILLEGAL, key=lambda p: (p[0].value, p[1].value)))
def test_illegal_case_transitions_raise_and_change_nothing(source: CaseStatus, target: CaseStatus) -> None:
    c = case(source, claimed_by="staff-1" if source is C.CLAIMED else None)
    with pytest.raises(IllegalCaseTransitionError):
        c.transition(target, actor="staff-1", at=LATER)
    assert c.status is source and c.events == [] and c.updated_at == NOW


def test_claiming_sets_claimed_by_and_releasing_clears_it() -> None:
    c = case()
    c.transition(C.CLAIMED, actor="staff-1", at=LATER)
    assert c.claimed_by == "staff-1"
    c.transition(C.OPEN, actor="staff-1", at=LATER, note="going home")
    assert c.claimed_by is None
    assert [e.kind for e in c.events] == ["claimed", "released"]
    assert c.events[1].note == "going home"


def test_resolved_and_returned_cases_are_final() -> None:
    for final in (C.RESOLVED, C.RETURNED_TO_AGENT):
        c = case(final)
        for target in CaseStatus:
            with pytest.raises(IllegalCaseTransitionError):
                c.transition(target, actor="x", at=LATER)


def test_status_cannot_be_assigned_directly() -> None:
    c = case()
    with pytest.raises(ValidationError):
        c.status = C.RESOLVED
    with pytest.raises(ValidationError):
        c.claimed_by = "someone"


def test_add_event_does_not_change_status() -> None:
    c = case()
    c.transition(C.CLAIMED, actor="staff-1", at=LATER)
    c.add_event(actor="staff-1", kind="reply", at=LATER, note="we are checking")
    assert c.status is C.CLAIMED and c.events[-1].kind == "reply"


def test_case_round_trips_through_json() -> None:
    c = case()
    c.transition(C.CLAIMED, actor="staff-1", at=LATER)
    assert HandoffCase.model_validate_json(c.model_dump_json()).status is C.CLAIMED


def test_package_needs_a_reason_and_valid_priority() -> None:
    with pytest.raises(ValidationError):
        HandoffPackage.model_validate({"summary": "s", "priority": "normal", "suggested_next_step": "x"})
    with pytest.raises(ValidationError):
        HandoffPackage.model_validate(
            {"summary": "s", "reason": "policy_denied", "priority": "asap", "suggested_next_step": "x"}
        )
