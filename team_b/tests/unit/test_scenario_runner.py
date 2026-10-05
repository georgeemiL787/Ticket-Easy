"""The scenario runner itself: a deliberately broken scenario must fail every assertion with a clear message."""

from dataclasses import replace
from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from team_b.container import Container
from team_b.contracts.tools import ToolCallRequest
from team_b.domain.decision import Decision
from team_b.domain.reply import AgentReply
from tests.integration.scenario_format import Inject, Scenario
from tests.integration.scenario_runner import inject_spec, run_scenario

T = "shop_001"


class MisbehavingOrchestrator:
    """Wraps the real one but cites a passage, claims `answer` (its trace says clarify) and makes a write that no
    policy entry backs."""

    def __init__(self, container: Container) -> None:
        self.inner = container.orchestrator
        self.shop = container.shop

    async def handle_turn(self, tenant_id: str, conversation_id: str, text: str) -> AgentReply:
        assert self.inner is not None and self.shop is not None
        reply = await self.inner.handle_turn(tenant_id, conversation_id, text)
        request = ToolCallRequest(
            request_id="req-ghost",
            tool="create_ticket",
            arguments={"subject": "a", "description": "b"},
            idempotency_key="idem-ghost",
            policy_request_id="pol-ghost",
        )
        await self.shop.call_tool(tenant_id, request)
        return reply.model_copy(update={"decision": Decision.ANSWER, "citations": ("return_policy@v1#s1",)})


def misbehave(container: Container) -> Container:
    return replace(container, orchestrator=MisbehavingOrchestrator(container))  # type: ignore[arg-type]


def scenario(**overrides: Any) -> Scenario:
    data: dict[str, Any] = {
        "id": "S99",
        "title": "sample",
        "status": "active",
        "turns": [{"say": "Hello", "expect": {"decision": "clarify"}}],
    }
    return Scenario.model_validate({**data, **overrides})


BROKEN = scenario(
    turns=[
        {
            "say": "Hello",
            "expect": {
                "decision": "handoff",
                "escalation": "mandatory_risk",
                "awaiting": "slot:phone",
                "locale": "ar",
                "citations_include": ["shipping_policy@v1#s1"],
                "citations_exclude": ["return_policy@v1#s1"],
                "text_contains": "refund",
                "text_contains_any": ["xyz", "abc"],
                "text_not_contains": "tell me",
            },
        }
    ],
    final={
        "executed_tools": ["create_refund"],
        "audit_count": 3,
        "no_writes": True,
        "case_reason": "policy_denied",
        "case_priority": "urgent",
        "case_has_pending_approval": True,
        "trace_invariants": True,
    },
)

EXPECTED_MESSAGES = [
    "S99 turn 1 expect.decision: expected 'handoff', got 'answer'",
    "S99 turn 1 expect.escalation: expected 'mandatory_risk', got None",
    "S99 turn 1 expect.awaiting: expected 'slot:phone', got 'detail'",
    "S99 turn 1 expect.locale: expected 'ar', got 'en'",
    "S99 turn 1 expect.citations_include: 'shipping_policy@v1#s1' is missing, got ['return_policy@v1#s1']",
    "S99 turn 1 expect.citations_exclude: 'return_policy@v1#s1' must not be cited",
    "S99 turn 1 expect.text_contains: 'refund' not found in reply",
    "S99 turn 1 expect.text_contains_any: none of ['xyz', 'abc'] found in reply",
    "S99 turn 1 expect.text_not_contains: 'tell me' must not appear",
    "S99 final.executed_tools: expected ['create_refund'], got ['create_ticket']",
    "S99 final.audit_count: expected 3, got 1",
    "S99 final.no_writes: expected True, got False",
    "S99 final.case_reason: expected 'policy_denied', got None",
    "S99 final.case_priority: expected 'urgent', got None",
    "S99 final.case_has_pending_approval: expected True, got False",
    "S99 final.trace_invariants: reply says answer but its trace",
    "SAFETY: write #1 create_ticket (request req-ghost) has no matching policy entry",
]


async def test_a_broken_scenario_fails_every_assertion_with_a_clear_message() -> None:
    result = await run_scenario(BROKEN, T, customize=misbehave)
    assert result.outcome == "failed"
    for wanted in EXPECTED_MESSAGES:
        assert any(wanted in failure for failure in result.failures), f"no failure says: {wanted}\n{result.failures}"


