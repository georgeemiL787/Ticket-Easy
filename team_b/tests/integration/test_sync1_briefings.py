"""SYNC 1: every handoff case opened in any scenario (by Track A's flow or Track B's) passes the completeness check."""

from pathlib import Path

import pytest

from team_b.brain.handoff import incomplete
from team_b.container import Container
from team_b.domain.handoff import HandoffCase
from tests.integration.scenario_format import discover, load_scenario
from tests.integration.scenario_runner import run_scenario

ACTIVE = [(t, p) for t, p in discover() if load_scenario(p).status == "active"]


@pytest.mark.parametrize(("tenant_id", "path"), ACTIVE, ids=[f"{t}/{p.stem}" for t, p in ACTIVE])
async def test_every_case_opened_in_a_scenario_has_a_complete_briefing(tenant_id: str, path: Path) -> None:
    opened: list[HandoffCase] = []

    def record(container: Container) -> Container:
        original = container.cases.add

        async def add(case: HandoffCase) -> None:
            opened.append(case.model_copy(deep=True))
            await original(case)

        container.cases.add = add  # type: ignore[method-assign]
        return container

    result = await run_scenario(load_scenario(path), tenant_id, customize=record)
    assert not result.failures, result.failures
    for case in opened:
        assert incomplete(case) == [], f"{case.case_id} ({case.package.reason.value}): {incomplete(case)}"


def test_the_scan_covers_cases_of_both_tracks() -> None:
    reasons = {load_scenario(p).final.case_reason for _, p in ACTIVE}
    assert {"approval_required", "policy_denied", "mandatory_risk", "no_evidence"} <= reasons
