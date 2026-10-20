# TicketEasy: Teams A, B and C integrated (Sandbox and Live Demo tools)

One application. The **TicketEasy assistant** (Team B's chat, inbox and manager dashboard, with Team A's safety screen
and policy search inside) can use **business tools** that Team C generated from an OpenAPI description, that the owner
approved and tested, and that Team C published in one of two environments:

| Environment | Calls go to | Credentials | Label in the UI |
|---|---|---|---|
| **Sandbox** | a test API (`SANDBOX_HOSTS` or an owner-confirmed sandbox URL) | test identities | SANDBOX · TEST-ONLY |
| **Live Demo** | the owner-configured **real** API (operator allowlist + owner confirmation) | the demo account's real credentials, kept apart from sandbox ones | LIVE DEMO · calls the real API at `host` |

Neither is a production release: `production_ready` is always false (`production_assessment: "not_assessed"`) and
production activation does not exist. A working Live Demo shows the tools work against a real API; it does not make
the platform production-ready (see "Limits").

## 1. Who does what

| Team | Folder | Runs as | Responsibility |
|---|---|---|---|
| A: safety + RAG | `services/knowledge/` | FastAPI, port 8001 | `classify_risk`, `search_knowledge` / `get_passage` (cited policy passages), `check_action` (allow / deny / require_human) |
| B: brain + handoff | `services/agent/` (+ `apps/dashboard/`) | FastAPI, port 8010 | the only customer-facing service: chat, identity check, gate order, confirmation, tool calls, result verification, handoff, manager dashboard |
| C: tool generation + MCP | `services/tools/` | FastAPI owner pages + REST, port 8101; an MCP stdio server per identity, started by Team B | OpenAPI discovery, model-written proposals, owner review and approval, artifact build, sandbox / live tests, publication, MCP `tools/list` / `tools/call` re-checked on every call |
| local APIs | `ops/local/nile_sandbox/` | sandbox 8102, demo-shop 8103 | the Nile Style shop API (same customers and orders as Team B's demo shop); demo-shop is a second, separate deployment with its own credentials |

All in one Git repository. `services/tools/.git` is a separate local-only repository with no remote; it is not used.

## 2. A customer message, end to end

```
browser /chat ──POST /v1/conversations/{id}/messages──► Team B pipeline
  understand ─► Team A classify_risk (flagged → handoff)
  knowledge question ─► Team A search_knowledge → cited passages → answer
  order / action:
     identity: verify_customer (order id + phone)                              [Team B's stand-in shop]
     read:     get_order ─► TeamCCapabilities ─► MCP tools/call ─► Team C re-checks publication, approval, enforcement,
               evidence, artifact hash, environment (Live Demo: switched on, host allowed, public address, call budget)
               ─► executor, as the verified customer's own identity ─► the sandbox or the real API
     write:    permission gate ─► Team A check_action ─► customer "yes" ─► check_action again ─► create_ticket via
               Team C (once, never retried) ─► verify_result
  handoff (forced or chosen) ─► case in /inbox, unchanged
◄── reply in the customer's language; trace stored (tool audit id "team-c:<execution id>")
```

Team C is reached only through Team B's `CapabilityClient` port, so every gate of Team B is unchanged. Unbound tools
keep using Team B's stand-in shop.

## 3. Contracts

| Boundary | Protocol | Contract |
|---|---|---|
| Browser → Team B | HTTP JSON | `POST /v1/conversations/{id}/messages` → `AgentReply`; staff pages need sign-in (cookie + CSRF), roles in `services/agent/src/team_b/api/auth.py` |
| Team B → Team A | HTTP JSON | `services/agent/src/team_b/adapters/team_a_http.py` (`TEAM_B_MODE=live`) |
| Team B → Team C tools | MCP over stdio (JSON-RPC, protocol 2025-06-18) | `services/agent/src/team_b/adapters/team_c_mcp.py` (client), `team_c_tools.py` (binding, identity, environment, result mapping) |
| Team B → Team C management | HTTP JSON, loopback | publications, proposals, connectors, disable, connection check (`team_c_admin.py`) |
| Team C → API | HTTPS/HTTP JSON, bearer or OAuth2 password per identity | the artifact's steps; no redirects; size and time limits; destination re-checked before each run |
| Owner → Team C | Team C's own pages / `/api/v1` | request, answer, approve, connector (environment), build, identities, connection check, tests, publish, disable |

### Binding a Team B tool to a Team C tool

`TEAM_B_TEAM_C_CONFIG` names a JSON file (example `services/agent/config/team_c.example.json`; the scripts write
`ops/local/var/team_c.json`). Per business: the Team C `business_id`, `identities` (verified Team B customer id →
Team C identity name) and `tools` (Team B tool name → Team C `proposal_id`, optional `arguments` / `fields` renames,
`reference_field` for creates, `environments`). The adapter enforces:

- a bound tool is served **only** by Team C; if Team C does not list it, the agent says so or hands over (never the stand-in);
- `environments` defaults to `["sandbox"]`; a tool uses Live Demo only when the operator lists `"live_demo"` there. If
  publications exist in two allowed environments at once the tool is ambiguous and not used: there is **no silent
  fallback** from live to sandbox data (or back);
- a result from another environment than the tool was listed in, or one that claims production, is not trusted;
- the identity of a call comes from the customer **Team B verified** (`acting_customer_id`), never from arguments.

### Results (Team C `status` → Team B)

| Team C | Team B `ToolResult` | Customer hears |
|---|---|---|
| `succeeded` | success, `audit_id = team-c:<execution_id>` | the answer / "Done, reference …" (after `verify_result`) |
| `rejected` (nothing sent) | `TEAM_C_REJECTED` | honest "could not look that up" / "could not do it" |
| `failed`, no write attempted | `TEAM_C_FAILED` (a read with category `not_found` → `NOT_FOUND`: "please check the number") | same |
| `failed` with a `rejected_unverified` write | error, `write_may_have_applied` | handoff `unverified_result` |
| `partial` / `outcome_unknown` | `PARTIAL_WRITE` / `OUTCOME_UNKNOWN`, `write_may_have_applied` | handoff: "I could not confirm that this went through" |
| `error` | `TEAM_C_ERROR` (uncertain for a write) | write: handoff `unverified_result` |
| `-32602` / `tool_not_published` | `TOOL_NOT_PUBLISHED`; cached list dropped | capability missing → handoff |
| sent, no answer | `TIMEOUT` / `CONNECTION_LOST` (never "not sent") | write: `unverified_result`; read: honest failure |

Every error message starts with Team C's `error.category` (`invalid_arguments`, `authentication_failed`,
`permission_denied`, `not_found`, `conflict`, `validation_error`, `rate_limited` + `retry_after`, `api_unavailable`,
`api_unreachable`, `timeout`, `invalid_response`, `partial_write`, `unknown_outcome`, `publication_unavailable`,
`configuration_error`, `internal_error`), kept in the trace for operators; customers never see it. Writes are never
retried; an uncertain write goes to a person (no reconciliation endpoint exists in these APIs).

