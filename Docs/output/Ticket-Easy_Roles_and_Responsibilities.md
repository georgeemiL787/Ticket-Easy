# Ticket-Easy — Team Roles, Responsibilities and Deliverables

Customer service automation platform: a policy-aware AI agent that answers customers in Egyptian Arabic, English or Arabizi, takes approved actions on company systems through auto-generated tools, and hands over to a human with full context when it should not act.

## 0. How this document works

- Five members. Each owns **one agentic component end to end**: design, build, evaluation set, and a demo slice.
- Priorities: **P0** = needed for the proof of concept (PoC). **P1** = needed for the full feature list. **P2** = stretch.
- Ground rule: the system is demo-able end to end at every milestone. Nothing is merged that breaks the last working demo. If the deadline slips, we stop at the last completed milestone and polish the demo, not add features.

---

## 1. Adjustments to the feature list

The original list is a good target. A few items are unrealistic for a first build or are missing. These changes are applied throughout the rest of the document.

| # | As written | Problem | What we build instead |
|---|---|---|---|
| 1 | Voice input | Egyptian-dialect speech recognition is a project on its own and not part of the agentic core. | **P2.** Accept voice notes, transcribe with an off-the-shelf model (e.g. Whisper), fall back to "please type it". Not on the critical path. |
| 2 | Auto-generate tools from "the company's data structure and available systems" | Too open-ended (spreadsheets, arbitrary databases, arbitrary systems). | **P0:** generate tools from a *declared* schema (OpenAPI spec or SQL/JSON schema). **P1:** infer a schema from CSV/Excel. Generated tools are always reviewed and switched on by a human before the agent can use them. |
| 3 | Convert policies into enforced rules | Fully automatic policy-to-rule conversion is unreliable; a wrong rule silently issues refunds. | The LLM extracts **candidate rules** into a fixed rule schema; a human approves them in a review step; a **deterministic validator** enforces them before any tool runs. The agent never enforces policy from its prompt alone. |
| 4 | Actions list: returns, disputes, baggage claims (PIR), vouchers, service requests, CRM updates | Spans four industries; there are no real systems to connect to. | **One primary domain: e-commerce** (orders, returns, vouchers, tickets, customer profile) with mock systems and seeded data. A **second small domain (telecom: account, invoice, plan, ticket)** is added late only to prove plug-and-play. |
| 5 | Customer data, CRM data, ticket history | No CRM available. | Mock CRM and ticket database. Past tickets are indexed and retrievable like documents. |
| 6 | Not in the list | Without these the PoC is not credible to a customer. | Added as **P0**: identity verification before any data action; audit log of every answer and action; a visible decision trace ("why did the agent do this"); an evaluation set covering Arabic, English and Arabizi. |
| 7 | Mandatory escalation categories | Fine as is. | Keep. A classifier plus a per-domain configuration file. Cheap and high impact. |
| 8 | Not in the list | Multi-tenant, Egypt-hosted deployment, real channel integrations (WhatsApp Business API). | Out of scope for the PoC. Single tenant, web chat UI. The architecture keeps a tenant id everywhere so this is not a rewrite later. |

---

## 2. Architecture and shared contracts

```
Customer (web chat, text; voice note P2)
        |
        v
 [1] Agent Core (orchestrator)  ---- DecisionTrace ----> Audit log
        |            |             |
        v            v             v
 [2] Knowledge   [4] Tools     [3] Policy
     MCP server      MCP server    MCP server
   (retrieve,      (generated    (validate action,
    cite)           read/create/  classify risk,
                    update)       explain rule)
                        |
                        v
                  Mock systems: orders, returns, vouchers,
                  tickets, CRM  (e-commerce; telecom later)
        |
        v  (on deny / mandatory category / low confidence / customer asks)
 [5] Handoff builder -> Escalation queue -> Human inbox
```

Every component talks to the others through a **contract** that is frozen in week 1. Each contract has one owner. Everyone mocks the contracts they depend on, so nobody is blocked by anyone else.

| Contract | Owner | Content |
|---|---|---|
| `DecisionTrace` | Member 1 | The agent's five determinations per turn: request, applicable rules, customer info used, chosen action, permission outcome; plus confidence and citations. |
| `RetrievalResult` | Member 2 | List of passages with document id, section, language, text, score; empty list with a reason when nothing is relevant. |
| `Rule` | Member 3 | id, source passage, applies-to (tool or intent), condition, effect (`allow` / `deny` / `require_human`), customer-facing message, approval status. |
| `ToolSpec` | Member 4 | name, description, kind (`read` / `create` / `update`), input JSON schema (required/optional, types, constraints), permission level, risk class, backend binding, enabled flag. |
| `HandoffPackage` / `EscalationEvent` | Member 5 | Summary, transcript, customer and CRM snapshot, retrieved passages, attempted actions and results, escalation reason (from a fixed taxonomy), recommended next steps. |

