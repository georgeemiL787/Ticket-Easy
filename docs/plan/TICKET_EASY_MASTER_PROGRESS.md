# Progress: Ticket-Easy master plan (`TICKET_EASY_MASTER_PLAN.md`)

Updated 2026-10-12. Branch `the integration branch`, nothing committed. The open decisions D1 to D9 were answered with the plan's own
recommendations (see "Decisions taken" below).

| Phase | State |
|---|---|
| P0 Baseline and safety net | done |
| P1 Design tokens (light aurora) | done |
| P2 Contracts, tenant bundle, orchestrator consumption | done |
| P3 Swagger/OpenAPI to tools, three screens | done (real-stack check written, not run) |
| P4 RAG ingestion and guardrails | done (real-stack check not run) |
| P5 Onboarding wizard | done (real-stack check not run) |
| P6 Restyle and unify | done, except screenshots of every page (see below) |
| P7 Repo restructuring | partly done on purpose (see below) |

## What was and was not verified

Run and green (after the last change of each area): the knowledge service pytest, the agent pytest (memory, plus the sqlite run for P2), the agent
`ruff check`, `ruff format --check`, `mypy`, the tools service pytest, dashboard `npm test`, `npm run typecheck`, a verify-only
`vite build`, `docker compose --profile light config`, the contrast script. Every phase has unit and in-process tests for its API.

**Not run** (they need the whole stack, a model provider for the tools service and they change state): `ops/local/e2e_chat.py`,
`e2e_live_chat.py`, `live_demo_check.py`, `autotest_check.py`, `production_check.py`, and the new
`ops/local/tools_flow_check.py`. The scripts that exist are syntax-checked only. Screenshots in the browser pane
(desktop/mobile, EN/AR) were not taken either; no claim about how the pages look is made here. The AI-dependent steps (a
model proposing a tool, rules or intents) are covered with the repository's deterministic model substitutes, not a live model.

## Decisions taken (the plan's recommendations)
D1 screens in the dashboard (the agent proxies the tools service) · D2 in-house Swagger 2.0 converter · D3 wizard businesses run in agent mode ·
D4 the knowledge service keys bound to tenants (off when unset) · D6 migrations 007 (bundles) and 008 (onboarding drafts) · D7 Fernet for
stored test credentials · D8 light theme opt-in per surface · D9 darker `-text` tokens for text. D5 (repo moves): see P7.

## P1 · Design tokens
`services/agent/web/shared/aurora-light.css` (new; `aurora.css` imports it; the dashboard imports the same file, so there is one source),
scoped to `:root[data-theme="aurora-light"]`, so nothing existing changes. Buttons, header/nav, states, badges, cards, focus
ring, 44px targets, reduced motion, fonts incl. IBM Plex Sans Arabic. `services/agent/scripts/check_contrast.py` + `tests/unit/test_contrast.py`
(text roles >= 4.5, accents >= 3). `services/agent/web/shared/tokens.html` is a preview page (not screenshotted).

## P2 · Bundles
`domain/bundle.py`, `adapters/bundle_repository.py` (memory + sqlite, migration 007), `bundles.py` (BundleRegistry = the
TenantRegistry the core reads, hot swap, rollback, listeners, publish checks), `api/bundles.py`, CLI `python -m agent_service bundle`,
`TenantBundle.schema.json`, `AGENT_BUNDLES` flag (default on; 0 = files only), `ToolCapabilities.on_bundle_change`, traces carry
`bundle_version` and the bundle hash. Tests: model fixtures, both stores, registry (precedence, immutability, rollback, concurrency,
restart), API with roles, **all 67 shop_001 scenarios decide the same on the imported bundle** (`tests/integration/test_bundle_parity.py`).

## P3 · Tools in three screens
The tools service: `swagger2.py` (Swagger 2.0 to OAS 3.0.3), `classify.py` (read/write/risky/restricted), apiKey (header/query) and HTTP basic in
`artifacts.py`/`executor.py`, `secretbox.py` (Fernet; test credentials sealed at rest, start-up sweep seals old rows, `cryptography`
pinned), upload limit 10 MiB and 1000 operations, `services/batches.py` + `api/batches.py` (operations list, connection check,
tool batches: prepare, approve with the four effect flags, publish to the TEST-ONLY sandbox), migration 10 `tool_batches`,
`TOOLS_PRODUCT_URL` redirect and banner. The agent: `api/tool_setup.py`, `tool_setup.py`, draft bundle on publish, explicit activate.
Dashboard: `/tools/connect`, `/tools/review`, `/tools/publish`. Not done: a decrypt-back script for the credential sealing (the key file
is the way back); Swagger `discriminator`/composition constructs stay "unsupported" per operation like before.
Owner effort: import, prepare, approve, publish, activate (plus the optional connection check): see `ops/local/tools_flow_check.py`.

