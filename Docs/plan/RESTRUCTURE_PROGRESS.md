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
