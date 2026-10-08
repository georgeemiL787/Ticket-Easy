# Ticket-Easy · Team A: AI Safety + RAG

Team A answers two questions for the agent:

1. **What does the company's policy say?** (Knowledge & RAG): cited passages, or an explicit "no evidence".
2. **Is this action allowed?** (Safety & Guardrails): a deterministic `allow` / `deny` / `require_human` decision with the rule and citation behind it.

Team A never talks to the customer (Team B) and never executes tools (Team C).

Everything is free and runs locally: embeddings come from Ollama (`bge-m3`). The only LLM use is OpenRouter `:free` models for *proposing* rules and a second opinion on risk. Nothing that controls execution depends on an LLM.

## Setup

```powershell
cd team_a
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
ollama pull bge-m3
copy .env.example .env          # add OPENROUTER_API_KEY (free) if you want rule extraction / LLM risk
$env:PYTHONPATH = "src"
python scripts\make_seed_docs.py          # regenerates the PDF/DOCX/XLSX seed documents
python -m team_a ingest --tenant shop_001 # builds var/index/shop_001 (add --no-embeddings to skip Ollama)
python -m pytest                          # 220 tests, no Ollama or API key needed
uvicorn team_a.service:app --app-dir src --port 8001
```

## CLI

| Command | What it does |
|---|---|
| `python -m team_a ingest --tenant shop_001` | Parse the corpus in `data/corpus/<tenant>/manifest.json`, embed, and index it. Reproducible; source hashes go to `meta.json`. |
| `python -m team_a search "momken araga3 el 7aga?"` | Hybrid retrieval with citations |
| `python -m team_a tickets "el order et2akhar"` | Past-ticket search |
| `python -m team_a risk "عايز تعويض" [--no-llm]` | Mandatory-escalation classification |
| `python -m team_a check request.json` | Run `check_action` on a request body |
| `python -m team_a explain R-REFUND-14D` | Explain a rule |
| `python -m team_a rules list [--status proposed]` | Review queue |
| `python -m team_a rules extract [--document return_policy]` | LLM proposes candidate rules (stored as `proposed`) |
| `python -m team_a rules approve <id> --reviewer <name>` / `reject` / `edit <id> changes.json` | Review workflow; any edit sends a rule back to `proposed`. `approve` refuses a rule that would break a guardrail case |
| `python -m team_a eval-retrieval [--split dev\|test] [--sweep]` | 70-question benchmark split into dev (33) and held-out test (37); `--sweep` runs on dev only |
| `python -m team_a eval-guardrails` | 44 guardrail cases; exits non-zero on any failure |
| `python -m team_a db build [--tenant X] [--reset] [--no-embeddings]` / `db stats` / `db query "<sql>"` | Build, count, or query (read-only) the SQLite data layer; see below |
| `python -m team_a eval-scenarios [--tenant noon_eg]` | Retrieval smoke test on a tenant's scenarios, read from the database |
| `python -m team_a export-schemas` | Write JSON Schemas to `contracts/schemas/` |

## HTTP API (for Team C's MCP adapter)

| MCP capability | Endpoint | Returns | Auth |
|---|---|---|---|
| `search_knowledge` | `POST /v1/knowledge/search` | `RetrievalResult` | none |
| `get_passage` | `GET /v1/knowledge/passages?tenant_id=&citation=` | `Passage` | none |
| `search_past_tickets` | `POST /v1/knowledge/past-tickets/search` | `PastTicketResult` | none |
| `check_action` | `POST /v1/policy/check-action` | `PolicyDecision` | none |
| `classify_risk` | `POST /v1/policy/classify-risk` | `RiskAssessment` | none |
| `explain_rule` | `GET /v1/policy/rules/{rule_id}/explain?tenant_id=` | `RuleExplanation` | none |
| review (admin) | `GET /v1/policy/rules`, `POST .../{id}/approve`, `POST .../{id}/reject`, `PATCH .../{id}` | `Rule` | `X-Admin-Key` |
| after re-ingest | `POST /v1/admin/reload` | clears the cached index | `X-Admin-Key` |
| data lookups (read-only) | `GET /v1/data/customers/{id}`, `.../customers/{id}/orders`, `/v1/data/orders/{id}`, `.../orders/{id}/items/{item}/facts`, `/v1/data/returns/{id}` (all `?tenant_id=`) | JSON record | `X-Admin-Key` |
| store a precedent (Team B, on `resolved`) | `POST /v1/knowledge/resolutions` | `ResolutionWriteResult` (`stored: false` + reasons when refused) | `X-Admin-Key` |
| precedents for a human reviewer | `POST /v1/knowledge/resolutions/search` | `ResolutionSearchResult` (advisory only) | `X-Admin-Key` |

Endpoints marked `X-Admin-Key` require that header to equal `ADMIN_API_KEY` from `.env`. If `ADMIN_API_KEY` is unset, they reject every request.

