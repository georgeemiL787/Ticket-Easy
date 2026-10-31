# Ticket-Easy RAG evaluation — full work log

Everything done on branch `cursor/deepeval-rag-eval` (pushed). RAGAS stays the fallback if judged scores look unreliable.

Judges are **local Ollama** or **Groq** only. OpenRouter is rejected. DeepEval telemetry is off (`DEEPEVAL_TELEMETRY_OPT_OUT=1`). Gold is **policy section IDs**, not written reference answers. Disagreements are not hidden.

The 54-row faithfulness experiment (appended labels, Arabizi→Arabic script, RAGAS prompts, hand review) is also written up in [FINDINGS.md](FINDINGS.md). This file is the full arc.

---

## 1. Why this exists

Team A already has retrieval gold as expected policy section IDs:

```json
{"id": "Q01", "style": "en", "question": "How many days do I have to return an item?", "expected": ["return_policy@v2#s2"]}
```

Empty `expected` means the question is unanswerable. Styles are `en`, `ar` (Egyptian Arabic), and `arabizi`.

### What the existing suite already measures

| Suite | What it scores | Gap it does not cover |
|---|---|---|
| `eval-retrieval` | recall@5, no-answer precision/recall | Does not say whether a *returned* passage actually answers the question |
| `eval_conversations` | citation ID overlap vs gold | Downstream of retrieval; agent can clarify/hand over instead of quoting |
| Guardrails | 44/44 allow/deny/require_human | Policy execution, not RAG quality |
| Style / locale / handoff | conversation behaviour | Not passage relevancy or reply faithfulness |

Known product gaps this work targets:

- Conversation citation accuracy was **70.7% of 75 turns vs a 95% target**. Retrieval recall is already high (shop test 89.3% with embeddings, 82% keyword-only here). The drop is often the agent not quoting, not a miss in the index.
- **Unanswerable questions** that share words with the corpus still return passages (no-answer recall 77.8% shop, 62.5% noon). The agent needs a signal that the hit is off-topic.
- Gold is section IDs. Faithfulness needs the **cited passage text** vs the bot reply, not a second gold answer.

DeepEval is wired so it **does not repeat** recall@5 / no-answer P/R. Those stay in `eval-retrieval`. DeepEval sits next to them.

---

## 2. Framework choice

Compared RAGAS, DeepEval, TruLens, Arize Phoenix, and promptfoo against: gold section IDs, Egyptian Arabic + Arabizi, pytest/CI, free-tier judge cost, and keeping eval text local.

**Picked DeepEval** (`deepeval>=4.0`).

- Works directly with expected section IDs (custom `section_hit` / `abstention` metrics).
- Runs as pytest/CLI next to the existing Python CI.
- Rubrics can be written in Arabic; prompts tell the judge to score meaning, not script (English / عامية / Arabizi).
- Fewer judge calls per question than RAGAS, which matters on free tiers.
- Local Ollama by default so labelled replies never have to leave the machine.

**RAGAS is the fallback** if DeepEval’s judged scores look unreliable. Several RAGAS metrics want a written reference answer; our gold is section IDs. It also makes more judge calls per question. Its Arabic adaptation helped MSA but hurt English and Arabizi when scored together.

Why not the others (short):

- **TruLens** — live-app monitoring. Conversation already traces. It does not score a labelled file of expected sections.
- **Arize Phoenix** — tracing first, eval second; Elastic License 2.0. Useful later in production, not for this labelled test file.
- **promptfoo** — good for guardrail red-teaming, but it adds a Node stack next to Python. Later, not now.

---

## 3. What was built

