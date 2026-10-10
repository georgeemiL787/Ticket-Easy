"""The lookup and knowledge scenarios, run again in agent mode with a scripted orchestrator (the "agent" runner).

The original runner (test_scenarios.py) keeps running every scenario with the usual flow (agent off). Here the same
scripts, with the same expectations, run through the agent loop: a scripted picker (agent_expert.py) proposes the moves,
the real loop checks and works them, and the stand-in shop, policy search and rule checker answer. Each of these
scenarios must end the same way in both modes.
"""

import pytest

from tests.integration.agent_expert import with_expert
from tests.integration.scenario_format import discover, load_scenario
from tests.integration.scenario_runner import run_scenario

# the knowledge scenarios (S01, S02, S10, S17, S25, S34) and the lookup scenarios (S03, S09, S12, S15, S28, S29),
# plus the greetings (S00, S40) the same picker handles
AGENT_SCENARIOS = ("S00", "S01", "S02", "S03", "S09", "S10", "S12", "S15", "S17", "S25", "S28", "S29", "S34", "S40")
CASES = [(tenant, path) for tenant, path in discover() if path.name.split("_")[0] in AGENT_SCENARIOS]


def test_every_agent_scenario_exists() -> None:
    assert {p.name.split("_")[0] for _, p in CASES} == set(AGENT_SCENARIOS)


@pytest.mark.scenario
@pytest.mark.parametrize(("tenant_id", "path"), CASES, ids=[f"{t}/{p.stem}" for t, p in CASES])
async def test_scenario_in_agent_mode(tenant_id: str, path: object) -> None:
    scenario = load_scenario(path)  # type: ignore[arg-type]
    result = await run_scenario(scenario, tenant_id, customize=with_expert)
    assert not result.failures, f"{scenario.id} ({scenario.title}) failed in agent mode:\n  " + "\n  ".join(
        result.failures
    )
    assert result.outcome == "passed"


@pytest.mark.parametrize(("tenant_id", "path"), CASES, ids=[f"{t}/{p.stem}" for t, p in CASES])
async def test_no_order_data_before_identity_is_verified_in_agent_mode(tenant_id: str, path: object) -> None:
    """The leak scan of test_a_leak_scan.py, with the agent loop doing the work."""
    from team_b.container import Container
    from team_b.domain.trace import DecisionTrace
    from tests.integration.scenario_runner import conversation_id_of
    from tests.integration.test_a_leak_scan import GATED_TOOLS, TOKENS

    scenario = load_scenario(path)  # type: ignore[arg-type]
    stored: list[DecisionTrace] = []

    def record(container: Container) -> Container:
        original = container.traces.add

        async def add(trace: DecisionTrace) -> None:
            stored.append(trace)
            await original(trace)

        container.traces.add = add  # type: ignore[method-assign]
        return with_expert(container)

    await run_scenario(scenario, tenant_id, customize=record)
    verified = False
    for number, trace in enumerate([t for t in stored if t.conversation_id == conversation_id_of(scenario)], start=1):
        verified = verified or trace.identity.verified
        if verified:
            continue
        label = f"{scenario.id} turn {number}"
        found = [t for t in TOKENS if t.lower() in trace.response_text.lower()]
        assert not found, f"{label}: order data in a reply before verification: {found}"
        gated = [c.tool for c in trace.tool_calls if c.tool in GATED_TOOLS and c.status == "success"]
        assert not gated, f"{label}: read {gated} before verification"
