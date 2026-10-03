  # Ticket-Easy — Team C

Local business-tool discovery, AI proposal generation, clarification reconciliation, and owner approval **to build**. Approved artifacts run only against sandbox connectors and can be published only to a local TEST-ONLY sandbox MCP server; nothing is active in production.

## Quick start (Windows PowerShell)

Prerequisites: Python 3.13, `uv`, and Ollama running with `qwen3:8b` installed. The installed model is used as-is; the application never downloads a model. Run from this component directory:

```powershell
Set-Location 'D:\nti project\team_c'
uv sync --locked
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

Put the generated value in `SESSION_SECRET` in `.env`. Do not overwrite an existing `.env`; retain its credentials/settings. Then:

```powershell
uv run uvicorn team_c.web:create_app --factory --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. API documentation is at <http://127.0.0.1:8000/docs>. Use one server process/worker. Stop with Ctrl+C. Data stays in `data/team_c.sqlite3`; restart with the same command/path.

The default is **Ollama only**, requiring no OpenRouter key. Empty model configuration is an error when generation/reconciliation is requested, not a reason to serve canned proposals. If `.env` is absent, the app uses Ollama defaults and generates an ephemeral session secret; restarting loses browser sessions, not persisted work.

## Discovery input

OpenAPI is the discovery source: upload a JSON/YAML file, or fetch it from a running system (set `OPENAPI_FETCH_HOSTS`, for example `127.0.0.1:8001`). The URL may be the OpenAPI JSON/YAML itself or a Swagger UI / ReDoc page such as FastAPI's `/docs`; the page is followed to the OpenAPI URL it names (same allowlist, at most two GETs). See [the OpenAPI subset and per-operation contract](docs/OPENAPI_SUBSET.md). On the inventory page, select which eligible operations to send to the model.

### Business areas and batches

Large APIs do not fit one model input, so the inventory page groups operations into business areas and the AI works through them in batches.

- **Areas come from each API, not from a fixed list.** Each operation's base group is its first OpenAPI tag. An untagged operation uses its first path segment after the leading segments most paths share (e.g. `/api/v1`), and only when the paths below them split into several groups. **Organize by business area (AI)** runs two small steps with a short dedicated prompt. An `area_naming` run sees only group names and counts and returns up to 8 business areas ranked by usefulness for tools serving the customers, each with an audience hint (customer, staff, internal, mixed). Then `area_assignment` runs place 10 groups at a time; their response schema has one required property per group whose value must be a named area, so each group is assigned exactly once. Areas that receive no group are dropped. With no saved choice, the top-ranked areas that fit about one batch are pre-ticked. Any failure keeps the API's own groups and shows why; only one organize runs at a time (`409 areas_busy`).
- **The owner chooses areas** (**Use these areas**). Only operations in the chosen areas are shown to the model for Request a tool and Suggest additional tools. With no choice saved, every operation is used. Areas and audience are hints: they never change whether an operation is eligible, restricted or unsupported. Reorganizing clears the choice.
- **Batches always fit.** The operation list is built until the complete model input passes the same size checks the provider applies (`MAX_MODEL_CHARS` and the Ollama context bound), shrinking `CAPABILITY_INDEX_CHARS` when needed. An unavailable request offers **Look in the next batch**, and suggestions offer **Suggest from the next batch**; both skip operations already considered. On the inventory page, Advanced generate starts unticked; **Select the next batch that fits** ticks operations not yet sent to the AI (recorded per run in `generation_batches`).
- **Live progress.** Browser AI actions (request, clarify, next batch, suggest, accept a suggestion, organize, generate, reconcile, revise) run in a background thread. If one is still running after 2 seconds, the browser moves to `/live/{id}`, which polls `GET /api/v1/live/{id}` and shows each model step, elapsed time and the streamed reasoning, then opens the result. **Stop** (`POST /live/{id}/cancel`) ends the model call at the next streamed chunk with `409 cancelled`, without trying a fallback provider; the run is recorded as failed. Only one browser AI action runs at a time: pressing another AI button while one runs opens the running job's page instead, and every page shows an "AI is busy" banner linking to it. If a reasoning step reaches the `num_predict` limit, the same input is sent once more without reasoning (diagnostic `thinking_retry`). Progress entries live in memory for up to an hour and are lost on restart; the work itself is recorded as usual.
- API: `GET`/`POST /api/v1/specifications/{id}/areas` (body `area_ids`), `POST /api/v1/specifications/{id}/areas/organize`, `GET /api/v1/specifications/{id}/generation-batch`, `POST /api/v1/tool-requests/{id}/next-batch`, and `next_batch: true` on suggestion runs.

Local Python/FastAPI code discovery is retired from the UI/API (`410 code_discovery_retired`). Existing code inventories stay viewable read-only; they cannot be analyzed or used for new proposals. See [docs/LOCAL_PROJECTS.md](docs/LOCAL_PROJECTS.md) for the legacy record format.

## Provider selection