Errors always return `{"error": {"code", "message", "request_id"}}` with one of: `INVALID_REQUEST` (422), `UNAUTHORIZED` (401), `TENANT_NOT_FOUND` / `NOT_FOUND` (404), `INDEX_NOT_BUILT` (503).

Schemas are in [contracts/schemas/](contracts/schemas/), and real recorded success, empty and failure examples for every endpoint are in [contracts/examples/](contracts/examples/).

## Precedents from resolved escalations

Redacted records of how humans resolved escalated cases, so a reviewer handling a similar case sees "N similar cases were resolved this way". They live in a separate corpus, `data/resolutions/<tenant>.jsonl`; code is in [knowledge/resolutions.py](src/team_a/knowledge/resolutions.py).

1. **Mandatory-risk cases are never stored or offered.** A write is refused if `risk_categories` (required) has any entry, if `escalation_reason` is `mandatory_category`, or if the stored text triggers the risk keywords. Search also requires `risk_categories` and returns nothing for such a case.
2. **No personal data is stored.** Only category, redacted summary, resolution, cited rule and tags are kept. `is_safe_to_persist()` refuses (never scrubs) text with an email, phone, order or reference id, payment details, address, name or transcript text. Its reasons name the kind of data, never the value.
3. **Precedents are advisory.** Results carry `advisory: true`. `check_action` never reads them, so a precedent cannot authorize or execute an action.

**Team B integration.** The handoff code isn't in this repo yet; this is built against the project plan's `HandoffPackage`. When a case moves to `resolved`, POST:
- `category`, `redacted_summary` and `resolution`, with no names, order numbers or quotes
- `cited_rule_id` (if any) and `escalation_reason`
- `risk_categories` from the case's `classify_risk`; send `[]` only if it really was empty
- optionally `redaction_check`: the package's known names, phones, emails, addresses, order ids and messages. They're compared against the text, then discarded.

`stored: false` is a normal outcome, not an error. Retries are idempotent on `request_id`. In the inbox, search with the open case's summary and `risk_categories`, and show results as context only.

## How `check_action` decides

The checks run in the order below, and the first one that decides stops the evaluation. It runs no LLM.

1. The record belongs to another tenant → `deny TENANT_MISMATCH`
2. The tool touches personal data or has side effects, and the customer is not verified → `deny IDENTITY_REQUIRED`
3. The tool has side effects and `risk_categories` contains a mandatory-escalation category → `require_human MANDATORY_RISK`
4. Approved, effective rules for the action (skipping rules whose `applies_if` is false):
   - The most restrictive outcome wins: `deny` beats `require_human`, which beats `allow`.
   - A missing or malformed fact fails closed with `deny MISSING_CONTEXT`, unless another rule already denies definitively.
5. No rule applies:
   - read-only → `allow`
   - high-risk tool → `require_human HIGH_RISK_DEFAULT`
   - any other side effect → `require_human NO_RULE_SIDE_EFFECT`

Rule conditions read **`facts`** (verified backend data) by default. `arguments` (values the agent proposes, such as a refund amount) are read only when a condition says `"from": "arguments"`. An argument can never stand in for a fact: guardrail case G25 covers this.

### Facts Team C's backend must send

| Action | Required facts / arguments |
|---|---|
| `create_return` | `order_status`, `delivered_at`, `product_category`, `is_clearance`, `item_condition` |
| `create_exchange` | `delivered_at` |
| `create_refund` | `order_status`, `delivered_at` (if delivered/returned), argument `amount` |
| `update_delivery_address`, `cancel_order` | `order_status` |
| `apply_voucher` | `expected_delivery_date`, `delivered_at`, argument `amount` |

`days_since_delivery` and `days_late` are derived from the dates. Full vocabulary: [data/rules/vocabulary.json](data/rules/vocabulary.json).

## Current results (seed corpus, `bge-m3`)

| Benchmark | Result |
|---|---|
| Retrieval recall@5, **held-out test** (28 answerable) | **89.3%** (ar 7/9, Arabizi 10/10, en 8/9) |
| No-answer precision / recall, **held-out test** (9 unanswerable) | **87.5% / 77.8%** |
| Retrieval recall@5, dev sweep (27 answerable) | 92.6% (ar 8/9, Arabizi 9/9, en 8/9) |
| No-answer precision / recall, dev sweep (6 unanswerable) | 100% / 50.0% |
| Guardrail cases | **44/44**, 0 blocked actions executed |
| Unit tests | 220 passed |

Thresholds (`MIN_COSINE=0.58`, `MIN_BM25=2.5`) were chosen on the dev split, so dev numbers are optimistic. The held-out test numbers are the real estimate.

**Demo 1:** the same return-window question asked in English, Egyptian Arabic and Arabizi cites `return_policy@v2#s2`. It ranks first in Arabic and Arabizi, and second in English, behind `refund_policy@v1#s1`, which states the same 14-day limit.

