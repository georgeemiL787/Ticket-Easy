# Architecture

How the Team C source is organized, which way dependencies point, and the rules for changing the schema and the artifact format. Feature behavior is described in the [README](../README.md).

## Project layout

| Path | Contents |
|---|---|
| `team_c/` | Application source (package `team_c`) |
| `tests/` | pytest suite (`testpaths` in `pyproject.toml`); `tests/fixtures/` holds the Petstore OpenAPI snapshots |
| `docs/` | This file, the OpenAPI subset, verification records and investigation notes |
| `scripts/` | Opt-in live checks and demos (see below); not used by the app or the tests |
| `examples/` | Sample inputs: OpenAPI documents (`ecommerce.json`, `room-booking.yaml`, `nilestay.json`), business descriptions (`*-business.txt`), the reviewed e-commerce proposal used as reference evidence, and `local-project/`, a small FastAPI app used only by the retired code discovery (inspected, never run) |
| `fixtures/service_desk/` | Local FastAPI service-desk app used as the second domain by tests and scripts (`base` and `renamed` variants) |
| `data/` | Runtime data: SQLite databases, owner lock files, connector files, demo logs. Ignored by git; never commit it |
| `.env` | Local settings and secrets (ignored by git); `.env.example` lists the keys |

`scripts/`:

| Script | Purpose |
|---|---|
| `live_smoke.py` | Real generation against an example or a fetched OpenAPI URL, in a dedicated database |
| `live_review.py` | Review lifecycle with the live model on a copy of a proposal database |
| `domain_demo.py` | End-to-end live run on the service-desk fixture (`--variant base` or `renamed`, optional `--repair`) |
| `repair_only.py` | Live repair check on a copy of the saved service-desk database |
| `request_live.py` | Live tool-request and suggestion check on the service-desk fixture's OpenAPI document |
| `sandbox_demo.py` | Sandbox build and runs against the local target on a copy of the review database |
| `mcp_smoke.py` | Real MCP client and stdio server against both domains |
| `code_smoke.py` | Live check of the retired local-code discovery |
| `count_tokens.py` | Counts Qwen prompt tokens for recorded messages (needs the extra `tokenizers` package) |
| `start_target.ps1` | Starts the cloned target application in Docker (see [TARGET_RUNTIME.md](TARGET_RUNTIME.md)) |

## Module map

Entry points:

| Module | Responsibility |
|---|---|
| `app.py` | `create_app(settings=None, provider_factory=None)`: builds the `Store`, `Providers` and `Service`, puts them and the job registry on `app.state`, adds session, trusted-host and same-origin middleware, error handlers, templates and routers |
| `mcp_server.py` | Stdio MCP server for TEST-ONLY sandbox publications (`python -m team_c.mcp_server`); builds its own `Store` and `Service` with no model providers |

HTTP layers:

| Module | Responsibility |
|---|---|
| `api/__init__.py` | `/health` and the `/api/v1` routers |
| `api/deps.py` | Typed route dependencies that read `service`, `store`, `settings` and `jobs` from `app.state` |
| `api/queries.py` | Read-only SQL for listing pages, kept in one place |
| `api/businesses.py`, `proposals.py`, `artifacts.py`, `requests.py`, `runs.py` | JSON routes, grouped by domain; each calls the service facade |
| `web/forms.py` | `check_csrf`, `field_kind`, `form_arguments` |
| `web/render.py` | Jinja templates and the shared page context |
| `web/pages.py` | HTML pages and the live progress page (`/live/{id}`, cancel) |
| `web/actions.py` | Browser form actions (`POST /actions/{action}`) |
| `web/templates/` | Server-rendered templates |
| `jobs.py` | Background AI jobs started by browser actions: one at a time, live progress, cancel, `LIVE_WAIT_SECONDS` before redirecting to the live page, entries expire after one hour |
| `progress.py` | `LIVE`, `live_step`, `check_cancelled`: the progress/cancellation contract shared by jobs and providers |

Workflow:

| Module | Responsibility |
|---|---|
| `service.py` | `Service(settings, store, providers)`: facade that keeps every earlier public method and delegates to the services below |
| `services/runs.py` | Recorded model runs and fitted operation indexes |
| `services/discovery.py` | Businesses, OpenAPI upload/fetch, legacy code inventories, business areas |
| `services/proposals.py` | Shared proposal rules: `view`, `snapshot`, `check_current`, `settle`, `scoped`, change summaries |
| `services/generation.py` | Proposal generation with one grounding retry; generation batches |
| `services/requests.py` | Owner tool requests and suggestions |
| `services/review.py` | Answers, reconciliation, decisions, revisions, requirement/gap/question decisions |
| `services/building.py` | Approval check, enforcement, artifacts, sandbox runs and tests, repair |
| `services/publication.py` | Sandbox publication and published-tool calls |

Model calls (`llm/`):

