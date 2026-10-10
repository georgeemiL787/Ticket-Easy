# Ticket-Easy · Team B (AI brain, orchestration, human handoff)

## What this service does
Ticket-Easy is a customer-service agent for e-commerce shops. Customers write in English, Egyptian Arabic, mixed Arabic-English, or Arabizi (Arabic in Latin letters and digits, e.g. "3ayez a3raf el order feen"). For every customer message this service decides exactly one outcome: answer, clarify, verify_identity, confirm, execute, refuse, or handoff, and records why in a DecisionTrace.

## The journey of one message
load memory → understand (language, intents, entities, yes/no, wants human, frustration) → risk screen → customer wants a human? → pending confirmation? → **agent** (agent mode: the orchestrator plans and picks one move at a time, code checks each: look up / search policy / propose action / ask / hand over / reply; with no orchestrator model, or off, or after a fallback: missing information? → route by intent kind (knowledge / lookup / action)) → handoff if needed → compose reply in the customer's register → save trace and session.

## Non-negotiable rules
1. The LLM proposes, code decides. The orchestrator may propose a move from the move menu, including a tool and its arguments; code validates every move and runs the gates of rule 4 before anything happens. The LLM never runs a tool itself, never clears a safety flag and never skips a gate. Identity, ownership, permission, policy check, confirmation and result verification are deterministic code; the LLM may fill structured fields that code validates and may raise safety flags.
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
                 agent_loop (run_agent(ctx) -> Step | raises AgentFallback; agent mode "on" = tenant agent_mode else TEAM_B_AGENT_MODE, and only with a MovePicker in Deps (a model for the orchestrator job);
                 the front stages run first, then ONE "agent" stage replaces disambiguate..queue; the loop asks MovePicker.next_move(view) up to TEAM_B_AGENT_MAX_MOVES (5) times (a plan update counts),
                 TEAM_B_AGENT_TURN_TIMEOUT_S (20) for the whole loop; check_move = the code checks per move (on the menu, same look-up/search at most once, arguments size, action intent exists, question
                 template exists, a reply may only answer/clarify/refuse with an allowed template and no facts/citations nobody verified, hand-over needs a valid EscalationReason); a refused move is told
                 back to the picker (view.refused), two in a row, a picker error, a time-out or the move limit = AgentFallback: the saved plan is put back and the usual middle stages run from their start
                 (trace.fallback_reason). The read moves are real (agent_reads.py): LookUp = tool published + allowed by gates.check_tool + only a READ + arguments fit the tool schema
                 (contracts/json_schema.validate) + every order id was written by the customer or returned by the shop (known_order_ids / session.order_ids_seen) + a customer_id
                 argument is the verified customer's; a personal-data tool with an unverified customer BECOMES the identity step (identity.ensure_verified, nothing is read first); the read is
                 shopcalls.call_read; NOT_FOUND = lookup._not_found, another customer's order = lookup._not_theirs (no data), facts kept on the session only for the customer's own order; results go back
                 to the picker as short redacted summaries (whitelisted fields). SearchPolicy = knowledge.quote_for (ids + short texts back, a down search = handoff dependency_unavailable, no_evidence_count).
                 Reply keys are limited: order_status (built by code from session.facts, needs a verified own order), policy_answer (the retrieved passages quoted, citations must be retrieved),
                 static templates without placeholders; EXECUTE/HANDOFF/CONFIRM decisions, handoff_/confirm_/action_ templates and unverified facts are refused. A Reply that finishes one request while the
                 plan still has an open step does not end the turn (ctx.earlier). ProposeAction still answers "not built yet": the loop cannot write (a write tool in a LookUp is refused).
                 move_picker (LLMMovePicker, job orchestrator, prompts/orchestrator_v1.md: sections customer_message/history/reading/verified/plan/moves_this_turn/tools, tool results in <tool_result>
                 tags as data, answer {plan, move} = MoveProposal in ONE ask, one repair attempt then the loop falls back; container wires it when llm_for("orchestrator") exists; trace versions
                 orchestrator_prompt). tests: ScriptedMovePicker / FunctionPicker (tests/fakes.py), tests/integration/agent_expert.py + test_agent_scenarios.py (the lookup/knowledge scenarios again in
                 agent mode, plus the leak scan), test_agent_safety_property.py (a chaotic orchestrator: nothing gets past the checks) domain/agent.py = the move menu (LookUp, SearchPolicy,
                 ProposeAction, AskCustomer, HandOver, Reply(ReplyPlan)), AgentPlan/PlanStep (saved on session.agent_plan, revision), AgentView, AgentStepRecord/CheckRecord (trace.agent_steps, redacted),
                 trace.agent_mode/agent_plan/fallback_reason; no model = versions.agent "unavailable: no model"; shadow behaves like off until it is built)
                 stages (the 12 stage functions + smalltalk/placeholder/handoff handlers; kinds plug in via Deps.handlers),
                 slots (required_slots, resolve_arguments -> Resolution(arguments, missing, missing_facts, unsourced, needs_identity),
                 next_question: order id, phone, item, reason, amount; sources slot:/fact:/identity:/const:, facts beat the customer)
                 choices (conflicting_pair, ordinal_choice "the second"/el awel/الأول, says_both, mask_order_id, describe_orders):
                 "do you mean A or B?" for tenant conflicting_intents within 0.1 confidence; a verified customer without an order id
                 is shown up to 3 open orders (list_customer_orders, masked, newest first); stage `disambiguate` resolves the answers
                 rewrite (LLMRewriter, TEAM_B_LLM_REWRITE=1, prompts/rewrite_v2.md: the model writes ANSWER/CLARIFY/VERIFY_IDENTITY/CONFIRM/REFUSE/HANDOFF replies from the draft + the customer message + history, never EXECUTE (the shop's word); a CONFIRM must still ask yes/no; only a HANDOFF may promise a colleague (composer.promises_handover); composer.check_grounded
                 rejects any new number/date/month/id/currency/link/citation/time word and any dropped one -> the template is sent;
                 policy passages are appended after the rewrite, never sent to the model; trace step 'rewrite' + versions.rewrite_prompt)
                 try before handing over: the first very frustrated message is answered with the fixed frustration_ack sentence in front and the request goes on
                 (session.frustration_strikes; a second in a row = handoff high_frustration, calm resets); an order number the shop does not know (NOT_FOUND) asks
                 "please check the number" once (order_not_found / order_not_found_choose with the customer's own open orders, masked; session.order_misses), a second
                 miss in a row = ownership_mismatch, while another customer's order stays an immediate handoff with no data; the safety screen, the rule checker and the shop
                 tool list are each asked once more at once when they fail with a retryable error (trace step "retry"; writes are never retried); a deny that closes the request
                 also names the tenant's IntentSpec.alternatives ("I can check other options for you instead: exchange it or ask for a voucher": policy_alternatives)
                 smalltalk (SmallTalkWriter, with TEAM_B_LLM_REWRITE=1: when the whole message is a greeting/check-in/thanks the model answers the person in their style and offers help,
                 prompts/smalltalk_v1.md; problems_with: check_grounded vs the template + shop name, no order/money/policy/promise words, no colleague, <= 300 chars, else the template;
                 trace step 'smalltalk_write' + versions.smalltalk_prompt; "how are you", "عامل ايه", "3amel eh" are greeting words in data/lexicon/default.json)
                 escalation pace: a first non-final policy deny (RULE_BLOCKED) is a REFUSE with the rule's wording minus its "I'll connect you" sentences + an offer (policy_refusal_offer),
                 session.denied_actions remembers it, asking again -> handoff policy_denied; unanswerable policy question: ask_rephrase, then no_answer_offer (ANSWER), the third time in a row
                 (session.no_evidence_count) -> handoff no_evidence; complaint description: freetext ignores clauses that only announce a problem ("I have a problem with my order") and asks
                 knowledge_llm (LLMKnowledgeAssistant, TEAM_B_LLM_KNOWLEDGE=1: search_query = the model writes the search query (Arabizi, follow-ups); answer = the model words the reply from
                 the passages in the customer's register (Step.text), names the passages it used, check_grounded vs the passages else the verbatim quote; answerable=false -> ask_rephrase/no_evidence;
                 prompts/knowledge_query_v1.md, knowledge_answer_v1.md)
                 knowledge (answer: policy search with message + knowledge_query, one normalized retry for mixed/arabizi, 60 s cache per (tenant, query),
                 top passage(s) quoted verbatim; empty -> ask_rephrase once then handoff no_evidence; search down (one retry) ->
                 handoff dependency_unavailable; quote_for, quote_citations via get_passage; trace.knowledge_answer needs evidence + citations)
                 composer (ResponseComposer.t/t_first/passage_block/policy_message; render(key, locale); PLACEHOLDERS = key -> its
                 {placeholders}; texts in data/locales/<locale>/{core,actions,knowledge,handoff}.json, 62 keys, same keys everywhere; keys: ask_<slot>,
                 confirm_action_<capability>, status_<status>, handoff_<reason>)
                 handoff (DEFAULT_PRIORITY + NEXT_STEP en/ar per reason, tenant escalation.priorities/next_steps override; open_case builds
                 the HandoffPackage from session + traces + get_passage quotes + past tickets; mask_phone; incomplete(case) = briefing
                 completeness check, run by the scenario runner on every case)

                 identity (ensure_verified: verify_tool with order id + phone, attempts counted, wrong phone dropped, identity_failed handoff at max_attempts,
                 phone not kept once verified) · lookup (answer: details -> identity -> get_order -> ownership (another customer's or a missing order
                 = handoff ownership_mismatch, no data) -> status from templates + knowledge.quote_for when knowledge_when holds; derive_order_facts)
                 · shopcalls (call_read: one retry in the turn, every attempt recorded; failed_read: lookup_failed, tool_failures counted, repeated_tool_failure
                 handoff at max_tool_failures; output_complete)
                 registry (CapabilityRegistry: list_tools cached per tenant for TEAM_B_CAPABILITY_TTL_S, last good list served stale when the shop is down,
                 None when nothing is known, drop() on TOOL_NOT_PUBLISHED; Deps.registry, Container.registry) · gates (check_tool: disabled, human_only, not_allowed
                 ("*" allows all names), risk_too_high -> handoff unsupported with the reason; configured_capabilities) · actions.handle: gate, identity, then
                 capability_missing / dependency_unavailable (checked after identity), then the details
                 actions.ActionCoordinator (COORDINATOR; propose(ctx, intent_spec) -> ActionProposal with arguments from slots.py, a facts copy and
                 idempotency_key = sha256(tenant|conversation|proposal_id); execute(ctx, proposal, actor): refuses unless APPROVED and execution_authorized(),
                 claims EXECUTED before the one call, sends key + policy_request_id + approval_id + actor, stores the result, never retries; an executed/confirmed
                 proposal returns its stored result; BACKEND_UNAVAILABLE = clear failure, any other UpstreamError = write_may_have_applied; TurnContext.policy and
                 .proposal_ids feed the trace) - SAFETY-CRITICAL, needs a second reviewer
                 actions.handle / on_confirmation (the full gate order: identity -> details -> order read + ownership -> permission gate -> risk screen
                 answered this turn -> check_policy (check_action, one retry) -> allow: confirm_action_<capability> (CONFIRM, pending_action_id) | require_human:
                 proposal AWAITING_HUMAN + handoff approval_required | deny: refuse (rules in tenant escalation.final_deny_rules) or handoff policy_denied, both with
                 the rule's own message (template policy_refusal) and citations; yes: order re-read, arguments compared, check_action again, execute once,
                 verify_result (VERIFIED -> action_done | FAILED -> action_failed, session.write_failures, handoff at max_tool_failures | UNCERTAIN ->
                 handoff unverified_result, proposal stays EXECUTED); the request ends (active_intent cleared) on done, refusal, no or topic change)
                 freetext (reason after because/لان/3shan or a clause after the request, new address "to 5 Nile Corniche", the whole answer to a question that
                 was asked; used by stages.merge) · slots: a const argument is a default that a same-named stated detail replaces (voucher amount)
                 multi (assign_order_ids: with two or more order numbers in one message each request gets the number nearest before its keyword, a request without
                 one keeps the previous request's) -> session.queue_slots[intent] applied when the queued intent runs; queue stage: runs queued requests after a completed
                 one up to Deps.max_queued_runs (TEAM_B_QUEUE_MAX_RUNS, 3); a CONFIRM stops the chain; a new request while a confirmation is pending cancels it and clears the queue
                 stages.handler: a clear request (want-marker, 3+ words) with no intent and no details is REFUSED with out_of_scope (offers a person); stages.merge:
                 a different request after an answered lookup/question replaces it and drops its request slots (order_id, reason, ...), nothing is dropped while awaiting an answer/yes
                 approval (decide_case, run by Orchestrator.human_decide: case re-read in the conversation lock; approve = order re-read, arguments compared, check_action
                 with HumanApproval (require_human -> allow, a deny stays a deny and closes the proposal), execute once with actor=human and approval_id=case id, verify,
                 customer message in the outbox, trace kind human_action, case events approved_and_done/approved_unverified/approval_denied/approval_blocked/decision_failed;
                 reject = proposal CANCELLED + approval_rejected message; a repeat decision is only noted as decision_ignored) - SAFETY-CRITICAL, second reviewer
                 alerts (AlertEngine: every TEAM_B_ALERT_INTERVAL_S (60) per business, reads TraceStore.facts()/query() and CaseStore.list(); rules service_down,
                 action_failing, unverified_result, action_missing, knowledge_gap (rate | same question), slow_replies, escalation_spike, ai_fallback, queue_backlog;
                 thresholds in tenant config "alerts" (domain/tenant.py AlertThresholds, all defaulted); opens ONE alert per (rule, key), resolves when the condition clears;
                 optional TEAM_B_ALERT_WEBHOOK gets opened/resolved JSON; alert_loop is an API lifespan task) · adapters/alert_repository (InMemoryAlertStore, SqliteAlertStore,
                 migration 003_alerts.sql, one open alert per tenant+rule+key) · api/alerts: GET /v1/dashboard/alerts?tenant_id=&status= -> {open_count, alerts}, POST .../{id}/ack
                 api/testadmin (only with TEAM_B_ENABLE_TEST_ADMIN=1: POST /v1/_test/chaos {plug, seconds} (self-healing), POST /v1/_test/heal, GET /v1/_test/state, GET /v1/_test/report; StandinShop.heal/break_everything)
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
                 versions (base_versions: schema, tenant_config_hash, lexicon_hash on every trace; prompt ids from the NLU, rewrite and summary);
                 trace rules: model validators (handoff has a reason, writes are authorized, policy answers are cited) + record_problems()
                 (every step that ran has duration_ms, versions complete) enforced in pipeline.finish; redaction hides phone/email/card/otp/address
                 in entities, tool arguments, customer_message, response_text, decision_reason, errors before storing (the customer still gets the
                 real text); log events turn_start, turn_complete, policy_check, tool_call, handoff_created, dependency_error, all with the 4 ids
                 (observability.turn_context); the scenario runner scans every stored trace for personal values and incomplete records
                 gaps (group_questions: unanswered questions grouped by token-set similarity); demo_seed.py + scripts/seed_dashboard.py
                 (synthetic conversations through the brain on a DriftClock); metrics (MetricsService: one function per metric of docs/metrics.md over TraceStore.facts() = turn_facts/tool_call_facts/policy_facts
                 written with each trace (migration 002, domain/facts.py) + cases; buckets hour/day/week/month, nearest-rank p50/p95, MetricFilters);
                 summarizer (Template + LLM history summaries, LLM text rejected on invented facts);
                 add_ai_summary (handoff summary from prompts/handoff_summary_v1.md: input = the structured package only, output
                 summary_en/summary_customer_language/suggested_next_step, check_grounded against the package else the template stays;
                 package.summary_source ai|template, ai_suggestion labelled 'AI suggestion, not approved'; Deps.llm / Orchestrator(llm=)), redaction (redact: phone/email/card/OTP),
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
                 llm (OpenAICompatibleLLM: Ollama, OpenRouter, Groq... over /chat/completions, JSON mode, UpstreamError / InvalidLLMOutput; TEAM_B_LLM_API_KEY may hold
                           several comma-separated keys: a 429 tries the next key, then waits (Retry-After, max 6 s) and goes round again, 3 rounds)
                 llm_registry (LLMRegistry.llm_for(role) -> LLMClient | None, built by the container; Container.llms; jobs = config.ROLES: reader, orchestrator, knowledge, reviewer, writer,
                 writer_egyptian, summary, judge; wired: LLMNLU=reader, LLMKnowledgeAssistant=knowledge, LLMPolicyGate=reviewer, LLMRewriter+SmallTalkWriter=writer, summarizers and the
                 handoff summary=summary, the eval judge=judge. A job reads TEAM_B_LLM_<SETTING>_<ROLE> and, for what it does not set, the general TEAM_B_LLM_*; with no per-job settings every job
                 is the same client as before. Settings per job (config.RoleSettings / ROLE_ENV_VARS): MODEL, BASE_URL (its own server: it never gets the general key; needs MODEL), API_KEY,
                 FALLBACK_MODEL (RoleLLM: used when the model answers "not found / decommissioned" (MODEL_NOT_FOUND, skipped 10 min) or fails 3 times in a row (skipped 1 min); a bad key is never
                 hidden), REASONING_EFFORT (low|medium|high, sent only when set, dropped for good if the server rejects it), RPM, TPM. TEAM_B_LLM_MAX_WAIT_S (4): llm_limits.RateLimiter is one per
                 (server, model) shared by every job on that model (tightest numbers win), a call waits for its slot at most that long, else RateBudgetExceeded (an UpstreamError, code RATE_BUDGET)
                 is raised at once; tokens are estimated, then replaced by the server's usage count. Callers treat it like any model error (rules or template). observability.ai_events /
                 record_ai_event: pipeline.note_ai_events puts "AI model fell back to the rules or template (job: code)" in trace.errors + versions.ai_fallback (domain/facts maps it to the
                 ai_model service; facts.ai_asked / ai_fell_back feed the ai_fallback alert and metric) and versions.ai_backup_model when a backup model answered)
                 llm_policy_gate (LLMPolicyGate, TEAM_B_LLM_POLICY=1: wraps the PolicyGate; for a write the model reads the retrieved policy passages (prompts/policy_agent_v2.md; v1 kept: v2 asks a person only for a missing fact, an exception a person may grant, or an amount above the chat limit)
                           and picks allow/require_human/deny; the STRICTER of its route and the rule checker's wins, it never loosens; uncited allow/deny -> require_human; model or
                           search failure -> an allowed write becomes require_human POLICY_AGENT_UNAVAILABLE; deny and reads pass through; a human approval still turns its require_human into allow)
                 Phase 6 adds team_a_http and mcp_client                                   (planned)
    api/         capabilities (GET /v1/capabilities?tenant_id=&refresh=: each configured tool available/missing/blocked), app (create_app, GET /health, request-id middleware, /chat page), errors (error envelope + handlers, 429 RATE_LIMITED),
                 chat (POST /v1/conversations/{id}/messages, GET .../{id}, .../outbox, .../events SSE, GET /v1/tenants/{t}/welcome),
                 traces (GET /v1/traces/{id}, GET .../{id}/traces), ratelimit (per-conversation sliding window, TEAM_B_RATE_LIMIT_PER_MIN)
                 inbox (GET /v1/handoff/cases?tenant_id=&status=&reason=&priority=&claimed_by=&limit=&cursor= sorted priority then age;
                 GET .../cases/{id}; POST .../claim|release|reply|decision|resolve|return-to-agent with {agent,...}; only the claimer may act,
                 illegal moves 409 INVALID_STATE; decision = Orchestrator.human_decide, claimer only; POST .../assign {agent=manager, assignee}: only names in tenant
                 `managers` (403 FORBIDDEN otherwise), Orchestrator.assign / HandoffCase.reassign; escalation.sla_minutes per priority, default 15/60/240)
                 dashboard (GET /v1/dashboard/overview|timeseries?metric=&bucket=|conversations?status=&reason=&language=&q=|conversations/{id}|
                 escalations|queue (open+claimed cases with SLA time left, overdue first)|tools|knowledge-gaps|passage|tenants, all with tenant_id, from, to; overview = fast TraceStore.summary for this and the previous period;
                 response models exported as contracts/schemas/Dashboard*.schema.json), auth        (planned)
    events.py    EventHub: in-process live delivery of human replies to open chat pages (Orchestrator.push_to_customer)
  contracts/schemas/   JSON Schemas of DecisionTrace, HandoffPackage, HandoffCase, AgentReply
                       (generated by scripts/export_schemas.py; a test fails if they drift from the models)
  config/tenants/      one JSON file per business: shop_001.json = demo shop "Nile Style" (12 intents)
  data/lexicon/default.json (arabizi_tokens, per-intent keywords x 4 styles, want/question/timing markers, yes/no, filler, human, negation, frustration)  data/locales/
  prompts/nlu_v1.md, rewrite_v2.md, smalltalk_v1.md, orchestrator_v1.md (gets the customer message + history), policy_agent_v1.md + v2.md (v2 in use), knowledge_*_v1.md   the AI prompts (file name = version recorded on the trace)
  fixtures/shop_001/   demo shop data: policies (36 passages, incl. superseded return_policy v1), rules (13, one
                       proposed), risk, synonyms, backend (8 customers, 16 orders), tools (11), tickets (10)
  scenarios/shop_001/  scripted test conversations, one JSON file each (run `make scenarios` to see which are active and which are pending)
  Dockerfile (build from the repo root: node stage builds dashboard/, python:3.12-slim stage, non-root, health check); repo root: docker-compose.yml (profiles light/full, ollama
               qwen3:8b), Makefile (demo, demo-full, down, logs, reset), .env.example (every TEAM_B_* variable; a test keeps it complete), `python -m team_b seed-demo` (the history ends at the service's own now, TEAM_B_FIXED_TODAY if set; plays an outage so the Alerts page has a resolved service_down)
  loadtest/    demo.py (the conversation mix), run.py (50 customers in-process or --url, --chaos PLUG:SECONDS@START, p50/p95 per endpoint and stage, checker verdict),
               checker.py (audit log + traces: no unauthorized/duplicate writes, failures as the table says), check.py, locustfile.py, README.md; make loadtest / make chaos
  scripts/     export_schemas.py, scenario_report.py (table of every scenario + counts; exit 1 if any fails)
  tests/conftest.py    fixtures: settings, container (stand-ins, memory stores, clock fixed at 2026-09-28),
                       app, client (async HTTP client with lifespan), tenants_dir
  tests/unit/  contracts/  domain/  adapters/  api/  test_config.py  test_container.py  ...
  tests/contract/  fixture validation: every shop_001 fixture parses and cross-references agree
                   conformance.py (check_* functions per port: EvidenceProvider, PolicyGate, CapabilityClient; reusable against real services), test_conformance_standins.py runs them on the stand-ins
  tests/integration/  test_failure_matrix.py (one case per failure row: safety screen, rule checker, reads, writes, unpublished tools, frustration, out of scope, identity, ownership), scenario runner: scenario_format (strict models), scenario_runner, test_scenarios (format docs at top), test_coverage
                      test_a_safety_property.py (200 random conversations x 4 seeds, ~6 s each: writes need an allow in the same turn and an earlier confirmation question,
                      success wording needs a verified write, no order data before verification or of another customer, refunds = shop total; plus broken-brain self-tests),
                      test_a_leak_scan.py (no order data in any scenario reply before verification)
  tests/fakes.py      FakeLLM (scripted responses)
  tests/support.py    make_settings(): follows TEAM_B_STORE; store tests in tests/unit/adapters run on memory and sqlite
  tests/adversarial/  prompt injection, fake approvals, amounts, identity and data theft, obfuscated threats, confirmation tricks and floods, hostile AI/shop answers, one conversation per rules.json rule (judged by the shop audit log + reply text)
  ../.github/  workflows ci.yml (lint, tests on memory+sqlite, scenario report, schema drift, dashboard, docker), safety.yml (adversarial + property test, 5 seeds),
               eval.yml (weekly evaluation artifact); CODEOWNERS for the safety-critical files; what to switch on in GitHub: docs/ci.md
  web/chat/    the browser chat (index.html, chat.js, style.css): /chat?tenant_id=shop_001; a "pending" bubble with moving dots and a progress line shows while the reply is awaited
  web/inbox/   the support inbox (index.html, inbox.js, style.css): /inbox; inbox.js exports pure helpers for node tests; no innerHTML
  web/dashboard                                                                            (planned)
  eval/conversations/<style>.jsonl (200 conversations, 50 per style, gold per turn: intents, entities, decision, citations, escalation; written by
  scripts/make_eval_set.py from the policies, not from the brain), eval_baseline.json (what the rules reach; tests/integration/test_b_eval.py holds the
  20-conversation sample to the targets or the baseline); `python -m team_b eval --set eval/conversations [--llm] [--save-baseline]` writes reports/eval_<date>.md/.json
  (src/team_b/eval_conversations.py)
  comparison measures (eval_conversations.py: solved without a person, needless handoffs, missed escalations, wrong or unapproved actions (writes the gold does not expect or sent without a rule check),
  unbacked facts (check_grounded re-run on every sent reply: must be 0), AI calls per message (CountingLLM via build_container(llm_wrapper=)), time per reply p50/p95; `eval --label NAME` saves
  reports/eval_NAME_<date>.json/.md; `python -m team_b eval-compare A.json B.json` (eval_compare.py) shows two runs side by side; eval/baseline_phase0.json = the rules before the agent upgrade)
  AI judge (prompts/judge_v1.md, src/team_b/judge.py): `eval --judge` grades clarity, politeness, register, helpfulness 1-5 with one-line reasons per style (advice only,
  never in pass/fail), writes 10% of judged replies to reports/judge_spotcheck.csv; `python -m team_b judge-agreement <csv>` compares with a person's grades
  eval/nlu_labelled.jsonl (151 hand-labelled messages), nlu_baseline.json (recorded intent accuracy; a test fails if it drops 2 points)
dashboard/   (repo root, next to team_b/) React + Vite + TypeScript + Recharts app: types generated from contracts/schemas/Dashboard*.schema.json
             (npm run types; the build fails on stale types), EN/AR with RTL, filters in the URL; `npm run build` -> team_b/web/dashboard (gitignored),
             served at /dashboard with SPA fallback by api/app.py (mount_dashboard_app). See dashboard/README.md
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
- Demo data for the dashboard: `python scripts/seed_dashboard.py --reset` then `TEAM_B_STORE=sqlite TEAM_B_DB_PATH=var/demo.sqlite3 make run`; slow tests: `python -m pytest -q -m slow` (not in the normal run)
- Scenarios: `make scenarios` (pending ones are skipped with their reason) · Tests: `make test` · Lint: `make lint` · Run: `make run` (port 8010; Team C's web app uses 8000, Team A uses 8001)
- Regenerate JSON Schemas after changing DecisionTrace, HandoffPackage, HandoffCase or AgentReply: `make schemas`

## Stubs and ownership
The work is one build sequence (the agentic upgrade); there are no separate tracks any more. Safety-critical files still need a
careful second look before a change: brain/actions.py, brain/approval.py, domain/actions.py, the two invariants in domain/trace.py,
brain/agent_loop.py (the checks on every move). The `owner` field of a scenario is kept for history only.

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
