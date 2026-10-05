# Ticket-Easy · Team B (AI brain, orchestration, human handoff)

## What this service does
Ticket-Easy is a customer-service agent for e-commerce shops. Customers write in English, Egyptian Arabic, mixed Arabic-English, or Arabizi (Arabic in Latin letters and digits, e.g. "3ayez a3raf el order feen"). For every customer message this service decides exactly one outcome: answer, clarify, verify_identity, confirm, execute, refuse, or handoff, and records why in a DecisionTrace.

## The journey of one message
load memory → understand (language, intents, entities, yes/no, wants human, frustration) → risk screen → customer wants a human? → pending confirmation? → missing information? → route by intent kind (knowledge / lookup / action) → handoff if needed → compose reply in the customer's register → save trace and session.

## Non-negotiable rules
1. The LLM proposes, code decides. Identity, ownership, permission, policy check, confirmation and result verification are deterministic code. The LLM may fill structured fields that code validates; it may raise safety flags but never clear them; it never chooses a tool.
2. Never invent. No policy claim without a retrieved passage and its citation. No order fact without a tool result. No "done" without a verified tool result (success + audit id + required output fields + reference id for creates).
3. Fail closed. If the policy check, risk screen or identity check cannot run, no side effect happens and the case is handed off.
4. Fixed gate order for actions: required slots → identity verified → facts loaded from a read tool → ownership → permission gate → risk screen completed this turn → policy check_action → customer confirmation (or human approval) → renewed check_action → execution authorized → call tool → verify result.
5. Writes are never retried automatically. Uncertain write outcomes escalate as unverified_result.
6. A human can never turn a policy "deny" into an execution.
7. Configuration over code: everything shop-specific lives in config/tenants/<tenant>.json, data/lexicon, data/locales and fixtures/<tenant>. Adding a business must not need brain code changes.
8. The brain imports only team_b.ports interfaces and team_b.domain / team_b.contracts models. Implementations live in team_b.adapters; container.py wires them from settings.

## Stand-ins
Team A (policy search, rule checker, risk screen) and Team C (shop actions) are unfinished. Team B builds its own stand-ins in adapters/standins, using the demo shop "Nile Style" (tenant shop_001) in fixtures/shop_001. Never import code from team_a/ or team_c/, and never make a test depend on their services. You may READ team_a/contracts/schemas and team_a/data/corpus as reference for data formats and policy texts.

