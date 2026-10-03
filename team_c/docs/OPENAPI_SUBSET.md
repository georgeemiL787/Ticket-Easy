# OpenAPI discovery subset, parser version 2

OpenAPI is the primary discovery source. Accepted declared versions: **3.0.0–3.0.4** and a documented **3.1.0–3.1.2 subset**, JSON or single-document safe YAML, uploaded or fetched from an explicit URL. The source version is never rewritten. No API operation, external reference, externalDocs URL or server URL is contacted.

## Validation before normalization

1. Parse with duplicate-key rejection and traversal limits.
2. Reject external (`http:`, `file:`, relative) and dangling references at document level: such a document cannot be validated offline.
3. Validate the **original** document with the validator for its declared version (`OpenAPIV30SpecValidator` or `OpenAPIV31SpecValidator`).
4. Only then normalize schemas into the internal representation (an OpenAPI 3.0-style schema subset used by grounding).

When the whole document has validation errors, each operation is re-validated as an unmodified sub-document containing only that operation, its path-item parameters and the components it transitively references. Errors reproduced there block that operation (and every operation that references the same component). Root-level errors (for example a missing `info`) and errors not attributable to any operation block the document.

## Blocking scope

| Problem | Blocks |
|---|---|
| Malformed JSON/YAML, unsupported version or dialect, external/dangling refs, operation limit, root-level validation errors | The document |
| Unsupported schema construct, union or constraint in parameters, request body or a **2xx** response (directly or via a referenced component) | That operation and every operation using the component |
| Non-JSON request body or 2xx response (form, HTML, binary), parameter `content`, undeclared security scheme | That operation |
| Unsupported construct or media in a non-2xx response | Nothing: `error_response_unsupported` warning, response `schema: null`, `schema_status: "unsupported"`; such responses cannot be selected as outputs |
| Referenced Path Item | Operations of that path |

Inventory state fields: `document_valid` (no document-level error), `eligible_operation_count`, and `proposal_generation_ready` (document valid and at least one eligible operation; gates generation). `valid` is a deprecated alias of `proposal_generation_ready`, kept for stored records; grounding checks `document_valid` plus per-step eligibility. Records stored before these fields existed are filled on read.

## Schema normalization (3.1)

Only forms whose semantics the internal representation preserves are normalized; each normalization is recorded in `normalization.notes` with the original pointer.

| 3.1 form | Internal form |
|---|---|
| `type: [T, "null"]` | `type: T`, `nullable: true` |
| `anyOf`/`oneOf`: `[schema, {type: null}]` (siblings such as `title`, `format`, `maxLength` merged when they do not conflict) | schema + `nullable: true` |
| `const: v` | `enum: [v]` |
| numeric `exclusiveMinimum`/`exclusiveMaximum` (without `minimum`/`maximum`) | `minimum`/`maximum` + boolean exclusive flag |
| `$ref` with annotation-only siblings (`title`, `description`, `examples`, …) | resolved schema + annotations |

Requiredness stays in the parent object's `required` list and is never inferred from nullability: a nullable field that is not required is optional *and* nullable; a required non-nullable field is guaranteed.

Explicit diagnostics instead of weakened schemas: other unions (`anyOf`/`oneOf`/`type` arrays with more than one non-null type, `oneOf` whose schema branch also admits null), `allOf`, `not`, `discriminator`, `if/then/else`, `prefixItems`, `patternProperties` and other unknown keywords, `nullable` in a 3.1 document, `$id`/`$anchor`/`$dynamicRef`, `$ref` with constraining siblings, conflicting sibling/branch constraints, null-only or boolean schemas, recursion. 3.0 documents keep the parser-1 keyword allowlist (no composition, no ref siblings).

The original document, each operation's `original` fragment and response `content` are retained. `normalization.source_pointers` maps normalized locations (for example `inputs/body.title`, `responses/200/properties/id`) to original JSON Pointers such as `#/components/schemas/ItemUpdate/properties/title`.

## Per-operation contract

