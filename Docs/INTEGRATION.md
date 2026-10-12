# TicketEasy: Teams A, B and C integrated (local, TEST-ONLY tools)

One application: the **TicketEasy assistant** (Team B's chat, inbox and manager dashboard, with Team A's safety and
policy search inside) can use **business tools** that Team C generated, tested in a sandbox and published. Team C's
publications are **TEST-ONLY**: sandbox environment, `production_ready: false`, `activated: false`. Nothing here is a
production integration.

## 1. Who does what

| Team | Folder | Runs as | Responsibility in the integrated flow |
|---|---|---|---|
| A: safety + RAG | `team_a/` | FastAPI, port 8001 | `classify_risk` (safety screen), `search_knowledge` / `get_passage` (cited policy passages), `check_action` (allow / deny / require_human per approved rule) |
| B: brain + handoff | `team_b/` (+ `dashboard/`) | FastAPI, port 8010 | the only customer-facing service: chat, identity check, gate order, confirmation, tool calls, result verification, handoff to the inbox, manager dashboard |
| C: tool generation + MCP | `team_c/` | FastAPI owner pages + REST, port 8101; MCP stdio server per identity | OpenAPI discovery, model-written proposals, owner review and approval, artifact build, sandbox tests, sandbox publication, MCP `tools/list` / `tools/call` with re-checks on every call |
| sandbox target | `integration/nile_sandbox/` | FastAPI, port 8102 | the Nile Style shop API Team C compiles tools against (same customers and orders as Team B's demo shop) |

All four live in one Git repository. `team_c/.git` is a separate local-only repository with no remote; the
integration does not use or change it.

## 2. A customer message, end to end

```
browser /chat ──POST /v1/conversations/{id}/messages──► Team B pipeline
  understand ─► Team A classify_risk (flagged → handoff)
  knowledge question ─► Team A search_knowledge → cited passages → answer
  order / action:
     identity: verify_customer (order id + phone)                       [Team B's stand-in shop]
     read:     get_order ─► TeamCCapabilities ─► MCP tools/call ─► Team C re-checks publication, approval,
               enforcement, evidence, hashes ─► executor (as the verified customer's sandbox identity) ─► sandbox API
     write:    permission gate ─► Team A check_action ─► customer "yes" ─► Team A check_action again
               ─► create_ticket via Team C (once, never retried) ─► verify_result
  handoff (forced or chosen) ─► case in /inbox, unchanged
◄── reply in the customer's language, trace stored (tool audit id "team-c:<execution id>")
```

Team C is reached only through Team B's existing `CapabilityClient` port, so every gate of Team B (identity, ownership,
permission gate, risk screen, Team A's rule check, confirmation, single execution, result verification, handoff) is
unchanged. Unbound tools keep using Team B's stand-in shop.

## 3. Contracts at each boundary

| Boundary | Protocol | Contract |
|---|---|---|
| Browser → Team B | HTTP JSON | unchanged: `POST /v1/conversations/{id}/messages` → `AgentReply`; staff pages need sign-in (cookie + CSRF), roles in `team_b/src/team_b/api/auth.py` |
| Team B → Team A | HTTP JSON | unchanged: `team_b/src/team_b/adapters/team_a_http.py` (`TEAM_B_MODE=live`) |
| Team B → Team C tools | MCP over stdio (JSON-RPC, protocol 2025-06-18) | `team_b/src/team_b/adapters/team_c_mcp.py` (client), `team_c_tools.py` (binding, identity, result mapping) |
| Team B → Team C management | HTTP JSON, loopback | `GET /api/v1/publications?business_id=`, `GET /api/v1/publications/{id}`, `POST /api/v1/publications/{id}/disable {note}`, `GET /api/v1/businesses/{id}/proposals` (`team_c_admin.py`) |
| Team C → sandbox API | HTTP JSON, bearer token per identity | the artifact's steps; host must be a confirmed sandbox destination / `SANDBOX_HOSTS` |
| Owner → Team C | Team C's own pages | request, answer, approve, build, test, publish: `http://127.0.0.1:8101/businesses/<id>` (linked from the dashboard) |

### Binding a Team B tool to a Team C tool

`TEAM_B_TEAM_C_CONFIG` names a JSON file (example: `team_b/config/team_c.example.json`; the lifecycle script writes
`integration/var/team_c.json`). Per business: the Team C `business_id`, `identities` (verified Team B customer id →
Team C sandbox identity) and `tools` (Team B tool name → Team C `proposal_id`, optional `arguments` / `fields` renames,
`reference_field` for creates). Rules the adapter enforces:

- a bound tool is served **only** by Team C; if Team C does not list it (not published, disabled, blocked by Team C's
  re-checks, Team C down) the agent sees it as missing and says so or hands over: it never falls back to the stand-in;
- the newest published version of the proposal is used; two published artifacts of the same version are ambiguous and
  nothing is used; a tool whose read/write nature differs from the business's declaration is not used; a publication
  that is not `environment: sandbox` with `production_ready: false` is not used;
- the ToolSpec the brain sees is the business's own declaration, so intents, gates and checks do not change.

### Identity

Team C's MCP server is bound to one business and one sandbox identity per process (that is Team C's contract). Team B
starts one process per (business, identity) and chooses the identity from `ToolCallRequest.acting_customer_id`, the
customer **Team B's code verified** (order number + phone). Tool arguments, the model and the customer can never choose
it; Team C rejects unknown arguments anyway (`additionalProperties: false`). A verified customer without a mapping is
not sent (`IDENTITY_NOT_MAPPED`); the agent answers "I could not look that up" and no Team C execution happens.

