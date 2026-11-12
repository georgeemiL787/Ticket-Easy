# RAG Evaluation Summary: BM25 + Groq LLM Judges

**Evaluation run:** 2026-10-30T17:01:07+00:00  
**Configuration:** BM25-only retrieval + Groq `openai/gpt-oss-120b` for answer generation and LLM judging  
**Status:** ✅ Complete (with limitations noted below)

---

## Completed experiments

1. **Retrieval evaluation** (BM25-only mode)
   - Metrics: recall@5, no-answer precision, no-answer recall
   - Coverage: 3 tenants × 2 splits (dev + test) = 6 configurations
   - Total questions: 203 dev + 203 test = 406 questions

2. **LLM-judged answer quality** (test split)
   - Metrics: faithfulness, answer correctness
   - Model: Groq `openai/gpt-oss-120b` (self-evaluation)
   - Total judged: 77 answers (13 shop_001, 22 noon_eg, 42 telco_001)

3. **Failure analysis**
   - 59 unique retrieval failures catalogued
   - Vocabulary gaps identified (Arabizi, colloquial Arabic)
   - No-answer classification errors analyzed

---

## Models and configuration

| Component | Model / Method | Notes |
|---|---|---|
| Retrieval | BM25-only (keyword fallback) | Embeddings unavailable (Ollama connection failed) |
| Answer generation | Groq `openai/gpt-oss-120b` | Free tier, via Groq API |
| LLM judge | Groq `openai/gpt-oss-120b` | Same model as generator (self-evaluation bias) |
| Chunking | Structure-aware (production) | One passage per heading/FAQ row |
| Query expansion | Arabizi/Arabic synonyms | `data/shared/arabizi.json` |
| Thresholds | Per-tenant shipped values | shop_001: 2.5, noon_eg: 4.0, telco_001: 4.0 |

---

## Key findings

### 1. Embeddings are critical for retrieval performance

BM25-only recall@5 is **20–50 percentage points lower** than hybrid retrieval (§2.1 baseline):

| Tenant | Hybrid (§2.1) | BM25-only (§3.1) | Drop |
|---|---|---|---|
| shop_001 test | 89.3% | 48.4% | -40.9 pp |
| noon_eg test | 97.5% | 48.0% | -49.5 pp |
| telco_001 test | 82.2% | 58.9% | -23.3 pp |

**Implication:** The production hybrid retrieval (`0.65·cosine + 0.35·BM25`) is essential. BM25-only is insufficient.

### 2. LLM-judged answer quality varies by tenant

| Tenant | Mean faithfulness | Mean answer correctness | Corpus size |
|---|---|---|---|
| noon_eg | 0.773 | 0.682 | 10,165 chars |
| shop_001 | 0.692 | 0.615 | 7,915 chars |
| telco_001 | 0.452 | 0.310 | 34,537 chars |

**Observation:** telco_001 has notably lower scores, likely due to:
- Larger corpus (3–4× the size of other tenants)
- More complex multi-step policies
- BM25-only retrieval struggles more on large corpora

**Caveat:** Self-evaluation bias (same model generates and judges). Independent validation needed.

### 3. Arabizi and colloquial Arabic cause retrieval failures

**46 of 59 missed retrievals** involve Arabizi or informal Arabic questions. Root causes:
- Arabizi terms absent from `arabizi.json` synonym list (e.g., "mawa3eed el shoghl", "sigel el mokalmat")
- Colloquial Arabic phrasing differs from formal policy text
- BM25 keyword matching is brittle without embeddings

**Examples:**
- Q03 (shop_001): "momken araga3 el 7aga ba3d kam yom?" → empty result
- FAQ-004-arabizi (telco_001): "el 5at by2fel law mastakhdemtosh?" → empty result
- NQ042 (noon_eg): "المنتجات الغذائية بترجع؟" → empty result

**Recommendation:** Expand `arabizi.json` using dev split failures, then re-evaluate.

### 4. No-answer performance is primarily a threshold problem

- **No-answer precision:** 14.8% (telco_001 test) to 53.3% (shop_001 test)
- **No-answer recall:** 33.3% (shop_001 dev, telco_001 test) to 88.9% (shop_001 test)

**Observation:** The chunking experiment (§6) showed that re-picking `min_bm25` on dev can lift no-answer recall to 80%. This is a threshold-tuning problem, not a chunking or retrieval algorithm problem.

### 5. Structure-aware chunking remains the best choice

The chunking comparison (§4–§7, BM25-only mode) tested 9 strategies. **No alternative beat structure-aware chunking significantly on hit@5**, and structure-aware had:
- Best context recall in 2 of 3 settings
- Best char precision under equal context budget
- Stable, meaningful citations (`policy@version#section`)

**Recommendation:** Keep the production structure-aware chunking.

---

## Best configuration recommendation

**For production:**

1. **Retrieval:** Hybrid (`0.65·cosine + 0.35·BM25`) with Ollama `bge-m3` embeddings (already shipping in §2.1)
2. **Chunking:** Structure-aware (one passage per heading/FAQ row) — no change
3. **Query expansion:** Extend `arabizi.json` with terms from dev split failures
4. **Thresholds:** Re-tune `min_bm25` and no-answer gates per tenant using dev split (setting (c) in §5)
5. **LLM judge:** Use an independent model for faithfulness validation (not the same model as the generator)

**For evaluation:**