| Path | Role |
|---|---|
| `rag/src/team_a/eval_rag.py` | DeepEval harness (`section_hit`, `abstention`, relevancy, faithfulness; Ollama / Groq judges) |
| `rag/src/team_a/__main__.py` | `eval-rag` CLI |
| `rag/tests/test_eval_rag.py` | 8 unit tests (no live judge); label-file length is `>= 30` |
| `rag/requirements-eval.txt` | `deepeval>=4.0`, `ollama>=0.4` |
| `rag/eval/faithfulness_labels.jsonl` | 54 hand labels (original 30 + 24 appended; original 30 not edited) |
| `rag/eval/faithfulness_labels_ar_script.jsonl` | Derived copy: Arabizi Q/reply rewritten in Arabic script |
| `rag/eval/arabizi_to_arabic.py` | Builds that derived file |
| `rag/eval/run_ragas_faithfulness.py` | RAGAS statement-split + NLI via local qwen3:8b (library 0.4.3 does not import here) |
| `rag/eval/FINDINGS.md` | 54-row experiment write-up |
| `rag/pytest.ini` | `-p no:deepeval` so the plugin does not hook the 262-test suite |
| `rag/.env.example` | judge env vars |
| `rag/var/eval/` | JSON reports from the runs (gitignored) |

### Commits on this branch

1. `28fd80f` — section hit + abstention + optional contextual relevancy
2. `8418d49` — faithfulness, Groq/Ollama judges, original 30-label set
3. `4efd4d4` — this README (first version) + link from `rag/README.md`
4. `27e1685` — 24 appended labels, Arabizi-script file, RAGAS runner, findings note

### Metrics (only the gaps)

| Metric | Judge? | Meaning |
|---|---|---|
| **section_hit** | no | Any gold citation in top-k. Same number as recall@5; kept so a DeepEval run sits next to the existing figure. Skips unanswerable rows. |
| **abstention** | no | Retrieval is empty **iff** the question is unanswerable. This is the no-answer gap. |
| **contextual relevancy** | yes (`--llm`) | Of the passages we *did* return, how many address the question. Diagnoses word-overlap hits. Want this **low** on unanswerable hits. |
| **faithfulness** | yes (`--replies` / `--labels`) | Is the bot reply supported by the **cited passage texts**? Not by gold IDs alone. |

Threshold for “faithful / relevant” vs a 0/1 hand label: score **≥ 0.5**. That cutoff is too lenient on half-right replies (see §7).

---

## 4. Setup

From `rag/`:

```powershell
.venv\Scripts\pip install -r requirements-eval.txt
ollama pull qwen3:8b
$env:PYTHONPATH = "src"
$env:DEEPEVAL_TELEMETRY_OPT_OUT = "1"
python -m pytest tests/test_eval_rag.py -q
```

Hybrid retrieval needs `bge-m3`. The numbers below are **keyword-only** (`--keyword`) because that embed model was not pulled on this machine. Deterministic metrics still match `eval-retrieval` on the same index.

Judges:

| Kind | Default model | Notes |
|---|---|---|
| `--judge ollama` | `qwen3:8b` local | Thinking disabled (`think=False`) or DeepEval cannot parse JSON verdicts. **This is the trusted judge.** |
| `--judge groq` | `qwen/qwen3.8-27b` | Needs `TEAM_A_RAG_EVAL_API_KEY` / `GROQ_API_KEY`. `openai/gpt-oss-20b` often returned empty JSON. `llama-3.3-70b-versatile` is 404 on this Groq key. Free tier 429s; the harness backs off. |

```text
TEAM_A_RAG_EVAL_JUDGE=ollama
TEAM_A_RAG_EVAL_MODEL=qwen3:8b
TEAM_A_RAG_EVAL_BASE_URL=http://127.0.0.1:11434
```

Harness notes that bit us while wiring this:

- DeepEval 4 uses `LLMTestCase.metadata`, not `additional_metadata`.
- `from team_a.config import ROOT` (not `settings.ROOT`).
- `OllamaModel` needs the `ollama` package; `think=False` is a chat kw, not an options field.
- Skip judge rows that return empty / unparseable JSON instead of crashing the whole run.
- Groq: `response_format=json_object`, then switch default model to `qwen/qwen3.8-27b`.
- Long evals: `python -u` so PowerShell shows progress.
- pytest: DeepEval’s plugin teardown broke the 262-test suite → `pytest.ini` `-p no:deepeval`.

