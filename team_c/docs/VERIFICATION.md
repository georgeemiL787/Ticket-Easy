# Verification record — 27 September 2026

This records actual checks, including failures. Automated substitutes are not evidence that a live model reasons correctly.

## Automated checks

`uv run pytest -q --tb=short`: **75 passed** in 9.74 seconds, one dependency deprecation warning (Starlette TestClient's HTTPX integration). The tests exercise real parsing, validation, SQLite transactions, API handlers, HTML forms and restart lifecycle. AI output is provided by `DeterministicModelSubstitute`; provider protocol/error/fallback tests use HTTPX mock transports. Neither is selectable in normal application configuration. One regression test also revalidates the saved actual 4b proposal against its original source and checks the customer-ID dependency; this performs no new inference.

Coverage includes both example domains, required/local-reference extraction, malformed and unsupported input, security metadata, invented operations, invalid/forward/optional-output mappings, configuration/runtime separation, missing model configuration, provider failures, malformed structured output, explicit fallback in both directions, reconciliation evidence, insufficient/contradictory answers, material versions, independent decisions, idempotency/concurrent submissions, stale review, restart preservation and interrupted runs. A revision that returns identical content fails explicitly. Unsupported OpenAPI version values of the wrong JSON type produce diagnostics rather than crashing.

The Petstore 3.0.4 snapshot is tested unchanged: the binary-only operation is reported as unsupported and blocks generation. The separately documented JSON derivative passes; neither version label was changed. See `OPENAPI_SUBSET.md` and `tests/fixtures/README.md`.

## Real Ollama and manual review

Local environment: Windows, Python 3.13.2 virtual environment, approximately 32 GB RAM, NVIDIA RTX 2070 Max-Q 8 GB. Actual inference used the locally installed models, never a canned substitute. No business API endpoint was called.

The e-commerce walkthrough in `data/team_c.sqlite3` used **qwen3:4b** with reasoning disabled. It reached **approved_to_build** through real generation and reconciliation, with manual browser answer editing and approval:

- Business: `26a4d005-a59b-4e10-9d9f-62c59305f86e` (Manual walkthrough — Small Shop).
- Specification: `423e508e-4a04-4eda-bfd9-8e05e05e5de3`.
- Proposal: `dd2c92e5-8d70-4883-817f-bc9fc4d085ce`, version 3.
- Generation: `c14e17fb-9f28-4b49-9840-05d520a7efca`.
- The answer `yes` did not establish queue configuration; approval remained blocked. Reconciliation `441353cd-9695-48bc-8ef4-8c95270ef309` recorded it as insufficient.
- An explicit correction to queue `support` created a new, unapproved version in reconciliation `64a37c3d-9bec-419f-8c73-a968034f49e4`.
- Reconciliation `109c086f-a84f-461c-933e-4dc37a5406cd` assessed the latest copied evidence and made that version ready for review.
- Manual browser approval was recorded at **10:48:05 UTC**, only to build. The source mapping from order response `/customer_id` to ticket `body.customer_id` was inspected.
- The server process was stopped and restarted. The complete proposal API result, including answers, reconciliations, versions and decision, compared exactly equal before and after restart. The browser showed the persisted approved state.

Business creation/review used the real browser UI. The browser automation's native file chooser timed out, so the fixture upload used the application's multipart API. The HTML upload form itself is covered by an automated multipart/CSRF test; a successful operating-system file-picker interaction was not verified.

## Failures found during live testing

Earlier live attempts produced operation aliases instead of inventory IDs, missing settings, inconsistent configuration/runtime bindings, deleted questions during reconciliation, unnecessary capability gaps, and cosmetic reordering. These were rejected or remained blocked. The implementation was tightened with constrained reference schemas, discriminated source contracts, configuration consistency checks, complete question coverage, and canonical content comparison. Validation was not bypassed and no failed output was replaced with a canned proposal.

For room booking, a 4b proposal mapped the string `/reservation_id` to `body.room_id`. This passes type compatibility but is semantically wrong. Manual inspection caught it; it was not approved. A revision repeated the mapping. The application now explicitly rejects unchanged revisions; natural-language correctness still requires owner review.

Both 4b and 8b reasoning-enabled trials exceeded the configured 240-second timeout. The 8b trial overlapped an earlier slow model request, so it is not a clean benchmark. Defaults now select **qwen3:8b with reasoning disabled**; this is configurable. Two initial 8b trials completed inference in approximately 92–96 seconds but failed grounding because a configuration binding had no declared configuration entry:

- `7614e123-6f55-45e0-b558-86bc93b5ee8e` (room booking).
- `414db69f-b701-4995-acf4-131198015d78` (e-commerce).

Their failures are retained in `data/live-8b.sqlite3`. Inspecting a further real response showed that it declared `team_id` but referenced `/team_id`. The schema now distinguishes plain setting/runtime/context names from response JSON Pointers and checks that distinction in tests. Merely reordering schema fields did not solve this; the rejected runs were retained.

### Subsequent 8b check

Run `12f3146c-d5a8-4bb4-9620-d2fa4146b435` passed structured-output and grounding validation in **90.31 seconds**, with no capability gap. Proposal `7e1a5958-7809-456a-ad58-c4fb00d41206` correctly separated runtime arguments and an unknown `team_id` setting. However, manual inspection found the type-compatible but incorrect `/reservation_id` → `body.room_id` mapping. This was **not approved** and is not counted as semantic success. A per-proposal `request_changes` decision (`0a970b75-f22f-4268-a60c-6fb76205f815`) requested `/room_id` and a clearer description of the record created. This is a live check of review catching a model mistake, not a successful room-booking end-to-end approval.

Real 8b revision run **`689d604b-a9ba-4b18-95ce-83bbcce9a96c`** then corrected the dependency to **s1 response 200 `/room_id` → s2 `body.room_id`**. The returned version 2 passed deterministic grounding, and the exact mapping was checked separately. It remains **needs_clarification**, with `team_id` unset and no inherited decision. The model did not improve `expected_writes` beyond `request_id`, despite the owner instruction, so this still needs editorial review. The revision endpoint reports a new candidate version, never that every natural-language request has been satisfied. No live 8b reconciliation-to-approval claim is made.

The final default is qwen3:8b, reasoning disabled, with no provider fallback. This switch is supported and live-tested, but is not evidence that 8b consistently outperforms 4b. The successful full e-commerce approval remains a 4b result; the live 8b check covers generation, explicit owner changes and a corrected dependent mapping. The server was restarted with the final code and the original approved proposal remained unchanged.

## Unverified and bounded scope

- **OpenRouter live inference was not run:** no key/model was configured. Its structured-output request, primary selection, missing-key behavior, fallback and error handling are covered by mock transport tests only.
- No live cross-provider fallback is claimed. Automated tests simulate both provider directions.
- Model choice does not guarantee semantic quality; schema validators cannot prove arbitrary business meaning or authorization.
- The server is for a single local development process, with a server-configured development reviewer label rather than authenticated ownership. SQLite persistence is verified, not distributed/multiworker deployment.
- Approval is only permission to build later. No generated executable tool, runtime customer identity, business endpoint execution, repair loop, activation, registration or MCP publication was implemented.

All smoke databases contain synthetic example data and local audit results. They are ignored by version control. To reproduce live generation, use the commands in the README; to reproduce decisions and restart behavior, follow its manual walkthrough.