async def test_the_write_safety_check_runs_even_when_the_scenario_asks_for_nothing() -> None:
    result = await run_scenario(scenario(), T, customize=misbehave)
    assert [f for f in result.failures if f.startswith("SAFETY: write #1 create_ticket")]


async def test_a_clean_scenario_passes() -> None:
    result = await run_scenario(scenario(final={"no_writes": True, "audit_count": 0, "trace_invariants": True}), T)
    assert (result.outcome, result.failures, result.final_decision) == ("passed", [], "clarify")


async def test_an_explicit_null_means_none_and_a_missing_key_is_not_checked() -> None:
    ok = scenario(turns=[{"say": "Hi", "expect": {"escalation": None}}], final={"case_reason": None})
    assert (await run_scenario(ok, T)).outcome == "passed"
    bad = scenario(turns=[{"say": "Hi", "expect": {"awaiting": None}}])
    assert "expect.awaiting: expected None, got 'detail'" in (await run_scenario(bad, T)).failures[0]


async def test_a_pending_scenario_is_skipped_with_its_reason_and_never_run() -> None:
    pending = scenario(status="pending", pending_reason="needs the rule checker", turns=[{"say": "x"}])
    result = await run_scenario(pending, T)
    assert (result.outcome, result.skip_reason, result.final_decision) == ("skipped", "needs the rule checker", None)


async def test_a_human_action_without_a_handoff_case_fails_clearly() -> None:
    sample = scenario(turns=[{"human": {"action": "claim"}}])
    result = await run_scenario(sample, T)
    assert result.failures[0] == "S99 turn 1 human.claim: the conversation has no handoff case"


class CrashingOrchestrator:
    async def handle_turn(self, tenant_id: str, conversation_id: str, text: str) -> AgentReply:
        raise RuntimeError("boom")


async def test_a_crash_in_the_orchestrator_is_a_failed_scenario_not_a_broken_run() -> None:
    crash = lambda c: replace(c, orchestrator=CrashingOrchestrator())  # type: ignore[arg-type]  # noqa: E731
    result = await run_scenario(scenario(turns=[{"say": "Hi"}]), T, customize=crash)
    assert result.outcome == "failed" and result.failures[0] == "S99 turn 1 raised RuntimeError: boom"


async def test_advance_days_moves_the_clock_of_the_scenario() -> None:
    seen: list[date] = []

    def spy(container: Container) -> Container:
        seen.append(container.clock.today())
        return container

    sample = scenario(setup={"today": "2026-09-28"}, turns=[{"say": "Hi", "advance_days": 3}])
    assert (await run_scenario(sample, T, customize=spy)).outcome == "passed"
    assert seen == [date(2026, 9, 28)]


async def test_an_unsupported_inject_is_reported_not_ignored() -> None:
    sample = scenario(inject=[{"plug": "llm", "operation": "complete", "mode": "fail"}])
    result = await run_scenario(sample, T)
    assert result.outcome == "failed" and "inject 1 (llm/complete/fail)" in result.failures[0]


def test_inject_entries_become_shop_and_search_switches() -> None:
    assert inject_spec(Inject(plug="shop", operation="get_order", mode="timeout", times=2)) == {
        "switch": "fail_next", "tool": "get_order", "code": "TIMEOUT", "times": 2,
    }  # fmt: skip
    assert inject_spec(Inject(plug="shop", operation="create_refund", mode="uncertain")) == {
        "switch": "uncertain", "tool": "create_refund",
    }  # fmt: skip
    assert inject_spec(Inject(plug="policy_search", operation="search_knowledge", mode="fail")) == {
        "switch": "fail_next", "operation": "search_knowledge", "code": "BACKEND_UNAVAILABLE", "times": 1,
    }  # fmt: skip
    with pytest.raises(ValueError, match="only exists for the shop plug"):
        inject_spec(Inject(plug="policy_search", operation="search_knowledge", mode="no_audit"))


@pytest.mark.parametrize(
    "bad",
    [
        {"status": "pending"},  # no reason
        {"colour": "red"},  # unknown field
        {"turns": [{}]},  # turn does nothing
        {"turns": [{"say": "x", "expect": {"decison": "answer"}}]},  # typo inside expect
        {"inject": [{"plug": "shop", "operation": "x", "mode": "explode"}]},
    ],
)
def test_the_scenario_format_rejects_mistakes(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        scenario(**bad)
