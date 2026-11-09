# Weekly report — Team A RAG evaluation

**Week:** 26–31 Oct 2026  
**Owner:** Team A (RAG eval)  
**Branch:** `cursor/deepeval-rag-eval` (pushed; not merged)  
**For:** Sunday review (2 Nov 2026)

In one sentence: we can now score “wrong passage” and “bot made it up,” we know which judge to trust, and we already ran the follow-ups (real embeddings, half-true scoring, telco floor) instead of leaving them as next week.

Full log: [README.md](README.md). Experiments: [FINDINGS.md](FINDINGS.md).

---

## What we delivered

1. **A DeepEval harness next to existing retrieval eval** — `python -m team_a eval-rag`. Does *not* replace recall@5. Adds passage relevancy and reply faithfulness. Gold stays policy section IDs. Judges: local Ollama or Groq only (no OpenRouter).
2. **A 54-row hand-labelled faithfulness exam** (original 30 untouched + 24 new). Wrong number, wrong section, invented extra, half-true. Hand-reviewed; keep all labels.
3. **Judge bake-off** on those 54, plus Arabizi→Arabic script and a RAGAS-prompt fallback.
4. **Hybrid re-run with `bge-m3`** on shop / noon / telco (this was listed as next; it is done).
5. **Tried the two “fixes”** (strict faithfulness, telco confidence floor), measured them, and **did not ship the ones that made held-out numbers worse.**

---

## Numbers to quote

**Judge vs 54 labels** (pass = score ≥ 0.5)

| Judge | Agree | Missed errors | False alarms |
|---|---|---|---|
| **Ollama qwen3:8b** | **48/54 (89%)** | 6 | **0** |
| Ollama 4b | 46/54 | 8 | 0 |
| Groq qwen3.8-27b | 43/54 | 11 | 0 |
| RAGAS prompts + 8b | 47/54 | 5 | **2** |

Quote **48/54**, not the earlier 30/30. Arabizi is the weak style. Live quoted replies scored 91% — that number is inflated; use the 54 labels for invented facts.

**Relevancy — keyword vs hybrid (`bge-m3`)**

| Tenant | section_hit keyword → hybrid | Off-topic hits that still look relevant |
|---|---|---|
| shop_001 | 82% → **89%** | 17% → **0%** |
| noon_eg | 90% → **98%** | 14% → 18% |
| telco_001 | 77% → **82%** | 46% → **44%** |

Shop is the clean win. Telco is not: hybrid finds more of the right policy **and** more off-topic questions (no-answer recall 58% → 33%). Jobs / 5G / satellite TV / travel insurance still retrieve because the brand name sits in every passage.

---

## What we tried and did *not* ship (this is the job)

| Attempt | Result | Decision |
|---|---|---|
| Arabizi → Arabic script before judging | 17/22 → 18/22 | Optional preprocess, not a fix |
| RAGAS as default | Better on Arabizi lies, 2 false alarms on good paraphrases | Fallback only |
| Faithfulness: any unsupported claim = fail | 48/54 → **41/54**. Judge reads “14 days” as “1:4” and fails good replies. Rows it scores **1.00** still slip | Keep ≥ 0.5. Strict score is stored as a diagnostic only |
| Telco `min_top_score=0.50` | Helped **dev** abstention; **test** Arabizi 19/30 → **15/30** | Floor not shipped. Note in the telco manifest |

Shipping the first number that looks stricter would have been worse. We measured on held-out data and kept the rule that actually matches labels.

---

## Decisions (locked unless Sunday overrides)

1. Trusted judge: **local qwen3:8b**. Not Groq, not 4b.
2. Label agreement number: **48/54 at ≥ 0.5**.
3. Hybrid `bge-m3` is how we score relevancy from now on (shop/noon match the shipped recall figures).
4. Telco off-topic is a **retrieval / corpus** problem, not a DeepEval bug, and a simple score floor is not the fix.
5. Do not treat 91% live-quote faithfulness as “the bot is grounded.”

---

## What is still open (real leftovers)

| # | Item | Why it is still open |
|---|---|---|
| 1 | **Open a PR** `cursor/deepeval-rag-eval` → `main` | Work is on the branch; not merged |
| 2 | **Telco off-topic** (jobs/5G/TV/insurance) | Hybrid made recall better and abstention worse; needs a product rule (when to return empty), not another judge |
| 3 | **Half-true / wrong Arabizi numbers scored 1.00** | The judge never extracts the false claim. A higher cutoff cannot catch a clean 1.00 |
| 4 | Conversation citation **70.7% vs 95%** | Agent not quoting; retrieval is already high. Team B / prompt, after this eval |
| 5 | Optional: CI for **deterministic** `eval-rag` only (no live judge) | Cheap safety net |

**Ask Sunday:** merge the PR? Who owns telco “return empty on off-topic”? Is citation 70.7% next or not this track?

---

## Pointers

- Branch: https://github.com/georgeemiL787/Ticket-Easy/tree/cursor/deepeval-rag-eval
- Open PR: https://github.com/georgeemiL787/Ticket-Easy/pull/new/cursor/deepeval-rag-eval
- Labels: `rag/eval/faithfulness_labels.jsonl` (54)
- Harness: `rag/src/team_a/eval_rag.py`

---

## WhatsApp (paste)

> تقرير الأسبوع — Team A RAG eval  
> بنينا تقييم DeepEval جنب الـ retrieval (relevancy + faithfulness). القاضي: Ollama qwen3:8b. على 54 مثال: **48/54**.  
> رجّعنا التشغيل بـ bge-m3: shop 89% والـ off-topic بقى 0%. Noon 98%. Telco 82% بس الأسئلة اللي مش من السياسة لسه بترجع (~44%) والـ hybrid خلّى abstention أسوأ. جرّبنا floor على telco ضرّ Arabizi على التيست فما شحنّاهوش.  
> قاعدة “أي claim غلط = فشل” نزّلت الاتفاق لـ 41/54 (القاضي بيقرا 14 إنها 1:4). فضّلنا 48/54.  
> الشغل على `cursor/deepeval-rag-eval`. نحدد الأحد: PR؟ ومين هيمسك telco off-topic؟