### Results (Team C `structuredContent.status` → Team B `ToolResult`)

| Team C | Team B | What the customer hears |
|---|---|---|
| `succeeded` | success, `audit_id = team-c:<execution_id>`, outputs mapped | the answer / "Done, reference TCK-…" (after `verify_result`) |
| `rejected` (nothing sent) | error `TEAM_C_REJECTED` | read: honest "could not look that up"; write: "could not do it" |
| `failed`, no write attempted | error `TEAM_C_FAILED` | same |
| `failed` with `rejected_unverified` write | error, `write_may_have_applied` | handoff `unverified_result` (Team C does not assert no change) |
| `partial` / `outcome_unknown` | error `PARTIAL_WRITE` / `OUTCOME_UNKNOWN`, `write_may_have_applied` | handoff `unverified_result`: "I could not confirm that this went through, so I will not say it is done" |
| `error` | error `TEAM_C_ERROR`, `write_may_have_applied` for a write tool | write: handoff `unverified_result` |
| JSON-RPC `-32602`, `data.code = tool_not_published` | error `TOOL_NOT_PUBLISHED`; the cached tool list is dropped | capability missing → handoff |
| request sent, no answer (timeout / process exit) | `UpstreamError TIMEOUT / CONNECTION_LOST` (never `BACKEND_UNAVAILABLE`) | write: `unverified_result`; read: one retry, then honest failure |
| process cannot start | `UpstreamError BACKEND_UNAVAILABLE` (nothing sent) | honest failure / handoff |

Writes are never retried by Team B (`brain/actions.py`, unchanged). No reconciliation call exists for an uncertain
write: a person checks it from the handoff case.

### Discovery and refresh

Team C sends no `list_changed`. Team B re-lists on its existing registry TTL (`TEAM_B_CAPABILITY_TTL_S`, 60 s), on
**Refresh tool list** in the dashboard, after a disable, and immediately after any `tool_not_published` answer. A cached
definition never bypasses Team C: every `tools/call` is re-checked there (publication, approval, enforcement, evidence,
artifact hash).

## 4. The interface

- **Customer chat** `/chat?tenant_id=shop_001` and **support inbox** `/inbox`: unchanged.
- **Manager dashboard** `/dashboard`: a new **Business tools** page (`/dashboard/tools`, English and Arabic):
  which tools the agent uses and who serves each (Team C sandbox or the stand-in), its status and the Team C tool name
  and version; the Team C connection (business, last listing, problems); Team C's sandbox publications with
  environment and "not production"; **Disable** (requires a note; blocks the next call; it cannot undo a write);
  **Refresh tool list**; the tool proposals in Team C with their state; a link to **Open the tool builder (Team C)**
  for the whole owner workflow (request, answer, approve, build, test, publish), which stays in Team C's own pages.
- Authorization is enforced by Team B's backend: reading the page and refresh/disable need the **manager** role, POSTs
  need the CSRF token, and a publication of another Team C business is answered 404 and never disabled.

## 5. Run it locally

Prerequisites: Python 3.13, Node 20+, `uv` (Team C), a model for Team C (its `team_c/.env`: Groq / OpenRouter /
Ollama). Ollama with `bge-m3` is optional for Team A (without it Team A searches by keywords only).

```powershell
# once: virtualenvs (git-ignored)
python -m venv team_a/.venv; team_a/.venv/Scripts/python -m pip install -r team_a/requirements.txt
python -m venv team_b/.venv; team_b/.venv/Scripts/python -m pip install -e team_b -r team_b/requirements-dev.txt
cd team_c; uv sync --locked; cd ..
cd team_a; $env:PYTHONPATH="src"; .venv/Scripts/python -m team_a ingest --tenant shop_001 --no-embeddings; cd ..
cd dashboard; npm ci; npm run build; cd ..          # builds into team_b/web/dashboard
```

Start in this order (each in its own terminal, from the repository root):

```powershell
python integration/serve.py sandbox   # http://127.0.0.1:8102/health
python integration/serve.py team-c    # http://127.0.0.1:8101/health   (database team_c/data/integration.sqlite3)
python integration/serve.py team-a    # http://127.0.0.1:8001/health
team_b/.venv/Scripts/python integration/team_c_lifecycle.py --tool order    # once: real lifecycle, writes integration/var/team_c.json
team_b/.venv/Scripts/python integration/team_c_lifecycle.py --tool ticket   # once: the write tool
python integration/serve.py team-b    # http://127.0.0.1:8010/health -> {"mode": "live"}; restart it after a new binding
```

