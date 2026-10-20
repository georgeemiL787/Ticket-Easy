# E0 branch health check (16 October 2026, branch eval-cost-report)

| Suite | Result |
|---|---|
| conversation service `make test` | 4053 passed, 1 failed, 1 deselected |
| conversation service `make scenarios` | 83 active, 83 passed, 0 pending, 0 failing |
| conversation service `make lint` | mypy clean; ruff reports 2 long lines (E501) and 2 files needing format |
| knowledge service pytest | 993 passed |
| tool service pytest | 709 passed, 1 skipped |
| dashboard typecheck | clean |
| dashboard `npm test` | 103 passed, 1 failed at first; fixed, now 104 passed (3 runs in a row) |

## Failures left alone (none block the evaluation commands)
- `orchestrator/tests/unit/test_export_schemas.py::test_committed_schemas_match_the_models`: committed JSON schemas are stale; the tool service models gained a `sandbox_only` field. Fix is regenerating schemas (`make schemas`).
- ruff E501 in `orchestrator/src/team_b/adapters/team_c_tools.py:278` and `orchestrator/tests/unit/adapters/test_llm_policy_gate.py:218`, plus 2 files not ruff-formatted (changes from the earlier uncommitted work).

No code changed in E0. No test skipped, deleted or loosened.

## Fixed after the first run
- Dashboard: the tests in `knowledge.test.tsx`, `onboarding.test.tsx` and `toolSetup.test.tsx` read the page theme right after the heading appeared, before React ran the effect that sets it. A different one of them failed on each full run (a timing race, not a product bug). The assertion now waits for the theme with `waitFor`. The expectation is unchanged.