| Setting | Meaning |
|---|---|
| `LLM_PRIMARY` | `ollama` or `openrouter` |
| `LLM_FALLBACK` | `none` (default), or the other provider |
| `OLLAMA_BASE_URL` | Default `http://127.0.0.1:11434` |
| `OLLAMA_MODEL` | Default `qwen3:8b`; override explicitly to use another model |
| `OLLAMA_TIMEOUT` | Seconds for one whole model answer; default 240. On a laptop GPU that runs part of qwen3:8b on the CPU, suggestions take about 3 minutes, so raise it (for example 1800) |
| `OLLAMA_CONTEXT` | Sent as `options.num_ctx` (tokens); default 32768. Requests are rejected unless an input-token upper bound (UTF-8 bytes of the chat text plus the template wrapper; the `format` schema is a decoding grammar, not prompt text) plus 8192 reserved output tokens (`num_predict`, including any thinking) plus a 512-token margin fits. That bound overestimates about fourfold, so when it fails Team C first asks Ollama for the real count (a one-token `/api/chat` call with the same messages and `num_ctx`; stored as `measured_input_tokens`) and accepts if that count plus the reserve and margin fits. A prompt longer than `num_ctx` is cut by Ollama and reported near `num_ctx`, so it is still rejected. Batch sizing keeps using the byte bound. The bound and Ollama's `prompt_eval_count`/`eval_count` are stored as run diagnostics |
| `OLLAMA_THINK` | Default false: turns Qwen3 reasoning on for every call. Request triage, suggestions, area naming, proposal writing, revision and answer checking (reconciliation) always reason; area assignment and automated repair do not. Reasoning shares the `num_predict` output budget with the answer, so proposal writing, revision and answer checking reserve 16384 output tokens instead of 8192. Reasoning text is streamed to the live progress page while an action runs, held only in server memory, cleared when the action succeeds and never stored in the database |
| `OPENROUTER_API_KEY` | Secret; required only when OpenRouter is selected |
| `OPENROUTER_MODEL` | Exact model ID supporting structured outputs |
| `OPENROUTER_BASE_URL` | Default `https://openrouter.ai/api/v1` |
| `OPENROUTER_TIMEOUT` | Seconds; default 120 |
| `OPENAPI_FETCH_HOSTS` | Comma-separated `host:port` allowlist for OpenAPI URL fetch; empty disables fetch |
| `OPENAPI_FETCH_TIMEOUT` | Seconds; default 15 |
| `DATABASE_PATH` | Persistent SQLite file, relative to working directory or absolute |
| `CONNECTORS_FILE` | JSON file of sandbox connectors and trusted execution identities (see below); empty disables building and running artifacts |
| `SANDBOX_HOSTS` | Comma-separated `host:port` allowlist of destinations artifacts may call, for example `127.0.0.1:8001` |
| `DEV_REVIEWER_ID` | Server-derived development audit identity |
| `SESSION_SECRET` | Local browser session signing secret |

For OpenRouter primary with local fallback, explicitly set `LLM_PRIMARY=openrouter` and `LLM_FALLBACK=ollama`, and supply the OpenRouter key/model. Ollama primary with OpenRouter fallback is supported too. Selected providers must be configured; unused providers are ignored. Fallback happens once, only for timeout/connection errors, 429, or 5xx. Invalid output, refusals, bad bindings, and authentication/configuration failures are explicit failures with no fallback. There are no automatic repair/retry loops or cross-model replacement on invalid content.

The UI shows the primary/fallback configuration; each run records actual provider/model attempts. A successful provider response is not the same as accepted generation: grounding can still fail the overall run.

Proposal writing narrows what the model can emit: each step is decoded as one supplied operation, and its runtime, configuration and context bindings may only target that operation's own input keys, each at most once and at least as many as it has required inputs (previous-output bindings may target the input keys of any supplied operation). When grounding still rejects a proposal, the same request is sent once more with `grounding_feedback` (the rejected proposal and the check's errors); both runs stay on the request. Answer checking (reconciliation) does the same once when its revised proposal fails grounding. Before grounding, a declared configuration that no binding uses but whose key a runtime_argument binding now uses is treated as left over from rebinding that input: it is removed and its questions are unlinked (they stay required), recorded as diagnostic `stale_configuration_removed`. Grounding errors (at most 20, 300 characters each, secret-bearing lines redacted) are kept on the failed run and shown as "What the check found" under a failed request and on the live progress page of a failed action (for example Check answers).

## Walkthrough: e-commerce

1. Create a business named **Small Shop**. Copy `examples/ecommerce-business.txt` into its description.
2. Upload `examples/ecommerce.json`. Confirm two supported operations and the security/access warning.
3. Select **Generate proposals with live model**. Open the returned proposal. If the model fails validation, inspect the recorded error; a failed result is never replaced by a demonstration proposal.
4. Inspect the inputs: `order_id` and `summary` are future runtime arguments; `customer_id` comes from the earlier order response; `queue_id` is business configuration. Exact generated wording/grouping may vary. Reject or request revision for semantic mistakes, even when structural validation passes.
5. Answer the queue question with **yes**. Save, then reconcile. It should remain insufficient: this does not identify a queue. Approval stays blocked. Review the actual finding rather than assuming every live model behaves perfectly.
6. Correct the answer: **Use the exact queue_id "support" for all tickets. This is business configuration, not an input to ask customers for.** Save and reconcile.
7. Updating configuration is a material change: review the new version/diff. Its copied answer is evidence only. Reconcile that new version again. Approval is enabled only when the latest reconciliation resolves all questions and passes validation without blockers.
8. Select **Approve to build**. Verify the precise state; there is no activation or publication control.
9. Create an explicit revision with a clearer description. The replacement needs fresh review; old approval appears only in history.
10. For an unsupported request, request a refund capability. The example has no refund operation: the result must be a capability gap, not a fabricated binding.