---

## 5. How to run

Deterministic only (no LLM):

```powershell
python -m team_a eval-rag --tenant shop_001 --split test --keyword
```

Contextual relevancy:

```powershell
python -m team_a eval-rag --tenant shop_001 --split test --keyword --llm --judge ollama --out var/eval/shop_001_relevancy.json
python -m team_a eval-rag --tenant noon_eg --split test --keyword --llm --judge ollama --out var/eval/noon_eg_relevancy.json
python -m team_a eval-rag --tenant telco_001 --split test --keyword --llm --judge ollama --out var/eval/telco_001_relevancy.json
```

Faithfulness on logged shop_001 replies (diagnosis jsonl with `reply` + `citations`):

```powershell
python -m team_a eval-rag --tenant shop_001 --replies var/eval/shop_001_replies.jsonl --judge ollama --out var/eval/shop_001_faithfulness.json
```

Compare a judge to the hand labels (now 54 rows):

```powershell
python -m team_a eval-rag --tenant shop_001 --labels eval/faithfulness_labels.jsonl --judge ollama --out var/eval/labels_ollama.json
python -m team_a eval-rag --tenant shop_001 --labels eval/faithfulness_labels.jsonl --judge groq --out var/eval/labels_groq.json
```

Arabizi rewritten in Arabic script, then the same Ollama judge:

```powershell
python -m team_a eval-rag --tenant shop_001 --labels eval/faithfulness_labels_ar_script.jsonl --judge ollama --out var/eval/labels_ollama_ar_script.json
```

RAGAS-style faithfulness (statement split + NLI, same 54, local qwen3:8b):

```powershell
python eval/run_ragas_faithfulness.py
```

Logged replies used for shop_001 faithfulness came from `Docs/output/eval_runs/diagnosis/`:

- `live_en_policy.jsonl` (English agent paraphrases)
- `agent_ar_all.jsonl` (Arabic)
- `live_rules_after_fix.jsonl` (Arabizi; mostly rules-mode **quotes**, not paraphrases)

Only rows with a reply **and** citations are scored (47 of those logs).

---

## 6. Results — relevancy and live faithfulness (27 Oct 2026)

Keyword-only indexes. Judge for relevancy/faithfulness: local `qwen3:8b`, except Groq in the label tables.

### Contextual relevancy

| Tenant | Questions | Unanswerable | section_hit (= recall@5) | abstention | no-answer recall | relevancy (on retrieved) | relevancy on unanswerable hits (want low) |
|---|---|---|---|---|---|---|---|
| shop_001 test | 37 (9 unans.) | 9 | **82.1%** (ar 7/9, az 9/10, en 7/9) | 89.2% | 77.8% | **58.5%** (28 retrieved) | **16.7%** |
| noon_eg test | 64 (24 unans.) | 24 | **90.0%** (ar 12/12, az 11/12, en 13/16) | 79.7% | 62.5% | **56.2%** (45 retrieved) | **13.7%** |
| telco_001 test | 102 (12 unans.) | 12 | **76.7%** (ar 26/30, az 19/30, en 24/30) | 86.3% | 58.3% | **74.8%** (86 retrieved) | **46.2%** |

How to read this:

- **section_hit** is the same idea as existing recall@5. Keyword-only is a bit below the hybrid figures in `rag/README.md` (shop 89.3%, noon 97.5%).
- **Relevancy ~56–58%** on shop/noon means many returned passages only weakly address the question. That is the word-overlap problem.
- **Unanswerable hits ~15%** on shop/noon is the useful number: when we wrongly retrieve, the judge usually says the passage is irrelevant. On **telco**, that jumps to **46%** — off-topic questions still pull passages the judge thinks are on-topic. Arabizi section hit on telco (19/30) is the weak style.

### Faithfulness (shop_001 logged replies)

**91.1%** on 47 cited replies (Ollama). Most policy answers score 1.0 because the bot **quotes** the cited passage. Drops were complaint / phone-number turns, not invented policy facts. Quote-heavy replies inflate faithfulness; the label set is what actually tests invented numbers.

