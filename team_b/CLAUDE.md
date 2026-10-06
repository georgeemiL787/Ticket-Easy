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
    __main__.py       python -m team_b eval-nlu [--mode rules|llm|both] [--save-baseline]
    nlu_eval.py       labelled-message evaluation: accuracy, multi-intent, entities, language, worst misses; baseline in eval/nlu_baseline.json
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
                 composer, handoff, metrics, alerts                                              (modules planned)
                 orchestrator (handle_turn -> pipeline.run_turn, retries once on a stale session; human methods raise
                 NotImplementedError until the handoff step), pipeline (STAGES, run_stage, finish: compose in the customer's
                 locale, validate and store trace + session), turn (Step, TurnContext, Deps: stages share state through them),
                 stages (the 12 stage functions + smalltalk/placeholder/handoff handlers; kinds plug in via Deps.handlers),
                 slots (required_slots, resolve_arguments -> Resolution(arguments, missing, missing_facts, unsourced, needs_identity),
                 next_question: order id, phone, item, reason, amount; sources slot:/fact:/identity:/const:, facts beat the customer)
                 choices (conflicting_pair, ordinal_choice "the second"/el awel/الأول, says_both, mask_order_id, describe_orders):
                 "do you mean A or B?" for tenant conflicting_intents within 0.1 confidence; a verified customer without an order id
                 is shown up to 3 open orders (list_customer_orders, masked, newest first); stage `disambiguate` resolves the answers
                 rewrite (LLMRewriter, TEAM_B_LLM_REWRITE=1: the model may reword ANSWER/CLARIFY/VERIFY_IDENTITY replies only; composer.check_grounded
                 rejects any new number/date/month/id/currency/link/citation/time word and any dropped one -> the template is sent;
                 policy passages are appended after the rewrite, never sent to the model; trace step 'rewrite' + versions.rewrite_prompt)
                 composer (ResponseComposer.t/t_first/passage_block/policy_message; render(key, locale); PLACEHOLDERS = key -> its
                 {placeholders}; texts in data/locales/<locale>/{core,actions,knowledge,handoff}.json, 58 keys, same keys everywhere; keys: ask_<slot>,
                 confirm_action_<capability>, status_<status>, handoff_<reason>), handoff (stub: ESCALATION_DEFAULTS
                 and open_case, the real briefing comes later)
                 Turn stages: load, handed_off_check, understand, risk_screen, human_request, pending_confirmation, disambiguate, merge,
                 frustration, plan, handler, queue, handoff, finish; each recorded in trace.steps (skipped once decided).
                 retention.py (top level): purge_expired / retention_loop, daily from the API lifespan, TEAM_B_RETENTION_DAYS (90);
                 open and claimed cases, and their conversation's session and traces, are never purged
                 llm_nlu (LLMNLU: rules first, then the model; validated: catalog-only intents, pattern entities win, invented details
                 dropped, wants_human/frustration/flags max(rules, model), yes/no only if very short, one repair then rules_fallback;
                 prompt file prompts/nlu_v1.md, version on the trace; phones/emails/cards hidden from the model)
                 nlu (NLU Protocol; RuleBasedNLU.understand(text, session, tenant) -> NLUResult: ordered multi-intent from lexicon
                 keywords, longest keyword wins, negation cancels action intents, timing questions become policy_question; entities
                 order_id/order_ids/phone/amount; yes/no only if the whole message is one; wants_human; frustration low/medium/high)
                 text (normalize, contains_term, find_spans), lexicon (Lexicon, default_lexicon: data/lexicon/default.json), language
                 (detect_language -> (Language|None, confidence); LOCALE_FOR/locale_for: the one en/ar/mixed->ar/arabizi table)
                 summarizer (Template + LLM history summaries, LLM text rejected on invented facts), redaction (redact: phone/email/card/OTP),
                 transcript (transcript_from_traces)
    adapters/    memory_store (in-memory stores, SystemClock, FixedClock)
                 standins/ shop (StandinShop: the fake Nile Style shop with audit log, idempotent replay, policy
                           safety net and failure switches; shop_backend.py = tool behaviour; json_schema.py)
                 standins/ policy_search (PolicySearchStandin: keyword + Arabizi-synonym search, text_search.py = the engine;
                           skips superseded passages, empty_reason no_match, fail_next switch)
                           rule_checker (RuleCheckerStandin, the PolicyGate over fixtures/<tenant>/rules.json: tenant -> identity -> mandatory
                           risk -> approved rules (deny > require_human > allow, missing/malformed fact = MISSING_CONTEXT) -> no-rule defaults
                           -> human approval turns require_human into allow, never a deny; facts only from facts, arguments only when
                           "from": "arguments"; days_since_delivery/days_late derived from dates and as_of; fail_next switch;
                           unreadable rules raise UpstreamError)
                           safety_screen (SafetyScreenStandin.classify_risk: risk.json keywords per category in en/ar/arabizi, normalized whole-word matching, stretched letters collapsed;
                           flagged = a mandatory category matched; fail_next switch; unreadable risk file raises UpstreamError)
                 sqlite_store (SqliteDatabase: one short connection per call, numbered migrations/NNN_*.sql applied on first use;
                           SqliteSessionStore/TraceStore/CaseStore, same behaviour as the memory stores; TEAM_B_STORE=sqlite)
                 llm (OpenAICompatibleLLM: Ollama or OpenRouter over /chat/completions, JSON mode, UpstreamError / InvalidLLMOutput)
                 Phase 6 adds team_a_http and mcp_client                                   (planned)
    api/         app (create_app, GET /health, request-id middleware, /chat page), errors (error envelope + handlers, 429 RATE_LIMITED),
                 chat (POST /v1/conversations/{id}/messages, GET .../{id}, .../outbox, .../events SSE, GET /v1/tenants/{t}/welcome),
                 traces (GET /v1/traces/{id}, GET .../{id}/traces), ratelimit (per-conversation sliding window, TEAM_B_RATE_LIMIT_PER_MIN)
                 inbox, dashboard, auth                                                    (planned)
    events.py    EventHub: in-process live delivery of human replies to open chat pages (Orchestrator.push_to_customer)
  contracts/schemas/   JSON Schemas of DecisionTrace, HandoffPackage, HandoffCase, AgentReply
                       (generated by scripts/export_schemas.py; a test fails if they drift from the models)
  config/tenants/      one JSON file per business: shop_001.json = demo shop "Nile Style" (11 intents)
  data/lexicon/default.json (arabizi_tokens, per-intent keywords x 4 styles, want/question/timing markers, yes/no, filler, human, negation, frustration)  data/locales/
  prompts/nlu_v1.md, rewrite_v1.md   the AI prompts (file name = version recorded on the trace)
  fixtures/shop_001/   demo shop data: policies (36 passages, incl. superseded return_policy v1), rules (13, one
                       proposed), risk, synonyms, backend (8 customers, 16 orders), tools (11), tickets (10)
  scenarios/shop_001/  scripted test conversations, one JSON file each (S00, S08, S40, S43 active; the rest pending until the brain exists; S41 and S42 cover disambiguation)
  scripts/     export_schemas.py, scenario_report.py (table of every scenario + counts; exit 1 if any fails)
  tests/conftest.py    fixtures: settings, container (stand-ins, memory stores, clock fixed at 2026-09-28),
                       app, client (async HTTP client with lifespan), tenants_dir
  tests/unit/  contracts/  domain/  adapters/  api/  test_config.py  test_container.py  ...
  tests/contract/  fixture validation: every shop_001 fixture parses and cross-references agree
  tests/integration/  scenario runner: scenario_format (strict models), scenario_runner, test_scenarios (format docs at top), test_coverage
  tests/fakes.py      FakeLLM (scripted responses)
  tests/support.py    make_settings(): follows TEAM_B_STORE; store tests in tests/unit/adapters run on memory and sqlite
  tests/adversarial                                                                        (planned)
  web/chat/    the browser chat (index.html, chat.js, style.css): /chat?tenant_id=shop_001
  web/inbox  web/dashboard                                                                 (planned)
  eval/nlu_labelled.jsonl (151 hand-labelled messages), nlu_baseline.json (recorded intent accuracy; a test fails if it drops 2 points)