Do not enter a real order ID during onboarding. Future customer identity/authorization is not supplied by owner answers. The only implemented application-context contract is `business_id`, which does not assert customer identity.

`examples/reviewed-ecommerce-proposal.json` preserves an actual reviewed local-model result and its source business ID for repeatable reference validation. It is documentation/test evidence, never a replacement response from a provider.

## Second domain

Create **Room Desk**, use `examples/room-booking-business.txt`, and upload `examples/room-booking.yaml`. A chained proposal can map the reservation response's `/room_id` to the service request's `body.room_id`. `reservation_id` and `message` are runtime inputs; `team_id` is business configuration. Answer with the exact team setting, such as **Use team_id "facilities" for all requests**. Inspect/reconcile revisions and reject this proposal to demonstrate decisions independent of Small Shop.

## Tests

### NileStay: no business configuration required

Use `examples/nilestay.json` with `examples/nilestay-business.txt`. These preserve the fictional uploaded NileStay test specification and description. The intended chain is `booking_reference` (runtime) → lookup response `/reservation_id` → create-service-request `body.reservation_id`. `category` and `message` are runtime arguments; the category enum is already in the API schema. No owner-wide API input setting is needed: `configuration: []` and zero `business_configuration` bindings are valid.

Service authentication belongs to the future connector, not model-supplied arguments. Guest ownership verification remains a required question with `configuration_key: null`; an empty configuration list never resolves that question or enables approval.

```powershell
uv run python -m scripts.live_smoke --provider ollama --example nilestay
```

Run records now expose sanitized structural diagnostics for model response, parsed output, and normalized generation output. Failed grounding also retains named configuration differences, including unused declarations, undeclared references and their step/target locations, plus the bounded list of grounding errors. View these through the run page or `GET /api/v1/proposal-runs/{id}`. Older runs cannot recover responses that were never retained.

Diagnostics omit prose, answers, configuration values, provider headers, reasoning and unknown fields. Only bounded structural identifiers are retained (up to 64 entries per collection and 160 characters per identifier), plus a response hash/byte count; configured provider/session secrets are redacted. This is a structural trace, not a complete raw response archive. Existing accepted proposal/history storage is unchanged. Diagnostic records live in the local SQLite database and should be treated as local project data.

```powershell
uv run pytest -q
```

Automated tests use `DeterministicModelSubstitute` and HTTPX mock transports, explicitly labeled in test code. They verify real application parsing, grounding, SQLite, APIs, forms, lifecycle, and concurrency. They do not establish live model quality. No test substitute is available as a production provider.

Separate real-generation smoke commands (write a dedicated database):

```powershell
uv run python -m scripts.live_smoke --provider ollama --example ecommerce
uv run python -m scripts.live_smoke --provider ollama --example room-booking
# Running target system; the item scope fits OLLAMA_CONTEXT=40960 (all 13 eligible operations do not):
$env:OPENAPI_FETCH_HOSTS="127.0.0.1:8001"; $env:OLLAMA_CONTEXT="40960"
uv run python -m scripts.live_smoke --url http://127.0.0.1:8001/api/v1/openapi.json --business-file examples/target-items-business.txt --path-prefix /api/v1/items --database data/openapi-live.sqlite3
# Requires your configured OpenRouter key/model:
uv run python -m scripts.live_smoke --provider openrouter --example ecommerce
```

These commands perform real inference, print outcomes/run IDs, and exit nonzero on failure. They do not claim reconciliation or approval coverage; use the manual workflow above for that. Inspect their saved records by starting the app with `DATABASE_PATH=data/live-smoke.sqlite3` if desired. Never run two app processes on the same database while testing restart behavior.

To test real fallback without making a paid primary request, run a separate local HTTP stub that returns 503, configure it as the OpenRouter base URL with nonsecret test credentials/model, explicitly enable Ollama fallback, and generate. This is **simulated primary failure plus live Ollama inference**, not a live OpenRouter test. Do not point failure tests at business endpoints.

## Core architecture and invariants

The implemented checkpoints can be checked independently:

1. Upload/inventory: `uv run pytest -q tests/test_discovery.py`.
2. Live provider/grounded proposals: run one smoke command above, then inspect its mappings; `uv run pytest -q tests/test_grounding.py` checks deterministic rejection rules.
3. Clarification/decisions/persistence: `uv run pytest -q tests/test_review.py tests/test_reconciliation_safety.py tests/test_canonical_reconciliation.py`.
4. Review interface/restart: follow the walkthrough, stop the server, restart with the same database, and reopen the proposal.
5. Second provider/fallback/edge cases: `uv run pytest -q tests/test_providers.py`; these provider tests use mock HTTP transports. OpenRouter live verification requires configured credentials and a structured-output-capable model.

Example dependency, validated in both automated domain walkthroughs:

| Step | API input | Proposed source |
|---|---|---|
| s1: reservation lookup | `path.reservation_id` | Future runtime argument `reservation_id` |
| s2: service request | `body.room_id` | s1 response `200`, pointer `/room_id` |
| s2: service request | `body.message` | Future runtime argument `message` |
| s2: service request | `body.team_id` | Owner-reconciled business setting `team_id` |