## P4 · Documents and guardrails
The knowledge service: parsers for txt/html, `knowledge/documents.py` (upload, status, versions, atomic re-index with a scratch folder and swap,
per-tenant lock), `jobs.py`, `admin_api.py`, onboarded tenants live under `var/tenants/<t>`, `X-Service-Key` on the public endpoints
bound to tenants (`SERVICE_API_KEY`, `SERVICE_API_KEYS`), rules history, stale rules (not enforced), `ruleset_version`, new error codes
FORBIDDEN / INVALID_STATE (schema regenerated). The agent: `adapters/knowledge_admin_client.py`, simulated service, `api/knowledge.py`, bundle publish check
(approved ruleset must be current), `refresh_knowledge` on drafts. Dashboard `/knowledge` and `/guardrails`. The violation
behaviours (deny refuses, require_human hands over, the knowledge service down hands over) were already covered by scenarios S05, S06, S16, S33, S34.
Limits: rule extraction still uses the e-commerce vocabulary (`data/shared/vocabulary.json`); no per-category vocabulary yet.

## P5 · Onboarding wizard
`domain/onboarding.py`, migration 008, `onboarding.py` (config generation), `api/onboarding.py`, `preview.py`, public chat answers
TENANT_NOT_LIVE for a draft-only business, staff test with a signed pass (`/chat?tenant_id=&preview=`), traces marked `preview`,
Go live blocked until documents are indexed, a rule is approved, a tool is published, a verifying tool is chosen and the test chat was
used. Dashboard `/onboarding` and `/onboarding/:id`. Not verified end to end with real knowledge and tools services/model (see above).

## Baselines vs now
| Suite | Baseline (2026-10-12) | Now |
|---|---|---|
| The knowledge service pytest | 973 | 992 |
| The agent pytest | 3844 (+1 flake), 3849 with P0 tests | 4036 (ruff, format, mypy clean) |
| The tools service pytest | 671 | 704 |
| Dashboard vitest | 51, 68 after P0 | 104 |

## P6 · Restyle and unify
The light aurora theme is now the default (`aurora-light.css` on `:root`, `aurora.css` rewritten light, dark block and blobs gone).
Chat: centred 720px column, customer teal on the right, assistant on glass, a person from customer care in magenta with a badge,
error banner, language switch, `?preview=` pass. Inbox: light cards, urgent rows get a red edge, list and case take turns below 900px
with a back button. `web/shared/i18n.js` gives chat and inbox English and Arabic (RTL). Dashboard: light tokens, sidebar with icons, open-alerts badge
and a menu button on phones, tables become card lists on phones, charts recoloured. "The knowledge service/B/C" no longer appears in any shown text
(dashboard texts, the tools service pages, tool-service messages); `tests/unit/test_product_names.py` guards it.
Looked at in the browser pane: the chat (English, desktop) and the dashboard's connect screen (English, desktop). **Not** looked at:
Arabic/RTL, mobile widths, the inbox, the other dashboard pages, Chat/Inbox with real conversations.

## P7 · Repo restructuring (D5: only the low-risk part)
Done: root `README.md` product framing, `contracts/README.md` index.
Not done, on purpose: renaming `Docs/` to `docs/`, moving `knowledge_service|b|c/` and `dashboard/` into new folders (venvs are not movable on Windows,
and CI, Docker and `serve.py` paths all change), removing `services/tools/.git`, dead code and scratch databases (needs your confirmation first).
These can be done later one step at a time; nothing else depends on them.

## QA pass through the running stack (2026-10-13)

Driven through the in-app browser and the real HTTP surface of the local stack; repeatable checks are in `ops/local/qa_journeys_check.py` (17 checks) next to the unit suites.

Fixed: tool binding for the demo shop pointed at the switched-off Live Demo (every order lookup became a handoff); a setup refusal from the tools service (`TOOLS_REJECTED`, `IDENTITY_NOT_MAPPED`) was retried by the customer instead of reaching a person; the web-address import said only `fetch_host`/`fetch_failed` (now plain wording, local addresses allowed in the local stack); the API address is pre-filled from the address the description was read from; a failing tool test now links to the test-data form; "Done" after activation named the whole tool list including a human-only tool, now it names what the batch added; the Knowledge page failed for a business with no upload yet; the test-chat button had the hint text as its label; source codes such as `refund_policy@v1#s1` are only shown in the developer view; the inbox conversation includes what colleagues wrote; the knowledge service's database build failed on a business added through the admin API.

Known limits: the stack pins the service date to 2026-09-28 (`AGENT_FIXED_TODAY`, the demo orders are dated against it), so the dashboard's "last 7 days" is empty and inbox ages read wrong against the real date; the knowledge service needs `OPENROUTER_API_KEY` for "Suggest rules" (Go live needs an approved rule); without an AI model the rules-only understanding misses loosely worded policy questions (a fallback search lowered the evaluation set's decision score, so it was not kept); no browser automation is installed (Playwright was not downloaded); the tools service test `test_target_system` depends on a project outside this repository.
