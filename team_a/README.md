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
python -m pytest                          # 46 tests, no Ollama or API key needed
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
| `python -m team_a rules approve <id> --reviewer <name>` / `reject` / `edit <id> changes.json` | Review workflow; any edit sends a rule back to `proposed` |
| `python -m team_a eval-retrieval [--split dev\|test] [--sweep]` | 70-question benchmark split into dev (33) and held-out test (37); `--sweep` runs on dev only |
| `python -m team_a eval-guardrails` | 44 guardrail cases; exits non-zero on any failure |
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

Endpoints marked `X-Admin-Key` require that header to equal `ADMIN_API_KEY` from `.env`. If `ADMIN_API_KEY` is unset, they reject every request.

Errors always return `{"error": {"code", "message", "request_id"}}` with one of: `INVALID_REQUEST` (422), `UNAUTHORIZED` (401), `TENANT_NOT_FOUND` / `NOT_FOUND` (404), `INDEX_NOT_BUILT` (503).

Schemas are in [contracts/schemas/](contracts/schemas/), and real recorded success, empty and failure examples for every endpoint are in [contracts/examples/](contracts/examples/).

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
| Unit tests | 46 passed |

Thresholds (`MIN_COSINE=0.58`, `MIN_BM25=2.5`) were chosen on the dev split, so dev numbers are optimistic; the held-out test numbers are the honest estimate. Both splits are stratified by language and answerability.

**Demo 1:** the same return-window question asked in English, Egyptian Arabic and Arabizi cites `return_policy@v2#s2`. It ranks first in Arabic and Arabizi, and second in English, behind `refund_policy@v1#s1`, which states the same 14-day limit.

**Demo 2:** a refund on day 20 is denied under `R-REFUND-14D`, citing `refund_policy@v1#s1`, with an Egyptian-Arabic `user_message` (see [contracts/examples/check_action.deny.json](contracts/examples/check_action.deny.json)).

## Data (all fictional)

- `data/corpus/shop_001/`: the fictional brand "Nile Style". It holds a return policy in Markdown (v2 current, v1 superseded), a shipping policy PDF in English, a refund policy DOCX in Arabic, and an FAQ XLSX in both languages.
- `data/tickets/shop_001.jsonl`: 20 past tickets with no personal data.
- `data/rules/shop_001.json`: 12 approved rules, plus 1 `proposed` rule (`R-DEFECT-48H`) that shows an unapproved rule has no effect.
- `data/synonyms/arabizi.json`: reviewed query expansions for Arabizi and dialect words.
- `data/risk/keywords.json`: escalation keywords in Arabic, English and Arabizi.
- `data/benchmark/`: 70 retrieval questions split into dev and held-out test, and the 44 guardrail cases.

## Known limitations

- The held-out test set is small: 37 questions, 9 of them unanswerable. One question moves recall by about 3.6 points and the no-answer numbers by about 11, so treat these as rough. Unanswerable shop questions that the corpus doesn't cover, such as instalment plans (valU), are the weakest area. When pilot questions are added, put them in dev and test with the same stratification, re-sweep on dev only, and score test once.
- Cross-lingual BM25 relies on the reviewed synonym list in `data/synonyms/arabizi.json`, and Arabic tokens get no suffix stemming. Q38 ("a payment on my card I did not make") missed its passage until "payment" → `دفع` and "card" → `بطاقته` were added. Expect similar gaps for English or Arabizi wording the list doesn't cover yet.
- The keyword risk layer now handles spelling variation: punctuation, letter elongation, Arabizi digit/letter swaps, and the ما…ش negation (`ماعملتوش` matches the listed `معملتوش`). It still misses new vocabulary, such as "someone changed the phone number on my account" (past ticket T-1019), and dropped Arabizi vowels (`m3maltahash`). Keep growing the list from real transcripts. Matching is also by substring, so "court shoes" escalates as legal. That fails safe, but it is noisy.
- Rule extraction has not been run against a live model yet, because it needs an `OPENROUTER_API_KEY`. The validation around it is covered by tests.
- The review endpoints are protected only by one shared `X-Admin-Key` (PoC, single tenant). There are no per-user accounts or roles, and the `reviewer` field is self-reported. Replace this with real auth before any real deployment.
- OCR, contradictory-document detection and reranking are out of scope (stretch items).
