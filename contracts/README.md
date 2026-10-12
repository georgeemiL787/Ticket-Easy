# Contracts

Every service publishes its data formats as JSON Schemas generated from its own models; nothing is duplicated here.

| Service | Folder | Regenerate |
|---|---|---|
| Knowledge (`team_a/`) | `team_a/contracts/schemas/` | `python -m team_a export-schemas` |
| Agent (`team_b/`) | `team_b/contracts/schemas/` (traces, handoff, dashboard, `TenantBundle`) | `make schemas` in `team_b/` |
| Tools (`team_c/`) | its REST/MCP contracts are in `team_c/team_c/contracts.py` | tests in `team_c/tests/test_contracts.py` |

Drift tests fail when a committed schema no longer matches its model. The dashboard's types are generated from the agent's dashboard schemas (`npm run types`).
