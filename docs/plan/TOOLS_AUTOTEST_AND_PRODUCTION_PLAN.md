# Plan: automatic tool testing, and real production publication

Status: plan only (2026-10-12). Written to be implemented step by step. Read the whole file before starting a phase.

## The two problems

1. **Testing a tool is manual and slow.** Before publishing, the owner must create dummy accounts in their sandbox,
   paste each account's credentials into the tools service, find records that belong to each account and are in the right state,
   and hand-write scenarios (own record ×2, cross-user refusal, data isolation). `ops/local/tools_lifecycle.py`
   shows the cost: every tool hard-codes identity → record pairs (`IDENTITIES`, `_UNSHIPPED`, carol/dan for orders
   that must be "processing" or "pending").
   **Goal:** one action ("Run automatic tests") that provisions what it needs, derives the scenarios, runs them,
   fixes what it safely can, and marks the tool **Ready to publish**, without the owner touching accounts or record ids.

2. **Tools never reach production.** Today a tool can be `sandbox` (TEST-ONLY) or `live_demo` (a real API, but
   `production_ready` is always false and there is no release process). A Live Demo tool also still runs as one
   dummy identity per demo customer.
   **Goal:** a real **production** publication: real API, the business's production credentials kept in a secret store,
   serving real verified customers, behind an explicit, auditable promotion, with every safety gate intact.

## Rules that do not change (check every phase against them)

- **Owner approval stays human.** Automation may test, diagnose, fix test *setup* and mark "ready". It never approves a
  proposal, never accepts an access policy, never approves a model repair, and never promotes to production by itself.
- **Evidence rules are not weakened.** `services/tools/tools_service/publishing.py` `evidence()` / `next_steps()` stay the
  definition of "may publish". Automation produces that evidence; it doesn't bypass it.
- **Writes are never retried blindly.** Uncertain outcomes stay uncertain (`partial`, `outcome_unknown`).
- **The model never sees credentials** and never chooses an identity.
- **The agent's gates are unchanged** (identity → ownership → the knowledge service `check_action` → customer confirmation → one
  execution → `verify_result`). Production is only another environment behind the same gates.
- **Every phase ships with tests**, and existing suites stay green: the knowledge service 973, the agent 3833, the tools service 634, dashboard 49,
  plus `ops/local/e2e_chat.py` (33), `e2e_live_chat.py` (22), `live_demo_check.py` (12).

---

# Part 1: automatic testing → Ready to publish

## 1.1 The Test Fixture contract (what makes it automatic)

The hard part is not running tests; it's having accounts and records in the right state. Add an optional, declared
**fixture provider** to a sandbox connector: a small set of endpoints the business's sandbox exposes for test setup
only. The Nile sandbox already has most of this (`/_harness/tokens`, `/_harness/state`, `/_harness/switches` in
`ops/local/nile_sandbox/app.py`).

Contract (JSON over HTTP, header `x-fixture-key`; never part of the OpenAPI document the tools service generates tools from):

| Endpoint | Purpose |
|---|---|
| `POST /_fixtures/accounts` `{label}` → `{account_id, credential: {token} \| {username, password}, context: {customer_id: …}}` | create a disposable test account |
| `POST /_fixtures/records` `{account_id, kind, state?, attributes?}` → `{record_id, kind, state}` | create a record owned by that account, optionally in a given state (`pending`, `delivered`, …) |
| `GET /_fixtures/kinds` → `[{kind, states, id_field, selects_by: ["order_id"]}]` | which record kinds and states exist, and which tool input selects them |
| `POST /_fixtures/reset` `{run_id}` | delete everything created for one test run |
| `GET /_fixtures/state?run_id=` | independent read-back of what changed (write verification) |

Connector config gains `fixtures: {base_url, key_ref}` (`key_ref` = an env var name, never the key itself).
**Only sandbox connectors may declare fixtures.** Live Demo and production connectors never get account creation.

If the API has **no** fixture provider, automatic testing still works partly (§1.4 fallback) and says plainly what
the owner must provide.