---

## 7. Faithfulness labels

### Original 30 (not edited later)

10 English, 10 Arabic, 10 Arabizi. Mix of diagnosis-backed rows and synthetic cases where the logs were thin.

| Style | From logs | Synthetic | Gold 1 (faithful) | Gold 0 (unfaithful) |
|---|---|---|---|---|
| en | 8 (`live_en_policy`) | 2 invented 30-day window / free shipping | 8 | 2 |
| ar | 8 (`agent_ar_all`) | 2 invented 30 days / no COD cap | 8 | 2 |
| arabizi | 4 (`live_rules_after_fix`) | 3 good paraphrases + 3 fakes (30 days, free ship, no COD cap) | 7 | 3 |

Arabizi agent paraphrases were missing from the logs (rules mode only quoted Arabic policy). The six synthetic Arabizi rows exist so the judge is tested on Latin-letter Egyptian Arabic, not only on quoted MSA.

### Judge vs original 30

| Judge | Model | Overall | en | ar | arabizi |
|---|---|---|---|---|---|
| Ollama | `qwen3:8b` | **30/30 (100%)** | 10/10 | 10/10 | 10/10 |
| Groq | `qwen/qwen3.8-27b` | **27/30 (90%)** | 10/10 | 10/10 | **7/10** |

Groq’s three misses are all **synthetic Arabizi fakes** (30-day return, always-free shipping, COD with no 5000 cap). It scored them 1.0 (faithful) against gold 0. Ollama caught all three.

Caveat on Ollama’s 100%: one English row (`en-02`, “return after 10 days”) sat on **0.5** with a shaky reason, and the ≥0.5 cutoff counted it as agree. Still: **use Ollama as the judge, especially for Arabizi.** Groq is fine on English and Arabic paraphrases.

### Expanded 54 (original 30 + 24 appended)

The original 30 rows were **not** rewritten. After line 30 we appended **18 unfaithful + 6 faithful** (29 gold-1 / 25 gold-0 overall). Types on the new unfaithful rows: wrong number, wrong section (true fact, wrong citation), invented extra, half-right (one true sentence + one false).

| Judge | Overall | en | ar | arabizi | Missed errors (gold 0, pass) | False alarms (gold 1, fail) |
|---|---|---|---|---|---|---|
| Ollama qwen3:8b | **48/54 (89%)** | 15/16 | 16/16 | 17/22 | 6 | 0 |
| Ollama qwen3:4b | 46/54 (85%) | 14/16 | 16/16 | 16/22 | 8 | 0 |
| Groq qwen3.8-27b | 43/54 (80%) | 14/16 | 15/16 | 14/22 | 11 | 0 |

The earlier 30/30 on qwen3:8b does not hold once there are more unfaithful rows. No DeepEval judge marked a correct reply as wrong. Every miss is an error they let through (lenient).

Arabizi is the weak style for every judge. Wrong numbers in Arabizi often get 1.00 (“no contradictions”). Half-right replies tend to land at 0.50–0.67 and slip through the cutoff. Changing the 0.5 cutoff does not fix that: several misses are a clean 1.00, and a higher threshold would also punish true paraphrases.

---

## 8. Arabizi → Arabic script (derived file only)

Hypothesis: Arabic was 16/16, so the same replies in Arabic script should score like Arabic.

`eval/arabizi_to_arabic.py` writes `eval/faithfulness_labels_ar_script.jsonl`. The original jsonl is untouched. Meaning, including the errors, is kept.

| | Overall | en | ar | **arabizi** |
|---|---|---|---|---|
| Before (Latin Arabizi) | 48/54 | 15/16 | 16/16 | **17/22** |
| After (Arabic script) | 47/54 | 14/16 | 15/16 | **18/22** |