| Module | Responsibility |
|---|---|
| `llm/prompts.py` | System prompts and model messages |
| `llm/schemas.py` | Strict structured-output schemas and pointer constraints |
| `llm/budget.py` | Ollama context and output-token budgets |
| `llm/types.py` | `AttemptInfo`, `ProviderResponse` |
| `llm/base.py`, `ollama.py`, `openrouter.py`, `groq.py` | One class per provider |
| `llm/openai_compat.py` | Strict `json_schema` response format and the chat-completions call/answer checks shared by OpenRouter and Groq |
| `llm/router.py` | `Providers`: provider chain `LLM_PRIMARY` then `LLM_FALLBACK` (`none`, one provider or an ordered comma-separated list such as `openrouter,ollama`; `Settings.llm_chain`), records an attempt and diagnostics for every provider tried, moves to the next provider only on `provider_service_failure`. The Ollama budget is checked inside the Ollama provider, so only when Ollama is attempted |

Before moving to the next provider, the router expands Groq into one attempt per distinct nonblank key (`GROQ_API_KEY`, `GROQ_API_KEY_2`, `GROQ_API_KEY_3`). Only service failures advance the chain. Each call rebuilds this order with no persistent failure state; keys can recover when their rate limits reset. Credential selection is local to each provider instance, so concurrent calls do not mutate shared settings. Diagnostics redact all configured model keys.

Groq strict structured output cannot disambiguate the correlated operation/status/pointer unions used by proposal generation. `llm/schemas.groq_schema` creates a separate wire schema that merges those response enums and uses binding kind as the sole binding discriminator. The router checks Groq answers against the original full JSON Schema before removing operation echoes or parsing proposals; grounding checks still run afterward. Invalid output never triggers credential or provider fallback. Groq HTTP 413 is retryable only when the structured error code is `rate_limit_exceeded`; other 413 responses remain request failures.

Persistence (`persistence/`):

| Module | Responsibility |
|---|---|
| `persistence/db.py` | `Store`: connections (WAL, foreign keys), migrations on open, run/attempt/diagnostic helpers |
| `persistence/util.py` | `now`, `uid`, `dump`, `digest` |
| `persistence/recovery.py` | Per-process ownership locks and recovery of runs/executions whose owner has exited |
| `persistence/migrations/` | Ordered migrations recorded in `schema_migrations` |
| `persistence/repositories/` | Focused queries (`proposals`, `runs`, `enforcement`, `publications`) |

Domain modules (no web or service dependencies): `models.py` (Pydantic contracts), `config.py` (`Settings`, `AppError`), `contracts.py` (TypedDicts for inventory, operations, compiled artifacts and execution reports), `discovery.py` (OpenAPI extraction), `grounding.py`, `artifacts.py`, `executor.py`, `capabilities.py`, `requirements.py`, `repair.py`, `publishing.py`, `areas.py`, `diagnostics.py`, and `legacy/code_discovery.py` (retired code discovery, kept so existing code inventories stay readable).

## Dependency direction

```text
app.py, mcp_server.py
  -> api/, web/            (web also uses api/deps.py and api/queries.py)
  -> service.py (facade)
  -> services/
  -> persistence/ (repositories, Store) + domain modules + llm/
```

Rules:

- `llm/` and `persistence/` never import `services/`, `service.py`, `api/`, `web/`, `jobs.py` or `app.py`.
- Domain modules never import those layers either.
- Services reach each other only through the sibling instances passed to their constructors, not through the facade.

A check of every `import` in `team_c/` found no violations. Internal modules import from the real locations (`persistence.db`, `persistence.recovery`, `persistence.util`, `llm.*`, `legacy.code_discovery`); no module in `team_c/` imports `storage`, `providers` or `code_discovery`. The shims below exist only for external callers (tests, scripts, older imports).

## Compatibility shims

| Old import | New location |
|---|---|
| `team_c.web:create_app` | `team_c.app:create_app` (lazy re-export in `web/__init__.py`) |
| `team_c.storage` (`Store`, `INTERRUPTED`, `OWNER`, `claim`, `owner_alive`, `digest`, `dump`, `now`, `uid`) | `team_c.persistence` (`db`, `recovery`, `util`) |
| `team_c.providers` (`Providers`, prompts, budgets, schema helpers, `ollama_stream`, `LIVE`, `live_step`, `check_cancelled`) | `team_c.llm.router`, `llm.prompts`, `llm.budget`, `llm.schemas`, `llm.ollama`; `team_c.progress` |
| `team_c.code_discovery` | `team_c.legacy.code_discovery` |

Moved without a shim: `team_c/templates/` is now `team_c/web/templates/`, and `team_c.web.LIVE_WAIT_SECONDS` is now `team_c.jobs.LIVE_WAIT_SECONDS`. New code should import the new locations.

## Transactions

