# Progress: automatic tool testing (Part 1 of TOOLS_AUTOTEST_AND_PRODUCTION_PLAN.md)

Updated 2026-10-12. Nothing is committed (work is in the working tree on branch a+b+c-integration).

## Part 1 is implemented (plan steps 1–5)
- Generic fixture contract (`/_fixtures/*`) on the Nile sandbox and on Team C's service-desk test fixture; `team_c/team_c/fixtures.py` (`FixtureClient`); connector `fixtures` setting (sandbox only, key by env-var name).
- `team_c/team_c/autotest.py` (pure plan) and `services/autotest.py` (run, state trying, owner questions, diagnostics, sandbox-only optional publish, never calls model repair), `readiness_state`, API (`POST /artifacts/{id}/auto-test`, `GET /auto-test-runs/{id}`, `GET /businesses/{bid}/artifacts`), artifact page card.
- Team B: `team_c_admin` (artifacts, auto_test, auto_test_run), `POST /v1/dashboard/business-tools/artifacts/{id}/auto-test` (manager), proposal rows carry `artifact_id`, `readiness_state`, `auto_test_status`, `auto_test_question`; dashboard button + badge + polling, en/ar.
- `integration/team_c_lifecycle.py` uses one auto-test call (`--manual-tests` keeps the old path); `integration/serve.py` gives Team C `NILE_SANDBOX_FIXTURE_KEY`; new `integration/autotest_check.py`.
- Docs: `team_c/README.md`, `Docs/INTEGRATION.md`, `team_b/CLAUDE.md`.

## Verified
Team C 646 + 1 new (test_autotest 11, test_nile_fixtures 3 included in the full run except the last two added tests: re-run once), Team B 3836 passed, dashboard 50 passed. Team A untouched (not re-run).

## Real run (2026-10-12, Nile sandbox + Team C + Groq)
`team_c_lifecycle.py --fresh --tool X` for all nine tools (order, ticket, orders, return_, exchange, refund, cancel, address, voucher): each reached ready_to_publish from ONE auto-test call (own_record x2 + cross_user, or the success pair for `orders`; bad-credential and invalid-argument diagnostics passed; writes confirmed by the fixture read-back; cancel/address needed a second record state). Found and fixed: a model-named input `order_id_input` was not matched to the fixture kind (now matched by prefix/suffix, tested).
The results were then rolled back: integration/var/team_c.json and lifecycle_*.json are restored from `integration/var/backup_pre_autotest/` (the run's own copies are in `integration/var/autotest_run/`; Team C's integration DB keeps the extra test businesses). Servers were stopped.

## Verification finished (2026-10-12, later)
Team C 649 passed, Team A 973 passed (schema.sql restored), Team B 3836, dashboard 50. Real stack: `e2e_chat.py` 33/33 (after binding the sandbox publications), `e2e_live_chat.py` 22/22 (binding back on live_demo, as it was), `live_demo_check.py` 12/12 (GitHub public API). Servers stopped; `integration/var/team_c.json` is on live_demo for get_order and create_ticket again. Note: the e2e_chat run disables/republishes the sandbox order publication (new publication rows in Team C's integration DB).

## Follow-ups done (2026-10-12, evening)
- Per-business `auto_publish_sandbox` setting (migration 8 `business_settings`, API + artifact page); Team B no longer sends `publish_when_ready`.
- `auto-*` identity pruning after every run (`AutoTest.prune_identities`, `Connectors.remove_identity`).
- Fixtures form on the artifact page (`save_fixtures`, `save_business_settings` actions).
- `integration/autotest_check.py --fresh` ran for all nine tools: 8 passed in the main run; `address` stopped when the proposal reviewer rated its proposal "revise" (model variance, before the automatic tests) and passed on a retry with `--variant 2` (own business). Every tool: ready_to_publish from one call, evidence tests all `auto.*`, state published. Lifecycle fixes: skip policy requirement answers once the policy is accepted; `--variant`. The binding/state were restored to the live_demo demo state afterwards (run copies in `integration/var/autotest_check_run/`).

## Part 2 (production) built and run for real (2026-10-12, night)
D1-D5 accepted as in the plan. Team C: production environment + kill switch + `secret_ref` (`env:` or `file:`), service-credential execution with trusted per-call context (`mcp_server --context-from-client`), production artifact (blockers for unrestricted / api_filtered / list scopes), promotion service + `promotions` table (checklist, smoke check, idempotency decision, rollback contact), rollback, error-spike auto-disable, per-connector and per-customer budgets, append-only audit, admin token (`TEAM_C_ADMIN_TOKEN`). Team B: `production` environment in the Team C client (separate service process, verified customer as `_meta.ticketeasy.context`), admin routes promote / rollback, dashboard PRODUCTION badge, banner, promotion dialog, rollback (en + ar). Nile: `NILE_SANDBOX_SERVICE_TOKEN` mode; `serve.py nile-prod` (port 8104) via `integration/production.env`. Docs: team_c/README "Production", Docs/INTEGRATION.md, team_b/CLAUDE.md.
Verified: Team C 671, Team B 3845 (+ruff, mypy), dashboard 51; `integration/production_check.py` phases promote (21/21) and chat (15/15) against the real services; `e2e_chat` 33/33, `e2e_live_chat` 22/22, `live_demo_check` 12/12 after the change. Integration state restored to the live_demo binding (backup in integration/var/backup_pre_production).
Deviations / limits: `PRODUCTION_PRIVATE_HOSTS` exists as a development-only exception (needed for the local deployment); Team C pages are read-only for production (promotion/connector creation only through the admin API / Team B); auto-disable is Team C's own watch (not Team B's `action_failing` hook); list / data-scope tools cannot be promoted (no `output_rows_match_context`); the idempotency-header path is unit-tested only (no API here declares one); nothing was run against a real external production API.

## Still to do
1. A real external production API with its own service credential (and an EMPTY PRODUCTION_PRIVATE_HOSTS) before any real customer data.
2. `output_rows_match_context` for list tools; per-customer OAuth (later option); Team B alert hook; a Team C page form for the production connector if wanted (today: admin API).
3. The e2e scripts keep ids of the last publication in integration/var/lifecycle_*.json: after manual republishing they go stale (re-point them or restart Team B).

## Gotchas
- `team_c/team_c/web.py` is dead; live code is the `web/` package.
- Test names: `^[A-Za-z0-9_.-]{1,80}$` → `auto.<scenario>.<label>`.
- `runs.input_hash` stores `digest(payload)`; look runs up with `digest(aid)`.
- In this shell, write edit scripts with the file tools (heredoc escapes corrupt `\n`).
