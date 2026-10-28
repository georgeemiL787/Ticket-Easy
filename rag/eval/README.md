# Ticket-Easy RAG evaluation (DeepEval)

Work log for the RAG eval framework: what was already measured, why DeepEval was picked, what was built, how to run it, and the numbers from 27 Oct 2026.

Branch: `cursor/deepeval-rag-eval` (pushed). RAGAS stays the fallback if judged scores look unreliable.

Judges are **local Ollama** or **Groq** only. OpenRouter is rejected. DeepEval telemetry is off (`DEEPEVAL_TELEMETRY_OPT_OUT=1`).

---

## Why this exists

Team A already has retrieval gold as **expected policy section IDs**, not written reference answers:

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

## Framework choice

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

## What was built

| Path | Role |
|---|---|
| `rag/src/team_a/eval_rag.py` | DeepEval harness |
| `rag/src/team_a/__main__.py` | `eval-rag` CLI |
| `rag/tests/test_eval_rag.py` | 8 unit tests (no live judge) |
| `rag/requirements-eval.txt` | `deepeval>=4.0`, `ollama>=0.4` |
| `rag/eval/faithfulness_labels.jsonl` | 30 hand labels (this folder) |
| `rag/pytest.ini` | `-p no:deepeval` so the plugin does not hook the 262-test suite |
| `rag/.env.example` | judge env vars |
| `rag/var/eval/` | JSON reports from the runs (gitignored) |

Commits on this branch:

1. `28fd80f` — section hit + abstention + optional contextual relevancy
2. `8418d49` — faithfulness, Groq/Ollama judges, 30-label set

### Metrics (only the gaps)

| Metric | Judge? | Meaning |
|---|---|---|
| **section_hit** | no | Any gold citation in top-k. Same number as recall@5; kept so a DeepEval run sits next to the existing figure. Skips unanswerable rows. |
| **abstention** | no | Retrieval is empty **iff** the question is unanswerable. This is the no-answer gap. |
| **contextual relevancy** | yes (`--llm`) | Of the passages we *did* return, how many address the question. Diagnoses word-overlap hits. Want this **low** on unanswerable hits. |
| **faithfulness** | yes (`--replies` / `--labels`) | Is the bot reply supported by the **cited passage texts**? Not by gold IDs alone. |

Threshold for “faithful / relevant” vs a 0/1 hand label: score **≥ 0.5**.

---

## Setup

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

---

## How to run

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

Compare a judge to the 30 hand labels:

```powershell
python -m team_a eval-rag --tenant shop_001 --labels eval/faithfulness_labels.jsonl --judge ollama --out var/eval/labels_ollama.json
python -m team_a eval-rag --tenant shop_001 --labels eval/faithfulness_labels.jsonl --judge groq --out var/eval/labels_groq.json
```

Logged replies used for shop_001 faithfulness came from `Docs/output/eval_runs/diagnosis/`:

- `live_en_policy.jsonl` (English agent paraphrases)
- `agent_ar_all.jsonl` (Arabic)
- `live_rules_after_fix.jsonl` (Arabizi; mostly rules-mode **quotes**, not paraphrases)

Only rows with a reply **and** citations are scored (47 of those logs).

---

## 30-label set

`faithfulness_labels.jsonl`: 10 English, 10 Arabic, 10 Arabizi. Mix of diagnosis-backed rows and synthetic cases where the logs were thin.

| Style | From logs | Synthetic | Gold 1 (faithful) | Gold 0 (unfaithful) |
|---|---|---|---|---|
| en | 8 (`live_en_policy`) | 2 invented 30-day window / free shipping | 8 | 2 |
| ar | 8 (`agent_ar_all`) | 2 invented 30 days / no COD cap | 8 | 2 |
| arabizi | 4 (`live_rules_after_fix`) | 3 good paraphrases + 3 fakes (30 days, free ship, no COD cap) | 7 | 3 |

Arabizi agent paraphrases were missing from the logs (rules mode only quoted Arabic policy). The six synthetic Arabizi rows exist so the judge is tested on Latin-letter Egyptian Arabic, not only on quoted MSA.

---

## Results (27 Oct 2026)

Keyword-only indexes. Judge for relevancy/faithfulness: local `qwen3:8b`, except the Groq column in the label table.

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

### Judge vs hand labels (same 30 rows)

| Judge | Model | Overall | en | ar | arabizi |
|---|---|---|---|---|---|
| Ollama | `qwen3:8b` | **30/30 (100%)** | 10/10 | 10/10 | 10/10 |
| Groq | `qwen/qwen3.8-27b` | **27/30 (90%)** | 10/10 | 10/10 | **7/10** |

Groq’s three misses are all **synthetic Arabizi fakes** (30-day return, always-free shipping, COD with no 5000 cap). It scored them 1.0 (faithful) against gold 0. Ollama caught all three.

Caveat on Ollama’s 100%: one English row (`en-02`, “return after 10 days”) sat on **0.5** with a shaky reason, and the ≥0.5 cutoff counted it as agree. Still: **use Ollama as the judge, especially for Arabizi.** Groq is fine on English and Arabic paraphrases.

---

## What this does *not* claim

- It does not replace `eval-retrieval`. Recall and no-answer P/R stay there.
- Keyword-only runs are not the shipped hybrid numbers. Re-run without `--keyword` once `bge-m3` is available if you need those.
- 91% faithfulness on live quotes is not “the bot is grounded.” It means quoted policy matches the citation. Use the 30 labels for invented facts.
- telco 75% relevancy with 46% on unanswerable hits is a **retrieval** finding, not a DeepEval bug.

---

## Files you can re-run from

JSON reports (local, gitignored):

```text
rag/var/eval/shop_001_relevancy.json
rag/var/eval/noon_eg_relevancy.json
rag/var/eval/telco_001_relevancy.json
rag/var/eval/shop_001_faithfulness.json
rag/var/eval/labels_ollama.json
rag/var/eval/labels_groq.json
```

PR link if you want one: https://github.com/georgeemiL787/Ticket-Easy/pull/new/cursor/deepeval-rag-eval