## Layout
Paths are relative to team_b/ unless noted. "(planned)" marks anything not created yet; remove it when a prompt creates the item, and add new modules here.
```
team_b/
  CLAUDE.md  README.md  pyproject.toml  requirements.txt  requirements-dev.txt  Makefile  .env.example
  src/team_b/
    config.py         Settings.from_env(): all TEAM_B_* / OLLAMA_* / OPENROUTER_* variables (see .env.example)
    container.py      build_container(settings) -> Container; the only place that picks implementations
    observability.py  structlog setup; bind_context() puts tenant/conversation/request/trace ids on every log line
    __main__.py                                                                            (planned)
    contracts/   data formats exchanged with the plugs
                 base (PlugModel ignores extra fields, RequestModel forbids them), errors (UpstreamError),
                 evidence (Passage, RetrievalResult, PastTicketResult, RiskAssessment),
                 policy (CheckActionRequest, PolicyDecision), tools (ToolSpec, ToolCallRequest, ToolResult)
    domain/      the brain own data models (strict mypy)
                 base, decision (Decision, EscalationReason), understanding (Language, Locale, NLUResult),
                 tenant (TenantConfig, TenantRegistry), actions (ActionProposal state machine),
                 session (SessionState), trace (DecisionTrace + invariants), handoff (HandoffPackage,
                 HandoffCase), reply (AgentReply)
    ports/       __init__.py: the Protocols EvidenceProvider, PolicyGate, CapabilityClient, LLMClient,
                 SessionStore, TraceStore, CaseStore, Clock + store errors (strict mypy)
    brain/       language, text, nlu, slots, knowledge, identity, registry, gates, actions,
                 composer, handoff, summarizer, metrics, alerts, orchestrator             (modules planned)
    adapters/    memory_store (in-memory stores, SystemClock, FixedClock)
                 standins/ shop (StandinShop: the fake Nile Style shop with audit log, idempotent replay, policy
                           safety net and failure switches; shop_backend.py = tool behaviour; json_schema.py)
                           evidence, policy_search, safety_screen, rule_checker
                           (placeholders that raise NotImplementedError until their Phase 2 prompt)
                 sqlite_store, migrations/, llm                                            (planned)
                 Phase 6 adds team_a_http and mcp_client                                   (planned)
    api/         app (create_app, GET /health, request-id middleware), errors (error envelope + handlers)
                 chat, inbox, traces, dashboard, auth                                      (planned)
  contracts/schemas/   JSON Schemas of DecisionTrace, HandoffPackage, HandoffCase, AgentReply
                       (generated by scripts/export_schemas.py; a test fails if they drift from the models)
  config/tenants/      one JSON file per business: shop_001.json = demo shop "Nile Style" (11 intents)
  data/lexicon/  data/locales/
  prompts/                                                                                 (planned)
  fixtures/shop_001/   demo shop data: policies (36 passages, incl. superseded return_policy v1), rules (13, one
                       proposed), risk, synonyms, backend (8 customers, 16 orders), tools (11), tickets (10)
  scenarios/shop_001/  scripted test conversations
  scripts/     export_schemas.py
  tests/conftest.py    fixtures: settings, container (stand-ins, memory stores, clock fixed at 2026-09-28),
                       app, client (async HTTP client with lifespan), tenants_dir
  tests/unit/  contracts/  domain/  adapters/  api/  test_config.py  test_container.py  ...
  tests/contract/  fixture validation: every shop_001 fixture parses and cross-references agree
  tests/integration  tests/adversarial                                                     (planned)
  web/chat  web/inbox  web/dashboard (built)                                               (planned)
  eval/                                                                                    (planned)
dashboard/   (repo root, next to team_b/; Phase 4 React app)                               (planned)
```

Safety-critical code (change only with tests): domain/actions.py (state machine, execution_authorized) and the two invariants in domain/trace.py. ActionProposal.state and HandoffCase.status are read-only fields; they change only through transition().

Fixture conventions (shop_001): copied from Team A sources, never loaded from team_a/ at runtime. Today is 2026-09-28; the
demo orders are dated against it (delivered 3/10/14/15/20 days ago, one shipment 4 days late; backend.json demo_guide says
what each order is for). Rules use Team A format: effect when applies_if and all conditions hold, else_effect otherwise,
only status=approved is enforced. Tenant argument_map values are slot:<name>, fact:<name> or const:<value>.

container.shop is the StandinShop; container.inject(container, 'shop', {switch: fail_next|uncertain|no_audit|unpublish|publish|reset, tool, ...}) flips its failure switches (for the scenario runner).

Error format of every API error: {"schema_version": "1.0", "error": {"code", "message", "request_id"}}. Codes: INVALID_REQUEST 422, NOT_FOUND / TENANT_NOT_FOUND 404, INVALID_STATE 409, UPSTREAM_UNAVAILABLE 503, INTERNAL_ERROR 500. Raise ApiError (api/errors.py) from routes; never put request values or internal details in messages.

## How to work in this repo
- Before editing, read the modules you will touch and their tests. Write a short plan, then implement.
- Stay inside the current task. Do not refactor unrelated code or rename public functions unless asked.
- Every behaviour change gets a test. Safety behaviour gets a scenario in scenarios/shop_001.
- Before finishing: `make test` and `make lint` must pass.
- Tests use a fixed date (2026-09-28) through the injectable Clock; never use the real date in brain code.
- Pydantic v2 models, async functions, full type hints, small functions, structlog for logs.
- Redact phone, email, address, card and OTP values in traces and logs.
- Keep this file updated when you add a module or a command.

## Commands
- Install: `pip install -e . -r requirements-dev.txt`
- Settings: environment variables listed in `.env.example` (all optional)
- Tests: `make test` · Lint: `make lint` · Run: `make run` (port 8010; Team C's web app uses 8000, Team A uses 8001)
- Regenerate JSON Schemas after changing DecisionTrace, HandoffPackage, HandoffCase or AgentReply: `make schemas`