### Discovery and refresh

Team C sends no `list_changed`. Team B re-lists every `TEAM_B_CAPABILITY_TTL_S` (60 s), on **Refresh tool list**,
after a disable and after any `tool_not_published`. A cached definition never bypasses Team C: every call is re-checked.

## 4. Live Demo: configure a real API

Everything below is configuration; no source file is edited.

1. **Operator** (once per machine): copy `ops/local/live_demo.example.env` to `ops/local/live_demo.env`
   (git-ignored) and set `LIVE_DEMO_ENABLED=true`, `LIVE_DEMO_HOSTS=<host:port of the real API>`. Keep
   `LIVE_DEMO_PRIVATE_HOSTS` empty unless the API is on a private development network. Restart Team C and Team B
   (`ops/local/serve.py` passes these settings to Team C's web app and to the MCP server Team B starts).
2. **Owner**, in Team C (`http://127.0.0.1:8101`): create the business, upload the API's OpenAPI file (only the
   operations you want; the server URL in it is only a suggestion), request the tool, answer the questions, accept the
   access policy, approve.
3. Under **Set up the API connector** choose **Live Demo**, enter the real base URL and confirm it is the real API you
   are authorized to call with a dedicated demo account. Build.
4. On the artifact page add the demo account's identity (bearer token, or username + password for an OAuth2 password
   login). It is stored in Team C's local database only.