---

## 3. Roles

Each role lists: mission, P0/P1/P2 tasks, deliverables with a definition of done (DoD), and the demo slice that member owns. Rough time split for every member: **60% agentic build, 15% mocks/UI, 15% evaluation, 10% integration and demo.**

### Member 1 — Agent Core Lead (Orchestrator)

**Mission:** the agent loop that turns a customer message into the five determinations and a safe, grounded reply or a hand-over.

**P0**
- Conversation state: multi-turn sessions per customer, history window, session end.
- Language handling: detect Egyptian Arabic / English / Arabizi; always reply in the customer's language and register; prompt conventions for Egyptian tone.
- Planner: the five determinations (what is requested, which policies apply, which customer info is relevant, which action, does the agent have permission) produced as a **structured `DecisionTrace`**, not hidden in prose.
- MCP client: discover and call the Knowledge, Tools and Policy servers.
- Identity verification step before any data tool is called (configurable rule, e.g. phone number + order id).
- Grounded answer composition: cite passages from `RetrievalResult`; refuse or escalate when retrieval is empty or confidence is low.
- Escalation triggers: policy `deny` or `require_human`, mandatory category, low confidence, customer asks for a human, N unsuccessful turns. Calls Member 5's service with the trace.
- Minimal web chat UI (text). Keep it thin.

**P1:** clarification questions when a tool's required fields are missing; multi-intent messages; frustration detection as an escalation trigger.
**P2:** streaming replies; voice-note pass-through to a transcription step.

**Deliverables**
- Agent service (API) and prompt pack (versioned).
- `DecisionTrace` schema and the audit-log writer for it.
- Web chat UI.
- Conversation evaluation set: 40 scripted conversations (Arabic / English / Arabizi mix) with expected outcomes.

**DoD:** every reply carries a `DecisionTrace`; no tool is ever called without a Policy `allow`; all milestone demo scenarios pass through this service.

**Demo slice:** "A customer writes in Arabizi about a late order; the agent verifies identity, looks it up, answers with a cited delivery policy."

---

### Member 2 — Knowledge and Grounding Engineer (RAG)

**Mission:** every factual sentence the agent says can be traced to a passage in the company's documents or ticket history.

**P0**
- Ingestion: PDF, DOCX, XLSX, Markdown; Arabic and English; heading- and table-aware chunking.
- Multilingual embeddings and a vector store keyed by tenant and document version.
- **Hybrid retrieval** (keyword + vector). Arabizi and dialect hurt pure embedding search; keyword fallback matters.
- Citation: each passage returned with document, section and exact text.
- "Nothing relevant" threshold and reason, so the agent can refuse instead of guessing.
- Ticket-history retrieval over the mock ticket database (`search_past_tickets`).
- Knowledge MCP server: `search_knowledge`, `get_passage`, `search_past_tickets`.
- Seed corpus for the e-commerce domain (return, shipping, voucher, complaint policies; FAQ) in Arabic and English, and later the telecom corpus.

**P1:** OCR for scanned Arabic PDFs; document versioning (new policy replaces old, old stays in the audit trail); contradiction warnings between documents.
**P2:** query rewriting Arabizi to Arabic before retrieval; a reranker.

**Deliverables**
- Ingestion CLI/API.
- Knowledge MCP server.
- Seed corpora for both domains.
- Retrieval evaluation: 50 questions (one third Arabizi) with expected passages; recall@5 report.

**DoD:** target recall@5 agreed in week 1 is met; every answer in the milestone demos is traceable to a passage; unknown questions return empty with a reason.

**Demo slice:** "Same policy question asked three ways (Arabic, English, Arabizi) returns the same cited passage; an off-topic question returns nothing and the agent says so."

---

### Member 3 — Policy and Guardrails Engineer

**Mission:** company policy becomes rules the agent cannot break, and high-risk cases always reach a human.

**P0**
- `Rule` schema (see contracts).
- Extractor: LLM reads policy documents and proposes candidate rules with the source passage attached.
- Review step: approve / edit / reject candidate rules (simple UI or a reviewed JSON file with status). Unapproved rules are never enforced.
- **Deterministic validator:** `check_action(tool_call, customer_context) -> allow | deny(reason, rule, passage) | require_human(reason)`. Evaluates conditions such as dates, amounts, customer status.
- Mandatory escalation classifier: fraud signals, regulator complaints, medical or safety issues, compensation claims, legal threats. Categories and keywords/examples in a per-domain config file.
- Policy MCP server: `check_action`, `classify_risk`, `explain_rule`.

