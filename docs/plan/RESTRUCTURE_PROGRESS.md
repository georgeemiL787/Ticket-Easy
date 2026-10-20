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
| 2 (R2b) | 20 Oct 2026 | Agent subset that reads the compose file, Makefile and docs paths passed; all three services healthy in Docker | The documentation folder is now docs/ (renamed in two steps because Windows ignores case), with its generated reports in docs/reports/, and every link and path into them was updated. The tools service has its own Dockerfile and a compose service in the live profile, so make demo-live starts all three services; its port is bound to this machine only and its secrets come from the environment. The agent is still wired to it by hand. |
| 3 (R3a) | 20 Oct 2026 | Knowledge 1,002 passed; the service starts with uvicorn knowledge_service.api:app and its Docker image is healthy | The knowledge service package is now knowledge_service (commands: python -m knowledge_service). Its web routes module is api.py and its parsing, indexing and search folder is search/. Every import, command, entrypoint, script and doc that used the old package name was updated; settings and database file names are renamed in a later step. |
| 3 (R3b) | 20 Oct 2026 | Tools 718 passed (1 skipped); agent 4,065 passed (1 deselected); tools service starts in Docker and locally | The tools service package is now tools_service (commands: python -m tools_service.mcp_server, uvicorn tools_service.web:create_app --factory) and the project is named ticket-easy-tools. Imports, commands, the agent binding example, scripts and docs follow. Comments and docstrings in the tools service no longer name a team. The header, MCP server name, stored values and file names are changed in later steps. |
| 3 (R3c) | 20 Oct 2026 | Agent 4,065 passed (1 deselected), 83 of 83 scenarios pass, lint and type checks clean; dashboard 104 passed; make demo healthy | The agent service package is now agent_service (commands: python -m agent_service, uvicorn agent_service.api.app:app) and the project is named ticket-easy-agent. Every import, the Dockerfile, Makefile, workflows, local scripts, dashboard type headers and docs follow. Only import lines changed in the safety-critical files. Import order and a few long lines were re-wrapped for the linter. |