5. **Check connection**: configuration, reachability, authentication, proven permissions, executable tools, each
   shown separately. Then **Run live test** (a real call, judged by Team C), then **Publish as Live Demo**.
6. In Team B's binding set `"environments": ["live_demo"]` for that tool (the scripts do this), restart Team B or press
   **Refresh tool list** on the dashboard's Business tools page.

Or let a script do 2-5 against your API: fill `LIVE_DEMO_BASE_URL`, `LIVE_DEMO_SPEC`, `LIVE_DEMO_GOAL`,
`LIVE_DEMO_TEST_ARGUMENTS` and the demo account's `LIVE_DEMO_TOKEN` (or `LIVE_DEMO_USERNAME` / `LIVE_DEMO_PASSWORD`) in
`ops/local/live_demo.env`, then `services/agent/.venv/Scripts/python ops/local/live_demo_check.py --api custom`. For a tool
that reads the caller's own records it stops once and asks you to accept the access policy in Team C's page (which
response field proves ownership is your decision).

Security controls on the live path: operator allowlist of exact `host:port`; every resolved address must be public
(blocks loopback, private, link-local and metadata addresses) unless explicitly excepted; owner confirmation; connector
scoped to its business; per-connector call budget (`LIVE_DEMO_CALLS_PER_MINUTE`); strict input schemas
(`additionalProperties: false`); no redirects; request/response size and time limits; credentials never in tool
definitions, prompts, logs or reports; the identity fixed by the server, never by arguments; disabling or switching
Live Demo off blocks the next call.

### Production (local deployment)

A tested tool is **promoted** to production by a signed-in Team B **admin** (Business tools page -> Promote to production: a note, a rollback contact, and for writes the explicit acceptance that the API has no idempotency). Team C builds the production twin, runs its whole checklist (evidence, customer scoping, identical content, secret, smoke check, idempotency, rollback contact) and refuses unless all pass. Production tools use ONE business service credential for every customer; Team B sends the VERIFIED customer's id as trusted context (`_meta.ticketeasy.context.customer_id`, never an argument), and Team C refuses any record that is not that customer's. See `services/tools/README.md`, "Production".

Local setup: copy `ops/local/production.example.env` to `ops/local/production.env` (git-ignored) and run `ops/local/serve.py up`. That also starts `nile-prod` (port 8104), a SEPARATE Nile shop deployment whose service token may act for any customer (so only Team C keeps customers apart), generates `ops/local/var/prod_service.key` (the credential; Team C reads it through a `file:` reference, because the MCP process Team B starts has a deliberately small environment) and `team_c_admin.key` (the admin token; Team B and the scripts send it, Team C's REST changes then require it). `ops/local/production_check.py --phase promote|bind|chat` runs the real thing: connector, promotion, the real production MCP process (own order succeeds, another customer's order is refused although the API would have allowed it, no/undeclared context, smuggled arguments), the customer chat, a confirmed write that lands as exactly one ticket, an admin rollback from the dashboard and the agent no longer using the tool. It is a LOCAL deployment with synthetic data, not a real production API.

### Automatic tool tests