The validator checks ordering, response code, pointer existence, requiredness and schema compatibility. It cannot infer that two different string identifiers mean the same thing: mapping `/reservation_id` to `body.room_id` can be type-compatible but semantically wrong and must be corrected in review.

- `discovery.py`: offline OpenAPI extraction; original source bytes/schemas and diagnostics are persisted.
- `models.py` / `grounding.py`: structured proposal/input-source contracts, required-input checks, conservative schema compatibility, dependency and field validation.
- `providers.py`: untrusted-data prompt boundary, strict structured output, selectable providers, bounded calls and explicit fallback. No model execution tools.
- `service.py`: transactions around reconciliation, revision and decisions. A changed answer invalidates reconciliation. Material content changes create a new version. Even copied answers need reassessment. Approval binds the exact content, answer, inventory and reconciliation snapshot.
- `storage.py`: schema version 1, foreign keys, immutable content/history, WAL SQLite. Decision uniqueness/idempotency and expected revisions prevent duplicate/conflicting submissions.
- `web.py` / `templates/page.html`: escaped server-rendered review forms, CSRF, same-origin checks, local identity, REST API.

Lifecycle: `needs_clarification` → `needs_reconciliation` → `ready_for_review` → `approved_to_build` / `rejected` / `changes_requested`. Reconciliation may return to clarification or create an unapproved replacement. Replaced versions are `superseded`, with their decisions retained. Runs are `running`, `succeeded`, `failed`, or `interrupted` once their owning process has exited.

Access requirements: the server derives durable access/identity requirements from the proposal's operations (declared authentication → who may call; `path.*` inputs → which records; no declared security → whether unauthenticated use is intended), independent of model questions. Each needs an answer, a current reconciliation assessing it as sufficient, and an explicit owner confirmation (`POST /api/v1/proposals/{id}/versions/{v}/requirements/{rid}/confirm`) before approval. A changed answer or a new version requires this again. Evidence is shown separately as source-documented, owner-confirmed, test-verified (none) and runtime implementation (awaiting). Approved to build is not runtime-ready.

Question review: a question naming a field the selected operations only return (not an input or configuration) is flagged. The owner may supersede a question without a configuration key (`POST …/versions/{v}/questions/{qid}/supersede` with a reason); this creates a regrounded version and keeps the question, reason and answers in history. Versions after the first show a readable change summary before the decision.

Live check on a database copy: `$env:PYTHONPATH="."; $env:OLLAMA_CONTEXT="40960"; python -m scripts.live_review --proposal <id>` copies `data/openapi-live.sqlite3` read-only to `data/openapi-live-review-copy.sqlite3` and applies labeled TEST-ONLY answers there only.

### Executable artifacts and sandbox runs

An `approved_to_build` version can be compiled, without a model, into an immutable tool artifact (`artifacts.py`). The artifact holds: name, purpose, input and output schemas, the approved version and decision ID, the spec SHA-256 and operation source pointers, ordered steps with parameter locations and serialization, body mappings, declared success statuses, media types and schemas, output pointers, connector and authentication, access requirements with their enforcement, limits and failure behaviour. Content is deterministic and hashed, so rebuilding returns the same artifact. Anything the executor cannot perform exactly (header/cookie inputs, non-simple path styles, object parameters, unbound path placeholders, unsupported authentication) rejects the build with `artifact_unsupported`. Approval permits artifact creation and sandbox testing only. `runtime_ready` and `activated` stay false; sandbox publication is a separate decision (see "Sandbox MCP publication").

Each owner-confirmed access requirement needs an enforceable mechanism. Record-scope requirements are derived from path parameters, required query parameters, and inputs that carry an identifier from an earlier response. The operator submits the mechanisms (`POST /proposals/{id}/enforcement`). They are validated immediately, but usable only after review (`POST /enforcement/{id}/review`, which records the reviewer, time and note). The artifact records which reviewed configuration it was built from. Submitting a new configuration invalidates earlier artifacts (`enforcement_not_current`) until it is reviewed and the artifact rebuilt. The model never supplies or changes enforcement. Requirements without a reviewed mechanism are listed as `execution_blockers` and block every run.

Supported mechanisms, and nothing else:

- `caller_access`: `{"mechanism": "delegated_user_credential"}` (`enforced_by: target_api`). Each run uses one operator-registered identity's own credential, and identities not labelled `scope: end_user` are refused. The label is an operator declaration, not proof of authorization. The target API decides what the credential may do; Team C verifies this only through sandbox tests.
- `record_scope`: `{"mechanism": "response_field_matches_context", "step_id", "response_status", "pointer", "context_field", "comparison": "equals", "check_point": "after_step"}` (`enforced_by: team_c_executor`). Its rules:
  - The check runs on a read step.
  - The pointer (nested allowed) must be a required, non-nullable string or integer in that response.
  - `context_field` must be declared in the connector's `context_fields`.
  - Every other step touching the record must follow the check and take the identifier either from the same source or from the checked step's response.
  - If the value differs from the identity's trusted context value, no later step runs.
- **Not supported:** service-account execution on behalf of many users, organization/team membership or role checks, list/set comparisons, and checks after a write. Such requirements stay blocked; they are not forced into equality checks.

`CONNECTORS_FILE` format (keep it out of version control; credentials never enter artifacts, prompts, arguments or reports):