**Demo 2:** a refund on day 20 is denied under `R-REFUND-14D`, citing `refund_policy@v1#s1`, with an Egyptian-Arabic `user_message` (see [contracts/examples/check_action.deny.json](contracts/examples/check_action.deny.json)).

## Data layer (SQLite)

One derived database, `var/db/team_a.sqlite` (gitignored), holding every tenant's structured data and knowledge. Build it with `python -m team_a db build` (add `--no-embeddings` without Ollama; if Ollama is down the build stores that tenant keyword-only and says so). Rebuilding is idempotent.

- **Tenants:** `shop_001` (Nile Style; customers/orders from Team B's fixture, `data/mock/shop_001/backend.json`) and `noon_eg` (noon's real public return policy, with fictional customers/orders/returns/scenarios in `data/mock/noon_eg/`, regenerated by `build_noon_mock_data.py`).
- **Tables:** `tenants`, `customers`, `orders`, `order_items`, `returns`, `policy_documents`, `policy_passages` (+ `passages_fts`, `passage_embeddings`), `past_tickets` (+ `ticket_embeddings`), `rules_mirror`, `resolutions_mirror`, `eval_scenarios`, `eval_scenario_sections`, `benchmark_questions`, `db_meta`, `db_sources`. Schema: [src/team_a/db/schema.sql](src/team_a/db/schema.sql).
- **Repository** ([db/repository.py](src/team_a/db/repository.py)), all tenant-scoped: `get_customer`, `list_orders_for_customer`, `get_order` (with items), `get_return`, `build_check_action_facts`, `search_knowledge`, `get_passage`, `search_past_tickets`. Search uses the same retrieval code, thresholds and result contracts as the file index.
- **Tenant isolation:** `tenant_id` leads every primary key and foreign key, so a row cannot reference another tenant's row.
- **Source of truth stays in files.** `rules_mirror` and `resolutions_mirror` are read-only copies for joins and reporting (writes are rejected); resolutions are re-checked by `is_safe_to_persist` on import. `check_action` never reads the database.
- **as_of:** dates are ISO text. Day counts are computed as of a date you pass; for mock tenants the default is the data's own `as_of` (`noon_eg` 2026-10-08, `shop_001` 2026-09-28), never the wall clock.

## Data (all fictional)

- `data/corpus/shop_001/`: the fictional brand "Nile Style". It holds a return policy in Markdown (v2 current, v1 superseded), a shipping policy PDF in English, a refund policy DOCX in Arabic, and an FAQ XLSX in both languages.
- `data/tickets/shop_001.jsonl`: 20 past tickets with no personal data.
- `data/rules/shop_001.json`: 12 approved rules. Also 21 `proposed` rules, unapproved: `R-DEFECT-48H`, which shows an unapproved rule has no effect, and 20 from the 2026-10-07 extraction run.
- `data/resolutions/shop_001.jsonl`: 7 seed precedents (redacted, no mandatory-risk cases), each citing an approved rule.
- `data/synonyms/arabizi.json`: reviewed query expansions for Arabizi and dialect words.
- `data/risk/keywords.json`: escalation keywords in Arabic, English and Arabizi.
- `data/benchmark/`: 70 retrieval questions split into dev and held-out test, and the 44 guardrail cases.
- `data/corpus/noon_eg/`, `data/mock/`: see [Data layer](#data-layer-sqlite). The noon policy text is a structured rewrite of noon's public page; all people, phones, emails and orders are fictional.

## Known limitations

- The held-out test set is small (37 questions, 9 unanswerable; one question moves recall about 3.6 points), so treat retrieval numbers as rough. When adding questions, keep the stratified split, sweep on dev only and score test once.
- Unanswerable shop questions the corpus doesn't cover, such as instalment plans, are the weakest retrieval area.
- Matching English or Arabizi queries to Arabic text by keyword depends on the reviewed list in `data/synonyms/arabizi.json`, and there's no Arabic suffix stemming. Wording the list doesn't cover can miss.
- Risk keywords handle spelling variants, but miss new vocabulary (e.g. "someone changed the phone number on my account") and dropped Arabizi vowels. Substring matching over-flags ("court shoes" escalates as legal), which fails safe.
- The `proposed` queue holds 20 LLM-extracted rules. The approval gate only catches rules that break a guardrail case, so review each one before approving.
- Admin endpoints share one `X-Admin-Key`, with no per-user accounts, and `reviewer` is self-reported. Replace with real auth before any real deployment.
- Precedent redaction is pattern-based: a bare first name can get through unless Team B sends `redaction_check`. It also over-refuses, e.g. summaries mentioning "compensation" or a 5-digit amount.
- Precedent search is only as safe as the `risk_categories` the caller sends; the keyword re-scan of the query is a backstop that misses phrasings.
- Precedent writes append to a JSONL file without locking, so run a single instance only. If Ollama is down at write time, precedents go keyword-only, which finds nothing until a handful exist.
- OCR, contradictory-document detection and reranking are out of scope.
