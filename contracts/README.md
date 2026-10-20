# Contracts

Every service publishes its data formats as JSON Schemas generated from its own models; nothing is duplicated here.

| Service | Folder | Regenerate |
|---|---|---|
| Knowledge (`rag/`) | `rag/contracts/schemas/` | `python -m team_a export-schemas` |
| Agent (`orchestrator/`) | `orchestrator/contracts/schemas/` (traces, handoff, dashboard, `TenantBundle`) | `make schemas` in `orchestrator/` |
| Tools (`tool_generation_mcp/`) | its REST/MCP contracts are in `tool_generation_mcp/team_c/contracts.py` | tests in `tool_generation_mcp/tests/test_contracts.py` |

Drift tests fail when a committed schema no longer matches its model. The dashboard's types are generated from the agent's dashboard schemas (`npm run types`).