```json
{"connectors": {"target-sandbox": {"business_id": "<business id>", "base_url": "http://127.0.0.1:8001", "sandbox": true, "context_fields": ["user_id"],
  "identities": {"alice": {"username": "alice@example.org", "password": "...", "scope": "end_user", "context": {"user_id": "<user id>"}}}}}}
```

Use `"token": "..."` instead of `username`/`password` for HTTP bearer APIs.

The executor (`executor.py`) validates arguments against the input schema before any request and rejects unknown arguments, so identity cannot be passed as an argument. Its host must be in `SANDBOX_HOSTS` and match the base URL recorded in the artifact. It obtains tokens from the spec's OAuth2 password `tokenUrl`, which must be a path on the connector origin, or uses a bearer token. Path values are percent-encoded and `.`/`..` are refused. Redirects are never followed. Requests and responses are bounded, and each response's status, media type and schema are checked. It stops at the first failure and never retries. Run statuses:

- `succeeded`: every step and output completed.
- `failed`: no write was sent, or the API rejected the write with a 4xx. A rejected write has `write_state: rejected_unverified`: the rejection was observed, but that nothing changed is not asserted without independent state verification.
- `partial`: an earlier write applied, then a later step failed.
- `outcome_unknown`: a write timed out, lost its connection or got a 5xx; check the target before retrying.

Before a run, the server checks that the approval is still current, that the stored content matches the approved snapshot, the destination and the credentials. A failed pre-flight is recorded as `rejected` with `requests_sent: 0`.

**Liveness of runs and executions.** Every model run and execution records the process that owns it. Each process holds an exclusive OS file lock (`<database>.owners/<owner>.lock`) for as long as it lives. When a Team C or MCP process starts, it marks a `running` row `interrupted` only if its owner's lock can be acquired (so the owner has exited) or the row has no owner (legacy data). Runs of other live processes are left alone. An interrupted execution reports that a write may or may not have been applied; nothing is retried. `tests/test_lifecycle.py` checks this with a real second process.

API: `POST /proposals/{id}/enforcement`, `POST /enforcement/{id}/review` (`note`), `POST /proposals/{id}/artifacts` (`connector_id`), `GET /artifacts/{id}`, `POST /artifacts/{id}/sandbox-runs` (`identity`, `arguments`), `POST /artifacts/{id}/sandbox-tests` (`name`, `identity`, `arguments`, `expect`), `POST /sandbox-tests/{id}/repairs` (`verification`). The proposal page has the enforcement submit/review and build forms plus repair history. `/artifacts/{id}` shows steps, enforcement provenance, a sandbox-run form, test results and execution history.

#### Sandbox tests and bounded repair

A sandbox test pairs a run with an expectation written by the operator or test author: `status`, optionally `failure_step`/`failure_outcome`, `outputs` (exact values) and `outputs_present`. A failed test stores a structured failure report: the case, expected versus actual status, failing step and operation, that step's bindings, affected output mappings and a sanitized trace. Target response values never enter the report; only codes, names and pointers do. Each failure is classified:

| Classification | Repairable | What happens |
|---|---|---|
| `mapping_suspect` (bad binding pointer, 4xx on a mapped request, schema mismatch, missing output) | yes | model repair (max 2 per case) |
| `authentication`, `access_or_authentication` (401/403), `configuration`, `test_input` | no | fix configuration or the test |
| `access_check` (Team C blocked the run), `expected_denial_not_observed` | no | owner review; access is never repaired |
| `uncertain_write`, `partial_write` | no | inspect target state manually; nothing is retried |
| `connectivity`, `destination` | no | check the connector |

A repair reuses the revision path with a server-generated instruction. The instruction contains:
- the failure report;
- contract evidence from the stored inventory: the failing operation's bound input schemas, the response schemas those bindings read, and the documented meaning of the observed status;
- for a second attempt, what the first attempt changed, why it was not accepted, and the error that remains.

The model answers with one of three outcomes:
- `revised`: the complete corrected proposal;
- `cannot_repair`: rewiring the existing steps cannot meet the expectation;
- `capability_gap`: a needed operation or response field does not exist.

The last two end the repair and ask for an owner revision or clarification. They are recorded separately from `repair_out_of_scope`.

A revised proposal may only rewire the existing steps. Operations, questions, configuration, trusted-context bindings and the runtime-input set must stay the same, or the attempt is rejected as `repair_out_of_scope`. The result is grounded like any revision and becomes a new, unapproved version. That version needs fresh answers, reconciliation, requirement confirmation, approval, a reviewed enforcement configuration and a rebuild before the test is rerun.

If a write was rejected, repair waits for `verification` (independent evidence that nothing changed). After two model attempts a case is `repair_exhausted`, and the check makes no model call. Every attempt, its feedback and every refusal are kept in the `repairs` history.

`scripts/repair_only.py` checks repair live without regenerating anything. It copies the saved `data/desk-base.sqlite3` into `data/desk-repair.sqlite3`, clones the saved TEST-ONLY reviewed proposal there, and recreates only the disposable fixture data.

#### Supported subset

