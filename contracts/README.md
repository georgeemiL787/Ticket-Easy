# Contracts

Every service publishes its data formats as JSON Schemas generated from its own models; nothing is duplicated here.

| Service | Folder | Regenerate |
|---|---|---|
| Knowledge (`services/knowledge/`) | `services/knowledge/contracts/schemas/` | `python -m knowledge_service export-schemas` |
| Agent (`services/agent/`) | `services/agent/contracts/schemas/` (traces, handoff, dashboard, `TenantBundle`) | `make schemas` in `services/agent/` |
| Tools (`services/tools/`) | its REST/MCP contracts are in `services/tools/tools_service/contracts.py` | tests in `services/tools/tests/test_contracts.py` |

Drift tests fail when a committed schema no longer matches its model. The dashboard's types are generated from the agent's dashboard schemas (`npm run types`).
