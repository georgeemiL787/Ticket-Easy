"""A return asked for an order that has not been delivered is explained, not handed to a person."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from team_b.brain.actions import _deny_step
from team_b.brain.turn import TurnContext
from team_b.container import Container, build_container
from team_b.contracts.policy import PolicyDecision
from team_b.domain.decision import Decision
from team_b.domain.session import SessionState
from tests.support import make_settings

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def container(tmp_path: Path) -> Container:
    return build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))


def ctx_with(container: Container, facts: dict[str, object]) -> TurnContext:
    assert container.orchestrator is not None
    session = SessionState(tenant_id="shop_001", conversation_id="c-nd", created_at=NOW, updated_at=NOW)
    session.facts.update(facts)
    session.active_intent = "return_request"
    return TurnContext(
        deps=container.orchestrator._deps, tenant=container.tenants.get("shop_001"), session=session,
        text="x", now=NOW, request_id="r", trace_id="t",
    )  # fmt: skip


def missing(code: str = "MISSING_CONTEXT", fields: tuple[str, ...] = ("days_since_delivery",)) -> PolicyDecision:
    return PolicyDecision.model_validate(
        {"request_id": "p1", "decision": "deny", "reason_code": code, "action": "create_return",
         "missing_fields": list(fields)}
    )  # fmt: skip


def test_a_shipped_order_gets_an_explanation_and_no_case(container: Container) -> None:
    ctx = ctx_with(container, {"order_status": "shipped", "delivered_at": None})
    step = _deny_step(ctx, missing())
    assert step.decision is Decision.REFUSE
    assert step.reply_key == "not_delivered_yet"
    assert step.escalation is None
    assert ctx.session.active_intent is None


def test_a_delivered_order_with_a_missing_fact_still_goes_to_a_person(container: Container) -> None:
    ctx = ctx_with(container, {"order_status": "delivered", "delivered_at": "2026-09-20"})
    assert _deny_step(ctx, missing()).decision is Decision.HANDOFF


def test_another_missing_fact_is_not_treated_as_not_delivered(container: Container) -> None:
    ctx = ctx_with(container, {"order_status": "shipped", "delivered_at": None})
    assert _deny_step(ctx, missing(fields=("product_category",))).decision is Decision.HANDOFF
