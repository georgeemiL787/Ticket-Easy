# E0 branch health check (16 October 2026, branch eval-cost-report)

| Suite | Result |
|---|---|
| conversation service `make test` | 4053 passed, 1 failed, 1 deselected |
| conversation service `make scenarios` | 83 active, 83 passed, 0 pending, 0 failing |
| conversation service `make lint` | mypy clean; ruff reports 2 long lines (E501) and 2 files needing format |
| knowledge service pytest | 993 passed |
| tool service pytest | 709 passed, 1 skipped |
| dashboard typecheck | clean |
| dashboard `npm test` | 103 passed, 1 failed |

## Failures left alone (none block the evaluation commands)
- `team_b/tests/unit/test_export_schemas.py::test_committed_schemas_match_the_models`: committed JSON schemas are stale; the tool service models gained a `sandbox_only` field. Fix is regenerating schemas (`make schemas`).
- ruff E501 in `team_b/src/team_b/adapters/team_c_tools.py:278` and `team_b/tests/unit/adapters/test_llm_policy_gate.py:218`, plus 2 files not ruff-formatted (changes from the earlier uncommitted work).
- `dashboard/src/test/knowledge.test.tsx` "knowledge page lists documents...": expects theme `aurora-light`, gets null.

No code changed in E0. No test skipped, deleted or loosened.