`ops/local/team_c_lifecycle.py` no longer hard-codes test identities and order ids: after the build it declares the Nile sandbox's fixture endpoints on the connector (`NILE_SANDBOX_FIXTURE_KEY`, set for Team C by `ops/local/serve.py`), saves the demo customers' identities (needed by Team B's chat), and makes one `POST /artifacts/{id}/auto-test` call. `--manual-tests` keeps the old hand-written tests. `ops/local/autotest_check.py` runs this for every Nile tool and checks that each reaches ready_to_publish with only `auto.*` evidence tests. The dashboard's Business tools page shows each built tool's state (Not tested / Testing… / Needs you / Ready to publish / Published) and a **Run automatic tests** button (manager; `POST /v1/dashboard/business-tools/artifacts/{id}/auto-test`, which only tests: it never approves or publishes).

## 5. The interface

- **Customer chat** `/chat?tenant_id=shop_001` and **support inbox** `/inbox`: unchanged.
- **Business tools** (`/dashboard/tools`, manager): a LIVE DEMO banner when any tool calls a real API (else the
  TEST-ONLY one); per tool who serves it, its environment badge and the real API host; Team C publications with
  SANDBOX / LIVE DEMO and "not production"; **Disable**; **Refresh tool list**; **API connections** with each
  connector's environment, host, identities and the steps of its last check, and **Check connection**; tool proposals;
  **Open the tool builder (Team C)**.
- **Team C pages**: the connector form asks where the tool runs (Sandbox / Live Demo, with the allowed hosts); the
  artifact page shows the environment, a Connection panel with **Check connection**, **Run live test** with a
  warning that it changes real data for write tools, and **Publish as Live Demo**; publications carry environment badges.
- Authorization is enforced by Team B's backend (manager role, CSRF) and connectors/publications of another business
  answer 404.

## 6. Run it locally

Prerequisites: Python 3.13, Node 20+, `uv` (Team C), a model provider for Team C proposals (`services/tools/.env`, Groq /
OpenRouter / Ollama). Ollama with `bge-m3` is optional for Team A (keyword search without it).

```powershell
# once: virtualenvs (git-ignored), Team A's index, the dashboard
python -m venv services/knowledge/.venv; services/knowledge/.venv/Scripts/python -m pip install -r services/knowledge/requirements.txt
python -m venv services/agent/.venv; services/agent/.venv/Scripts/python -m pip install -e services/agent -r services/agent/requirements-dev.txt
cd services/tools; uv sync --locked; cd ..
cd services/knowledge; $env:PYTHONPATH="src"; .venv/Scripts/python -m team_a ingest --tenant shop_001 --no-embeddings; cd ..
cd apps/dashboard; npm ci; npm run build; cd ..
copy ops/local/live_demo.example.env ops/local/live_demo.env   # optional: Live Demo
```

Start everything (checks the setup first, starts in order, waits for health, reuses services that already run, refuses
a port held by another program, logs to `ops/local/var/logs/`):

```powershell
python ops/local/serve.py up
python ops/local/serve.py status     # in another terminal: what runs, Live Demo settings, setup warnings
```

Create tools (once; both resume where they stopped):

```powershell
services/agent/.venv/Scripts/python ops/local/team_c_lifecycle.py --tool order     # sandbox: model proposal → … → publish
services/agent/.venv/Scripts/python ops/local/team_c_lifecycle.py --tool ticket
services/agent/.venv/Scripts/python ops/local/team_c_lifecycle.py --tool order --environment live_demo   # Live Demo on demo-shop
services/agent/.venv/Scripts/python ops/local/team_c_lifecycle.py --tool ticket --environment live_demo
services/agent/.venv/Scripts/python ops/local/live_demo_check.py                   # Live Demo on GitHub's real public API
```

Restart Team B after a binding changes (`ops/local/var/team_c.json`). Sign-in for the inbox and dashboard:
`cd services/agent; $env:TEAM_B_STORE="sqlite"; $env:TEAM_B_DB_PATH="../../ops/local/var/team_b.sqlite3"; .venv/Scripts/python -m team_b create-user --email you@example.com --name You --role manager --tenants shop_001 --generate-password`.
Demo customers: C-100 (alice, phone 01012345601), C-101 (bob, 01123456702).

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `serve.py up`: `ERROR … port N is used by another program` | another process holds the port (`status` says "another program"); stop it |
| Business tools: tool `missing`, reason `environment_not_allowed` | the publication is Live Demo but the binding allows only sandbox (or the reverse): set `environments` |
| reason `ambiguous` | the same tool is published in two allowed environments (or two artifacts): disable one or narrow `environments` |
| publication `blocked`, `live_demo_disabled` | `LIVE_DEMO_ENABLED` is off for Team C (or the MCP server): fix `ops/local/live_demo.env`, restart Team C and Team B |
| connector check `configuration failed: destination_not_allowed` | the host:port is not in `LIVE_DEMO_HOSTS` |
| `destination_unsafe` | the host resolves to a private/loopback/metadata address; use a public API (or, for a dev network, `LIVE_DEMO_PRIVATE_HOSTS`) |
| check `authentication failed` / calls `authentication_failed` | wrong or revoked credential: replace the identity on the artifact page |
| calls `rate_limited` | the API's own limit (see `retry_after`) or `LIVE_DEMO_CALLS_PER_MINUTE` |
| customer told "I could not confirm that this went through" | an uncertain write (`outcome_unknown`/`partial`): a person checks the API, nothing is retried |

## 7. Tests (2026-10-12)

Automated suites (mocked or in-process targets where noted):

| Suite | Command | Result |
|---|---|---|
| Team A | `cd services/knowledge; $env:PYTHONPATH="src"; .venv/Scripts/python -m pytest -q` | 973 passed |
| Team B (adapter, API, auth, live opt-in, categories) | `cd services/agent; .venv/Scripts/python -m pytest -q; make lint` | 3833 passed; ruff, format, mypy clean |
| Team C (incl. `tests/test_live_demo.py`, mocked target) | `cd services/tools; .venv/Scripts/python -m pytest -q` | 634 passed |
| Dashboard | `cd apps/dashboard; npm test; npm run build` | 49 passed; build ok |

Real runs with all services up:

| Script | Target | Result |
|---|---|---|
| `ops/local/live_demo_check.py` | **GitHub's real public REST API** (`api.github.com`, unauthenticated public read) | 12/12: model proposal → owner approval → Live Demo connector → real connection check → live test → publish → MCP discovery and a real call matching an independent read → invalid argument rejected → a real 404 = `failed`/`not_found` → disable refuses the next call |
| `ops/local/e2e_live_chat.py` | the separate local **demo-shop** deployment as Live Demo target (synthetic data, allowed through `LIVE_DEMO_PRIVATE_HOSTS`; **not an external API**) | 22/22: chat read and confirmed write through Live Demo, verified at the API, nothing sent to the sandbox, uncertain live write not retried, connection check, disable, revoked credential fails honestly |
| `ops/local/e2e_chat.py` | sandbox | 33/33: the original Team A + Team B + Team C sandbox flow, unchanged |

Not verified with a real external API: **authenticated** calls (bearer / OAuth2) and **writes** against an external
system, because no external credentials were available. The code path is the one the demo-shop and GitHub runs use;
to verify it, fill `ops/local/live_demo.env` and run `live_demo_check.py --api custom`.

## 8. Limits (not production)

- **Live Demo ≠ production.** No production release process, readiness assessment or activation exists.
- **Identity is a local mechanism.** Identities are stored credentials mapped from verified demo customers by an
  operator file; Team C's MCP server trusts its command-line identity. Production needs per-customer delegated
  credentials issued by the business (e.g. OAuth on behalf of the customer), a trusted caller identity between Team B
  and Team C, and a real secret store (credentials sit in Team C's local SQLite file).
- **One process per identity, stdio, one machine.** Team B and Team C share a filesystem; the Docker demos do not
  include Team C; a remote MCP transport would need authentication and identity propagation that do not exist.
- **Address guard timing.** Public-address resolution is checked before each run; a DNS answer that changes between
  the check and the connection (rebinding) is not pinned.
- **Stale lists.** Up to 60 s before Team B notices a new publication without a refresh; disabled or changed tools are
  refused by Team C on the very next call regardless.
- **Uncertain writes** go to a person; there is no automatic reconciliation.
- Team C's owner pages have no sign-in (its documented loopback-only design); only Team B's dashboard actions are
  role-checked.