dashboard/   (repo root, next to team_b/; Phase 4 React app)                               (planned)
```

Safety-critical code (change only with tests): domain/actions.py (state machine, execution_authorized) and the two invariants in domain/trace.py. ActionProposal.state and HandoffCase.status are read-only fields; they change only through transition().

Fixture conventions (shop_001): copied from Team A sources, never loaded from team_a/ at runtime. Today is 2026-09-28; the
demo orders are dated against it (delivered 3/10/14/15/20 days ago, one shipment 4 days late; backend.json demo_guide says
what each order is for). Rules use Team A format: effect when applies_if and all conditions hold, else_effect otherwise,
only status=approved is enforced. Tenant argument_map values are slot:<name>, fact:<name> or const:<value>.

container.policy_search is the PolicySearchStandin (switches: fail_next with operation, reset). container.shop is the StandinShop; container.inject(container, 'shop', {switch: fail_next|uncertain|no_audit|unpublish|publish|reset, tool, ...}) flips its failure switches (for the scenario runner; plugs: shop, policy_search, rule_checker, safety_screen). container.rule_checker is the RuleCheckerStandin behind container.policy.

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
- AI model (optional): TEAM_B_LLM=ollama|openrouter, see .env.example; `python -m team_b eval-nlu --mode llm|both`
- Run the suite on SQLite: `TEAM_B_STORE=sqlite make test` (a temp database per test)
- Scenarios: `make scenarios` (pending ones are skipped with their reason) · Tests: `make test` · Lint: `make lint` · Run: `make run` (port 8010; Team C's web app uses 8000, Team A uses 8001)
- Regenerate JSON Schemas after changing DecisionTrace, HandoffPackage, HandoffCase or AgentReply: `make schemas`

## Ownership (two-person team)
Track A (the agent that acts) owns: brain/identity.py, brain/lookup.py, brain/actions.py, brain/registry.py, brain/gates.py,
brain/alerts.py, adapters/standins/shop.py, rule_checker.py, safety_screen.py, api/alerts.py, api/auth.py,
data/locales/*/actions.json, tests/adversarial/, loadtest/, docker files, and tests named test_a_*.py.
Track B owns: brain/knowledge.py, brain/handoff.py, brain/summarizer.py, brain/metrics.py, adapters/standins/policy_search.py,
adapters/*_store.py, adapters/migrations/ (except files numbered for A), api/inbox.py, api/dashboard.py, web/inbox/, dashboard/,
eval/, .github/workflows/, data/locales/*/knowledge.json and handoff.json, and tests named test_b_*.py.
Shared: brain/orchestrator.py, domain/, contracts/, ports/, container.py, config.py, Makefile, CLAUDE.md,
data/locales/*/core.json, tests/integration/test_scenarios.py.

Rules:
- Never change a stub's name, inputs or return type: the other track depends on it.
- Edit shared files only by adding small pieces, and mention them in the commit message so the other person can spot them.
- A scenario is activated only by its owner (the "owner" field: A, B, or sync = both), or by a sync prompt for "sync" ones.
- If a task truly needs the other track's file, make the smallest change and list it in the report.

Stubs (signatures are fixed; the orchestrator routes by intent kind through them):
- brain/knowledge.py (B): `answer(ctx) -> Step`, `quote_for(ctx, query) -> list[Passage]`. Kind "knowledge".
- brain/identity.py (A): `ensure_verified(ctx) -> Step | None`.
- brain/lookup.py (A): `answer(ctx, intent_spec) -> Step`. Kind "lookup".
- brain/actions.py (A): `handle(ctx, intent_spec) -> Step`, `on_confirmation(ctx, nlu) -> Step`. Kind "action"; a yes to a pending action.
- brain/handoff.py (B): `open_case(ctx, reason, detail, pending_approval=None) -> case_id`; called by the handoff stage for every escalation.
- Orchestrator human methods: `human_reply(case_id, agent, text)`, `human_decide(case_id, agent, approve, note=None)` (A, raises
  NotImplementedError until built), `return_to_agent(case_id, agent, note=None)`, `resolve(case_id, agent, note=None)`,
  `claim(case_id, agent)`, `release(case_id, agent)`. The case is found by id across the configured tenants.
- domain/alerts.py: the `Alert` model (A fills the engine).
Reply texts live in data/locales/<locale>/{core,actions,knowledge,handoff}.json; the composer merges them and rejects a key defined twice.
Migration numbers: 002 summary tables (B), 003 alerts (A), 004 users (A), 005+ ask first (see adapters/migrations/README.md).
