from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from team_b.domain.actions import ActionProposal
from team_b.domain.session import Message, SessionIdentity, SessionState
from team_b.domain.understanding import Language

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def session(**over: object) -> SessionState:
    base: dict[str, object] = {
        "tenant_id": "shop_001",
        "conversation_id": "conv-1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    return SessionState.model_validate({**base, **over})


def action(proposal_id: str = "p1") -> ActionProposal:
    return ActionProposal(proposal_id=proposal_id, tool="t", capability="create_refund", idempotency_key="k")


def test_new_session_starts_empty_and_unverified() -> None:
    s = session()
    assert s.status == "active" and s.channel == "web" and s.version == 0 and s.turn_index == 0
    assert s.identity.verified is False and s.identity.attempts == 0
    assert s.history == [] and s.slots == {} and s.pending_action_id is None and s.language is None


def test_session_requires_ids_and_timestamps() -> None:
    with pytest.raises(ValidationError):
        SessionState.model_validate({"tenant_id": "shop_001", "conversation_id": "c"})
    with pytest.raises(ValidationError):
        session(tenant_id="")


def test_counters_cannot_go_negative() -> None:
    for field in ("clarifications", "tool_failures", "no_evidence_count", "turn_index", "version"):
        with pytest.raises(ValidationError):
            session(**{field: -1})


def test_verified_identity_must_name_the_customer() -> None:
    with pytest.raises(ValidationError):
        SessionIdentity(verified=True)
    assert SessionIdentity(verified=True, customer_id="C-100", method="phone+order_id").verified


def test_pending_action_must_exist_in_the_session() -> None:
    with pytest.raises(ValidationError):
        session(pending_action_id="p1")
    s = session(actions=[action("p1")], pending_action_id="p1")
    assert s.pending_action_id == "p1"


def test_assignments_are_validated() -> None:
    s = session()
    s.language = Language.ARABIZI
    with pytest.raises(ValidationError):
        s.clarifications = -3
    with pytest.raises(ValidationError):
        s.pending_action_id = "nope"


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        session(surprise=1)


def test_messages_are_immutable_and_roles_are_checked() -> None:
    m = Message(role="customer", text="hello", at=NOW)
    with pytest.raises(ValidationError):
        m.text = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Message(role="robot", text="x", at=NOW)  # type: ignore[arg-type]
    assert {r for r in ("customer", "agent", "human_agent", "system")} == {
        Message(role=r, text="x", at=NOW).role  # type: ignore[arg-type]
        for r in ("customer", "agent", "human_agent", "system")
    }


def test_session_survives_a_json_round_trip() -> None:
    s = session(
        language=Language.MIXED,
        history=[Message(role="customer", text="3ayez a3raf el order feen", at=NOW, trace_id="t1")],
        slots={"order_id": "NS-20877"},
        actions=[action()],
        pending_action_id="p1",
        awaiting="confirmation",
        facts={"order_status": "shipped"},
        version=3,
    )
    again = SessionState.model_validate_json(s.model_dump_json())
    assert again == s