- Repositories never open, commit or roll back a transaction. Write functions take the caller's connection.
- A workflow that must be atomic opens one write transaction in the service (`with self.store.connect(write=True) as c:`, which starts `BEGIN IMMEDIATE` and commits or rolls back on exit) and passes `c` to every repository call in it.
- `Store.start_run`, `finish_run`, `attempt` and `diagnostic` each open their own short write transaction; they record run history and are not part of a workflow's transaction.

## Migrations

`Store(path)` applies pending migrations when it opens a database. `persistence/migrations/__init__.py` runs each one in its own `BEGIN IMMEDIATE` transaction together with its row in `schema_migrations(version, name, applied_at)`, so a migration is either fully applied and recorded or not at all; concurrent starts apply each one once. There are five: `baseline`, `run_and_execution_owner`, `sandbox_test_scenario_owner`, `sandbox_test_selector_sha256`, `gap_dismissals`. The older `schema_version` table (rows 1 and 2) is kept unchanged as a historical marker.

To add a migration:

1. Append a `(version, name, apply)` tuple to `MIGRATIONS` in `persistence/migrations/versions.py`. The version is the next integer (versions must stay contiguous from 1) and the name must be unique; `tests/test_migrations.py` checks both.
2. Use `script(sql)` for new tables (`CREATE TABLE IF NOT EXISTS`) and `columns((table, column), ...)` for new columns; `columns` adds a `TEXT` column only if it is missing, so it is safe on databases that already have it.
3. Never edit, reorder or remove a migration that has been released; databases record each version once and will not rerun it.

## Artifact format

MCP publication converts normalized OpenAPI schemas to JSON Schema 2020-12, including nullable types and exclusive numeric bounds, while preserving enum constraints. Both wire schemas must pass meta-schema validation before publication, listing or invocation. Conversion happens in memory; stored artifacts and their hashes remain unchanged. Invalid legacy schemas are blocked with `invalid_mcp_schema`.

The executor validates decoded paths and the HTTP client's normalized URL against the connector's origin and base-path boundary before sending a request. Dot segments, backslashes and control characters are rejected even when repeatedly percent encoded. Suggestion title matching uses Unicode normalization and case folding so distinct Arabic titles remain distinct.

Generation batches use the primary provider's input budget. An optional Ollama fallback does not shrink a cloud-primary batch; Ollama checks its own budget if attempted. Cloud calls recheck cancellation when response headers arrive and after the response is read, and always finish their progress step. A blocking request may still wait for a response or timeout, but its cancelled result is discarded and cannot trigger fallback or save proposals.

Compiled artifacts carry `format` (`FORMAT` in `artifacts.py`, now `team_c.tool_artifact/2`). Rules:

- Artifacts are immutable and hashed. A rebuild with identical content returns the existing record; a rebuild whose hash differs creates a new artifact record.
- When compiled content changes shape, bump the version in `FORMAT`.
- The executor must keep running every earlier format. It decides behavior from the fields present, not from the format string: `/2` step bodies carry `required` and the full request `schema`; `/1` bodies have neither and are always sent without that validation.

## Migration and compatibility notes

For anyone upgrading an existing checkout or database:

- **Database.** On the first start after upgrading, the `schema_migrations` table is created and filled automatically for existing databases. Missing tables and columns are added; existing data is kept. No manual step is needed.
- **Artifact format `/2`.** New builds produce `team_c.tool_artifact/2`. Existing `/1` artifacts are not rewritten and still execute. Rebuilding an older approval produces a new artifact record with a new hash, so sandbox tests and publications of the old artifact do not count for the new one.
- **`LIVE_WAIT_SECONDS`.** It moved from `team_c.web` to `team_c.jobs`. Patch `team_c.jobs.LIVE_WAIT_SECONDS`; `team_c.web` no longer has it.
- **Facade method replacement.** Assigning a replacement method on a `Service` instance (for example `service.generate = fake`) changes only calls made through that facade attribute. The services call each other directly, so other workflows (for example tool requests calling generation) do not see the replacement. Patch the method on the service object instead (`service.generation.generate`). Replacing `service.providers`, `service.fetch_transport` or `service.execution_transport` still reaches every service.
- **`Providers.check_ollama_budget`.** It remains callable, but `Providers.call()` no longer goes through it: the Ollama provider checks its own budget when it is attempted. Overriding it on a `Providers` instance does not change calls.
- **Behavior.** The refactor did not change the limits listed in the README "Limitations" section: loopback, single-process development use, shared development reviewer label, stdio-only sandbox MCP server.

## Capability compiler layers

`compiler/` owns semantic evidence, structured policy contracts, input-boundary compilation, readiness and generated guard tests. `services/capability.py` persists owner decisions independently of model output; `persistence/repositories/policies.py` verifies accepted policy hashes. Approval snapshots include the policy hash. The executor enforces trusted principals/context and ordered ownership/tenant checks. See [CAPABILITY_COMPILER.md](CAPABILITY_COMPILER.md) for migration and compatibility details.