Implement:
- `ops/local/nile_sandbox/app.py`: add `/_fixtures/*` next to the existing harness routes. Records with a requested
  state = copy a template order and set `order_status` / dates so the state's preconditions hold. Keep the harness
  routes for the existing scripts.
- `services/tools/tools_service/fixtures.py` (new): `FixtureClient(connector, settings)` with `accounts()`, `record()`, `kinds()`,
  `reset()`, `state()`. Same safety as the executor: sandbox destinations only, no redirects, timeouts, size limits,
  and the key read from the environment variable named by `key_ref`.
- `services/tools/tools_service/services/connectors.py`: `save()` accepts optional `fixtures` (sandbox only); `listing()` shows
  `fixtures: configured | none` (never the key); the connection check gets a `fixtures` step (`GET /_fixtures/kinds`).

## 1.2 Derive the test plan from the artifact (deterministic, no model needed)

New `services/tools/tools_service/autotest.py` with `plan(artifact, view, fixture_kinds) -> TestPlan`. Derive scenarios from what
The tools service already knows. Don't invent new rules; mirror `publishing.evidence()` and `next_steps()` so the plan is exactly
what publication needs:

| Artifact fact | Scenarios generated |
|---|---|
| always | `success` as account A |
| record-scope requirement (`record_selectors(a)` non-empty) | `own_record` A on A's record, `own_record` B on B's record (same kind), `cross_user` B → A's record (expect failed) |
| data-scope requirement | success as A and B, each with own data; assert no shared record (the existing `outputs_records` fingerprints) |
| `api_filtered` data scope | the isolation pair above is required, not optional |
| write step with a state precondition (the operation documents 409 / the proposal names a required state) | A's record created in the required state |
| caller-access requirement | `bad_credential`: a fixture account whose credential is then revoked → expect `failed` / `authentication_failed` (diagnostic only, not publication evidence) |
| input schema | `invalid_arguments`: an extra argument → expect `rejected` before any request (diagnostic) |

**Choosing argument values:**
1. Record-selecting inputs: the `record_id` from the fixture created for that scenario's account.
2. Other inputs: schema `enum` / `default` / `example` / `format`. Only if none exists, the existing
   `Building.suggest_test_inputs` (model, never sees credentials). Model values are marked `source: model` in the plan.
3. Record state: map the operation's documented preconditions to fixture `states`. If ambiguous, try each state the
   kind offers, until one succeeds or all fail. Each attempt is a separate recorded run; a write's attempts each use a
   fresh record.

`TestPlan` = list of `{name, scenario, identity_label, record_owner_label, record_kind, record_state, arguments,
expect, purpose: evidence|diagnostic, argument_sources}`. Stored on the run so the page can show "what we tested and why".

## 1.3 The automatic test run

New method `Building.auto_test(aid, publish_when_ready=False)` exposed as:
- API `POST /api/v1/artifacts/{aid}/auto-test` → run id (background, the existing `jobs.py` machinery: add
  `"auto_test"` to `LIVE_ACTIONS` and `ACTION_LABELS`, with progress steps "Creating test accounts", "Creating records",
  "Running tests", "Checking evidence");
- page button **Run automatic tests** on `artifact.html`, above the manual test form (keep the manual form).

Steps:
1. Preconditions: the artifact is buildable and has no execution blockers, the connector is sandbox, and fixtures are
   configured (else go to §1.4).
2. `kinds()` → `plan()`.
3. Provision: create accounts (labels `auto-<run>-a`, `auto-<run>-b`, …), save them as connector identities through
   the existing `Connectors.save_identity` (scope from the accepted policy's principals; context from the fixture
   answer). Create the records.
4. Run each scenario through the existing `run_sandbox_test` (so verdicts, failure reports and evidence are exactly the
   manual ones). Name tests `auto:<scenario>:<n>` so re-runs supersede earlier ones by name (latest run counts).
