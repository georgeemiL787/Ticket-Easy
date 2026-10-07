# Capability compiler

The workflow now records API facts, semantic interpretations, owner intent, runtime enforcement, tool arguments, test evidence and publication separately. A model response cannot directly create a policy or authorize execution.

## Data flow and modules

1. `services/discovery.py` projects `compiler/semantics.py` over imported inventories and older stored inventories. Original specification bytes and their checksums remain intact.
2. Every request field has a semantic category, evidence state, source references and a reason. Locations, schema annotations, declared security, field documentation and relevant operation documentation drive classification. Nested properties and arrays are inspected. Dependency/code references and business context are supplied to the reasoning layer; their presence is not implementation verification.
3. The model receives compact semantic categories alongside API schemas and business context. It proposes capabilities spanning related operations. A deterministic capability manifest preserves purpose, steps and operation traceability. Discovery alone cannot reliably establish business intent or ownership.
4. The proposal page shows input meanings, unresolved decisions, a proposed interface, effects, risk and readiness. Technical references live in expandable evidence/details. Read/write descriptions use operation summaries with readable fallbacks.
5. Owners accept an `AccessPolicy` through the structured form or API. Policies are append-only records for one proposal version. Acceptance invalidates reconciliation, increments the review revision and records machine-readable security answers. Subsequent prose cannot replace those access decisions. A replacement proposal requires a fresh policy acceptance.
6. `compiler/compile.py` applies explicit input mappings, validates schemas again and compiles accepted checks. `artifacts.py` serializes the minimal interface and ordered execution plan. `executor.py` uses registered connector context and fails closed on absent or incompatible values.
7. `compiler/security_tests.py` automatically generates allowed/forbidden principal, missing authentication, role/permission, cross-owner/cross-tenant, protected-input override and server-derived-input cases. Scope tests also execute a synthetic read/check/write sequence using the real executor and `httpx.MockTransport`; negative cases must suppress the write and output.
8. Readiness explains build and publication gates. Generated test results are bound to the artifact hash. Publication still requires the existing actual sandbox success and applicable cross-owner evidence, current approval, current enforcement and valid MCP schemas. A readiness score is never authorization.

## Evidence meaning

| State | Meaning |
| --- | --- |
| DECLARED | Present in a schema, security declaration or source metadata; not proof of target behavior. |
| INFERRED | A classifier/model interpretation, with a reason. |
| VERIFIED | Verification within a named scope. An accepted policy verifies owner intent only. |
| UNRESOLVED | Information or an executable mapping is missing. |

Policies do not establish verified target authorization. Generated tests record `runtime_guard_simulation`, not business API verification. Sandbox evidence remains separate and production activation remains disabled.

## Structured policy contract

`POST /api/v1/proposals/{id}/versions/{version}/policy` accepts `expected_revision` and `policy`. Principal choices are `public`, `guest`, `end_user`, `resource_owner`, `admin`, and `service`; multiple choices are supported. Roles use an explicitly named trusted context field and any allowed role; permissions require all listed values. Identity labels and context are operator-registered connector data, never LLM arguments.

Each ownership/tenant/account check identifies a requirement, resource description, earlier read step, response status, resource field JSON pointer and trusted identity field. The supported comparison is equality and the failure action is deny. Multiple checks all have to pass. Checks must use required non-nullable response fields and protect later operations through validated selector provenance. An explicitly unrestricted scope needs a recorded reason. Unsupported comparisons, missing reads or unverifiable data flow block compilation.

Input decisions identify a step, API target, semantic category, source reference and reason. Sources are a tool argument, trusted application context, declared business configuration, or omission of an optional field. Required values cannot be silently omitted. Declared protected fields and objects containing protected descendants cannot be delegated to the LLM. Documentation-based inferences can be corrected through explicit owner decisions; a declared protection cannot be overridden this way.

Trusted context fields must be declared in the connector's `context_fields`. The compiler records their API schemas; the executor validates their values before requests. The application business identifier remains authoritative. Primitive custom headers and cookies can come from trusted context/configuration. Authentication and protocol headers cannot be overridden through these mappings. Existing bearer/password-grant authentication support is retained; unsupported schemes remain blocked.

Writes in the structured policy path require explicit financial, irreversible, external-effect and idempotency decisions. Declaring an operation idempotent does not enable retries; writes are still never retried automatically.

## Schema annotations

Optional `x-semantic-role` annotations use one of `business_input`, `identity_context`, `runtime_context`, `server_derived`, `resource_selector`, `configuration`, `sensitive_internal`, or `unknown`. Standard `readOnly`, `writeOnly`, password format and security metadata also inform classification. Unsupported annotations remain unresolved. No business endpoint, resource or parameter name determines a security decision.

## Migration and compatibility

Database migration **6** adds `capability_policies` and `policy_test_runs`, with a version lookup index. The existing migration runner applies it transactionally and records completion. Back up the database before upgrading. No stored proposal, answer, specification or artifact is rewritten by this migration.

Structured-policy artifacts use **`team_c.tool_artifact/3`**. Versions `/1` and `/2` remain readable and retain their established request semantics and reviewed enforcement workflow. They are not retroactively described as having a structured policy or generated-test evidence. The compatibility build path continues to accept existing reviewed mechanisms; adopting the structured policy path requires owner acceptance before approval. All execution/publication paths now recheck semantic input safety, so an old artifact exposing a protected field is blocked until revised and rebuilt. Rebuilding creates a new hash; old hashes are preserved.

Policy hashes participate in reconciliation and approval snapshots. Stored policy content is integrity-checked. Policy changes cannot inherit an old approval, and a closed version cannot receive a replacement policy. Test records apply only to their exact artifact hash.

## Deliberate limits

- Semantic inference is conservative, not a claim to understand every undocumented API. Ambiguous sources and unsupported transformations require a decision or an implementation change.
- Nested protected fields are blocked from caller-controlled objects; the compiler does not invent an arbitrary nested transformation language.
- The current runtime is sandbox-only. An ownership read followed by a write is not an atomic transaction; the target API must enforce authorization and concurrency correctly too.
- Generated guard simulations verify predicates and executor failure behavior. They do not prove that a remote API's identity claims, side effects or authorization are correct.
- Existing legacy artifacts keep their separate reviewed-enforcement lifecycle. They do not silently gain owner policies or test verification.

## Validation

The compiler tests run the same behavior against healthcare, banking, logistics, HR and opaque-name synthetic APIs. They check minimal arguments, trusted input injection, denial before writes, roles/permissions, public/guest/service access, headers/cookies, missing context, unresolved effects and generated negative cases. Lifecycle tests cover policy acceptance, stale revisions, approval binding, integrity failures, UI rendering and the separation between generated tests and publication. The existing migration, artifact, execution, reconciliation, MCP and publication tests remain part of the full suite.

## Correcting protected input sources after approval

The proposal's structured-policy section offers **Review input sources in a new version** for closed versions. This deterministic action copies the operations and saved answers, preserves the old version's history, and opens a new review without an LLM request. Existing accepted policies become form drafts; their acceptance and approval do not carry forward. The new version cannot be approved until its policy is explicitly accepted.

Unresolved identity/runtime inputs receive suggested trusted-context mappings in the form. These suggestions never affect compilation until accepted. Unknown and sensitive fields still require explicit source decisions. Build errors name blocked arguments and link to policy review; the build button stays disabled while input-source blockers remain.

On build, app-managed connectors register context field names from the approved policy automatically. Values still come from explicitly configured test identities. External connector files remain operator-managed. A reviewed legacy delegated-user mechanism can prefill the new policy's authenticated-user choice, but still requires explicit acceptance.