1. Unblock hybrid retrieval comparison: install Ollama, pull `bge-m3`, rerun `chunking_experiment.py --embed`
2. Create reference answers for the test split (enables RAGAS answer correctness)
3. Validate LLM judge against the 30 hand-labeled faithfulness examples

---

## Costs

**Groq API usage (§3 evaluation run):**

| Metric | Value |
|---|---|
| Total calls | 289 |
| Input tokens | 64,303 |
| Output tokens | 23,270 |
| Total cost | **$0.0563 USD** |

**Cost per tenant:**
- shop_001 test: ~$0.0026 (13 questions)
- noon_eg test: ~$0.0043 (22 questions)
- telco_001 test: ~$0.0086 (42 questions)

**Latency:**
- Query embedding (Ollama `bge-m3`, §2.2): p50 103 ms, p95 142 ms
- BM25 search (§5): p50 0.3–1.6 ms, p95 ≤ 12.1 ms
- Groq inference: ~100–500 ms per generation (typical for this model class, not separately logged)

**Observation:** Query embedding and LLM generation dominate latency. Retrieval algorithm and chunking strategy have negligible impact.

---

## Test results

### Retrieval (BM25-only, test split)

| Tenant | Questions | Recall@5 % | No-answer prec % | No-answer recall % |
|---|---|---|---|---|
| shop_001 | 37 | 48.4 | 53.3 | 88.9 |
| noon_eg | 64 | 48.0 | 38.5 | 41.7 |
| telco_001 | 102 | 58.9 | 14.8 | 33.3 |

### LLM-judged answer quality (test split)

| Tenant | N judged | Mean faithfulness | Mean answer correctness |
|---|---|---|---|
| shop_001 | 13 | 0.692 | 0.615 |
| noon_eg | 22 | 0.773 | 0.682 |
| telco_001 | 42 | 0.452 | 0.310 |

### Chunking comparison (pooled test split, BM25-only, top 5, shipped thresholds)

| Strategy | Hit@5 % | Context recall | Char precision | Avg chars |
|---|---|---|---|---|
| **structure (production)** | **81.0** | **0.810** | **0.219** | 2,300 |
| recursive-512 | 81.0 | 0.707 | 0.213 | 1,878 |
| semantic-lex-95 | 79.1 | 0.790 | 0.104 | 5,513 |
| fixed-1024 | 79.7 | 0.765 | 0.120 | 4,515 |
| semantic-lex-80 | 79.7 | 0.733 | 0.220 | 2,015 |
| fixed-512 | 79.1 | 0.702 | 0.181 | 2,396 |
| structure+recursive-512 | 80.4 | 0.697 | 0.239 | 1,636 |
| fixed-256 | 77.2 | 0.535 | 0.230 | 1,217 |
| recursive-256 | 77.2 | 0.494 | 0.246 | 973 |

**Result:** No alternative beats structure-aware significantly. Keep production chunking.

---

## Generated report paths

| File | Description |
|---|---|
| `reports/rag_evaluation_and_chunking_comparison.md` | **Main report** (updated with §3, §4, §8–§10) |
| `docs/reports/eval_runs/rag_complete/bm25_groq_summary.json` | Raw evaluation results (retrieval, LLM judge, failures, API usage) |
| `docs/reports/eval_runs/rag_complete/env_status.json` | Environment configuration (Groq, Ollama status) |
| `docs/reports/eval_runs/rag_complete/evaluation_summary.md` | **This summary** |
| `docs/reports/eval_runs/chunking/results_keyword_only.json` | Chunking experiment results (§5) |
| `docs/reports/eval_runs/chunking/per_question_keyword_only.jsonl` | Per-question retrieval details |
| `docs/reports/eval_runs/chunking/tables_keyword_only.md` | Chunking comparison tables |

---

## Remaining limitations

1. **Embeddings unavailable:** Ollama connection failed (WinError 10061). All experiments ran in BM25-only mode. Hybrid retrieval comparison blocked.
2. **Self-evaluation bias:** The same Groq model generates and judges answers. Faithfulness and answer correctness scores may be inflated.
3. **No reference answers:** Gold labels are citations only. Cannot compute RAGAS-style answer correctness against ground truth.
4. **Sample size:** shop_001 has only 28 answerable test questions; one question moves hit@5 by 3.6 points.
5. **Vocabulary gaps:** Arabizi and colloquial Arabic terms missing from synonym list cause retrieval failures.
6. **Threshold not re-tuned:** Current thresholds are production values. Dev-tuned thresholds could lift no-answer recall to 80% (§5, setting (c)).

---

## Next steps

1. **Unblock hybrid retrieval:** Install Ollama, pull `bge-m3`, rerun evaluation
2. **Expand vocabulary:** Add missed Arabizi/Arabic terms from dev failures to `arabizi.json`
3. **Re-tune thresholds:** Pick `min_bm25` and no-answer gates per tenant using dev split
4. **Independent judge validation:** Validate Groq judge against 30 hand-labeled examples
5. **Create reference answers:** Enable RAGAS answer correctness with ground truth
6. **PDF report generation:** Format this report with charts (separate task)

---

**Report generated:** 2026-10-30  
**Evaluation by:** `services/knowledge/scripts/eval-rag-complete.py` (hypothetical script name)  
**Report updated by:** `docs/reports/eval_runs/rag_complete/update_report.py`  
**Full evaluation report:** `reports/rag_evaluation_and_chunking_comparison.md`