| Area | Supported | Not supported (explicitly reported) |
|---|---|---|
| Media | `application/json` requests/responses | multipart, form, XML, other media (operation marked unsupported) |
| Parameters | path (simple, primitive), query (form; primitive, or exploded primitive arrays) | header/cookie inputs, deepObject/other styles, object parameters |
| Bodies | whole JSON body or top-level fields | nested field construction, transforms |
| Responses | explicit 2xx codes with JSON schemas; nested-object pointers | `2XX` ranges, array indexing in pointers |
| Chaining | earlier step output to a later input (same meaning checked by review only) | forward references, loops, conditionals |
| Auth | OAuth2 password flow (token URL on the connector origin), HTTP bearer | API keys, client credentials, mTLS, cookies |
| Enforcement | delegated user credential; response field equals trusted context field | service accounts, org membership, roles |

Two domains (the item API and the service-desk fixture with a renamed variant) are evidence of generality within this subset, not proof of support for arbitrary APIs.

#### Second domain: service-desk fixture

`fixtures/service_desk/app.py` is a local FastAPI app whose expected behavior and access rules are stated in its docstring. A booking lookup takes a required query reference and returns a nested internal identifier and holder to any authenticated caller. Filing a request needs that identifier and returns 201 in a different envelope; the backend refuses non-holders with 403. Attachment upload is multipart (unsupported). The `renamed` variant changes every path and field and uses an integer holder number. The live run (discovery, live-model proposal, TEST-ONLY review, enforcement, build, sandbox tests, independent state checks):

```powershell
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe scripts/domain_demo.py --variant base --port 8002 --repair
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe scripts/domain_demo.py --variant renamed --port 8003
```

These start the fixture on the given port, write `data/desk-<variant>.sqlite3` and print a sanitized JSON log. With `--repair`, a labeled FAULT INJECTION (authored, recorded as provider `FAULT_INJECTION`) creates a mapping defect through the revision path; the live model must then repair it. A missing-capability case follows. Mocked equivalents with an authored substitute model: `uv run pytest -q tests/test_second_domain.py`.

Live demonstration against the local target, on a copy of the TEST-ONLY approved review database (requires the target at `127.0.0.1:8001` and `data/openapi-live-review-copy.sqlite3`):

```powershell
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe scripts/sandbox_demo.py > data/sandbox-demo.log.json
```

It copies the database read-only to `data/sandbox-demo.sqlite3`. It creates two disposable non-superuser accounts with one item each through public signup, writes `data/sandbox-connectors.json`, builds the artifact and runs the scenarios. It checks backend state with an independent client, then deletes the items and accounts; pass `--keep` to keep them for manual runs in the UI. To browse the result: `$env:DATABASE_PATH="data/sandbox-demo.sqlite3"; $env:CONNECTORS_FILE="data/sandbox-connectors.json"; $env:SANDBOX_HOSTS="127.0.0.1:8001"`, then start the app.

#### Sandbox MCP publication (TEST-ONLY, local development only)

Publication is a separate, explicit decision from approve-to-build: nothing built is exposed until an operator publishes that exact artifact (artifact page, or `POST /artifacts/{id}/publications`). The publication record stores the artifact hash and proposal version, the approval decision, the enforcement configuration id and hash, the IDs of the passing sandbox tests, the publisher (the development reviewer label), the time, status and environment (`sandbox`). Production activation and production readiness are always false.

Publishing is refused unless all of the following hold:

- The artifact is intact, its approval is still current, its enforcement is current and reviewed, it has no execution blockers, and its connector is a sandbox destination.
- **This artifact's own** sandbox tests include at least one passing success test.
- If the reviewed requirements include a record-scope requirement, there is a passing explicit access-control scenario. Sandbox tests declare `scenario`: `general`, `own_record` (must expect success) or `cross_user` (must expect failure and name `record_owner`, another configured identity). The `cross_user` test counts only if all of the following hold:
  - the caller has its own passing `own_record` test, which shows its credential is valid and it can perform the allowed control;
  - the record owner has a passing `own_record` test on the same record (same record-selecting arguments);
  - the recorded trace shows the refusal happened before any write.
- The trace classifies each refusal:
  - `authentication_failure`: a 401 or failed connector authentication. It never counts.
  - `general_access_denial`: a 403/404 when the caller never showed it can use its own record. It never counts.
  - `target_record_denial`: the API refused the other user's record after the caller succeeded on its own. It counts.
  - `record_scope_enforced`: Team C's record check refused the call. It counts.
- Public tools, and tools that only need caller access, require only a success test. No cross-user test is invented for them.
- No test name's latest run failed.

Tests of an older or different artifact never count.

`python -m team_c.mcp_server` is a stdio server built on the official SDK (`mcp==2.2.0`, pinned in `pyproject.toml`; lowlevel `Server`). Protocol messages go to stdout and logs to stderr; httpx request logging is off because URLs carry record references. Behavior:

- **Tool definitions.** Tools are generated from the stored artifacts: name `<artifact-name-slug>_v<version>_<artifact-id-hex>` (stable per artifact, unique across artifacts), title, description, the artifact's input schema (OpenAPI `nullable` rendered as a JSON Schema null type), an output schema, and `_meta.team_c` with the publication/artifact/version mapping.
- **Rechecks.** Every `tools/list` and every `tools/call` re-reads the database. Disabling, a superseded approval, changed enforcement, a changed artifact or a newly failing test blocks the next call, including from a client holding an old tool list. No restart is needed.
- **Refresh.** New publications appear on the client's next `tools/list`. The server does not send `list_changed` notifications, so clients re-list (or restart).
- **Disabling does not undo writes.** Disabling does not undo a write that already completed or is in flight.
- **Delegation.** Calls go through `run_sandbox`, so every pre-flight check, enforcement and trace applies. They are recorded as executions with mode `mcp_sandbox`, carrying the publication id; audit reports keep output hashes only, while the caller receives the permitted outputs.
- **Caller identity.** Each process is bound at startup to `--business-id` and `--identity` (a sandbox identity in the trusted connectors file). There is no shared mutable identity, and no tool argument can choose a user or credential (unknown arguments are rejected). This is a local development mechanism, not production authentication: run one process per test user.