**P1:** rule conflict detection; plain-language owner rules ("never promise delivery dates") injected as soft constraints into the agent prompt; per-tool default risk classes.
**P2:** rule simulation: "what would this rule have done on the last 100 conversations".

**Deliverables**
- Rule schema and extractor.
- Validator library and Policy MCP server.
- Escalation category config for both domains.
- Guardrail evaluation: 40 cases, including adversarial ones (a customer arguing for a refund on day 20, a customer claiming to be someone else, a message mixing a complaint with a threat to go to the regulator).

**DoD:** zero blocked actions executed across the evaluation; every `deny` shows the source passage; every mandatory-category message is escalated regardless of what the agent could do.

**Demo slice:** "Refund requested on day 20 of a 14-day policy: the agent explains the rule, cites it, and escalates with the reason 'policy deny'."

---

### Member 4 — Tool Generation and MCP Engineer

**Mission:** the plug-and-play promise. Give the platform a schema, get validated, permissioned tools the agent can call.

**P0**
- `ToolSpec` schema (see contracts).
- Generator: OpenAPI spec or SQL/JSON schema in, `ToolSpec`s out. Deterministic for types, required fields and constraints; LLM only for names, descriptions and grouping into read/create/update.
- Tools MCP server that loads `ToolSpec`s dynamically and serves them; no code change when a spec is added.
- Permission model: agent role versus tool permission level. Defaults: `read` = automatic; `create` = automatic after validation; `update` = `require_human` unless explicitly whitelisted.
- Input validation before execution (types, required, constraints); clear error back to the agent.
- Audit log of every tool call: who, what, when, arguments, result.
- Enable/disable switch per tool (the human approval step).
- Mock backends for e-commerce: orders, returns, vouchers, tickets, CRM profile (lightweight database plus API) with seeded Egyptian-flavoured data.

**P1:** telecom schema and mock backend (account, invoice, plan, ticket) — the plug-and-play demo; idempotency keys on `create` so a retried call does not open two tickets; CSV/Excel schema inference.
**P2:** one real sandbox connector (e.g. a store or helpdesk sandbox).

**Deliverables**
- Generator CLI.
- Tools MCP server.
- Mock systems and seed data for both domains.
- Audit log store and a simple viewer.
- Tool evaluation: schema in, expected tools out; invalid inputs rejected; permission levels respected.

**DoD:** second domain goes from schema to working tools **in under one hour without code changes**; no tool executes with invalid input; every execution is in the audit log.

**Demo slice:** "Drop in the telecom OpenAPI spec; `get_account_status`, `get_invoice`, `create_ticket` appear, are switched on, and the agent uses them in the next conversation."

---

### Member 5 — Handoff, Escalation and Evaluation Engineer

**Mission:** when the agent stops, the human continues without asking the customer to repeat anything; and the team can prove, with numbers, that the system works.

**P0**
- `HandoffPackage` schema and the **handoff builder agent**: given transcript, `DecisionTrace`, retrieved passages, attempted actions and reason, it writes the summary and recommended next steps in the human agent's language and attaches the customer/CRM snapshot.
- Escalation reason taxonomy (policy deny, require_human, mandatory category, low confidence, customer request, repeated failure).
- Escalation service: queue with states open / claimed / resolved / returned-to-agent.
- Human inbox UI: case list; case view with the package; reply to the customer; approve or reject a `require_human` action; resolve or hand back.
- **End-to-end evaluation harness:** scenario files (customer script, seed data, expected outcome), a runner that drives the agent, deterministic checks (tool called? blocked? escalated? citation present?) plus an LLM-as-judge score for answer quality and language correctness, and a report.
- Demo playbook: the scripted scenarios for each milestone, with seed data reset.

**P1:** human rating of hand-over quality ("did you have everything you needed?"); off-hours message and SLA timer; agent-assist suggested reply in the inbox.
**P2:** notifications (email/WhatsApp) on new escalation; CSAT prompt at conversation end.

**Deliverables**
- Handoff builder and escalation service.
- Human inbox UI.
- Evaluation harness and the evaluation report used in the final presentation.
- Demo playbook.

**DoD:** a reviewer who did not see the conversation can resolve an escalated case from the package alone (blind test, 10 cases); the evaluation report covers all milestone scenarios with pass/fail and quality scores.

**Demo slice:** "The refund-on-day-20 case lands in the inbox with summary, cited rule, actions tried and a recommended next step; the human approves an exception and the customer is told in the same thread."

---

## 4. Shared responsibilities (everyone)

- Own an evaluation set for your component (at least 30 to 50 cases, Arabic / English / Arabizi mix) and keep it green.
- Expose your component through MCP or a documented API, and provide a mock of it so others can work without you.
- Write every answer and action to the shared audit log with the tenant id and conversation id.
- Demo your slice every week; the demo uses the shared seed data and reset script.
- Review one other member's contract changes before they merge.

