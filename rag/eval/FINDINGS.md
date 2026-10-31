# Faithfulness labels — findings (29 Oct 2026)

Expanded set: 54 rows (29 faithful / 25 unfaithful). Original 30 rows were not edited. Judges: local `qwen3:8b`, local `qwen3:4b`, Groq `qwen/qwen3.8-27b`. Cutoff: score ≥ 0.5. No OpenRouter.

## DeepEval on the 54 (Arabizi left in Latin script)

| Judge | Overall | en | ar | arabizi | Missed errors (gold 0, pass) | False alarms (gold 1, fail) |
|---|---|---|---|---|---|---|
| Ollama qwen3:8b | 48/54 (89%) | 15/16 | 16/16 | 17/22 | 6 | 0 |
| Ollama qwen3:4b | 46/54 (85%) | 14/16 | 16/16 | 16/22 | 8 | 0 |
| Groq qwen3.8-27b | 43/54 (80%) | 14/16 | 15/16 | 14/22 | 11 | 0 |

The earlier 30/30 on qwen3:8b does not hold once there are more unfaithful rows. No DeepEval judge marked a correct reply as wrong. Every miss is an error they let through (lenient).

Arabizi is the weak style for every judge. Wrong numbers in Arabizi often get 1.00 (“no contradictions”). Half-right replies (one true sentence + one false) tend to land at 0.50–0.67 and slip through the cutoff. Changing the 0.5 cutoff does not fix that: several misses are a clean 1.00, and the 0.50 cases would need a threshold above 0.5 that also punishes true paraphrases.

## Convert Arabizi → Arabic script, then re-judge (qwen3:8b)

Hypothesis: Arabic was 16/16, so the same replies in Arabic script should score like Arabic.

Derived file only (original jsonl untouched): `eval/faithfulness_labels_ar_script.jsonl`, built by `eval/arabizi_to_arabic.py`. Meaning, including the errors, is kept.

| | Overall | en | ar | **arabizi** |
|---|---|---|---|---|
| Before (Latin Arabizi) | 48/54 | 15/16 | 16/16 | **17/22** |
| After (Arabic script) | 47/54 | 14/16 | 15/16 | **18/22** |

Arabizi +1 only. Two previous misses were caught (`az-syn-pickup1`, `az-syn-half-closed-fri`). One new miss (`az-syn-cod-nocap` went 0 → 1). `az-syn-cod3k` stayed 1.00. `az-syn-half-cashsame` got worse (0.50 → 1.00). English and Arabic each dropped one row on this rerun (judge noise).

**Verdict:** converting script helps a little and is worth doing as a preprocess, but it is not the main bug. The judge still misses Arabizi (and Arabic-script) wrong numbers and half-right replies.

## RAGAS faithfulness on the same 54 (fallback)

`ragas 0.4.3` does not import in this venv (it requires `langchain_community.chat_models.vertexai`, which this LangChain drop no longer ships). The fallback run uses the **RAGAS faithfulness procedure** from that package: split the reply into statements, NLI each against the cited passage, score = share of verdict=1. Same local `qwen3:8b`, thinking off. Script: `eval/run_ragas_faithfulness.py`.

| | Overall | en | ar | arabizi | Missed errors | False alarms |
|---|---|---|---|---|---|---|
| RAGAS prompts + qwen3:8b | 47/54 (87%) | 14/16 | 14/16 | **19/22** | 5 | **2** |

RAGAS is stricter on Arabizi errors (19/22 vs DeepEval 17/22) and **does** mark some correct paraphrases as unfaithful (`az-syn-ship` 0.00, `az-syn-ok-size` 0.33) because the English statement-splitter mishandles Arabizi tokens (“el shahn” / “geneh”). That matches why RAGAS was the fallback, not the default: better at catching unsupported claims, worse when English and Arabizi are scored with the same English few-shot prompts.

## Hand review of the 24 new rows

Checked against `return_policy@v2`, `shipping_policy@v1`, `refund_policy@v1`, `faq@v1` in the shop_001 index. All 18 unfaithful rows are unsupported by the **cited** passage. All 6 faithful rows are paraphrases of that passage, not copies.

Keep as labelled. Two notes, not relabels:

- `en-syn-inv-freereturn` is also half-right (14 days is in s2; “we always cover return shipping” is not). Label 0 is still correct.
- Wrong-section rows (`en-syn-wrongsec-retfee`, `ar-syn-wrongsec-cod`, `az-syn-wrongsec-*`) state facts that are true in **another** section. Faithfulness vs the cited text is 0. That is the point of the type.

## What to use

- Judge for this project: **local qwen3:8b**, not Groq and not 4b.
- Do not trust a 0.5 cutoff to catch half-right replies.
- Optional preprocess: Arabizi → Arabic script before judging (small Arabizi gain, not a fix).
- RAGAS is a useful second opinion on unfaithful rows; it is not a drop-in on mixed English/Arabizi without Arabic few-shots.

JSON reports (gitignored): `var/eval/labels_ollama.json`, `labels_ollama_4b.json`, `labels_groq.json`, `labels_ollama_ar_script.json`, `labels_ragas.json`.