Arabizi +1 only. Two previous misses were caught (`az-syn-pickup1`, `az-syn-half-closed-fri`). One new miss (`az-syn-cod-nocap` went 0 → 1). `az-syn-cod3k` stayed 1.00. `az-syn-half-cashsame` got worse (0.50 → 1.00). English and Arabic each dropped one row on this rerun (judge noise).

**Verdict:** converting script helps a little and is worth doing as a preprocess, but it is not the main bug. The judge still misses Arabizi (and Arabic-script) wrong numbers and half-right replies.

---

## 9. RAGAS faithfulness fallback (same 54)

`ragas 0.4.3` does not import in this venv (it requires `langchain_community.chat_models.vertexai`, which this LangChain drop no longer ships). pip also conflicted with `deepeval` (`click<8.4` vs 8.5). The fallback run uses the **RAGAS faithfulness procedure**: split the reply into statements, NLI each against the cited passage, score = share of verdict=1. Same local `qwen3:8b`, thinking off. Script: `eval/run_ragas_faithfulness.py`. `eval_rag.py` was not changed for this.

| | Overall | en | ar | arabizi | Missed errors | False alarms |
|---|---|---|---|---|---|---|
| RAGAS prompts + qwen3:8b | 47/54 (87%) | 14/16 | 14/16 | **19/22** | 5 | **2** |

RAGAS is stricter on Arabizi errors (19/22 vs DeepEval 17/22) and **does** mark some correct paraphrases as unfaithful (`az-syn-ship` 0.00, `az-syn-ok-size` 0.33) because the English statement-splitter mishandles Arabizi tokens (“el shahn” / “geneh”). That matches why RAGAS was the fallback, not the default: better at catching unsupported claims, worse when English and Arabizi are scored with the same English few-shot prompts.

---

## 10. Hand review of the 24 new rows

Checked against `return_policy@v2`, `shipping_policy@v1`, `refund_policy@v1`, `faq@v1` in the shop_001 index. All 18 unfaithful rows are unsupported by the **cited** passage. All 6 faithful rows are paraphrases of that passage, not copies.

Keep as labelled. Two notes, not relabels:

- `en-syn-inv-freereturn` is also half-right (14 days is in s2; “we always cover return shipping” is not). Label 0 is still correct.
- Wrong-section rows (`en-syn-wrongsec-retfee`, `ar-syn-wrongsec-cod`, `az-syn-wrongsec-*`) state facts that are true in **another** section. Faithfulness vs the cited text is 0. That is the point of the type.

---

## 11. What to use

- Judge for this project: **local qwen3:8b**, not Groq and not 4b.
- Do not trust a 0.5 cutoff to catch half-right replies.
- Optional preprocess: Arabizi → Arabic script before judging (small Arabizi gain, not a fix).
- RAGAS is a useful second opinion on unfaithful rows; it is not a drop-in on mixed English/Arabizi without Arabic few-shots.

---

## 12. What this does *not* claim

- It does not replace `eval-retrieval`. Recall and no-answer P/R stay there.
- Keyword-only runs are not the shipped hybrid numbers. Re-run without `--keyword` once `bge-m3` is available if you need those.
- 91% faithfulness on live quotes is not “the bot is grounded.” It means quoted policy matches the citation. Use the 54 labels for invented facts.
- telco 75% relevancy with 46% on unanswerable hits is a **retrieval** finding, not a DeepEval bug.
- 30/30 on the first label set was too small; 48/54 on qwen3:8b is the number to quote.

---

## 13. Files you can re-run from

JSON reports (local, gitignored):

```text
rag/var/eval/shop_001_relevancy.json
rag/var/eval/noon_eg_relevancy.json
rag/var/eval/telco_001_relevancy.json
rag/var/eval/shop_001_faithfulness.json
rag/var/eval/labels_ollama.json
rag/var/eval/labels_ollama_4b.json
rag/var/eval/labels_groq.json
rag/var/eval/labels_ollama_ar_script.json
rag/var/eval/labels_ragas.json
```

PR link if you want one: https://github.com/georgeemiL787/Ticket-Easy/pull/new/cursor/deepeval-rag-eval