`integration/serve.py` gives each service its team's virtualenv, working directory, port and an isolated database, and
generates local keys once into `integration/var/` (git-ignored). It never touches another Team C instance (for example
one on port 8000). Sign-in for the inbox and dashboard:
`cd team_b; $env:TEAM_B_STORE="sqlite"; $env:TEAM_B_DB_PATH="../integration/var/team_b.sqlite3"; .venv/Scripts/python -m team_b create-user --email you@example.com --name You --role manager --tenants shop_001 --generate-password`.

Then: chat `http://127.0.0.1:8010/chat?tenant_id=shop_001` ("Where is my order NS-20877?", phone `01012345601`;
"I have a complaint about order NS-20877, the parcel arrived late"), dashboard `http://127.0.0.1:8010/dashboard/tools`.
Demo customers with a sandbox identity: C-100 (alice, phone 01012345601), C-101 (bob, 01123456702).

## 6. The tool lifecycle (what the script does, and what an owner does in Team C's pages)

1. Business + OpenAPI: upload the sandbox's `/openapi.json` (3.1; both operations eligible, bearer auth detected).
2. Request a tool (goal text) → the model triages and writes a proposal (`needs_clarification`).
3. Answer questions and access requirements; accept the structured access policy (ownership: response `customer_id`
   equals the identity's trusted `customer_id`).
4. Reconcile (model) until `ready_for_review`; confirm requirements; **approve to build** (owner decision, labelled TEST-ONLY).
5. Save the sandbox connector (`http://127.0.0.1:8102`, context field `customer_id`); build the artifact.
6. Add sandbox identities (alice = C-100, bob = C-101; tokens go only into Team C's database); run policy tests and
   sandbox tests: two own-record successes and one cross-customer test that must be refused before any write.
7. Publish to the sandbox MCP (`environment: sandbox`, not production) and write Team B's binding.
8. Team B discovers it on its next listing (or **Refresh tool list**); disable it from the dashboard or Team C's page.

## 7. Tests

| What | Command | Result on 2026-10-12 |
|---|---|---|
| Team A | `cd team_a; $env:PYTHONPATH="src"; .venv/Scripts/python -m pytest -q` | 973 passed (before and after) |
| Team B (incl. 30 new: adapter + API + auth) | `cd team_b; .venv/Scripts/python -m pytest -q; make lint` | 3825 passed (3795 before); ruff, format, mypy clean |
| Team C | `cd team_c; .venv/Scripts/python -m pytest -q` | 616 passed (no Team C code changed) |
| Dashboard | `cd dashboard; npm test; npm run build` | 48 passed; build ok |
| Real end to end | `team_b/.venv/Scripts/python integration/e2e_chat.py` (services + lifecycle first) | 33 passed, 0 failed |

`integration/e2e_chat.py` uses only real services: Team A's policy answer and safety screen; the order read served by
Team C as alice and as bob (Team C's own execution records checked); an unmapped customer gets no data and nothing is
sent; extra argument rejected; unknown tool `-32602 tool_not_published`; disable through Team B blocks the next call
(also for a client holding the old tool name); republish is found after refresh; a confirmed complaint opens exactly
one ticket in the sandbox; and with the sandbox storing the ticket and then answering 500, Team C reports
`outcome_unknown`, Team B calls once, does not retry, does not claim success and hands over as `unverified_result`.

Team B's unit tests use a scripted fake MCP server (`team_b/tests/fake_team_c_mcp.py`) because Team B's tests must
never start Team C. Verified by hand in the browser: the chat answering from Team C, the Business tools page (data,
refresh, Arabic right-to-left). The dashboard's disable flow is covered by the component test and the real E2E (API).

## 8. Limits (not production)

- **TEST-ONLY.** Team C publishes to a sandbox only; there is no production activation, and none was added.
- **Identity is a local mechanism.** Each sandbox identity is a stored test credential mapped from a verified demo
  customer by an operator file; Team C's MCP server trusts its command-line identity. Real multi-user production needs
  per-customer delegated credentials issued by the business (for example OAuth on behalf of the customer), a trusted
  caller identity between Team B and Team C, and secret storage that is not a local SQLite file.
- **Transport is stdio on one machine.** Team B and Team C must share a filesystem (Team C's database and liveness
  locks). The Docker demo (`make demo*`) does not include Team C; a remote MCP transport would need authentication,
  tenant scoping and identity propagation that do not exist yet.
- **One process per identity.** Fine for a handful of test identities, not for many customers.
- **Stale lists.** Up to `TEAM_B_CAPABILITY_TTL_S` (60 s) before Team B notices a new publication without a refresh;
  disabled or changed tools are refused by Team C on the very next call regardless.
- **Uncertain writes** go to a person; there is no automatic reconciliation (the sandbox has none to offer).
- **Rate limits / model failures** affect Team C's proposal generation (the model is only used there, never at
  call time); a run that fails is recorded by Team C and can be repeated.
- Team C's owner pages have no sign-in (Team C's documented loopback-only development app); only Team B's dashboard
  actions are role-checked.
