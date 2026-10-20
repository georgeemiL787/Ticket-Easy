"""Forced escalations in agent mode (safety-critical): code hands over at once, and the orchestrator is not in the way.

The same scenarios run with the scripted orchestrator. Every handoff they cause must be labelled "forced"; the ones
decided before any work starts (a flagged safety screen, a request for a person, a chat a person already owns) must not
wait for a single move of the orchestrator. Each handoff's briefing also lists what was tried.
"""

from typing import Any

import pytest

from agent_service.container import Container
from agent_service.domain.decision import Decision
from agent_service.domain.trace import DecisionTrace
from tests.fakes import FunctionPicker
from tests.integration.agent_expert import expert
from tests.integration.scenario_format import discover, load_scenario
from tests.integration.scenario_runner import run_scenario

# scenario -> the turn (0-based) that must hand over by itself, and whether the orchestrator is never asked on it
FORCED = {
    "S07": (0, True),  # a fraud report: the safety screen
    "S08": (0, True),  # the customer asks for a person
    "S35": (0, True),  # a legal threat in Arabic: the safety screen
    "S12": (1, False),  # the wrong phone twice: identity failed
    "S15": (None, False),  # someone else's order: ownership mismatch
    "S06": (None, False),  # a refund above the limit: the rules need a person
    "S46": (None, False),  # an order number the shop does not know, twice
}
CASES = [(t, p) for t, p in discover() if p.name.split("_")[0] in FORCED]


def test_every_forced_scenario_exists() -> None:
    assert {p.name.split("_")[0] for _, p in CASES} == set(FORCED)


@pytest.mark.parametrize(("tenant_id", "path"), CASES, ids=[f"{t}/{p.stem}" for t, p in CASES])
async def test_a_forced_handoff_is_labelled_forced_and_does_not_wait_for_the_orchestrator(
    tenant_id: str, path: Any
) -> None:
    scenario = load_scenario(path)
    stored: list[DecisionTrace] = []
    pickers: list[FunctionPicker] = []

    def wrap(container: Container) -> Container:
        original = container.traces.add

        async def add(trace: DecisionTrace) -> None:
            stored.append(trace)
            await original(trace)

        container.traces.add = add  # type: ignore[method-assign]
        picker = FunctionPicker(expert)
        pickers.append(picker)
        bot: Any = container.orchestrator
        from dataclasses import replace

        bot._deps = replace(bot._deps, move_picker=picker, agent_mode="on")
        return container

    result = await run_scenario(scenario, tenant_id, customize=wrap)
    assert not result.failures, result.failures
    handoffs = [t for t in stored if t.decision is Decision.HANDOFF and t.kind == "customer_turn"]
    assert handoffs, "the scenario was meant to hand over"
    assert all(t.escalation_kind == "forced" for t in handoffs), [
        (t.escalation_reason, t.escalation_kind) for t in handoffs
    ]
    turn, never_asked = FORCED[scenario.id]
    if never_asked:
        assert pickers[0].views == [], "a forced handoff in the front stages must not ask the orchestrator"
        assert handoffs[0].agent_steps == () and handoffs[0].fallback_reason is None
    if turn is not None:
        assert stored[turn].decision is Decision.HANDOFF
