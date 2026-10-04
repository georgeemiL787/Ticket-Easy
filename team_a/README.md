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
| `python -m team_a eval-retrieval [--sweep]` | 50-question benchmark (+ threshold sweep) |
| `python -m team_a eval-guardrails` | 44 guardrail cases; exits non-zero on any failure |
| `python -m team_a export-schemas` | Write JSON Schemas to `contracts/schemas/` |

## HTTP API (for Team C's MCP adapter)

| MCP capability | Endpoint | Returns |
|---|---|---|
| `search_knowledge` | `POST /v1/knowledge/search` | `RetrievalResult` |
| `get_passage` | `GET /v1/knowledge/passages?tenant_id=&citation=` | `Passage` |
| `search_past_tickets` | `POST /v1/knowledge/past-tickets/search` | `PastTicketResult` |
| `check_action` | `POST /v1/policy/check-action` | `PolicyDecision` |
| `classify_risk` | `POST /v1/policy/classify-risk` | `RiskAssessment` |
| `explain_rule` | `GET /v1/policy/rules/{rule_id}/explain?tenant_id=` | `RuleExplanation` |
| review (admin) | `GET /v1/policy/rules`, `POST .../{id}/approve`, `POST .../{id}/reject`, `PATCH .../{id}` | `Rule` |
| after re-ingest | `POST /v1/admin/reload` | clears the cached index |

Errors always return `{"error": {"code", "message", "request_id"}}` with one of: `INVALID_REQUEST` (422), `TENANT_NOT_FOUND` / `NOT_FOUND` (404), `INDEX_NOT_BUILT` (503).

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
| Retrieval recall@5 (45 answerable questions) | **97.8%** (ar 14/14, Arabizi 15/15, en 15/16) |
| No-answer precision / recall (5 unanswerable) | **100% / 100%** |
| Guardrail cases | **44/44**, 0 blocked actions executed |
| Unit tests | 46 passed |

**Demo 1:** the same return-window question asked in English, Egyptian Arabic and Arabizi cites `return_policy@v2#s2`. It ranks first in Arabic and Arabizi, and second in English, behind `refund_policy@v1#s1`, which states the same 14-day limit.

**Demo 2:** a refund on day 20 is denied under `R-REFUND-14D`, citing `refund_policy@v1#s1`, with an Egyptian-Arabic `user_message` (see [contracts/examples/check_action.deny.json](contracts/examples/check_action.deny.json)).

## Data (all fictional)

- `data/corpus/shop_001/`: the fictional brand "Nile Style". It holds a return policy in Markdown (v2 current, v1 superseded), a shipping policy PDF in English, a refund policy DOCX in Arabic, and an FAQ XLSX in both languages.
- `data/tickets/shop_001.jsonl`: 20 past tickets with no personal data.
- `data/rules/shop_001.json`: 12 approved rules, plus 1 `proposed` rule (`R-DEFECT-48H`) that shows an unapproved rule has no effect.
- `data/synonyms/arabizi.json`: reviewed query expansions for Arabizi and dialect words.
- `data/risk/keywords.json`: escalation keywords in Arabic, English and Arabizi.
- `data/benchmark/`: the 50 retrieval questions (16 in Arabizi) and the 44 guardrail cases.

## Known limitations

- The thresholds (`MIN_COSINE=0.52`, `MIN_BM25=2.5`) were tuned on the same 50 questions they are scored on, so real traffic will score lower. Add questions from pilot conversations and re-run `--sweep`.
- Q38 ("a payment on my card I did not make") misses its passage. `classify_risk` still flags it as fraud, so it escalates anyway.
- The keyword risk layer only catches phrasings it knows. The eval caught one missing phrasing ("معملتهاش") during development, so grow the list from real transcripts.
- Rule extraction has not been run against a live model yet, because it needs an `OPENROUTER_API_KEY`. The validation around it is covered by tests.
- The review endpoints have no authentication (PoC, single tenant). Put them behind an admin role before any real deployment.
- OCR, contradictory-document detection and reranking are out of scope (stretch items).