5. On failure, the existing `repairs.diagnose`:
   - `auto` (test setup: wrong identity/scope, wrong record state) → adjust and re-run, at most 2 extra attempts per scenario;
   - `repair` → **do not** call the model repair automatically; record "needs your review: the tool's wiring may be
     wrong" with the failure report and a button for the existing **Fix and retest**;
   - `owner` → record the owner question.
6. Run the generated policy guard tests (`POST /artifacts/{aid}/policy-tests` path).
7. Evaluate `publication_problems(aid)`. Empty → artifact readiness `ready_to_publish`.
8. `publish_when_ready` (default **off**, owner setting per business): publish **sandbox only**. Never Live Demo,
   never production.
9. Cleanup: `reset(run_id)` for records the tests don't need as evidence. Keep the accounts that evidence references:
   delete them only when the artifact is superseded (identities named by passing evidence must stay valid, or
   `check_publication` would block the tool).

Record everything on the run (`runs` table, kind `auto_test`): the plan, each attempt, the diagnosis, and what still
needs the owner.

Add `readiness_state` to the artifact view: `not_tested | testing | needs_owner | ready_to_publish | published`,
computed from tests, evidence and publications (not stored, so it can't drift).

## 1.4 Fallback without a fixture provider

- Use existing connector identities. If fewer than the plan needs, `needs_owner` with an exact request ("add one more
  test account of type end_user that owns a delivered order").
- Records: if the spec has a read/list operation the identity may call (e.g. "list my orders"), run it as each
  identity through the executor and pick candidates from the response by the record kind's id field. Try a candidate;
  a 409/422 means wrong state → classify as `test_input` and try the next one. Log every attempt.
- Never call signup, admin or restricted operations to create data (they stay restricted, see `docs/OPENAPI_SUBSET.md`).

## 1.5 the agent and the dashboard

- `services/agent/src/agent_service/adapters/tools_admin_client.py`: `auto_test(artifact_id)`, `run(run_id)`.
- `services/agent/src/agent_service/api/business_tools.py`: per the tools service proposal, show `readiness_state` and the latest auto-test run
  summary. Add `POST /v1/dashboard/business-tools/artifacts/{id}/auto-test` (manager; the artifact must belong to the
  tenant's business, else 404; add the route to `ROLES` in `api/auth.py`).
- `dashboard/src/pages/BusinessTools.tsx`: under Tool proposals, a **Run automatic tests** button and the state badge
  (Not tested / Testing… / Needs you: <question> / Ready to publish / Published), polling the run until it finishes.
  en + ar texts.
- `ops/local/tools_lifecycle.py`: replace hard-coded `IDENTITIES`, `_UNSHIPPED` and the per-tool test lists with
  one call to auto-test; keep the old path behind `--manual-tests` for comparison.

## 1.6 Tests for Part 1

- `services/tools/tests/test_autotest.py` (in-process service-desk fixture + a mock fixture provider):
  - plan derivation for each row of §1.2 (pure function);
  - a record-scope tool reaches `ready_to_publish` with zero identities configured beforehand;
  - a tool whose state precondition needs `pending` gets a `pending` record;
  - a wiring defect stops at `needs_owner` and the model repair is **not** called;
  - `publish_when_ready` publishes sandbox only; a Live Demo connector refuses fixtures;
  - the fixture key never appears in a run, page, report or log;
  - a re-run supersedes by test name and doesn't duplicate evidence.
- Nile sandbox: `/_fixtures/*` contract tests.
- The agent API test for the proxy (scoping, role).
- Real check: `ops/local/autotest_check.py`. Fresh business → model proposal → owner approval (scripted, labelled)
  → **one** auto-test call → `ready_to_publish` for each Nile tool (order, ticket, orders, return, exchange, refund,
  cancel, address, voucher) with no hand-written identities or records.

**Done when:** every Nile tool reaches Ready to publish from one button / API call, and the manual test form and
existing evidence rules are unchanged.

---

# Part 2: real production publication

## 2.0 Decisions needed before starting (defaults chosen; confirm or change)

| # | Question | Default in this plan |
|---|---|---|
| D1 | Whose credentials does a production tool use? | **A business service credential** (an API key/token the business issues to TicketEasy), with **The tools service enforcing per-customer scoping** on every call. Per-customer OAuth (each customer links their own account) is a later option, not this plan. |
| D2 | Where do production secrets live? | An env var or OS secret store referenced by name (`secret_ref: "env:NILE_PROD_TOKEN"`). Never in the tools service's SQLite, pages, artifacts, logs or prompts. |
| D3 | Who may promote to production? | A the agent **admin** (dashboard), recorded with name, time and note; sandbox evidence plus a passing production smoke check are required. |
| D4 | Write tools in production? | Allowed only if the API declares an idempotency mechanism **or** the owner explicitly accepts "no idempotency" for that tool; still subject to the knowledge service rules and customer confirmation. |
| D5 | Does Live Demo stay? | Yes, as the step between sandbox and production (real API, demo account). |

## 2.1 Why the current model can't serve real customers

Today the identity is fixed per MCP process (`--identity`), and the agent maps a few demo customers to dummy identities
(`TenantBinding.identities`). Real customers are unbounded and have no the tools service identity. The tools service's README states that
service-account execution on behalf of many users is unsupported. That is the core change, and it must be done safely:
**the service credential may only ever reach the verified customer's own records.**

## 2.2 New execution mode: service credential + trusted customer context

**The tools service**
- `services/tools/tools_service/compiler/policies.py` / `artifacts.py`: new caller-access mechanism `service_credential_scoped`:
  - allowed only when **every** record-scope requirement has a tools service check (`response_field_matches_context`). No
    `unrestricted`, and no `api_filtered` reliance for data scope: data-scope outputs must be filtered by the tools service
    against the context too (new mechanism `output_rows_match_context`: drop rows whose owner field differs, or fail
    the call; owner chooses per tool);
  - every write step that targets a record must come after a checked read of that record (the two-step shape the
    ticket tool already has); otherwise `execution_blockers`.
- Trusted context per call: `tools/call` accepts `_meta.ticketeasy.context = {customer_id: …}` **only** on a
  production server started with `--context-from-client`. Rules:
  - context keys must be the connector's declared `context_fields`;
  - arguments still can't carry identity (`additionalProperties: false` unchanged);
  - the context is never logged in full (only a hash), never returned, and never reaches a model;
  - **trust boundary:** this is safe only because the stdio process is spawned by the agent and nobody else can write to
    it. Document it; any network transport needs mutual authentication first.
- `services/tools/tools_service/executor.py`: `credentials()` for `service_credential_scoped` takes the service secret from
  `secret_ref` and the context from the call (not from a stored identity). The identity context check then compares
  against the call's context.

**The agent**
- `tools_catalog.py`: for a tool whose resolved environment is `production`, don't map to a dummy identity. Use one MCP
  process per business started with `--identity service --context-from-client`, and send `acting_customer_id` as
  `_meta.ticketeasy.context.customer_id`. An unverified customer still sends **nothing** (`IDENTITY_NOT_MAPPED` stays).
- `TenantBinding.identities` stays for sandbox and Live Demo only.

## 2.3 The `production` environment

**The tools service**
- `connectors.py`: `ENVIRONMENTS += ("production",)`. A production connector has:
  - `environment: "production"`, id prefix `prod-`, `base_url`, `secret_ref` (no stored credentials), `context_fields`;
  - hosts from operator settings `PRODUCTION_HOSTS` (exact host:port), the same public-address guard as Live Demo, and
    **no** private-host exception;
  - the kill switch `PRODUCTION_ENABLED` (default off).
- `executor.destination()`: a production branch mirroring Live Demo, plus a check that `secret_ref` resolves (the env
  var is set), else `credential_missing`, nothing sent.
- `artifacts.py`: `PRODUCTION_ACTIVATION = dict(permitted_uses=[…, "production_execution"], runtime_ready=True,
  activated=True)` only for artifacts built on production connectors, and only after promotion (§2.4).
  `connector.environment: "production"` is part of the hash.
- Budget: `PRODUCTION_CALLS_PER_MINUTE` per connector, plus an optional per-customer limit (count executions by
  context hash).

## 2.4 Promotion: sandbox-tested → production (human decision, automated checks)

New table `promotions` (migration in `services/tools/tools_service/persistence/migrations/versions.py`, next version number) and
service `services/tools/tools_service/services/promotion.py`:

`promote(source_artifact_id, production_connector_id, actor, note)`:
1. The source artifact is published (sandbox or Live Demo) with complete evidence (`publication_problems` empty).
2. Build the **production artifact** from the same approved proposal version on the production connector. Assert that
   its content equals the source's except `connector` and `activation` (same steps, schemas, policy and enforcement),
   so the evidence carries over. Any other difference refuses promotion.
3. Production smoke check: the connection check plus one **read-only** operation of the tool (or a declared health
   operation) with the service credential and a designated smoke context (operator-configured, e.g. an internal test
   customer). Writes are never smoke-tested in production.
4. Readiness checklist, all required, each stored with its evidence. This is what makes `production_ready` true, and
   nothing else does:
   - approval current;
   - enforcement reviewed;
   - sandbox evidence complete;
   - service-scoped mechanism satisfied (§2.2);
   - smoke passed;
   - secret resolved;
   - idempotency decision recorded for writes (D4);
   - rate limits set;
   - an owner-named rollback contact.
5. Record the promotion (actor from the agent's signed-in admin, time, note, the checklist, both artifact hashes) and
   create a publication with `environment: "production"`, `production_ready: true`, `production_activation: true`.

Keep `production_assessment` truthful: `passed` with the checklist id, or `not_assessed` for everything else.

Rollback: `POST /publications/{id}/disable` (unchanged), plus `POST /promotions/{id}/rollback`, which disables
production and leaves the sandbox / Live Demo publications as they are. Auto-disable on an error spike: reuse the agent's
`action_failing` alert. When it opens for a production tool, call the tools service's disable through `tools_admin_client` (configurable
per tenant; default on) and notify through the existing `AGENT_ALERT_WEBHOOK`.

## 2.5 Idempotency for production writes

- Discovery (`services/tools/tools_service/discovery.py`): detect an `Idempotency-Key` header parameter (or a documented body field)
  on write operations and record `idempotency: header|field|none`.
- `executor.py`: for production writes with `idempotency != none`, send the agent's `ToolCallRequest.idempotency_key`
  (already sha256(tenant|conversation|proposal)) through a new trusted MCP `_meta.ticketeasy.idempotency_key`, never as
  an argument. With `none`, require D4's explicit owner acceptance, shown on the promotion checklist.
- An uncertain write stays uncertain. If the API has a lookup-by-idempotency-key read, add a **reconciliation** step for
  the inbox: "check whether this went through" runs that read, as a person-triggered action only.

## 2.6 the agent, dashboard and labels

- `tools_catalog.py`: `Environment = Literal["sandbox", "live_demo", "production"]`. Production results are trusted only
  if `environment == "production"`, `production_ready is True` and the publication id matches. The rule against
  silent fallback stays (sandbox/live/prod published together and all allowed = ambiguous).
- `business_tools.py`: environment `production`, the promotion record, the checklist, a **Promote to production**
  action (admin only; add to `ROLES`; scoped to the tenant's business) and **Roll back**.
- Dashboard: a PRODUCTION badge (distinct colour), a promotion dialog showing the checklist and requiring a note, a
  banner "N tools act on real customer data", and the rollback button. en + ar.
- The tools service pages: a production connector form (secret reference, not secret value) and a read-only promotion view;
  promotion itself is done from the agent (admin sign-in exists there; the tools service pages have none).
- Customer chat: unchanged. Customers never see environments.

## 2.7 Security work that production needs (and Live Demo didn't)

- The tools service's management API must stop being open on loopback for production actions: require a service token
  (`TOOLS_ADMIN_TOKEN`, env) on `/api/v1` mutations, and send it from `tools_admin_client.py`. The tools service's own pages: add at
  least a single-operator password, or bind production actions to the agent only.
- Pin DNS for production calls: resolve once, check public, connect to that address (httpx transport with a custom
  resolver), closing the rebinding gap noted in `docs/INTEGRATION.md` §8.
- Audit export: executions with mode `mcp_production` → append-only log file per day (hashes and ids, no values).
- Secrets never cross into the agent: the agent's binding `env` must not carry production secrets. The tools service's process reads
  them from its own environment.

## 2.8 Tests for Part 2

- The tools service `tests/test_production.py` (mock target):
  - a service credential cannot read another customer's record (context check refuses);
  - a call with no context or with undeclared keys is refused before any request;
  - context is ignored on a server started without `--context-from-client`;
  - promotion refuses changed content, missing evidence, a failed smoke check, a missing secret, or a write with no
    idempotency decision;
  - `production_ready` is true only after promotion;
  - disable and rollback block the next call;
  - an error spike auto-disables;
  - the secret never appears anywhere;
  - the idempotency key is sent as a header and never as an argument.
- The agent: production results need `production_ready`; context is sent only for verified customers; admin-only
  promotion; ambiguity rules.
- Dashboard: PRODUCTION badge, promotion dialog, rollback.
- Real check `ops/local/production_check.py` against the Nile shop started as a separate "production" deployment
  with a service token (labelled clearly as a local deployment), then against a real API when the business provides a
  service credential.

**Done when:** a Nile tool goes sandbox → (Live Demo) → promoted to production by an admin; a real verified customer's
chat uses it with the service credential, sees only their own data, and writes are confirmed, idempotent and audited.
Disable and rollback work on the next call.

---

# Order of work (each step ends green: unit suites + the three integration scripts)

1. Fixture contract on the Nile sandbox + `FixtureClient` + connector `fixtures` (1.1).
2. `autotest.plan()` as a pure function with full tests (1.2).
3. `auto_test` run, job progress, readiness state, artifact page button (1.3).
4. Fallback without fixtures (1.4).
5. The agent proxy + dashboard button + lifecycle script switch (1.5); `autotest_check.py` passes for all Nile tools.
6. **Confirm D1–D5 with the product owner before starting Part 2.**
7. Service-credential mechanism + trusted per-call context (2.2), mock-tested.
8. Production environment + kill switch + secrets by reference (2.3).
9. Promotion service + checklist + rollback + auto-disable (2.4).
10. Idempotency (2.5).
11. The agent + dashboard + the tools service pages (2.6).
12. Security hardening (2.7), then `production_check.py`, then docs (`docs/INTEGRATION.md`, `services/tools/README.md`,
    `services/agent/CLAUDE.md`).

## Notes for the implementer

- Read before editing: `services/tools/tools_service/publishing.py`, `services/building.py` (`run_sandbox`, `run_sandbox_test`,
  `fix_and_retest`, `suggest_test_inputs`), `repair.py` (`diagnose`), `services/publication.py`, `connectors.py`,
  `executor.py`, `services/agent/src/agent_service/adapters/tools_catalog.py`, `services/agent/CLAUDE.md` (rules for the agent changes).
- The agent tests must never start the tools service: extend `services/agent/tests/fake_tools_mcp.py` instead.
- The knowledge service's test suite rewrites `services/knowledge/data/telecom/telco_001/schema.sql`. Restore it with
  `git restore -- services/knowledge/data/telecom/telco_001/schema.sql` after running the knowledge service's tests; don't commit it.
- Generated files: after changing the agent dashboard models, run `python scripts/export_schemas.py` in `services/agent/` and
  `npm run types` in `dashboard/`.
- Use Python heredocs carefully on Windows bash: `\n` inside an unquoted Python string in a heredoc becomes a real
  newline. Write edit scripts to files instead.
- Commit per step, small and reviewed. Never push.
