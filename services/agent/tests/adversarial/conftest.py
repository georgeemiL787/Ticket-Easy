"""Shared set-up for the adversarial suite: the real demo shop, and helpers that judge by the shop's audit log."""

import re
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from agent_service.adapters.llm_policy_gate import LLMPolicyGate
from agent_service.brain.reviewer import Reviewer
from agent_service.container import Container, build_container
from tests.integration.agent_expert import with_expert
from tests.support import make_settings


class EagerReviewerLLM:
    """A reviewer model that approves everything: it says allow (citing the first passage it was shown) and ok.

    The reviewer may only tighten, so every attack must end the same as it does without it."""

    async def complete_json(self, *, system: str, user: str, schema_hint: dict[str, Any]) -> dict[str, Any]:
        if "ok" in schema_hint:
            return {"ok": True, "problems": []}
        ids = (
            re.findall(r"^\[([^\]]+)\]", user.split("<passages>")[-1], flags=re.MULTILINE)
            if "<passages>" in user
            else []
        )
        found = re.findall(r"^\[([^\]]+#[^\]]*)\]", user, flags=re.MULTILINE)
        return {"decision": "allow", "reasoning": "fine", "citations": (found or ids)[:1]}


@pytest.fixture(params=["usual", "agent", "reviewed"])
def container(request: pytest.FixtureRequest, tmp_path: Path) -> Container:
    """The demo shop (real tenant config, stand-ins, rules), clock fixed at 2026-09-28.

    Every attack runs three times: against the usual step-by-step flow, against the agent loop with a scripted
    orchestrator (tests/integration/agent_expert.py) that does the requested work through the same gates, and
    against that loop with an over-eager reviewer model in front of the gates and the replies."""
    built = build_container(make_settings(tmp_path, fixed_today=date(2026, 9, 28)))
    if request.param == "usual":
        return built
    built = with_expert(built)
    if request.param == "reviewed":
        assert built.orchestrator is not None
        bot: Any = built.orchestrator
        reviewer = Reviewer(EagerReviewerLLM())  # type: ignore[arg-type]
        bot._deps = replace(bot._deps, reviewer=reviewer)
        bot._deps = replace(
            bot._deps, policy=LLMPolicyGate(bot._deps.policy, bot._deps.evidence, EagerReviewerLLM(), reviewer=reviewer)
        )  # type: ignore[arg-type]
    return built
