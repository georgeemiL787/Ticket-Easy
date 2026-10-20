# Restructure progress

Where the restructure stands, step by step. The baseline below was measured on `main` at commit `69feeca` on 20 October 2026,
before any step. Every step must end with the same tests passing.

## Baseline

| Suite | Passed | Failed | Skipped | Deselected |
|---|---|---|---|---|
| Agent service (`orchestrator/`) | 4,065 | 0 | 0 | 1 (the slow tests) |
| Knowledge service (`rag/`) | 1,002 | 0 | 0 | 0 |
| Tools service (`tool_generation_mcp/`) | 718 | 0 | 1 | 0 |
| Dashboard (`dashboard/`) | 104 | 0 | 0 | 0 |

The dashboard type check, its tests and its build also pass.

## Steps

| Step | Date | Suites | Notes |
|---|---|---|---|
| 2 (R2a) | 20 Oct 2026 | Agent 4,065 passed (1 deselected), knowledge 1,002 passed, tools 718 passed (1 skipped), dashboard 104 passed | The three services moved under services/ (agent, knowledge, tools), the dashboard to apps/dashboard and the local scripts to ops/local. Every script, Dockerfile, compose file, workflow and path now points at the new folders. The agent image build also needed the shared stylesheet copied into its dashboard stage. Virtual environments must be recreated. |
