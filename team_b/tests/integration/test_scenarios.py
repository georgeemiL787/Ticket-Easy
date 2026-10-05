"""Scripted conversations: the acceptance test for every later step.

Each file scenarios/<tenant>/*.json holds one scenario. A scenario runs on a fresh container (stand-in mode, memory
stores, clock fixed at setup.today). "pending" scenarios are skipped with their reason, so the suite stays green while
features are missing; flip them to "active" when the feature lands. Every active scenario also passes the global safety
check: every write in the stand-in shop's audit log has a matching allow (or require_human + approval) policy entry in
that conversation's traces.

Format (strict: an unknown field is an error; models in scenario_format.py):

  {"id", "title", "status": "active" | "pending", "pending_reason",   # a pending scenario must give its reason
   "setup":  {"today": "YYYY-MM-DD"},                                 # default 2026-09-28
   "inject": [{"plug": "shop" | "policy_search" | "rule_checker" | "safety_screen" | "llm",
               "operation",    # shop tool name, or policy_search operation
               "mode": "fail" | "timeout" | "uncertain" | "no_audit" | "unpublish",
               "code",         # mode fail: BACKEND_UNAVAILABLE (default), TIMEOUT or NOT_FOUND
               "times"}],      # how many calls fail (default 1); applied before the first turn
   "turns":  [{"say": "...",   # a customer message, and/or:
               "human": {"action": "claim" | "reply" | "approve" | "reject" | "resolve" | "return_to_agent",
                         "text"},          # done on the conversation's handoff case, before "say"
               "advance_days": N,          # move the clock forward before the turn
               "expect": {"decision", "escalation", "awaiting", "locale",   # exact; null = none
                          "citations_include", "citations_exclude",          # lists of passage ids
                          "text_contains",        # all of these (case-insensitive)
                          "text_contains_any",    # at least one of these
                          "text_not_contains"}}], # none of these
   "final":  {"executed_tools": [...],   # successful, non-replayed writes, in order
              "audit_count": N,          # every call that reached the stand-in shop, reads included
              "no_writes": true,         # no write call reached the shop at all
              "case_reason", "case_priority",   # of the conversation's handoff case; null = no case
              "case_has_pending_approval": bool,
              "trace_invariants": true}} # traces valid; every reply matches its stored trace

A turn's expect is checked against the reply to "say"; a human-only turn is checked against the latest trace.
Only keys that are present are checked. Run: make scenarios (the same files, with a summary table).
"""

from pathlib import Path

import pytest

from tests.integration.scenario_format import discover, load_scenario
from tests.integration.scenario_runner import run_scenario

CASES = [(tenant, path) for tenant, path in discover()]


@pytest.mark.scenario
@pytest.mark.parametrize(("tenant_id", "path"), CASES, ids=[f"{t}/{p.stem}" for t, p in CASES])
async def test_scenario(tenant_id: str, path: Path) -> None:
    scenario = load_scenario(path)
    result = await run_scenario(scenario, tenant_id)
    if result.outcome == "skipped":
        pytest.skip(f"{scenario.id} pending: {result.skip_reason}")
    assert not result.failures, f"{scenario.id} ({scenario.title}) failed:\n  " + "\n  ".join(result.failures)
