# NileStay configuration-binding failure

Investigated 27 September 2026. Original run: `19dcb7d5-0470-4ec2-8be4-d89c9491deed`.

## Evidence and diagnosis

The original record retained the business, specification, input hash, successful Ollama response status, and generic grounding failure. It did **not** retain the model response, parsed proposal, or detailed grounding error. Its exact original configuration names cannot be recovered.

After adding sanitized capture, a real **qwen3:8b** replay with the unchanged prompt, schema, saved business description and inventory reproduced the invariant failure:

- Reproduction run: `69720f5b-76ed-45f8-8ed7-0988f5a243ef`.
- Original and reproduction input hash: `7bca6dc3e42688833e78f6bda54ac92df7394fabba4fe32d78a1975071fa70de`.
- Specification ID: `b77ddb90-8678-47a1-951f-ecbc1610a9ad`.
- Specification SHA-256: `f9b5150d442dd7fed4474ab10fe19d6f10ca99091c04eb2e67acdad1af68a20a`.

| Configuration check | Reproduced output |
|---|---|
| Declared | `verification_method`, `allowed_service_request_categories` |
| Referenced by business_configuration bindings | None |
| Unused declarations | Both declarations |
| Undeclared references | None |

The sanitized pre-parse response structure exactly matches the parsed structure. Canonical normalization only sorted the declarations. The actual API mappings were already runtime `booking_reference`, `category`, and `message`, plus s1 response `/reservation_id` into s2 `body.reservation_id`. The model incorrectly put a policy question and an API enum into configuration despite neither being passed as a configured API input.

This is a **model-output contract mistake**, encouraged by incomplete prompt/schema guidance about non-configuration questions. It is not a parser conversion or incorrect set-comparison validator. The validator accepted empty declarations/references before this fix and continues to do so. See `nilestay-reproduction-diagnostics.json` for the retained structural evidence; it is a sanitized projection, not the full raw response.

## Fix

- Source selection comes first: previous-operation outputs remain dependencies; customer arguments remain runtime inputs. Only explicitly needed owner-wide API input settings become business configuration.
- Prompt and schema explicitly permit `configuration: []`. A non-input ownership/design question uses `configuration_key: null` and remains required for review.
- Added a no-configuration contract example without substituting it for model results. Existing fixed-routing-setting examples remain supported.
- API enums remain schema constraints. Service security declarations, including scheme descriptions, are passed as unverified connector metadata rather than model-supplied credentials.
- Grounding still rejects unused declarations, undeclared references, invented inputs and invalid dependencies. It now names both set differences and records each configuration binding's step/target. No declarations are silently removed and no dummy fields are added.
- Local SQLite diagnostics record sanitized response structure/fingerprint, parsed structure, normalized generation structure and named grounding errors. The run API/page exposes these. Values, natural-language text, credentials, reasoning and unknown fields are omitted; collections and identifiers are bounded.

## Verification

`uv run pytest -q --tb=short`: **87 passed**, one existing Starlette/HTTPX deprecation warning. These are deterministic tests, with explicitly mocked model transports where needed. Tests include the captured real replay's structural evidence, but do not perform live inference.

A first live prompt revision (`3b858467-e930-44c9-99e6-c9ef94b2f531`) still produced invalid output: it declared runtime inputs as configuration and used configuration for `reservation_id`. The improved validator rejected it and retained exact named differences. That failed attempt was not repaired or counted as success.

The original failed run and existing approvals are preserved. Debug replays are in `data/nilestay-debug.sqlite3`, copied consistently from SQLite before investigation. The example specification is preserved byte-for-byte as `examples/nilestay.json`; the business description is in `examples/nilestay-business.txt`.

Reproduce a separate new live run with:

```powershell
Set-Location 'D:\nti project\team_c'
uv run python -m scripts.live_smoke --provider ollama --example nilestay
```

This smoke command uses a new business ID in its dedicated smoke database. The investigation replays used the original saved business/specification IDs and verified matching input hashes. No business endpoint or runtime tool was executed.