```powershell
$env:PYTHONPATH="."; .\.venv\Scripts\python.exe -m team_c.mcp_server --business-id <business-id> --identity alice `
  --database data/mcp-desk.sqlite3 --connectors data/mcp-desk-connectors.json --sandbox-hosts 127.0.0.1:8004
```

Client configuration (Cursor / Claude Desktop style; paths are examples):

```json
{"mcpServers": {"team-c-sandbox-alice": {
  "command": "D:/nti project/team_c/.venv/Scripts/python.exe",
  "args": ["-m", "team_c.mcp_server", "--business-id", "<business-id>", "--identity", "alice",
           "--database", "data/mcp-desk.sqlite3", "--connectors", "data/mcp-desk-connectors.json", "--sandbox-hosts", "127.0.0.1:8004"],
  "cwd": "D:/nti project/team_c", "env": {"PYTHONPATH": "."}}}}
```

**Team B integration contract.** Discover tools with `tools/list` and call them with `tools/call`, sending only the arguments in `inputSchema` (`additionalProperties: false`). Every call result has `structuredContent` (mirrored as JSON text in `content[0]`):

`{status, output, error{code, message, step_id?, outcome?, errors?}, message, execution_id, writes[{step_id, write_state}], tool, publication_id, artifact{id, sha256, proposal_id, proposal_version}, production_ready: false}`

| status | isError | meaning | may the agent retry? |
|---|---|---|---|
| `succeeded` | false | all steps ran; `output` holds the reviewed outputs | n/a |
| `rejected` | true | refused before any request (arguments, stale approval/enforcement, evidence, configuration) | only after fixing arguments |
| `failed` | true | a step failed; see `writes` (`rejected_unverified` means the API refused a write but no-change is unverified) | not blindly |
| `partial` | true | an earlier write applied, a later step failed; nothing rolled back | no |
| `outcome_unknown` | true | a write may or may not have applied | no; check the target first |
| `error` | true | server failure while handling the call; outcome unknown | no |

An unknown, never-published or disabled tool name is a JSON-RPC error `-32602` with `data.code = "tool_not_published"`. Team B must not treat `partial` or `outcome_unknown` as success.

Evidence:

- **Mocked, in process** (authored substitute, mock transport): `tests/test_publication.py`.
- **Real SDK client and real server process** (fixture over HTTP; authored proposals, no model calls; both service-desk variants): `tests/test_mcp_stdio.py`.
- **Real smoke run on both domains** (running item target and fixture process; reuses saved artifacts from copies of the TEST-ONLY databases; disposable accounts; isolated `data/mcp-*.sqlite3`): `$env:PYTHONPATH="."; .\.venv\Scripts\python.exe scripts/mcp_smoke.py > data/mcp-smoke.log.json`.

### Owner-requested tools and suggestions

Both features use the latest valid OpenAPI inventory of the business. The model sees a **compact operation index**:
- per operation: id, method, path, short summary, status (`eligible`, `restricted` or `unsupported`), declared auth status, input names and top-level return names;
- eligible operations come first, and the index is cut off at `CAPABILITY_INDEX_CHARS` (default 16000);
- the considered and omitted operation ids are stored, and a cut shows as incomplete coverage.

Detailed schemas are sent only for the operations chosen for generation. Model output may only name operation ids from the index and ids of existing tools; this is enforced in the decoding schema and again by deterministic checks.

**Request a tool** (business page):
1. The owner enters a goal and optional examples. The hidden request key makes a repeated submission return the same request.
2. Triage returns one of four outcomes:
   - `feasible`: names eligible operations, then normal generation runs on only those operations, with the owner request attached;
   - `needs_clarification`: asks questions;
   - `existing_tool`: cites an existing proposal;
   - `unavailable`: names each absent operation (citing no id), unsupported operation or restricted operation.
3. Invalid triage fails the request visibly. Examples are an invented or restricted operation, or an "absent" operation that exists.
4. A generated proposal identical, step for step, to an existing active proposal is not stored; the request points to the existing tool instead.
5. New proposals start in `needs_clarification` and go through the normal answers, reconciliation, approval, enforcement, build, test and publication flow. There is no other path.
6. Each request stores its status (`processing`, `needs_clarification`, `proposed`, `existing_tool`, `unavailable`, `failed`), model runs, linked proposals, unresolved needs, coverage and outcome. The owner can answer and reprocess a request that is not yet proposed.

**Suggest additional tools** (business page):
1. The model receives the business description, recent request goals, the index, existing tools (state, built, published) and earlier suggestions, and returns at most N ideas (`SUGGESTION_COUNT` default 3, capped at `SUGGESTION_MAX` 5). Each idea has a purpose, benefit, business reason, supporting operations, relationship to existing tools and missing information.
2. Screening withholds, with a reason:
   - invented operations;
   - restricted operations used as steps (restricted operations stay restricted);
   - unexplained categories;
   - duplicates of earlier suggestions (open, dismissed or accepted);
   - suggestions over the count.
3. Suggestions whose operations equal an existing active tool are listed as already covered.
4. The category comes from the evidence, not the model label: absent, unsupported or restricted means `blocked_by_missing_api`; missing information means `needs_clarification`; otherwise `feasible`.
5. The owner decides on each suggestion:
   - Accept starts a normal tool request on the suggestion's operations. It is not build approval; blocked suggestions cannot be accepted, and `needs_clarification` ones need an answer.
   - Dismiss changes only the suggestion.
   - Revise stores an owner-edited copy.

UI steps:
1. Open `/businesses/<id>`, which must have a discovered OpenAPI inventory.
2. In **Request a tool**, enter a goal and optional examples, then press **Request a tool**. The request appears below with its status. Follow the proposal link to review, answer questions or use **Send and process again**.
3. In **Suggest additional tools**, choose a number and press **Suggest additional tools**. Each suggestion shows its category, operations and missing information. Use **Accept: start proposal review** (with the missing information if asked), **Dismiss**, or **Revise**.
4. Every resulting proposal is reviewed on its proposal page exactly like a generated one.

The model can still miss a gap. In the live check it called "cancel a booking and refund" feasible. Generation then reported the missing cancel operation as a capability gap, and that gap blocks approval until an explicit revision. Owner review remains the fallback; the checks only reject what they can prove wrong.

Capability gaps returned with proposals remain blocking until an explicit revision addresses the unsupported scope. A reconciliation cannot silently remove them. An explicit revision that returns a gap preserves the existing version/decision.

## API summary

All routes below use `/api/v1`:

- `POST/GET /businesses`
- `POST /businesses/{id}/specifications` (multipart `file`)
- `POST /businesses/{id}/specifications/fetch`: `url` (allowlisted host)
- `GET /specifications/{id}/inventory`
- `POST /inventories/{specification_id}/proposal-runs`, optional body `operation_ids` (subset of eligible operations; default all) (inventory identity equals its immutable specification ID)
- `GET /proposal-runs/{id}`
- `GET /businesses/{id}/proposals`
- `GET /proposals/{id}?version=N`
- `POST /proposals/{id}/versions/{version}/answers`: `expected_revision`, `answers` keyed by question ID
- `POST /proposals/{id}/versions/{version}/reconcile`: `expected_revision`
- `POST /proposals/{id}/versions/{version}/decisions`: `expected_revision`, `action`, `reason`, `idempotency_key`
- `POST /proposals/{id}/revisions`: `expected_revision`, `instruction`
- `POST /artifacts/{id}/publications`: `note` (environment is always `sandbox`); `GET /publications?business_id=`, `GET /publications/{id}`, `POST /publications/{id}/disable`: `note`
- `POST /artifacts/{id}/sandbox-tests`: `name`, `identity`, `arguments`, `expect`, `scenario` (`general`/`own_record`/`cross_user`), `record_owner`
- `POST /businesses/{id}/tool-requests`: `goal`, `examples`, `idempotency_key`, optional `spec_id`; `GET /businesses/{id}/tool-requests`, `GET /tool-requests/{id}`, `POST /tool-requests/{id}/clarifications`: `text`
- `POST /businesses/{id}/suggestion-runs`: optional `count`, `spec_id`; `GET /businesses/{id}/suggestions`; `POST /suggestions/{id}/decision`: `action` (`accept`/`dismiss`/`revise`), `note`, `clarification`, `title`, `purpose`

Actions are `approve_to_build`, `reject`, `request_changes`. A change request requires a reason. Stale/conflicting submissions return 409; blocked approval/invalid input returns 422; provider errors return 502/503. Errors include stable codes and relevant run IDs. There is no API accepting a client reviewer identity.

## Limitations

This is a **loopback, single-process development app**, not authenticated multitenant hosting. Every local operator shares the server-configured development reviewer label. Business IDs partition records but are not authorization boundaries. Do not expose the server to a network or use it for real private production data. Uploaded specifications and owner answers are sent to whichever provider you explicitly configure.

The OpenAPI subset and compatibility evidence are in `docs/OPENAPI_SUBSET.md`. The source version is never rewritten; 3.1 schemas are normalized only where semantics are preserved. Unsupported constructs block the affected operations (and operations sharing the component), not the whole upload; only document-level errors block everything. Input/output mappings are declarative direct mappings; arrays, transforms and general schema subsumption are not implemented. Only explicitly supported mappings can pass.

The sandbox MCP server has these limits:

- It is stdio only, one test identity per process, bound by command-line flags. That is not authentication.
- Revocation is enforced at the next call. It cannot stop or undo a write that has already been sent.
- There are no `list_changed` notifications.
- Liveness uses local file locks. It is correct for processes on one machine sharing one database file, not for network filesystems.
- Returned outputs are whatever the reviewed output contract selects (for the repaired desk artifact, that includes the caller's own booking holder details).

LLM interpretations and reconciliation are fallible. Deterministic validators verify references, schema relationships, versioning, and review preconditions; they cannot prove all natural-language claims. Owner review remains necessary. The small local model may reject/fail a run or propose an unnecessary gap; errors and rejected results are retained, not silently repaired. See `docs/VERIFICATION.md` for actual results and unverified checks.
