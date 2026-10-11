"""Every Decision and every EscalationReason is specified by at least one scenario (pending or active).

A value counts as specified when a scenario expects it: expect.decision, expect.escalation or final.case_reason.
Values that are not real enum members fail too, so a typo in a scenario cannot hide a gap.
"""

from team_b.domain.decision import Decision, EscalationReason
from tests.integration.scenario_format import Scenario, discover, load_scenario


def scenarios() -> list[Scenario]:
    return [load_scenario(path) for _, path in discover()]


def expected_values(all_scenarios: list[Scenario]) -> tuple[set[str], set[str]]:
    decisions: set[str] = set()
    reasons: set[str] = set()
    for scenario in all_scenarios:
        for turn in scenario.turns:
            if turn.expect.decision:
                decisions.add(turn.expect.decision)
            if turn.expect.escalation:
                reasons.add(turn.expect.escalation)
        if scenario.final.case_reason:
            reasons.add(scenario.final.case_reason)
    return decisions, reasons


def test_every_decision_is_specified_by_a_scenario() -> None:
    decisions, _ = expected_values(scenarios())
    missing = sorted(d.value for d in Decision if d.value not in decisions)
    assert not missing, f"no scenario expects these decisions: {missing}"


def test_every_escalation_reason_is_specified_by_a_scenario() -> None:
    _, reasons = expected_values(scenarios())
    missing = sorted(r.value for r in EscalationReason if r.value not in reasons)
    assert not missing, f"no scenario expects these escalation reasons: {missing}"


def test_scenarios_only_expect_values_that_exist() -> None:
    decisions, reasons = expected_values(scenarios())
    unknown_decisions = sorted(decisions - {d.value for d in Decision})
    unknown_reasons = sorted(reasons - {r.value for r in EscalationReason})
    assert not unknown_decisions, f"unknown decisions in scenarios: {unknown_decisions}"
    assert not unknown_reasons, f"unknown escalation reasons in scenarios: {unknown_reasons}"


def test_scenario_ids_are_unique_and_match_their_file_names() -> None:
    found = [(scenario.id, path.name) for (_, path), scenario in zip(discover(), scenarios(), strict=True)]
    ids = [i for i, _ in found]
    assert len(ids) == len(set(ids)), f"duplicate scenario ids: {sorted(i for i in ids if ids.count(i) > 1)}"
    wrong = [name for i, name in found if not name.startswith(f"{i}_")]
    assert not wrong, f"file name must start with the scenario id: {wrong}"


def reasons_by_status(all_scenarios: list[Scenario], status: str) -> set[str]:
    return expected_values([s for s in all_scenarios if s.status == status])[1]


def test_every_escalation_reason_is_covered_by_an_active_scenario_or_waits_on_a_pending_one() -> None:
    """The goal is all 14 reasons produced by an active scenario. Until Track A's flows exist, the rest must at least
    be waiting in a pending scenario, so no reason is left without a plan. Print the gap with `pytest -s`."""
    every = scenarios()
    active, pending = reasons_by_status(every, "active"), reasons_by_status(every, "pending")
    uncovered = sorted(r.value for r in EscalationReason if r.value not in active | pending)
    assert not uncovered, f"no scenario at all produces: {uncovered}"
    print("reasons not yet produced by an ACTIVE scenario:", sorted(pending - active))
