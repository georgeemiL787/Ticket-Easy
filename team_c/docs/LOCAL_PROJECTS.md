# Local Python/FastAPI discovery (legacy)

> **Retired from the active flow.** OpenAPI upload/URL fetch is the discovery source ([OPENAPI_SUBSET.md](OPENAPI_SUBSET.md)). The local-project and code-analysis endpoints and forms return `410 code_discovery_retired`. Existing code inventories remain viewable read-only (inventory, evidence records); generating or analyzing from them returns `409 legacy_code_inventory`. The implementation (`team_c/legacy/code_discovery.py`; `team_c/code_discovery.py` only re-exports it) is kept until a separate cleanup. The setup and verification notes below describe how those legacy records were produced.

Local projects and OpenAPI uploads produce the same capability inventory and use the existing proposal, clarification, version and decision workflow. Approval remains permission to build. Target code is never imported, executed, installed, cloned or modified.

## Setup and UI

The backend currently runs on Windows. `/workspace/target-system` is not available here. The user-supplied clone is at `D:\target-system`. Configure that directory as the allowed root before starting Team C:

```powershell
Set-Location 'D:\nti project\team_c'
uv sync --locked
$env:ALLOWED_PROJECT_DIRECTORY='D:\target-system'
uv run uvicorn team_c.web:create_app --factory --host 127.0.0.1 --port 8000
```

The equivalent persistent setting is `ALLOWED_PROJECT_DIRECTORY=D:\target-system` in `.env`. Local discovery is disabled when this setting is empty. On a Linux backend use an actual accessible allowed root such as `/workspace`, and enter `/workspace/target-system` in the UI. Paths always refer to the backend filesystem.

Create/open a business, choose **Local project**, enter the absolute repository path, and select **Discover local project**. Inspect supported operations, partial-coverage diagnostics and **Inspectable code evidence**. Select **Explain selected code with live model** to add grounded AI explanations. This creates a new immutable source inventory; it preserves the earlier facts snapshot. Then select **Generate proposals with live model**, inspect inputs/dependencies, answer questions, reconcile revisions and make per-proposal decisions. The existing OpenAPI upload option remains available.

## Deterministic facts and limitations

The extractor indexes eligible Python files in deterministic order, resolves imported local symbols without importing their modules, and inspects top-level `FastAPI`/`APIRouter` declarations. It supports literal prefixes and nested `include_router` registrations, static route decorators and `api_route(methods=[...])`, primitive inputs, JSON model bodies, declared response models/statuses, local model inheritance, typed arrays, Optional/nullable annotations, Literal enums, selected Field/Query constraints and declared Depends/Security references. UUID, date and datetime annotations have explicit standard string formats. Simple Annotated dependency aliases are resolved across indexed modules. Internal calls are traced for a finite number of steps and become evidence only.

Every operation has a stable identifier based on business, relative file, function, method and established path. Its evidence records contain a relative file, symbol, inclusive line range, original source SHA-256 and snapshot ID. Evidence identifiers are hashes of those fields. Only supported operations with validated evidence are eligible for proposal binding; existing required-input, response-pointer, dependency-order and schema-compatibility checks still apply. AI call traces must cite selected evidence IDs for statically observed callees; invented or unrelated functions are rejected.

Unregistered routers and dynamic prefixes remain unresolved. App factories, conditional registrations, mount/add_api_route, custom model methods/validators, unsupported types/unions, model aliases, custom response/router classes, serialization options, converters and unresolved dependencies are reported. A model cannot resolve these by guessing a path or schema. Partial inventories can propose from their supported operations; unresolved operations remain visible and cannot be bound. A project with no supported operations cannot generate proposals.

Prefixes may combine literals, module constants, `+` concatenation and f-strings. A prefix that reads a field of a module-level `pydantic_settings.BaseSettings` instance (for example `settings.API_V1_STR`) is identified statically. Its literal code default is recorded but never used, because it is not proof of the deployed value. Such paths resolve only when the owner enters a confirmed, non-secret `NAME=/prefix` value in **Confirmed non-secret route settings** (API field `setting_values`). Secret-like names and non-path values are rejected. Each operation records the value source (`owner_confirmed` or `unconfirmed`), the path template and `runtime_verified: false`. Confirmations are part of the discovery cache key. Settings code and environment files are never executed or read.

Python 3.14 unparenthesized `except A, B:` clauses (PEP 758) are parenthesized in memory, on the same line, when the running 3.13 parser rejects them. Source hashes and line numbers are unchanged and an info diagnostic records each adapted line. Other syntax errors remain `python_parse_error`.

Each operation separately reports route, binding and schema status (`discovery`), static access observations (`access`: database session, authenticated user, role checks, ownership checks and user-dependent branches, always `runtime_verified: false`) and customer-facing suitability (`exposure`). Credential-bearing fields are extracted with `x-sensitive: credential`. Credential-bearing, authentication/password-lifecycle, role-dependent and partly unresolved-access operations are `restricted`. Only fully resolved, unrestricted operations are `supported` for proposal binding, and that still is not approval. `EmailStr` is a string with `format: email`. `Any` responses stay unknown (`schema: null`), so grounding rejects outputs or later-step inputs that depend on them.

Dependencies and declared scopes are observations, not verified authentication, authorization or guest identity. Model explanations and risk claims remain interpretations. Owners must review unresolved requirements.

## Read boundaries, limits and caching