---

## 5. Milestones and the proof-of-concept safety net

Milestones are expressed as a share of the timeline because the deadline is not fixed yet. Each milestone is a **complete, presentable PoC on its own**.

| Milestone | Target | What works end to end | If we stop here, what we can present |
|---|---|---|---|
| **M0 — Kick-off** | first 10% | Stack chosen, repo laid out (one folder per component), contracts frozen, e-commerce seed data and corpus in place, walking skeleton (chat → agent → canned reply). | Architecture and contracts. |
| **M1 — Grounded answers** | 25% | Customer asks a policy/FAQ question in any of the three languages; agent answers with citation or says it does not know; "talk to a human" creates a hand-over with the transcript. | A grounded, multilingual support agent that never invents policy. |
| **M2 — Actions with guardrails** | 50% | Identity verification; agent calls generated `read` tools (order status) and a `create` tool (open ticket); the policy validator blocks a refund outside 14 days and escalates with a full hand-over package; the inbox shows it and the human can reply. | **The minimum "great PoC":** an agent that acts on data, is stopped by policy, and hands over cleanly. |
| **M3 — Plug-and-play and safety** | 75% | Telecom domain onboarded from schema plus policies in under an hour; mandatory escalation categories live; tool and rule approval switches; audit log viewer; first evaluation report. | The full platform thesis demonstrated across two industries with numbers. |
| **M4 — Hardening and stretch** | 100% | `update` tools with human approval, clarification questions, frustration detection, human rating of hand-overs, voice-note transcription, polished UIs, final evaluation report. | The complete feature list. |

Rules when time runs short:
1. Freeze at the last completed milestone. No new features.
2. Spend the remaining time on the demo playbook, seed data, and the evaluation report.
3. Everyone's P2 items are dropped first, then P1 items, in this order across the team: voice, real connectors, agent-assist, contradiction detection, OCR.

---

## 6. Balance check

| Member | Agentic core they own | Non-agentic support work | Contract owned | Evaluation set |
|---|---|---|---|---|
| 1 Agent Core | Planner, language handling, grounded composition, escalation triggers | Chat UI | `DecisionTrace` | 40 conversations |
| 2 Knowledge | Retrieval, citation, refuse-when-unknown, ticket-history search | Ingestion pipeline, corpora | `RetrievalResult` | 50 questions |
| 3 Policy | Rule extraction, validator, risk classifier | Review step, config files | `Rule` | 40 guardrail cases |
| 4 Tools | Tool generation, permission model, MCP serving | Mock systems, audit log | `ToolSpec` | Schema/tool cases |
| 5 Handoff & Eval | Handoff builder agent, LLM-as-judge harness | Inbox UI, escalation queue, demo playbook | `HandoffPackage` | End-to-end scenarios |

Each member has one agentic component, one thin UI or mock, one contract and one evaluation set. The heaviest R&D items (tool generation, policy validation, grounded multilingual answering) sit with Members 4, 3 and 1/2; Member 5 carries the integration and proof burden instead, which peaks in the second half when the others are stabilising.

---

## 7. Main risks per role and the fallback

| Role | Biggest risk | Fallback that still keeps the demo |
|---|---|---|
| 1 Agent Core | Planner is unreliable on mixed-language input | Constrain to a fixed set of intents per domain for the demo; free-form intents in P1 |
| 2 Knowledge | Arabizi questions miss the right passage | Keyword retrieval plus a small hand-written synonym list for the demo corpus |
| 3 Policy | Extracted rules are wrong or incomplete | Ship the e-commerce rules hand-written and approved; extractor shown as "proposes, human approves" |
| 4 Tools | Generator cannot handle a messy schema | Support OpenAPI only for the PoC; hand-write `ToolSpec`s for anything else |
| 5 Handoff & Eval | Harness eats the time meant for the inbox | Inbox first (it is in the M2 demo); judge scoring after M3 |

---

## 8. Week-1 decisions to take together

- Stack: language and framework for services, MCP SDK, LLM provider(s) with a fallback, embedding model, vector store, database for mocks and audit log, UI framework for chat and inbox.
- Confirm e-commerce as the primary domain and telecom as the second.
- Freeze the five contracts above.
- Agree the retrieval and guardrail targets used in the DoDs.
- Set up the shared seed data and the reset script everyone's demo uses.

## Assumptions

- Team of five with comparable skills; no dedicated designer or DevOps.
- No fixed deadline given, so milestones are proportional.
- Single tenant, web chat, mock systems for the PoC; WhatsApp, multi-tenant isolation and Egypt-hosted deployment are deliberately out of scope.
- The name "Ticket-Easy" is used as the project name only; the earlier proposal's product name is unchanged.
