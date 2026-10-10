"""The knowledge scenarios in both modes with the knowledge agent switched on (a deterministic fake model).

S01, S02, S10, S17, S25, S34, S39 and S41 run through knowledge.answer (the usual flow, agent mode off) and again
through the agent loop (agent mode on, with the scripted orchestrator). The fake model writes the search, gives up on
a question the documents do not cover, and answers with the first passage word for word, so the expectations of the
scenario files hold unchanged. (The second search is covered in test_knowledge_agent.py.)
"""

from dataclasses import replace
from typing import Any

import pytest

from team_b.brain.knowledge_agent import KnowledgeAgent
from team_b.container import Container
from tests.fakes import FunctionPicker, KnowledgeFakeLLM
from tests.integration.agent_expert import expert
from tests.integration.scenario_format import discover, load_scenario
from tests.integration.scenario_runner import run_scenario

KNOWLEDGE = ("S01", "S02", "S10", "S17", "S25", "S34", "S39", "S41")
CASES = [(tenant, path) for tenant, path in discover() if path.name.split("_")[0] in KNOWLEDGE]


def with_agent(container: Container, *, agent_mode: str) -> Container:
    assert container.orchestrator is not None
    bot: Any = container.orchestrator
    changes: dict[str, Any] = {"knowledge_agent": KnowledgeAgent(KnowledgeFakeLLM()), "agent_mode": agent_mode}
    if agent_mode == "on":
        changes["move_picker"] = FunctionPicker(expert)
    bot._deps = replace(bot._deps, **changes)
    return container


def test_every_knowledge_scenario_exists() -> None:
    assert {p.name.split("_")[0] for _, p in CASES} == set(KNOWLEDGE)


@pytest.mark.scenario
@pytest.mark.parametrize("mode", ["off", "on"])
@pytest.mark.parametrize(("tenant_id", "path"), CASES, ids=[f"{t}/{p.stem}" for t, p in CASES])
async def test_knowledge_scenario_with_the_knowledge_agent(tenant_id: str, path: object, mode: str) -> None:
    scenario = load_scenario(path)  # type: ignore[arg-type]
    result = await run_scenario(scenario, tenant_id, customize=lambda c: with_agent(c, agent_mode=mode))
    assert not result.failures, f"{scenario.id} failed (agent mode {mode}):\n  " + "\n  ".join(result.failures)