- Absolute project paths must lie within `ALLOWED_PROJECT_DIRECTORY` both lexically and after resolution. Links/junctions in the project path are rejected; indexed symlinks/junctions are excluded. File identities and boundaries are rechecked around bounded reads.
- Hidden files/directories, `.env`, common secrets/credential filenames, `.git`, dependencies, virtual environments, caches, binaries, build/output trees, tests and migrations are excluded. Only `.py` files are indexed. README/documents are not sent to the model in this subset.
- Source snippets come from AST nodes. Comments/docstrings are omitted and string/numeric literals are redacted. Literal route paths, schema enums and supported schema constraints remain extracted facts. Raw project files, default values and environment values are not stored or sent as source snippets. Hashes describe original source bytes; snippets are visibly sanitized evidence, not byte-for-byte copies.
- Defaults: 200 Python files, 3,000 directory entries, 128 KiB/file, 2 MiB total, 50 operations, 8 router levels and at most 200 router expansions, two call-trace steps, 12 selected snippets and 8,000 code-context characters. Individual snippets cap at 3,000 characters and disclose truncation. Exhausted limits and omitted coverage are reported.
- AI analysis performs one configured provider call, with no exploration/retry loop. The existing explicitly configured service-error fallback remains available. The existing total prompt/schema/context budget still applies; over-budget requests fail without truncating the provider prompt.
- SQLite caches deterministic inventories by resolved root, business, indexed content hashes, exclusions/diagnostics and configuration/parser version. New relevant source/configuration versions produce new immutable specifications. AI analysis additionally caches by facts snapshot, prompt/output schema, business description and provider/model settings. Cache reuse still requires accessible paths and selected provider configuration.
- Discovery assumes a locally trusted filesystem that is not deliberately replacing ancestor directories during a scan. It is not a hardened sandbox for concurrent hostile filesystem mutation.

## API and commands

`POST /api/v1/businesses/{id}/local-projects` accepts `{"path":"absolute backend path"}`. `POST /api/v1/specifications/{id}/code-analysis` explains bounded selected code. `GET /api/v1/specifications/{id}/evidence/{evidence_id}` returns an immutable evidence record. Existing inventory/proposal/review APIs are reused.

```powershell
uv run pytest -q
uv run pytest -q tests/test_code_discovery.py
uv run python -m scripts.code_smoke --project 'D:\nti project\team_c\examples\local-project' --allowed-directory 'D:\nti project\team_c\examples' --description-file examples/local-project-business.txt --analyze
```

The fixture contains declarative example code with an intentionally unfinished ownership dependency. It is inspected, never run. The live smoke command uses a dedicated database (`data/code-live.sqlite3` by default). To check your own clone, replace `--project`, `--allowed-directory` and `--description-file` with the real paths and your business description file. Do not run multiple server/smoke processes against the same SQLite file.

## Actual verification — 30 September 2026

The full suite passed: **128 tests**, with one existing Starlette/HTTPX deprecation warning. The local-discovery file contains 17 focused cases. `tests/test_discovery_resolution.py` adds 23 cases for PEP 758 parsing, settings and nested prefixes, dependency aliases and classification, schema handling, exposure, cache keys and the compact model-facing inventory. `tests/test_target_system.py` is an opt-in deterministic check against `D:\target-system` (skipped when absent). Deterministic tests exercise extraction, nested prefixes, dependency aliases, evidence hashes, stable operation IDs, malformed/unsupported patterns, excluded secrets, source boundaries (including a Windows junction), limits, caching, model-reference rejection, and shared proposal/review/restart integration. Provider responses in these tests are labeled substitutes.

The final live fixture check used configured Ollama **qwen3:8b**, reasoning disabled, with parser version `code-2` and validated call-trace evidence IDs. Code-analysis run `0721914a-1f56-4578-b8ec-4ddedb87fea6` validated interpretations for two operations from ten selected snippets (4,425 characters). Proposal run `4fff04b3-f689-4dfd-bcc3-c524a730b47f` passed grounding with no capability gaps. It produced proposal `98838f5f-6a47-449c-9d13-e8248cf36aa6`, correctly mapping lookup `/record_id` into create-request `body.record_id`, with runtime `record_id`/`message`, no business configuration, and an unanswered ownership question. It remains `needs_clarification`; it was not approved. These records are in `data/code-live.sqlite3`.

The model's low-risk wording and abbreviated read/write descriptions need owner review. Grounding is not semantic approval. Indexed source hashes remained unchanged after both live calls. Deterministic tests compare the entire fixture tree before/after discovery and verify no target bytecode or side-effect marker was created.

The clone was subsequently supplied at `D:\target-system`. The earlier `code-2` inventory `e6c92683-2b62-4c0a-99f0-12ba0cd5c611` found 23 route declarations and zero eligible operations, because `deps.py` could not be parsed and `settings.API_V1_STR` was unresolved.

With parser `code-3`, all 22 indexed files parse. Without a confirmation, all 23 routes remain unresolved and none are eligible. With `API_V1_STR=/api/v1` confirmed, 22 paths resolve, 20 operations are fully resolved, 15 are restricted and 8 are eligible (five item operations, `GET /users/me`, `PATCH /users/me` and the health check). Still unresolved: login (credential form inputs), the HTML password-recovery route (`response_class`) and the conditionally registered private router.

Live check (`data/code-live.sqlite3`, facts spec `b8852e64-87d7-4cd2-8568-b732aa0527a8`, `OLLAMA_CONTEXT=40960`): qwen3:8b chose `read_item` then `update_item` with correct runtime bindings. Its outputs used pointer `/data`, which exists only on the list response, so grounding rejected run `346802ec-974f-4d68-b53d-6b5c75594f56` and no proposal was stored. With eight eligible operations, generation exceeds the default 32,768-byte conservative budget, and code analysis exceeds even 40,960. The target repository remained unchanged (`git status --porcelain --ignored` empty). No live cloud-provider check is claimed.