Each operation separates five facts; none implies another.

1. **Technical support** (`supported`, `binding`): bindings and schemas are representable. It is not permission.
2. **Declared authentication** (`declared_auth`): `required`, `optional` (an `{}` alternative), `none_declared` (no `security` anywhere) or `explicitly_none` (`security: []`), with schemes and scopes. Missing security is not evidence of public access.
3. **Authorization** (`authorization`): always `not_declared`; `unresolved_requirements` lists what OpenAPI cannot state (caller roles, scope-to-user mapping, record ownership for path identifiers, whether an undeclared operation is really public).
4. **Exposure** (`exposure.classification`): `restricted`, `requires_clarification` or `technically_unsupported`, with `signals`. Each signal records its basis (`declared` or name/field-name heuristic) and effect. Heuristics are review signals, not verified permissions.
   - Restricting signals: OAuth2 token/refresh/authorization URL equal to the operation path; `format: password`; credential-looking fields (password, secret, token, api key, credential) in inputs or success responses; auth-lifecycle words (login, logout, signup, password, token, oauth, auth…) or privileged words (admin, superuser, internal, private, staff…) in path, operationId or tags.
   - Review-only signals: user/account records, `DELETE`.
5. **Owner approval** (`owner_approval.state`): `not_reviewed` at discovery. Approval applies to proposals (approved to build) and is never runtime authorization.

`proposal_eligible = supported and classification != restricted`. Only eligible operations are offered to the model; grounding rejects steps using ineligible operations or operations outside the selected generation scope. Admin/password operations are never offered automatically. Admin-only operations without such naming (for example a plain `GET /users/`) cannot be identified from OpenAPI and remain `requires_clarification`.

## Generation scope and output pointers

Owners select which eligible operations are sent to the model (default: all). The selection is stored as `generation_scope` and reused for reconciliation, revision and approval checks. Smaller selections fit the local context budget.

The structured-output schema offers output and previous-output pointers per (operation, success status). The model first repeats the referenced step's operation (`operation_id` / `source_operation_id`), then picks a pointer from that operation's response only; a mismatching echo is rejected as `invalid_model_output`, and grounding still validates pointers against the stored inventory.

## URL fetch

`POST /api/v1/businesses/{id}/specifications/fetch` with `{"url": ...}` (or the business page form). Requires `OPENAPI_FETCH_HOSTS` (comma-separated `host:port` allowlist); disabled when empty. One `GET`, no redirects, no credentials/cookies/proxy environment, 2 MiB cap, `OPENAPI_FETCH_TIMEOUT` seconds. Content type decides JSON vs YAML (extension only for generic types). The inventory records `source` (URL, fetch time, content type, SHA-256, bytes) and notes that it reflects the running server at fetch time.

## Unchanged from parser 1

Parameter inheritance/overrides by `(name,in)`; JSON selection with `alternative_media` warnings; `body.<field>` inputs for top-level object bodies (readOnly fields excluded) or a whole `body`; object-field output pointers, no array indexing or transforms; conservative compatibility. Defaults: 2 MiB, 50 operations, depth 60, 50,000 document entries.

## Compatibility evidence

- `tests/fixtures/petstore-original.json` (3.0.4, 19 operations, byte-for-byte snapshot): the binary-only `POST /pet/{petId}/uploadImage` is blocked; the other 18 operations are technically supported; login, logout, user creation and other credential-bearing operations are restricted.
- `petstore-json-subset.json`: derivative without the binary path; all operations technically supported.
- `tests/test_openapi_primary.py` generates a FastAPI 3.1.0 document shaped like the target system (OAuth2 password flow, form login, `anyOf` nullables, HTML response, 422 `ValidationError` union) and checks normalization, scoping, the contract, fetch safety and pointer constraints.
- Observed target (`http://127.0.0.1:8001/api/v1/openapi.json`, 3.1.0, 23 operations): 21 technically supported (form login and HTML recovery blocked), 10 restricted, 13 eligible with clarification.
